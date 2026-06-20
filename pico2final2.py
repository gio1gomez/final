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


def mover_servo(angulo, repeticiones=5):
    global servo_angulo_actual

    servo_angulo_actual = max(0, min(180, angulo))

    for _ in range(repeticiones):
        servo_pulso(servo_angulo_actual)


def mantener_servo():
    servo_pulso(servo_angulo_actual)


# =====================================================
# ESC
# Señal ESC -> GP19
#
# Lógica:
# - Armado en 1000 us durante 5 segundos
# - Sube suavemente a 40%
# - Luego sube:
#   40% -> 50% -> 60% -> 70% -> 80%
# - La subida de 40% a 80% tarda 2 segundos
# - Se mantiene al 80%
# - X/Q solo detiene motores, NO apaga ESC
# - E apaga ESC manualmente
# =====================================================

ESC_PIN = 19

ESC_MIN_US = 1000
ESC_MAX_US = 2500

ESC_40_US = 1600
ESC_50_US = 1750
ESC_60_US = 1900
ESC_70_US = 2050
ESC_80_US = 2200

ESC_ARM_TIME_MS = 5000

# 40 -> 50 -> 60 -> 70 -> 80
# 4 saltos en 2000 ms = 500 ms por salto
ESC_STEP_INTERVAL_MS = 500

ESC_RAMP_STEP_US = 5
ESC_RAMP_INTERVAL_MS = 15

ESC_ESTADO_APAGADO = 0
ESC_ESTADO_ARMANDO = 1
ESC_ESTADO_SUBIENDO_40 = 2
ESC_ESTADO_ESCALONADO_40_80 = 3
ESC_ESTADO_EN_80 = 4

esc = PWM(Pin(ESC_PIN))
esc.freq(50)

esc_estado = ESC_ESTADO_APAGADO
esc_actual_us = ESC_MIN_US
esc_objetivo_us = ESC_MIN_US
esc_inicio_estado_ms = time.ticks_ms()
esc_ultimo_ramp_ms = time.ticks_ms()

esc_porcentaje_actual = 0
esc_ultimo_paso_ms = time.ticks_ms()


def esc_us_to_duty(pulso_us):
    periodo_us = 20000
    return int((pulso_us / periodo_us) * 65535)


def esc_write_us(pulso_us):
    global esc_actual_us

    pulso_us = max(ESC_MIN_US, min(ESC_MAX_US, pulso_us))
    esc_actual_us = pulso_us
    esc.duty_u16(esc_us_to_duty(pulso_us))


def esc_set_objetivo(pulso_us):
    global esc_objetivo_us

    esc_objetivo_us = max(ESC_MIN_US, min(ESC_MAX_US, pulso_us))


def esc_pulso_por_porcentaje(porcentaje):
    if porcentaje <= 40:
        return ESC_40_US

    if porcentaje <= 50:
        return ESC_50_US

    if porcentaje <= 60:
        return ESC_60_US

    if porcentaje <= 70:
        return ESC_70_US

    return ESC_80_US


def esc_rampa_update():
    global esc_actual_us
    global esc_ultimo_ramp_ms

    ahora = time.ticks_ms()

    if time.ticks_diff(ahora, esc_ultimo_ramp_ms) < ESC_RAMP_INTERVAL_MS:
        return

    esc_ultimo_ramp_ms = ahora

    if esc_actual_us < esc_objetivo_us:
        esc_write_us(min(esc_actual_us + ESC_RAMP_STEP_US, esc_objetivo_us))

    elif esc_actual_us > esc_objetivo_us:
        esc_write_us(max(esc_actual_us - ESC_RAMP_STEP_US, esc_objetivo_us))


def encender_esc_secuencia():
    global esc_estado
    global esc_inicio_estado_ms
    global esc_objetivo_us
    global esc_porcentaje_actual
    global esc_ultimo_paso_ms

    if esc_estado != ESC_ESTADO_APAGADO:
        return

    print("ESC: iniciando armado en 1000 us")

    esc_porcentaje_actual = 0
    esc_write_us(ESC_MIN_US)
    esc_set_objetivo(ESC_MIN_US)

    esc_estado = ESC_ESTADO_ARMANDO
    esc_inicio_estado_ms = time.ticks_ms()
    esc_ultimo_paso_ms = esc_inicio_estado_ms


def apagar_esc():
    global esc_estado
    global esc_objetivo_us
    global esc_inicio_estado_ms
    global esc_porcentaje_actual

    print("ESC: apagado manual por comando E")

    esc_estado = ESC_ESTADO_APAGADO
    esc_inicio_estado_ms = time.ticks_ms()
    esc_porcentaje_actual = 0

    esc_set_objetivo(ESC_MIN_US)
    esc_write_us(ESC_MIN_US)


def esc_update():
    global esc_estado
    global esc_inicio_estado_ms
    global esc_porcentaje_actual
    global esc_ultimo_paso_ms

    ahora = time.ticks_ms()

    if esc_estado == ESC_ESTADO_APAGADO:
        esc_write_us(ESC_MIN_US)
        return

    if esc_estado == ESC_ESTADO_ARMANDO:
        esc_write_us(ESC_MIN_US)

        if time.ticks_diff(ahora, esc_inicio_estado_ms) >= ESC_ARM_TIME_MS:
            print("ESC: armado terminado, subiendo a 40%")
            esc_set_objetivo(ESC_40_US)
            esc_estado = ESC_ESTADO_SUBIENDO_40
            esc_inicio_estado_ms = ahora

        return

    if esc_estado == ESC_ESTADO_SUBIENDO_40:
        esc_rampa_update()

        if esc_actual_us == ESC_40_US:
            print("ESC: en 40%, iniciando subida gradual 40 -> 80")
            esc_porcentaje_actual = 40
            esc_ultimo_paso_ms = ahora
            esc_estado = ESC_ESTADO_ESCALONADO_40_80
            esc_inicio_estado_ms = ahora

        return

    if esc_estado == ESC_ESTADO_ESCALONADO_40_80:
        esc_rampa_update()

        if time.ticks_diff(ahora, esc_ultimo_paso_ms) >= ESC_STEP_INTERVAL_MS:
            esc_ultimo_paso_ms = ahora

            if esc_porcentaje_actual < 80:
                esc_porcentaje_actual += 10

                pulso_objetivo = esc_pulso_por_porcentaje(esc_porcentaje_actual)
                esc_set_objetivo(pulso_objetivo)

                print(
                    "ESC: subiendo a",
                    str(esc_porcentaje_actual) + "%",
                    "-",
                    pulso_objetivo,
                    "us"
                )

        if esc_porcentaje_actual >= 80 and esc_actual_us == ESC_80_US:
            print("ESC: en 80%, manteniendo")
            esc_estado = ESC_ESTADO_EN_80
            esc_inicio_estado_ms = ahora

        return

    if esc_estado == ESC_ESTADO_EN_80:
        esc_write_us(ESC_80_US)
        return


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
# BMP280 INTERNO DEL GY-91
# Dirección usual: 0x76 o 0x77
# =====================================================

class BMP280:
    def __init__(self, i2c, address=0x76):
        self.i2c = i2c
        self.address = address

        calib = self.i2c.readfrom_mem(self.address, 0x88, 24)

        self.dig_T1 = self.u16(calib[1], calib[0])
        self.dig_T2 = self.s16(calib[3], calib[2])
        self.dig_T3 = self.s16(calib[5], calib[4])

        self.dig_P1 = self.u16(calib[7], calib[6])
        self.dig_P2 = self.s16(calib[9], calib[8])
        self.dig_P3 = self.s16(calib[11], calib[10])
        self.dig_P4 = self.s16(calib[13], calib[12])
        self.dig_P5 = self.s16(calib[15], calib[14])
        self.dig_P6 = self.s16(calib[17], calib[16])
        self.dig_P7 = self.s16(calib[19], calib[18])
        self.dig_P8 = self.s16(calib[21], calib[20])
        self.dig_P9 = self.s16(calib[23], calib[22])

        self.t_fine = 0

        self.i2c.writeto_mem(self.address, 0xF4, bytes([0x27]))
        self.i2c.writeto_mem(self.address, 0xF5, bytes([0xA0]))

        print("BMP280 interno del GY-91 inicializado en", hex(self.address))

    def u16(self, msb, lsb):
        return (msb << 8) | lsb

    def s16(self, msb, lsb):
        value = (msb << 8) | lsb

        if value > 32767:
            value -= 65536

        return value

    def read_raw(self):
        data = self.i2c.readfrom_mem(self.address, 0xF7, 6)

        raw_p = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        raw_t = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)

        return raw_t, raw_p

    def compensate_temperature(self, adc_t):
        var1 = (((adc_t >> 3) - (self.dig_T1 << 1)) * self.dig_T2) >> 11

        var2 = (
            (((adc_t >> 4) - self.dig_T1) *
             ((adc_t >> 4) - self.dig_T1)) >> 12
        )

        var2 = (var2 * self.dig_T3) >> 14

        self.t_fine = var1 + var2

        temperature = ((self.t_fine * 5 + 128) >> 8) / 100

        return temperature

    def compensate_pressure(self, adc_p):
        var1 = self.t_fine - 128000
        var2 = var1 * var1 * self.dig_P6
        var2 = var2 + ((var1 * self.dig_P5) << 17)
        var2 = var2 + (self.dig_P4 << 35)

        var1 = ((var1 * var1 * self.dig_P3) >> 8) + ((var1 * self.dig_P2) << 12)
        var1 = (((1 << 47) + var1) * self.dig_P1) >> 33

        if var1 == 0:
            return None

        pressure = 1048576 - adc_p
        pressure = (((pressure << 31) - var2) * 3125) // var1

        var1 = (self.dig_P9 * (pressure >> 13) * (pressure >> 13)) >> 25
        var2 = (self.dig_P8 * pressure) >> 19
        pressure = ((pressure + var1 + var2) >> 8) + (self.dig_P7 << 4)
        pressure = pressure / 256

        return pressure

    def read(self):
        raw_t, raw_p = self.read_raw()

        temperature = self.compensate_temperature(raw_t)
        pressure = self.compensate_pressure(raw_p)

        if pressure is None:
            return temperature, None, None

        altitude = 44330 * (1 - (pressure / 101325) ** 0.1903)

        return temperature, pressure, altitude


# =====================================================
# MOTORES PICO 2 - TRASEROS
# Motor A = lado izquierdo trasero
# Motor B = lado derecho trasero
# =====================================================

PWMA = 16
AIN2 = 17
AIN1 = 18

PWMB = 10
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
    motor_izquierdo_atras()
    motor_derecho_adelante()
    mover_servo(SERVO_RETROCESO, repeticiones=5)


def retroceder():
    motor_izquierdo_adelante()
    motor_derecho_atras()
    mover_servo(SERVO_AVANCE, repeticiones=5)


def izquierda():
    motor_izquierdo_atras()
    motor_derecho_atras()
    mover_servo(SERVO_CENTRO, repeticiones=5)


def derecha():
    motor_izquierdo_adelante()
    motor_derecho_adelante()
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
bmp280 = None

try:
    dispositivos = i2c.scan()

    if 0x76 in dispositivos:
        bmp280 = BMP280(i2c, address=0x76)
    elif 0x77 in dispositivos:
        bmp280 = BMP280(i2c, address=0x77)
    else:
        print("BMP280 interno del GY-91 no detectado en 0x76/0x77")

except Exception as e:
    print("Error inicializando BMP280 interno del GY-91:", e)
    bmp280 = None

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
    altitud = None

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

    if bmp280 is not None:
        try:
            _, _, altitud = bmp280.read()
        except Exception as e:
            print("Error leyendo BMP280 interno del GY-91:", e)

    if imu is None:
        linea = "P2S,{},{},NA,NA,NA,NA,NA,NA,{},NA,NA".format(
            formato(temperatura),
            formato(humedad),
            formato(altitud)
        )
    else:
        linea = "P2S,{},{},{},{},{},{},{},{},{},{},{}".format(
            formato(temperatura),
            formato(humedad),
            formato(imu["accel_x"]),
            formato(imu["accel_y"]),
            formato(imu["accel_z"]),
            formato(imu["gyro_x"]),
            formato(imu["gyro_y"]),
            formato(imu["gyro_z"]),
            formato(altitud),
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

    if comando not in ("W", "S", "A", "D", "Q", "F", "G", "E"):
        return

    uart_main.write(("P2ACK," + comando + "\n").encode())

    if comando in ("W", "S", "A", "D"):
        ultimo_tiempo_comando = time.ticks_ms()

        if esc_estado == ESC_ESTADO_APAGADO:
            encender_esc_secuencia()

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
        print("Q/X recibido: motores detenidos, ESC sigue activa")

    elif comando == "E":
        estado_actual = "Q"
        detener()
        apagar_esc()

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
                if caracter.upper() in ("W", "A", "S", "D", "X", "Q", "F", "G", "E"):
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
print("ESC en GP19")
print("ESC: armado -> 40% -> 50% -> 60% -> 70% -> 80% en 2 segundos")
print("X/Q solo detiene motores, NO apaga ESC")
print("Comando E apaga ESC manualmente")

detener()
encender_esc_secuencia()

ultimo_envio_sensores = time.ticks_ms()

# =====================================================
# BUCLE PRINCIPAL
# =====================================================

while True:
    leer_uart_main()
    esc_update()

    tiempo_actual = time.ticks_ms()

    if estado_actual in ("W", "S", "A", "D"):
        if time.ticks_diff(tiempo_actual, ultimo_tiempo_comando) > TIMEOUT_SEGURIDAD_MS:
            estado_actual = "Q"
            detener()
            print("Timeout UART -> motores detenidos, ESC sigue activa")

    mantener_servo()

    if time.ticks_diff(tiempo_actual, ultimo_envio_sensores) >= 2000:
        ultimo_envio_sensores = tiempo_actual

        if not uart_main.any():
            enviar_sensores_a_pico1()

    time.sleep_ms(1)