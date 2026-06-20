import asyncio
import websockets
import json
import base64
import cv2
import numpy as np
import os
import time
from datetime import datetime
from pymongo import MongoClient

MODEL_PATH = "ModeloV3.tflite"

# Ajusta estos valores si quieres más o menos sensibilidad
CONF_THRESHOLD = 0.35
NMS_THRESHOLD = 0.45

# Para no guardar la misma grieta muchas veces seguidas
SAVE_COOLDOWN_SECONDS = 5

# Carpeta donde se guardan las imágenes de evidencia
EVIDENCE_DIR = "evidence"

# MongoDB local en tu laptop
MONGO_URI = "mongodb://localhost:27017/"
MONGO_DB_NAME = "robot_grietas"
MONGO_COLLECTION_NAME = "detecciones"

# Clientes conectados
browser_clients = set()
zero_clients = set()

# Lock para enviar comandos a la Zero
zero_send_lock = asyncio.Lock()

interpreter = None
input_details = None
output_details = None

mongo_collection = None
last_telemetry = None
last_save_time = 0

# Contador de columna/carril actual.
# Inicia en 1 y aumenta cuando se presiona "Cambio de carril".
current_lane = 1


# =====================================================
# MONGODB
# =====================================================

def conectar_mongodb():
    global mongo_collection

    try:
        client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")

        db = client[MONGO_DB_NAME]
        mongo_collection = db[MONGO_COLLECTION_NAME]

        print("MongoDB conectado correctamente")
        print(f"Base de datos: {MONGO_DB_NAME}")
        print(f"Coleccion: {MONGO_COLLECTION_NAME}")

    except Exception as e:
        print("No se pudo conectar a MongoDB:", e)
        mongo_collection = None


def crear_carpeta_evidencia():
    os.makedirs(EVIDENCE_DIR, exist_ok=True)
    print(f"Carpeta de evidencia lista: {EVIDENCE_DIR}/")


# =====================================================
# MODELO
# =====================================================

def cargar_modelo():
    global interpreter, input_details, output_details

    if not os.path.exists(MODEL_PATH):
        print("Modelo no encontrado:", MODEL_PATH)
        return

    try:
        try:
            from tflite_runtime.interpreter import Interpreter
            print("Usando tflite_runtime")
        except ImportError:
            from tensorflow.lite.python.interpreter import Interpreter
            print("Usando tensorflow.lite")

        interpreter = Interpreter(model_path=MODEL_PATH)
        interpreter.allocate_tensors()

        input_details = interpreter.get_input_details()
        output_details = interpreter.get_output_details()

        print("Modelo cargado correctamente:", MODEL_PATH)

        print("INPUTS:")
        for i in input_details:
            print(i["name"], i["shape"], i["dtype"])

        print("OUTPUTS:")
        for o in output_details:
            print(o["name"], o["shape"], o["dtype"])

    except Exception as e:
        print("No se pudo cargar el modelo:", e)
        interpreter = None


# =====================================================
# TELEMETRÍA
# =====================================================

def convertir_telemetria(csv_data):
    datos = csv_data.split(",")

    # Formato esperado:
    # temp,hum,ax,ay,az,gx,gy,gz
    if len(datos) != 8:
        return None

    try:
        return {
            "temperature": float(datos[0]),
            "humidity": float(datos[1]),
            "ax": float(datos[2]),
            "ay": float(datos[3]),
            "az": float(datos[4]),
            "gx": float(datos[5]),
            "gy": float(datos[6]),
            "gz": float(datos[7]),
        }
    except ValueError:
        return None


# =====================================================
# GUARDAR DETECCIÓN EN MONGODB
# =====================================================

def guardar_deteccion(frame, crack_count, confidence):
    global last_save_time
    global current_lane

    if mongo_collection is None:
        print("No se guarda deteccion porque MongoDB no esta conectado")
        return

    if last_telemetry is None:
        print("No se guarda deteccion porque aun no hay telemetria")
        return

    ahora = time.time()

    if ahora - last_save_time < SAVE_COOLDOWN_SECONDS:
        return

    timestamp = datetime.now()
    timestamp_file = timestamp.strftime("%Y%m%d_%H%M%S")

    image_filename = f"grieta_{timestamp_file}.jpg"
    image_path = os.path.join(EVIDENCE_DIR, image_filename)

    cv2.imwrite(image_path, frame)

    documento = {
        "detected_at": timestamp,

        # Columna/carril actual donde estaba el robot
        # cuando se detectó y guardó la grieta.
        "lane_column": int(current_lane),

        "telemetry": last_telemetry,
        "detection": {
            "crack_count": int(crack_count),
            "confidence": float(confidence),
        },
        "image_path": image_path,
    }

    try:
        result = mongo_collection.insert_one(documento)
        last_save_time = ahora
        print("Deteccion guardada en MongoDB:", result.inserted_id)
        print("Columna/carril registrado:", current_lane)
        print("Imagen guardada:", image_path)

    except Exception as e:
        print("Error guardando deteccion en MongoDB:", e)


# =====================================================
# PROCESAMIENTO DEL MODELO
# =====================================================

def procesar_con_modelo(frame):
    if interpreter is None:
        cv2.putText(
            frame,
            "Modelo no cargado",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
        )
        return frame, "Modelo no cargado", 0.0, 0

    try:
        input_shape = input_details[0]["shape"]
        input_dtype = input_details[0]["dtype"]

        input_h = int(input_shape[1])
        input_w = int(input_shape[2])

        original_h, original_w = frame.shape[:2]

        # Preparar imagen para el modelo
        img = cv2.resize(frame, (input_w, input_h))
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        if input_dtype == np.float32:
            img_rgb = img_rgb.astype(np.float32) / 255.0
        else:
            img_rgb = img_rgb.astype(input_dtype)

        img_input = np.expand_dims(img_rgb, axis=0)

        interpreter.set_tensor(input_details[0]["index"], img_input)
        interpreter.invoke()

        output = interpreter.get_tensor(output_details[0]["index"])
        output = np.squeeze(output)

        # Modelo tipo YOLO: [1, 5, 8400] -> [5, 8400] -> [8400, 5]
        if output.ndim == 2 and output.shape[0] == 5:
            output = output.T

        boxes = []
        confidences = []

        for det in output:
            if len(det) < 5:
                continue

            x, y, w, h, conf = det[:5]

            if not np.isfinite(conf):
                continue

            if conf < CONF_THRESHOLD:
                continue

            # Si las coordenadas vienen normalizadas 0-1
            if x <= 1 and y <= 1 and w <= 1 and h <= 1:
                x *= input_w
                y *= input_h
                w *= input_w
                h *= input_h

            # Escalar del tamaño del input al tamaño original
            x = x * original_w / input_w
            y = y * original_h / input_h
            w = w * original_w / input_w
            h = h * original_h / input_h

            # YOLO normalmente usa x,y como centro
            x1 = int(x - w / 2)
            y1 = int(y - h / 2)
            x2 = int(x + w / 2)
            y2 = int(y + h / 2)

            x1 = max(0, min(x1, original_w - 1))
            y1 = max(0, min(y1, original_h - 1))
            x2 = max(0, min(x2, original_w - 1))
            y2 = max(0, min(y2, original_h - 1))

            box_w = x2 - x1
            box_h = y2 - y1

            if box_w <= 0 or box_h <= 0:
                continue

            boxes.append([x1, y1, box_w, box_h])
            confidences.append(float(conf))

        indices = cv2.dnn.NMSBoxes(
            boxes,
            confidences,
            score_threshold=CONF_THRESHOLD,
            nms_threshold=NMS_THRESHOLD,
        )

        detecciones = 0
        mejor_confianza = 0.0

        if len(indices) > 0:
            for i in indices.flatten():
                x, y, w, h = boxes[i]
                conf = confidences[i]

                detecciones += 1
                mejor_confianza = max(mejor_confianza, conf)

                cv2.rectangle(
                    frame,
                    (x, y),
                    (x + w, y + h),
                    (0, 255, 0),
                    2,
                )

                cv2.putText(
                    frame,
                    f"Grieta {conf * 100:.1f}%",
                    (x, max(25, y - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (0, 255, 0),
                    2,
                )

        if detecciones > 0:
            resultado = f"Grieta detectada ({detecciones})"
        else:
            resultado = "Sin grieta detectada"

        return frame, resultado, mejor_confianza, detecciones

    except Exception as e:
        print("Error usando modelo:", e)

        cv2.putText(
            frame,
            "Error modelo",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 0, 255),
            2,
        )

        return frame, "Error modelo", 0.0, 0


# =====================================================
# ENVÍO A PÁGINAS
# =====================================================

async def enviar_a_paginas(mensaje):
    if not browser_clients:
        return

    desconectados = []

    for client in browser_clients:
        try:
            await client.send(json.dumps(mensaje))
        except Exception:
            desconectados.append(client)

    for client in desconectados:
        browser_clients.discard(client)


# =====================================================
# COMANDOS WEB -> SERVER -> ZERO
# =====================================================

async def enviar_comando_a_zero(comando):
    comando = str(comando).strip().upper()

    if comando not in ["W", "A", "S", "D", "X"]:
        print("Comando invalido desde pagina:", comando)
        return 0

    if not zero_clients:
        print("No hay Zero conectada para enviar comando:", comando)
        return 0

    mensaje = {
        "type": "command",
        "command": comando
    }

    enviados = 0
    desconectados = []

    async with zero_send_lock:
        for zero in zero_clients:
            try:
                await zero.send(json.dumps(mensaje))
                enviados += 1
            except Exception:
                desconectados.append(zero)

    for zero in desconectados:
        zero_clients.discard(zero)

    print(f"Comando reenviado a Zero: {comando} | conexiones: {enviados}")
    return enviados


async def enviar_estado_control_a_zero(active):
    if not zero_clients:
        print("No hay Zero conectada para cambiar estado de control:", active)
        return 0

    mensaje = {
        "type": "control_state",
        "active": bool(active)
    }

    enviados = 0
    desconectados = []

    async with zero_send_lock:
        for zero in zero_clients:
            try:
                await zero.send(json.dumps(mensaje))
                enviados += 1
            except Exception:
                desconectados.append(zero)

    for zero in desconectados:
        zero_clients.discard(zero)

    print(f"Estado de control reenviado a Zero: {active} | conexiones: {enviados}")
    return enviados


# =====================================================
# CAMBIO DE CARRIL / COLUMNA
# =====================================================

async def enviar_estado_carril_a_paginas():
    mensaje = {
        "type": "lane_state",
        "lane": int(current_lane)
    }

    await enviar_a_paginas(mensaje)


async def cambiar_carril():
    global current_lane

    current_lane += 1
    print(f"Cambio de carril solicitado. Nueva columna/carril: {current_lane}")

    await enviar_estado_carril_a_paginas()

    return current_lane


# =====================================================
# WEBSOCKET DESDE ZERO
# Puerto 8766
# =====================================================

async def recibir_desde_zero(websocket, path=None):
    global last_telemetry

    print("Zero conectada")
    zero_clients.add(websocket)

    try:
        async for message in websocket:
            try:
                data = json.loads(message)

                tipo = data.get("type")

                if tipo == "telemetry":
                    telemetry = convertir_telemetria(data.get("data", ""))

                    if telemetry is not None:
                        last_telemetry = telemetry

                    # Reenviar telemetría a la página
                    await enviar_a_paginas(data)

                elif tipo == "frame":
                    img_b64 = data.get("image", "")
                    img_bytes = base64.b64decode(img_b64)

                    arr = np.frombuffer(img_bytes, np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)

                    if frame is None:
                        print("Frame invalido")
                        continue

                    frame_procesado, resultado, confianza, detecciones = procesar_con_modelo(frame)

                    if detecciones > 0:
                        guardar_deteccion(
                            frame_procesado,
                            detecciones,
                            confianza,
                        )

                    _, buffer = cv2.imencode(
                        ".jpg",
                        frame_procesado,
                        [int(cv2.IMWRITE_JPEG_QUALITY), 70],
                    )

                    out_b64 = base64.b64encode(buffer).decode("utf-8")

                    await enviar_a_paginas({
                        "type": "frame",
                        "image": out_b64,
                        "class": resultado,
                        "confidence": confianza,
                    })

            except Exception as e:
                print("Error procesando mensaje desde Zero:", e)

    except Exception as e:
        print("Zero desconectada:", e)

    finally:
        zero_clients.discard(websocket)
        print("Zero removida de clientes conectados")


# =====================================================
# WEBSOCKET DESDE PÁGINA
# Puerto 8767
# =====================================================

async def pagina_conectada(websocket, path=None):
    print("Pagina web conectada")
    browser_clients.add(websocket)

    try:
        # Enviar carril actual apenas se conecta la página.
        await websocket.send(json.dumps({
            "type": "lane_state",
            "lane": int(current_lane)
        }))

        async for message in websocket:
            try:
                data = json.loads(message)
                tipo = data.get("type")

                if tipo == "command":
                    comando = data.get("command", "")
                    enviados = await enviar_comando_a_zero(comando)

                    await websocket.send(json.dumps({
                        "type": "command_ack",
                        "command": str(comando).upper(),
                        "sent_to_zero": enviados
                    }))

                elif tipo == "control_state":
                    active = bool(data.get("active", False))
                    enviados = await enviar_estado_control_a_zero(active)

                    await websocket.send(json.dumps({
                        "type": "control_state_ack",
                        "active": active,
                        "sent_to_zero": enviados
                    }))

                elif tipo == "lane_change":
                    nueva_columna = await cambiar_carril()

                    await websocket.send(json.dumps({
                        "type": "lane_state",
                        "lane": int(nueva_columna)
                    }))

            except Exception as e:
                print("Error procesando mensaje desde pagina:", e)

    except Exception as e:
        print("Pagina web desconectada:", e)

    finally:
        browser_clients.discard(websocket)
        print("Pagina web desconectada")


# =====================================================
# MAIN
# =====================================================

async def main():
    crear_carpeta_evidencia()
    conectar_mongodb()
    cargar_modelo()

    await websockets.serve(
        recibir_desde_zero,
        "0.0.0.0",
        8766,
        max_size=None,
    )

    await websockets.serve(
        pagina_conectada,
        "0.0.0.0",
        8767,
        max_size=None,
    )

    print("Servidor para Zero: ws://0.0.0.0:8766")
    print("Servidor para pagina: ws://0.0.0.0:8767")
    print("Control web habilitado: index.html -> server.py -> Zero -> UART")
    print("Contador de columna/carril habilitado. Inicia en 1.")

    await asyncio.Future()


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nCerrando server.py")