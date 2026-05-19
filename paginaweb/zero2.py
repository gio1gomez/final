import asyncio
import websockets
import serial
import json
import base64
import cv2

# IP de tu laptop
LAPTOP_WS = "ws://192.168.1.11:8766"

# UART desde la Pico
ser = serial.Serial('/dev/serial0', 9600, timeout=1)

# Camara USB en la Zero
cam = cv2.VideoCapture("/dev/video0", cv2.CAP_V4L2)
cam.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
cam.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
cam.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

if cam.isOpened():
    print("Camara USB abierta correctamente en /dev/video0")
else:
    print("No se pudo abrir la camara USB en /dev/video0")

send_lock = asyncio.Lock()


async def enviar_telemetria(ws):
    print("Leyendo telemetria del Pico por UART...")

    while True:
        try:
            if ser.in_waiting > 0:
                linea = ser.readline().decode('utf-8', errors='ignore').strip()

                if linea:
                    datos = linea.split(',')

                    if len(datos) == 8:
                        t, h, ax, ay, az, gx, gy, gz = datos

                        print("\n" + "=" * 35)
                        print(f"Temp: {t} C | Hum: {h} %")
                        print(f"Accel: X={ax}, Y={ay}, Z={az} (g)")
                        print(f"Gyro: X={gx}, Y={gy}, Z={gz} (deg/s)")
                        print("=" * 35)

                        mensaje = {
                            "type": "telemetry",
                            "data": linea
                        }

                        async with send_lock:
                            await ws.send(json.dumps(mensaje))
                    else:
                        print("Dato incompleto:", linea)

        except Exception as e:
            print("Error telemetria:", e)

        await asyncio.sleep(0.05)


async def enviar_camara(ws):
    print("Enviando camara a la laptop...")

    while True:
        try:
            ok, frame = cam.read()

            if ok:
                _, buffer = cv2.imencode(
                    ".jpg",
                    frame,
                    [int(cv2.IMWRITE_JPEG_QUALITY), 55]
                )

                img_b64 = base64.b64encode(buffer).decode("utf-8")

                mensaje = {
                    "type": "frame",
                    "image": img_b64
                }

                async with send_lock:
                    await ws.send(json.dumps(mensaje))
            else:
                print("No se pudo leer la camara")

        except Exception as e:
            print("Error camara:", e)

        # Aproximadamente 6 FPS
        await asyncio.sleep(0.15)


async def main():
    print("--- ZERO INICIADA ---")
    print("Conectando a laptop:", LAPTOP_WS)

    while True:
        try:
            async with websockets.connect(LAPTOP_WS, max_size=None) as ws:
                print("Zero conectada a la laptop")

                await asyncio.gather(
                    enviar_telemetria(ws),
                    enviar_camara(ws)
                )

        except Exception as e:
            print("No se pudo conectar con la laptop:", e)
            print("Reintentando en 3 segundos...")
            await asyncio.sleep(3)


try:
    asyncio.run(main())

except KeyboardInterrupt:
    print("\nCerrando Zero...")
    ser.close()
    cam.release()