"""
============================================================================
 INTERFAZ DE CONFIGURACION DEL MOTOR LOGICO  (Horner XL4 - Modbus TCP)
 VERSION CON TIMERS, PULSO TEMPORIZADO, CONTADORES RETENTIVOS,
 RESET FISICO CONFIGURABLE Y CONTADOR MANTENIDO
============================================================================

Python NO reprograma el PLC.
Solo escribe registros %R.

MAPEO %Rn -> Modbus holding 0-based = n + 2999

SALIDAS:
  Q10 = verde
  Q11 = amarilla
  Q12 = roja

ENTRADAS:
  I1 = codigo 1
  I2 = codigo 2
  I3 = codigo 3   (tambien S1 de la banda, NC)
  I4 = codigo 4   (tambien S2 de la banda, NC)
  I7 = codigo 5

TIPO FISICO:
  I1, I3, I4 = NA
  I2, I7     = NC

----------------------------------------------------------------------------
SELECTOR DE EQUIPO (Ladder maestro nuevo)
----------------------------------------------------------------------------
El PLC es uno solo y ahora atiende dos equipos. SysMode decide cual manda:
  SysMode = 0  standby (todo apagado, VFD en paro)
  SysMode = 1  maletin (motor por salida + secuenciador)
  SysMode = 2  banda   (VFD + sensores S1/S2 + torreta)

El ladder evalua  MasterEnable := (SysMode = 1) AND (CmdWord <> 0),
por lo que escribir solo CmdWord=1 YA NO enciende nada. habilitar() escribe
ambos registros.

----------------------------------------------------------------------------
MAPA DE REGISTROS %R
----------------------------------------------------------------------------
  %R00001  CmdWord            habilitacion general del maletin
  %R00002  GenStopSrc         paro general
  %R00003  ComboIndex         (lectura) entradas fisicas
  %R00004  OutStatus          (lectura) salidas Q10/Q11/Q12
  %R00005  SysMode            selector de equipo  [NUEVO]
  %R00009  BandFreqRef        frecuencia de la banda en Hz  [NUEVO]
  %R00010..%R00039            bloques de configuracion por salida
  %R00040..%R00054            acumulados, resets y reset fisico
  %R00060..%R00087            secuenciador de pasos
  %R00090  BandCmdWord        habilitacion de la banda  [NUEVO]
  %R00091  BandS1Preset       espera de S1 en segundos  [NUEVO]
  %R00092  BandS2Preset       espera de S2 en segundos  [NUEVO]
  %R00093  BandS1RetrigTime   anti-retrigger S1 (default 8 s)  [NUEVO]
  %R00094  BandS2RetrigTime   anti-retrigger S2 (default 12 s)  [NUEVO]
  %R00095  BandVFDReset       reset del VFD, auto-clear  [NUEVO]
  %R00096  BandDirCmd         0 = derecha | 1 = izquierda  [NUEVO]
  %R00097  BandProdCountReg   (lectura) productos contados  [NUEVO]
  %R00500  control_vfd        (lectura) 18=der | 34=izq | 1=paro
  %R00502  vfd_freq_read      (lectura) velocidad real x100
  %R00504  vfd_freq_send      (lectura) BandFreqRef x 100
  %R00506  vfd_reset          (lectura) pulso de reset
  %R00508  leer_vfd_speed     (lectura) velocidad legible en Hz

Las direcciones marcadas [NUEVO] fuera de %R00009 y %R00500.. venian como
"%R nueva" en el ladder: se asignaron aqui en huecos libres y deben
declararse con esas mismas direcciones en la tabla de variables de Cscape.
============================================================================
"""

import sys
import json

from pymodbus.client import ModbusTcpClient


PLC_IP = "192.168.3.12"
PLC_PORT = 502
UNIT_ID = 1


def R(n: int) -> int:
    return n + 2999


# ---------------------------------------------------------------------------
# REGISTROS GENERALES
# ---------------------------------------------------------------------------
ADDR_CMD = R(1)
ADDR_GENSTOP = R(2)
ADDR_INDEX = R(3)
ADDR_STATUS = R(4)


# ---------------------------------------------------------------------------
# SELECTOR DE EQUIPO  (Ladder maestro nuevo, secciones 4 y 12.0)
# El ladder ahora exige  MasterEnable := (SysMode = 1) AND (CmdWord <> 0).
# Sin escribir SysMode, CmdWord por si solo YA NO habilita el maletin.
#   0 = standby (todo apagado)   1 = maletin   2 = banda transportadora
# ---------------------------------------------------------------------------
ADDR_SYSMODE = R(5)

SYSMODE_STANDBY = 0
SYSMODE_MALETIN = 1
SYSMODE_BANDA = 2

SYSMODES = {
    "standby": SYSMODE_STANDBY,
    "maletin": SYSMODE_MALETIN,
    "banda": SYSMODE_BANDA,
}


# ---------------------------------------------------------------------------
# BLOQUES POR SALIDA
# Q10 usa %R10..%R19
# Q11 usa %R20..%R29
# Q12 usa %R30..%R39
# ---------------------------------------------------------------------------
OUT_BASE = {
    "Q10": 10,
    "Q11": 20,
    "Q12": 30,
    "VERDE": 10,
    "AMARILLA": 20,
    "ROJA": 30,
}


# ---------------------------------------------------------------------------
# CODIGOS DE ENTRADA
# ---------------------------------------------------------------------------
SRC = {
    "NINGUNA": 0,
    "I1": 1,
    "I2": 2,
    "I3": 3,
    "I4": 4,
    "I7": 5,
}


INPUT_NC = {
    "I1": False,
    "I2": True,
    "I3": False,
    "I4": False,
    "I7": True,
}


# ---------------------------------------------------------------------------
# MODOS
# ---------------------------------------------------------------------------
MODE_OFF = 0
MODE_DIRECTO = 1
MODE_ENCLAVADO = 2

TIMER_OFF = 0
TIMER_RET_ON_DELAY = 1
TIMER_PULSO_TEMPORIZADO = 2

COUNTER_OFF = 0
COUNTER_UP_BASE = 1
COUNTER_UP_MANTENIDO = 2


# ---------------------------------------------------------------------------
# ACUMULADOS
# ---------------------------------------------------------------------------
ADDR_TACC = {
    "Q10": R(40),
    "Q11": R(41),
    "Q12": R(42),
    "VERDE": R(40),
    "AMARILLA": R(41),
    "ROJA": R(42),
}

ADDR_CACC = {
    "Q10": R(43),
    "Q11": R(44),
    "Q12": R(45),
    "VERDE": R(43),
    "AMARILLA": R(44),
    "ROJA": R(45),
}


# ---------------------------------------------------------------------------
# RESET POR SOFTWARE
# ---------------------------------------------------------------------------
ADDR_RESET_TIMER = {
    "Q10": R(46),
    "Q11": R(47),
    "Q12": R(48),
    "VERDE": R(46),
    "AMARILLA": R(47),
    "ROJA": R(48),
}

ADDR_RESET_COUNTER = {
    "Q10": R(49),
    "Q11": R(50),
    "Q12": R(51),
    "VERDE": R(49),
    "AMARILLA": R(50),
    "ROJA": R(51),
}


# ---------------------------------------------------------------------------
# RESET FISICO CONFIGURABLE PARA CONTADOR
# ---------------------------------------------------------------------------
ADDR_COUNTER_RESET_SRC = {
    "Q10": R(52),
    "Q11": R(53),
    "Q12": R(54),
    "VERDE": R(52),
    "AMARILLA": R(53),
    "ROJA": R(54),
}


# ---------------------------------------------------------------------------
# SECUENCIADOR DE PASOS  (capa opcional sobre el motor por salida)
# Si SeqEnable=1, controla Q10/Q11/Q12 por pasos (ej. semaforo). Cada paso
# enciende las salidas de su mascara durante SeqDur[paso] segundos y avanza
# con el pulso de 1 s del PLC. Si SeqEnable=0, el motor por salida funciona
# igual que siempre (no se altera la logica existente).
# ---------------------------------------------------------------------------
ADDR_SEQ_ENABLE     = R(60)   # 0 = apagado, 1 = activo
ADDR_SEQ_START_SRC  = R(61)   # codigo de entrada que arranca (1..5)
ADDR_SEQ_MODE       = R(62)   # 0 = una vez, 1 = ciclico
ADDR_SEQ_STEP_COUNT = R(63)   # numero de pasos (1..SEQ_MAX_STEPS)
ADDR_SEQ_RESET_SRC  = R(64)   # entrada que aborta/reinicia (0 = ninguna)
# R(65) SeqActiveFlag, R(66) SeqCurStep, R(67) SeqStepAcc -> solo lectura
SEQ_MASK_BASE = 70            # %R70..%R77 mascara de salidas por paso
SEQ_DUR_BASE  = 80            # %R80..%R87 duracion en segundos por paso
SEQ_MAX_STEPS = 8

SEQ_MODES = {"once": 0, "loop": 1}

# Bit de cada salida dentro de la mascara de un paso (bit0=Q10, bit1=Q11, bit2=Q12)
OUT_BIT = {
    "Q10": 1, "Q11": 2, "Q12": 4,
    "VERDE": 1, "AMARILLA": 2, "ROJA": 4,
}


# ---------------------------------------------------------------------------
# BANDA TRANSPORTADORA + VFD  (seccion 12 del Ladder maestro)
#
# La banda NO tiene PLC propio: corre en el mismo XL4 del maletin y solo se
# activa con SysMode = 2 AND BandCmdWord <> 0. Cuando esta activa, el ladder
# sobreescribe Q10/Q11/Q12 con la torreta (verde/amarillo/rojo).
#
# Los registros %R00500..%R00508 los escribe/lee el PLC hacia el VFD; desde
# aqui solo se LEEN para monitoreo (no se escriben nunca).
#
# NOTA: BandFreqRef es la unica direccion fija que trae el ladder (%R00009).
# El resto venia marcado como "%R nueva": se asignan aqui en el bloque libre
# %R00090..%R00097 y DEBEN declararse con esas mismas direcciones en la tabla
# de variables de Cscape.
#
# BandProdCount es una variable RETENTIVA interna del ladder y no se puede
# leer por Modbus tal cual. Para exponerla hay que publicarla en un %R al
# final de la seccion 12 del programa de Cscape:
#
#     BandProdCountReg := BandProdCount;    (* %R00097 *)
#
# donde BandProdCountReg es un INT nuevo mapeado a %R00097 (no retentivo).
# ---------------------------------------------------------------------------
ADDR_BAND_CMD        = R(90)   # BandCmdWord      <>0 = habilitar banda
ADDR_BAND_FREQ_REF   = R(9)    # BandFreqRef      Hz (el PLC lo multiplica x100)
ADDR_BAND_S1_PRESET  = R(91)   # BandS1Preset     espera de S1 en segundos
ADDR_BAND_S2_PRESET  = R(92)   # BandS2Preset     espera de S2 en segundos
ADDR_BAND_S1_RETRIG  = R(93)   # BandS1RetrigTime anti-retrigger S1 (default 8)
ADDR_BAND_S2_RETRIG  = R(94)   # BandS2RetrigTime anti-retrigger S2 (default 12)
ADDR_BAND_VFD_RESET  = R(95)   # BandVFDReset     <>0 = reset VFD (auto-clear)
ADDR_BAND_DIR        = R(96)   # BandDirCmd       0 = derecha | 1 = izquierda
ADDR_BAND_PROD_COUNT = R(97)   # BandProdCountReg (lectura) productos contados

# Tope del contador de productos en el ladder (CTU PV=9999: al llegar, vuelve a 0)
BAND_PROD_COUNT_MAX = 9999

# Registros del VFD (solo lectura desde Python)
ADDR_VFD_CONTROL     = R(500)  # 18 = derecha | 34 = izquierda | 1 = paro
ADDR_VFD_FREQ_READ   = R(502)  # velocidad real del VFD (escalada x100)
ADDR_VFD_FREQ_SEND   = R(504)  # BandFreqRef x 100
ADDR_VFD_RESET       = R(506)  # pulso de reset (auto-clear en 12.1)
ADDR_VFD_SPEED_HMI   = R(508)  # %R00502 / 100 -> valor legible

VFD_CMD_DERECHA   = 18
VFD_CMD_IZQUIERDA = 34
VFD_CMD_PARO      = 1

# Sensores fisicos de la banda (NC: el ladder los invierte con NOT I[])
# S1 = I3 = I[2]   S2 = I4 = I[3]
BAND_SENSOR_S1 = "I3"
BAND_SENSOR_S2 = "I4"

BAND_DIR = {
    "derecha": 0, "right": 0, "der": 0, "cw": 0, "0": 0,
    "izquierda": 1, "left": 1, "izq": 1, "ccw": 1, "1": 1,
}

BAND_S1_RETRIG_DEFAULT = 8    # segundos (TOF ~8 s del ladder original)
BAND_S2_RETRIG_DEFAULT = 12   # segundos (TOF ~12 s del ladder original)


class XL4:
    def __init__(self, ip=PLC_IP, port=PLC_PORT, unit=UNIT_ID):
        self.ip = ip
        self.port = port
        self.unit = unit
        self.client = ModbusTcpClient(ip, port=port)

    # -----------------------------------------------------------------------
    # CONEXION
    # -----------------------------------------------------------------------
    def connect(self):
        if not self.client.connect():
            raise ConnectionError(f"No conecta a {self.ip}:{self.port}")
        print("Conectado al XL4.")

    def close(self):
        self.client.close()

    # -----------------------------------------------------------------------
    # MODBUS
    # -----------------------------------------------------------------------
    def _w(self, addr, value):
        value = int(value) & 0xFFFF

        try:
            rr = self.client.write_register(addr, value, device_id=self.unit)
        except TypeError:
            rr = self.client.write_register(addr, value, slave=self.unit)

        if rr is not None and hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"Error escribiendo registro Modbus {addr}: {rr}")

    def _r(self, addr):
        try:
            rr = self.client.read_holding_registers(addr, count=1, device_id=self.unit)
        except TypeError:
            rr = self.client.read_holding_registers(addr, count=1, slave=self.unit)

        if rr is None:
            raise RuntimeError(f"Sin respuesta leyendo registro Modbus {addr}")

        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"Error leyendo registro Modbus {addr}: {rr}")

        return rr.registers[0]

    # -----------------------------------------------------------------------
    # VALIDACIONES
    # -----------------------------------------------------------------------
    def _src(self, nombre):
        if nombre is None:
            return 0

        n = str(nombre).upper()

        if n not in SRC:
            raise ValueError(f"Entrada no valida: {nombre}. Usa: {list(SRC.keys())}")

        return SRC[n]

    def _out(self, salida):
        s = str(salida).upper()

        if s not in OUT_BASE:
            raise ValueError(f"Salida no valida: {salida}. Usa: {list(OUT_BASE.keys())}")

        return s

    def _es_nc(self, nombre):
        if nombre is None:
            return False
        return INPUT_NC.get(str(nombre).upper(), False)

    # -----------------------------------------------------------------------
    # ESCRITURA DE CONFIGURACION BASE
    # -----------------------------------------------------------------------
    def _escribir_salida(self, salida, mode, srcA, srcB, stop, enable, flags=0):
        s = self._out(salida)
        base = OUT_BASE[s]

        self._w(R(base + 0), mode)
        self._w(R(base + 1), srcA)
        self._w(R(base + 2), srcB)
        self._w(R(base + 3), stop)
        self._w(R(base + 4), enable)
        self._w(R(base + 5), flags)

    # -----------------------------------------------------------------------
    # COMANDOS GENERALES
    # -----------------------------------------------------------------------
    def habilitar(self, on=True):
        # El ladder nuevo exige SysMode=1 ademas de CmdWord<>0 para que
        # MasterEnable sea TRUE. Se escribe el modo junto con el comando.
        if on:
            self._w(ADDR_SYSMODE, SYSMODE_MALETIN)
            self._w(ADDR_CMD, 1)
        else:
            self._w(ADDR_CMD, 0)
            self._w(ADDR_SYSMODE, SYSMODE_STANDBY)
        print(f"Sistema {'HABILITADO (modo maletin)' if on else 'DESHABILITADO (standby)'}")

    def paro_general(self, entrada):
        self._w(ADDR_GENSTOP, self._src(entrada))
        print(f"Paro general -> {entrada}")

    def quitar_paro_general(self):
        self._w(ADDR_GENSTOP, 0)
        print("Paro general desactivado")

    def apagar(self, salida):
        self._escribir_salida(salida, MODE_OFF, 0, 0, 0, 0, 0)
        print(f"{salida}: desactivada")

    # -----------------------------------------------------------------------
    # LOGICAS BASE
    # -----------------------------------------------------------------------
    def directo(self, salida, entrada, habilitacion=None):
        self._escribir_salida(
            salida=salida,
            mode=MODE_DIRECTO,
            srcA=self._src(entrada),
            srcB=0,
            stop=0,
            enable=self._src(habilitacion),
            flags=0,
        )

        print(
            f"{salida}: DIRECTO con {entrada}"
            + (f", habilita {habilitacion}" if habilitacion else "")
        )

    def enclavar(self, salida, arranque, paro=None, habilitacion=None):
        self._escribir_salida(
            salida=salida,
            mode=MODE_ENCLAVADO,
            srcA=self._src(arranque),
            srcB=0,
            stop=self._src(paro),
            enable=self._src(habilitacion),
            flags=0,
        )

        print(
            f"{salida}: ENCLAVADO arranque={arranque}"
            + (f" paro={paro}" if paro else "")
            + (f" habilita={habilitacion}" if habilitacion else "")
        )

    def combinacional(self, salida, a, b, op="OR", enclavado=False, paro=None):
        mode = MODE_ENCLAVADO if enclavado else MODE_DIRECTO
        op = op.upper()

        if op == "OR":
            srcA = self._src(a)
            srcB = self._src(b)
            srcEn = 0

        elif op == "AND":
            srcA = self._src(a)
            srcB = 0
            srcEn = self._src(b)

        else:
            raise ValueError("op debe ser 'AND' u 'OR'.")

        self._escribir_salida(
            salida=salida,
            mode=mode,
            srcA=srcA,
            srcB=srcB,
            stop=self._src(paro),
            enable=srcEn,
            flags=0,
        )

        print(
            f"{salida}: {op}({a},{b})"
            + (" enclavado" if enclavado else "")
            + (f" paro={paro}" if paro else "")
        )

    # -----------------------------------------------------------------------
    # TIMER
    # -----------------------------------------------------------------------
    def configurar_timer(self, salida, segundos):
        s = self._out(salida)
        base = OUT_BASE[s]
        segundos = int(segundos)

        if segundos < 0 or segundos > 32767:
            raise ValueError("El preset del timer debe estar entre 0 y 32767 segundos.")

        self._w(R(base + 6), TIMER_RET_ON_DELAY)
        self._w(R(base + 7), segundos)

        print(f"{salida}: TIMER retentivo configurado a {segundos} s")

    def configurar_pulso_salida(self, salida, segundos):
        """
        Aplica TimerMode = 2 a una salida que ya tenga logica base configurada.
        """
        s = self._out(salida)
        base = OUT_BASE[s]
        segundos = int(segundos)

        if segundos < 1 or segundos > 32767:
            raise ValueError("El tiempo debe estar entre 1 y 32767 segundos.")

        self._w(R(base + 6), TIMER_PULSO_TEMPORIZADO)
        self._w(R(base + 7), segundos)

        print(f"{salida}: pulso temporizado configurado a {segundos} s")

    def pulso_temporizado(self, salida, entrada, segundos):
        """
        Configura una salida directa con una entrada y pulso temporizado.
        """
        self.directo(salida, entrada)
        self.configurar_pulso_salida(salida, segundos)

        print(f"{salida}: prende con {entrada} durante {segundos} segundos y luego se apaga")

    def quitar_timer(self, salida):
        s = self._out(salida)
        base = OUT_BASE[s]

        self._w(R(base + 6), TIMER_OFF)
        self._w(R(base + 7), 0)

        print(f"{salida}: TIMER desactivado")

    def reset_timer(self, salida):
        s = self._out(salida)
        self._w(ADDR_RESET_TIMER[s], 1)

        print(f"{salida}: reset de TIMER enviado")

    # -----------------------------------------------------------------------
    # CONTADOR
    # -----------------------------------------------------------------------
    def configurar_contador(self, salida, conteos):
        """
        CounterMode = 1.
        La salida solo prende si la entrada base está activa y el contador llegó al preset.
        """
        s = self._out(salida)
        base = OUT_BASE[s]
        conteos = int(conteos)

        if conteos < 0 or conteos > 32767:
            raise ValueError("El preset del contador debe estar entre 0 y 32767.")

        self._w(R(base + 8), COUNTER_UP_BASE)
        self._w(R(base + 9), conteos)

        print(f"{salida}: CONTADOR retentivo configurado a {conteos} conteos")

    def configurar_contador_mantenido(self, salida, conteos):
        """
        CounterMode = 2.

        La salida se mantiene encendida cuando CounterAcc >= CounterPreset,
        aunque la entrada de conteo ya no esté presionada.

        Se apaga únicamente al resetear el contador.
        """
        s = self._out(salida)
        base = OUT_BASE[s]
        conteos = int(conteos)

        if conteos < 1 or conteos > 32767:
            raise ValueError("El preset del contador debe estar entre 1 y 32767.")

        self._w(R(base + 8), COUNTER_UP_MANTENIDO)
        self._w(R(base + 9), conteos)

        print(f"{salida}: CONTADOR mantenido configurado a {conteos} conteos")

    def quitar_contador(self, salida):
        s = self._out(salida)
        base = OUT_BASE[s]

        self._w(R(base + 8), COUNTER_OFF)
        self._w(R(base + 9), 0)

        print(f"{salida}: CONTADOR desactivado")

    def reset_contador(self, salida):
        s = self._out(salida)
        self._w(ADDR_RESET_COUNTER[s], 1)

        print(f"{salida}: reset de CONTADOR enviado")

    def configurar_reset_contador(self, salida, entrada):
        """
        Define qué entrada física resetea el contador de una salida.

        entrada:
          None o "NINGUNA" = sin reset físico
          "I1", "I2", "I3", "I4", "I7"
        """
        s = self._out(salida)
        self._w(ADDR_COUNTER_RESET_SRC[s], self._src(entrada))

        if entrada is None or str(entrada).upper() == "NINGUNA":
            print(f"{salida}: reset físico de contador desactivado")
        else:
            print(f"{salida}: contador se resetea con {entrada}")

    def quitar_reset_contador(self, salida):
        s = self._out(salida)
        self._w(ADDR_COUNTER_RESET_SRC[s], 0)

        print(f"{salida}: reset físico de contador desactivado")

    # -----------------------------------------------------------------------
    # SECUENCIADOR DE PASOS
    # -----------------------------------------------------------------------
    def configurar_secuencia(self, start, steps, mode="once", reset=None):
        """Configura el secuenciador (ej. semaforo) y lo activa (SeqEnable=1).

        start  : entrada que arranca la secuencia ('I1'..'I7').
        steps  : lista de pasos {"outputs": ["Q10",...], "duration_s": N}.
        mode   : 'once' (una vez) o 'loop' (ciclico).
        reset  : entrada que aborta/reinicia (opcional).
        """
        modo = mode if isinstance(mode, int) else SEQ_MODES.get(str(mode).lower(), 0)

        if not isinstance(steps, list) or not (1 <= len(steps) <= SEQ_MAX_STEPS):
            raise ValueError(f"La secuencia debe tener entre 1 y {SEQ_MAX_STEPS} pasos.")

        self._w(ADDR_SEQ_START_SRC, self._src(start))
        self._w(ADDR_SEQ_MODE, modo)
        self._w(ADDR_SEQ_STEP_COUNT, len(steps))
        self._w(ADDR_SEQ_RESET_SRC, self._src(reset))

        for i in range(SEQ_MAX_STEPS):
            if i < len(steps):
                mask = 0
                for o in steps[i].get("outputs", []):
                    key = str(o).upper()
                    if key not in OUT_BIT:
                        raise ValueError(f"Paso {i + 1}: salida no valida '{o}'.")
                    mask |= OUT_BIT[key]
                dur = int(steps[i].get("duration_s", 0))
                if dur < 1 or dur > 32767:
                    raise ValueError(f"Paso {i + 1}: duration_s {dur} fuera de [1, 32767].")
            else:
                mask, dur = 0, 0
            self._w(R(SEQ_MASK_BASE + i), mask)
            self._w(R(SEQ_DUR_BASE + i), dur)

        self._w(ADDR_SEQ_ENABLE, 1)
        print(f"SECUENCIA activada: arranque={start} modo={mode} pasos={len(steps)}"
              + (f" reset={reset}" if reset else ""))

    def quitar_secuencia(self):
        """Apaga el secuenciador (SeqEnable=0) y limpia su configuracion."""
        self._w(ADDR_SEQ_ENABLE, 0)
        self._w(ADDR_SEQ_START_SRC, 0)
        self._w(ADDR_SEQ_MODE, 0)
        self._w(ADDR_SEQ_STEP_COUNT, 0)
        self._w(ADDR_SEQ_RESET_SRC, 0)
        for i in range(SEQ_MAX_STEPS):
            self._w(R(SEQ_MASK_BASE + i), 0)
            self._w(R(SEQ_DUR_BASE + i), 0)
        print("SECUENCIA desactivada")

    # -----------------------------------------------------------------------
    # SELECTOR DE EQUIPO  (SysMode)
    # -----------------------------------------------------------------------
    def seleccionar_modo(self, modo):
        """Fija SysMode: 'standby'/0, 'maletin'/1 o 'banda'/2.

        Solo un subsistema puede estar activo a la vez: con SysMode=1 la banda
        queda deshabilitada y el VFD en paro; con SysMode=2 el motor por salida
        y el secuenciador quedan deshabilitados."""
        if isinstance(modo, int):
            valor = modo
        else:
            clave = str(modo).lower()
            if clave not in SYSMODES:
                raise ValueError(f"Modo no valido: {modo}. Usa {list(SYSMODES.keys())}")
            valor = SYSMODES[clave]

        if valor not in (SYSMODE_STANDBY, SYSMODE_MALETIN, SYSMODE_BANDA):
            raise ValueError("SysMode debe ser 0 (standby), 1 (maletin) o 2 (banda).")

        self._w(ADDR_SYSMODE, valor)
        print(f"SysMode -> {valor} "
              f"({'standby' if valor == 0 else 'maletin' if valor == 1 else 'banda'})")

    def leer_modo(self):
        """Lee SysMode del PLC."""
        return self._r(ADDR_SYSMODE)

    # -----------------------------------------------------------------------
    # BANDA TRANSPORTADORA + VFD
    # -----------------------------------------------------------------------
    def _dir_banda(self, direccion):
        if direccion is None:
            return None
        if isinstance(direccion, int):
            if direccion not in (0, 1):
                raise ValueError("La direccion debe ser 0 (derecha) o 1 (izquierda).")
            return direccion
        clave = str(direccion).lower()
        if clave not in BAND_DIR:
            raise ValueError(f"Direccion no valida: {direccion}. Usa 'derecha' o 'izquierda'.")
        return BAND_DIR[clave]

    def configurar_banda(self, frecuencia_hz=None, espera_s1=None, espera_s2=None,
                         retrigger_s1=None, retrigger_s2=None, direccion=None):
        """Escribe la configuracion de la banda SIN habilitarla.

        frecuencia_hz  : referencia de frecuencia al VFD (BandFreqRef, %R00009).
        espera_s1/s2   : segundos que la banda se detiene al detectar S1 / S2.
        retrigger_s1/s2: bloqueo anti-retrigger tras el rearranque (segundos).
        direccion      : 'derecha' (VFD 18) o 'izquierda' (VFD 34).
        """
        if frecuencia_hz is not None:
            hz = int(frecuencia_hz)
            if hz < 0 or hz > 32767:
                raise ValueError("La frecuencia debe estar entre 0 y 32767 Hz.")
            self._w(ADDR_BAND_FREQ_REF, hz)

        for valor, addr, etiqueta in (
            (espera_s1, ADDR_BAND_S1_PRESET, "espera de S1"),
            (espera_s2, ADDR_BAND_S2_PRESET, "espera de S2"),
            (retrigger_s1, ADDR_BAND_S1_RETRIG, "anti-retrigger de S1"),
            (retrigger_s2, ADDR_BAND_S2_RETRIG, "anti-retrigger de S2"),
        ):
            if valor is None:
                continue
            v = int(valor)
            if v < 0 or v > 32767:
                raise ValueError(f"El tiempo de {etiqueta} debe estar entre 0 y 32767 s.")
            self._w(addr, v)

        d = self._dir_banda(direccion)
        if d is not None:
            self._w(ADDR_BAND_DIR, d)

        print(
            "BANDA configurada"
            + (f" | frecuencia={frecuencia_hz} Hz" if frecuencia_hz is not None else "")
            + (f" | espera S1={espera_s1} s" if espera_s1 is not None else "")
            + (f" | espera S2={espera_s2} s" if espera_s2 is not None else "")
            + (f" | retrigger S1={retrigger_s1} s" if retrigger_s1 is not None else "")
            + (f" | retrigger S2={retrigger_s2} s" if retrigger_s2 is not None else "")
            + (f" | direccion={'izquierda' if d == 1 else 'derecha'}" if d is not None else "")
        )

    def habilitar_banda(self, on=True):
        """Arranca o detiene la banda (SysMode=2 + BandCmdWord).

        Al apagarla se vuelve a standby: el ladder pone el VFD en paro (1) y
        Q10/Q11/Q12 regresan al control del motor por salida."""
        if on:
            self._w(ADDR_CMD, 0)            # el maletin no puede estar activo a la vez
            self._w(ADDR_SYSMODE, SYSMODE_BANDA)
            self._w(ADDR_BAND_CMD, 1)
            print("BANDA HABILITADA (SysMode=2)")
        else:
            self._w(ADDR_BAND_CMD, 0)
            self._w(ADDR_SYSMODE, SYSMODE_STANDBY)
            print("BANDA DESHABILITADA (standby, VFD en paro)")

    def parar_banda(self):
        """Paro de la banda dejando la configuracion escrita."""
        self.habilitar_banda(False)

    def banda_frecuencia(self, hz):
        """Cambia la referencia de frecuencia del VFD en caliente."""
        self.configurar_banda(frecuencia_hz=hz)

    def banda_direccion(self, direccion):
        """Cambia el sentido de giro ('derecha' = 18 | 'izquierda' = 34)."""
        self.configurar_banda(direccion=direccion)

    def reset_vfd(self):
        """Pulso de reset al VFD. El ladder lo auto-limpia en el siguiente scan."""
        self._w(ADDR_BAND_VFD_RESET, 1)
        print("VFD: reset enviado")

    def leer_banda(self):
        """Estado de la banda: comando al VFD, velocidad real y torreta."""
        control = self._r(ADDR_VFD_CONTROL)
        vel_hmi = self._r(ADDR_VFD_SPEED_HMI)
        freq_raw = self._r(ADDR_VFD_FREQ_READ)
        idx = self._r(ADDR_INDEX)
        status = self._r(ADDR_STATUS)
        productos = self._r(ADDR_BAND_PROD_COUNT)

        # S1/S2 son NC: el ladder los normaliza con NOT I[]. Aqui se replica
        # sobre los bits crudos de ComboIndex (bit2 = I3, bit3 = I4).
        s1 = 0 if (idx >> 2) & 1 else 1
        s2 = 0 if (idx >> 3) & 1 else 1

        datos = {
            "vfd_control": control,
            "marcha": control in (VFD_CMD_DERECHA, VFD_CMD_IZQUIERDA),
            "direccion": ("izquierda" if control == VFD_CMD_IZQUIERDA
                          else "derecha" if control == VFD_CMD_DERECHA else "paro"),
            "velocidad_hz": vel_hmi,
            "velocidad_raw": freq_raw,
            "S1": s1,
            "S2": s2,
            "productos": productos,
            "torreta": {
                "verde": status & 1,
                "amarilla": (status >> 1) & 1,
                "roja": (status >> 2) & 1,
            },
        }

        print(
            f"BANDA | VFD={control} ({datos['direccion']}) "
            f"vel={vel_hmi} Hz | S1={s1} S2={s2} | "
            f"productos={productos} | "
            f"torreta V={datos['torreta']['verde']} "
            f"A={datos['torreta']['amarilla']} R={datos['torreta']['roja']}"
        )

        return datos

    def leer_contador_productos(self):
        """Productos contados por la banda (BandProdCount publicado en %R00097).

        El ladder incrementa el contador cada vez que S1 completa su espera y
        libera la banda; al llegar a BAND_PROD_COUNT_MAX vuelve a 0. Es
        retentivo en el PLC: sobrevive a un corte de energia."""
        n = self._r(ADDR_BAND_PROD_COUNT)
        print(f"BANDA: productos contados = {n}")
        return n

    # -----------------------------------------------------------------------
    # RESET GENERAL
    # -----------------------------------------------------------------------
    def reset_todo(self, borrar_acumulados=True):
        self._w(ADDR_CMD, 0)

        # Standby: apaga banda y VFD antes de reconfigurar. Si SysMode quedara
        # en 2 de una carga anterior, la torreta seguiria mandando en Q10..Q12.
        self._w(ADDR_BAND_CMD, 0)
        self._w(ADDR_SYSMODE, SYSMODE_STANDBY)

        # Apagar el secuenciador antes que nada (si quedo activo de una carga
        # anterior, dejaria de controlar las salidas al reconfigurar).
        self.quitar_secuencia()

        for s in ("Q10", "Q11", "Q12"):
            self.apagar(s)
            self.quitar_timer(s)
            self.quitar_contador(s)
            self.quitar_reset_contador(s)

            if borrar_acumulados:
                self.reset_timer(s)
                self.reset_contador(s)

        # El reset de timer/contador es un COMANDO MOMENTANEO: se pone en 1 para
        # que el PLC borre el acumulado, pero DEBE volver a 0. Si se queda en 1,
        # el contador queda retenido en reset y NUNCA cuenta (acumulado siempre 0).
        # Se libera aqui, despues de que el lazo de arriba ya dio tiempo de sobra
        # al PLC para ver el pulso de reset en sus escaneos.
        if borrar_acumulados:
            for s in ("Q10", "Q11", "Q12"):
                self._w(ADDR_RESET_TIMER[s], 0)
                self._w(ADDR_RESET_COUNTER[s], 0)

        self._w(ADDR_GENSTOP, 0)

        print("Configuracion reiniciada.")

    # -----------------------------------------------------------------------
    # LECTURAS
    # -----------------------------------------------------------------------
    def leer_estado(self):
        idx = self._r(ADDR_INDEX)
        status = self._r(ADDR_STATUS)

        I1 = idx & 1
        I2 = (idx >> 1) & 1
        I3 = (idx >> 2) & 1
        I4 = (idx >> 3) & 1
        I7 = (idx >> 4) & 1

        Q10 = status & 1
        Q11 = (status >> 1) & 1
        Q12 = (status >> 2) & 1

        # I3/I4 son ademas los sensores NC de la banda (S1/S2 = NOT I[]).
        S1 = 0 if I3 else 1
        S2 = 0 if I4 else 1

        print(
            f"Entradas electricas: "
            f"I1={I1} I2={I2} I3={I3} I4={I4} I7={I7}  |  "
            f"Banda: S1={S1} S2={S2}  |  "
            f"Salidas: Q10={Q10} Q11={Q11} Q12={Q12}"
        )

        return idx, status

    def leer_acumulados(self):
        datos = {}

        for s in ("Q10", "Q11", "Q12"):
            timer_s = self._r(ADDR_TACC[s])
            contador = self._r(ADDR_CACC[s])

            datos[s] = {
                "timer_s": timer_s,
                "contador": contador,
            }

        print(
            "Acumulados | "
            f"Q10: T={datos['Q10']['timer_s']}s C={datos['Q10']['contador']} | "
            f"Q11: T={datos['Q11']['timer_s']}s C={datos['Q11']['contador']} | "
            f"Q12: T={datos['Q12']['timer_s']}s C={datos['Q12']['contador']}"
        )

        return datos


# ===========================================================================
# DESPACHADOR JSON  ->  LLAMADAS A XL4
# ===========================================================================
# La IA NO genera geometria ladder ni registros: devuelve este JSON dual.
# Python lee SOLO la parte de configuracion del motor (output/logic/timer/
# counter/system/reset_before) y la traduce a llamadas de la clase XL4.
# Los campos de presentacion del JSON dual ("expr", "comment", "view"...) son
# del frontend para dibujar el ladder y aqui se ignoran.
#
# Forma esperada (ver tambien CONTRACT del proyecto):
#   {
#     "name": "Demo maletin",
#     "device": "maletin_basico",
#     "reset_before": true,
#     "system": { "enable": true, "global_stop": null },
#     "outputs": [
#       { "output": "Q11",
#         "logic": { "mode": "combinacional", "a": "I1", "b": "I3", "op": "OR" },
#         "timer": { "type": "pulse", "preset_s": 5 },
#         "counter": null,
#         "expr": "I1 + I3" },                # <- solo para el render del front
#       { "output": "Q10",
#         "logic": { "mode": "directo", "source": "I2" },
#         "counter": { "type": "up_held", "preset": 3, "reset_input": "I4" } }
#     ]
#   }
# ---------------------------------------------------------------------------

ENTRADAS_VALIDAS = set(SRC.keys())                 # NINGUNA, I1, I2, I3, I4, I7
SALIDAS_VALIDAS = set(OUT_BASE.keys())             # Q10..Q12 + alias de color
MODOS_LOGICA = {"off", "directo", "enclavado", "combinacional"}
TIPOS_TIMER = {"on_delay", "pulse"}
TIPOS_COUNTER = {"up", "up_held"}


def _es_entrada_valida(nombre):
    """Una entrada del JSON es valida si es None o uno de los codigos SRC."""
    return nombre is None or str(nombre).upper() in ENTRADAS_VALIDAS


def _canon_salida(salida):
    """Numero de bloque base de una salida, para detectar duplicados aunque
    se mezclen alias ('Q10' y 'VERDE' son la misma salida fisica)."""
    return OUT_BASE.get(str(salida).upper())


def _entero_en_rango(valor, low, high, etiqueta, errores):
    try:
        v = int(valor)
    except (TypeError, ValueError):
        errores.append(f"{etiqueta}: '{valor}' no es un entero.")
        return None
    if v < low or v > high:
        errores.append(f"{etiqueta}: {v} fuera de rango [{low}, {high}].")
    return v


def validar_config(cfg) -> list:
    """Valida el JSON sin tocar Modbus. Devuelve la lista de errores
    (vacia = valido). Pensado para que el backend muestre fallos claros
    antes de enviar nada al PLC."""
    errores = []
    if not isinstance(cfg, dict):
        return ["El JSON raiz no es un objeto."]

    outputs = cfg.get("outputs")
    seq = cfg.get("sequence")
    band = cfg.get("band")
    if not isinstance(outputs, list):
        outputs = []
    # Una config valida necesita al menos salidas, una secuencia o la banda.
    if not outputs and not seq and not band:
        errores.append("Falta 'outputs' o esta vacio: debe haber al menos una salida "
                       "(o una 'sequence', o un bloque 'band').")

    vistos = set()
    for i, o in enumerate(outputs):
        tag = f"salida {i + 1}"
        if not isinstance(o, dict):
            errores.append(f"{tag}: no es un objeto.")
            continue

        salida = o.get("output")
        tag = f"salida {i + 1} ({salida})"
        if str(salida).upper() not in SALIDAS_VALIDAS:
            errores.append(f"{tag}: salida invalida. Usa {sorted({'Q10', 'Q11', 'Q12'})}.")
        else:
            canon = _canon_salida(salida)
            if canon in vistos:
                errores.append(f"{tag}: salida repetida (ya configurada antes).")
            vistos.add(canon)

        lg = o.get("logic") or {"mode": "off"}
        if not isinstance(lg, dict):
            errores.append(f"{tag}: 'logic' no es un objeto.")
            lg = {"mode": "off"}
        mode = str(lg.get("mode", "off")).lower()
        if mode not in MODOS_LOGICA:
            errores.append(f"{tag}: mode '{mode}' no soportado. Usa {sorted(MODOS_LOGICA)}.")

        if mode == "directo":
            if not lg.get("source"):
                errores.append(f"{tag}: 'directo' requiere 'source'.")
            for campo in ("source", "enable"):
                if not _es_entrada_valida(lg.get(campo)):
                    errores.append(f"{tag}: '{campo}'='{lg.get(campo)}' no es una entrada valida.")
        elif mode == "enclavado":
            if not lg.get("start"):
                errores.append(f"{tag}: 'enclavado' requiere 'start'.")
            for campo in ("start", "stop", "enable"):
                if not _es_entrada_valida(lg.get(campo)):
                    errores.append(f"{tag}: '{campo}'='{lg.get(campo)}' no es una entrada valida.")
        elif mode == "combinacional":
            if not lg.get("a") or not lg.get("b"):
                errores.append(f"{tag}: 'combinacional' requiere 'a' y 'b'.")
            for campo in ("a", "b", "stop"):
                if not _es_entrada_valida(lg.get(campo)):
                    errores.append(f"{tag}: '{campo}'='{lg.get(campo)}' no es una entrada valida.")
            op = str(lg.get("op", "OR")).upper()
            if op not in ("OR", "AND"):
                errores.append(f"{tag}: 'op'='{op}' debe ser OR o AND.")
            if op == "AND" and lg.get("enable"):
                errores.append(f"{tag}: en AND el segundo operando usa el slot de enable; "
                               "no se puede pasar 'enable' aparte.")

        tm = o.get("timer")
        if tm:
            if not isinstance(tm, dict) or str(tm.get("type")).lower() not in TIPOS_TIMER:
                errores.append(f"{tag}: timer.type debe ser {sorted(TIPOS_TIMER)}.")
            else:
                low = 0 if tm["type"].lower() == "on_delay" else 1
                _entero_en_rango(tm.get("preset_s"), low, 32767, f"{tag} timer.preset_s", errores)

        ct = o.get("counter")
        if ct:
            if not isinstance(ct, dict) or str(ct.get("type")).lower() not in TIPOS_COUNTER:
                errores.append(f"{tag}: counter.type debe ser {sorted(TIPOS_COUNTER)}.")
            else:
                low = 0 if ct["type"].lower() == "up" else 1
                _entero_en_rango(ct.get("preset"), low, 32767, f"{tag} counter.preset", errores)
                if not _es_entrada_valida(ct.get("reset_input")):
                    errores.append(f"{tag}: counter.reset_input='{ct.get('reset_input')}' invalido.")

    if seq is not None:
        errores.extend(_validar_secuencia(seq))

    if band is not None:
        errores.extend(_validar_banda(band))

    sysc = cfg.get("system") or {}
    if not isinstance(sysc, dict):
        errores.append("'system' no es un objeto.")
    else:
        if not _es_entrada_valida(sysc.get("global_stop")):
            errores.append(f"system.global_stop='{sysc.get('global_stop')}' no es una entrada valida.")
        modo = sysc.get("mode")
        if modo is not None and str(modo).lower() not in SYSMODES:
            errores.append(f"system.mode='{modo}' debe ser {sorted(SYSMODES.keys())}.")

    # El ladder nuevo usa I3/I4 como sensores NC de la banda (S1/S2). Mezclar
    # banda y motor sobre esas entradas deja el estado NA/NC ambiguo.
    if band and _banda_activa(band):
        if seq:
            errores.append("No se puede activar la banda y el secuenciador a la vez "
                           "(la torreta sobreescribe Q10/Q11/Q12).")
        for i, o in enumerate(outputs):
            if not isinstance(o, dict):
                continue
            lg = o.get("logic") or {}
            usadas = [str(lg.get(c)).upper() for c in ("source", "start", "stop", "a", "b", "enable")
                      if lg.get(c)]
            if BAND_SENSOR_S1 in usadas or BAND_SENSOR_S2 in usadas:
                errores.append(f"salida {i + 1} ({o.get('output')}): I3/I4 estan reservadas "
                               "como sensores S1/S2 de la banda; no se pueden usar en la "
                               "logica del maletin mientras la banda este activa.")

    return errores


def _banda_activa(band) -> bool:
    """True si el bloque 'band' del JSON pide arrancar la banda."""
    return isinstance(band, dict) and bool(band.get("enable", True))


def _validar_banda(band) -> list:
    """Valida el bloque 'band' del JSON. Espejo de la seccion 12 del ladder."""
    errores = []
    if not isinstance(band, dict):
        return ["'band' no es un objeto."]

    if band.get("freq_hz") is not None:
        _entero_en_rango(band.get("freq_hz"), 0, 32767, "band.freq_hz", errores)
    for campo in ("wait_s1_s", "wait_s2_s", "retrigger_s1_s", "retrigger_s2_s"):
        if band.get(campo) is not None:
            _entero_en_rango(band.get(campo), 0, 32767, f"band.{campo}", errores)

    d = band.get("direction")
    if d is not None and str(d).lower() not in BAND_DIR:
        errores.append(f"band.direction='{d}' debe ser 'derecha' o 'izquierda'.")

    return errores


def _validar_secuencia(seq) -> list:
    """Valida el bloque 'sequence' del JSON dual. Espejo del secuenciador
    en Texto Estructurado del PLC."""
    errores = []
    if not isinstance(seq, dict):
        return ["'sequence' no es un objeto."]

    if not seq.get("start") or not _es_entrada_valida(seq.get("start")):
        errores.append(f"sequence.start='{seq.get('start')}' debe ser una entrada valida (I1..I7).")

    modo = str(seq.get("mode", "once")).lower()
    if modo not in SEQ_MODES:
        errores.append(f"sequence.mode='{seq.get('mode')}' debe ser 'once' o 'loop'.")

    if seq.get("reset") is not None and not _es_entrada_valida(seq.get("reset")):
        errores.append(f"sequence.reset='{seq.get('reset')}' no es una entrada valida.")

    steps = seq.get("steps")
    if not isinstance(steps, list) or not steps:
        errores.append("sequence.steps debe ser una lista con al menos un paso.")
    elif len(steps) > SEQ_MAX_STEPS:
        errores.append(f"sequence.steps no puede tener mas de {SEQ_MAX_STEPS} pasos.")
    else:
        for i, st in enumerate(steps):
            etq = f"sequence paso {i + 1}"
            if not isinstance(st, dict):
                errores.append(f"{etq}: no es un objeto.")
                continue
            outs = st.get("outputs")
            if not isinstance(outs, list) or not outs:
                errores.append(f"{etq}: 'outputs' debe listar al menos una salida.")
            else:
                for o in outs:
                    if str(o).upper() not in OUT_BIT:
                        errores.append(f"{etq}: salida '{o}' invalida. Usa Q10, Q11 o Q12.")
            _entero_en_rango(st.get("duration_s"), 1, 32767, f"{etq} duration_s", errores)

    return errores


def plan_config(cfg) -> list:
    """Traduce el JSON a una lista ordenada de (metodo, args, kwargs) SIN
    tocar Modbus. Permite inspeccionar/auditar el plan o ejecutarlo en seco.
    Orden: reset_todo -> por salida (base, timer, counter, reset) ->
    paro_general -> habilitar al final (igual que el ejemplo de uso)."""
    plan = []

    if cfg.get("reset_before", True):
        plan.append(("reset_todo", (), {"borrar_acumulados": True}))

    for o in cfg.get("outputs", []):
        out = o["output"]
        lg = o.get("logic") or {"mode": "off"}
        mode = str(lg.get("mode", "off")).lower()

        if mode == "off":
            plan.append(("apagar", (out,), {}))
        elif mode == "directo":
            plan.append(("directo", (out, lg.get("source")),
                         {"habilitacion": lg.get("enable")}))
        elif mode == "enclavado":
            plan.append(("enclavar", (out, lg.get("start")),
                         {"paro": lg.get("stop"), "habilitacion": lg.get("enable")}))
        elif mode == "combinacional":
            plan.append(("combinacional", (out, lg.get("a"), lg.get("b")),
                         {"op": str(lg.get("op", "OR")).upper(),
                          "enclavado": bool(lg.get("latched", False)),
                          "paro": lg.get("stop")}))

        tm = o.get("timer")
        if tm:
            metodo = ("configurar_timer" if str(tm["type"]).lower() == "on_delay"
                      else "configurar_pulso_salida")
            plan.append((metodo, (out, tm["preset_s"]), {}))

        ct = o.get("counter")
        if ct:
            metodo = ("configurar_contador" if str(ct["type"]).lower() == "up"
                      else "configurar_contador_mantenido")
            plan.append((metodo, (out, ct["preset"]), {}))
            if ct.get("reset_input"):
                plan.append(("configurar_reset_contador", (out, ct["reset_input"]), {}))

    seq = cfg.get("sequence")
    if isinstance(seq, dict) and seq.get("steps"):
        plan.append(("configurar_secuencia", (seq.get("start"), seq.get("steps")),
                     {"mode": seq.get("mode", "once"), "reset": seq.get("reset")}))

    band = cfg.get("band")
    banda_on = _banda_activa(band)
    if isinstance(band, dict):
        plan.append(("configurar_banda", (), {
            "frecuencia_hz": band.get("freq_hz"),
            "espera_s1": band.get("wait_s1_s"),
            "espera_s2": band.get("wait_s2_s"),
            "retrigger_s1": band.get("retrigger_s1_s", BAND_S1_RETRIG_DEFAULT),
            "retrigger_s2": band.get("retrigger_s2_s", BAND_S2_RETRIG_DEFAULT),
            "direccion": band.get("direction"),
        }))

    sysc = cfg.get("system") or {}
    if sysc.get("global_stop"):
        plan.append(("paro_general", (sysc["global_stop"],), {}))

    # Selector de equipo: SysMode=2 manda la banda, SysMode=1 el maletin.
    # Solo uno puede quedar activo (lo exige el ladder en 4 y 12.0).
    if banda_on:
        plan.append(("habilitar_banda", (True,), {}))
    elif str(sysc.get("mode", "maletin")).lower() == "banda":
        plan.append(("seleccionar_modo", ("banda",), {}))
    elif sysc.get("enable", True):
        plan.append(("habilitar", (True,), {}))

    return plan


def aplicar_config(plc: "XL4", cfg, dry_run=False) -> list:
    """Valida y aplica el JSON dual sobre un XL4. Lanza ValueError con todos
    los errores si el JSON no es valido. Con dry_run=True imprime el plan sin
    escribir Modbus (util para probar sin PLC). Devuelve el plan ejecutado."""
    errores = validar_config(cfg)
    if errores:
        raise ValueError("JSON invalido:\n  - " + "\n  - ".join(errores))

    plan = plan_config(cfg)
    for metodo, args, kwargs in plan:
        if dry_run:
            firma = ", ".join([repr(a) for a in args]
                              + [f"{k}={v!r}" for k, v in kwargs.items()])
            print(f"[dry-run] plc.{metodo}({firma})")
        else:
            getattr(plc, metodo)(*args, **kwargs)
    return plan


def cargar_y_aplicar(ruta_json, dry_run=False):
    """Lee un JSON de disco y lo aplica. Sin dry_run abre conexion al PLC."""
    with open(ruta_json, encoding="utf-8") as f:
        cfg = json.load(f)
    if dry_run:
        return aplicar_config(None, cfg, dry_run=True)
    plc = XL4()
    plc.connect()
    try:
        return aplicar_config(plc, cfg, dry_run=False)
    finally:
        plc.close()


# JSON dual de referencia: reproduce exactamente el ejemplo de uso de abajo.
EJEMPLO_CONFIG = {
    "name": "Demo maletin",
    "device": "maletin_basico",
    "reset_before": True,
    "system": {"enable": True, "global_stop": None},
    "outputs": [
        {
            "output": "Q11",
            "logic": {"mode": "combinacional", "a": "I1", "b": "I3", "op": "OR"},
            "timer": {"type": "pulse", "preset_s": 5},
            "expr": "I1 + I3",
            "comment": "I1 o I3 encienden la amarilla 5 s",
        },
        {
            "output": "Q10",
            "logic": {"mode": "directo", "source": "I2"},
            "counter": {"type": "up_held", "preset": 3, "reset_input": "I4"},
            "expr": "I2",
            "comment": "Cuenta a 3 y se mantiene; I4 resetea",
        },
    ],
}


# ---------------------------------------------------------------------------
# EJEMPLO DE USO
# ---------------------------------------------------------------------------
#   python plc_maestro.py            -> conecta al PLC y aplica EJEMPLO_CONFIG
#   python plc_maestro.py --dry-run  -> imprime el plan de llamadas, SIN PLC
#   python plc_maestro.py prog.json  -> aplica un JSON de disco (conecta al PLC)
#   python plc_maestro.py prog.json --dry-run -> valida e imprime el plan, SIN PLC
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    rutas = [a for a in args if a != "--dry-run"]

    if rutas:
        cargar_y_aplicar(rutas[0], dry_run=dry_run)
    elif dry_run:
        aplicar_config(None, EJEMPLO_CONFIG, dry_run=True)
    else:
        plc = XL4()
        plc.connect()
        try:
            aplicar_config(plc, EJEMPLO_CONFIG, dry_run=False)
            plc.leer_estado()
            plc.leer_acumulados()
        finally:
            plc.close()
