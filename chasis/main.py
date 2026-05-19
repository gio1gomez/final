from machine import Pin, PWM, SoftI2C, UART
import time
import math

# =====================================================
# UART PICO 2 <-> PICO 1
# Pico 2 GP0 TX -> Pico 1 GP5 RX
# Pico 2 GP1 RX -> Pico 1 GP4 TX
# =====================================================

uart_main = UART(
    0,
    baudrate=9600,
    tx=Pin(0),
    rx=Pin(1)
)

# =====================================================
# VELOCIDADES
# =====================================================

VELOCIDAD_MOTOR_A = 100
VELOCIDAD_MOTOR_B = 100

TIMEOUT_SEGURIDAD_MS = 700

ultimo_tiempo_comando = time.ticks_ms()
estado_actual = "Q"

# =====================================================
# SERVO EN GP6 CON PULSOS MANUALES
# Este método es el que ya te funcionaba antes.
# Señal -> GP6
# =====================================================

SERVO_PIN = 6
servo_pin = Pin(SERVO_PIN, Pin.OUT)

SERVO_CENTRO = 90
SERVO_AVANCE = 95
SERVO_RETROCESO = 85

servo_angulo_actual = SERVO_CENTRO


def servo_pulso(angulo):
    angulo = max(0, min(180, angulo))

    min_us = 500
    max_us = 2500
    periodo_us = 20000

    pulso_us = int(min_us + (angulo / 180) * (max_us - min_us))

    servo_pin.value(1)
    time.sleep_us(pulso_us)

    servo_pin.value(0)
    time.sleep_us(periodo_us - pulso_us)


def mover_servo(angulo, repeticiones=8):
    global servo_angulo_actual

    servo_angulo_actual = max(0, min(180, angulo))

    for i in range(repeticiones):
        servo_pulso(servo_angulo_actual)


def mantener_servo():
    servo_pulso(servo_angulo_actual)


# =====================================================
# I2C SENSORES PICO 2
# SDA -> GP2
# SCL -> GP3
# =====================================================

i2c = SoftI2C(
    sda=Pin(2),
    scl=Pin(3),
    freq=10000
)

print("Bus I2C Pico 2 iniciado")
print("Dispositivos encontrados:", [hex(d) for d in i2c.scan()])


# =====================================================
# AHT10
# =====================================================

class AHT10:
    def __init__(self, i2c, address=0x38):
        self.i2c = i2c
        self.address = address
        self.init_sensor()

    def init_sensor(self):
        time.sleep_ms(100)

        try:
            self.i2c.writeto(self.address, bytes([0xBA]))
            time.sleep_ms(100)
        except Exception as e:
            print("Aviso AHT10 reset:", e)

        self.i2c.writeto(self.address, bytes([0xE1, 0x08, 0x00]))
        time.sleep_ms(100)

        print("AHT10 inicializado correctamente")

    def read(self):
        self.i2c.writeto(self.address, bytes([0xAC, 0x33, 0x00]))
        time.sleep_ms(100)

        data = self.i2c.readfrom(self.address, 6)

        if data[0] & 0x80:
            time.sleep_ms(20)
            data = self.i2c.readfrom(self.address, 6)

        raw_humidity = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
        raw_temperature = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]

        humidity = (raw_humidity * 100) / 1048576
        temperature = (raw_temperature * 200) / 1048576 - 50

        return temperature, humidity


# =====================================================
# MPU9250 / MPU6500 DEL GY-91
# =====================================================

class MPU9250:
    def __init__(self, i2c, address=0x68):
        self.i2c = i2c
        self.address = address

        self.i2c.writeto(self.address, bytes([0x75]))
        who_am_i = self.i2c.readfrom(self.address, 1)[0]
        print("WHO_AM_I MPU:", hex(who_am_i))

        self.i2c.writeto_mem(self.address, 0x6B, bytes([0x00]))
        time.sleep_ms(100)

        self.i2c.writeto_mem(self.address, 0x1C, bytes([0x00]))
        self.i2c.writeto_mem(self.address, 0x1B, bytes([0x00]))

        print("MPU inicializado correctamente")

    def read_signed_16(self, high, low):
        value = (high << 8) | low

        if value > 32767:
            value -= 65536

        return value

    def read(self):
        data = self.i2c.readfrom_mem(self.address, 0x3B, 14)

        ax_raw = self.read_signed_16(data[0], data[1])
        ay_raw = self.read_signed_16(data[2], data[3])
        az_raw = self.read_signed_16(data[4], data[5])

        gx_raw = self.read_signed_16(data[8], data[9])
        gy_raw = self.read_signed_16(data[10], data[11])
        gz_raw = self.read_signed_16(data[12], data[13])

        ax = ax_raw / 16384
        ay = ay_raw / 16384
        az = az_raw / 16384

        gx = gx_raw / 131
        gy = gy_raw / 131
        gz = gz_raw / 131

        roll = math.atan2(ay, az) * 180 / math.pi
        pitch = math.atan2(-ax, math.sqrt((ay * ay) + (az * az))) * 180 / math.pi

        return {
            "accel_x": ax,
            "accel_y": ay,
            "accel_z": az,
            "gyro_x": gx,
            "gyro_y": gy,
            "gyro_z": gz,
            "roll": roll,
            "pitch": pitch
        }


# =====================================================
# MOTORES PICO 2 - TRASEROS
# Motor A = lado izquierdo trasero
# Motor B = lado derecho trasero
# =====================================================

PWMA = 16
AIN2 = 17
AIN1 = 18

PWMB = 22
BIN1 = 20
BIN2 = 21

pwm_a = PWM(Pin(PWMA))
pwm_a.freq(20000)

ain1 = Pin(AIN1, Pin.OUT)
ain2 = Pin(AIN2, Pin.OUT)

pwm_b = PWM(Pin(PWMB))
pwm_b.freq(20000)

bin1 = Pin(BIN1, Pin.OUT)
bin2 = Pin(BIN2, Pin.OUT)


def velocidad_a_pwm(velocidad):
    velocidad = max(0, min(100, velocidad))
    return int(velocidad * 65535 / 100)


# =====================================================
# MOTOR A / IZQUIERDO TRASERO
# Este se deja normal.
# =====================================================

def motor_izquierdo_adelante():
    ain1.value(1)
    ain2.value(0)
    pwm_a.duty_u16(velocidad_a_pwm(VELOCIDAD_MOTOR_A))


def motor_izquierdo_atras():
    ain1.value(0)
    ain2.value(1)
    pwm_a.duty_u16(velocidad_a_pwm(VELOCIDAD_MOTOR_A))


def motor_izquierdo_detener():
    ain1.value(0)
    ain2.value(0)
    pwm_a.duty_u16(0)


# =====================================================
# MOTOR B / DERECHO TRASERO
# CORREGIDO:
# Este motor estaba girando al sentido contrario.
# Por eso se invirtió su lógica.
# =====================================================

def motor_derecho_adelante():
    bin1.value(0)
    bin2.value(1)
    pwm_b.duty_u16(velocidad_a_pwm(VELOCIDAD_MOTOR_B))


def motor_derecho_atras():
    bin1.value(1)
    bin2.value(0)
    pwm_b.duty_u16(velocidad_a_pwm(VELOCIDAD_MOTOR_B))


def motor_derecho_detener():
    bin1.value(0)
    bin2.value(0)
    pwm_b.duty_u16(0)


# =====================================================
# MOVIMIENTOS PICO 2
# =====================================================

def avanzar():
    motor_izquierdo_adelante()
    motor_derecho_adelante()
    mover_servo(SERVO_RETROCESO, repeticiones=5)


def retroceder():
    motor_izquierdo_atras()
    motor_derecho_atras()
    mover_servo(SERVO_AVANCE, repeticiones=5)


def izquierda():
    # Giro tanque izquierda:
    # lado izquierdo atrás, lado derecho adelante
    motor_izquierdo_atras()
    motor_derecho_adelante()
    mover_servo(SERVO_CENTRO, repeticiones=5)


def derecha():
    # Giro tanque derecha:
    # lado izquierdo adelante, lado derecho atrás
    motor_izquierdo_adelante()
    motor_derecho_atras()
    mover_servo(SERVO_CENTRO, repeticiones=5)


def detener():
    motor_izquierdo_detener()
    motor_derecho_detener()
    mover_servo(SERVO_CENTRO, repeticiones=5)


# =====================================================
# INICIALIZAR SENSORES PICO 2
# =====================================================

aht10 = None
mpu = None

try:
    if 0x38 in i2c.scan():
        aht10 = AHT10(i2c)
    else:
        print("AHT10 no detectado en 0x38")
except Exception as e:
    print("Error inicializando AHT10:", e)
    aht10 = None

try:
    if 0x68 in i2c.scan():
        mpu = MPU9250(i2c)
    else:
        print("MPU no detectado en 0x68")
except Exception as e:
    print("Error inicializando MPU:", e)
    mpu = None


# =====================================================
# SENSORES
# =====================================================

def formato(valor):
    if valor is None:
        return "NA"

    try:
        return "{:.2f}".format(valor)
    except Exception:
        return "NA"


def enviar_sensores_a_pico1():
    temperatura = None
    humedad = None
    imu = None

    if aht10 is not None:
        try:
            temperatura, humedad = aht10.read()
        except Exception as e:
            print("Error leyendo AHT10:", e)

    if mpu is not None:
        try:
            imu = mpu.read()
        except Exception as e:
            print("Error leyendo MPU:", e)

    if imu is None:
        linea = "P2S,{},{},NA,NA,NA,NA,NA,NA,NA,NA".format(
            formato(temperatura),
            formato(humedad)
        )
    else:
        linea = "P2S,{},{},{},{},{},{},{},{},{},{}".format(
            formato(temperatura),
            formato(humedad),
            formato(imu["accel_x"]),
            formato(imu["accel_y"]),
            formato(imu["accel_z"]),
            formato(imu["gyro_x"]),
            formato(imu["gyro_y"]),
            formato(imu["gyro_z"]),
            formato(imu["roll"]),
            formato(imu["pitch"])
        )

    uart_main.write((linea + "\n").encode())


# =====================================================
# COMANDOS DESDE PICO 1
# =====================================================

def aplicar_comando(comando):
    global estado_actual
    global ultimo_tiempo_comando

    comando = comando.strip().upper()

    if comando == "":
        return

    if comando == "X":
        comando = "Q"

    if comando not in ("W", "S", "A", "D", "Q", "F", "G"):
        return

    uart_main.write(("P2ACK," + comando + "\n").encode())

    if comando in ("W", "S", "A", "D"):
        ultimo_tiempo_comando = time.ticks_ms()

    if comando == estado_actual and comando in ("W", "S", "A", "D"):
        return

    if comando == "W":
        estado_actual = "W"
        avanzar()

    elif comando == "S":
        estado_actual = "S"
        retroceder()

    elif comando == "A":
        estado_actual = "A"
        izquierda()

    elif comando == "D":
        estado_actual = "D"
        derecha()

    elif comando == "Q":
        estado_actual = "Q"
        detener()

    elif comando == "F":
        mover_servo(servo_angulo_actual + 15, repeticiones=8)

    elif comando == "G":
        mover_servo(servo_angulo_actual - 15, repeticiones=8)


# =====================================================
# LEER UART DESDE PICO 1
# =====================================================

buffer_uart = ""


def leer_uart_main():
    global buffer_uart

    if uart_main.any():
        data = uart_main.read()

        if not data:
            return

        try:
            texto = data.decode()
        except Exception:
            texto = ""

        for caracter in texto:
            if caracter in ("\n", "\r"):
                comando = buffer_uart.strip()
                buffer_uart = ""

                if comando:
                    aplicar_comando(comando)

            else:
                if caracter.upper() in ("W", "A", "S", "D", "X", "Q", "F", "G"):
                    aplicar_comando(caracter.upper())
                    buffer_uart = ""
                else:
                    buffer_uart += caracter

                    if len(buffer_uart) > 20:
                        buffer_uart = ""


# =====================================================
# INICIO
# =====================================================

print("Pico 2 iniciada")
print("Motor B invertido para corregir direccion")
print("Servo en GP6 usando pulsos manuales")

detener()
time.sleep_ms(300)

print("Prueba servo Pico 2")
mover_servo(90, repeticiones=15)
time.sleep_ms(300)
mover_servo(120, repeticiones=15)
time.sleep_ms(300)
mover_servo(60, repeticiones=15)
time.sleep_ms(300)
mover_servo(90, repeticiones=15)

ultimo_envio_sensores = time.ticks_ms()

# =====================================================
# BUCLE PRINCIPAL
# =====================================================

while True:
    leer_uart_main()

    tiempo_actual = time.ticks_ms()

    if estado_actual in ("W", "S", "A", "D"):
        if time.ticks_diff(tiempo_actual, ultimo_tiempo_comando) > TIMEOUT_SEGURIDAD_MS:
            estado_actual = "Q"
            detener()

    # Mantener señal del servo sin bloquear mucho
    mantener_servo()

    # Enviar sensores cada 2 segundos para reducir carga y delay
    if time.ticks_diff(tiempo_actual, ultimo_envio_sensores) >= 2000:
        ultimo_envio_sensores = tiempo_actual

        # Si hay comando pendiente, primero comando
        if not uart_main.any():
            enviar_sensores_a_pico1()

    time.sleep_ms(1)