"""
============================================================================
CAPA FISICA DE LA BANDA TRANSPORTADORA  (PLC INDEPENDIENTE)
============================================================================

Espejo EXACTO del programa maestro en TEXTO ESTRUCTURADO (ST) de la banda:
    Archivos Cscape/ladder_maestro_banda.csp   (ST + tabla de tags vigente)

Las direcciones NO se deducen del texto del programa (el ST usa solo nombres
simbolicos): salen de la tabla de tags del .csp, que es la que el compilador
de Cscape descargo al PLC. Verificadas contra ese archivo:

    R1  BandEnable_Reg    R20..R27  Sensor S1      R40 TorretaRun
    R2  DirCmd            R30..R37  Sensor S2      R41 TorretaIdle
    R3  BandStatus        R60 Pluma1Cmd            R62 Pluma1Status
    R4  FreqRequest       R61 Pluma2Cmd            R63 Pluma2Status
    R5  NewCfgFlag        R500/R502/R504/R506  registros internos del VFD
    R6  ResetCmd
    R7  CfgReady_Reg
    R8  VFD_SpeedDisp

REPARTO DE RESPONSABILIDADES (no negociable)

    Python  -> interpreta al usuario, valida, escribe R2/R4/R20..R41/R60..R61,
               dispara R5/R6 y LEE R1/R3/R7/R8/R25..R27/R35..R37/R62/R63.
    ST      -> arranque, paro, direccion, frecuencia, reset del VFD, sensores,
               contadores, temporizadores, torreta, plumas, interlocks y todo
               el control fisico (R500/R502/R504/R506, Q3..Q9).

Python NUNCA escribe R500 / R504 / R506: son del ST y este los reescribe en
cada scan. Aqui solo se LEEN, como diagnostico.

Este modulo es GEMELO e INDEPENDIENTE de plc_maestro.py:

  plc_maestro.py  -> PLC del MALETIN  (Q10/Q11/Q12, I1..I7, secuenciador)
  plc_banda.py    -> PLC de la BANDA  (VFD, sensores S1/S2, torreta, plumas)

No comparten ni una sola direccion de registro ni se importan entre si.

Convencion de direcciones del XL4:  %Rnnnnn de Cscape -> registro Modbus
n + 2999.   %R00001 -> 3000     %R00500 -> 3499
============================================================================
"""

import os
import time

from pymodbus.client import ModbusTcpClient


# ---------------------------------------------------------------------------
# CONEXION
# ---------------------------------------------------------------------------
# La banda es un PLC DISTINTO al del maletin: su IP se configura por variable
# de entorno BANDA_PLC_IP (o desde el frontend / POST /plc/config?device=banda).
# A proposito NO se hereda la IP del maletin: sin IP explicita se falla con un
# mensaje claro, en vez de arriesgarse a escribir la banda en el maletin.
PLC_IP = os.environ.get("BANDA_PLC_IP", "").strip()
PLC_PORT = int(os.environ.get("BANDA_PLC_PORT", "502"))
UNIT_ID = 1


def R(n: int) -> int:
    """%Rn de Cscape -> direccion Modbus (holding register)."""
    return n + 2999


# ---------------------------------------------------------------------------
# MAPA DE REGISTROS  (tabla de tags de ladder_maestro_banda.csp)
# ---------------------------------------------------------------------------
# CONTROL GENERAL
# OJO: BandEnable NO es un registro escribible. En el ST es un BOOL global que
# SOLO enciende el boton fisico I1 (§5, y unicamente si CfgReady AND CfgValid)
# y borran el boton I3 (§4), NewCfgFlag (§6) y ResetCmd (§7). %R1 es su ESPEJO
# de lectura (§18), no su mando.
ADDR_BAND_ENABLE_REG = R(1)    # BandEnable_Reg  RO  1 = habilitacion latcheada
ADDR_DIR_CMD         = R(2)    # DirCmd          RW  1=dir 1, 2=dir 2 (0=invalida)
ADDR_BAND_STATUS     = R(3)    # BandStatus      RO  0=parada, 1=dir 1, 2=dir 2
ADDR_FREQ_REQUEST    = R(4)    # FreqRequest     RW  Hz SIN escalar (1..327)
ADDR_NEW_CFG_FLAG    = R(5)    # NewCfgFlag      RW  cambio de valor <>0 = nueva config
ADDR_RESET_CMD       = R(6)    # ResetCmd        RW  cambio de valor <>0 = reset VFD
ADDR_CFG_READY_REG   = R(7)    # CfgReady_Reg    RO  1 = configuracion armada
ADDR_VFD_SPEED_DISP  = R(8)    # VFD_SpeedDisp   RO  velocidad real en Hz (§10)

# SENSOR S1 (%R20..%R27) y S2 (%R30..%R37)
ADDR_S1_ENABLE        = R(20)
ADDR_S1_ACTION        = R(21)
ADDR_S1_TIMER_PRESET  = R(22)
ADDR_S1_COUNT_PRESET  = R(23)
ADDR_S1_TORRETA_MASK  = R(24)
ADDR_S1_COUNT_ACCUM   = R(25)   # RO salvo para ponerlo a 0 (no hay CountReset)
ADDR_S1_TIMER_ACCUM   = R(26)   # RO
ADDR_S1_COUNT_DONE    = R(27)   # RO  1 = CountPreset alcanzado

ADDR_S2_ENABLE        = R(30)
ADDR_S2_ACTION        = R(31)
ADDR_S2_TIMER_PRESET  = R(32)
ADDR_S2_COUNT_PRESET  = R(33)
ADDR_S2_TORRETA_MASK  = R(34)
ADDR_S2_COUNT_ACCUM   = R(35)   # RO salvo para ponerlo a 0
ADDR_S2_TIMER_ACCUM   = R(36)   # RO
ADDR_S2_COUNT_DONE    = R(37)   # RO

# Acceso por numero de sensor (1 / 2)
ADDR_SENSOR = {
    1: {"enable": ADDR_S1_ENABLE, "action": ADDR_S1_ACTION,
        "timer_preset": ADDR_S1_TIMER_PRESET, "count_preset": ADDR_S1_COUNT_PRESET,
        "torreta_mask": ADDR_S1_TORRETA_MASK, "count_accum": ADDR_S1_COUNT_ACCUM,
        "timer_accum": ADDR_S1_TIMER_ACCUM, "count_done": ADDR_S1_COUNT_DONE},
    2: {"enable": ADDR_S2_ENABLE, "action": ADDR_S2_ACTION,
        "timer_preset": ADDR_S2_TIMER_PRESET, "count_preset": ADDR_S2_COUNT_PRESET,
        "torreta_mask": ADDR_S2_TORRETA_MASK, "count_accum": ADDR_S2_COUNT_ACCUM,
        "timer_accum": ADDR_S2_TIMER_ACCUM, "count_done": ADDR_S2_COUNT_DONE},
}

# TORRETA  (bitmask b0=Verde, b1=Amarilla, b2=Roja; valores 0..7).
# Salidas fisicas Q3/Q4/Q5: las genera el ST (§15). Python solo da mascaras.
ADDR_TORRETA_RUN   = R(40)
ADDR_TORRETA_IDLE  = R(41)

# PLUMAS  (§17). Salidas fisicas Q6/Q7/Q8/Q9: las genera el ST, que ademas
# impide activar los dos sentidos de una misma pluma a la vez. Python solo
# escribe el comando 0/1/2 y lee el estado.
# ATENCION (documentado en el propio ST): "Pluma 2 UP usa Q9 provisionalmente".
# Es una inconsistencia PENDIENTE DE CONFIRMAR FISICAMENTE y NO se corrige
# desde software: aqui no se toca ninguna Q.
ADDR_PLUMA1_CMD    = R(60)
ADDR_PLUMA2_CMD    = R(61)
ADDR_PLUMA1_STATUS = R(62)
ADDR_PLUMA2_STATUS = R(63)

ADDR_PLUMA = {
    1: {"cmd": ADDR_PLUMA1_CMD, "status": ADDR_PLUMA1_STATUS},
    2: {"cmd": ADDR_PLUMA2_CMD, "status": ADDR_PLUMA2_STATUS},
}

# VFD (FIJOS, no modificar). Desde Python solo se LEEN: los gobierna el ST.
ADDR_VFD_CONTROL   = R(500)   # 18=dir 1, 34=dir 2, 1=stop  (§16)
ADDR_VFD_SPEED_RAW = R(502)   # velocidad leida del variador (escala x100)
ADDR_VFD_FREQ_CALC = R(504)   # consigna enviada al variador = FreqRequest*100
ADDR_VFD_RESET     = R(506)   # 0 / 2 durante la secuencia de reset (§8)

# Registros que el backend NUNCA debe escribir (son del ST).
ADDR_SOLO_LECTURA = {
    ADDR_BAND_ENABLE_REG, ADDR_BAND_STATUS, ADDR_CFG_READY_REG,
    ADDR_VFD_SPEED_DISP, ADDR_S1_TIMER_ACCUM, ADDR_S1_COUNT_DONE,
    ADDR_S2_TIMER_ACCUM, ADDR_S2_COUNT_DONE,
    ADDR_PLUMA1_STATUS, ADDR_PLUMA2_STATUS,
    ADDR_VFD_CONTROL, ADDR_VFD_SPEED_RAW, ADDR_VFD_FREQ_CALC, ADDR_VFD_RESET,
}


# ---------------------------------------------------------------------------
# VOCABULARIO DEL PROGRAMA ST
# ---------------------------------------------------------------------------
# DirCmd (%R2) -> §16 lo traduce a VFD_Control (%R500):
#   DirCmd = 1 -> VFD_Control = 18 (direccion 1)
#   DirCmd = 2 -> VFD_Control = 34 (direccion 2)
# Python NUNCA escribe 18 ni 34.
#
# DirCmd = 0 NO es "paro" para el ST: es CONFIGURACION INVALIDA (§3). El
# efecto practico es un paro inmediato y limpio (§8 pone CfgReady=0,
# VFD_Control=1, VFD_FreqCalc=0), pero para volver a operar hace falta
# reconfigurar (DirCmd 1/2 + NewCfgFlag) y volver a pulsar I1.
DIR_PARO      = 0
DIR_1         = 1
DIR_2         = 2
# Nombres historicos: el ST solo distingue "direccion 1" y "direccion 2".
DIR_DERECHA   = DIR_1
DIR_IZQUIERDA = DIR_2

BAND_DIR = {
    "paro": DIR_PARO, "parar": DIR_PARO, "stop": DIR_PARO, "0": DIR_PARO,
    "derecha": DIR_1, "right": DIR_1, "der": DIR_1, "cw": DIR_1,
    "horario": DIR_1, "1": DIR_1, "dir1": DIR_1, "direccion1": DIR_1,
    "izquierda": DIR_2, "left": DIR_2, "izq": DIR_2, "ccw": DIR_2,
    "antihorario": DIR_2, "2": DIR_2, "dir2": DIR_2, "direccion2": DIR_2,
}

DIR_NOMBRE = {DIR_PARO: "sin direccion", DIR_1: "direccion 1", DIR_2: "direccion 2"}

VFD_CMD_DIR1 = 18
VFD_CMD_DIR2 = 34
VFD_CMD_PARO = 1

# S1_Action / S2_Action (%R21 / %R31), tal como los interpretan §11..§14:
#   0 -> el sensor solo detecta y cuenta; no detiene la banda
#   1 -> detiene MIENTRAS el sensor siga detectando
#   2 -> detiene durante TimerPreset segundos
#   3 -> como 1, ademas superpone la mascara de torreta del sensor
#   4 -> como 2, ademas superpone la mascara de torreta del sensor
ACTION_NADA                     = 0
ACTION_PARO_PRESENCIA           = 1
ACTION_PARO_TEMPORIZADO         = 2
ACTION_PARO_PRESENCIA_TORRETA   = 3
ACTION_PARO_TEMPORIZADO_TORRETA = 4

ACTION_MIN, ACTION_MAX = 0, 4

SENSOR_ACTIONS = {
    "nada": ACTION_NADA,
    "paro_presencia": ACTION_PARO_PRESENCIA,
    "paro_mientras_detecta": ACTION_PARO_PRESENCIA,
    "paro_temporizado": ACTION_PARO_TEMPORIZADO,
    "paro_presencia_torreta": ACTION_PARO_PRESENCIA_TORRETA,
    "paro_mientras_detecta_torreta": ACTION_PARO_PRESENCIA_TORRETA,
    "paro_temporizado_torreta": ACTION_PARO_TEMPORIZADO_TORRETA,
}

ACTION_NOMBRE = {
    ACTION_NADA: "solo detectar y contar",
    ACTION_PARO_PRESENCIA: "detener mientras detecta",
    ACTION_PARO_TEMPORIZADO: "detener un tiempo",
    ACTION_PARO_PRESENCIA_TORRETA: "detener mientras detecta + torreta",
    ACTION_PARO_TEMPORIZADO_TORRETA: "detener un tiempo + torreta",
}

# Acciones del Ladder ANTERIOR que ya no existen. Se siguen ACEPTANDO para no
# romper programas guardados, pero se traducen a lo que el ST si puede hacer,
# y avisos_config() lo explica.
SENSOR_ACTIONS_OBSOLETAS = {
    "paro_enclavado": ACTION_PARO_TEMPORIZADO,
    "contar": ACTION_NADA,
    "contar_y_parar": ACTION_NADA,
}

# Sensores fisicos: S1 = %I4 -> PhIn4 ; S2 = %I5 -> PhIn5. Ambos NC (el ST los
# invierte con NOT en §2). Python NO los lee ni los controla: configura que
# debe hacer el PLC cuando cada uno se active.
SENSORES = {1: "S1", 2: "S2"}

# Botonera fisica del tablero (§2/§4/§5). NO se controla por Modbus: el ST la
# lee directo y el backend solo puede OBSERVAR su efecto en %R1/%R3.
#   %I1 -> PhIn1  I1  NA  arranque: flanco de subida engancha BandEnable, y
#                         SOLO si CfgReady AND CfgValid (§5)
#   %I2 -> PhIn2  I2  NC  entrada auxiliar disponible (BtnAux). Sin funcion
#                         asignada en el ST: no se inventa ninguna aqui.
#   %I3 -> PhIn3  I3  NC  paro general PRIORITARIO: borra BandEnable, para el
#                         VFD y deja las plumas en 0 (§4)
BOTONES = {
    "I1": {"addr": "%I1", "tipo": "NA", "funcion": "arranque (latch de habilitacion)"},
    "I2": {"addr": "%I2", "tipo": "NC", "funcion": "auxiliar disponible (sin uso en el ST)"},
    "I3": {"addr": "%I3", "tipo": "NC", "funcion": "paro general prioritario"},
}

# Bit de cada lampara dentro de una mascara de torreta (0..7)
TORRETA_BIT = {"verde": 1, "amarilla": 2, "roja": 4}

TORRETA_NOMBRE = {
    0: "apagada", 1: "verde", 2: "amarilla", 3: "verde + amarilla",
    4: "roja", 5: "verde + roja", 6: "amarilla + roja",
    7: "verde + amarilla + roja",
}

# BandStatus (%R3) segun §18: enumerado, no bitfield.
BAND_STATUS = {
    0: "parada",
    1: "corriendo_direccion_1",
    2: "corriendo_direccion_2",
}

# PlumaNCmd (%R60/%R61) y PlumaNStatus (%R62/%R63) segun §17.
PLUMA_STOP  = 0
PLUMA_SUBIR = 1
PLUMA_BAJAR = 2
PLUMA_CMD_MIN, PLUMA_CMD_MAX = 0, 2

PLUMA_CMDS = {
    "stop": PLUMA_STOP, "parar": PLUMA_STOP, "paro": PLUMA_STOP, "0": PLUMA_STOP,
    "subir": PLUMA_SUBIR, "arriba": PLUMA_SUBIR, "up": PLUMA_SUBIR, "1": PLUMA_SUBIR,
    "bajar": PLUMA_BAJAR, "abajo": PLUMA_BAJAR, "down": PLUMA_BAJAR, "2": PLUMA_BAJAR,
}

PLUMA_ESTADO = {0: "detenida", 1: "subiendo", 2: "bajando"}

# Rango entero admitido por los registros del ST (INT de Cscape)
INT_MAX = 32767
MASK_MIN, MASK_MAX = 0, 7

# Frecuencia: §3 del ST invalida la configuracion fuera de este rango, porque
# §8/§9 hacen FreqRequest * 100 y un INT no pasa de 32767.
FREQ_MIN_HZ = 1
FREQ_MAX_HZ = 327

# La secuencia init del VFD (§8) mantiene el reset 2 s. Se da margen de sobra.
CFG_READY_TIMEOUT_S = 8.0
CFG_READY_POLL_S = 0.25


# ---------------------------------------------------------------------------
# DRIVER MODBUS
# ---------------------------------------------------------------------------
class BandaPLC:
    """Cliente Modbus TCP del PLC de la banda transportadora.

    TODA la comunicacion Modbus de la banda pasa por esta clase: no hay
    llamadas sueltas repartidas por otros archivos. Las primitivas publicas
    son read_band_register / write_band_register y, encima de ellas, las
    operaciones de alto nivel (write_band_config, trigger_new_config,
    trigger_vfd_reset, read_band_status, read_sensor_status,
    read_tower_status, command_gate).

    Ningun metodo escribe un registro fuera del mapa documentado arriba, y
    ninguno escribe R500/R502/R504/R506."""

    def __init__(self, ip=None, port=None, unit=UNIT_ID):
        ip = (ip or PLC_IP or "").strip()
        if not ip:
            raise ValueError(
                "No hay IP para el PLC de la banda. Configura BANDA_PLC_IP, "
                "manda 'ip' en la peticion o usa POST /plc/config con "
                "device='banda'."
            )
        self.ip = ip
        self.port = int(port or PLC_PORT)
        self.unit = unit
        self.client = ModbusTcpClient(self.ip, port=self.port)

    # -- conexion ----------------------------------------------------------
    def connect(self):
        if not self.client.connect():
            raise ConnectionError(
                f"No se pudo conectar al PLC de la banda en {self.ip}:{self.port}")
        print(f"Conectado al PLC de la BANDA {self.ip}:{self.port}")
        return self

    def close(self):
        self.client.close()

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()
        return False

    # -- primitivas Modbus (UNICO punto de escritura/lectura) --------------
    # pymodbus renombro el argumento que identifica al servidor Modbus: hasta
    # 3.8 era slave=, desde 3.9 es device_id= (y en 3.13, que es la version
    # instalada en este proyecto, slave= ya ni existe y la llamada revienta
    # con "write_register() got an unexpected keyword argument 'slave'"). Se
    # intenta el nombre moderno y se cae al antiguo, exactamente igual que
    # plc_maestro.XL4, para que los dos PLC funcionen con cualquiera de las
    # dos versiones de la libreria SIN cambiar la version instalada.
    def write_band_register(self, addr: int, valor: int):
        """Escribe un registro de la banda. Rechaza los que son del ST."""
        if addr in ADDR_SOLO_LECTURA:
            raise ValueError(
                f"%R{addr - 2999} es de SOLO LECTURA: lo gobierna el programa "
                f"maestro ST. El backend no debe escribirlo.")
        valor = int(valor) & 0xFFFF
        try:
            r = self.client.write_register(addr, valor, device_id=self.unit)
        except TypeError:
            r = self.client.write_register(addr, valor, slave=self.unit)
        if r is None:
            raise IOError(f"Sin respuesta escribiendo %R{addr - 2999} (Modbus {addr})")
        if hasattr(r, "isError") and r.isError():
            raise IOError(f"Error escribiendo %R{addr - 2999} (Modbus {addr}) = {valor}")

    def read_band_block(self, addr: int, count: int) -> list:
        """Lee 'count' registros consecutivos en una sola peticion Modbus.

        El feedback de la banda vive en bloques contiguos (R1-R8, R20-R27,
        R30-R37, R40-R41, R60-R63), asi que el sondeo del frontend cabe en
        unas pocas peticiones en vez de una por registro: el PLC no se satura."""
        try:
            r = self.client.read_holding_registers(addr, count=count, device_id=self.unit)
        except TypeError:
            r = self.client.read_holding_registers(addr, count=count, slave=self.unit)
        if r is None:
            raise IOError(f"Sin respuesta leyendo %R{addr - 2999}+{count} (Modbus {addr})")
        if hasattr(r, "isError") and r.isError():
            raise IOError(f"Error leyendo %R{addr - 2999}+{count} (Modbus {addr})")
        return list(r.registers)

    def read_band_register(self, addr: int) -> int:
        """Lee un registro de la banda (holding register)."""
        return self.read_band_block(addr, 1)[0]

    # Alias cortos de uso interno (misma funcion, nombre historico).
    _w = write_band_register
    _r = read_band_register

    def _pulso_valor_nuevo(self, addr: int) -> int:
        """Escribe en 'addr' un valor <>0 DISTINTO del que ya tenia.

        §6 y §7 del ST no detectan un flanco 0->1, sino un CAMBIO DE VALOR:
            NewCfgTrigger := (NewCfgFlag <> 0) AND (NewCfgFlag <> NewCfgPrev)
        Escribir 0 y luego 1 solo dispara la primera vez, porque NewCfgPrev
        se queda en 1 y el segundo 1 ya no es un valor distinto. Por eso el
        registro se usa como ID/contador incremental (1, 2, 3, ...)."""
        try:
            actual = self._r(addr)
        except IOError:
            actual = 0
        nuevo = (int(actual) % INT_MAX) + 1     # 1..32767, siempre distinto
        self._w(addr, nuevo)
        return nuevo

    # -- helpers de traduccion / validacion --------------------------------
    @staticmethod
    def _direccion(valor, permitir_paro=True):
        if valor is None:
            return None
        if isinstance(valor, bool):
            raise ValueError("DirCmd debe ser 1 (direccion 1) o 2 (direccion 2).")
        if isinstance(valor, int):
            d = valor
        else:
            clave = str(valor).strip().lower()
            if clave not in BAND_DIR:
                raise ValueError(
                    f"Direccion no valida: {valor}. Usa 1/'derecha' o 2/'izquierda'.")
            d = BAND_DIR[clave]
        validos = (DIR_PARO, DIR_1, DIR_2) if permitir_paro else (DIR_1, DIR_2)
        if d not in validos:
            raise ValueError(
                f"DirCmd={d} no valido. El ST solo acepta 1 (direccion 1) o 2 "
                f"(direccion 2); 0 deja la configuracion invalida.")
        return d

    @staticmethod
    def _accion(valor):
        if valor is None:
            return None
        if isinstance(valor, int) and not isinstance(valor, bool):
            if valor < ACTION_MIN or valor > ACTION_MAX:
                raise ValueError(
                    f"Accion de sensor no valida: {valor} ({ACTION_MIN}..{ACTION_MAX}).")
            return valor
        clave = str(valor).strip().lower()
        if clave in SENSOR_ACTIONS:
            return SENSOR_ACTIONS[clave]
        if clave in SENSOR_ACTIONS_OBSOLETAS:
            return SENSOR_ACTIONS_OBSOLETAS[clave]
        raise ValueError(
            f"Accion de sensor no valida: {valor}. Usa {sorted(SENSOR_ACTIONS)}.")

    @staticmethod
    def _pluma_cmd(valor):
        if valor is None:
            return None
        if isinstance(valor, int) and not isinstance(valor, bool):
            cmd = valor
        else:
            clave = str(valor).strip().lower()
            if clave not in PLUMA_CMDS:
                raise ValueError(
                    f"Comando de pluma no valido: {valor}. Usa 'subir', 'bajar' o 'stop'.")
            cmd = PLUMA_CMDS[clave]
        if cmd < PLUMA_CMD_MIN or cmd > PLUMA_CMD_MAX:
            raise ValueError(f"Comando de pluma {cmd} fuera de [0, 2] (0=stop, 1=subir, 2=bajar).")
        return cmd

    @staticmethod
    def _entero(valor, low, high, etiqueta):
        try:
            v = int(valor)
        except (TypeError, ValueError):
            raise ValueError(f"{etiqueta}: '{valor}' no es un numero entero.")
        if v < low or v > high:
            raise ValueError(f"{etiqueta} debe estar entre {low} y {high} (recibido {v}).")
        return v

    # -- §3/§9  configuracion general --------------------------------------
    def configurar_banda(self, frecuencia_hz=None, direccion=None):
        """Escribe FreqRequest (%R4) y DirCmd (%R2). NO arranca la banda.

        La frecuencia va en Hz TAL CUAL: el ST hace VFD_FreqCalc :=
        FreqRequest * 100 (§8 estado 4 y §9). Escribir aqui 3000 para 30 Hz
        seria un error: el variador recibiria 300000 y el INT desbordaria."""
        if frecuencia_hz is not None:
            self._w(ADDR_FREQ_REQUEST,
                    self._entero(frecuencia_hz, FREQ_MIN_HZ, FREQ_MAX_HZ,
                                 "La frecuencia (Hz)"))
        d = self._direccion(direccion, permitir_paro=False)
        if d is not None:
            self._w(ADDR_DIR_CMD, d)
        print("BANDA configurada"
              + (f" | frecuencia={frecuencia_hz} Hz" if frecuencia_hz is not None else "")
              + (f" | DirCmd={d} ({DIR_NOMBRE[d]})" if d is not None else ""))

    def cambiar_direccion(self, direccion):
        """Cambia SOLO el sentido de giro (%R2), sin reconfigurar el VFD.

        El ST usa DirCmd en vivo (§16), asi que un cambio de sentido no
        necesita secuencia de reset: solo la frecuencia la necesita."""
        d = self._direccion(direccion, permitir_paro=False)
        self._w(ADDR_DIR_CMD, d)
        print(f"BANDA: DirCmd={d} ({DIR_NOMBRE[d]})")
        return d

    # -- §11..§14  sensores S1 y S2 ---------------------------------------
    def configurar_sensor(self, n, accion=None, timer_preset=None,
                          count_preset=None, torreta_mask=None, habilitar=True):
        """Configura el bloque completo de S1 (%R20..%R24) o S2 (%R30..%R34).

        accion       : 0..4 o su nombre ('nada', 'paro_presencia',
                       'paro_temporizado', 'paro_presencia_torreta',
                       'paro_temporizado_torreta')
        timer_preset : segundos de paro (acciones 2 y 4). El ST invalida la
                       configuracion si la accion es 2 o 4 y el preset es <= 0.
        count_preset : 0 = actuar en CADA deteccion; N>0 = actuar al llegar a
                       N detecciones (y entonces CountDone se queda en 1).
        torreta_mask : bitmask 0..7 que se superpone durante el evento
                       (acciones 3 y 4)
        """
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}. Solo existen S1 y S2.")
        a = ADDR_SENSOR[n]

        acc = self._accion(accion)
        # El ST invalida TODA la configuracion si una accion temporizada llega
        # sin tiempo: se avisa aqui, con nombre y numero, en vez de dejar la
        # banda muerta con CfgReady=0 sin explicacion.
        if acc in (ACTION_PARO_TEMPORIZADO, ACTION_PARO_TEMPORIZADO_TORRETA):
            if timer_preset is None or int(timer_preset) <= 0:
                raise ValueError(
                    f"S{n}: la accion {acc} ({ACTION_NOMBRE[acc]}) necesita un "
                    f"tiempo mayor que 0 segundos; el ST rechaza la "
                    f"configuracion completa si llega en 0.")

        self._w(a["enable"], 1 if habilitar else 0)
        if acc is not None:
            self._w(a["action"], acc)
        if timer_preset is not None:
            self._w(a["timer_preset"],
                    self._entero(timer_preset, 0, INT_MAX, f"La espera de S{n} (s)"))
        if count_preset is not None:
            self._w(a["count_preset"],
                    self._entero(count_preset, 0, INT_MAX, f"El preset del contador de S{n}"))
        if torreta_mask is not None:
            self._w(a["torreta_mask"],
                    self._entero(torreta_mask, MASK_MIN, MASK_MAX,
                                 f"La mascara de torreta de S{n}"))

        print(f"S{n}: {'habilitado' if habilitar else 'deshabilitado'}"
              + (f" | accion={accion} (S{n}_Action={acc})" if acc is not None else "")
              + (f" | timer={timer_preset}s" if timer_preset is not None else "")
              + (f" | conteo={count_preset}" if count_preset is not None else "")
              + (f" | torreta={torreta_mask}" if torreta_mask is not None else ""))

    def apagar_sensor(self, n):
        """Deja el sensor sin efecto (Enable=0, Action=0)."""
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}.")
        self._w(ADDR_SENSOR[n]["enable"], 0)
        self._w(ADDR_SENSOR[n]["action"], ACTION_NADA)
        print(f"S{n}: deshabilitado")

    def reset_contador(self, n):
        """Pone a 0 el acumulado del sensor (%R25 / %R35).

        El ST no tiene registro CountReset: el acumulado es RETAIN y lo borran
        el paro fisico I3 (§4), NewCfgFlag (§6) y ResetCmd (§7). Se escribe
        directamente el acumulador, que §11/§13 solo incrementan en flanco."""
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}.")
        self._w(ADDR_SENSOR[n]["count_accum"], 0)
        print(f"S{n}: contador reseteado")

    # -- §15  torreta ------------------------------------------------------
    def configurar_torreta(self, mask_run=None, mask_idle=None):
        """TorretaRun (%R40) y TorretaIdle (%R41). Bitmask 0..7 (b0=V,b1=A,b2=R).

        La torreta la gobierna el ST (Q3/Q4/Q5); aqui solo se declara que se
        enciende con la banda corriendo y que se enciende con la banda
        detenida. Las mascaras de los sensores (acciones 3 y 4) tienen
        prioridad sobre estas."""
        if mask_run is not None:
            self._w(ADDR_TORRETA_RUN,
                    self._entero(mask_run, MASK_MIN, MASK_MAX,
                                 "La mascara de torreta en marcha"))
        if mask_idle is not None:
            self._w(ADDR_TORRETA_IDLE,
                    self._entero(mask_idle, MASK_MIN, MASK_MAX,
                                 "La mascara de torreta detenida"))
        print("Torreta configurada"
              + (f" | run={mask_run}" if mask_run is not None else "")
              + (f" | idle={mask_idle}" if mask_idle is not None else ""))

    # -- §17  plumas -------------------------------------------------------
    def command_gate(self, n, comando):
        """Manda un comando a la pluma n (1 o 2): 0=stop, 1=subir, 2=bajar.

        Escribe SOLO Pluma1Cmd (%R60) o Pluma2Cmd (%R61). Las salidas fisicas
        (Q6/Q7/Q8/Q9) y el enclavamiento entre sentidos son del ST (§17)."""
        if n not in ADDR_PLUMA:
            raise ValueError(f"Pluma no valida: {n}. Solo existen la 1 y la 2.")
        cmd = self._pluma_cmd(comando)
        if cmd is None:
            raise ValueError("Falta el comando de la pluma (0=stop, 1=subir, 2=bajar).")
        self._w(ADDR_PLUMA[n]["cmd"], cmd)
        print(f"Pluma {n}: comando {cmd} ({PLUMA_ESTADO[cmd]})")
        return cmd

    def parar_plumas(self):
        """Deja las dos plumas quietas (R60 = R61 = 0)."""
        self._w(ADDR_PLUMA1_CMD, PLUMA_STOP)
        self._w(ADDR_PLUMA2_CMD, PLUMA_STOP)
        print("Plumas detenidas (R60=R61=0)")

    # -- §6/§7/§8  triggers de configuracion y reset -----------------------
    def trigger_new_config(self):
        """Cambia NewCfgFlag (%R5): dispara la secuencia init del VFD.

        §6 -> InitSeqState=1, BandEnable=FALSE, CfgReady=0, contadores a 0.
        §8 -> VFD_ResetReg 0 -> 2 -> 2 s -> 0 -> VFD_FreqCalc = FreqRequest*100
              -> CfgReady=1.

        SIEMPRE debe ser lo ULTIMO que se escribe: hasta que el trigger llega,
        el PLC no lee la configuracion, y asi nunca la lee a medias."""
        v = self._pulso_valor_nuevo(ADDR_NEW_CFG_FLAG)
        print(f"Nueva configuracion aplicada (NewCfgFlag={v}; secuencia init lanzada)")
        return v

    def trigger_vfd_reset(self):
        """Cambia ResetCmd (%R6): reset manual del VFD (§7 -> InitSeqState=1).

        Igual que NewCfgFlag, se detecta por CAMBIO DE VALOR, no por flanco."""
        v = self._pulso_valor_nuevo(ADDR_RESET_CMD)
        print(f"Reset del VFD solicitado (ResetCmd={v})")
        return v

    # Nombres historicos (los usa el plan de carga y quedan como alias).
    aplicar_nueva_config = trigger_new_config
    reset_vfd = trigger_vfd_reset

    def esperar_config_lista(self, timeout=CFG_READY_TIMEOUT_S) -> bool:
        """Espera a que CfgReady_Reg (%R7) valga 1 tras un trigger.

        NO se asume que el VFD este listo por haber escrito los registros: la
        secuencia de §8 mantiene el reset 2 s. Devuelve True si quedo lista."""
        limite = time.time() + float(timeout)
        while time.time() < limite:
            try:
                if self._r(ADDR_CFG_READY_REG) == 1:
                    print("Configuracion lista (CfgReady=1)")
                    return True
            except IOError:
                pass
            time.sleep(CFG_READY_POLL_S)
        print("AVISO: CfgReady (%R7) sigue en 0 tras esperar la secuencia init.")
        return False

    # -- §16/§18  marcha y paro -------------------------------------------
    def parar(self):
        """Paro por Modbus: DirCmd (%R2) = 0.

        Para el ST, DirCmd=0 no es un modo de marcha sino CONFIGURACION
        INVALIDA (§3): §8 responde con CfgReady=0, VFD_Control=1 y
        VFD_FreqCalc=0, o sea paro inmediato del variador. Es la via de paro
        mas rapida que le queda al backend, pero deja el sistema en 'no
        listo': para volver a operar hay que reconfigurar (DirCmd 1/2 +
        NewCfgFlag) y pulsar otra vez I1."""
        self._w(ADDR_DIR_CMD, DIR_PARO)
        print("BANDA DETENIDA (DirCmd=0 -> configuracion invalida -> VFD_Control=1)")

    def habilitar(self, on=True, direccion=None):
        """Da (on=True) o quita (on=False) la orden de movimiento.

        AVISO IMPORTANTE del programa maestro ST: BandEnable NO es un registro
        Modbus. Es un BOOL que solo engancha el boton fisico I1 (§5), y solo
        si CfgReady AND CfgValid. Ademas, cada NewCfgFlag lo borra (§6): tras
        cargar una configuracion SIEMPRE hay que volver a pulsar I1. Desde
        aqui lo unico que se puede hacer es dejar el sentido de giro escrito y
        comprobar en %R1 si el operador ya habilito la banda."""
        if not on:
            self.parar()
            return
        d = self._direccion(direccion, permitir_paro=False) if direccion is not None else None
        if d is not None:
            self._w(ADDR_DIR_CMD, d)
        print("BANDA lista para marcha" + (f" (DirCmd={d})" if d is not None else ""))
        if not self.band_enable():
            print("AVISO: BandEnable esta en 0 (%R1). El programa maestro solo lo "
                  "enciende con el boton fisico I1: pulsalo (y suelta I3) para "
                  "que la banda arranque con esta configuracion.")

    def band_enable(self) -> bool:
        """True si BandEnable esta latcheado (%R1 = BandEnable_Reg, §18)."""
        return self._r(ADDR_BAND_ENABLE_REG) == 1

    def config_lista(self) -> bool:
        """True si la secuencia init termino y CfgReady esta activo (%R7)."""
        return self._r(ADDR_CFG_READY_REG) == 1

    # -- ESCRITURA DE CONFIGURACION COMPLETA -------------------------------
    def write_band_config(self, direccion=None, frecuencia_hz=None,
                          sensores=None, torreta=None):
        """Escribe TODA la configuracion de la banda SIN disparar el trigger.

        Orden: R2/R4 -> R20..R24 / R30..R34 -> R40/R41. El trigger (R5) va
        aparte y SIEMPRE despues, para que el PLC no lea una configuracion a
        medias.

        sensores : {1: {...}, 2: {...}} con claves accion / timer_preset /
                   count_preset / torreta_mask / habilitar
        torreta  : {"run": 0..7, "idle": 0..7}
        """
        self.configurar_banda(frecuencia_hz=frecuencia_hz, direccion=direccion)

        for n, datos in (sensores or {}).items():
            n = int(n)
            if datos is None or datos is False:
                self.apagar_sensor(n)
                continue
            self.configurar_sensor(
                n,
                accion=datos.get("accion"),
                timer_preset=datos.get("timer_preset"),
                count_preset=datos.get("count_preset"),
                torreta_mask=datos.get("torreta_mask"),
                habilitar=datos.get("habilitar", True),
            )

        if torreta:
            self.configurar_torreta(mask_run=torreta.get("run"),
                                    mask_idle=torreta.get("idle"))

    # -- LECTURA DE ESTADO / FEEDBACK --------------------------------------
    def read_band_status(self) -> dict:
        """Feedback general de la banda: R1, R3, R7, R8, R2, R4."""
        status = self._r(ADDR_BAND_STATUS)
        return {
            "band_status": status,
            "estado": BAND_STATUS.get(status, str(status)),
            "running": status in (1, 2),
            "direccion": {1: 1, 2: 2}.get(status),
            "band_enable": self._r(ADDR_BAND_ENABLE_REG) == 1,
            "cfg_ready": self._r(ADDR_CFG_READY_REG) == 1,
            "dir_cmd": self._r(ADDR_DIR_CMD),
            "freq_request_hz": self._r(ADDR_FREQ_REQUEST),
            # %R8 ya viene escalado por el ST (§10: VFD_SpeedRaw / 100), asi
            # que se muestra tal cual: no se vuelve a dividir.
            "vfd_speed_hz": self._r(ADDR_VFD_SPEED_DISP),
        }

    def read_sensor_status(self, n=None) -> dict:
        """Feedback de los sensores: conteo (R25/R35), timer (R26/R36) y
        CountDone (R27/R37). Sin argumento devuelve los dos."""
        nums = [n] if n else sorted(ADDR_SENSOR)
        salida = {}
        for i in nums:
            if i not in ADDR_SENSOR:
                raise ValueError(f"Sensor no valido: {i}.")
            a = ADDR_SENSOR[i]
            salida[i] = {
                "count": self._r(a["count_accum"]),
                "timer_s": self._r(a["timer_accum"]),
                "count_done": self._r(a["count_done"]) == 1,
            }
        return salida

    def read_tower_status(self) -> dict:
        """Mascaras de torreta cargadas en el PLC (R40 / R41).

        Q3/Q4/Q5 no se leen: son salidas del ST, no registros de interfaz."""
        run = self._r(ADDR_TORRETA_RUN)
        idle = self._r(ADDR_TORRETA_IDLE)
        return {
            "run": run, "run_nombre": TORRETA_NOMBRE.get(run, str(run)),
            "idle": idle, "idle_nombre": TORRETA_NOMBRE.get(idle, str(idle)),
        }

    def read_gate_status(self) -> dict:
        """Estado real de las plumas (R62 / R63) y el ultimo comando (R60/R61)."""
        salida = {}
        for n, a in ADDR_PLUMA.items():
            st = self._r(a["status"])
            salida[n] = {
                "status": st,
                "estado": PLUMA_ESTADO.get(st, str(st)),
                "cmd": self._r(a["cmd"]),
            }
        return salida

    def leer_estado(self) -> dict:
        """Estado COMPLETO para el frontend (una sola tanda de lecturas).

        Incluye los registros internos del VFD SOLO como diagnostico: se leen,
        nunca se escriben. InitSeqState e InitTimerAccum NO aparecen: en el
        .csp vigente no tienen $tag, asi que Cscape les asigna la direccion y
        puede moverlas en cualquier recompilacion; el estado de la secuencia
        se deduce de CfgReady (%R7), que si es un tag fijo."""
        # 5 lecturas por bloque en vez de ~20 sueltas.
        gen = self.read_band_block(ADDR_BAND_ENABLE_REG, 8)     # R1..R8
        s1 = self.read_band_block(ADDR_S1_ENABLE, 8)            # R20..R27
        s2 = self.read_band_block(ADDR_S2_ENABLE, 8)            # R30..R37
        tor = self.read_band_block(ADDR_TORRETA_RUN, 2)         # R40..R41
        plu = self.read_band_block(ADDR_PLUMA1_CMD, 4)          # R60..R63
        vfd = self.read_band_block(ADDR_VFD_CONTROL, 7)         # R500..R506

        status = gen[2]
        estado = {
            "band_status": status,
            "estado": BAND_STATUS.get(status, str(status)),
            "running": status in (1, 2),
            "direccion": {1: 1, 2: 2}.get(status),
            "band_enable": gen[0] == 1,
            "cfg_ready": gen[6] == 1,
            "dir_cmd": gen[1],
            "freq_request_hz": gen[3],
            # %R8 ya viene escalado por el ST (§10): no se vuelve a dividir.
            "vfd_speed_hz": gen[7],
            "s1_count": s1[5], "s1_timer_s": s1[6], "s1_count_done": s1[7] == 1,
            "s2_count": s2[5], "s2_timer_s": s2[6], "s2_count_done": s2[7] == 1,
            "torreta": {
                "run": tor[0], "run_nombre": TORRETA_NOMBRE.get(tor[0], str(tor[0])),
                "idle": tor[1], "idle_nombre": TORRETA_NOMBRE.get(tor[1], str(tor[1])),
            },
            "pluma1": {"status": plu[2], "estado": PLUMA_ESTADO.get(plu[2], str(plu[2])),
                       "cmd": plu[0]},
            "pluma2": {"status": plu[3], "estado": PLUMA_ESTADO.get(plu[3], str(plu[3])),
                       "cmd": plu[1]},
            # Diagnostico del VFD (solo lectura).
            "vfd_control": vfd[0],
            "vfd_speed_raw": vfd[2],
            "vfd_freq_raw": vfd[4],
            "vfd_freq_hz": vfd[4] / 100.0,
            "vfd_reset": vfd[6],
        }
        # Etiqueta lista para pintar en el frontend.
        estado["fase"] = _fase_visual(estado)
        return estado

    # -- verificacion post-carga ------------------------------------------
    def verificar_vfd(self) -> list:
        """Comprueba que la consigna de frecuencia llego ESCALADA al variador.

        El variador lee %R504 en centesimas de Hz y el ST entrega
        FreqRequest * 100 (§8 estado 4 y §9). Si algun dia esa multiplicacion
        se pierde, el variador recibe una frecuencia 100 veces menor
        (30 Hz -> 0.30 Hz) y la banda no se mueve, sin que nada falle de forma
        visible.

        Esto NO se corrige desde aqui: §9 reescribe %R504 en cada scan, asi
        que cualquier valor que mande el backend dura menos de un scan. Se
        detecta y se avisa para que el arreglo se haga en el ST."""
        avisos = []
        hz = self._r(ADDR_FREQ_REQUEST)
        enviado = self._r(ADDR_VFD_FREQ_CALC)
        if hz <= 0:
            return avisos
        if enviado == hz * 100:
            return avisos                      # escalado correcto
        if enviado == 0:
            avisos.append(
                "El variador aun no tiene consigna (%R504 = 0): la secuencia "
                "de configuracion no ha terminado o el ST considera invalida "
                "la configuracion (CfgValid = FALSE).")
        elif enviado == hz:
            avisos.append(
                f"El programa maestro esta entregando la frecuencia SIN escalar: "
                f"%R504 = {enviado} en vez de {hz * 100}. El variador lo lee "
                f"como {enviado / 100:.2f} Hz y la banda no se movera. "
                f"Corrige en el ST (§8 estado 4 y §9): "
                f"VFD_FreqCalc := FreqRequest * 100 ;")
        else:
            avisos.append(
                f"La consigna del variador no cuadra con la frecuencia pedida: "
                f"%R4 = {hz} Hz pero %R504 = {enviado} (se esperaba {hz * 100}). "
                f"El variador la leera como {enviado / 100:.2f} Hz.")
        return avisos


def _fase_visual(estado: dict) -> str:
    """Traduce el feedback del PLC a la etiqueta que pinta el frontend.

    Se decide SOLO con registros leidos del PLC, nunca con el ultimo comando
    enviado: el operador puede haber pulsado el paro fisico I3 y el frontend
    tiene que enterarse."""
    if estado.get("running"):
        return "corriendo"
    if not estado.get("cfg_ready"):
        return "configurando"          # secuencia init en curso o cfg invalida
    if estado.get("band_enable"):
        return "habilitada"            # lista y habilitada, pero detenida
    return "lista"                     # configurada; falta pulsar I1


FASE_TEXTO = {
    "configurando": "Configurando VFD...",
    "lista": "Sistema listo — pulsa I1 para habilitar",
    "habilitada": "Banda habilitada",
    "corriendo": "Banda corriendo",
    "detenida": "Banda detenida",
}


# ---------------------------------------------------------------------------
# VALIDADOR DEL BLOQUE "band"
# ---------------------------------------------------------------------------
# Contrato del JSON (el mismo que dibuja el frontend, mas campos ADITIVOS
# opcionales que el programa maestro ST hizo posibles):
#
#   {
#     "device": "banda",
#     "name": "...",
#     "band": {
#       "enable": true,
#       "direction": "derecha" | "izquierda" | 1 | 2,
#       "freq_hz": 1..327 | null,
#       "wait_s1_s": 0..32767 | null,      # atajo: accion 'paro_temporizado'
#       "wait_s2_s": 0..32767 | null,
#       "s1_action": "nada"|"paro_presencia"|"paro_temporizado"|
#                    "paro_presencia_torreta"|"paro_temporizado_torreta",
#       "s2_action": ...,
#       "count_s1": 0..32767 | null,       # preset del contador de S1 (%R23)
#       "count_s2": 0..32767 | null,
#       "torreta_s1": 0..7 | null,         # mascara durante el evento de S1
#       "torreta_s2": 0..7 | null,
#       "torreta_run": 0..7 | null,        # mascara con la banda en marcha
#       "torreta_idle": 0..7 | null,       # mascara con la banda detenida
#       "pluma1": 0..2 | "subir"|"bajar"|"stop" | null,
#       "pluma2": ...
#     }
#   }
#
# retrigger_s1_s / retrigger_s2_s se ACEPTAN por compatibilidad con el
# frontend actual, pero NO se escriben: el ST no tiene registro de
# anti-retrigger (lo resuelve con los flancos SN_Rising).

BAND_DIRS = set(BAND_DIR.keys())
CAMPOS_IGNORADOS = ("retrigger_s1_s", "retrigger_s2_s")


def _entero_en_rango(valor, low, high, etiqueta, errores):
    try:
        v = int(valor)
    except (TypeError, ValueError):
        errores.append(f"{etiqueta}: '{valor}' no es un entero.")
        return None
    if v < low or v > high:
        errores.append(f"{etiqueta}: {v} fuera de [{low}, {high}].")
        return None
    return v


def _accion_codigo(valor):
    """Codigo 0..4 de una accion, o None si el valor no es reconocible."""
    if valor is None:
        return None
    if isinstance(valor, int) and not isinstance(valor, bool):
        return valor if ACTION_MIN <= valor <= ACTION_MAX else None
    clave = str(valor).strip().lower()
    if clave in SENSOR_ACTIONS:
        return SENSOR_ACTIONS[clave]
    return SENSOR_ACTIONS_OBSOLETAS.get(clave)


def validar_config(cfg) -> list:
    """Valida un engine_config de BANDA contra su programa maestro ST.

    Reproduce las mismas reglas que §3 del ST (CfgValid), para que un
    programa invalido se rechace ANTES de escribir nada, en vez de dejar el
    PLC con CfgReady = 0 y sin explicacion.

    Rechaza expresamente cualquier bloque del maletin (outputs / sequence):
    esos registros pertenecen al otro PLC y no existen aqui."""
    errores = []
    if not isinstance(cfg, dict):
        return ["El JSON raiz no es un objeto."]

    band = cfg.get("band")
    if not isinstance(band, dict):
        return ["Falta el bloque 'band': un programa de la banda debe traerlo."]

    if cfg.get("outputs"):
        errores.append("El PLC de la banda no tiene Q10/Q11/Q12 configurables: "
                       "'outputs' debe ir vacio (la torreta la maneja su ST).")
    if cfg.get("sequence"):
        errores.append("El PLC de la banda no tiene secuenciador de pasos: "
                       "'sequence' no aplica a este equipo.")

    # Frecuencia: el ST invalida la configuracion fuera de 1..327 Hz porque
    # multiplica por 100 sobre un INT de 16 bits.
    if band.get("freq_hz") is not None:
        _entero_en_rango(band["freq_hz"], FREQ_MIN_HZ, FREQ_MAX_HZ,
                         "band.freq_hz (Hz)", errores)

    for campo in ("wait_s1_s", "wait_s2_s", "count_s1", "count_s2"):
        if band.get(campo) is not None:
            _entero_en_rango(band[campo], 0, INT_MAX, f"band.{campo}", errores)

    for campo in ("torreta_s1", "torreta_s2", "torreta_run", "torreta_idle"):
        if band.get(campo) is not None:
            _entero_en_rango(band[campo], MASK_MIN, MASK_MAX, f"band.{campo}", errores)

    # Direccion: el ST solo acepta 1 o 2. 0 deja CfgValid = FALSE.
    d = band.get("direction")
    if d is not None:
        clave = str(d).strip().lower()
        if clave not in BAND_DIRS:
            errores.append(f"band.direction='{d}' debe ser 1/'derecha' o 2/'izquierda'.")
        elif BAND_DIR[clave] == DIR_PARO:
            errores.append("band.direction=0 no es una direccion valida: el ST "
                           "necesita 1 o 2 para dar por buena la configuracion.")

    for n in (1, 2):
        acc = band.get(f"s{n}_action")
        if acc is None:
            continue
        codigo = _accion_codigo(acc)
        if codigo is None:
            errores.append(f"band.s{n}_action='{acc}' debe ser un codigo 0..4 o "
                           f"uno de {sorted(SENSOR_ACTIONS)}.")
            continue
        # Acciones temporizadas sin tiempo: el ST invalida TODA la config.
        if codigo in (ACTION_PARO_TEMPORIZADO, ACTION_PARO_TEMPORIZADO_TORRETA):
            espera = band.get(f"wait_s{n}_s")
            if espera is None or _entero_en_rango(espera, 1, INT_MAX,
                                                  f"band.wait_s{n}_s", []) is None:
                errores.append(
                    f"band.s{n}_action='{acc}' detiene la banda por tiempo, asi "
                    f"que band.wait_s{n}_s debe ser mayor que 0 segundos.")

    for n in (1, 2):
        p = band.get(f"pluma{n}")
        if p is None:
            continue
        clave = str(p).strip().lower()
        if clave not in PLUMA_CMDS:
            errores.append(f"band.pluma{n}='{p}' debe ser 0 (stop), 1 (subir) o 2 (bajar).")

    return errores


# Valores historicos del anti-retrigger. El editor los usa para DIBUJAR sus
# rungs (presentacion); el PLC ya no los tiene. Solo se avisa cuando el usuario
# pidio un tiempo distinto del historico, para no llenar de ruido cada programa.
RETRIGGER_HISTORICO = {"retrigger_s1_s": 8, "retrigger_s2_s": 12}


def avisos_config(cfg) -> list:
    """Avisos no bloqueantes (campos que el programa maestro ST ya no usa, y
    condiciones que el operador tiene que conocer)."""
    avisos = []
    band = (cfg or {}).get("band") or {}

    pedidos = [c for c in CAMPOS_IGNORADOS
               if band.get(c) is not None and band.get(c) != RETRIGGER_HISTORICO[c]]
    if pedidos:
        avisos.append(
            "El programa maestro de la banda no tiene registro de anti-retrigger "
            "(lo resuelve por flanco de los sensores): se ignora "
            f"{', '.join('band.' + c for c in pedidos)} al cargar al PLC.")

    for n in (1, 2):
        acc = band.get(f"s{n}_action")
        clave = str(acc).lower() if acc is not None else ""
        if clave == "paro_enclavado":
            avisos.append(
                f"band.s{n}_action='paro_enclavado' ya no existe en el programa "
                f"maestro: se carga como 'paro_temporizado' (S{n}_Action=2), "
                f"asi que la banda continua sola al vencer el tiempo.")
        elif clave in ("contar", "contar_y_parar"):
            avisos.append(
                f"band.s{n}_action='{clave}' ya no es una accion del ST: "
                f"cualquier sensor habilitado cuenta sus flancos en "
                f"S{n}_CountAccum (%R{25 if n == 1 else 35}). Se carga como "
                f"S{n}_Action=0; si quieres que el conteo detenga la banda, usa "
                f"una accion de paro junto con count_s{n}.")

        # El conteo cambio de significado con el ST nuevo: conviene decirlo.
        preset = band.get(f"count_s{n}")
        codigo = _accion_codigo(acc)
        if preset and codigo:
            avisos.append(
                f"S{n}: con count_s{n}={preset} la accion se ejecuta UNA vez, al "
                f"llegar a {preset} detecciones (CountDone se queda en 1 hasta la "
                f"siguiente configuracion o el paro I3). Con count_s{n}=0 se "
                f"ejecutaria en cada deteccion.")

    if band.get("enable", True) is not False:
        avisos.append(
            "El programa maestro no tiene registro BandEnable: tras cargar la "
            "configuracion la banda queda LISTA pero no habilitada. Cada nueva "
            "configuracion borra la habilitacion, asi que hay que pulsar el "
            "boton fisico I1 (con I3 suelto) para que arranque.")

    return avisos


# ---------------------------------------------------------------------------
# PLAN Y APLICACION
# ---------------------------------------------------------------------------
def _accion_de_sensor(band, n):
    """Deduce (accion, timer_preset, count_preset) para S1 o S2 desde el JSON.

    'wait_sN_s' es el atajo historico del frontend: significa 'paro
    temporizado de N segundos', que en el ST es Action = 2."""
    accion = band.get(f"s{n}_action")
    espera = band.get(f"wait_s{n}_s")
    conteo = band.get(f"count_s{n}")

    if accion is None:
        if espera is not None:
            accion = "paro_temporizado"
        elif conteo is not None:
            # El conteo no necesita accion propia: basta habilitar el sensor
            # con su preset (§11 cuenta siempre que Enable <> 0).
            accion = "nada"
        else:
            return None, None, None      # sensor no mencionado: no se toca

    return accion, espera, conteo


def plan_config(cfg) -> list:
    """Traduce el engine_config de banda a (metodo, args, kwargs) SIN Modbus.

    Orden impuesto por el programa maestro ST:

      1) parar()              -> DirCmd = 0: el ST lo lee como configuracion
                                 invalida y detiene el VFD en el acto. Nada se
                                 reconfigura con la banda en movimiento.
      2) configuracion        -> frecuencia, sensores, torreta y, al final,
                                 el sentido de giro (DirCmd = 1/2)
      3) trigger_new_config() -> NewCfgFlag: SIEMPRE lo ultimo, para que el PLC
                                 no lea una configuracion a medias. Dispara el
                                 reset del VFD y la carga de FreqRequest*100.
      4) esperar_config_lista -> se espera CfgReady (%R7) = 1 antes de dar el
                                 sistema por listo.
      5) plumas               -> comandos independientes, si el programa los
                                 pidio.

    El arranque NO esta en el plan: BandEnable solo lo enciende el boton
    fisico I1, y cada NewCfgFlag lo borra."""
    plan = []
    band = cfg.get("band") or {}

    # 1) Estado seguro: nada se reconfigura con la banda en marcha.
    plan.append(("parar", (), {}))

    # 2) Configuracion (la direccion va al final de este bloque)
    if band.get("freq_hz") is not None:
        plan.append(("configurar_banda", (), {"frecuencia_hz": band.get("freq_hz")}))

    for n in (1, 2):
        accion, espera, conteo = _accion_de_sensor(band, n)
        if accion is None:
            plan.append(("apagar_sensor", (n,), {}))
            continue
        # count_preset y torreta_mask se escriben SIEMPRE que el sensor se
        # configure, aunque el programa no los mencione: esos registros son
        # RETAIN en el PLC y, sin escribirlos, un preset viejo seguiria
        # decidiendo cuando actua el sensor. 0 = comportamiento por defecto
        # (actuar en cada deteccion / sin lamparas).
        plan.append(("configurar_sensor", (n,), {
            "accion": accion,
            "timer_preset": espera,
            "count_preset": conteo if conteo is not None else 0,
            "torreta_mask": band.get(f"torreta_s{n}") or 0,
            "habilitar": True,
        }))

    if band.get("torreta_run") is not None or band.get("torreta_idle") is not None:
        plan.append(("configurar_torreta", (), {
            "mask_run": band.get("torreta_run"),
            "mask_idle": band.get("torreta_idle"),
        }))

    # Sentido de giro: ultimo parametro antes del trigger. El ST exige 1 o 2
    # para dar la configuracion por valida, asi que un programa sin direccion
    # explicita se carga en direccion 1.
    plan.append(("cambiar_direccion", (band.get("direction") or DIR_1,), {}))

    # 3) Trigger de nueva configuracion (siempre el ultimo registro escrito)
    plan.append(("trigger_new_config", (), {}))

    # 4) Confirmacion real del PLC
    plan.append(("esperar_config_lista", (), {}))

    # 5) Plumas (independientes de la secuencia del VFD)
    for n in (1, 2):
        if band.get(f"pluma{n}") is not None:
            plan.append(("command_gate", (n, band.get(f"pluma{n}")), {}))

    return plan


def aplicar_config(plc: "BandaPLC", cfg, dry_run=False) -> list:
    """Valida y aplica el engine_config de banda sobre su PLC."""
    errores = validar_config(cfg)
    if errores:
        raise ValueError("JSON invalido para la banda:\n  - " + "\n  - ".join(errores))

    plan = plan_config(cfg)
    for metodo, args, kwargs in plan:
        if dry_run:
            firma = ", ".join([repr(a) for a in args]
                              + [f"{k}={v!r}" for k, v in kwargs.items()])
            print(f"[dry-run] banda.{metodo}({firma})")
        else:
            getattr(plc, metodo)(*args, **kwargs)
    return plan


# JSON de referencia del equipo BANDA.
EJEMPLO_CONFIG = {
    "name": "Banda con paro por S1",
    "device": "banda",
    "band": {
        "enable": True,
        "direction": "derecha",
        "freq_hz": 30,
        "s1_action": "paro_temporizado",
        "wait_s1_s": 5,
        "wait_s2_s": None,
    },
    "outputs": [],
}


if __name__ == "__main__":
    print("Plan de ejemplo para el PLC de la banda:")
    aplicar_config(None, EJEMPLO_CONFIG, dry_run=True)
