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

# IP de tu laptop
LAPTOP_WS = "ws://172.31.76.66:8766"
# UART hacia/desde las Picos
PUERTO_UART = "/dev/serial0"
BAUDRATE = 9600

# Cámara CSI
CAMERA_WIDTH = 320
CAMERA_HEIGHT = 240
CAMERA_FPS = 15
JPEG_QUALITY = 55
CAMERA_DELAY_SECONDS = 0.15

# Reenvío de movimiento para que las Picos no hagan timeout
MOVEMENT_REPEAT_SECONDS = 0.20

# Comandos permitidos para movimiento
COMANDOS_MOVIMIENTO = ["W", "A", "S", "D", "X"]

# =====================================================
# UART
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
# LOCKS
# =====================================================

send_lock = asyncio.Lock()
uart_write_lock = asyncio.Lock()

ultimos_encoders = {
    "total_a": "0",
    "total_b": "0",
    "delta_a": "0",
    "delta_b": "0"
}


async def enviar_json(ws, mensaje):
    async with send_lock:
        await ws.send(json.dumps(mensaje))


async def enviar_comando_uart(comando):
    comando = comando.upper()

    if comando not in COMANDOS_MOVIMIENTO:
        return

    async with uart_write_lock:
        uart.write((comando + "\n").encode())
        uart.flush()


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


# =====================================================
# CONTROL DE MOVIMIENTO
# =====================================================

async def control_movimiento():
    print("\nControl de movimiento activo")
    print("--------------------------------")
    print("W -> avanzar")
    print("S -> retroceder")
    print("A -> izquierda")
    print("D -> derecha")
    print("X -> detener")
    print("Ctrl + C -> salir")
    print("--------------------------------\n")

    ultimo_movimiento = ""
    ultimo_envio = time.time()

    while True:
        try:
            tecla = leer_tecla_no_bloqueante()

            if tecla:
                if tecla == "\x03":
                    await enviar_comando_uart("X")
                    raise KeyboardInterrupt

                tecla = tecla.upper()

                if tecla in ["W", "A", "S", "D"]:
                    ultimo_movimiento = tecla
                    await enviar_comando_uart(tecla)
                    ultimo_envio = time.time()
                    print(f"Comando enviado: {tecla}")

                elif tecla == "X":
                    ultimo_movimiento = ""
                    await enviar_comando_uart("X")
                    ultimo_envio = time.time()
                    print("Comando enviado: X - detenido")

            if ultimo_movimiento in ["W", "A", "S", "D"]:
                if time.time() - ultimo_envio >= MOVEMENT_REPEAT_SECONDS:
                    await enviar_comando_uart(ultimo_movimiento)
                    ultimo_envio = time.time()

        except KeyboardInterrupt:
            raise

        except Exception as e:
            print("Error control movimiento:", e)

        await asyncio.sleep(0.02)


# =====================================================
# PARSEO DE TELEMETRÍA
# =====================================================

def convertir_linea_a_csv_telemetria(linea):
    linea = linea.strip()

    if not linea:
        return None

    if "P2S," in linea:
        linea = linea[linea.find("P2S,"):]

    partes = linea.split(",")

    if len(partes) == 8:
        try:
            valores = [float(x) for x in partes]
            return ",".join(str(v) for v in valores)
        except ValueError:
            return None

    if len(partes) >= 11 and partes[0] == "P2S":
        try:
            temp = float(partes[1])
            hum = float(partes[2])
            ax = float(partes[3])
            ay = float(partes[4])
            az = float(partes[5])
            gx = float(partes[6])
            gy = float(partes[7])
            gz = float(partes[8])

            return f"{temp},{hum},{ax},{ay},{az},{gx},{gy},{gz}"
        except ValueError:
            return None

    return None


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
        return

    t, h, ax, ay, az, gx, gy, gz = datos

    print("\n" + "=" * 42)
    print(f"Temp: {t} C | Hum: {h} %")
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

                        if mostrar_encoders(linea):
                            continue

                        csv_telemetria = convertir_linea_a_csv_telemetria(linea)

                        if csv_telemetria is None:
                            continue

                        mostrar_telemetria(csv_telemetria)

                        mensaje = {
                            "type": "telemetry",
                            "data": csv_telemetria
                        }

                        await enviar_json(ws, mensaje)

                    else:
                        buffer_uart += c

                        if len(buffer_uart) > 250:
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
    print("--- ZERO UNIFICADA INICIADA ---")
    print("Conectando a laptop:", LAPTOP_WS)

    movimiento_task = asyncio.create_task(control_movimiento())

    try:
        while True:
            try:
                async with websockets.connect(LAPTOP_WS, max_size=None) as ws:
                    print("Zero conectada a la laptop")

                    await asyncio.gather(
                        enviar_telemetria(ws),
                        enviar_camara(ws)
                    )

            except KeyboardInterrupt:
                raise

            except Exception as e:
                print("No se pudo conectar con la laptop:", e)
                print("Reintentando en 3 segundos...")
                await asyncio.sleep(3)

    finally:
        movimiento_task.cancel()


try:
    asyncio.run(main())

except KeyboardInterrupt:
    print("\nCerrando Zero unificada...")
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
