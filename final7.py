import asyncio
import websockets
import serial
import json
import base64
import cv2
import time
import sys
import termios
import tty
import select
from picamera2 import Picamera2

# =====================================================
# CONFIGURACIÓN GENERAL
# =====================================================

# Cambia esta IP si cambia la IP de tu laptop
LAPTOP_WS = "ws://192.168.1.169:8766"

PUERTO_UART = "/dev/serial0"
BAUDRATE = 9600

CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240
CAMERA_FPS = 15
JPEG_QUALITY = 55
CAMERA_DELAY_SECONDS = 0.15

MOVEMENT_REPEAT_SECONDS = 0.20

COMANDOS_MOVIMIENTO = ["W", "A", "S", "D", "X"]

# =====================================================
# UART ZERO <-> PICO 1
# Zero GPIO15 RX recibe desde Pico 1 GP0 TX
# Zero GPIO14 TX envía hacia Pico 1 GP1 RX
# =====================================================

uart = serial.Serial(
    port=PUERTO_UART,
    baudrate=BAUDRATE,
    timeout=0.05
)

uart.reset_input_buffer()

# =====================================================
# CÁMARA CSI CON PICAMERA2
# =====================================================

picam2 = Picamera2()

camera_config = picam2.create_video_configuration(
    main={
        "size": (CAMERA_WIDTH, CAMERA_HEIGHT),
        "format": "RGB888"
    },
    controls={
        "FrameRate": CAMERA_FPS
    }
)

picam2.configure(camera_config)
picam2.start()
time.sleep(2)

print("Camara CSI iniciada correctamente con Picamera2")

# =====================================================
# LOCKS Y ESTADO
# =====================================================

send_lock = asyncio.Lock()
uart_write_lock = asyncio.Lock()

control_habilitado = False
movimiento_actual = ""
ultimo_envio_movimiento = time.time()

ultima_altitud = "NA"

ultimos_encoders = {
    "total_a": "0",
    "total_b": "0",
    "delta_a": "0",
    "delta_b": "0"
}


# =====================================================
# ENVÍO JSON AL SERVER
# =====================================================

async def enviar_json(ws, mensaje):
    async with send_lock:
        await ws.send(json.dumps(mensaje))


# =====================================================
# ENVÍO DE COMANDOS A PICO 1
# =====================================================

async def enviar_comando_uart(comando):
    comando = comando.upper()

    if comando not in COMANDOS_MOVIMIENTO:
        return

    async with uart_write_lock:
        uart.write((comando + "\n").encode())
        uart.flush()
        print("Zero envio a Pico 1:", comando)


async def aplicar_estado_control(active):
    global control_habilitado
    global movimiento_actual

    control_habilitado = bool(active)

    if control_habilitado:
        print("Control habilitado desde pagina")
    else:
        print("Control pausado desde pagina")
        movimiento_actual = ""
        await enviar_comando_uart("X")


async def aplicar_comando_movimiento(comando, origen="desconocido"):
    global movimiento_actual
    global ultimo_envio_movimiento

    comando = str(comando).strip().upper()

    if comando not in COMANDOS_MOVIMIENTO:
        return

    if comando != "X" and not control_habilitado:
        print(f"Comando ignorado porque el control esta pausado: {comando}")
        return

    if comando in ["W", "A", "S", "D"]:
        movimiento_actual = comando
        ultimo_envio_movimiento = time.time()
        await enviar_comando_uart(comando)
        print(f"Comando {origen}: {comando}")

    elif comando == "X":
        movimiento_actual = ""
        ultimo_envio_movimiento = time.time()
        await enviar_comando_uart("X")
        print(f"Comando {origen}: X - detenido")


async def repetir_movimiento_periodico():
    global ultimo_envio_movimiento

    while True:
        try:
            if control_habilitado and movimiento_actual in ["W", "A", "S", "D"]:
                if time.time() - ultimo_envio_movimiento >= MOVEMENT_REPEAT_SECONDS:
                    await enviar_comando_uart(movimiento_actual)
                    ultimo_envio_movimiento = time.time()

        except Exception as e:
            print("Error repitiendo movimiento:", e)

        await asyncio.sleep(0.02)


# =====================================================
# LECTURA DE TECLA SIN ENTER
# =====================================================

def leer_tecla_no_bloqueante():
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    tecla = ""

    try:
        tty.setraw(fd)

        if select.select([sys.stdin], [], [], 0.01)[0]:
            tecla = sys.stdin.read(1)

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    return tecla


async def control_movimiento_teclado():
    print("\nControl de movimiento por teclado activo")
    print("--------------------------------")
    print("W -> avanzar")
    print("S -> retroceder")
    print("A -> izquierda")
    print("D -> derecha")
    print("X -> detener")
    print("Desde la pagina primero debes presionar Iniciar/Reanudar")
    print("Ctrl + C -> salir")
    print("--------------------------------\n")

    while True:
        try:
            tecla = leer_tecla_no_bloqueante()

            if tecla:
                if tecla == "\x03":
                    await aplicar_comando_movimiento("X", origen="teclado")
                    raise KeyboardInterrupt

                tecla = tecla.upper()

                if tecla in COMANDOS_MOVIMIENTO:
                    await aplicar_comando_movimiento(tecla, origen="teclado")

        except KeyboardInterrupt:
            raise

        except Exception as e:
            print("Error control teclado:", e)

        await asyncio.sleep(0.02)


# =====================================================
# COMANDOS DESDE SERVER.PY
# =====================================================

async def recibir_comandos_desde_server(ws):
    print("Esperando comandos desde server.py...")

    try:
        async for message in ws:
            try:
                data = json.loads(message)

                if data.get("type") == "command":
                    comando = data.get("command", "")
                    await aplicar_comando_movimiento(comando, origen="pagina")

                elif data.get("type") == "control_state":
                    active = bool(data.get("active", False))
                    await aplicar_estado_control(active)

            except Exception as e:
                print("Error procesando mensaje desde server:", e)

    except websockets.exceptions.ConnectionClosed:
        print("Conexion cerrada mientras se recibian comandos")
        raise


# =====================================================
# PARSEO DE TELEMETRÍA
# =====================================================

def valor_telemetria(valor):
    """
    Convierte valores a texto numérico si se puede.
    Si llega NA, vacío o algo inválido, devuelve NA.
    Esto evita que la Zero ignore toda la línea.
    """

    valor = str(valor).strip()

    if valor == "" or valor.upper() == "NA":
        return "NA"

    try:
        return str(float(valor))
    except Exception:
        return "NA"


def convertir_linea_a_csv_telemetria(linea):
    """
    Convierte cualquier línea recibida desde UART a 8 datos:

    temp,hum,ax,ay,az,gx,gy,gz

    Acepta formatos:
    - DE_PICO2,P2S,temp,hum,ax,ay,az,gx,gy,gz,altitud,roll,pitch
    - DE_PICO2,P2S,temp,hum,ax,ay,az,gx,gy,gz,roll,pitch
    - P2S,temp,hum,ax,ay,az,gx,gy,gz,altitud,roll,pitch
    - P2S,temp,hum,ax,ay,az,gx,gy,gz,roll,pitch
    - temp,hum,ax,ay,az,gx,gy,gz
    - temp,hum,ax,ay,az,gx,gy,gz,altitud
    """

    global ultima_altitud

    linea = linea.strip()

    if not linea:
        return None

    # Si llega como DE_PICO2,P2S,...
    # recortamos desde P2S para normalizar.
    if "P2S," in linea:
        linea = linea[linea.find("P2S,"):]

    partes = linea.split(",")

    # Formato P2S con o sin altitud:
    # P2S,temp,hum,ax,ay,az,gx,gy,gz,...
    if len(partes) >= 11 and partes[0] == "P2S":
        temp = valor_telemetria(partes[1])
        hum = valor_telemetria(partes[2])
        ax = valor_telemetria(partes[3])
        ay = valor_telemetria(partes[4])
        az = valor_telemetria(partes[5])
        gx = valor_telemetria(partes[6])
        gy = valor_telemetria(partes[7])
        gz = valor_telemetria(partes[8])

        # Si viene altitud, normalmente está en partes[9]
        # pero para la página se ignora porque index.html espera 8 datos.
        if len(partes) >= 12:
            ultima_altitud = valor_telemetria(partes[9])
        else:
            ultima_altitud = "NA"

        return f"{temp},{hum},{ax},{ay},{az},{gx},{gy},{gz}"

    # Formato directo correcto:
    # temp,hum,ax,ay,az,gx,gy,gz
    if len(partes) == 8:
        valores = [valor_telemetria(v) for v in partes]
        ultima_altitud = "NA"
        return ",".join(valores)

    # Formato directo con altitud:
    # temp,hum,ax,ay,az,gx,gy,gz,altitud
    if len(partes) == 9:
        valores = [valor_telemetria(v) for v in partes[:8]]
        ultima_altitud = valor_telemetria(partes[8])
        return ",".join(valores)

    return None


# =====================================================
# ENCODERS
# =====================================================

def mostrar_encoders(linea):
    global ultimos_encoders

    if "P1ENC," not in linea:
        return False

    linea = linea[linea.find("P1ENC,"):]
    partes = linea.split(",")

    if len(partes) < 5:
        print("Datos incompletos de encoders:", linea)
        return True

    ultimos_encoders = {
        "total_a": partes[1],
        "total_b": partes[2],
        "delta_a": partes[3],
        "delta_b": partes[4]
    }

    print("\n========== ENCODERS ==========")
    print("Motor A total:", ultimos_encoders["total_a"])
    print("Motor B total:", ultimos_encoders["total_b"])
    print("Motor A delta:", ultimos_encoders["delta_a"])
    print("Motor B delta:", ultimos_encoders["delta_b"])
    print("==============================\n")

    return True


def mostrar_telemetria(csv_telemetria):
    datos = csv_telemetria.split(",")

    if len(datos) != 8:
        print("Telemetria invalida, no tiene 8 datos:", csv_telemetria)
        return

    t, h, ax, ay, az, gx, gy, gz = datos

    print("\n" + "=" * 42)
    print(f"Temp: {t} C | Hum: {h} % | Altitud: {ultima_altitud} m")
    print(f"Accel: X={ax}, Y={ay}, Z={az} (g)")
    print(f"Gyro:  X={gx}, Y={gy}, Z={gz} (deg/s)")
    print("Encoders:")
    print(f"  Motor A total: {ultimos_encoders['total_a']} | delta: {ultimos_encoders['delta_a']}")
    print(f"  Motor B total: {ultimos_encoders['total_b']} | delta: {ultimos_encoders['delta_b']}")
    print("=" * 42)


# =====================================================
# TAREA: LEER UART Y ENVIAR TELEMETRÍA AL SERVER
# =====================================================

async def enviar_telemetria(ws):
    print("Leyendo telemetria desde UART...")

    buffer_uart = ""

    while True:
        try:
            while uart.in_waiting > 0:
                data = uart.read(uart.in_waiting)

                if not data:
                    break

                texto = data.decode("utf-8", errors="ignore")

                for c in texto:
                    if c == "\n" or c == "\r":
                        linea = buffer_uart.strip()
                        buffer_uart = ""

                        if not linea:
                            continue

                        print("Zero recibio por UART:", linea)

                        if mostrar_encoders(linea):
                            continue

                        csv_telemetria = convertir_linea_a_csv_telemetria(linea)

                        if csv_telemetria is None:
                            print("Linea UART ignorada, no es telemetria valida:", linea)
                            continue

                        mostrar_telemetria(csv_telemetria)

                        mensaje = {
                            "type": "telemetry",
                            "data": csv_telemetria
                        }

                        await enviar_json(ws, mensaje)
                        print("Telemetria enviada al server:", csv_telemetria)

                    else:
                        buffer_uart += c

                        if len(buffer_uart) > 300:
                            print("Buffer UART limpiado por exceso:", buffer_uart)
                            buffer_uart = ""

        except websockets.exceptions.ConnectionClosed:
            print("Conexion WebSocket cerrada mientras se enviaba telemetria")
            raise

        except Exception as e:
            print("Error telemetria:", e)

        await asyncio.sleep(0.03)


# =====================================================
# TAREA: CAPTURAR CÁMARA CSI Y ENVIAR FRAME AL SERVER
# =====================================================

async def enviar_camara(ws):
    print("Enviando camara CSI a la laptop...")

    while True:
        try:
            frame_rgb = picam2.capture_array()

            frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

            ok, buffer = cv2.imencode(
                ".jpg",
                frame_bgr,
                [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
            )

            if not ok:
                print("No se pudo codificar imagen de la camara CSI")
                await asyncio.sleep(CAMERA_DELAY_SECONDS)
                continue

            img_b64 = base64.b64encode(buffer).decode("utf-8")

            mensaje = {
                "type": "frame",
                "image": img_b64
            }

            await enviar_json(ws, mensaje)

        except websockets.exceptions.ConnectionClosed:
            print("Conexion WebSocket cerrada mientras se enviaba camara")
            raise

        except Exception as e:
            print("Error camara CSI:", e)

        await asyncio.sleep(CAMERA_DELAY_SECONDS)


# =====================================================
# MAIN
# =====================================================

async def main():
    print("--- ZERO FINAL INICIADA ---")
    print("Conectando a laptop:", LAPTOP_WS)

    teclado_task = asyncio.create_task(control_movimiento_teclado())
    repetir_task = asyncio.create_task(repetir_movimiento_periodico())

    try:
        while True:
            try:
                async with websockets.connect(LAPTOP_WS, max_size=None) as ws:
                    print("Zero conectada a la laptop")

                    await asyncio.gather(
                        enviar_telemetria(ws),
                        enviar_camara(ws),
                        recibir_comandos_desde_server(ws)
                    )

            except KeyboardInterrupt:
                raise

            except Exception as e:
                print("No se pudo conectar con la laptop:", e)
                print("Reintentando en 3 segundos...")
                await asyncio.sleep(3)

    finally:
        teclado_task.cancel()
        repetir_task.cancel()


try:
    asyncio.run(main())

except KeyboardInterrupt:
    print("\nCerrando Zero final...")

    try:
        uart.write(b"X\n")
        uart.flush()
    except Exception:
        pass

finally:
    try:
        uart.close()
    except Exception:
        pass

    try:
        picam2.stop()
    except Exception:
        pass
