"""
============================================================================
MOTOR CONFIGURABLE DE LA BANDA  (ST v6, EngineMode = 1)
============================================================================

Programa maestro: Archivos Cscape/ST_banda_v6_motor_configurable.txt

En v5 cada entrada tenia un papel fijo (I1 arranca, I2 para, S1/S2 son
eventos con acciones fijas). En v6 el PLC trae un MOTOR GENERICO que lee su
configuracion de un bloque de registros y conecta cualquier senal con
cualquier accion:

    SENALES  ->  CONDICIONES  ->  TIMERS / CONTADORES / MEMORIAS
             ->  SECUENCIA (maquina de estados)  ->  MOTOR / TORRETA / PLUMAS

Todo bloque PRODUCE una senal (con su codigo) y todo bloque CONSUME senales
por codigo. Por eso no hay "casos": "S1 -> espera 3 s -> invierte" es un timer
RETARDO con entrada S1 conectado a motor.invertir; "I1 y S2 -> amarilla" es una
condicion AND conectada a salidas.amarilla.

Este modulo es la UNICA fuente del contrato en Python:
  - el mapa de registros (EC = configuracion, ES = estado),
  - los codigos de senal y de accion,
  - el validador del JSON simbolico (lo que genera el LLM),
  - el compilador JSON -> 260 registros (%R200..%R459),
  - la descripcion legible.
banda_motor_sim.py ejecuta EXACTAMENTE la misma logica que el ST para probarla
sin PLC. plc_banda.py escribe el bloque por Modbus.

JSON simbolico (vive en cfg["band"]["engine"]):

{
  "motor": {"arranque": "I1", "paro": "S1", "modo": "enclavado"|"mientras",
            "pausa": null, "dir_1": null, "dir_2": null, "invertir": "T1",
            "paro_inversion_s": 2},
  "condiciones": [{"id": "C1", "op": "AND"|"OR", "entradas": ["I1", "S2"],
                   "flanco": "nivel"|"subida"|"bajada"}],
  "timers":      [{"id": "T1", "tipo": "TON"|"TOF"|"PULSO"|"RETARDO",
                   "entrada": "S1", "reset": null, "segundos": 3}],
  "contadores":  [{"id": "CT1", "entrada": "S1", "reset": "I2", "preset": 3,
                   "modo": "una_vez"|"ciclico"}],
  "memorias":    [{"id": "M1", "set": "S1", "reset": "I2", "prioridad": "reset"|"set"}],
  "salidas": {"verde": "C1", "amarilla": null, "roja": "CT1",
              "pluma1_sube": null, "pluma1_baja": null,
              "pluma2_sube": null, "pluma2_baja": null},
  "secuencia": {"inicio": "S1", "paro": null, "reset": null,
                "modo": "una_vez"|"ciclica", "exclusiva": true,
                "pasos": [{"acciones": ["amarilla"], "avanza": "tiempo",
                           "senal": null, "segundos": 3, "siguiente": null}]},
  "opciones": {"filtro_sensores_s": 0.1, "filtro_botones_s": 0,
               "pluma_max_s": 0, "luces_paro": []}
}
============================================================================
"""

import re
import unicodedata

ENGINE_VERSION = 6

# ---------------------------------------------------------------------------
# BLOQUES DE REGISTROS  (arreglos EC / ES del ST)
# ---------------------------------------------------------------------------
ENGINE_BASE = 200          # EC[0]  = %R200
ENGINE_LEN = 260           # EC[0..259] = %R200..%R459
STATUS_BASE = 460          # ES[0]  = %R460
STATUS_LEN = 40            # ES[0..39] = %R460..%R499

# Cabecera
O_MODE = 0                 # EngineMode: 0 = ST v5, 1 = motor configurable
O_RUN, O_STOP, O_RUNMODE, O_PAUSE = 1, 2, 3, 4
O_DIR1, O_DIR2, O_TOGGLE, O_REVTIME = 5, 6, 7, 8
O_DEB_SENS, O_DEB_BTN = 9, 10
O_APP1, O_APP2 = 11, 12
O_PLUMA_MAX, O_HARD_LAMPS, O_SEQ_EXCL = 13, 14, 15
O_OUT = 20                 # 20..26 fuente de cada salida
O_COND, COND_W = 30, 6     # Op, Src1..Src4, Edge
O_TIMER, TIMER_W = 78, 4   # Type, In, Reset, Preset(0.1 s)
O_COUNT, COUNT_W = 110, 4  # In, Reset, Preset, Mode
O_MEM, MEM_W = 142, 3      # Set, Reset, Prio
O_SEQ = 166                # Start, Stop, Reset, Mode, StepCount
O_STEP, STEP_W = 172, 5    # Actions, TransMode, TransSrc, Time(0.1 s), Next

N_COND = N_TIMER = N_COUNT = N_MEM = 8
N_STEPS = 16
N_COND_INPUTS = 4

# Estado (ES): solo lectura, lo escribe el ST en cada scan.
S_VERSION, S_VALID, S_ERROR = 0, 1, 2
S_MOTOR, S_DIR, S_DIR_TARGET, S_RUN_LATCH = 3, 4, 5, 6
S_SEQ_ACTIVE, S_SEQ_STEP, S_SEQ_ACC = 7, 8, 9
S_COND_MASK, S_TIMER_MASK, S_COUNT_MASK, S_MEM_MASK = 10, 11, 12, 13
S_OUT_MASK, S_IN_MASK = 14, 15
S_TIMER_ACC, S_COUNT_ACC = 16, 24
S_PLUMA_FAULT, S_BRAKE_ACC = 32, 33

MOTOR_ESTADO = {0: "detenido", 1: "en marcha", 2: "frenando para invertir"}

# ---------------------------------------------------------------------------
# SENALES
# ---------------------------------------------------------------------------
NOT_OFFSET = 100
SIG_TRUE = 15

SENALES = {
    "I1": 1, "I2": 2, "I3": 3, "S1": 4, "S2": 5,
    "BANDA_CORRIENDO": 6, "BANDA_DETENIDA": 7, "DIR_1": 8, "DIR_2": 9,
    "INVIRTIENDO": 10, "CFG_LISTA": 11, "VFD_CERO": 12,
    "APP1": 13, "APP2": 14, "SIEMPRE": SIG_TRUE,
}
for _i in range(N_COND):
    SENALES[f"C{_i + 1}"] = 16 + _i
for _i in range(N_TIMER):
    SENALES[f"T{_i + 1}"] = 24 + _i
for _i in range(N_COUNT):
    SENALES[f"CT{_i + 1}"] = 32 + _i
for _i in range(N_MEM):
    SENALES[f"M{_i + 1}"] = 40 + _i
for _i in range(N_STEPS):
    SENALES[f"PASO{_i + 1}"] = 48 + _i
CODIGO_A_SENAL = {v: k for k, v in SENALES.items()}

ALIAS_SENAL = {
    "BOTON_ARRANQUE": "I1", "BTN_START": "I1", "ARRANQUE": "I1", "BOTON_VERDE": "I1",
    "BOTON_AUX": "I2", "BTN_AUX": "I2",
    "PARO": "I3", "BTN_STOP": "I3",
    "SENSOR1": "S1", "SENSOR_1": "S1", "I4": "S1",
    "SENSOR2": "S2", "SENSOR_2": "S2", "I5": "S2",
    "CORRIENDO": "BANDA_CORRIENDO", "EN_MARCHA": "BANDA_CORRIENDO",
    "DETENIDA": "BANDA_DETENIDA", "PARADA": "BANDA_DETENIDA",
    "DERECHA": "DIR_1", "IZQUIERDA": "DIR_2",
    "TRUE": "SIEMPRE", "ON": "SIEMPRE",
}

DESCRIPCION_SENAL = {
    "I1": "boton I1 (NA)", "I2": "boton I2 (NC)", "I3": "paro I3",
    "S1": "sensor S1", "S2": "sensor S2",
    "BANDA_CORRIENDO": "banda corriendo", "BANDA_DETENIDA": "banda detenida",
    "DIR_1": "banda en direccion 1", "DIR_2": "banda en direccion 2",
    "INVIRTIENDO": "banda invirtiendo", "CFG_LISTA": "configuracion lista",
    "VFD_CERO": "variador en velocidad cero", "APP1": "boton 1 de la app",
    "APP2": "boton 2 de la app", "SIEMPRE": "siempre",
}

# Salidas fisicas (orden = EC[20..26] y bits 0..6 de las acciones de un paso)
SALIDAS = ("verde", "amarilla", "roja", "pluma1_sube", "pluma1_baja",
           "pluma2_sube", "pluma2_baja")
SALIDA_FISICA = {"verde": "Q3", "amarilla": "Q4", "roja": "Q5",
                 "pluma1_sube": "Q8", "pluma1_baja": "Q9",
                 "pluma2_sube": "Q6", "pluma2_baja": "Q7"}

# Acciones de un paso de la secuencia (bit = posicion)
ACCIONES_PASO = SALIDAS + ("marcha", "paro", "dir_1", "dir_2", "invertir")
ACCION_BIT = {a: i for i, a in enumerate(ACCIONES_PASO)}
ALIAS_ACCION = {
    "luz_verde": "verde", "luz_amarilla": "amarilla", "luz_roja": "roja",
    "subir_pluma1": "pluma1_sube", "bajar_pluma1": "pluma1_baja",
    "subir_pluma2": "pluma2_sube", "bajar_pluma2": "pluma2_baja",
    "arrancar": "marcha", "motor": "marcha", "avanzar": "marcha",
    "detener": "paro", "parar": "paro", "derecha": "dir_1", "izquierda": "dir_2",
    "cambiar_sentido": "invertir", "cambiar_direccion": "invertir",
}
COLORES = ("verde", "amarilla", "roja")

COND_OPS = {"and": 1, "y": 1, "or": 2, "o": 2}
COND_EDGES = {"nivel": 0, "subida": 1, "flanco": 1, "flanco_subida": 1,
              "bajada": 2, "flanco_bajada": 2}
TIMER_TIPOS = {"ton": 1, "on_delay": 1, "retardo_mientras": 1,
               "tof": 2, "off_delay": 2,
               "pulso": 3, "tp": 3,
               "retardo": 4, "delay": 4, "espera": 4}
TIMER_NOMBRE = {1: "TON", 2: "TOF", 3: "PULSO", 4: "RETARDO"}
COUNT_MODOS = {"una_vez": 0, "unico": 0, "ciclico": 1, "rearme": 1, "cada": 1}
MEM_PRIO = {"reset": 0, "set": 1}
SEQ_MODOS = {"una_vez": 0, "once": 0, "ciclica": 1, "ciclico": 1, "loop": 1}
TRANS_MODOS = {"tiempo": 0, "senal": 1, "nivel": 1, "tiempo_y_senal": 2,
               "senal_o_tiempo": 3, "flanco": 4}
TRANS_NOMBRE = {0: "tiempo", 1: "senal", 2: "tiempo_y_senal", 3: "senal_o_tiempo", 4: "flanco"}
MOTOR_MODOS = {"enclavado": 0, "mientras": 1}

DECIMAS_MAX = 32767
REV_TIME_DEFECTO = 20      # 2.0 s de paro antes de invertir
REV_TIME_MIN = 10          # el ST nunca invierte con menos de 1 s


def _norm(s) -> str:
    t = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[\s\-]+", "_", t.strip().lower())


def es_motor(cfg_o_band) -> bool:
    """True si el programa usa el motor configurable (bloque band.engine)."""
    if not isinstance(cfg_o_band, dict):
        return False
    band = cfg_o_band.get("band", cfg_o_band)
    # Un bloque con "version": 7 es del motor LAVO v7 (lavo_motor.py), no del v6.
    return (isinstance(band, dict) and isinstance(band.get("engine"), dict)
            and str(band["engine"].get("version")) != "7")


# ---------------------------------------------------------------------------
# SENAL SIMBOLICA <-> CODIGO
# ---------------------------------------------------------------------------
def codigo_senal(nombre):
    """'S1' -> 4, 'NOT S1' / '!S1' -> 104, None -> 0. Lanza ValueError."""
    if nombre is None or nombre == "" or nombre == 0:
        return 0
    if isinstance(nombre, bool):
        raise ValueError(f"senal '{nombre}' no valida")
    if isinstance(nombre, int):
        if 0 <= nombre <= 63 or 100 <= nombre <= 163:
            return nombre
        raise ValueError(f"codigo de senal {nombre} fuera de rango")
    t = str(nombre).strip().upper().replace("-", "_")
    neg = False
    m = re.match(r"^(NOT\s+|NO\s+|!|~|/)\s*(.+)$", t)
    if m:
        neg, t = True, m.group(2).strip()
    t = re.sub(r"\s+", "_", t)
    # La voz transcribe "I2" como "12" (o "I 2", "boton 12"): entradas I1..I5.
    m = re.match(r"^(?:BOTON_|BTN_|ENTRADA_)?(?:I_?|1)([1-5])$", t)
    if m:
        t = f"I{m.group(1)}"
    t = ALIAS_SENAL.get(t, t)
    m = re.match(r"^(C|T|CT|M|PASO)_?(\d+)(\.DN)?$", t)
    if m:
        t = f"{m.group(1)}{int(m.group(2))}"
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


def _texto_senal(nombre) -> str:
    try:
        n = nombre_senal(codigo_senal(nombre))
    except ValueError:
        return str(nombre)
    neg = n.startswith("NOT ")
    base = n[4:] if neg else n
    txt = DESCRIPCION_SENAL.get(base, base)
    return f"NO {txt}" if neg else txt


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
        errores.append(f"{etiqueta}: {v} s fuera de rango "
                       f"[{minimo / 10:g}, {DECIMAS_MAX / 10:g}] s.")
        return None
    return d


def _id_num(ident, prefijo, maximo):
    m = re.match(rf"^{prefijo}_?(\d+)$", str(ident or "").strip().upper())
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= maximo else None


def _lista(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


# ---------------------------------------------------------------------------
# VALIDACION
# ---------------------------------------------------------------------------
def _referencias(eng) -> list:
    """(etiqueta, nombre) de TODAS las senales que usa el motor."""
    refs = []
    motor = eng.get("motor") or {}
    for k in ("arranque", "paro", "pausa", "dir_1", "dir_2", "invertir"):
        if motor.get(k) not in (None, ""):
            refs.append((f"motor.{k}", motor[k]))
    for c in _lista(eng.get("condiciones")):
        for s in _lista((c or {}).get("entradas")):
            refs.append((f"condicion {c.get('id')}", s))
    for t in _lista(eng.get("timers")):
        for k in ("entrada", "reset"):
            if (t or {}).get(k) not in (None, ""):
                refs.append((f"timer {t.get('id')}.{k}", t[k]))
    for c in _lista(eng.get("contadores")):
        for k in ("entrada", "reset"):
            if (c or {}).get(k) not in (None, ""):
                refs.append((f"contador {c.get('id')}.{k}", c[k]))
    for m in _lista(eng.get("memorias")):
        for k in ("set", "reset"):
            if (m or {}).get(k) not in (None, ""):
                refs.append((f"memoria {m.get('id')}.{k}", m[k]))
    for k, v in (eng.get("salidas") or {}).items():
        if v not in (None, ""):
            refs.append((f"salidas.{k}", v))
    seq = eng.get("secuencia") or {}
    for k in ("inicio", "paro", "reset"):
        if seq.get(k) not in (None, ""):
            refs.append((f"secuencia.{k}", seq[k]))
    for i, p in enumerate(_lista(seq.get("pasos"))):
        if (p or {}).get("senal") not in (None, ""):
            refs.append((f"paso {i + 1}.senal", p["senal"]))
    return refs


def _ids_definidos(eng) -> set:
    ids = set()
    for clave, pref, maximo in (("condiciones", "C", N_COND), ("timers", "T", N_TIMER),
                                ("contadores", "CT", N_COUNT), ("memorias", "M", N_MEM)):
        for b in _lista(eng.get(clave)):
            n = _id_num((b or {}).get("id"), pref, maximo)
            if n:
                ids.add(f"{pref}{n}")
    for i in range(len(_lista((eng.get("secuencia") or {}).get("pasos")))):
        ids.add(f"PASO{i + 1}")
    return ids


def _acciones_paso(lista, tag, errores):
    out = []
    for a in _lista(lista):
        k = _norm(a)
        k = ALIAS_ACCION.get(k, k)
        if k not in ACCION_BIT:
            errores.append(f"{tag}: accion '{a}' no existe (usa: {', '.join(ACCIONES_PASO)}).")
            continue
        if k not in out:
            out.append(k)
    return out


def _retardos_de_plumas(eng):
    """[(pluma, timer_sube, timer_baja)] cuando una pluma sube con un timer
    RETARDO y baja con otro. Un RETARDO queda activo hasta su reset: sin
    reset, los dos acaban activos a la vez y el ST apaga la pluma (falla)."""
    timers = {}
    for t in _lista(eng.get("timers")):
        if isinstance(t, dict) and _norm(t.get("tipo")) == "retardo":
            n = _id_num(t.get("id"), "T", N_TIMER)
            if n is not None:
                timers[f"T{n}"] = t
    salidas = eng.get("salidas") if isinstance(eng.get("salidas"), dict) else {}
    out = []
    for p in (1, 2):
        a = str(salidas.get(f"pluma{p}_sube") or "").strip().upper()
        b = str(salidas.get(f"pluma{p}_baja") or "").strip().upper()
        if a in timers and b in timers and a != b:
            out.append((p, timers[a], timers[b]))
    return out


def _id_timer(t) -> str:
    return f"T{_id_num(t.get('id'), 'T', N_TIMER)}"


def rearmar_plumas(eng) -> list:
    """Repara el patron "S1 -> espera -> sube / S2 -> espera -> baja": a cada
    RETARDO sin reset le pone de reset la ENTRADA del otro, asi la orden
    vieja se borra cuando llega la nueva. Devuelve las notas de lo ajustado."""
    notas = []
    for p, ts, tb in _retardos_de_plumas(eng):
        for t, otro in ((ts, tb), (tb, ts)):
            if t.get("reset") in (None, "") and otro.get("entrada") not in (None, ""):
                t["reset"] = otro["entrada"]
                notas.append(f"{_id_timer(t)}: reset = {otro['entrada']} (pluma {p} no puede "
                             f"recibir subir y bajar a la vez)")
    return notas


def _choques_retardo_plumas(eng) -> list:
    errores = []
    for p, ts, tb in _retardos_de_plumas(eng):
        a, b = _id_timer(ts), _id_timer(tb)
        for t, otro, ido in ((ts, tb, b), (tb, ts, a)):
            r = str(t.get("reset") or "").strip().upper()
            if not r:
                errores.append(
                    f"pluma {p}: {a} (sube) y {b} (baja) son RETARDO y se quedan activos; juntos "
                    f"apagan la pluma. Pon en {_id_timer(t)} reset = {otro.get('entrada')} "
                    f"(la entrada de {ido}).")
            elif r == ido:
                errores.append(
                    f"pluma {p}: {_id_timer(t)} tiene reset = {ido} y se bloquean entre si. "
                    f"Usa reset = {otro.get('entrada')} (la entrada de {ido}).")
    return errores


def validar_motor(band) -> tuple:
    """Valida band.engine. Devuelve (errores, avisos). Mismas reglas que el
    bloque §3b del ST, mas comprobaciones semanticas que el PLC no puede ver
    (referencias a bloques no definidos, secuencias que giran sin espera...)."""
    errores, avisos = [], []
    if not isinstance(band, dict):
        return ["Falta el bloque 'band'."], []
    eng = band.get("engine")
    if not isinstance(eng, dict):
        return ["band.engine no es un objeto."], []
    marcha = band.get("enable") is not False

    # --- ids y bloques -----------------------------------------------------
    for clave, pref, maximo in (("condiciones", "C", N_COND), ("timers", "T", N_TIMER),
                                ("contadores", "CT", N_COUNT), ("memorias", "M", N_MEM)):
        bloques = eng.get(clave)
        if bloques is not None and not isinstance(bloques, list):
            errores.append(f"'{clave}' debe ser una lista.")
            continue
        vistos = set()
        for b in _lista(bloques):
            if not isinstance(b, dict):
                errores.append(f"{clave}: cada elemento debe ser un objeto.")
                continue
            n = _id_num(b.get("id"), pref, maximo)
            if n is None:
                errores.append(f"{clave}: id '{b.get('id')}' invalido (usa {pref}1..{pref}{maximo}).")
            elif n in vistos:
                errores.append(f"{clave}: id {pref}{n} repetido.")
            else:
                vistos.add(n)

    for c in _lista(eng.get("condiciones")):
        if not isinstance(c, dict):
            continue
        tag = f"condicion {c.get('id')}"
        if _norm(c.get("op") or "and") not in COND_OPS:
            errores.append(f"{tag}: op '{c.get('op')}' debe ser AND u OR.")
        ent = [e for e in _lista(c.get("entradas")) if e not in (None, "")]
        if not 1 <= len(ent) <= N_COND_INPUTS:
            errores.append(f"{tag}: necesita entre 1 y {N_COND_INPUTS} entradas.")
        if _norm(c.get("flanco") or "nivel") not in COND_EDGES:
            errores.append(f"{tag}: flanco '{c.get('flanco')}' debe ser nivel, subida o bajada.")

    for t in _lista(eng.get("timers")):
        if not isinstance(t, dict):
            continue
        tag = f"timer {t.get('id')}"
        if _norm(t.get("tipo")) not in TIMER_TIPOS:
            errores.append(f"{tag}: tipo '{t.get('tipo')}' debe ser TON, TOF, PULSO o RETARDO.")
        if t.get("entrada") in (None, ""):
            errores.append(f"{tag}: falta 'entrada' (la senal que lo dispara).")
        if t.get("segundos") is None:
            errores.append(f"{tag}: falta 'segundos'.")
        else:
            _decimas(t.get("segundos"), f"{tag}.segundos", errores)

    for c in _lista(eng.get("contadores")):
        if not isinstance(c, dict):
            continue
        tag = f"contador {c.get('id')}"
        if c.get("entrada") in (None, ""):
            errores.append(f"{tag}: falta 'entrada' (lo que cuenta).")
        try:
            p = int(c.get("preset"))
            if not 1 <= p <= 32767:
                errores.append(f"{tag}: preset {p} fuera de [1, 32767].")
        except (TypeError, ValueError):
            errores.append(f"{tag}: preset '{c.get('preset')}' no es entero.")
        if _norm(c.get("modo") or "una_vez") not in COUNT_MODOS:
            errores.append(f"{tag}: modo '{c.get('modo')}' debe ser una_vez o ciclico.")

    for m in _lista(eng.get("memorias")):
        if not isinstance(m, dict):
            continue
        tag = f"memoria {m.get('id')}"
        if m.get("set") in (None, "") and m.get("reset") in (None, ""):
            errores.append(f"{tag}: necesita 'set' o 'reset'.")
        if _norm(m.get("prioridad") or "reset") not in MEM_PRIO:
            errores.append(f"{tag}: prioridad '{m.get('prioridad')}' debe ser reset o set.")

    # --- salidas -------------------------------------------------------------
    salidas = eng.get("salidas") or {}
    if not isinstance(salidas, dict):
        errores.append("'salidas' debe ser un objeto {salida: senal}.")
        salidas = {}
    for k in salidas:
        if k not in SALIDAS:
            errores.append(f"salidas.{k}: no existe (usa: {', '.join(SALIDAS)}).")
    for p in (1, 2):
        if salidas.get(f"pluma{p}_sube") and salidas.get(f"pluma{p}_sube") == salidas.get(f"pluma{p}_baja"):
            errores.append(f"pluma {p}: la misma senal no puede subirla y bajarla.")
    errores += _choques_retardo_plumas(eng)

    # --- secuencia -----------------------------------------------------------
    seq = eng.get("secuencia")
    pasos = []
    if seq is not None:
        if not isinstance(seq, dict):
            errores.append("'secuencia' debe ser un objeto.")
            seq = None
        else:
            pasos = _lista(seq.get("pasos"))
            if not 1 <= len(pasos) <= N_STEPS:
                errores.append(f"secuencia: necesita entre 1 y {N_STEPS} pasos.")
            if seq.get("inicio") in (None, ""):
                errores.append("secuencia: falta 'inicio' (la senal que la arranca).")
            if _norm(seq.get("modo") or "una_vez") not in SEQ_MODOS:
                errores.append(f"secuencia.modo '{seq.get('modo')}' debe ser una_vez o ciclica.")
            espera_total = 0
            for i, p in enumerate(pasos):
                tag = f"paso {i + 1}"
                if not isinstance(p, dict):
                    errores.append(f"{tag}: debe ser un objeto.")
                    continue
                acc = _acciones_paso(p.get("acciones"), tag, errores)
                if "marcha" in acc and "paro" in acc:
                    errores.append(f"{tag}: 'marcha' y 'paro' a la vez.")
                if "dir_1" in acc and "dir_2" in acc:
                    errores.append(f"{tag}: 'dir_1' y 'dir_2' a la vez.")
                for n in (1, 2):
                    if f"pluma{n}_sube" in acc and f"pluma{n}_baja" in acc:
                        errores.append(f"{tag}: la pluma {n} no puede subir y bajar a la vez.")
                if not marcha and set(acc) & {"marcha", "dir_1", "dir_2", "invertir"}:
                    errores.append(f"{tag}: mueve la banda, pero el programa no tiene "
                                   f"movimiento (enable=false).")
                av = _norm(p.get("avanza") or ("flanco" if p.get("senal") else "tiempo"))
                if av not in TRANS_MODOS:
                    errores.append(f"{tag}: avanza '{p.get('avanza')}' no existe "
                                   f"(usa: {', '.join(TRANS_NOMBRE.values())}).")
                    continue
                modo = TRANS_MODOS[av]
                if modo != 0 and p.get("senal") in (None, ""):
                    errores.append(f"{tag}: avanza por '{av}' pero falta 'senal'.")
                if modo in (0, 2) and p.get("segundos") is None:
                    errores.append(f"{tag}: avanza por '{av}' pero falta 'segundos'.")
                d = _decimas(p.get("segundos") or 0, f"{tag}.segundos", errores)
                if modo in (0, 2, 3):
                    espera_total += d or 0
                else:
                    espera_total += 1            # espera una senal: no gira solo
                sig = p.get("siguiente")
                if sig not in (None, 0, ""):
                    try:
                        s = int(sig)
                        if not 1 <= s <= len(pasos):
                            errores.append(f"{tag}: siguiente={s} no existe (1..{len(pasos)}).")
                    except (TypeError, ValueError):
                        errores.append(f"{tag}: siguiente '{sig}' no es entero.")
            if (pasos and _norm(seq.get("modo") or "una_vez") in ("ciclica", "ciclico", "loop")
                    and espera_total == 0):
                errores.append("secuencia ciclica sin ninguna espera: giraria en cada scan.")

    # --- motor ---------------------------------------------------------------
    motor = eng.get("motor") or {}
    if not isinstance(motor, dict):
        errores.append("'motor' debe ser un objeto.")
        motor = {}
    if _norm(motor.get("modo") or "enclavado") not in MOTOR_MODOS:
        errores.append(f"motor.modo '{motor.get('modo')}' debe ser enclavado o mientras.")
    usa_motor = any(motor.get(k) not in (None, "") for k in
                    ("arranque", "paro", "pausa", "dir_1", "dir_2", "invertir"))
    if not marcha and usa_motor:
        errores.append("El motor tiene senales de arranque/paro/direccion pero el programa "
                       "no mueve la banda (enable=false).")
    if motor.get("paro_inversion_s") is not None:
        _decimas(motor["paro_inversion_s"], "motor.paro_inversion_s", errores, minimo=REV_TIME_MIN)
    try:
        if codigo_senal(motor.get("arranque")) in (SENALES["I3"], SENALES["I3"] + NOT_OFFSET):
            errores.append("I3 es el paro prioritario: no puede arrancar la banda.")
        if (codigo_senal(motor.get("arranque")) == SENALES["I2"]
                and band.get("stop_mode") in (1, 3)):
            errores.append("I2 arranca la banda pero tambien esta como paro (paros.i2 = true): "
                           "el mismo boton la arranca y la detiene y nunca se mueve. "
                           "Si I2 es el arranque, pon paros.i2 = false.")
        if (motor.get("arranque") not in (None, "")
                and codigo_senal(motor.get("arranque")) == codigo_senal(motor.get("paro"))):
            errores.append("motor: la misma senal no puede arrancar y detener la banda.")
        if codigo_senal(motor.get("arranque")) == SENALES["I2"]:
            avisos.append("I2 es NC: si su cable se corta, el PLC lo ve presionado y "
                          "arrancaria la banda. Mejor usa I1 para arrancar.")
    except ValueError:
        pass

    # --- opciones ------------------------------------------------------------
    op = eng.get("opciones") or {}
    if not isinstance(op, dict):
        errores.append("'opciones' debe ser un objeto.")
        op = {}
    for k in ("filtro_sensores_s", "filtro_botones_s", "pluma_max_s"):
        if op.get(k) is not None:
            _decimas(op[k], f"opciones.{k}", errores)
    for c in _lista(op.get("luces_paro")):
        if _norm(c) not in COLORES:
            errores.append(f"opciones.luces_paro: color '{c}' no existe.")

    # --- referencias ---------------------------------------------------------
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
        if re.match(r"^(C|T|CT|M|PASO)\d+$", base) and base not in definidos:
            errores.append(f"{tag}: usa {base}, que no esta definido.")
    for ident in sorted(definidos):
        if not ident.startswith("PASO") and ident not in usados:
            avisos.append(f"{ident} esta definido pero nada lo usa.")

    # Un contador ciclico da un pulso de UN scan: en una lampara no se ve.
    ciclicos = {f"CT{_id_num(c.get('id'), 'CT', N_COUNT)}" for c in _lista(eng.get("contadores"))
                if isinstance(c, dict) and _norm(c.get("modo")) in ("ciclico", "rearme", "cada")}
    for k, v in salidas.items():
        try:
            base = nombre_senal(codigo_senal(v)).replace("NOT ", "")
        except ValueError:
            continue
        if base in ciclicos:
            avisos.append(f"salidas.{k} usa {base} (ciclico): es un pulso de un scan y la "
                          f"salida no se veria. Usa un timer PULSO disparado por {base}.")

    if marcha and not usa_motor and not any(
            "marcha" in _acciones_paso(p.get("acciones"), "", [])
            for p in pasos if isinstance(p, dict)):
        avisos.append("Nada arranca la banda: se usa I1 como boton de arranque.")
    return errores, avisos


# ---------------------------------------------------------------------------
# COMPILADOR  JSON simbolico -> EC[0..259]
# ---------------------------------------------------------------------------
def _mascara_colores(lista) -> int:
    m = 0
    for c in _lista(lista):
        k = _norm(c)
        if k in COLORES:
            m |= 1 << COLORES.index(k)
    return m


def compilar_motor(band) -> list:
    """band (con band.engine valido) -> lista de 260 enteros para %R200..%R459.
    Lanza ValueError si el motor no es valido."""
    errores, _ = validar_motor(band)
    if errores:
        raise ValueError("Motor de la banda invalido:\n  - " + "\n  - ".join(errores))
    eng = band["engine"]
    ec = [0] * ENGINE_LEN
    ec[O_MODE] = 1
    marcha = band.get("enable") is not False

    motor = eng.get("motor") or {}
    pasos = _lista((eng.get("secuencia") or {}).get("pasos"))
    marcha_en_pasos = any("marcha" in _acciones_paso(p.get("acciones"), "", []) for p in pasos)
    arranque = motor.get("arranque")
    if marcha and arranque in (None, "") and not marcha_en_pasos and not any(
            motor.get(k) not in (None, "") for k in ("paro", "pausa", "dir_1", "dir_2", "invertir")):
        arranque = "I1"
    ec[O_RUN] = codigo_senal(arranque)
    ec[O_STOP] = codigo_senal(motor.get("paro"))
    ec[O_RUNMODE] = MOTOR_MODOS[_norm(motor.get("modo") or "enclavado")]
    ec[O_PAUSE] = codigo_senal(motor.get("pausa"))
    ec[O_DIR1] = codigo_senal(motor.get("dir_1"))
    ec[O_DIR2] = codigo_senal(motor.get("dir_2"))
    ec[O_TOGGLE] = codigo_senal(motor.get("invertir"))
    rev = motor.get("paro_inversion_s")
    ec[O_REVTIME] = REV_TIME_DEFECTO if rev is None else int(round(float(rev) * 10))

    op = eng.get("opciones") or {}
    ec[O_DEB_SENS] = int(round(float(op.get("filtro_sensores_s", 0.1) or 0) * 10))
    ec[O_DEB_BTN] = int(round(float(op.get("filtro_botones_s", 0) or 0) * 10))
    ec[O_PLUMA_MAX] = int(round(float(op.get("pluma_max_s", 0) or 0) * 10))
    ec[O_HARD_LAMPS] = _mascara_colores(op.get("luces_paro"))

    salidas = eng.get("salidas") or {}
    for i, k in enumerate(SALIDAS):
        ec[O_OUT + i] = codigo_senal(salidas.get(k))

    for c in _lista(eng.get("condiciones")):
        b = O_COND + (_id_num(c["id"], "C", N_COND) - 1) * COND_W
        ec[b] = COND_OPS[_norm(c.get("op") or "and")]
        ent = [e for e in _lista(c.get("entradas")) if e not in (None, "")]
        for j, e in enumerate(ent[:N_COND_INPUTS]):
            ec[b + 1 + j] = codigo_senal(e)
        ec[b + 5] = COND_EDGES[_norm(c.get("flanco") or "nivel")]

    for t in _lista(eng.get("timers")):
        b = O_TIMER + (_id_num(t["id"], "T", N_TIMER) - 1) * TIMER_W
        ec[b] = TIMER_TIPOS[_norm(t["tipo"])]
        ec[b + 1] = codigo_senal(t.get("entrada"))
        ec[b + 2] = codigo_senal(t.get("reset"))
        ec[b + 3] = int(round(float(t["segundos"]) * 10))

    for c in _lista(eng.get("contadores")):
        b = O_COUNT + (_id_num(c["id"], "CT", N_COUNT) - 1) * COUNT_W
        ec[b] = codigo_senal(c.get("entrada"))
        ec[b + 1] = codigo_senal(c.get("reset"))
        ec[b + 2] = int(c["preset"])
        ec[b + 3] = COUNT_MODOS[_norm(c.get("modo") or "una_vez")]

    for m in _lista(eng.get("memorias")):
        b = O_MEM + (_id_num(m["id"], "M", N_MEM) - 1) * MEM_W
        ec[b] = codigo_senal(m.get("set"))
        ec[b + 1] = codigo_senal(m.get("reset"))
        ec[b + 2] = MEM_PRIO[_norm(m.get("prioridad") or "reset")]

    seq = eng.get("secuencia")
    if isinstance(seq, dict) and pasos:
        ec[O_SEQ] = codigo_senal(seq.get("inicio"))
        ec[O_SEQ + 1] = codigo_senal(seq.get("paro"))
        ec[O_SEQ + 2] = codigo_senal(seq.get("reset"))
        ec[O_SEQ + 3] = SEQ_MODOS[_norm(seq.get("modo") or "una_vez")]
        ec[O_SEQ + 4] = len(pasos)
        excl = seq.get("exclusiva")
        ec[O_SEQ_EXCL] = 1 if excl is None or excl else 0
        for i, p in enumerate(pasos):
            b = O_STEP + i * STEP_W
            mask = 0
            for a in _acciones_paso(p.get("acciones"), "", []):
                mask |= 1 << ACCION_BIT[a]
            ec[b] = mask
            av = _norm(p.get("avanza") or ("flanco" if p.get("senal") else "tiempo"))
            ec[b + 1] = TRANS_MODOS[av]
            ec[b + 2] = codigo_senal(p.get("senal"))
            ec[b + 3] = int(round(float(p.get("segundos") or 0) * 10))
            ec[b + 4] = int(p.get("siguiente") or 0)
    return ec


def registros_apagado() -> list:
    """EC con EngineMode = 0: el PLC vuelve a la logica v5."""
    return [0] * ENGINE_LEN


# ---------------------------------------------------------------------------
# VALIDACION DEL BLOQUE COMPILADO (espejo exacto de §3b del ST)
# ---------------------------------------------------------------------------
def _es_fuente(ofs: int) -> bool:
    if ofs in (O_RUN, O_STOP, O_PAUSE, O_DIR1, O_DIR2, O_TOGGLE):
        return True
    if O_OUT <= ofs <= O_OUT + 6:
        return True
    if O_COND <= ofs < O_COND + N_COND * COND_W:
        return 1 <= (ofs - O_COND) % COND_W <= 4
    if O_TIMER <= ofs < O_TIMER + N_TIMER * TIMER_W:
        return (ofs - O_TIMER) % TIMER_W in (1, 2)
    if O_COUNT <= ofs < O_COUNT + N_COUNT * COUNT_W:
        return (ofs - O_COUNT) % COUNT_W in (0, 1)
    if O_MEM <= ofs < O_MEM + N_MEM * MEM_W:
        return (ofs - O_MEM) % MEM_W in (0, 1)
    if O_SEQ <= ofs <= O_SEQ + 2:
        return True
    if O_STEP <= ofs < O_STEP + N_STEPS * STEP_W:
        return (ofs - O_STEP) % STEP_W == 2
    return False


def _rango(ofs: int):
    """(min, max) de un campo que no es fuente, o None si es libre/reservado."""
    if ofs == O_MODE:
        return (0, 1)
    if ofs in (O_RUNMODE, O_APP1, O_APP2, O_SEQ_EXCL):
        return (0, 1)
    if ofs in (O_REVTIME, O_DEB_SENS, O_DEB_BTN, O_PLUMA_MAX):
        return (0, 32767)
    if ofs == O_HARD_LAMPS:
        return (0, 7)
    if O_COND <= ofs < O_COND + N_COND * COND_W:
        r = (ofs - O_COND) % COND_W
        return (0, 2) if r in (0, 5) else None
    if O_TIMER <= ofs < O_TIMER + N_TIMER * TIMER_W:
        r = (ofs - O_TIMER) % TIMER_W
        return (0, 4) if r == 0 else (0, 32767) if r == 3 else None
    if O_COUNT <= ofs < O_COUNT + N_COUNT * COUNT_W:
        r = (ofs - O_COUNT) % COUNT_W
        return (0, 32767) if r == 2 else (0, 1) if r == 3 else None
    if O_MEM <= ofs < O_MEM + N_MEM * MEM_W:
        return (0, 1) if (ofs - O_MEM) % MEM_W == 2 else None
    if ofs == O_SEQ + 3:
        return (0, 1)
    if ofs == O_SEQ + 4:
        return (0, N_STEPS)
    if O_STEP <= ofs < O_STEP + N_STEPS * STEP_W:
        r = (ofs - O_STEP) % STEP_W
        return {0: (0, 4095), 1: (0, 4), 3: (0, 32767), 4: (0, N_STEPS)}.get(r)
    return None


def error_registros(ec) -> int:
    """0 si EC es valido; si no, offset+1 del primer campo invalido (igual que
    ES[2] del PLC). Sirve para explicar un EngError leido del PLC."""
    for ofs, v in enumerate(ec):
        if ofs > 0 and ec[O_MODE] != 1:
            break
        if _es_fuente(ofs):
            if not (0 <= v <= 63 or 100 <= v <= 163):
                return ofs + 1
        else:
            r = _rango(ofs)
            if r and not (r[0] <= v <= r[1]):
                return ofs + 1
    return 0


def campo_de_offset(ofs: int) -> str:
    """Nombre legible del campo EC[ofs] (para explicar ES[2])."""
    cab = {O_MODE: "EngineMode", O_RUN: "motor.arranque", O_STOP: "motor.paro",
           O_RUNMODE: "motor.modo", O_PAUSE: "motor.pausa", O_DIR1: "motor.dir_1",
           O_DIR2: "motor.dir_2", O_TOGGLE: "motor.invertir", O_REVTIME: "motor.paro_inversion_s",
           O_DEB_SENS: "opciones.filtro_sensores_s", O_DEB_BTN: "opciones.filtro_botones_s",
           O_APP1: "APP1", O_APP2: "APP2", O_PLUMA_MAX: "opciones.pluma_max_s",
           O_HARD_LAMPS: "opciones.luces_paro", O_SEQ_EXCL: "secuencia.exclusiva"}
    if ofs in cab:
        return cab[ofs]
    if O_OUT <= ofs <= O_OUT + 6:
        return f"salidas.{SALIDAS[ofs - O_OUT]}"
    for base, w, n, pref, campos in (
            (O_COND, COND_W, N_COND, "C", ("op", "entrada1", "entrada2", "entrada3", "entrada4", "flanco")),
            (O_TIMER, TIMER_W, N_TIMER, "T", ("tipo", "entrada", "reset", "segundos")),
            (O_COUNT, COUNT_W, N_COUNT, "CT", ("entrada", "reset", "preset", "modo")),
            (O_MEM, MEM_W, N_MEM, "M", ("set", "reset", "prioridad")),
            (O_STEP, STEP_W, N_STEPS, "PASO", ("acciones", "avanza", "senal", "segundos", "siguiente"))):
        if base <= ofs < base + n * w:
            return f"{pref}{(ofs - base) // w + 1}.{campos[(ofs - base) % w]}"
    if O_SEQ <= ofs <= O_SEQ + 4:
        return "secuencia." + ("inicio", "paro", "reset", "modo", "pasos")[ofs - O_SEQ]
    return f"EC[{ofs}]"


# ---------------------------------------------------------------------------
# DESCRIPCION LEGIBLE
# ---------------------------------------------------------------------------
def describir_motor(band) -> list:
    eng = (band or {}).get("engine") or {}
    L = []
    if band.get("enable") is not False:
        d = "izquierda (dir 2)" if str(band.get("direction")).lower() in ("izquierda", "2") else "derecha (dir 1)"
        f = f" a {band.get('freq_hz')} Hz" if band.get("freq_hz") else ""
        L.append(f"Banda configurable: arranca hacia la {d}{f}.")
    else:
        L.append("Banda configurable sin movimiento (solo torreta/plumas).")
    m = eng.get("motor") or {}
    if m.get("arranque"):
        modo = "mientras este activo" if _norm(m.get("modo")) == "mientras" else "y se queda en marcha"
        L.append(f"Motor: arranca con {_texto_senal(m['arranque'])} {modo}.")
    if m.get("paro"):
        L.append(f"Motor: se detiene con {_texto_senal(m['paro'])}.")
    if m.get("pausa"):
        L.append(f"Motor: pausa mientras {_texto_senal(m['pausa'])}.")
    for k, txt in (("dir_1", "direccion 1"), ("dir_2", "direccion 2")):
        if m.get(k):
            L.append(f"Direccion: pasa a {txt} con {_texto_senal(m[k])}.")
    if m.get("invertir"):
        L.append(f"Direccion: invierte con {_texto_senal(m['invertir'])} "
                 f"(para {m.get('paro_inversion_s') or REV_TIME_DEFECTO / 10:g} s antes de invertir).")
    for c in _lista(eng.get("condiciones")):
        op = " Y " if COND_OPS.get(_norm(c.get("op") or "and")) == 1 else " O "
        fl = {1: " (al activarse)", 2: " (al desactivarse)"}.get(COND_EDGES.get(_norm(c.get("flanco") or "nivel")), "")
        L.append(f"{c.get('id')}: {op.join(_texto_senal(e) for e in _lista(c.get('entradas')))}{fl}.")
    for t in _lista(eng.get("timers")):
        tipo = TIMER_TIPOS.get(_norm(t.get("tipo")))
        txt = {1: "termina si {e} sigue activo {s} s",
               2: "sigue activo {s} s despues de que {e} se apaga",
               3: "pulso de {s} s cada vez que se activa {e}",
               4: "termina {s} s despues de que se activa {e}"}.get(tipo, "?")
        L.append(f"{t.get('id')} ({TIMER_NOMBRE.get(tipo, '?')}): "
                 + txt.format(e=_texto_senal(t.get("entrada")), s=t.get("segundos"))
                 + (f"; se reinicia con {_texto_senal(t['reset'])}" if t.get("reset") else "") + ".")
    for c in _lista(eng.get("contadores")):
        ciclo = COUNT_MODOS.get(_norm(c.get("modo") or "una_vez")) == 1
        L.append(f"{c.get('id')}: cuenta {_texto_senal(c.get('entrada'))} hasta {c.get('preset')}"
                 + (" y vuelve a empezar (pulso cada vez)" if ciclo else "")
                 + (f"; reset con {_texto_senal(c['reset'])}" if c.get("reset") else "") + ".")
    for mm in _lista(eng.get("memorias")):
        L.append(f"{mm.get('id')}: se activa con {_texto_senal(mm.get('set')) or '-'} y se "
                 f"desactiva con {_texto_senal(mm.get('reset')) or '-'}.")
    for k, v in (eng.get("salidas") or {}).items():
        if v:
            L.append(f"{k.replace('_', ' ')} ({SALIDA_FISICA.get(k, '?')}): con {_texto_senal(v)}.")
    seq = eng.get("secuencia")
    if isinstance(seq, dict) and seq.get("pasos"):
        ciclo = SEQ_MODOS.get(_norm(seq.get("modo") or "una_vez")) == 1
        L.append(f"Secuencia de {len(seq['pasos'])} pasos: inicia con {_texto_senal(seq.get('inicio'))}"
                 + (", se repite" if ciclo else "")
                 + (f", se detiene con {_texto_senal(seq['paro'])}" if seq.get("paro") else "")
                 + (f", reinicia con {_texto_senal(seq['reset'])}" if seq.get("reset") else "") + ".")
        for i, p in enumerate(seq["pasos"]):
            acc = ", ".join(a.replace("_", " ") for a in _acciones_paso(p.get("acciones"), "", [])) or "nada"
            av = _norm(p.get("avanza") or ("flanco" if p.get("senal") else "tiempo"))
            cuando = {"tiempo": f"a los {p.get('segundos')} s",
                      "senal": f"cuando {_texto_senal(p.get('senal'))} este activo",
                      "nivel": f"cuando {_texto_senal(p.get('senal'))} este activo",
                      "flanco": f"cuando se active {_texto_senal(p.get('senal'))}",
                      "tiempo_y_senal": f"a los {p.get('segundos')} s si {_texto_senal(p.get('senal'))}",
                      "senal_o_tiempo": f"con {_texto_senal(p.get('senal'))} o a los {p.get('segundos')} s"}.get(av, av)
            sig = f" -> paso {p['siguiente']}" if p.get("siguiente") else ""
            L.append(f"  Paso {i + 1}: {acc}; avanza {cuando}{sig}.")
    op = eng.get("opciones") or {}
    if op.get("pluma_max_s"):
        L.append(f"Plumas: se cortan tras {op['pluma_max_s']} s moviendose.")
    if op.get("luces_paro"):
        L.append(f"Con el paro I3: enciende {', '.join(_lista(op['luces_paro']))}.")
    return L


def estado_desde_es(es: list) -> dict:
    """ES[0..39] leido del PLC -> dict legible para el panel."""
    if not es or len(es) < STATUS_LEN:
        return {}
    v = es[S_VERSION]
    if v != ENGINE_VERSION:
        return {"version": v, "disponible": False}
    err = es[S_ERROR]

    def bits(m, n):
        return [bool((m >> i) & 1) for i in range(n)]

    out = bits(es[S_OUT_MASK], 7)
    return {
        "version": v, "disponible": True,
        "valido": es[S_VALID] == 1,
        "error": campo_de_offset(err - 1) if err else "",
        "motor": MOTOR_ESTADO.get(es[S_MOTOR], str(es[S_MOTOR])),
        "direccion": es[S_DIR], "direccion_objetivo": es[S_DIR_TARGET],
        "enclavado": es[S_RUN_LATCH] == 1,
        "secuencia_activa": es[S_SEQ_ACTIVE] == 1,
        "paso": es[S_SEQ_STEP], "paso_tiempo_s": es[S_SEQ_ACC] / 10,
        "condiciones": bits(es[S_COND_MASK], N_COND),
        "timers": bits(es[S_TIMER_MASK], N_TIMER),
        "contadores": bits(es[S_COUNT_MASK], N_COUNT),
        "memorias": bits(es[S_MEM_MASK], N_MEM),
        "timers_s": [x / 10 for x in es[S_TIMER_ACC:S_TIMER_ACC + N_TIMER]],
        "contadores_acc": list(es[S_COUNT_ACC:S_COUNT_ACC + N_COUNT]),
        "salidas": {k: out[i] for i, k in enumerate(SALIDAS)},
        "entradas": dict(zip(("I1", "I2", "I3", "S1", "S2"), bits(es[S_IN_MASK], 5))),
        "falla_plumas": es[S_PLUMA_FAULT],
    }


# ---------------------------------------------------------------------------
# RESPUESTA DEL LLM -> engine_config
# ---------------------------------------------------------------------------
CLAVES_MOTOR = ("motor", "condiciones", "timers", "contadores", "memorias",
                "salidas", "secuencia", "opciones")


def _vacio(v) -> bool:
    if v is None or v == [] or v == {}:
        return True
    if isinstance(v, dict):
        return all(_vacio(x) for x in v.values())
    return False


def cfg_desde_respuesta(resp) -> dict:
    """JSON del LLM (SYSTEM_PROMPT_BANDA_MOTOR) -> engine_config de la banda.

    {"name", "banda": {mover, direccion, frecuencia_hz, paros}, motor, ...}
      -> {"name", "device": "banda", "outputs": [], "no_soportado": [...],
          "band": {"enable", "direction", "freq_hz", "stop_mode", "engine": {...}}}"""
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
    paros = b.get("paros") or {}
    band["stop_mode"] = (1 if paros.get("i2") else 0) + (2 if paros.get("software") else 0)
    eng = {}
    for k in CLAVES_MOTOR:
        v = resp.get(k)
        if k == "salidas" and isinstance(v, dict):
            v = {s: x for s, x in v.items() if x not in (None, "")}
        if k == "motor" and isinstance(v, dict):
            v = {s: x for s, x in v.items() if x not in (None, "")}
        if not _vacio(v):
            eng[k] = v
    rearmar_plumas(eng)
    band["engine"] = eng
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
