"""
============================================================================
CAPA FISICA DE LA BANDA TRANSPORTADORA  (PLC INDEPENDIENTE)
============================================================================

Espejo EXACTO del programa maestro en TEXTO ESTRUCTURADO (ST) de la banda:
    Archivos Cscape/ladder_maestro_banda.csp   (ST + tabla de tags vigente)

Las direcciones NO se deducen del texto del programa (el ST usa solo nombres
simbolicos): salen de la tabla de tags del .csp (lineas V-NOMBRE=%Rn), que es
la que el compilador de Cscape descargo al PLC. Verificadas contra ese archivo:

    R1..R8     control general         R20..R29  sensor S1 (+ plumas R28/R29)
    R9  StopMode · R10 SoftStopCmd     R30..R39  sensor S2 (+ plumas R38/R39)
    R11..R15   paro automatico         R40/R41   TorretaRun / TorretaIdle
    R16/R17    S1/S2_BandMode          R18/R19   S1/S2_EventActive_Reg
    R50        I1_LampMask             R60..R63  plumas (comando / estado)
    R100..R127 monitoreo (solo lectura)
    R500/R502/R504/R506  registros internos del VFD (solo lectura)

El mapa completo vive en BAND_REGISTERS (UNA sola tabla): de ahi salen las
direcciones, el conjunto de registros escribibles y la lectura de estado.

REPARTO DE RESPONSABILIDADES (no negociable)

    Python  -> interpreta al usuario, valida, escribe SOLO los registros de
               configuracion/comando de BAND_REGISTERS, dispara R5/R6 y LEE
               el feedback y los monitores.
    ST      -> arranque, paros, direccion, frecuencia, reset del VFD, sensores,
               contadores, temporizadores, paro automatico, torreta, plumas,
               prioridades y todo el control fisico (R500/R504/R506, Q3..Q9).

Python NUNCA escribe R500 / R504 / R506 ni los monitores R100..R127: son del
ST y este los reescribe en cada scan. Aqui solo se LEEN, como diagnostico.

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
# Unica fuente de direcciones de la banda. Cada entrada:
#     clave: (%R, acceso, simbolo en el ST)
# Acceso:
#   CFG   configuracion o comando que escribe el backend
#   TRIG  trigger por CAMBIO DE VALOR (NewCfgFlag / ResetCmd)
#   FB    feedback que escribe el ST: solo lectura
#   MON   monitoreo / diagnostico (§19): solo lectura
#   VFD   registro interno del variador (§8/§9/§10/§16): solo lectura
# OJO: BandEnable NO es escribible. Es un BOOL que SOLO engancha el boton
# fisico I1 (§5, y unicamente si CfgReady AND CfgValid AND NOT GenStop) y
# %R1 es su espejo de lectura (§18), no su mando.
CFG, TRIG, FB, MON, VFD = "cfg", "trig", "fb", "mon", "vfd"

BAND_REGISTERS = {
    # -- control general (§3..§10, §18)
    "band_enable":       (1,   FB,   "BandEnable_Reg"),    # 1 = habilitacion latcheada por I1
    "dir_cmd":           (2,   CFG,  "DirCmd"),            # 1 = dir 1 (VFD 18) · 2 = dir 2 (VFD 34)
    "band_status":       (3,   FB,   "BandStatus"),        # 0 detenida · 1 dir 1 · 2 dir 2
    "freq_request":      (4,   CFG,  "FreqRequest"),       # Hz SIN escalar (1..327)
    "new_cfg_flag":      (5,   TRIG, "NewCfgFlag"),        # cambio de valor <>0 = nueva config
    "reset_cmd":         (6,   TRIG, "ResetCmd"),          # cambio de valor <>0 = reset VFD
    "cfg_ready":         (7,   FB,   "CfgReady_Reg"),      # 1 = configuracion armada
    "vfd_speed_disp":    (8,   FB,   "VFD_SpeedDisp"),     # velocidad real en Hz (R502 / 100)
    # -- paros (§4)
    "stop_mode":         (9,   CFG,  "StopMode"),          # 0 I3 · 1 I2+I3 · 2 SW+I3 · 3 I2+SW+I3
    "soft_stop_cmd":     (10,  CFG,  "SoftStopCmd"),       # 0 liberado · 1 paro software
    # -- paro automatico por tiempo (§14b)
    "auto_stop_preset":  (11,  CFG,  "AutoStopPreset"),    # segundos hasta el paro automatico
    "auto_stop_accum":   (12,  FB,   "AutoStopAccum"),
    "auto_stop_done":    (13,  FB,   "AutoStopDone_Reg"),  # 1 = paro automatico completado
    "stop_reason":       (14,  FB,   "StopReason"),        # ver STOP_REASON_TEXTO
    "auto_stop_mode":    (15,  CFG,  "AutoStopMode"),      # 0 off · 1 movimiento real · 2 desde START
    # -- interaccion sensor -> banda (§3/§11/§13)
    "s1_band_mode":      (16,  CFG,  "S1_BandMode"),       # 0 el evento puede pausar la banda · 1 solo evento
    "s2_band_mode":      (17,  CFG,  "S2_BandMode"),
    "s1_event_active":   (18,  FB,   "S1_EventActive_Reg"),  # 1 = evento de S1 en curso
    "s2_event_active":   (19,  FB,   "S2_EventActive_Reg"),
    # -- sensor S1 (§11/§12)
    "s1_enable":         (20,  CFG,  "S1_Enable"),
    "s1_action":         (21,  CFG,  "S1_Action"),
    "s1_timer_preset":   (22,  CFG,  "S1_TimerPreset"),
    "s1_count_preset":   (23,  CFG,  "S1_CountPreset"),
    "s1_torreta_mask":   (24,  CFG,  "S1_TorretaMask"),
    "s1_count_accum":    (25,  CFG,  "S1_CountAccum"),     # solo para ponerlo a 0 (no hay CountReset)
    "s1_timer_accum":    (26,  FB,   "S1_TimerAccum"),
    "s1_count_done":     (27,  FB,   "S1_CountDone_Reg"),
    "s1_pluma1":         (28,  CFG,  "S1_Pluma1Cmd"),      # 0 nada · 1 subir · 2 bajar · 3 forzar stop
    "s1_pluma2":         (29,  CFG,  "S1_Pluma2Cmd"),
    # -- sensor S2 (§13/§14)
    "s2_enable":         (30,  CFG,  "S2_Enable"),
    "s2_action":         (31,  CFG,  "S2_Action"),
    "s2_timer_preset":   (32,  CFG,  "S2_TimerPreset"),
    "s2_count_preset":   (33,  CFG,  "S2_CountPreset"),
    "s2_torreta_mask":   (34,  CFG,  "S2_TorretaMask"),
    "s2_count_accum":    (35,  CFG,  "S2_CountAccum"),
    "s2_timer_accum":    (36,  FB,   "S2_TimerAccum"),
    "s2_count_done":     (37,  FB,   "S2_CountDone_Reg"),
    "s2_pluma1":         (38,  CFG,  "S2_Pluma1Cmd"),
    "s2_pluma2":         (39,  CFG,  "S2_Pluma2Cmd"),
    # -- torreta (§15 / §15b). Bitmask b0 verde · b1 amarilla · b2 roja.
    "torreta_run":       (40,  CFG,  "TorretaRun"),
    "torreta_idle":      (41,  CFG,  "TorretaIdle"),
    "torreta_i1":        (50,  CFG,  "I1_LampMask"),       # lamparas mientras I1 esta presionado
    # -- plumas (§17). Pluma 1: Q8 sube / Q9 baja · Pluma 2: Q6 sube / Q7 baja
    "pluma1_cmd":        (60,  CFG,  "Pluma1Cmd"),         # 0 stop · 1 subir · 2 bajar
    "pluma2_cmd":        (61,  CFG,  "Pluma2Cmd"),
    "pluma1_status":     (62,  FB,   "Pluma1Status"),
    "pluma2_status":     (63,  FB,   "Pluma2Status"),
    # -- monitoreo §19 (1 = activo)
    "mon_i1_raw":        (100, MON,  "Mon_I1_Raw"),
    "mon_i2_raw":        (101, MON,  "Mon_I2_Raw"),
    "mon_i3_raw":        (102, MON,  "Mon_I3_Raw"),
    "mon_i4_raw":        (103, MON,  "Mon_I4_Raw"),
    "mon_i5_raw":        (104, MON,  "Mon_I5_Raw"),
    "mon_btn_start":     (105, MON,  "Mon_BtnStart"),
    "mon_btn_aux":       (106, MON,  "Mon_BtnAux"),
    "mon_btn_stop":      (107, MON,  "Mon_BtnStop"),
    "mon_s1_det":        (108, MON,  "Mon_S1_Det"),
    "mon_s2_det":        (109, MON,  "Mon_S2_Det"),
    "mon_q3":            (110, MON,  "Mon_Q3"),
    "mon_q4":            (111, MON,  "Mon_Q4"),
    "mon_q5":            (112, MON,  "Mon_Q5"),
    "mon_q6":            (113, MON,  "Mon_Q6"),
    "mon_q7":            (114, MON,  "Mon_Q7"),
    "mon_q8":            (115, MON,  "Mon_Q8"),
    "mon_q9":            (116, MON,  "Mon_Q9"),
    "mon_gen_stop":      (117, MON,  "Mon_GenStop"),
    "mon_band_enable":   (118, MON,  "Mon_BandEnable"),
    "mon_band_running":  (119, MON,  "Mon_BandRunning"),
    "mon_cfg_valid":     (120, MON,  "Mon_CfgValid"),
    "mon_cfg_ready":     (121, MON,  "Mon_CfgReady"),
    "mon_hard_stop":     (122, MON,  "Mon_HardStop"),
    "mon_aux_stop":      (123, MON,  "Mon_AuxStop"),
    "mon_soft_stop":     (124, MON,  "Mon_SoftStop"),
    "mon_auto_stop_done": (125, MON, "Mon_AutoStopDone"),
    "mon_stop_reason":   (126, MON,  "Mon_StopReason"),
    "mon_auto_stop_accum": (127, MON, "Mon_AutoStopAccum"),
    # -- VFD (FIJOS). Desde Python solo se LEEN: los gobierna el ST.
    "vfd_control":       (500, VFD,  "VFD_Control"),       # 18 dir 1 · 34 dir 2 · 1 stop (§16)
    "vfd_speed_raw":     (502, VFD,  "VFD_SpeedRaw"),      # velocidad leida del variador (x100)
    "vfd_freq_calc":     (504, VFD,  "VFD_FreqCalc"),      # consigna = FreqRequest * 100
    "vfd_reset":         (506, VFD,  "VFD_ResetReg"),      # 0 / 2 durante la secuencia de reset
}


def _addr(clave: str) -> int:
    return R(BAND_REGISTERS[clave][0])


# Registros que el backend puede escribir. TODO lo demas se rechaza: no es
# una lista negra de registros prohibidos sino una lista blanca.
ADDR_ESCRIBIBLES = frozenset(R(n) for n, acc, _ in BAND_REGISTERS.values() if acc in (CFG, TRIG))
ADDR_SOLO_LECTURA = frozenset(R(n) for n, acc, _ in BAND_REGISTERS.values() if acc not in (CFG, TRIG))

# Bloques contiguos que lee leer_estado(): 5 peticiones Modbus en total.
LECTURA_BLOQUES = ((1, 41), (50, 1), (60, 4), (100, 28), (500, 7))

# Nombres historicos (los usan los metodos y app.py).
ADDR_BAND_ENABLE_REG = _addr("band_enable")
ADDR_DIR_CMD         = _addr("dir_cmd")
ADDR_BAND_STATUS     = _addr("band_status")
ADDR_FREQ_REQUEST    = _addr("freq_request")
ADDR_NEW_CFG_FLAG    = _addr("new_cfg_flag")
ADDR_RESET_CMD       = _addr("reset_cmd")
ADDR_CFG_READY_REG   = _addr("cfg_ready")
ADDR_VFD_SPEED_DISP  = _addr("vfd_speed_disp")
ADDR_STOP_MODE       = _addr("stop_mode")
ADDR_SOFT_STOP_CMD   = _addr("soft_stop_cmd")
ADDR_AUTO_STOP_PRESET = _addr("auto_stop_preset")
ADDR_AUTO_STOP_MODE  = _addr("auto_stop_mode")

# Acceso por numero de sensor (1 / 2)
ADDR_SENSOR = {
    n: {campo: _addr(f"s{n}_{campo}") for campo in (
        "enable", "action", "timer_preset", "count_preset", "torreta_mask",
        "count_accum", "timer_accum", "count_done", "pluma1", "pluma2",
        "band_mode", "event_active")}
    for n in (1, 2)
}

ADDR_TORRETA_RUN   = _addr("torreta_run")
ADDR_TORRETA_IDLE  = _addr("torreta_idle")
ADDR_TORRETA_I1    = _addr("torreta_i1")

ADDR_PLUMA1_CMD    = _addr("pluma1_cmd")
ADDR_PLUMA2_CMD    = _addr("pluma2_cmd")
ADDR_PLUMA1_STATUS = _addr("pluma1_status")
ADDR_PLUMA2_STATUS = _addr("pluma2_status")

ADDR_PLUMA = {
    1: {"cmd": ADDR_PLUMA1_CMD, "status": ADDR_PLUMA1_STATUS},
    2: {"cmd": ADDR_PLUMA2_CMD, "status": ADDR_PLUMA2_STATUS},
}

ADDR_VFD_FREQ_CALC = _addr("vfd_freq_calc")


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

BAND_DIR = {
    "paro": DIR_PARO, "parar": DIR_PARO, "stop": DIR_PARO, "0": DIR_PARO,
    "derecha": DIR_1, "right": DIR_1, "der": DIR_1, "cw": DIR_1,
    "horario": DIR_1, "1": DIR_1, "dir1": DIR_1, "direccion1": DIR_1,
    "izquierda": DIR_2, "left": DIR_2, "izq": DIR_2, "ccw": DIR_2,
    "antihorario": DIR_2, "2": DIR_2, "dir2": DIR_2, "direccion2": DIR_2,
}

DIR_NOMBRE = {DIR_PARO: "sin direccion", DIR_1: "direccion 1", DIR_2: "direccion 2"}

# StopMode (%R9) segun §4. I3 SIEMPRE detiene: no se puede deshabilitar.
STOP_MODE_I3, STOP_MODE_I2, STOP_MODE_SW, STOP_MODE_I2_SW = 0, 1, 2, 3
STOP_MODE_NOMBRE = {
    STOP_MODE_I3: "I3", STOP_MODE_I2: "I2 + I3",
    STOP_MODE_SW: "Software + I3", STOP_MODE_I2_SW: "I2 + Software + I3",
}
STOP_MODES = {
    "i3": 0, "solo_i3": 0,
    "i2": 1, "i2_i3": 1,
    "software": 2, "sw": 2, "software_i3": 2,
    "i2_software": 3, "i2_software_i3": 3, "todos": 3,
}

# AutoStopMode (%R15) segun §14b.
AUTO_STOP_OFF, AUTO_STOP_MOVIMIENTO, AUTO_STOP_TOTAL = 0, 1, 2
AUTO_STOP_NOMBRE = {
    AUTO_STOP_OFF: "deshabilitado",
    AUTO_STOP_MOVIMIENTO: "cuenta solo el tiempo de movimiento real",
    AUTO_STOP_TOTAL: "cuenta desde START aunque un sensor pause la banda",
}
AUTO_STOP_MODES = {
    "off": 0, "no": 0, "deshabilitado": 0,
    "movimiento": 1, "real": 1,
    "total": 2, "desde_start": 2,
}

# StopReason (%R14) segun §4 y §14b.
STOP_REASON_TEXTO = {
    0: "Sin paro",
    1: "Detenida por I3",
    2: "Detenida por I2",
    3: "Detenida por paro software",
    4: "Paro automatico completado",
    5: "Detenida por Sensor 1",
    6: "Detenida por Sensor 2",
}

# S1_Action / S2_Action (%R21 / %R31), tal como los interpretan §11..§14.
# Cada deteccion (o el conteo alcanzado) abre un EVENTO del sensor, que NO
# depende de BandEnable:
#   0 -> evento mientras el sensor detecta; no pausa la banda (cuenta)
#   1 -> evento mientras detecta; con BandMode 0 pausa la banda
#   2 -> evento de TimerPreset segundos; con BandMode 0 pausa la banda
#   3 -> como 1 (nombre historico "con torreta")
#   4 -> como 2 (nombre historico "con torreta")
# La torreta y las plumas del sensor actuan mientras dura el evento, con
# cualquier accion. Una pausa por sensor NO quita BandEnable: al terminar el
# evento la banda CONTINUA SOLA, sin pedir un nuevo START.
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
    ACTION_NADA: "evento mientras detecta, sin pausar la banda",
    ACTION_PARO_PRESENCIA: "evento mientras detecta",
    ACTION_PARO_TEMPORIZADO: "evento por tiempo",
    ACTION_PARO_PRESENCIA_TORRETA: "evento mientras detecta (con torreta)",
    ACTION_PARO_TEMPORIZADO_TORRETA: "evento por tiempo (con torreta)",
}

# Acciones del Ladder ANTERIOR que ya no existen. Se siguen ACEPTANDO para no
# romper programas guardados, pero se traducen a lo que el ST si puede hacer,
# y avisos_config() lo explica.
SENSOR_ACTIONS_OBSOLETAS = {
    "paro_enclavado": ACTION_PARO_TEMPORIZADO,
    "contar": ACTION_NADA,
    "contar_y_parar": ACTION_NADA,
}

# Comando de pluma que aplica un sensor (%R28/%R29/%R38/%R39, §17). Se aplica
# MIENTRAS dura el evento de ese sensor (SN_EventActive); al terminar, la
# pluma vuelve al comando manual (%R60/%R61).
SENSOR_PLUMA_NADA, SENSOR_PLUMA_SUBIR, SENSOR_PLUMA_BAJAR, SENSOR_PLUMA_STOP = 0, 1, 2, 3
SENSOR_PLUMA_CMDS = {
    "nada": 0, "ninguno": 0, "sin_intervenir": 0,
    "subir": 1, "arriba": 1, "up": 1,
    "bajar": 2, "abajo": 2, "down": 2,
    "stop": 3, "parar": 3, "detener": 3, "forzar_stop": 3,
}

# SN_BandMode (%R16/%R17, §11/§13): si el evento de un sensor puede pausar la banda.
BAND_MODE_PAUSA, BAND_MODE_SOLO_EVENTO = 0, 1

# Botones fisicos que el ST acepta como START (§5: BtnStart := PhIn1). Si una
# version futura del ST admite otro, se agrega aqui y el resto lo respeta.
START_BUTTONS = ("I1",)

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
# Mapeo fisico confirmado: Pluma 1 Q8 sube / Q9 baja · Pluma 2 Q6 sube / Q7 baja.
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
PLUMA_SALIDAS = {1: {"subir": "Q8", "bajar": "Q9"}, 2: {"subir": "Q6", "bajar": "Q7"}}

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
# El PLC atiende Modbus entre scans: justo despues de escribir R5/R6, %R7 aun
# puede leerse en 1 (valor de ANTES del trigger). Tras un trigger se espera
# primero a verlo en 0, para no confirmar un "listo" que no existe. La
# secuencia de §8 deja CfgReady en 0 al menos 1-2 s, asi que sobra margen.
CFG_DROP_TIMEOUT_S = 2.0
CFG_DROP_POLL_S = 0.05


def _codigo(valor, tabla, low, high):
    """Codigo entero low..high de un valor numerico o de su nombre en 'tabla'.
    None si no se reconoce (o si viene None)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if low <= valor <= high else None
    clave = str(valor).strip().lower().replace("+", "_").replace(" ", "_")
    if clave.lstrip("-").isdigit():
        n = int(clave)
        return n if low <= n <= high else None
    return tabla.get(clave)


def _con_signo(v: int) -> int:
    """Modbus entrega 0..65535; los tags del ST son INT de 16 bits con signo."""
    v = int(v) & 0xFFFF
    return v - 0x10000 if v > INT_MAX else v


# ---------------------------------------------------------------------------
# DRIVER MODBUS
# ---------------------------------------------------------------------------
class BandaPLC:
    """Cliente Modbus TCP del PLC de la banda transportadora.

    TODA la comunicacion Modbus de la banda pasa por esta clase: no hay
    llamadas sueltas repartidas por otros archivos. Las primitivas publicas
    son read_band_register / write_band_register y, encima de ellas, las
    operaciones de alto nivel (configurar_*, trigger_new_config,
    trigger_vfd_reset, configurar_paros, paro_software, configurar_autostop,
    leer_estado, command_gate).

    Ningun metodo escribe un registro fuera de ADDR_ESCRIBIBLES, y ninguno
    escribe R500/R502/R504/R506 ni los monitores R100..R127."""

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
        """Escribe un registro de la banda. Solo acepta registros de
        configuracion/comando (lista blanca ADDR_ESCRIBIBLES)."""
        if addr not in ADDR_ESCRIBIBLES:
            motivo = ("lo gobierna el programa maestro ST (feedback/monitoreo/VFD)"
                      if addr in ADDR_SOLO_LECTURA else "no existe en el mapa de la banda")
            raise ValueError(
                f"%R{addr - 2999} no es escribible: {motivo}. El backend no debe escribirlo.")
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

        El feedback de la banda vive en bloques contiguos (LECTURA_BLOQUES),
        asi que el sondeo del frontend cabe en unas pocas peticiones en vez de
        una por registro: el PLC no se satura."""
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
        if clave.isdigit():
            return BandaPLC._accion(int(clave))
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
    def _enum(valor, tabla, low, high, etiqueta):
        codigo = _codigo(valor, tabla, low, high)
        if codigo is None:
            raise ValueError(f"{etiqueta}: '{valor}' no es valido ({low}..{high} o {sorted(tabla)}).")
        return codigo

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

    def sin_movimiento(self):
        """Configuracion sin banda: DirCmd (%R2) = FreqRequest (%R4) = 0.

        §3 del ST solo da por valida una configuracion sin movimiento si los
        DOS valen 0 (MotionCfgPresent = FALSE). Una frecuencia vieja en %R4
        con DirCmd = 0 dejaria CfgValid = FALSE y sin sensores ni torreta."""
        self._w(ADDR_DIR_CMD, DIR_PARO)
        self._w(ADDR_FREQ_REQUEST, 0)
        print("BANDA sin movimiento (DirCmd=0, FreqRequest=0)")

    # -- §4  paros ---------------------------------------------------------
    def configurar_paros(self, stop_mode=STOP_MODE_I3):
        """StopMode (%R9): que fuentes, ademas de I3, generan el paro general.

        0 = solo I3 · 1 = I2 + I3 · 2 = software + I3 · 3 = I2 + software + I3.
        I3 SIEMPRE detiene: ningun valor lo deshabilita. El ST lo lee en cada
        scan, asi que no necesita NewCfgFlag."""
        modo = self._enum(stop_mode, STOP_MODES, 0, 3, "StopMode")
        self._w(ADDR_STOP_MODE, modo)
        print(f"Paros: StopMode={modo} ({STOP_MODE_NOMBRE[modo]})")
        return modo

    def paro_software(self, activo: bool):
        """SoftStopCmd (%R10): 1 = enclava el paro software, 0 = lo libera.

        Solo tiene efecto con StopMode 2 o 3 (§4). Liberarlo NO rearranca la
        banda: el paro borro BandEnable y hace falta un nuevo flanco de I1."""
        valor = 1 if activo else 0
        self._w(ADDR_SOFT_STOP_CMD, valor)
        print(f"Paro software {'ACTIVADO' if valor else 'liberado'} (SoftStopCmd={valor})")
        return valor

    # -- §14b  paro automatico ---------------------------------------------
    def configurar_autostop(self, modo=AUTO_STOP_OFF, preset_s=0):
        """AutoStopPreset (%R11) y AutoStopMode (%R15).

        modo 1 cuenta solo los segundos de movimiento real (una pausa por
        sensor detiene el conteo); modo 2 cuenta desde START aunque un sensor
        pause la banda. El ST invalida la configuracion si modo > 0 con
        preset <= 0, asi que se rechaza aqui antes de escribir."""
        m = self._enum(modo, AUTO_STOP_MODES, 0, 2, "AutoStopMode")
        p = self._entero(preset_s or 0, 0, INT_MAX, "AutoStopPreset (s)")
        if m and p <= 0:
            raise ValueError("El paro automatico necesita un tiempo mayor que 0 segundos.")
        self._w(ADDR_AUTO_STOP_PRESET, p)
        self._w(ADDR_AUTO_STOP_MODE, m)
        print(f"Paro automatico: modo={m} ({AUTO_STOP_NOMBRE[m]}) | preset={p}s")
        return m, p

    # -- §11..§14  sensores S1 y S2 ---------------------------------------
    def configurar_sensor(self, n, accion=None, timer_preset=None,
                          count_preset=None, torreta_mask=None, habilitar=True,
                          pluma1=None, pluma2=None, band_mode=None):
        """Configura el bloque completo de S1 (%R16, %R20..%R29) o S2 (%R17, %R30..%R39).

        accion       : 0..4 o su nombre ('nada', 'paro_presencia',
                       'paro_temporizado', 'paro_presencia_torreta',
                       'paro_temporizado_torreta')
        timer_preset : segundos de paro (acciones 2 y 4). El ST invalida la
                       configuracion si la accion es 2 o 4 y el preset es <= 0.
        count_preset : 0 = actuar en CADA deteccion; N>0 = actuar al llegar a
                       N detecciones (y entonces CountDone se queda en 1).
        torreta_mask : bitmask 0..7 que se superpone durante el evento
                       (acciones 3 y 4)
        pluma1/pluma2: 0 nada · 1 subir · 2 bajar · 3 forzar stop, aplicado
                       MIENTRAS dura el evento del sensor (§17)
        band_mode    : 0 = el evento puede pausar la banda (acciones 1..4)
                       1 = solo evento: el sensor nunca pausa la banda
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
        for m, valor in ((1, pluma1), (2, pluma2)):
            if valor is not None:
                self._w(a[f"pluma{m}"],
                        self._enum(valor, SENSOR_PLUMA_CMDS, 0, 3, f"S{n} pluma {m}"))
        if band_mode is not None:
            self._w(a["band_mode"], self._entero(band_mode, 0, 1, f"S{n}_BandMode"))

        print(f"S{n}: {'habilitado' if habilitar else 'deshabilitado'}"
              + (f" | accion={accion} (S{n}_Action={acc})" if acc is not None else "")
              + (f" | timer={timer_preset}s" if timer_preset is not None else "")
              + (f" | conteo={count_preset}" if count_preset is not None else "")
              + (f" | torreta={torreta_mask}" if torreta_mask is not None else "")
              + (f" | plumas={pluma1}/{pluma2}" if (pluma1, pluma2) != (None, None) else ""))

    def apagar_sensor(self, n):
        """Deja el sensor sin efecto y con TODOS sus campos en valores validos.

        §3 del ST valida accion, tiempo, conteo, mascara, plumas y BandMode
        aunque el sensor este deshabilitado, y esos registros son RETAIN: un
        valor viejo fuera de rango (p. ej. S1_TorretaMask = 15) dejaria
        CfgValid = FALSE y la configuracion nunca llegaria a CfgReady."""
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}.")
        a = ADDR_SENSOR[n]
        self._w(a["enable"], 0)
        self._w(a["action"], ACTION_NADA)
        self._w(a["timer_preset"], 0)
        self._w(a["count_preset"], 0)
        self._w(a["torreta_mask"], 0)
        self._w(a["pluma1"], SENSOR_PLUMA_NADA)
        self._w(a["pluma2"], SENSOR_PLUMA_NADA)
        self._w(a["band_mode"], BAND_MODE_PAUSA)
        print(f"S{n}: deshabilitado")

    def reset_contador(self, n) -> bool:
        """Pone a 0 el acumulado del sensor (%R25 / %R35).

        El ST no tiene registro CountReset: el acumulado es RETAIN y solo lo
        borran NewCfgFlag (§6) y ResetCmd (§7); el paro I3 NO lo toca. Se
        escribe directamente el acumulador, que §11/§13 solo incrementan.

        OJO: esto NO rearma la accion por conteo. SN_CountDone es un BOOL
        interno que solo borran NewCfgFlag y ResetCmd; %R27/%R37 es su espejo
        y §18 lo reescribe en cada scan, asi que escribirlo no sirve.
        Devuelve True si CountDone seguia activo (la accion no se repetira)."""
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}.")
        seguia = self._r(ADDR_SENSOR[n]["count_done"]) == 1
        self._w(ADDR_SENSOR[n]["count_accum"], 0)
        print(f"S{n}: contador en 0" + (" (CountDone sigue activo)" if seguia else ""))
        return seguia

    # -- §15  torreta ------------------------------------------------------
    def configurar_torreta(self, mask_run=None, mask_idle=None, mask_i1=None):
        """TorretaRun (%R40), TorretaIdle (%R41) e I1_LampMask (%R50).
        Bitmask 0..7 (b0=V,b1=A,b2=R).

        mask_i1: lamparas que el ST (§15b) enciende SOLO mientras I1 esta
        presionado; se apagan al soltarlo y el paro general las apaga.

        La torreta la gobierna el ST (Q3/Q4/Q5); aqui solo se declara que se
        enciende con la banda corriendo y que se enciende con la banda
        detenida. Prioridad del ST: S2 -> S1 -> RUN -> IDLE."""
        if mask_run is not None:
            self._w(ADDR_TORRETA_RUN,
                    self._entero(mask_run, MASK_MIN, MASK_MAX,
                                 "La mascara de torreta en marcha"))
        if mask_idle is not None:
            self._w(ADDR_TORRETA_IDLE,
                    self._entero(mask_idle, MASK_MIN, MASK_MAX,
                                 "La mascara de torreta detenida"))
        if mask_i1 is not None:
            self._w(ADDR_TORRETA_I1,
                    self._entero(mask_i1, MASK_MIN, MASK_MAX,
                                 "La mascara de lamparas con I1"))
        print("Torreta configurada"
              + (f" | run={mask_run}" if mask_run is not None else "")
              + (f" | idle={mask_idle}" if mask_idle is not None else "")
              + (f" | i1={mask_i1}" if mask_i1 is not None else ""))

    # -- §17  plumas -------------------------------------------------------
    def command_gate(self, n, comando):
        """Manda un comando a la pluma n (1 o 2): 0=stop, 1=subir, 2=bajar.

        Escribe SOLO Pluma1Cmd (%R60) o Pluma2Cmd (%R61). Las salidas fisicas
        (Pluma 1: Q8/Q9 · Pluma 2: Q6/Q7), el enclavamiento entre sentidos y
        la prioridad paro > S2 > S1 > manual son del ST (§17)."""
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

    def esperar_config_lista(self, timeout=CFG_READY_TIMEOUT_S,
                             esperar_caida=False) -> bool:
        """Espera a que CfgReady_Reg (%R7) valga 1 tras un trigger.

        NO se asume que el VFD este listo por haber escrito los registros: la
        secuencia de §8 mantiene el reset 2 s. Devuelve True si quedo lista.

        esperar_caida=True (triggers que llegan con R7 posiblemente en 1, como
        ResetCmd): primero se exige ver R7 = 0, que prueba que el PLC tomo el
        trigger. Si nunca baja, el trigger no se proceso y se devuelve False
        en vez de confirmar el 1 viejo."""
        if esperar_caida:
            limite = time.time() + CFG_DROP_TIMEOUT_S
            bajo = False
            while time.time() < limite:
                try:
                    if self._r(ADDR_CFG_READY_REG) == 0:
                        bajo = True
                        break
                except IOError:
                    pass
                time.sleep(CFG_DROP_POLL_S)
            if not bajo:
                print("AVISO: CfgReady (%R7) no bajo a 0: el PLC no tomo el trigger.")
                return False
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
        """Paro por Modbus que DESARMA la configuracion: DirCmd (%R2) = 0.

        Para el ST, DirCmd=0 no es un modo de marcha sino CONFIGURACION
        INVALIDA (§3): §8 responde con CfgReady=0, VFD_Control=1 y
        VFD_FreqCalc=0, o sea paro inmediato del variador. Funciona con
        cualquier StopMode, pero deja el sistema en 'no listo': para volver a
        operar hay que reconfigurar (DirCmd 1/2 + NewCfgFlag) y pulsar I1.
        El paro que conserva la configuracion es paro_software() (StopMode 2/3)."""
        self._w(ADDR_DIR_CMD, DIR_PARO)
        print("BANDA DETENIDA (DirCmd=0 -> configuracion invalida -> VFD_Control=1)")

    def config_lista(self) -> bool:
        """True si la secuencia init termino y CfgReady esta activo (%R7)."""
        return self._r(ADDR_CFG_READY_REG) == 1

    # -- LECTURA DE ESTADO / FEEDBACK --------------------------------------
    def leer_estado(self) -> dict:
        """Estado COMPLETO para el frontend (6 lecturas por bloque).

        Todo sale de registros del ST: control y paros (R1..R15), sensores y
        torreta (R20..R41), I1_LampMask (R50), plumas (R60..R63), monitoreo §19
        (R100..R127) y VFD (R500..R506, solo diagnostico). Las entradas y
        salidas fisicas se toman de los monitores, que el ST ya normaliza."""
        reg = {}
        for inicio, cuantos in LECTURA_BLOQUES:
            for i, v in enumerate(self.read_band_block(R(inicio), cuantos)):
                reg[inicio + i] = _con_signo(v)
        return estado_desde_registros(reg)

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


def estado_desde_registros(reg: dict) -> dict:
    """Traduce {numero %R: valor} al estado que pinta el frontend.

    Separado de BandaPLC para poder probarlo sin PLC. Todo sale de registros
    LEIDOS: nunca del ultimo comando enviado."""
    def g(clave):
        return reg.get(BAND_REGISTERS[clave][0], 0)

    def si(clave):
        return g(clave) == 1

    status = g("band_status")
    razon = g("stop_reason")
    estado = {
        "band_status": status,
        "estado": BAND_STATUS.get(status, str(status)),
        "running": status in (1, 2),
        "direccion": {1: 1, 2: 2}.get(status),
        "band_enable": si("band_enable"),
        "cfg_ready": si("cfg_ready"),
        "cfg_valid": si("mon_cfg_valid"),
        "gen_stop": si("mon_gen_stop"),
        "dir_cmd": g("dir_cmd"),
        "freq_request_hz": g("freq_request"),
        # §3 MotionCfgPresent: sin direccion ni frecuencia no hay VFD ni START.
        "movimiento_configurado": g("dir_cmd") != 0 or g("freq_request") != 0,
        # %R8 ya viene escalado por el ST (§10): no se vuelve a dividir.
        "vfd_speed_hz": g("vfd_speed_disp"),
        # Paros (§4)
        "stop_mode": g("stop_mode"),
        "stop_mode_nombre": STOP_MODE_NOMBRE.get(g("stop_mode"), str(g("stop_mode"))),
        "soft_stop_cmd": g("soft_stop_cmd"),
        "hard_stop": si("mon_hard_stop"),
        "aux_stop": si("mon_aux_stop"),
        "soft_stop": si("mon_soft_stop"),
        "stop_reason": razon,
        "stop_reason_texto": STOP_REASON_TEXTO.get(razon, f"Causa desconocida ({razon})"),
        # Paro automatico (§14b)
        "auto_stop_mode": g("auto_stop_mode"),
        "auto_stop_mode_nombre": AUTO_STOP_NOMBRE.get(g("auto_stop_mode"), str(g("auto_stop_mode"))),
        "auto_stop_preset": g("auto_stop_preset"),
        "auto_stop_accum": g("auto_stop_accum"),
        "auto_stop_done": si("auto_stop_done"),
        # Entradas tal como las normaliza §2 (1 = activo)
        "i1_pulsado": si("mon_btn_start"),
        "i2_activo": si("mon_btn_aux"),
        "i3_paro": si("mon_btn_stop"),
        "s1_detecta": si("mon_s1_det"),
        "s2_detecta": si("mon_s2_det"),
        "entradas": {f"I{n}": g(f"mon_i{n}_raw") for n in range(1, 6)},
        "salidas": {f"Q{n}": g(f"mon_q{n}") for n in range(3, 10)},
        "lamparas": {"verde": si("mon_q3"), "amarilla": si("mon_q4"), "roja": si("mon_q5")},
        "torreta": {
            "run": g("torreta_run"), "run_nombre": TORRETA_NOMBRE.get(g("torreta_run"), ""),
            "idle": g("torreta_idle"), "idle_nombre": TORRETA_NOMBRE.get(g("torreta_idle"), ""),
            "i1": g("torreta_i1"), "i1_nombre": TORRETA_NOMBRE.get(g("torreta_i1"), ""),
        },
        # Diagnostico del VFD (solo lectura).
        "vfd_control": g("vfd_control"),
        "vfd_speed_raw": g("vfd_speed_raw"),
        "vfd_freq_raw": g("vfd_freq_calc"),
        "vfd_freq_hz": g("vfd_freq_calc") / 100.0,
        "vfd_reset": g("vfd_reset"),
    }
    for n in (1, 2):
        st = g(f"pluma{n}_status")
        q = PLUMA_SALIDAS[n]
        estado[f"pluma{n}"] = {
            "status": st, "estado": PLUMA_ESTADO.get(st, str(st)),
            "cmd": g(f"pluma{n}_cmd"),
            "subir": g(f"mon_q{q['subir'][1:]}") == 1, "bajar": g(f"mon_q{q['bajar'][1:]}") == 1,
            "salidas": q,
        }
    estado["sensores"] = {}
    for n in (1, 2):
        acc = g(f"s{n}_action")
        estado[f"s{n}_count"] = g(f"s{n}_count_accum")
        estado[f"s{n}_timer_s"] = g(f"s{n}_timer_accum")
        estado[f"s{n}_count_done"] = si(f"s{n}_count_done")
        estado[f"s{n}_event_active"] = si(f"s{n}_event_active")
        estado["sensores"][f"s{n}"] = {
            "enable": g(f"s{n}_enable") != 0,
            "action": acc, "action_nombre": ACTION_NOMBRE.get(acc, str(acc)),
            "timer_preset": g(f"s{n}_timer_preset"),
            "count_preset": g(f"s{n}_count_preset"),
            "torreta": g(f"s{n}_torreta_mask"),
            "pluma1": g(f"s{n}_pluma1"), "pluma2": g(f"s{n}_pluma2"),
            "count": g(f"s{n}_count_accum"), "timer_s": g(f"s{n}_timer_accum"),
            "count_done": si(f"s{n}_count_done"),
            "detecta": si(f"mon_s{n}_det"),
            "band_mode": g(f"s{n}_band_mode"),
            "event_active": si(f"s{n}_event_active"),
        }
    # Por que el PLC rechaza la configuracion (vacio si CfgValid = 1).
    estado["motivos_cfg_invalida"] = [] if estado["cfg_valid"] else motivos_config_invalida(reg)
    # Tabla de diagnostico: todos los registros leidos, con su acceso.
    estado["registros"] = [
        {"r": n, "simbolo": simbolo, "acceso": acc, "valor": reg[n]}
        for n, acc, simbolo in sorted(BAND_REGISTERS.values())
        if n in reg
    ]
    estado["fase"] = _fase_visual(estado)
    return estado


def motivos_config_invalida(reg: dict) -> list:
    """Reproduce §3 del ST (CfgValid) sobre los registros LEIDOS y devuelve,
    en texto, cada regla que falla. Sirve para decirle al operador por que el
    PLC no llega a CfgReady en vez de un aviso generico."""
    def g(clave):
        return reg.get(BAND_REGISTERS[clave][0], 0)

    def r(clave):
        n, _, simbolo = BAND_REGISTERS[clave]
        return f"{simbolo} (%R{n})"

    m = []
    dir_cmd, freq = g("dir_cmd"), g("freq_request")
    movimiento = dir_cmd != 0 or freq != 0
    if movimiento:
        if dir_cmd not in (DIR_1, DIR_2):
            m.append(f"{r('dir_cmd')} = {dir_cmd}: con movimiento debe ser 1 o 2.")
        if not FREQ_MIN_HZ <= freq <= FREQ_MAX_HZ:
            m.append(f"{r('freq_request')} = {freq}: con movimiento debe estar entre "
                     f"{FREQ_MIN_HZ} y {FREQ_MAX_HZ} Hz.")
    if not 0 <= g("stop_mode") <= 3:
        m.append(f"{r('stop_mode')} = {g('stop_mode')}: debe estar entre 0 y 3.")
    am, ap = g("auto_stop_mode"), g("auto_stop_preset")
    if not 0 <= am <= 2:
        m.append(f"{r('auto_stop_mode')} = {am}: debe estar entre 0 y 2.")
    if ap < 0:
        m.append(f"{r('auto_stop_preset')} = {ap}: no puede ser negativo.")
    if am > 0 and ap <= 0:
        m.append(f"{r('auto_stop_mode')} = {am} necesita {r('auto_stop_preset')} mayor que 0.")
    if am > 0 and not movimiento:
        m.append(f"{r('auto_stop_mode')} = {am} necesita movimiento (DirCmd y FreqRequest).")
    for n in (1, 2):
        s = f"s{n}_"
        if not 0 <= g(s + "band_mode") <= 1:
            m.append(f"{r(s + 'band_mode')} = {g(s + 'band_mode')}: debe ser 0 o 1.")
        acc = g(s + "action")
        if not ACTION_MIN <= acc <= ACTION_MAX:
            m.append(f"{r(s + 'action')} = {acc}: debe estar entre 0 y 4.")
        if g(s + "timer_preset") < 0:
            m.append(f"{r(s + 'timer_preset')} = {g(s + 'timer_preset')}: no puede ser negativo.")
        if acc in (ACTION_PARO_TEMPORIZADO, ACTION_PARO_TEMPORIZADO_TORRETA) and g(s + "timer_preset") <= 0:
            m.append(f"{r(s + 'action')} = {acc} necesita {r(s + 'timer_preset')} mayor que 0.")
        if g(s + "count_preset") < 0:
            m.append(f"{r(s + 'count_preset')} = {g(s + 'count_preset')}: no puede ser negativo.")
        if not MASK_MIN <= g(s + "torreta_mask") <= MASK_MAX:
            m.append(f"{r(s + 'torreta_mask')} = {g(s + 'torreta_mask')}: debe estar entre 0 y 7.")
        for p in ("pluma1", "pluma2"):
            if not 0 <= g(s + p) <= 3:
                m.append(f"{r(s + p)} = {g(s + p)}: debe estar entre 0 y 3.")
    for clave in ("torreta_run", "torreta_idle", "torreta_i1"):
        if not MASK_MIN <= g(clave) <= MASK_MAX:
            m.append(f"{r(clave)} = {g(clave)}: debe estar entre 0 y 7.")
    for clave in ("pluma1_cmd", "pluma2_cmd"):
        if not PLUMA_CMD_MIN <= g(clave) <= PLUMA_CMD_MAX:
            m.append(f"{r(clave)} = {g(clave)}: debe estar entre 0 y 2.")
    return m


def _fase_visual(estado: dict) -> str:
    """Traduce el feedback del PLC a la etiqueta que pinta el frontend.

    Se decide SOLO con registros leidos del PLC, nunca con el ultimo comando
    enviado: el operador puede haber pulsado un paro fisico y el frontend
    tiene que enterarse. Distingue PARO GENERAL (borra BandEnable) de PAUSA
    POR SENSOR (BandEnable sigue activo y la banda continua sola)."""
    if estado.get("hard_stop"):
        return "paro_i3"
    if estado.get("aux_stop"):
        return "paro_i2"
    if estado.get("soft_stop"):
        return "paro_software"
    if estado.get("running"):
        return "corriendo"
    if estado.get("band_enable"):
        return "pausa_sensor" if estado.get("stop_reason") in (5, 6) else "habilitada"
    if estado.get("auto_stop_done"):
        return "auto_completado"
    if not estado.get("cfg_valid"):
        return "esperando_config"
    if not estado.get("cfg_ready"):
        return "configurando"          # secuencia init del VFD en curso
    if not estado.get("movimiento_configurado"):
        return "sin_marcha"            # DirCmd = FreqRequest = 0: eventos sin VFD
    return "lista"                     # configurada; falta pulsar I1


FASE_TEXTO = {
    "paro_i3": "Paro I3 activo — suelta I3 y pulsa I1",
    "paro_i2": "Paro I2 activo — suelta I2 y pulsa I1",
    "paro_software": "Paro software activo — liberalo y pulsa I1",
    "esperando_config": "Esperando configuracion",
    "sin_marcha": "Lista sin movimiento — sensores, torreta y plumas activos",
    "configurando": "Configurando VFD...",
    "lista": "Lista — esperando I1",
    "habilitada": "Banda habilitada",
    "pausa_sensor": "Pausada por sensor — continua sola",
    "auto_completado": "Secuencia terminada (paro automatico) — pulsa I1 para repetir",
    "corriendo": "Banda corriendo",
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
#       "enable": true,                    # false = la instruccion no pide movimiento
#       "direction": "derecha" | "izquierda" | 1 | 2,
#       "freq_hz": 1..327 | null,
#       "stop_mode": 0..3 | null,          # %R9  (0 I3 · 1 I2+I3 · 2 SW+I3 · 3 I2+SW+I3)
#       "auto_stop_mode": 0..2 | null,     # %R15 (0 off · 1 movimiento real · 2 desde START)
#       "auto_stop_s": 0..32767 | null,    # %R11
#       "s1_action": 0..4 | "nada"|"paro_presencia"|"paro_temporizado"|
#                    "paro_presencia_torreta"|"paro_temporizado_torreta",
#       "wait_s1_s": 0..32767 | null,      # %R22 (acciones 2 y 4)
#       "count_s1": 0..32767 | null,       # %R23 preset del contador
#       "torreta_s1": 0..7 | null,         # %R24 mascara durante el evento
#       "s1_pluma1": 0..3 | "subir"|"bajar"|"stop" | null,   # %R28
#       "s1_pluma2": ...                                     # %R29
#       ... lo mismo para s2 (%R30..%R39)
#       "torreta_run": 0..7 | null,        # %R40 mascara con la banda en marcha
#       "torreta_idle": 0..7 | null,       # %R41 mascara con la banda detenida
#       "torreta_i1": 0..7 | null,         # %R50 mascara mientras I1 esta presionado
#       "pluma1": 0..2 | "subir"|"bajar"|"stop" | null,      # %R60 manual
#       "pluma2": ...                                        # %R61
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
    if clave.isdigit():
        return _accion_codigo(int(clave))
    if clave in SENSOR_ACTIONS:
        return SENSOR_ACTIONS[clave]
    return SENSOR_ACTIONS_OBSOLETAS.get(clave)


def _auto_stop(band) -> tuple:
    """(modo, preset_s) del paro automatico. Un tiempo sin modo explicito se
    entiende como modo 1 (solo tiempo de movimiento real)."""
    preset = band.get("auto_stop_s")
    try:
        preset = int(preset) if preset is not None else 0
    except (TypeError, ValueError):
        preset = 0
    modo = _codigo(band.get("auto_stop_mode"), AUTO_STOP_MODES, 0, 2)
    if modo is None:
        modo = AUTO_STOP_MOVIMIENTO if preset > 0 else AUTO_STOP_OFF
    return modo, preset


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

    for campo in ("wait_s1_s", "wait_s2_s", "count_s1", "count_s2", "auto_stop_s"):
        if band.get(campo) is not None:
            _entero_en_rango(band[campo], 0, INT_MAX, f"band.{campo}", errores)

    for campo in ("torreta_s1", "torreta_s2", "torreta_run", "torreta_idle", "torreta_i1"):
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

    # Paros (§3: StopMode 0..3).
    if band.get("stop_mode") is not None and _codigo(band["stop_mode"], STOP_MODES, 0, 3) is None:
        errores.append(f"band.stop_mode='{band['stop_mode']}' debe ser 0 (I3), 1 (I2+I3), "
                       f"2 (software+I3) o 3 (I2+software+I3).")

    # Paro automatico (§3: AutoStopMode 0..2 y preset > 0 si esta activo).
    am = band.get("auto_stop_mode")
    if am is not None and _codigo(am, AUTO_STOP_MODES, 0, 2) is None:
        errores.append(f"band.auto_stop_mode='{am}' debe ser 0 (off), 1 (movimiento real) "
                       f"o 2 (desde START).")
    else:
        modo, preset = _auto_stop(band)
        if modo and preset <= 0:
            errores.append("band.auto_stop_mode activo necesita band.auto_stop_s mayor que "
                           "0 segundos (el ST invalida la configuracion).")

    for n in (1, 2):
        acc = band.get(f"s{n}_action")
        if acc is not None:
            codigo = _accion_codigo(acc)
            if codigo is None:
                errores.append(f"band.s{n}_action='{acc}' debe ser un codigo 0..4 o "
                               f"uno de {sorted(SENSOR_ACTIONS)}.")
            # Acciones temporizadas sin tiempo: el ST invalida TODA la config.
            elif codigo in (ACTION_PARO_TEMPORIZADO, ACTION_PARO_TEMPORIZADO_TORRETA):
                espera = band.get(f"wait_s{n}_s")
                if espera is None or _entero_en_rango(espera, 1, INT_MAX,
                                                      f"band.wait_s{n}_s", []) is None:
                    errores.append(
                        f"band.s{n}_action='{acc}' detiene la banda por tiempo, asi "
                        f"que band.wait_s{n}_s debe ser mayor que 0 segundos.")
        # Plumas por sensor (§3: 0..3).
        for m in (1, 2):
            v = band.get(f"s{n}_pluma{m}")
            if v is not None and _codigo(v, SENSOR_PLUMA_CMDS, 0, 3) is None:
                errores.append(f"band.s{n}_pluma{m}='{v}' debe ser 0 (nada), 1 (subir), "
                               f"2 (bajar) o 3 (forzar stop).")

    for n in (1, 2):
        p = band.get(f"pluma{n}")
        if p is None:
            continue
        clave = str(p).strip().lower()
        if clave not in PLUMA_CMDS:
            errores.append(f"band.pluma{n}='{p}' debe ser 0 (stop), 1 (subir) o 2 (bajar).")

    for n in (1, 2):
        bm = band.get(f"s{n}_band_mode")
        if bm is not None and _codigo(bm, {}, 0, 1) is None:
            errores.append(f"band.s{n}_band_mode='{bm}' debe ser 0 (el evento puede pausar "
                           f"la banda) o 1 (solo evento).")

    # Movimiento (§3 MotionCfgPresent): con marcha la frecuencia es obligatoria;
    # sin marcha el backend deja DirCmd = FreqRequest = 0.
    if band.get("enable") is False and _auto_stop(band)[0]:
        errores.append("El paro automatico necesita movimiento: el ST invalida la "
                       "configuracion si AutoStopMode > 0 sin direccion ni frecuencia.")
    elif not sin_marcha(cfg) and band.get("freq_hz") is None:
        errores.append("La banda debe moverse: indica la frecuencia en Hz (1..327). "
                       "Sin ella el ST da la configuracion por invalida.")
    boton = band.get("start_button")
    if boton is not None and str(boton).strip().upper() not in START_BUTTONS:
        errores.append(f"band.start_button='{boton}': el programa maestro solo arranca con "
                       f"{' o '.join(START_BUTTONS)}.")

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

        codigo, _, _ = _accion_de_sensor(band, n)
        codigo = _accion_codigo(codigo)
        # El conteo cambio de significado con el ST nuevo: conviene decirlo.
        preset = band.get(f"count_s{n}")
        if preset and codigo:
            avisos.append(
                f"S{n}: con count_s{n}={preset} la accion se ejecuta UNA vez, al "
                f"llegar a {preset} detecciones (CountDone se queda en 1 hasta la "
                f"siguiente configuracion o un Reset del VFD; ni un paro ni "
                f"poner el conteo en 0 la rearman). Con count_s{n}=0 se "
                f"ejecutaria en cada deteccion.")

        # Torreta y plumas de un sensor duran lo que dura su evento (§15/§17).
        plumas = [_codigo(band.get(f"s{n}_pluma{m}"), SENSOR_PLUMA_CMDS, 0, 3) for m in (1, 2)]
        if codigo is not None and (band.get(f"torreta_s{n}") or any(plumas)):
            dura = (f"{band.get(f'wait_s{n}_s')} s" if codigo in (ACTION_PARO_TEMPORIZADO,
                                                             ACTION_PARO_TEMPORIZADO_TORRETA)
                    else "mientras el sensor detecta")
            avisos.append(
                f"S{n}: su torreta y sus plumas actuan {dura}; al terminar el evento "
                f"cada pluma vuelve a su comando manual (%R60/%R61).")
        pausa = codigo and (_codigo(band.get(f"s{n}_band_mode"), {}, 0, 1) or 0) == BAND_MODE_PAUSA
        if pausa and sin_marcha(cfg):
            avisos.append(f"S{n}: el evento pausaria la banda, pero este programa no la mueve.")

    modo, preset = _auto_stop(band)
    if modo:
        avisos.append(
            f"Paro automatico a los {preset} s ({AUTO_STOP_NOMBRE[modo]}). Al "
            f"completarse la banda queda detenida (causa 'paro automatico'); pulsa "
            f"I1 para repetir la secuencia.")

    stop_mode = _codigo(band.get("stop_mode"), STOP_MODES, 0, 3) or 0
    if stop_mode in (STOP_MODE_I2, STOP_MODE_I2_SW):
        avisos.append("I2 (NC) tambien detiene la banda (los eventos de sensores siguen "
                      "activos); I3 sigue siendo el paro prioritario y cancela todo.")
    if stop_mode in (STOP_MODE_SW, STOP_MODE_I2_SW):
        avisos.append(
            "Paro software habilitado: se activa y se libera desde el panel de la "
            "banda (%R10). Liberarlo NO rearranca la banda: hay que pulsar I1. Con "
            "el paro software activo el VFD no termina su configuracion.")

    if band.get("torreta_i1"):
        i1 = band.get("torreta_i1")
        avisos.append(
            f"Lamparas con I1 ({TORRETA_NOMBRE.get(i1, i1)}): se encienden SOLO "
            f"mientras I1 este presionado, se apagan al soltarlo y el paro general "
            f"las apaga.")
    if sin_marcha(cfg):
        avisos.append(
            "Configuracion sin movimiento: DirCmd y FreqRequest quedan en 0. El PLC "
            "la da por lista sin preparar el VFD; sensores, torreta y plumas "
            "funcionan sin pulsar ningun boton de arranque.")
    if band.get("torreta_i1") and not sin_marcha(cfg):
        avisos.append(
            "I1 es a la vez el arranque de la banda y el boton de las lamparas con "
            "I1: al pulsarlo se encienden esas lamparas y la banda arranca.")

    if not sin_marcha(cfg):
        avisos.append(
            "El programa maestro no tiene registro BandEnable: tras cargar la "
            "configuracion la banda queda LISTA pero no habilitada. Cada nueva "
            "configuracion borra la habilitacion, asi que hay que pulsar el "
            f"boton fisico {boton_start(cfg)} (sin paro activo) para que arranque.")

    return avisos


# ---------------------------------------------------------------------------
# PLAN Y APLICACION
# ---------------------------------------------------------------------------
def _accion_de_sensor(band, n):
    """Deduce (accion, timer_preset, count_preset) para S1 o S2 desde el JSON.

    'wait_sN_s' es el atajo historico del frontend: significa 'paro
    temporizado de N segundos', que en el ST es Action = 2. Plumas o torreta
    de sensor sin accion se entienden como un evento que sigue al sensor
    (Action = 0): el ST las aplica con cualquier accion."""
    accion = band.get(f"s{n}_action")
    espera = band.get(f"wait_s{n}_s")
    conteo = band.get(f"count_s{n}")
    plumas = [_codigo(band.get(f"s{n}_pluma{m}"), SENSOR_PLUMA_CMDS, 0, 3) for m in (1, 2)]

    if accion is None:
        if espera is not None:
            accion = "paro_temporizado"
        elif any(plumas) or band.get(f"torreta_s{n}"):
            accion = "nada"
        elif conteo is not None:
            # El conteo no necesita accion propia: basta habilitar el sensor
            # con su preset (§11 cuenta siempre que Enable <> 0).
            accion = "nada"
        else:
            return None, None, None      # sensor no mencionado: no se toca

    return accion, espera, conteo


def sin_marcha(cfg) -> bool:
    """True si el programa NO pide mover la banda.

    Con el ST vigente los sensores, la torreta y las plumas son eventos que
    no dependen de BandEnable, asi que un programa sin marcha puede usarlos
    todos. Se carga con DirCmd = FreqRequest = 0 (§3 MotionCfgPresent =
    FALSE): el PLC lo da por listo sin preparar el VFD y I1 no arranca nada."""
    band = (cfg or {}).get("band") or {}
    return band.get("enable") is False


def requiere_start(cfg) -> bool:
    """¿El operador tiene que pulsar el boton fisico de arranque? Solo cuando
    la configuracion mueve la banda (§5 exige MotionCfgPresent)."""
    return not sin_marcha(cfg)


def boton_start(cfg) -> str:
    """Boton de arranque de la configuracion (el ST vigente solo acepta I1)."""
    band = (cfg or {}).get("band") or {}
    boton = str(band.get("start_button") or "").strip().upper()
    return boton if boton in START_BUTTONS else START_BUTTONS[0]



def plan_config(cfg) -> list:
    """Traduce el engine_config de banda a (metodo, args, kwargs) SIN Modbus.

    Orden impuesto por el programa maestro ST:

      1) parar()              -> DirCmd = 0: el ST lo lee como configuracion
                                 invalida y detiene el VFD en el acto. Nada se
                                 reconfigura con la banda en movimiento.
      2) configuracion        -> frecuencia, paros, paro automatico, sensores
                                 (con sus plumas), torreta y, al final, el
                                 sentido de giro (DirCmd = 1/2)
      3) trigger_vfd_reset()  -> ResetCmd y NewCfgFlag SIEMPRE, aunque el
         trigger_new_config()    usuario no pida reset. Cambian de valor
                                 (1, 2, 3...) y NewCfgFlag va el ultimo, para
                                 que el PLC no lea una configuracion a medias.
      4) esperar_config_lista -> se espera CfgReady (%R7) = 1 antes de dar el
                                 sistema por listo.
      5) plumas               -> comandos manuales, si el programa los pidio.

    El arranque NO esta en el plan: BandEnable solo lo enciende el boton
    fisico I1, y cada NewCfgFlag lo borra. SoftStopCmd tampoco: es un mando en
    vivo del panel, no parte del programa."""
    plan = []
    band = cfg.get("band") or {}
    marcha = not sin_marcha(cfg)

    # 1) Estado seguro: nada se reconfigura con la banda en marcha.
    plan.append(("parar", (), {}))

    # 2) Configuracion (la direccion va al final de este bloque). Sin marcha,
    #    DirCmd y FreqRequest quedan en 0: §3 la da por valida sin VFD.
    if not marcha:
        plan.append(("sin_movimiento", (), {}))
    elif band.get("freq_hz") is not None:
        plan.append(("configurar_banda", (), {"frecuencia_hz": band.get("freq_hz")}))

    # Paros y paro automatico SIEMPRE (null -> 0): son RETAIN en el PLC y un
    # valor de un programa anterior seguiria decidiendo como para la banda.
    plan.append(("configurar_paros", (), {
        "stop_mode": _codigo(band.get("stop_mode"), STOP_MODES, 0, 3) or STOP_MODE_I3}))
    modo, preset = _auto_stop(band)
    plan.append(("configurar_autostop", (), {"modo": modo, "preset_s": preset if modo else 0}))

    for n in (1, 2):
        accion, espera, conteo = _accion_de_sensor(band, n)
        if accion is None:
            plan.append(("apagar_sensor", (n,), {}))
            continue
        # count_preset, torreta_mask y plumas se escriben SIEMPRE que el sensor
        # se configure, aunque el programa no los mencione: esos registros son
        # RETAIN en el PLC y, sin escribirlos, un valor viejo seguiria
        # decidiendo cuando y como actua el sensor. 0 = comportamiento por
        # defecto (actuar en cada deteccion / sin lamparas / sin plumas).
        plan.append(("configurar_sensor", (n,), {
            "accion": accion,
            "timer_preset": espera,
            "count_preset": conteo if conteo is not None else 0,
            "torreta_mask": band.get(f"torreta_s{n}") or 0,
            "habilitar": True,
            "pluma1": band.get(f"s{n}_pluma1") or 0,
            "pluma2": band.get(f"s{n}_pluma2") or 0,
            "band_mode": _codigo(band.get(f"s{n}_band_mode"), {}, 0, 1) or BAND_MODE_PAUSA,
        }))

    # Torreta: las tres mascaras SIEMPRE (null -> 0). Son registros que el PLC
    # conserva entre cargas: sin escribirlos, la mascara de un programa
    # anterior seguiria encendiendo lamparas que este programa no pidio.
    plan.append(("configurar_torreta", (), {
        "mask_run": band.get("torreta_run") or 0,
        "mask_idle": band.get("torreta_idle") or 0,
        "mask_i1": band.get("torreta_i1") or 0,
    }))

    if marcha:
        # Sentido de giro: ultimo parametro antes de los triggers. El ST exige
        # 1 o 2 cuando hay movimiento, asi que sin direccion explicita va la 1.
        plan.append(("cambiar_direccion", (band.get("direction") or DIR_1,), {}))

    # 3) Reset + nueva configuracion SIEMPRE, aunque el usuario no lo pida.
    #    NewCfgFlag va el ultimo para que el PLC nunca lea la config a medias.
    plan.append(("trigger_vfd_reset", (), {}))
    plan.append(("trigger_new_config", (), {}))

    # 4) Confirmacion real del PLC (%R7). Con movimiento, CfgReady cae y vuelve
    #    a 1 al terminar el reset del VFD (§8); sin movimiento, el ST lo da por
    #    listo en cuanto la configuracion es valida.
    plan.append(("esperar_config_lista", (), {"esperar_caida": marcha}))

    # 5) Plumas manuales (independientes de la secuencia del VFD)
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
