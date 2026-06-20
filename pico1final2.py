from machine import Pin, PWM, UART
import time

# =====================================================
# UART ZERO <-> PICO 1
# GP0 = TX hacia Zero
# GP1 = RX desde Zero
# =====================================================

uart_zero = UART(
    0,
    baudrate=9600,
    tx=Pin(0),
    rx=Pin(1)
)

# =====================================================
# UART PICO 1 <-> PICO 2
# GP4 = TX hacia Pico 2
# GP5 = RX desde Pico 2
# =====================================================

uart_pico2 = UART(
    1,
    baudrate=9600,
    tx=Pin(4),
    rx=Pin(5)
)

# =====================================================
# VELOCIDAD
# =====================================================

VELOCIDAD = 80
TIMEOUT_SEGURIDAD_MS = 700

ultimo_tiempo_comando = time.ticks_ms()
estado_actual = "Q"

# =====================================================
# MOTORES PICO 1 - DELANTEROS
# Motor 1 = lado izquierdo delantero
# Motor 2 = lado derecho delantero
# =====================================================

PWM1 = 10
IN1_M1 = 11
IN2_M1 = 12

PWM2 = 13
IN1_M2 = 15
IN2_M2 = 14

pwm1 = PWM(Pin(PWM1))
pwm1.freq(20000)

in1_m1 = Pin(IN1_M1, Pin.OUT)
in2_m1 = Pin(IN2_M1, Pin.OUT)

pwm2 = PWM(Pin(PWM2))
pwm2.freq(20000)

in1_m2 = Pin(IN1_M2, Pin.OUT)
in2_m2 = Pin(IN2_M2, Pin.OUT)

# =====================================================
# SERVO PICO 1
# Señal -> GP8
# =====================================================

SERVO_PIN = 8

servo = PWM(Pin(SERVO_PIN))
servo.freq(50)

SERVO_CENTRO = 90
SERVO_AVANCE = 95
SERVO_RETROCESO = 85


def mover_servo(angulo):
    angulo = max(0, min(180, angulo))

    min_us = 500
    max_us = 2500
    periodo_us = 20000

    pulso_us = int(min_us + (angulo / 180) * (max_us - min_us))
    duty = int((pulso_us / periodo_us) * 65535)

    servo.duty_u16(duty)


def velocidad_pwm(velocidad):
    velocidad = max(0, min(100, velocidad))
    return int(velocidad * 65535 / 100)


# =====================================================
# ENCODERS PICO 1
# Motor A = GPIO26 / GPIO27
# Motor B = GPIO22 / GPIO28
# =====================================================

ENC_A_1 = 26
ENC_A_2 = 27

ENC_B_1 = 22
ENC_B_2 = 28

enc_a_1 = Pin(ENC_A_1, Pin.IN, Pin.PULL_UP)
enc_a_2 = Pin(ENC_A_2, Pin.IN, Pin.PULL_UP)

enc_b_1 = Pin(ENC_B_1, Pin.IN, Pin.PULL_UP)
enc_b_2 = Pin(ENC_B_2, Pin.IN, Pin.PULL_UP)

encoder_a_count = 0
encoder_b_count = 0

encoder_a_last = 0
encoder_b_last = 0

ultimo_envio_encoders = time.ticks_ms()
ultimo_encoder_a_enviado = 0
ultimo_encoder_b_enviado = 0

ENCODER_SEND_MS = 250


def estado_encoder(pin_1, pin_2):
    return (pin_1.value() << 1) | pin_2.value()


def actualizar_encoder_a(pin):
    global encoder_a_count
    global encoder_a_last

    nuevo = estado_encoder(enc_a_1, enc_a_2)
    transicion = (encoder_a_last << 2) | nuevo

    if transicion in (0b0001, 0b0111, 0b1110, 0b1000):
        encoder_a_count += 1
    elif transicion in (0b0010, 0b1011, 0b1101, 0b0100):
        encoder_a_count -= 1

    encoder_a_last = nuevo


def actualizar_encoder_b(pin):
    global encoder_b_count
    global encoder_b_last

    nuevo = estado_encoder(enc_b_1, enc_b_2)
    transicion = (encoder_b_last << 2) | nuevo

    if transicion in (0b0001, 0b0111, 0b1110, 0b1000):
        encoder_b_count += 1
    elif transicion in (0b0010, 0b1011, 0b1101, 0b0100):
        encoder_b_count -= 1

    encoder_b_last = nuevo


def inicializar_encoders():
    global encoder_a_last
    global encoder_b_last

    encoder_a_last = estado_encoder(enc_a_1, enc_a_2)
    encoder_b_last = estado_encoder(enc_b_1, enc_b_2)

    enc_a_1.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=actualizar_encoder_a)
    enc_a_2.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=actualizar_encoder_a)

    enc_b_1.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=actualizar_encoder_b)
    enc_b_2.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=actualizar_encoder_b)

    print("Encoders inicializados")
    print("Motor A encoder: GP26 / GP27")
    print("Motor B encoder: GP22 / GP28")


def enviar_encoders_periodico():
    global ultimo_envio_encoders
    global ultimo_encoder_a_enviado
    global ultimo_encoder_b_enviado

    ahora = time.ticks_ms()

    if time.ticks_diff(ahora, ultimo_envio_encoders) >= ENCODER_SEND_MS:
        total_a = encoder_a_count
        total_b = encoder_b_count

        delta_a = total_a - ultimo_encoder_a_enviado
        delta_b = total_b - ultimo_encoder_b_enviado

        ultimo_encoder_a_enviado = total_a
        ultimo_encoder_b_enviado = total_b
        ultimo_envio_encoders = ahora

        enviar_zero(
            "P1ENC,{},{},{},{}".format(
                total_a,
                total_b,
                delta_a,
                delta_b
            )
        )


# =====================================================
# MOTOR IZQUIERDO DELANTERO
# =====================================================

def motor_izquierdo_adelante():
    in1_m1.value(1)
    in2_m1.value(0)
    pwm1.duty_u16(velocidad_pwm(VELOCIDAD))


def motor_izquierdo_atras():
    in1_m1.value(0)
    in2_m1.value(1)
    pwm1.duty_u16(velocidad_pwm(VELOCIDAD))


def motor_izquierdo_detener():
    in1_m1.value(0)
    in2_m1.value(0)
    pwm1.duty_u16(0)


# =====================================================
# MOTOR DERECHO DELANTERO
# =====================================================

def motor_derecho_adelante():
    in1_m2.value(1)
    in2_m2.value(0)
    pwm2.duty_u16(velocidad_pwm(VELOCIDAD))


def motor_derecho_atras():
    in1_m2.value(0)
    in2_m2.value(1)
    pwm2.duty_u16(velocidad_pwm(VELOCIDAD))


def motor_derecho_detener():
    in1_m2.value(0)
    in2_m2.value(0)
    pwm2.duty_u16(0)


# =====================================================
# MOVIMIENTOS PICO 1
# =====================================================

def avanzar():
    print("Pico 1: avanzar")
    mover_servo(SERVO_RETROCESO)

    motor_izquierdo_adelante()
    motor_derecho_atras()


def retroceder():
    print("Pico 1: retroceder")
    mover_servo(SERVO_AVANCE)

    motor_izquierdo_atras()
    motor_derecho_adelante()


def izquierda():
    print("Pico 1: giro tanque izquierda")
    mover_servo(SERVO_CENTRO)

    motor_izquierdo_adelante()
    motor_derecho_adelante()


def derecha():
    print("Pico 1: giro tanque derecha")
    mover_servo(SERVO_CENTRO)

    motor_izquierdo_atras()
    motor_derecho_atras()


def detener():
    print("Pico 1: detener")

    motor_izquierdo_detener()
    motor_derecho_detener()
    mover_servo(SERVO_CENTRO)


# =====================================================
# UART FUNCIONES
# =====================================================

def enviar_zero(texto):
    try:
        uart_zero.write((texto.strip() + "\n").encode())
    except Exception as e:
        print("Error enviando a Zero:", e)


def enviar_pico2(texto):
    try:
        uart_pico2.write((texto.strip() + "\n").encode())
        print("Pico 1 envio a Pico 2:", texto.strip())
    except Exception as e:
        print("Error enviando a Pico 2:", e)


# =====================================================
# PROCESAR COMANDOS DESDE ZERO
# =====================================================

def aplicar_comando(comando):
    global estado_actual
    global ultimo_tiempo_comando

    comando = comando.strip().upper()

    if comando == "":
        return

    if comando == "X":
        comando = "Q"

    if comando not in ("W", "A", "S", "D", "Q", "F", "G"):
        return

    print("Pico 1 recibio de Zero:", comando)

    enviar_pico2(comando)
    enviar_zero("P1ACK," + comando)

    if comando in ("W", "A", "S", "D"):
        ultimo_tiempo_comando = time.ticks_ms()

    if comando == estado_actual and comando in ("W", "A", "S", "D"):
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
        print("F reenviado a Pico 2")

    elif comando == "G":
        print("G reenviado a Pico 2")


# =====================================================
# LEER UART DESDE ZERO
# =====================================================

buffer_zero = ""


def leer_uart_zero():
    global buffer_zero

    if uart_zero.any():
        data = uart_zero.read()

        if not data:
            return

        try:
            texto = data.decode()
        except Exception:
            texto = ""

        for caracter in texto:
            if caracter in ("\n", "\r"):
                comando = buffer_zero.strip()
                buffer_zero = ""

                if comando:
                    aplicar_comando(comando)

            else:
                if caracter.upper() in ("W", "A", "S", "D", "X", "Q", "F", "G"):
                    aplicar_comando(caracter.upper())
                    buffer_zero = ""
                else:
                    buffer_zero += caracter

                    if len(buffer_zero) > 20:
                        buffer_zero = ""


# =====================================================
# LEER DATOS DESDE PICO 2 Y REENVIAR A ZERO
# =====================================================

buffer_pico2 = ""


def leer_uart_pico2():
    global buffer_pico2

    if uart_pico2.any():
        data = uart_pico2.read()

        if not data:
            return

        try:
            texto = data.decode()
        except Exception:
            texto = ""

        for caracter in texto:
            if caracter in ("\n", "\r"):
                linea = buffer_pico2.strip()
                buffer_pico2 = ""

                if linea:
                    print("Pico 1 recibio de Pico 2:", linea)
                    enviar_zero("DE_PICO2," + linea)

            else:
                buffer_pico2 += caracter

                if len(buffer_pico2) > 250:
                    buffer_pico2 = ""


# =====================================================
# INICIO
# =====================================================

print("Pico 1 iniciada")
print("Pico 1 = motores delanteros")
print("Motor 1 = delantero izquierdo")
print("Motor 2 = delantero derecho")
print("UART Zero: GP0 TX / GP1 RX")
print("UART Pico 2: GP4 TX / GP5 RX")
print("Encoders Motor A: GP26 / GP27")
print("Encoders Motor B: GP22 / GP28")

detener()
inicializar_encoders()

# =====================================================
# BUCLE PRINCIPAL
# =====================================================

while True:
    leer_uart_zero()
    leer_uart_pico2()
    enviar_encoders_periodico()

    tiempo_actual = time.ticks_ms()

    if estado_actual in ("W", "A", "S", "D"):
        if time.ticks_diff(tiempo_actual, ultimo_tiempo_comando) > TIMEOUT_SEGURIDAD_MS:
            print("Timeout Pico 1 -> detener")
            estado_actual = "Q"
            detener()
            enviar_pico2("Q")

    time.sleep_ms(5)