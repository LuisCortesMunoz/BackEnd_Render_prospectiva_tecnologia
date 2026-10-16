"""
============================================================================
MOTOR LAVO v7 — PROGRAMA MAESTRO GENERAL DE LA BANDA (EngineMode 7)
============================================================================

Programa maestro: cscape/ST_banda_v7_lavo.txt  (= LAVO_Main_ST.txt en
"Archivos Cscape"). Lo genera cscape/generar_st_v7.py A PARTIR DE ESTE MODULO:
las direcciones, los offsets y los codigos que usa el ST salen de las
constantes de aqui, asi que Python y el PLC no pueden divergir.

Tres conceptos separados (punto 6 de la especificacion):

  RECURSOS FISICOS   lo que existe y como esta cableado. Vive en la seccion
                     §L0 del ST y en HW_BANDA (abajo). NA/NC, el paro de
                     emergencia I3 y el enclavamiento de las plumas son
                     FIJOS: ningun registro Modbus los cambia.
  FUNCIONES LOGICAS  bloques genericos del motor: condiciones (AND/OR/XOR/
                     NAND/NOR con flanco), memorias set/reset, timers (TON,
                     TOF, PULSO, RETARDO, TONR, INTERMITENTE), contadores
                     (sube/baja, reset, una vez/ciclico/nivel), 2 secuencias
                     de 16 pasos con transiciones y ramas, motor (VFD) y
                     salidas.
  ASIGNACIONES       los registros de configuracion: cada campo es un CODIGO
                     DE SENAL que conecta un recurso o un bloque con otro.
                     I1 puede ser arranque, paro, reset, condicion... segun
                     la configuracion que llegue.

Carga TRANSACCIONAL (sin configuraciones a medias):

    BEGIN  -> el PLC abre la transaccion (la config activa sigue corriendo)
    write  -> Python escribe las 640 palabras en el area de ESPERA (%R1010..)
    verify -> Python relee el area de espera
    COMMIT -> el PLC copia la espera a un buffer privado en trozos de 128
              palabras por scan, calcula el checksum y valida rangos, reglas
              de seguridad y de hardware; si todo pasa, la copia a la config
              ACTIVA en UN solo scan (atomico) y reinicia el motor en estado
              seguro. Si algo falla, RECHAZA y la config activa no cambia.
    status -> Python confirma ActiveId y checksum leidos del PLC.

Una interrupcion de comunicacion a mitad de la escritura deja la transaccion
abierta; a los 60 s el PLC la cierra por TIMEOUT. La config activa nunca se
toca hasta un COMMIT valido.

JSON simbolico v7 (vive en cfg["band"]["engine"], con "version": 7):

{
  "version": 7,
  "motor": {"arranque": "I1", "paro": "I2", "modo": "enclavado"|"mientras",
            "pausa": null, "dir_1": null, "dir_2": null, "invertir": null,
            "paro_inversion_s": 2},
  "condiciones": [{"id": "C1", "op": "AND"|"OR"|"XOR"|"NAND"|"NOR",
                   "entradas": ["I1", "NOT S2"], "flanco": "nivel"|"subida"|"bajada"}],
  "memorias":    [{"id": "M1", "set": "I1", "reset": "I2", "prioridad": "reset"|"set"}],
  "timers":      [{"id": "T1", "tipo": "TON"|"TOF"|"PULSO"|"RETARDO"|"TONR"|"INTERMITENTE",
                   "entrada": "S1", "reset": null, "segundos": 3, "segundos_off": null}],
  "contadores":  [{"id": "CT1", "entrada": "S1", "descuenta": null, "reset": "I2",
                   "preset": 3, "modo": "una_vez"|"ciclico"|"nivel"}],
  "salidas":     {"Q3": "C1", "Q4": "T1", ...}       (alias: verde, amarilla, roja,
                                                      pluma1_sube, ...)
  "secuencias":  [{"id": "SQ1", "inicio": "I1", "paro": "I2", "reset": null,
                   "pausa": null, "modo": "una_vez"|"infinita"|"repetir",
                   "repeticiones": 3, "exclusiva": true, "pausa_apaga": true,
                   "pasos": [{"salidas": ["Q3"], "motor": ["marcha"],
                              "avanza": "tiempo"|"senal"|"tiempo_y_senal"|
                                        "senal_o_tiempo"|"flanco",
                              "senal": null, "segundos": 5, "siguiente": null,
                              "alternativa": {"senal": "S2", "ir_a": 3}}]}],
  "opciones":    {"filtro_sensores_s": 0.1, "filtro_botones_s": 0,
                  "pluma_max_s": 0, "luces_paro": ["roja"]}
}
============================================================================
"""

import copy
import re
import unicodedata

ENGINE_VERSION = 7
MAGIC = 19521                      # 0x4C41 = "LA": confirma que es el ST de LAVO
CFG_LEN = 640

# ---------------------------------------------------------------------------
# DIRECCIONES  (%R de Cscape; Modbus = %R + 2999)
# Rango verificado libre en ladder_maestro_banda.csp (el %R mas alto usado es
# %R571 = fin de EI del v6, que el v7 elimina).
# ---------------------------------------------------------------------------
CMD_BASE, CMD_LEN = 1000, 10       # LCMD[0..9]   = %R1000..%R1009 (escribe Python)
STAGE_BASE = 1010                  # LSTG[0..639] = %R1010..%R1649 (escribe Python)
STATUS_BASE, STATUS_LEN = 1700, 100  # LSTA[0..99] = %R1700..%R1799 (solo lectura)
# Internas del ST con direccion FIJA (no las asigna Cscape): asi ninguna cae en
# %R200..%R499, el hueco que deja el v6 y que el flujo v5 del backend todavia
# escribe (%R200 = EngineMode del v6). Solo lectura para Python.
ACT_BASE = 2000                    # LACT[0..639] = %R2000..%R2639 (config ACTIVA)
PEN_BASE = 2700                    # LPEN[0..639] = %R2700..%R3339 (buffer de validacion)
LI_BASE, LI_LEN = 3400, 120        # LI[0..119]   = %R3400..%R3519
LD_BASE = 3530                     # LD[0..3] DINT = %R3530..%R3537
RANGO_V7 = (CMD_BASE, LD_BASE + 7)

# LCMD
C_SEQ, C_CODE, C_ARG = 0, 1, 2
C_APP = 3                          # 3..6 = APP1..APP4 (botones virtuales, en vivo)
N_APP = 4
C_SOFTSTOP = 7                     # <> 0 = paro por software (en vivo)

CMD_BEGIN, CMD_COMMIT, CMD_ABORT, CMD_UNLOAD, CMD_RESET = 1, 2, 3, 4, 5
CMD_NOMBRE = {CMD_BEGIN: "BEGIN", CMD_COMMIT: "COMMIT", CMD_ABORT: "ABORT",
              CMD_UNLOAD: "UNLOAD", CMD_RESET: "RESET"}

# Estado de la transaccion (LSTA[S_TX])
TX_IDLE, TX_RX, TX_VAL, TX_OK, TX_REJ, TX_ABORT, TX_TIMEOUT = range(7)
TX_NOMBRE = {TX_IDLE: "sin transaccion", TX_RX: "recibiendo", TX_VAL: "validando",
             TX_OK: "aplicada", TX_REJ: "rechazada", TX_ABORT: "abortada",
             TX_TIMEOUT: "tiempo agotado"}
RX_TIMEOUT_DECIMAS = 600           # 60 s con la transaccion abierta -> TIMEOUT
VAL_CHUNK = 128                    # palabras validadas por scan

# Codigos de error (LSTA[S_ERR] / S_RESULT)
E_OK, E_FORMAT, E_LEN, E_ID, E_CHK, E_RANGE, E_SAFETY, E_HW, E_SEM = 0, 1, 2, 3, 4, 5, 6, 7, 8
E_NOTX, E_TIMEOUT, E_CMD, E_IDMISMATCH = 9, 10, 11, 12
ERROR_TEXTO = {
    E_OK: "sin error",
    E_FORMAT: "formato distinto de 7 (el bloque no es del motor v7)",
    E_LEN: "longitud distinta de 640 palabras",
    E_ID: "identificador de configuracion fuera de rango",
    E_CHK: "checksum incorrecto (la escritura llego incompleta o alterada)",
    E_RANGE: "campo fuera de rango",
    E_SAFETY: "regla de seguridad (paro de emergencia o enclavamiento de plumas)",
    E_HW: "salida no cableada en este PLC",
    E_SEM: "configuracion incoherente",
    E_NOTX: "comando fuera de orden (no hay transaccion abierta)",
    E_TIMEOUT: "la transaccion quedo abierta mas de 60 s",
    E_CMD: "comando desconocido",
    E_IDMISMATCH: "el COMMIT no corresponde al bloque escrito",
}

# LSTA
S_VERSION, S_MAGIC, S_HEARTBEAT, S_SCANS = 0, 1, 2, 3
S_TX, S_ACK, S_RESULT, S_ERR, S_ERRFIELD = 4, 5, 6, 7, 8
S_ACTIVE_ID, S_ACTIVE_CHK, S_ENGINE, S_CFGREADY = 9, 10, 11, 12
S_MOTOR, S_DIR, S_DIR_TARGET, S_RUNLATCH, S_STOPREASON = 13, 14, 15, 16, 17
S_IN, S_OUT = 18, 19
S_COND, S_MEM, S_TIMERS, S_COUNTS = 20, 22, 24, 26     # 2 palabras c/u (bits 0..7, 8..15)
S_SEQ, S_SEQ_W = 28, 4                                 # estado, paso, tiempo, ciclos
S_TACC, S_CACC = 36, 52                                # 16 + 16
S_PLFAULT, S_BRAKE, S_HW_OUT, S_HW_IN, S_HW_LAMP = 68, 69, 70, 71, 72
S_RX, S_CFGLEN, S_STAGED_ID = 73, 74, 75

MOTOR_ESTADO = {0: "detenido", 1: "en marcha", 2: "frenando para invertir"}
SEQ_ESTADO = {0: "inactiva", 1: "activa", 2: "en pausa", 3: "terminada"}
STOP_REASON = {0: "", 1: "paro de emergencia I3", 2: "paro por software",
               3: "senal de paro del motor", 4: "pausa", 5: "configuracion no lista",
               6: "una secuencia pide paro"}

# ---------------------------------------------------------------------------
# MAPA DE LA CONFIGURACION  (CFG[0..639] = LSTG / LACT)
# ---------------------------------------------------------------------------
H_FORMAT, H_ID, H_LEN, H_CHK = 0, 1, 2, 3
H_MOVE, H_FREQ, H_DIR = 4, 5, 6
H_DEB_SENS, H_DEB_BTN, H_PLUMA_MAX, H_ESTOP_LAMPS = 7, 8, 9, 10
M_RUN, M_STOP, M_MODE, M_PAUSE, M_DIR1, M_DIR2, M_TOGGLE, M_REVTIME = 20, 21, 22, 23, 24, 25, 26, 27
O_OUT, N_OUT = 30, 12                       # fuente de Q1..Q12
O_COND, COND_W, N_COND = 50, 6, 16          # Op, In1..In4, Flanco
O_MEM, MEM_W, N_MEM = 146, 3, 16            # Set, Reset, Prioridad
O_TIMER, TIMER_W, N_TIMER = 194, 5, 16      # Tipo, In, Reset, Preset, Preset2 (0.1 s)
O_COUNT, COUNT_W, N_COUNT = 274, 5, 16      # Sube, Baja, Reset, Preset, Modo
O_SEQ, SEQ_W, N_SEQ = 354, 140, 2
SEQ_HDR, STEP_W, N_STEPS = 12, 8, 16
SQ_START, SQ_STOP, SQ_RESET, SQ_PAUSE, SQ_MODE, SQ_REPEAT, SQ_NSTEPS, SQ_EXCL, SQ_PAUSEOFF = range(9)
ST_OUT, ST_MOTOR, ST_TMODE, ST_TSIG, ST_TIME, ST_NEXT, ST_ALTSIG, ST_ALTNEXT = range(8)
NEXT_FIN = 17
N_IN = 12

DECIMAS_MAX = 32767
FREQ_MIN, FREQ_MAX = 1, 327
REV_TIME_DEFECTO, REV_TIME_MIN = 20, 10
DEB_MAX = 50

assert O_SEQ + N_SEQ * SEQ_W <= CFG_LEN
assert SEQ_HDR + N_STEPS * STEP_W == SEQ_W

# ---------------------------------------------------------------------------
# SENALES  (codigo; +1000 = NOT)
# ---------------------------------------------------------------------------
NOT_OFFSET = 1000
SIG_I, SIG_Q, SIG_TRUE = 1, 13, 25
SIG_SYS = {"BANDA_CORRIENDO": 26, "BANDA_DETENIDA": 27, "DIR_1": 28, "DIR_2": 29,
           "INVIRTIENDO": 30, "VFD_CERO": 31, "CFG_LISTA": 32, "PRIMER_CICLO": 33,
           "APP1": 34, "APP2": 35, "APP3": 36, "APP4": 37, "PARO_SOFTWARE": 38}
SIG_COND, SIG_MEM, SIG_TIMER, SIG_COUNT = 40, 56, 72, 88
SIG_SEQ, SEQ_SIG_W = 104, 20               # +0 ACTIVA +1 PAUSADA +2 FIN +3 CICLO +4.. PASO1..16
SIG_MAX = SIG_SEQ + N_SEQ * SEQ_SIG_W - 1  # 143
SIG_TABLE = 160                            # LSIG[0..159]

SENALES = {"SIEMPRE": SIG_TRUE, **SIG_SYS}
for _i in range(N_IN):
    SENALES[f"I{_i + 1}"] = SIG_I + _i
for _i in range(N_OUT):
    SENALES[f"Q{_i + 1}"] = SIG_Q + _i
for _i in range(N_COND):
    SENALES[f"C{_i + 1}"] = SIG_COND + _i
for _i in range(N_MEM):
    SENALES[f"M{_i + 1}"] = SIG_MEM + _i
for _i in range(N_TIMER):
    SENALES[f"T{_i + 1}"] = SIG_TIMER + _i
for _i in range(N_COUNT):
    SENALES[f"CT{_i + 1}"] = SIG_COUNT + _i
for _s in range(N_SEQ):
    _b = SIG_SEQ + _s * SEQ_SIG_W
    SENALES[f"SQ{_s + 1}_ACTIVA"] = _b
    SENALES[f"SQ{_s + 1}_PAUSADA"] = _b + 1
    SENALES[f"SQ{_s + 1}_FIN"] = _b + 2
    SENALES[f"SQ{_s + 1}_CICLO"] = _b + 3
    for _p in range(N_STEPS):
        SENALES[f"SQ{_s + 1}_PASO{_p + 1}"] = _b + 4 + _p
CODIGO_A_SENAL = {v: k for k, v in SENALES.items()}
assert max(SENALES.values()) == SIG_MAX < SIG_TABLE

# ---------------------------------------------------------------------------
# RECURSOS FISICOS DE LA BANDA  (espejo de §L0 del ST; NO se configuran por Modbus)
# ---------------------------------------------------------------------------
HW_BANDA = {
    "id": "banda_xl4",
    "entradas": {
        "I1": {"tipo": "NA", "desc": "boton verde"},
        "I2": {"tipo": "NC", "desc": "boton rojo / auxiliar"},
        "I3": {"tipo": "NC", "desc": "paro de emergencia", "emergencia": True},
        "I4": {"tipo": "sensor (activo en bajo)", "desc": "sensor S1", "alias": "S1", "sensor": True},
        "I5": {"tipo": "sensor (activo en bajo)", "desc": "sensor S2", "alias": "S2", "sensor": True},
    },
    "salidas": {
        "Q3": {"desc": "torreta verde", "alias": "verde", "lampara": True},
        "Q4": {"desc": "torreta amarilla", "alias": "amarilla", "lampara": True},
        "Q5": {"desc": "torreta roja", "alias": "roja", "lampara": True},
        "Q6": {"desc": "pluma 2 sube", "alias": "pluma2_sube"},
        "Q7": {"desc": "pluma 2 baja", "alias": "pluma2_baja"},
        "Q8": {"desc": "pluma 1 sube", "alias": "pluma1_sube"},
        "Q9": {"desc": "pluma 1 baja", "alias": "pluma1_baja"},
    },
    # pares que NUNCA pueden encenderse juntos (enclavamiento fijo en el ST)
    "pares_excluyentes": [("Q8", "Q9", "pluma 1"), ("Q6", "Q7", "pluma 2")],
    "motor": True,
}
EMERGENCIA = "I3"


def _mascara(nombres, prefijo):
    m = 0
    for n in nombres:
        m |= 1 << (int(n[len(prefijo):]) - 1)
    return m


def hw_out_mask(hw=HW_BANDA) -> int:
    return _mascara(hw["salidas"], "Q")


def hw_in_mask(hw=HW_BANDA) -> int:
    return _mascara(hw["entradas"], "I")


def hw_lamp_mask(hw=HW_BANDA) -> int:
    return _mascara([q for q, d in hw["salidas"].items() if d.get("lampara")], "Q")


def hw_sens_mask(hw=HW_BANDA) -> int:
    return _mascara([i for i, d in hw["entradas"].items() if d.get("sensor")], "I")


ALIAS_SALIDA = {d["alias"]: q for q, d in HW_BANDA["salidas"].items() if d.get("alias")}
ALIAS_SALIDA.update({"luz_verde": "Q3", "luz_amarilla": "Q4", "luz_roja": "Q5",
                     "subir_pluma1": "Q8", "bajar_pluma1": "Q9",
                     "subir_pluma2": "Q6", "bajar_pluma2": "Q7"})

ALIAS_SENAL = {
    "S1": "I4", "S2": "I5", "SENSOR1": "I4", "SENSOR_1": "I4", "SENSOR2": "I5", "SENSOR_2": "I5",
    "BOTON_VERDE": "I1", "BOTON_ARRANQUE": "I1", "BTN_START": "I1",
    "BOTON_ROJO": "I2", "BOTON_AUX": "I2", "BTN_AUX": "I2",
    "PARO_EMERGENCIA": "I3", "EMERGENCIA": "I3",
    "CORRIENDO": "BANDA_CORRIENDO", "EN_MARCHA": "BANDA_CORRIENDO",
    "DETENIDA": "BANDA_DETENIDA", "PARADA": "BANDA_DETENIDA",
    "DERECHA": "DIR_1", "IZQUIERDA": "DIR_2", "TRUE": "SIEMPRE", "ON": "SIEMPRE",
    "VERDE": "Q3", "AMARILLA": "Q4", "ROJA": "Q5",
}

DESCRIPCION_SENAL = {
    "I1": "boton I1 (verde, NA)", "I2": "boton I2 (rojo, NC)", "I3": "paro de emergencia I3",
    "I4": "sensor S1", "I5": "sensor S2", "SIEMPRE": "siempre",
    "BANDA_CORRIENDO": "banda corriendo", "BANDA_DETENIDA": "banda detenida",
    "DIR_1": "banda en direccion 1", "DIR_2": "banda en direccion 2",
    "INVIRTIENDO": "banda invirtiendo", "VFD_CERO": "variador en velocidad cero",
    "CFG_LISTA": "configuracion lista", "PRIMER_CICLO": "primer ciclo tras cargar",
    "PARO_SOFTWARE": "paro por software",
}
for _q, _d in HW_BANDA["salidas"].items():
    DESCRIPCION_SENAL[_q] = f"salida {_q} ({_d['desc']}) encendida"

# Codigos de cada campo
COND_OPS = {"and": 1, "y": 1, "or": 2, "o": 2, "xor": 3, "nand": 4, "nor": 5}
COND_OP_NOMBRE = {1: "AND", 2: "OR", 3: "XOR", 4: "NAND", 5: "NOR"}
COND_EDGES = {"nivel": 0, "subida": 1, "flanco": 1, "flanco_subida": 1, "bajada": 2, "flanco_bajada": 2}
TIMER_TIPOS = {"ton": 1, "on_delay": 1, "tof": 2, "off_delay": 2, "pulso": 3, "tp": 3,
               "retardo": 4, "delay": 4, "espera": 4, "tonr": 5, "retentivo": 5, "acumulado": 5,
               "intermitente": 6, "parpadeo": 6, "blink": 6, "oscilador": 6}
TIMER_NOMBRE = {1: "TON", 2: "TOF", 3: "PULSO", 4: "RETARDO", 5: "TONR", 6: "INTERMITENTE"}
COUNT_MODOS = {"una_vez": 0, "unico": 0, "ciclico": 1, "rearme": 1, "cada": 1, "nivel": 2}
COUNT_NOMBRE = {0: "una_vez", 1: "ciclico", 2: "nivel"}
MEM_PRIO = {"reset": 0, "set": 1}
SEQ_MODOS = {"una_vez": 0, "once": 0, "infinita": 1, "ciclica": 1, "ciclico": 1, "loop": 1,
             "repetir": 2, "n_veces": 2, "veces": 2}
SEQ_MODO_NOMBRE = {0: "una_vez", 1: "infinita", 2: "repetir"}
TRANS_MODOS = {"tiempo": 0, "senal": 1, "nivel": 1, "tiempo_y_senal": 2, "senal_o_tiempo": 3, "flanco": 4}
TRANS_NOMBRE = {0: "tiempo", 1: "senal", 2: "tiempo_y_senal", 3: "senal_o_tiempo", 4: "flanco"}
MOTOR_MODOS = {"enclavado": 0, "mientras": 1}
ACCIONES_MOTOR = ("marcha", "paro", "dir_1", "dir_2", "invertir")
ALIAS_ACCION_MOTOR = {"arrancar": "marcha", "avanzar": "marcha", "motor": "marcha", "mover": "marcha",
                      "detener": "paro", "parar": "paro", "derecha": "dir_1", "izquierda": "dir_2",
                      "cambiar_sentido": "invertir", "cambiar_direccion": "invertir"}


def _norm(s) -> str:
    t = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[\s\-]+", "_", t.strip().lower())


def _lista(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _vacio(v) -> bool:
    return v is None or v == "" or v == 0


def es_motor_v7(cfg_o_band) -> bool:
    """True si el programa usa el motor LAVO v7 (band.engine.version == 7)."""
    if not isinstance(cfg_o_band, dict):
        return False
    band = cfg_o_band.get("band", cfg_o_band)
    eng = band.get("engine") if isinstance(band, dict) else None
    return isinstance(eng, dict) and str(eng.get("version")) == "7"


# ---------------------------------------------------------------------------
# SENAL / SALIDA SIMBOLICA <-> CODIGO
# ---------------------------------------------------------------------------
def nombre_canonico(nombre) -> str:
    """'s1' -> 'I4', 'paso 3' -> 'SQ1_PASO3', 'verde' -> 'Q3' (sin NOT)."""
    t = str(nombre).strip().upper().replace("-", "_").replace(".", "_")
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"^%", "", t)
    # la voz transcribe "I2" como "12", "I 2" o "boton 12"
    m = re.match(r"^(?:BOTON_|BTN_|ENTRADA_)?1([1-5])$", t)
    if m:
        t = f"I{m.group(1)}"
    m = re.match(r"^(?:BOTON_|BTN_|ENTRADA_)?I_?0*(\d+)$", t)
    if m:
        t = f"I{int(m.group(1))}"
    t = ALIAS_SENAL.get(t, t)
    m = re.match(r"^(C|M|T|CT|Q|I)_?0*(\d+)(_DN|_Q)?$", t)
    if m:
        t = f"{m.group(1)}{int(m.group(2))}"
    m = re.match(r"^(?:SQ_?(\d)_?)?(?:PASO|P|STEP)_?(\d+)$", t)
    if m:
        t = f"SQ{m.group(1) or 1}_PASO{int(m.group(2))}"
    m = re.match(r"^SQ_?(\d)_?(ACTIVA|PAUSADA|FIN|CICLO)$", t)
    if m:
        t = f"SQ{m.group(1)}_{m.group(2)}"
    return t


def codigo_senal(nombre):
    """'S1' -> 4, 'NOT S1' -> 1004, None -> 0. Lanza ValueError."""
    if nombre is None or nombre == "" or nombre == 0:
        return 0
    if isinstance(nombre, bool):
        raise ValueError(f"senal '{nombre}' no valida")
    if isinstance(nombre, int):
        if 1 <= nombre <= SIG_MAX or NOT_OFFSET + 1 <= nombre <= NOT_OFFSET + SIG_MAX:
            return nombre
        raise ValueError(f"codigo de senal {nombre} fuera de rango")
    t = str(nombre).strip()
    neg = False
    m = re.match(r"^(NOT\s+|NO\s+|!|~|/)\s*(.+)$", t, re.I)
    if m:
        neg, t = True, m.group(2).strip()
    t = nombre_canonico(t)
    if t not in SENALES:
        raise ValueError(f"senal '{nombre}' no existe")
    return SENALES[t] + (NOT_OFFSET if neg else 0)


def nombre_senal(codigo) -> str:
    codigo = int(codigo or 0)
    if codigo == 0:
        return ""
    neg = codigo >= NOT_OFFSET
    base = codigo - NOT_OFFSET if neg else codigo
    n = CODIGO_A_SENAL.get(base, f"#{base}")
    return f"NOT {n}" if neg else n


def texto_senal(nombre) -> str:
    try:
        n = nombre_senal(codigo_senal(nombre))
    except ValueError:
        return str(nombre)
    neg = n.startswith("NOT ")
    base = n[4:] if neg else n
    txt = DESCRIPCION_SENAL.get(base, base)
    return f"NO {txt}" if neg else txt


def salida_canonica(nombre) -> str:
    """'verde' / 'q3' / 'Q03' -> 'Q3'. No valida que exista en el PLC."""
    k = _norm(nombre)
    if k in ALIAS_SALIDA:
        return ALIAS_SALIDA[k]
    m = re.match(r"^%?q_?0*(\d+)$", k)
    if m:
        return f"Q{int(m.group(1))}"
    return str(nombre).strip().upper()


def _decimas(segundos, etiqueta, errores, minimo=0):
    if segundos is None:
        return None
    try:
        v = float(segundos)
    except (TypeError, ValueError):
        errores.append(f"{etiqueta}: '{segundos}' no es un numero de segundos.")
        return None
    d = int(round(v * 10))
    if d < minimo or d > DECIMAS_MAX:
        errores.append(f"{etiqueta}: {v:g} s fuera de rango [{minimo / 10:g}, {DECIMAS_MAX / 10:g}] s.")
        return None
    return d


def _id_num(ident, prefijo, maximo):
    m = re.match(rf"^{prefijo}_?0*(\d+)$", str(ident or "").strip().upper())
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= maximo else None


def _destino(v, n_pasos):
    """siguiente / ir_a: None|0 -> 0, 'fin' -> 17, N -> N."""
    if v in (None, "", 0):
        return 0
    if _norm(v) in ("fin", "end", "terminar", "termina"):
        return NEXT_FIN
    return int(v)


# ---------------------------------------------------------------------------
# NORMALIZACION  (acepta el JSON del LLM y el del v6)
# ---------------------------------------------------------------------------
def desde_v6(eng6: dict) -> dict:
    """Convierte band.engine del v6 (banda_motor.py) al formato v7.
    Mismo significado; solo cambian nombres de salidas y la secuencia."""
    e = copy.deepcopy(eng6 or {})
    out = {"version": 7}
    for k in ("motor", "condiciones", "timers", "contadores", "memorias", "opciones"):
        if e.get(k) not in (None, [], {}):
            out[k] = e[k]
    if e.get("salidas"):
        out["salidas"] = {salida_canonica(k): v for k, v in e["salidas"].items() if v}
    sq = e.get("secuencia")
    if isinstance(sq, dict) and sq.get("pasos"):
        pasos = []
        for p in sq["pasos"]:
            acc = [_norm(a) for a in _lista(p.get("acciones"))]
            mot = [ALIAS_ACCION_MOTOR.get(a, a) for a in acc
                   if ALIAS_ACCION_MOTOR.get(a, a) in ACCIONES_MOTOR]
            sal = [salida_canonica(a) for a in acc if ALIAS_ACCION_MOTOR.get(a, a) not in ACCIONES_MOTOR]
            pasos.append({"salidas": sal, "motor": mot, "avanza": p.get("avanza"),
                          "senal": _v6_paso(p.get("senal")), "segundos": p.get("segundos"),
                          "siguiente": p.get("siguiente")})
        modo = "infinita" if _norm(sq.get("modo")) in ("ciclica", "ciclico", "loop") else "una_vez"
        out["secuencias"] = [{"id": "SQ1", "inicio": _v6_paso(sq.get("inicio")),
                              "paro": _v6_paso(sq.get("paro")), "reset": _v6_paso(sq.get("reset")),
                              "modo": modo, "exclusiva": sq.get("exclusiva", True) is not False,
                              "pausa_apaga": True, "pasos": pasos}]
    # referencias PASOn del v6 -> SQ1_PASOn
    return _renombrar_pasos(out)


def _v6_paso(s):
    if not isinstance(s, str):
        return s
    return re.sub(r"\bPASO\s*(\d+)\b", r"SQ1_PASO\1", s, flags=re.I)


def _renombrar_pasos(obj):
    if isinstance(obj, dict):
        return {k: _renombrar_pasos(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_renombrar_pasos(v) for v in obj]
    return _v6_paso(obj)


def normalizar_motor(eng: dict) -> dict:
    """Forma CANONICA del bloque v7 (ids, salidas, nombres). Pura y
    determinista: dos JSON con el mismo significado dan el mismo resultado,
    sin importar el orden de las listas ni los alias usados."""
    e = copy.deepcopy(eng or {})
    if str(e.get("version")) != "7":
        e = desde_v6(e)
    out = {"version": 7}
    m = {k: v for k, v in (e.get("motor") or {}).items() if v not in (None, "")}
    if m:
        out["motor"] = m
    for clave in ("condiciones", "memorias", "timers", "contadores"):
        pref = {"condiciones": "C", "memorias": "M", "timers": "T", "contadores": "CT"}[clave]
        bloques = [dict(b) for b in _lista(e.get(clave)) if isinstance(b, dict)]
        for b in bloques:
            n = _id_num(b.get("id"), pref, 99)
            if n:
                b["id"] = f"{pref}{n}"
        bloques.sort(key=lambda b: (_id_num(b.get("id"), pref, 99) or 999))
        if bloques:
            out[clave] = bloques
    sal = {}
    for k, v in (e.get("salidas") or {}).items():
        if v not in (None, ""):
            sal[salida_canonica(k)] = v
    if sal:
        out["salidas"] = dict(sorted(sal.items(), key=lambda kv: int(kv[0][1:]) if kv[0][1:].isdigit() else 99))
    seqs = []
    for i, s in enumerate(_lista(e.get("secuencias"))):
        if not isinstance(s, dict):
            continue
        s = dict(s)
        s["id"] = f"SQ{_id_num(s.get('id'), 'SQ', 9) or i + 1}"
        pasos = []
        for p in _lista(s.get("pasos")):
            if not isinstance(p, dict):
                pasos.append(p)
                continue
            p = dict(p)
            p["salidas"] = sorted({salida_canonica(x) for x in _lista(p.get("salidas")) if x},
                                  key=lambda q: int(q[1:]) if q[1:].isdigit() else 99)
            # Las acciones desconocidas se buscan en la lista ORIGINAL (antes de
            # normalizarla): asi validar_motor rechaza "volar" en vez de
            # descartarla sin decir nada. Los vacios (None, "") no son acciones.
            originales = [x for x in _lista(p.get("motor")) if x not in (None, "")]
            extra = [x for x in originales
                     if ALIAS_ACCION_MOTOR.get(_norm(x), _norm(x)) not in ACCIONES_MOTOR]
            p["motor"] = [a for a in ACCIONES_MOTOR
                          if a in {ALIAS_ACCION_MOTOR.get(_norm(x), _norm(x)) for x in originales}]
            if extra:
                p["_motor_desconocido"] = extra
            pasos.append(p)
        s["pasos"] = pasos
        seqs.append(s)
    seqs.sort(key=lambda s: s["id"])
    if seqs:
        out["secuencias"] = seqs
    op = {k: v for k, v in (e.get("opciones") or {}).items() if v not in (None, [], "")}
    if op.get("luces_paro"):
        op["luces_paro"] = sorted({salida_canonica(x) for x in _lista(op["luces_paro"])},
                                  key=lambda q: int(q[1:]) if q[1:].isdigit() else 99)
    if op:
        out["opciones"] = op
    return out


# ---------------------------------------------------------------------------
# VALIDACION  (errores bloquean; avisos se muestran)
# ---------------------------------------------------------------------------
def _referencias(eng) -> list:
    refs = []
    for k, v in (eng.get("motor") or {}).items():
        if k in ("arranque", "paro", "pausa", "dir_1", "dir_2", "invertir") and v not in (None, ""):
            refs.append((f"motor.{k}", v))
    for c in _lista(eng.get("condiciones")):
        for s in _lista((c or {}).get("entradas")):
            if s not in (None, ""):
                refs.append((f"condicion {c.get('id')}", s))
    for x in _lista(eng.get("memorias")):
        for k in ("set", "reset"):
            if (x or {}).get(k) not in (None, ""):
                refs.append((f"memoria {x.get('id')}.{k}", x[k]))
    for t in _lista(eng.get("timers")):
        for k in ("entrada", "reset"):
            if (t or {}).get(k) not in (None, ""):
                refs.append((f"timer {t.get('id')}.{k}", t[k]))
    for c in _lista(eng.get("contadores")):
        for k in ("entrada", "descuenta", "reset"):
            if (c or {}).get(k) not in (None, ""):
                refs.append((f"contador {c.get('id')}.{k}", c[k]))
    for k, v in (eng.get("salidas") or {}).items():
        if v not in (None, ""):
            refs.append((f"salidas.{k}", v))
    for s in _lista(eng.get("secuencias")):
        if not isinstance(s, dict):
            continue
        for k in ("inicio", "paro", "reset", "pausa"):
            if s.get(k) not in (None, ""):
                refs.append((f"{s.get('id')}.{k}", s[k]))
        for i, p in enumerate(_lista(s.get("pasos"))):
            if not isinstance(p, dict):
                continue
            if p.get("senal") not in (None, ""):
                refs.append((f"{s.get('id')} paso {i + 1}.senal", p["senal"]))
            alt = p.get("alternativa") or {}
            if isinstance(alt, dict) and alt.get("senal") not in (None, ""):
                refs.append((f"{s.get('id')} paso {i + 1}.alternativa", alt["senal"]))
    return refs


def _ids_definidos(eng) -> set:
    ids = set()
    for clave, pref, maximo in (("condiciones", "C", N_COND), ("memorias", "M", N_MEM),
                                ("timers", "T", N_TIMER), ("contadores", "CT", N_COUNT)):
        for b in _lista(eng.get(clave)):
            n = _id_num((b or {}).get("id"), pref, maximo)
            if n:
                ids.add(f"{pref}{n}")
    for s in _lista(eng.get("secuencias")):
        if isinstance(s, dict):
            sid = s.get("id")
            ids |= {f"{sid}_ACTIVA", f"{sid}_PAUSADA", f"{sid}_FIN", f"{sid}_CICLO"}
            for i in range(len(_lista(s.get("pasos")))):
                ids.add(f"{sid}_PASO{i + 1}")
    return ids


def recursos_usados(eng) -> dict:
    """{'entradas': {...}, 'salidas': {...}} fisicos que usa el programa."""
    ent, sal = set(), set()
    for _tag, nombre in _referencias(eng):
        try:
            base = nombre_senal(codigo_senal(nombre)).replace("NOT ", "")
        except ValueError:
            continue
        if re.match(r"^I\d+$", base):
            ent.add(base)
    for q in (eng.get("salidas") or {}):
        sal.add(q)
    for s in _lista(eng.get("secuencias")):
        for p in _lista((s or {}).get("pasos")):
            for q in _lista((p or {}).get("salidas")):
                sal.add(q)
    return {"entradas": sorted(ent), "salidas": sorted(sal)}


def validar_motor(band, hw=HW_BANDA) -> tuple:
    """Valida band.engine v7. Devuelve (errores, avisos).

    Aplica las MISMAS reglas que el PLC (rangos, seguridad, hardware) mas las
    que el PLC no puede ver (referencias a bloques no definidos, secuencias
    que giran sin espera, recursos incompatibles, conflictos)."""
    errores, avisos = [], []
    if not isinstance(band, dict):
        return ["Falta el bloque 'band'."], []
    if not isinstance(band.get("engine"), dict):
        return ["band.engine no es un objeto."], []
    eng = normalizar_motor(band["engine"])
    marcha = band.get("enable") is not False
    salidas_hw = set(hw["salidas"])
    entradas_hw = set(hw["entradas"])

    # --- movimiento ----------------------------------------------------------
    if marcha:
        f = band.get("freq_hz")
        if f in (None, ""):
            errores.append(f"La banda se mueve: falta la frecuencia en Hz ({FREQ_MIN}..{FREQ_MAX}).")
        else:
            try:
                if not FREQ_MIN <= int(f) <= FREQ_MAX:
                    errores.append(f"band.freq_hz={f} fuera de [{FREQ_MIN}, {FREQ_MAX}] Hz.")
            except (TypeError, ValueError):
                errores.append(f"band.freq_hz='{f}' no es un numero.")
        if _norm(band.get("direction") or "derecha") not in ("derecha", "izquierda", "1", "2", "dir_1", "dir_2"):
            errores.append(f"band.direction='{band.get('direction')}' debe ser derecha o izquierda.")
    if not hw.get("motor") and marcha:
        errores.append("Este PLC no tiene motor/VFD.")

    # --- ids -------------------------------------------------------------------
    for clave, pref, maximo in (("condiciones", "C", N_COND), ("memorias", "M", N_MEM),
                                ("timers", "T", N_TIMER), ("contadores", "CT", N_COUNT)):
        bloques = band["engine"].get(clave)
        if bloques is not None and not isinstance(bloques, list):
            errores.append(f"'{clave}' debe ser una lista.")
            continue
        vistos = set()
        for b in _lista(eng.get(clave)):
            n = _id_num(b.get("id"), pref, maximo)
            if n is None:
                errores.append(f"{clave}: id '{b.get('id')}' invalido (usa {pref}1..{pref}{maximo}).")
            elif n in vistos:
                errores.append(f"{clave}: id {pref}{n} repetido.")
            else:
                vistos.add(n)

    for c in _lista(eng.get("condiciones")):
        tag = f"condicion {c.get('id')}"
        if _norm(c.get("op") or "and") not in COND_OPS:
            errores.append(f"{tag}: op '{c.get('op')}' debe ser AND, OR, XOR, NAND o NOR.")
        ent = [x for x in _lista(c.get("entradas")) if x not in (None, "")]
        if not 1 <= len(ent) <= 4:
            errores.append(f"{tag}: necesita entre 1 y 4 entradas (anida condiciones para mas).")
        if _norm(c.get("flanco") or "nivel") not in COND_EDGES:
            errores.append(f"{tag}: flanco '{c.get('flanco')}' debe ser nivel, subida o bajada.")

    for x in _lista(eng.get("memorias")):
        tag = f"memoria {x.get('id')}"
        if x.get("set") in (None, "") and x.get("reset") in (None, ""):
            errores.append(f"{tag}: necesita 'set' o 'reset'.")
        if _norm(x.get("prioridad") or "reset") not in MEM_PRIO:
            errores.append(f"{tag}: prioridad '{x.get('prioridad')}' debe ser reset o set.")
        if x.get("set") not in (None, "") and x.get("set") == x.get("reset"):
            errores.append(f"{tag}: la misma senal no puede activarla y desactivarla.")

    for t in _lista(eng.get("timers")):
        tag = f"timer {t.get('id')}"
        tipo = TIMER_TIPOS.get(_norm(t.get("tipo")))
        if tipo is None:
            errores.append(f"{tag}: tipo '{t.get('tipo')}' debe ser TON, TOF, PULSO, RETARDO, TONR o INTERMITENTE.")
        if t.get("entrada") in (None, ""):
            errores.append(f"{tag}: falta 'entrada' (la senal que lo activa).")
        if t.get("segundos") is None:
            errores.append(f"{tag}: falta 'segundos'.")
        else:
            d = _decimas(t.get("segundos"), f"{tag}.segundos", errores)
            if tipo in (3, 6) and d == 0:
                errores.append(f"{tag}: un {TIMER_NOMBRE[tipo]} necesita mas de 0 s.")
        if t.get("segundos_off") is not None:
            _decimas(t.get("segundos_off"), f"{tag}.segundos_off", errores)
            if tipo != 6:
                avisos.append(f"{tag}: 'segundos_off' solo aplica a INTERMITENTE; se ignora.")
        if t.get("entrada") not in (None, "") and t.get("entrada") == t.get("reset"):
            errores.append(f"{tag}: la entrada y el reset son la misma senal; nunca terminaria.")

    for c in _lista(eng.get("contadores")):
        tag = f"contador {c.get('id')}"
        if c.get("entrada") in (None, "") and c.get("descuenta") in (None, ""):
            errores.append(f"{tag}: falta 'entrada' (lo que cuenta).")
        try:
            p = int(c.get("preset"))
            if not 1 <= p <= 32767:
                errores.append(f"{tag}: preset {p} fuera de [1, 32767].")
        except (TypeError, ValueError):
            errores.append(f"{tag}: preset '{c.get('preset')}' no es entero.")
        if _norm(c.get("modo") or "una_vez") not in COUNT_MODOS:
            errores.append(f"{tag}: modo '{c.get('modo')}' debe ser una_vez, ciclico o nivel.")
        if c.get("entrada") not in (None, "") and c.get("entrada") == c.get("reset"):
            errores.append(f"{tag}: cuenta y se reinicia con la misma senal; nunca llegaria.")

    # --- salidas -------------------------------------------------------------
    for q, v in (eng.get("salidas") or {}).items():
        if not re.match(r"^Q\d+$", q) or not 1 <= int(q[1:]) <= N_OUT:
            errores.append(f"salidas.{q}: no es una salida (usa Q1..Q12 o verde/amarilla/roja/pluma...).")
        elif q not in salidas_hw:
            errores.append(f"salidas.{q}: {q} no esta cableada en este PLC "
                           f"(disponibles: {', '.join(sorted(salidas_hw, key=lambda s: int(s[1:])))}).")
    for a, b, nombre in hw.get("pares_excluyentes", []):
        sal = eng.get("salidas") or {}
        if sal.get(a) and sal.get(a) == sal.get(b):
            errores.append(f"{nombre}: la misma senal no puede subirla y bajarla ({a}/{b}).")

    # --- secuencias ------------------------------------------------------------
    seqs = _lista(eng.get("secuencias"))
    if len(seqs) > N_SEQ:
        errores.append(f"Maximo {N_SEQ} secuencias (hay {len(seqs)}).")
    ids_seq = set()
    for s in seqs:
        if not isinstance(s, dict):
            errores.append("cada secuencia debe ser un objeto.")
            continue
        sid = s.get("id")
        if _id_num(sid, "SQ", N_SEQ) is None:
            errores.append(f"secuencia '{sid}': id invalido (SQ1 o SQ2).")
        if sid in ids_seq:
            errores.append(f"secuencia {sid} repetida.")
        ids_seq.add(sid)
        pasos = _lista(s.get("pasos"))
        if not 1 <= len(pasos) <= N_STEPS:
            errores.append(f"{sid}: necesita entre 1 y {N_STEPS} pasos.")
        if s.get("inicio") in (None, ""):
            errores.append(f"{sid}: falta 'inicio' (la senal que la arranca).")
        modo = SEQ_MODOS.get(_norm(s.get("modo") or "una_vez"))
        if modo is None:
            errores.append(f"{sid}.modo '{s.get('modo')}' debe ser una_vez, infinita o repetir.")
        if modo == 2:
            try:
                r = int(s.get("repeticiones"))
                if not 1 <= r <= 32767:
                    errores.append(f"{sid}: repeticiones {r} fuera de [1, 32767].")
            except (TypeError, ValueError):
                errores.append(f"{sid}: modo 'repetir' necesita 'repeticiones' (entero).")
        espera_total = 0
        for i, p in enumerate(pasos):
            tag = f"{sid} paso {i + 1}"
            if not isinstance(p, dict):
                errores.append(f"{tag}: debe ser un objeto.")
                continue
            for q in _lista(p.get("salidas")):
                if not re.match(r"^Q\d+$", str(q)) or not 1 <= int(str(q)[1:]) <= N_OUT:
                    errores.append(f"{tag}: '{q}' no es una salida.")
                elif q not in salidas_hw:
                    errores.append(f"{tag}: {q} no esta cableada en este PLC.")
            for a, b, nombre in hw.get("pares_excluyentes", []):
                if a in _lista(p.get("salidas")) and b in _lista(p.get("salidas")):
                    errores.append(f"{tag}: {nombre} no puede subir y bajar a la vez ({a} y {b}).")
            for x in p.get("_motor_desconocido", []):
                errores.append(f"{tag}: accion de motor '{x}' no existe (usa {', '.join(ACCIONES_MOTOR)}).")
            mot = _lista(p.get("motor"))
            if "marcha" in mot and "paro" in mot:
                errores.append(f"{tag}: 'marcha' y 'paro' a la vez.")
            if "dir_1" in mot and "dir_2" in mot:
                errores.append(f"{tag}: 'dir_1' y 'dir_2' a la vez.")
            if mot and not marcha:
                errores.append(f"{tag}: mueve la banda, pero el programa no tiene movimiento (banda.mover=false).")
            av = _norm(p.get("avanza") or ("flanco" if p.get("senal") else "tiempo"))
            if av not in TRANS_MODOS:
                errores.append(f"{tag}: avanza '{p.get('avanza')}' no existe ({', '.join(TRANS_NOMBRE.values())}).")
                continue
            tm = TRANS_MODOS[av]
            if tm != 0 and p.get("senal") in (None, ""):
                errores.append(f"{tag}: avanza por '{av}' pero falta 'senal'.")
            if tm in (0, 2) and p.get("segundos") is None:
                errores.append(f"{tag}: avanza por '{av}' pero falta 'segundos'.")
            d = _decimas(p.get("segundos") or 0, f"{tag}.segundos", errores)
            espera_total += (d or 0) if tm in (0, 2, 3) else 1
            for campo, valor in (("siguiente", p.get("siguiente")),
                                 ("alternativa.ir_a", (p.get("alternativa") or {}).get("ir_a")
                                  if isinstance(p.get("alternativa"), dict) else None)):
                try:
                    dst = _destino(valor, len(pasos))
                    if dst not in (0, NEXT_FIN) and not 1 <= dst <= len(pasos):
                        errores.append(f"{tag}: {campo}={valor} no existe (1..{len(pasos)} o 'fin').")
                except (TypeError, ValueError):
                    errores.append(f"{tag}: {campo} '{valor}' no es un paso.")
            alt = p.get("alternativa")
            if alt is not None:
                if not isinstance(alt, dict) or alt.get("senal") in (None, ""):
                    errores.append(f"{tag}: 'alternativa' necesita 'senal' e 'ir_a'.")
                elif alt.get("ir_a") in (None, ""):
                    errores.append(f"{tag}: la alternativa con {alt.get('senal')} necesita 'ir_a'.")
        if pasos and modo in (1, 2) and espera_total == 0:
            errores.append(f"{sid}: secuencia ciclica sin ninguna espera (giraria en cada scan).")
        if pasos and modo == 1 and s.get("paro") in (None, "") and s.get("reset") in (None, ""):
            avisos.append(f"{sid}: se repite indefinidamente; solo la detiene el paro I3 o el paro "
                          f"por software (no tiene 'paro').")

    excl = {}
    for s in seqs:
        if isinstance(s, dict) and s.get("exclusiva", True) is not False:
            for p in _lista(s.get("pasos")):
                for q in _lista((p or {}).get("salidas")):
                    excl.setdefault(q, set()).add(s.get("id"))
    for q, ss in excl.items():
        if len(ss) > 1:
            avisos.append(f"{q} lo manejan dos secuencias exclusivas ({', '.join(sorted(ss))}): "
                          f"si corren a la vez, la salida se enciende con cualquiera de las dos.")

    # --- motor ---------------------------------------------------------------
    motor = eng.get("motor") or {}
    if _norm(motor.get("modo") or "enclavado") not in MOTOR_MODOS:
        errores.append(f"motor.modo '{motor.get('modo')}' debe ser enclavado o mientras.")
    usa_motor = any(motor.get(k) not in (None, "") for k in ("arranque", "paro", "pausa", "dir_1", "dir_2", "invertir"))
    if usa_motor and not marcha:
        errores.append("El motor tiene senales de arranque/paro/direccion pero la banda no se mueve (banda.mover=false).")
    if motor.get("paro_inversion_s") is not None:
        _decimas(motor["paro_inversion_s"], "motor.paro_inversion_s", errores, minimo=REV_TIME_MIN)

    # --- seguridad: el paro de emergencia no se reasigna -----------------------
    emerg = {SENALES[EMERGENCIA], SENALES[EMERGENCIA] + NOT_OFFSET}
    for etiqueta, valor in [("motor.arranque", motor.get("arranque"))] + [
            (f"{s.get('id')}.inicio", s.get("inicio")) for s in seqs if isinstance(s, dict)]:
        try:
            if codigo_senal(valor) in emerg:
                errores.append(f"{etiqueta}: I3 es el paro de emergencia; no puede arrancar el motor "
                               f"ni iniciar secuencias (ni negado).")
        except ValueError:
            pass
    try:
        a, p_ = codigo_senal(motor.get("arranque")), codigo_senal(motor.get("paro"))
        if a and a == p_:
            errores.append("motor: la misma senal no puede arrancar y detener la banda.")
        if a == SENALES["I2"]:
            avisos.append("I2 es NC: si su cable se corta el PLC lo ve presionado y arrancaria la banda. "
                          "Para arrancar es mas seguro I1.")
    except ValueError:
        pass

    # --- opciones --------------------------------------------------------------
    op = eng.get("opciones") or {}
    for k in ("filtro_sensores_s", "filtro_botones_s"):
        if op.get(k) is not None:
            d = _decimas(op[k], f"opciones.{k}", errores)
            if d is not None and d > DEB_MAX:
                errores.append(f"opciones.{k}: maximo {DEB_MAX / 10:g} s.")
    if op.get("pluma_max_s") is not None:
        _decimas(op["pluma_max_s"], "opciones.pluma_max_s", errores)
    lamparas = {q for q, d in hw["salidas"].items() if d.get("lampara")}
    for q in _lista(op.get("luces_paro")):
        if q not in lamparas:
            errores.append(f"opciones.luces_paro: {q} no es una lampara; con el paro de emergencia "
                           f"solo pueden quedar encendidas lamparas ({', '.join(sorted(lamparas))}).")

    # --- referencias -----------------------------------------------------------
    definidos = _ids_definidos(eng)
    usados = set()
    for tag, nombre in _referencias(eng):
        try:
            cod = codigo_senal(nombre)
        except ValueError as e:
            errores.append(f"{tag}: {e}.")
            continue
        base = nombre_senal(cod).replace("NOT ", "")
        usados.add(base)
        if re.match(r"^(C|M|T|CT)\d+$", base) or base.startswith("SQ"):
            if base not in definidos:
                errores.append(f"{tag}: usa {base}, que no esta definido.")
        if re.match(r"^I\d+$", base) and base not in entradas_hw:
            errores.append(f"{tag}: la entrada {base} no existe en este PLC "
                           f"(disponibles: {', '.join(sorted(entradas_hw))}).")
        if re.match(r"^Q\d+$", base) and base not in salidas_hw:
            errores.append(f"{tag}: la salida {base} no existe en este PLC.")
    for ident in sorted(d for d in definidos if re.match(r"^(C|M|T|CT)\d+$", d)):
        if ident not in usados:
            avisos.append(f"{ident} esta definido pero nada lo usa.")

    ciclicos = {f"CT{_id_num(c.get('id'), 'CT', N_COUNT)}" for c in _lista(eng.get("contadores"))
                if COUNT_MODOS.get(_norm(c.get("modo"))) == 1}
    for q, v in (eng.get("salidas") or {}).items():
        try:
            base = nombre_senal(codigo_senal(v)).replace("NOT ", "")
        except ValueError:
            continue
        if base in ciclicos or base.endswith("_CICLO"):
            avisos.append(f"salidas.{q} usa {base}, un pulso de un scan: la salida no se veria. "
                          f"Usa un timer PULSO disparado por {base}.")

    if marcha and not usa_motor and not any("marcha" in _lista((p or {}).get("motor"))
                                            for s in seqs for p in _lista((s or {}).get("pasos"))):
        avisos.append("Nada arranca la banda: se usara I1 como boton de arranque.")
    return errores, avisos


# ---------------------------------------------------------------------------
# COMPILADOR  JSON simbolico -> CFG[0..639]  (determinista)
# ---------------------------------------------------------------------------
def _palabra(v) -> int:
    """Entero con signo de 16 bits (como lo ve el PLC)."""
    v = int(v) & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def checksum(cfg, desde=0) -> int:
    """Checksum del bloque (espejo de §L1 del ST). Excluye la palabra H_CHK."""
    s = 0
    for i in range(desde, CFG_LEN):
        if i == H_CHK:
            continue
        s = (s * 31 + _palabra(cfg[i]) + 32769) % 32749
    return s


def compilar_motor(band, hw=HW_BANDA) -> list:
    """band (con band.engine v7 valido) -> 640 enteros para %R1010..%R1649.
    Lanza ValueError si el programa no es valido."""
    errores, _ = validar_motor(band, hw)
    if errores:
        raise ValueError("Motor LAVO v7 invalido:\n  - " + "\n  - ".join(errores))
    eng = normalizar_motor(band["engine"])
    cfg = [0] * CFG_LEN
    marcha = band.get("enable") is not False
    cfg[H_FORMAT] = ENGINE_VERSION
    cfg[H_LEN] = CFG_LEN
    motor = eng.get("motor") or {}
    seqs = _lista(eng.get("secuencias"))
    if marcha:
        cfg[H_MOVE] = 1
        cfg[H_FREQ] = int(band["freq_hz"])
        cfg[H_DIR] = 2 if _norm(band.get("direction")) in ("izquierda", "2", "dir_2") else 1
        arranque = motor.get("arranque")
        marcha_en_pasos = any("marcha" in _lista(p.get("motor")) for s in seqs for p in _lista(s.get("pasos")))
        if arranque in (None, "") and not marcha_en_pasos and not any(
                motor.get(k) not in (None, "") for k in ("paro", "pausa", "dir_1", "dir_2", "invertir")):
            arranque = "I1"
        cfg[M_RUN] = codigo_senal(arranque)
        cfg[M_STOP] = codigo_senal(motor.get("paro"))
        cfg[M_MODE] = MOTOR_MODOS[_norm(motor.get("modo") or "enclavado")]
        cfg[M_PAUSE] = codigo_senal(motor.get("pausa"))
        cfg[M_DIR1] = codigo_senal(motor.get("dir_1"))
        cfg[M_DIR2] = codigo_senal(motor.get("dir_2"))
        cfg[M_TOGGLE] = codigo_senal(motor.get("invertir"))
        rev = motor.get("paro_inversion_s")
        cfg[M_REVTIME] = REV_TIME_DEFECTO if rev is None else int(round(float(rev) * 10))
    op = eng.get("opciones") or {}
    cfg[H_DEB_SENS] = int(round(float(op.get("filtro_sensores_s", 0.1) or 0) * 10))
    cfg[H_DEB_BTN] = int(round(float(op.get("filtro_botones_s", 0) or 0) * 10))
    cfg[H_PLUMA_MAX] = int(round(float(op.get("pluma_max_s", 0) or 0) * 10))
    cfg[H_ESTOP_LAMPS] = _mascara(_lista(op.get("luces_paro")), "Q")

    for q, v in (eng.get("salidas") or {}).items():
        cfg[O_OUT + int(q[1:]) - 1] = codigo_senal(v)

    for c in _lista(eng.get("condiciones")):
        b = O_COND + (_id_num(c["id"], "C", N_COND) - 1) * COND_W
        cfg[b] = COND_OPS[_norm(c.get("op") or "and")]
        for j, x in enumerate([x for x in _lista(c.get("entradas")) if x not in (None, "")][:4]):
            cfg[b + 1 + j] = codigo_senal(x)
        cfg[b + 5] = COND_EDGES[_norm(c.get("flanco") or "nivel")]

    for x in _lista(eng.get("memorias")):
        b = O_MEM + (_id_num(x["id"], "M", N_MEM) - 1) * MEM_W
        cfg[b] = codigo_senal(x.get("set"))
        cfg[b + 1] = codigo_senal(x.get("reset"))
        cfg[b + 2] = MEM_PRIO[_norm(x.get("prioridad") or "reset")]

    for t in _lista(eng.get("timers")):
        b = O_TIMER + (_id_num(t["id"], "T", N_TIMER) - 1) * TIMER_W
        tipo = TIMER_TIPOS[_norm(t["tipo"])]
        cfg[b] = tipo
        cfg[b + 1] = codigo_senal(t.get("entrada"))
        cfg[b + 2] = codigo_senal(t.get("reset"))
        cfg[b + 3] = int(round(float(t["segundos"]) * 10))
        if tipo == 6:
            off = t.get("segundos_off")
            cfg[b + 4] = cfg[b + 3] if off in (None, "") else int(round(float(off) * 10))

    for c in _lista(eng.get("contadores")):
        b = O_COUNT + (_id_num(c["id"], "CT", N_COUNT) - 1) * COUNT_W
        cfg[b] = codigo_senal(c.get("entrada"))
        cfg[b + 1] = codigo_senal(c.get("descuenta"))
        cfg[b + 2] = codigo_senal(c.get("reset"))
        cfg[b + 3] = int(c["preset"])
        cfg[b + 4] = COUNT_MODOS[_norm(c.get("modo") or "una_vez")]

    for s in seqs:
        k = _id_num(s["id"], "SQ", N_SEQ) - 1
        b = O_SEQ + k * SEQ_W
        pasos = _lista(s.get("pasos"))
        cfg[b + SQ_START] = codigo_senal(s.get("inicio"))
        cfg[b + SQ_STOP] = codigo_senal(s.get("paro"))
        cfg[b + SQ_RESET] = codigo_senal(s.get("reset"))
        cfg[b + SQ_PAUSE] = codigo_senal(s.get("pausa"))
        modo = SEQ_MODOS[_norm(s.get("modo") or "una_vez")]
        cfg[b + SQ_MODE] = modo
        cfg[b + SQ_REPEAT] = int(s.get("repeticiones") or 0) if modo == 2 else 0
        cfg[b + SQ_NSTEPS] = len(pasos)
        cfg[b + SQ_EXCL] = 0 if s.get("exclusiva") is False else 1
        cfg[b + SQ_PAUSEOFF] = 0 if s.get("pausa_apaga") is False else 1
        for i, p in enumerate(pasos):
            st = b + SEQ_HDR + i * STEP_W
            cfg[st + ST_OUT] = _mascara(_lista(p.get("salidas")), "Q")
            cfg[st + ST_MOTOR] = sum(1 << ACCIONES_MOTOR.index(a) for a in _lista(p.get("motor")))
            av = _norm(p.get("avanza") or ("flanco" if p.get("senal") else "tiempo"))
            cfg[st + ST_TMODE] = TRANS_MODOS[av]
            cfg[st + ST_TSIG] = codigo_senal(p.get("senal"))
            cfg[st + ST_TIME] = int(round(float(p.get("segundos") or 0) * 10))
            cfg[st + ST_NEXT] = _destino(p.get("siguiente"), len(pasos))
            alt = p.get("alternativa") if isinstance(p.get("alternativa"), dict) else {}
            cfg[st + ST_ALTSIG] = codigo_senal(alt.get("senal"))
            cfg[st + ST_ALTNEXT] = _destino(alt.get("ir_a"), len(pasos)) if alt.get("senal") else 0

    cfg[H_ID] = 1 + checksum(cfg, desde=H_MOVE) % 32766
    cfg[H_CHK] = checksum(cfg)
    err = error_plc(cfg, hw)
    if err[0] != E_OK:            # nunca deberia pasar: el validador es mas estricto
        raise ValueError(f"El bloque compilado no pasaria la validacion del PLC: "
                         f"{explicar_error(*err)}")
    return cfg


# ---------------------------------------------------------------------------
# VALIDACION DEL BLOQUE COMPILADO  (espejo EXACTO de §L1 del ST: mismo orden)
# ---------------------------------------------------------------------------
def _es_senal(ofs) -> bool:
    if ofs in (M_RUN, M_STOP, M_PAUSE, M_DIR1, M_DIR2, M_TOGGLE):
        return True
    if O_OUT <= ofs < O_OUT + N_OUT:
        return True
    if O_COND <= ofs < O_COND + N_COND * COND_W:
        return 1 <= (ofs - O_COND) % COND_W <= 4
    if O_MEM <= ofs < O_MEM + N_MEM * MEM_W:
        return (ofs - O_MEM) % MEM_W <= 1
    if O_TIMER <= ofs < O_TIMER + N_TIMER * TIMER_W:
        return (ofs - O_TIMER) % TIMER_W in (1, 2)
    if O_COUNT <= ofs < O_COUNT + N_COUNT * COUNT_W:
        return (ofs - O_COUNT) % COUNT_W <= 2
    if O_SEQ <= ofs < O_SEQ + N_SEQ * SEQ_W:
        r = (ofs - O_SEQ) % SEQ_W
        if r < SEQ_HDR:
            return r <= SQ_PAUSE
        return (r - SEQ_HDR) % STEP_W in (ST_TSIG, ST_ALTSIG)
    return False


def rango_campo(ofs):
    """(min, max) que el PLC exige a un campo que no es senal (None para la
    cabecera 0..3). El PLC solo vigila lo que puede romperlo: indices de
    pasos, frecuencia/direccion y valores negativos. La coherencia completa
    la revisa validar_motor() antes de enviar."""
    if ofs <= H_CHK:
        return None
    if ofs == H_FREQ:
        return (0, FREQ_MAX)
    if ofs == H_DIR:
        return (0, 2)
    if O_SEQ <= ofs < O_SEQ + N_SEQ * SEQ_W:
        r = (ofs - O_SEQ) % SEQ_W
        if r == SQ_NSTEPS:
            return (0, N_STEPS)
        if r >= SEQ_HDR and (r - SEQ_HDR) % STEP_W in (ST_NEXT, ST_ALTNEXT):
            return (0, NEXT_FIN)
    return (0, 32767)


def _bits(v, n=12):
    return [((v >> i) & 1) == 1 for i in range(n)]


def error_plc(cfg, hw=HW_BANDA, staged_id=None) -> tuple:
    """(codigo, campo) que reportaria el PLC al hacer COMMIT de 'cfg'.
    Campo = offset + 1 (0 = sin campo). Mismo orden de reglas que el ST."""
    cfg = [_palabra(v) for v in cfg]
    if len(cfg) != CFG_LEN:
        return E_LEN, H_LEN + 1
    rng = 0
    for ofs in range(CFG_LEN):
        v = cfg[ofs]
        if _es_senal(ofs):
            if not (0 <= v <= SIG_MAX or NOT_OFFSET + 1 <= v <= NOT_OFFSET + SIG_MAX):
                rng = ofs + 1
                break
        else:
            r = rango_campo(ofs)
            if r and not r[0] <= v <= r[1]:
                rng = ofs + 1
                break
    if cfg[H_FORMAT] != ENGINE_VERSION:
        return E_FORMAT, H_FORMAT + 1
    if cfg[H_LEN] != CFG_LEN:
        return E_LEN, H_LEN + 1
    if cfg[H_ID] < 1:
        return E_ID, H_ID + 1
    if staged_id is not None and cfg[H_ID] != staged_id:
        return E_IDMISMATCH, H_ID + 1
    if checksum(cfg) != cfg[H_CHK]:
        return E_CHK, H_CHK + 1
    if rng:
        return E_RANGE, rng
    hwq = _bits(hw_out_mask(hw))
    lamp = _bits(hw_lamp_mask(hw))
    emerg = (SENALES[EMERGENCIA], SENALES[EMERGENCIA] + NOT_OFFSET)
    pares = [(int(a[1:]) - 1, int(b[1:]) - 1) for a, b, _ in hw.get("pares_excluyentes", [])]
    if cfg[H_MOVE] == 1:
        if not FREQ_MIN <= cfg[H_FREQ] <= FREQ_MAX:
            return E_SEM, H_FREQ + 1
        if cfg[H_DIR] not in (1, 2):
            return E_SEM, H_DIR + 1
    if cfg[M_RUN] in emerg:
        return E_SAFETY, M_RUN + 1
    for j in range(N_OUT):
        if cfg[O_OUT + j] != 0 and not hwq[j]:
            return E_HW, O_OUT + j + 1
    for j, on in enumerate(_bits(cfg[H_ESTOP_LAMPS])):
        if on and not lamp[j]:
            return E_HW, H_ESTOP_LAMPS + 1
    for k in range(N_SEQ):
        b = O_SEQ + k * SEQ_W
        n = cfg[b + SQ_NSTEPS]
        if n > 0 and cfg[b + SQ_START] in emerg:
            return E_SAFETY, b + SQ_START + 1
        for i in range(n):
            st = b + SEQ_HDR + i * STEP_W
            for dst in (ST_NEXT, ST_ALTNEXT):
                if cfg[st + dst] > n and cfg[st + dst] != NEXT_FIN:
                    return E_SEM, st + dst + 1
            bits = _bits(cfg[st + ST_OUT])
            for j in range(N_OUT):
                if bits[j] and not hwq[j]:
                    return E_HW, st + ST_OUT + 1
            for a, c in pares:
                if bits[a] and bits[c]:
                    return E_SAFETY, st + ST_OUT + 1
    return E_OK, 0


def campo_de_offset(ofs: int) -> str:
    """Nombre legible de CFG[ofs] (para explicar el campo de error del PLC)."""
    cab = {H_FORMAT: "formato", H_ID: "id", H_LEN: "longitud", H_CHK: "checksum",
           H_MOVE: "banda.mover", H_FREQ: "banda.frecuencia_hz", H_DIR: "banda.direccion",
           H_DEB_SENS: "opciones.filtro_sensores_s", H_DEB_BTN: "opciones.filtro_botones_s",
           H_PLUMA_MAX: "opciones.pluma_max_s", H_ESTOP_LAMPS: "opciones.luces_paro",
           M_RUN: "motor.arranque", M_STOP: "motor.paro", M_MODE: "motor.modo",
           M_PAUSE: "motor.pausa", M_DIR1: "motor.dir_1", M_DIR2: "motor.dir_2",
           M_TOGGLE: "motor.invertir", M_REVTIME: "motor.paro_inversion_s"}
    if ofs in cab:
        return cab[ofs]
    if O_OUT <= ofs < O_OUT + N_OUT:
        return f"salidas.Q{ofs - O_OUT + 1}"
    for base, w, n, pref, campos in (
            (O_COND, COND_W, N_COND, "C", ("op", "entrada1", "entrada2", "entrada3", "entrada4", "flanco")),
            (O_MEM, MEM_W, N_MEM, "M", ("set", "reset", "prioridad")),
            (O_TIMER, TIMER_W, N_TIMER, "T", ("tipo", "entrada", "reset", "segundos", "segundos_off")),
            (O_COUNT, COUNT_W, N_COUNT, "CT", ("entrada", "descuenta", "reset", "preset", "modo"))):
        if base <= ofs < base + n * w:
            return f"{pref}{(ofs - base) // w + 1}.{campos[(ofs - base) % w]}"
    if O_SEQ <= ofs < O_SEQ + N_SEQ * SEQ_W:
        k, r = divmod(ofs - O_SEQ, SEQ_W)
        if r < SEQ_HDR:
            nombres = ("inicio", "paro", "reset", "pausa", "modo", "repeticiones", "pasos",
                       "exclusiva", "pausa_apaga")
            return f"SQ{k + 1}." + (nombres[r] if r < len(nombres) else f"reservado{r}")
        i, q = divmod(r - SEQ_HDR, STEP_W)
        return f"SQ{k + 1}.paso{i + 1}." + ("salidas", "motor", "avanza", "senal", "segundos",
                                            "siguiente", "alternativa.senal", "alternativa.ir_a")[q]
    return f"reservado[{ofs}]"


def explicar_error(codigo, campo=0) -> str:
    txt = ERROR_TEXTO.get(codigo, f"error {codigo}")
    if campo:
        txt += f" en {campo_de_offset(campo - 1)} (palabra {campo - 1}, %R{STAGE_BASE + campo - 1})"
    return txt


# ---------------------------------------------------------------------------
# DESCRIPCION LEGIBLE
# ---------------------------------------------------------------------------
def _txt_salida(q):
    d = HW_BANDA["salidas"].get(q)
    return f"{q} ({d['desc']})" if d else q


def describir_motor(band) -> list:
    eng = normalizar_motor((band or {}).get("engine") or {})
    L = []
    if band.get("enable") is not False:
        d = "izquierda (dir 2)" if _norm(band.get("direction")) in ("izquierda", "2") else "derecha (dir 1)"
        L.append(f"Banda (motor LAVO v7): arranca hacia la {d}"
                 + (f" a {band.get('freq_hz')} Hz." if band.get("freq_hz") else "."))
    else:
        L.append("Motor LAVO v7 sin movimiento de banda (torreta, plumas y logica).")
    m = eng.get("motor") or {}
    if m.get("arranque"):
        modo = "mientras este activo" if _norm(m.get("modo")) == "mientras" else "y se queda en marcha (enclavado)"
        L.append(f"Motor: arranca con {texto_senal(m['arranque'])} {modo}.")
    if m.get("paro"):
        L.append(f"Motor: se detiene con {texto_senal(m['paro'])}.")
    if m.get("pausa"):
        L.append(f"Motor: pausa mientras {texto_senal(m['pausa'])} y reanuda sola.")
    for k, txt in (("dir_1", "direccion 1"), ("dir_2", "direccion 2")):
        if m.get(k):
            L.append(f"Direccion: pasa a {txt} con {texto_senal(m[k])}.")
    if m.get("invertir"):
        L.append(f"Direccion: invierte con {texto_senal(m['invertir'])} (frena "
                 f"{m.get('paro_inversion_s') or REV_TIME_DEFECTO / 10:g} s y espera velocidad cero).")
    for c in _lista(eng.get("condiciones")):
        op = COND_OPS.get(_norm(c.get("op") or "and"), 1)
        sep = {1: " Y ", 2: " O ", 3: " XOR ", 4: " Y ", 5: " O "}[op]
        pre = "NO (" if op in (4, 5) else ""
        post = ")" if op in (4, 5) else ""
        fl = {1: " (al activarse)", 2: " (al desactivarse)"}.get(COND_EDGES.get(_norm(c.get("flanco") or "nivel")), "")
        L.append(f"{c.get('id')}: {pre}{sep.join(texto_senal(x) for x in _lista(c.get('entradas')))}{post}{fl}.")
    for x in _lista(eng.get("memorias")):
        L.append(f"{x.get('id')} (memoria): se activa con {texto_senal(x.get('set')) or '-'} y se "
                 f"desactiva con {texto_senal(x.get('reset')) or '-'}"
                 f"{' (gana el set)' if _norm(x.get('prioridad')) == 'set' else ''}.")
    for t in _lista(eng.get("timers")):
        tipo = TIMER_TIPOS.get(_norm(t.get("tipo")))
        txt = {1: "se activa si {e} sigue activo {s} s",
               2: "sigue activo {s} s despues de que {e} se apaga",
               3: "pulso de {s} s cada vez que se activa {e}",
               4: "se activa {s} s despues de que se activa {e} y se queda",
               5: "acumula el tiempo con {e} activo y se activa a los {s} s (no se borra al soltar)",
               6: "parpadea mientras {e}: {s} s encendido / {off} s apagado"}.get(tipo, "?")
        L.append(f"{t.get('id')} ({TIMER_NOMBRE.get(tipo, '?')}): "
                 + txt.format(e=texto_senal(t.get("entrada")), s=t.get("segundos"),
                              off=t.get("segundos_off") or t.get("segundos"))
                 + (f"; reset con {texto_senal(t['reset'])}" if t.get("reset") else "") + ".")
    for c in _lista(eng.get("contadores")):
        modo = COUNT_MODOS.get(_norm(c.get("modo") or "una_vez"), 0)
        L.append(f"{c.get('id')}: cuenta {texto_senal(c.get('entrada')) or '-'}"
                 + (f" y descuenta {texto_senal(c['descuenta'])}" if c.get("descuenta") else "")
                 + f" hasta {c.get('preset')}"
                 + {0: " y se queda activo", 1: " (pulso y vuelve a 0, cada N)", 2: " (activo mientras la cuenta >= preset)"}[modo]
                 + (f"; reset con {texto_senal(c['reset'])}" if c.get("reset") else "") + ".")
    for q, v in (eng.get("salidas") or {}).items():
        L.append(f"{_txt_salida(q)}: encendida con {texto_senal(v)}.")
    for s in _lista(eng.get("secuencias")):
        modo = SEQ_MODOS.get(_norm(s.get("modo") or "una_vez"), 0)
        L.append(f"{s['id']}: secuencia de {len(_lista(s.get('pasos')))} pasos; inicia con "
                 f"{texto_senal(s.get('inicio'))}"
                 + {0: ", corre una vez", 1: ", se repite sin fin",
                    2: f", se repite {s.get('repeticiones')} veces"}[modo]
                 + (f", se detiene con {texto_senal(s['paro'])}" if s.get("paro") else "")
                 + (f", reinicia con {texto_senal(s['reset'])}" if s.get("reset") else "")
                 + (f", pausa con {texto_senal(s['pausa'])}" if s.get("pausa") else "") + ".")
        for i, p in enumerate(_lista(s.get("pasos"))):
            acc = [_txt_salida(q) for q in _lista(p.get("salidas"))] + list(_lista(p.get("motor")))
            av = _norm(p.get("avanza") or ("flanco" if p.get("senal") else "tiempo"))
            cuando = {"tiempo": f"a los {p.get('segundos')} s",
                      "senal": f"cuando {texto_senal(p.get('senal'))} este activo",
                      "nivel": f"cuando {texto_senal(p.get('senal'))} este activo",
                      "flanco": f"cuando se active {texto_senal(p.get('senal'))}",
                      "tiempo_y_senal": f"a los {p.get('segundos')} s si {texto_senal(p.get('senal'))}",
                      "senal_o_tiempo": f"con {texto_senal(p.get('senal'))} o a los {p.get('segundos')} s"}.get(av, av)
            sig = p.get("siguiente")
            sig = "" if sig in (None, 0, "") else (" -> fin" if _norm(sig) in ("fin", "17") else f" -> paso {sig}")
            alt = p.get("alternativa") if isinstance(p.get("alternativa"), dict) else None
            alt_t = (f"; si {texto_senal(alt['senal'])} va al "
                     f"{'fin' if _norm(alt.get('ir_a')) == 'fin' else 'paso ' + str(alt.get('ir_a'))}") if alt else ""
            L.append(f"  Paso {i + 1}: {', '.join(acc) or 'nada'}; avanza {cuando}{sig}{alt_t}.")
    op = eng.get("opciones") or {}
    if op.get("pluma_max_s"):
        L.append(f"Plumas: se cortan tras {op['pluma_max_s']} s en movimiento.")
    if op.get("luces_paro"):
        L.append(f"Con el paro I3 quedan encendidas: {', '.join(_txt_salida(q) for q in op['luces_paro'])}.")
    L.append("Siempre: I3 detiene motor, secuencias y plumas; las memorias y timers se reinician.")
    return L


# ---------------------------------------------------------------------------
# ESTADO LEIDO DEL PLC  (LSTA -> dict para el panel)
# ---------------------------------------------------------------------------
def estado_desde_status(st: list) -> dict:
    if not st or len(st) < STATUS_LEN:
        return {}
    st = [_palabra(v) for v in st]
    if st[S_VERSION] != ENGINE_VERSION or st[S_MAGIC] != MAGIC:
        return {"version": st[S_VERSION], "disponible": False}

    def bits(m, n):
        return [bool((m >> i) & 1) for i in range(n)]

    def b16(lo, hi):
        return bits(lo, 8) + bits(hi, 8)

    out = bits(st[S_OUT], N_OUT)
    ent = bits(st[S_IN], N_IN)
    seqs = []
    for k in range(N_SEQ):
        b = S_SEQ + k * S_SEQ_W
        seqs.append({"estado": SEQ_ESTADO.get(st[b], str(st[b])), "paso": st[b + 1],
                     "paso_tiempo_s": st[b + 2] / 10, "ciclos": st[b + 3]})
    err = st[S_ERR]
    return {
        "version": ENGINE_VERSION, "disponible": True,
        "activo": st[S_ENGINE] == 1,
        "transaccion": TX_NOMBRE.get(st[S_TX], str(st[S_TX])),
        "tx_codigo": st[S_TX],
        "ack": st[S_ACK], "resultado": st[S_RESULT],
        "error_codigo": err, "error_campo": st[S_ERRFIELD],
        "error": explicar_error(err, st[S_ERRFIELD]) if err else "",
        "config_activa": st[S_ACTIVE_ID], "checksum_activo": st[S_ACTIVE_CHK],
        "cfg_lista": st[S_CFGREADY] == 1,
        "heartbeat": st[S_HEARTBEAT], "scans_por_s": st[S_SCANS],
        "ciclo_ms": round(1000 / st[S_SCANS], 2) if st[S_SCANS] > 0 else None,
        "motor": MOTOR_ESTADO.get(st[S_MOTOR], str(st[S_MOTOR])),
        "direccion": st[S_DIR], "direccion_objetivo": st[S_DIR_TARGET],
        "enclavado": st[S_RUNLATCH] == 1,
        "motivo_paro": STOP_REASON.get(st[S_STOPREASON], str(st[S_STOPREASON])),
        "entradas": {f"I{i + 1}": ent[i] for i in range(N_IN) if f"I{i + 1}" in HW_BANDA["entradas"]},
        "salidas": {f"Q{i + 1}": out[i] for i in range(N_OUT) if f"Q{i + 1}" in HW_BANDA["salidas"]},
        "condiciones": b16(st[S_COND], st[S_COND + 1]),
        "memorias": b16(st[S_MEM], st[S_MEM + 1]),
        "timers": b16(st[S_TIMERS], st[S_TIMERS + 1]),
        "contadores": b16(st[S_COUNTS], st[S_COUNTS + 1]),
        "timers_s": [x / 10 for x in st[S_TACC:S_TACC + N_TIMER]],
        "contadores_acc": list(st[S_CACC:S_CACC + N_COUNT]),
        "secuencias": seqs,
        "falla_plumas": st[S_PLFAULT],
        "hw": {"salidas": st[S_HW_OUT], "entradas": st[S_HW_IN], "lamparas": st[S_HW_LAMP]},
        "hw_coincide": (st[S_HW_OUT] == hw_out_mask() and st[S_HW_IN] == hw_in_mask()
                        and st[S_HW_LAMP] == hw_lamp_mask()),
        "longitud_cfg": st[S_CFGLEN],
    }


# ---------------------------------------------------------------------------
# COMPATIBILIDAD: interpretar, aproximar y pedir confirmacion (punto 1)
# ---------------------------------------------------------------------------
_RE_RECURSO = re.compile(r"(?<![A-Za-z0-9])%?([IQ])\s?0*(\d{1,2})(?![0-9])", re.I)


def recursos_mencionados(texto: str) -> dict:
    """Entradas/salidas fisicas que el usuario nombra EXPLICITAMENTE
    ('Q10', 'I 7', '%Q12'). No interpreta colores ni sensores."""
    ent, sal = set(), set()
    for m in _RE_RECURSO.finditer(str(texto or "")):
        n = int(m.group(2))
        if not 1 <= n <= 16:
            continue
        (ent if m.group(1).upper() == "I" else sal).add(f"{m.group(1).upper()}{n}")
    return {"entradas": sorted(ent), "salidas": sorted(sal)}


def analizar_compatibilidad(texto: str, band: dict, analisis_llm: dict = None, hw=HW_BANDA) -> dict:
    """Combina el analisis del LLM con comprobaciones DETERMINISTAS.

    Devuelve {"compatibilidad": total|parcial|ninguna, "conservado": [...],
    "modificado": [...], "simplificado": [...], "no_soportado": [...],
    "requiere_confirmacion": bool, "intencion": str}.

    Regla: si el programa cambia algo de lo pedido (otra salida, otra entrada,
    un tiempo distinto, una parte omitida), requiere confirmacion humana antes
    de cargarse al PLC. Nunca se relaja una regla de seguridad."""
    a = dict(analisis_llm or {})
    out = {k: [str(x) for x in _lista(a.get(k)) if str(x).strip()]
           for k in ("conservado", "modificado", "simplificado", "no_soportado")}
    out["intencion"] = str(a.get("intencion") or "").strip()
    eng = normalizar_motor(((band or {}).get("engine")) or {})
    usados = recursos_usados(eng)
    pedidos = recursos_mencionados(texto)
    sal_hw, ent_hw = set(hw["salidas"]), set(hw["entradas"])

    for q in pedidos["salidas"]:
        if q in sal_hw:
            continue
        nota = (f"{q} no existe en el PLC de la banda (salidas cableadas: "
                f"{', '.join(sorted(sal_hw, key=lambda s: int(s[1:])))}).")
        usadas_otras = [x for x in usados["salidas"] if x not in pedidos["salidas"]]
        if usadas_otras:
            if not any(q in x for x in out["modificado"]):
                out["modificado"].append(nota + f" Se uso {', '.join(usadas_otras)} en su lugar.")
        elif not any(q in x for x in out["no_soportado"]):
            out["no_soportado"].append(nota)
    for i in pedidos["entradas"]:
        if i in ent_hw:
            continue
        nota = (f"{i} no existe en el PLC de la banda (entradas: I1..I5; S1 = I4, S2 = I5).")
        if not any(i in x for x in out["modificado"] + out["no_soportado"]):
            out["no_soportado"].append(nota)

    vacio = not any(eng.get(k) for k in ("motor", "condiciones", "memorias", "timers",
                                         "contadores", "salidas", "secuencias")) \
        and (band or {}).get("enable") is False
    if vacio and (out["no_soportado"] or out["modificado"]):
        out["compatibilidad"] = "ninguna"
    elif out["modificado"] or out["simplificado"] or out["no_soportado"]:
        out["compatibilidad"] = "parcial"
    else:
        out["compatibilidad"] = "total"
    pide = bool(a.get("requiere_confirmacion"))
    out["requiere_confirmacion"] = (out["compatibilidad"] == "parcial") or (pide and not vacio)
    return out


# ---------------------------------------------------------------------------
# RESPUESTA DEL LLM -> engine_config
# ---------------------------------------------------------------------------
CLAVES_MOTOR = ("motor", "condiciones", "memorias", "timers", "contadores", "salidas",
                "secuencias", "opciones")


def cfg_desde_respuesta(resp) -> dict:
    """JSON del LLM (SYSTEM_PROMPT_BANDA_V7) -> engine_config de la banda."""
    if not isinstance(resp, dict):
        raise ValueError("La respuesta no es un objeto JSON.")
    b = resp.get("banda") or {}
    mover = bool(b.get("mover"))
    band = {"enable": mover}
    if mover:
        d = _norm(b.get("direccion"))
        band["direction"] = "izquierda" if d in ("izquierda", "izq", "2", "dir_2") else "derecha"
        f = b.get("frecuencia_hz")
        try:
            band["freq_hz"] = int(round(float(f))) if f not in (None, "") else None
        except (TypeError, ValueError):
            band["freq_hz"] = None
    band["stop_mode"] = 0
    eng = {"version": 7}
    for k in CLAVES_MOTOR:
        v = resp.get(k)
        if isinstance(v, dict):
            v = {s: x for s, x in v.items() if x not in (None, "")}
        if v not in (None, [], {}):
            eng[k] = v
    band["engine"] = normalizar_motor(eng)
    return {"name": str(resp.get("name") or "Programa banda")[:80], "device": "banda",
            "band": band, "outputs": [],
            "no_soportado": [str(x) for x in _lista(resp.get("no_soportado")) if x]}


def falta_frecuencia(cfg) -> bool:
    band = (cfg or {}).get("band") or {}
    return band.get("enable") is not False and band.get("freq_hz") in (None, "")


def pregunta_frecuencia() -> dict:
    return {"id": "freq_hz", "campo": "freq_hz",
            "pregunta": "¿A qué frecuencia debe moverse la banda?",
            "opciones": ["20 Hz", "30 Hz", "40 Hz"]}


def capacidades(hw=HW_BANDA) -> dict:
    """Catalogo de capacidades del programa maestro (para /banda/capacidades)."""
    return {
        "version": ENGINE_VERSION,
        "entradas": hw["entradas"], "salidas": hw["salidas"],
        "pares_excluyentes": [{"a": a, "b": b, "nombre": n} for a, b, n in hw["pares_excluyentes"]],
        "paro_emergencia": EMERGENCIA,
        "bloques": {"condiciones": N_COND, "memorias": N_MEM, "timers": N_TIMER,
                    "contadores": N_COUNT, "secuencias": N_SEQ, "pasos_por_secuencia": N_STEPS},
        "condicion_ops": list(COND_OP_NOMBRE.values()),
        "timer_tipos": list(TIMER_NOMBRE.values()),
        "contador_modos": list(COUNT_NOMBRE.values()),
        "secuencia_modos": list(SEQ_MODO_NOMBRE.values()),
        "transiciones": list(TRANS_NOMBRE.values()),
        "acciones_motor": list(ACCIONES_MOTOR),
        "senales_sistema": sorted(SIG_SYS),
        "resolucion_s": 0.1, "tiempo_max_s": DECIMAS_MAX / 10,
        "registros": {"comando": [CMD_BASE, CMD_BASE + CMD_LEN - 1],
                      "espera": [STAGE_BASE, STAGE_BASE + CFG_LEN - 1],
                      "estado": [STATUS_BASE, STATUS_BASE + STATUS_LEN - 1]},
    }
