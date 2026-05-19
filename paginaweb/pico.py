# pyrefly: ignore [missing-import]
from machine import Pin, SoftI2C, UART
import time

# UART0 en Raspberry Pi Pico:
# TX = GP0 (Pin 1 físico)
# RX = GP1 (Pin 2 físico)
uart = UART(0, baudrate=9600, tx=Pin(0), rx=Pin(1))

# I2C para sensores
i2c = SoftI2C(sda=Pin(2), scl=Pin(3), freq=50000)

class AHT10:
    def __init__(self, i2c):
        self.i2c = i2c
        time.sleep_ms(100)
        self.i2c.writeto(0x38, bytes([0xE1, 0x08, 0x00]))
    def read(self):
        self.i2c.writeto(0x38, bytes([0xAC, 0x33, 0x00]))
        time.sleep_ms(100)
        d = self.i2c.readfrom(0x38, 6)
        h = ((d[1] << 12) | (d[2] << 4) | (d[3] >> 4)) / 1048576 * 100
        t = (((d[3] & 0x0F) << 16) | (d[4] << 8) | d[5]) / 1048576 * 200 - 50
        return t, h

class MPU9250:
    def __init__(self, i2c):
        self.i2c = i2c
        self.i2c.writeto_mem(0x68, 0x6B, bytes([0x00]))
    def read(self):
        d = self.i2c.readfrom_mem(0x68, 0x3B, 14)
        def conv(h, l):
            v = (h << 8) | l
            return v if v <= 32767 else v - 65536
        return {
            "ax": conv(d[0], d[1])/16384, "ay": conv(d[2], d[3])/16384, "az": conv(d[4], d[5])/16384,
            "gx": conv(d[8], d[9])/131, "gy": conv(d[10], d[11])/131, "gz": conv(d[12], d[13])/131
        }

# Inicialización de sensores
try:
    sensor_clima = AHT10(i2c)
    sensor_imu = MPU9250(i2c)
except:
    print("Error al detectar sensores en bus I2C")

print("Transmitiendo datos por UART (GP0/GP1)...")

while True:
    try:
        t, h = sensor_clima.read()
        imu = sensor_imu.read()
        
        # Formato CSV para facilitar el parseo en la Pi Zero
        datos = "{:.2f},{:.2f},{:.2f},{:.2f},{:.2f},{:.1f},{:.1f},{:.1f}\n".format(
            t, h, imu["ax"], imu["ay"], imu["az"], imu["gx"], imu["gy"], imu["gz"]
        )
        
        uart.write(datos)
        # Mostrar en consola local para verificar que funciona
        print("Enviando:", datos.strip())
        
    except Exception as e:
        print("Error lectura:", e)
        
    time.sleep(1)