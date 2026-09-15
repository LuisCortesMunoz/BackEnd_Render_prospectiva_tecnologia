"""
============================================================================
INTENCION ESTRUCTURADA DE LA BANDA  (LLM -> normalizador -> configuracion)
============================================================================

Flujo de una instruccion de la banda:

    texto -> LLM -> INTENCION (eventos y acciones, sin registros)
          -> revisar_contra_texto()  red heuristica: ¿el LLM leyo todo?
          -> normalizar_intencion()  bloque 'band' con los campos del ST
          -> errores_cobertura()     ninguna accion pedida desaparecio
          -> plc_banda.validar_config()  compatible con el ST
          -> preview / confirmacion -> plc_banda.plan_config() -> Modbus

El LLM ENTIENDE la instruccion: que evento dispara que, que acciones son
simultaneas, tiempos, conteos y si la banda se mueve. Este modulo NO
interpreta lenguaje natural: traduce la intencion ya estructurada a los
campos del programa maestro ST y comprueba que no se perdio nada. La unica
lectura del texto (revisar_contra_texto) sirve para pedirle al LLM que
revise su propia intencion; nunca decide la configuracion.

Forma de la intencion (la pide SYSTEM_PROMPT_BANDA en app.py):

    {
      "movimiento": {"mover": bool, "direccion": "derecha"|"izquierda"|null,
                     "frecuencia_hz": int|null, "boton_inicio": "I1"|null,
                     "paro_automatico": {"segundos": int,
                                         "cuenta": "movimiento"|"total"} | null},
      "paros": {"i2": bool, "software": bool},
      "eventos": [{"sensor": 1|2, "conteo": int|null,
                   "banda": "no_afecta"|"pausa_mientras_detecta"|"pausa_temporizada",
                   "duracion_s": int|null, "luces": ["verde"|"amarilla"|"roja"],
                   "pluma1": "subir"|"bajar"|"stop"|null, "pluma2": ...,
                   "al_contar": null | {"pausar_banda": bool, "detener_proceso": bool,
                                        "luces": [...], "direccion": "derecha"|"izquierda"|"invertir"|null,
                                        "pluma1": ..., "pluma2": ..., "duracion_s": int|null}}],
      # ST v5: luces/plumas/banda del evento ocurren en CADA deteccion; "al_contar" es
      # una segunda capa al llegar a "conteo" (duracion_s null = enclavadas).
      "luces": {"corriendo": [...], "detenida": [...], "mientras_i1": [...]},
      "luz_temporizada": null | {"luces": [...], "segundos": int},
      "plumas_manual": {"pluma1": "subir"|"bajar"|"stop"|null, "pluma2": ...},
      "no_soportado": ["..."]
    }
============================================================================
"""

import json
import re
import unicodedata


# Campos del bloque 'band' canonico (mismo contrato que plc_banda.validar_config
# y canonicalBand del frontend).
CAMPOS_BAND = (
    "enable", "direction", "freq_hz", "start_button",
    "stop_mode", "auto_stop_mode", "auto_stop_s",
    "s1_action", "s1_band_mode", "wait_s1_s", "count_s1", "torreta_s1", "s1_pluma1", "s1_pluma2",
    "s2_action", "s2_band_mode", "wait_s2_s", "count_s2", "torreta_s2", "s2_pluma1", "s2_pluma2",
    "torreta_run", "torreta_idle", "torreta_i1", "pluma1", "pluma2",
    # Acciones enclavadas al alcanzar el conteo (§14c, %R70..%R79).
    "s1_count_action_mask", "s1_count_lamp_mask", "s1_count_dir", "s1_count_pluma1", "s1_count_pluma2",
    "s2_count_action_mask", "s2_count_lamp_mask", "s2_count_dir", "s2_count_pluma1", "s2_count_pluma2",
    # Duracion de las acciones del contador (%R88/%R89, %R92/%R93) y lampara
    # temporizada independiente (%R96/%R97).
    "s1_count_hold_s", "s2_count_hold_s", "timed_lamp_mask", "timed_lamp_s",
)

# Bits de S_CountActionMask (%R70/%R75): se suman para combinar acciones.
CONTAR_BITS = {"pausar_banda": 1, "detener_proceso": 2, "luces": 4, "direccion": 8,
               "pluma1": 16, "pluma2": 32}
CONTAR_DIR = {"derecha": 1, "izquierda": 2, "invertir": 3}
CONTAR_DIR_TXT = {v: k for k, v in CONTAR_DIR.items()}

COLOR_BIT = {"verde": 1, "amarilla": 2, "roja": 4}
_ALIAS_COLOR = {"verde": "verde", "amarilla": "amarilla", "amarillo": "amarilla",
                "ambar": "amarilla", "roja": "roja", "rojo": "roja",
                "q3": "verde", "q4": "amarilla", "q5": "roja"}
EFECTOS_BANDA = ("no_afecta", "pausa_mientras_detecta", "pausa_temporizada")
PLUMA_EVENTO = {"subir": 1, "bajar": 2, "stop": 3}      # %R28/%R29/%R38/%R39
PLUMA_MANUAL = {"stop": 0, "subir": 1, "bajar": 2}      # %R60/%R61
PLUMA_EVENTO_TXT = {v: k for k, v in PLUMA_EVENTO.items()}
PLUMA_MANUAL_TXT = {v: k for k, v in PLUMA_MANUAL.items()}
CUENTA_AUTO = {"movimiento": 1, "total": 2}             # %R15
CUENTA_AUTO_TXT = {v: k for k, v in CUENTA_AUTO.items()}
LUCES_ESTADO = {"corriendo": "torreta_run", "detenida": "torreta_idle", "mientras_i1": "torreta_i1"}


def _norm(s) -> str:
    t = unicodedata.normalize("NFD", str(s or "").strip().lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").replace(" ", "_")


def _entero(v):
    """Entero o None. Un valor no numerico tambien da None (validar_intencion
    ya lo reporta)."""
    if v is None or isinstance(v, bool) or v == "":
        return None
    try:
        return int(round(float(str(v).replace(",", "."))))
    except (TypeError, ValueError):
        return None


def _direccion(v):
    t = _norm(v)
    if t in ("izquierda", "izq", "left", "2", "direccion_2"):
        return "izquierda"
    if t in ("derecha", "der", "right", "1", "direccion_1"):
        return "derecha"
    return None


def _boton(v):
    t = str(v or "").strip().upper().replace(" ", "")
    return t or None


def _colores(lista) -> list:
    if isinstance(lista, str):
        lista = [lista]
    out = []
    for c in lista or []:
        k = _ALIAS_COLOR.get(_norm(c))
        if k and k not in out:
            out.append(k)
    return out


def _mascara(lista) -> int:
    return sum(COLOR_BIT[c] for c in _colores(lista))


def _colores_de_mascara(m) -> list:
    m = _entero(m) or 0
    return [c for c, bit in COLOR_BIT.items() if m & bit]


def _botones_inicio() -> tuple:
    try:
        import plc_banda
        return plc_banda.START_BUTTONS
    except Exception:
        return ("I1",)


# ---------------------------------------------------------------------------
# LECTURA DE LA RESPUESTA DEL LLM
# ---------------------------------------------------------------------------
def extraer_intencion(respuesta) -> dict:
    """Acepta {"name", "intencion": {...}} o la intencion suelta."""
    if not isinstance(respuesta, dict):
        return {}
    intent = respuesta.get("intencion")
    if isinstance(intent, dict):
        intent = dict(intent)
        intent.setdefault("name", respuesta.get("name"))
        return intent
    return respuesta


def validar_intencion(intent) -> list:
    """Errores de FORMA de la intencion (no de compatibilidad con el ST)."""
    if not isinstance(intent, dict) or not intent:
        return ["La respuesta no trae la intencion estructurada ('intencion')."]
    errores = []
    mov = intent.get("movimiento")
    if mov is not None and not isinstance(mov, dict):
        errores.append("'movimiento' debe ser un objeto.")
        mov = {}
    mov = mov or {}
    if mov.get("direccion") is not None and _direccion(mov["direccion"]) is None:
        errores.append(f"movimiento.direccion='{mov['direccion']}' debe ser 'derecha' o 'izquierda'.")
    if mov.get("frecuencia_hz") is not None and _entero(mov["frecuencia_hz"]) is None:
        errores.append(f"movimiento.frecuencia_hz='{mov['frecuencia_hz']}' debe ser un entero.")
    pa = mov.get("paro_automatico")
    if pa is not None:
        if not isinstance(pa, dict) or not (_entero(pa.get("segundos")) or 0) > 0:
            errores.append("movimiento.paro_automatico necesita 'segundos' mayor que 0.")
        elif _norm(pa.get("cuenta") or "movimiento") not in CUENTA_AUTO:
            errores.append("movimiento.paro_automatico.cuenta debe ser 'movimiento' o 'total'.")

    eventos = intent.get("eventos") or []
    if not isinstance(eventos, list):
        errores.append("'eventos' debe ser una lista.")
        eventos = []
    for i, ev in enumerate(eventos, 1):
        tag = f"eventos[{i}]"
        if not isinstance(ev, dict):
            errores.append(f"{tag} no es un objeto.")
            continue
        if _entero(ev.get("sensor")) not in (1, 2):
            errores.append(f"{tag}.sensor='{ev.get('sensor')}': solo existen el sensor 1 y el 2.")
        efecto = _norm(ev.get("banda") or "no_afecta")
        if efecto not in EFECTOS_BANDA:
            errores.append(f"{tag}.banda='{ev.get('banda')}' debe ser uno de {list(EFECTOS_BANDA)}.")
        dur = ev.get("duracion_s")
        if dur is not None and not (_entero(dur) or 0) > 0:
            errores.append(f"{tag}.duracion_s='{dur}' debe ser mayor que 0 o null.")
        if efecto == "pausa_temporizada" and not (_entero(dur) or 0) > 0:
            errores.append(f"{tag}: una pausa temporizada necesita duracion_s mayor que 0.")
        if efecto == "pausa_mientras_detecta" and dur is not None:
            errores.append(f"{tag}: 'pausa_mientras_detecta' no lleva duracion; si hay "
                           f"segundos usa 'pausa_temporizada'.")
        if ev.get("conteo") is not None and (_entero(ev.get("conteo")) is None or _entero(ev["conteo"]) < 0):
            errores.append(f"{tag}.conteo='{ev.get('conteo')}' debe ser un entero >= 0 o null.")
        for c in (ev.get("luces") or []):
            if _ALIAS_COLOR.get(_norm(c)) is None:
                errores.append(f"{tag}.luces: '{c}' no es un color de la torreta (verde, amarilla, roja).")
        for m in (1, 2):
            p = ev.get(f"pluma{m}")
            if p is not None and _norm(p) not in PLUMA_EVENTO:
                errores.append(f"{tag}.pluma{m}='{p}' debe ser 'subir', 'bajar', 'stop' o null.")
        ac = ev.get("al_contar")
        if ac is not None:
            if not isinstance(ac, dict):
                errores.append(f"{tag}.al_contar debe ser un objeto o null.")
                continue
            if ac.get("direccion") is not None and _norm(ac["direccion"]) not in CONTAR_DIR:
                errores.append(f"{tag}.al_contar.direccion='{ac['direccion']}' debe ser 'derecha', "
                               f"'izquierda', 'invertir' o null.")
            for c in (ac.get("luces") or []):
                if _ALIAS_COLOR.get(_norm(c)) is None:
                    errores.append(f"{tag}.al_contar.luces: '{c}' no es un color de la torreta.")
            for m in (1, 2):
                p = ac.get(f"pluma{m}")
                if p is not None and _norm(p) not in PLUMA_EVENTO:
                    errores.append(f"{tag}.al_contar.pluma{m}='{p}' debe ser 'subir', 'bajar', 'stop' o null.")
            d = ac.get("duracion_s")
            if d is not None and not (_entero(d) or 0) > 0:
                errores.append(f"{tag}.al_contar.duracion_s='{d}' debe ser mayor que 0 o null.")

    luces = intent.get("luces") or {}
    if not isinstance(luces, dict):
        errores.append("'luces' debe ser un objeto con 'corriendo', 'detenida' y 'mientras_i1'.")
    else:
        for estado, lista in luces.items():
            if estado not in LUCES_ESTADO:
                errores.append(f"luces.{estado}: usa 'corriendo', 'detenida' o 'mientras_i1'.")
            for c in (lista or []):
                if _ALIAS_COLOR.get(_norm(c)) is None:
                    errores.append(f"luces.{estado}: '{c}' no es un color de la torreta.")
    lt = intent.get("luz_temporizada")
    if lt is not None:
        if not isinstance(lt, dict) or not _colores(lt.get("luces")):
            errores.append("luz_temporizada necesita al menos una luz (verde, amarilla o roja).")
        elif not (_entero(lt.get("segundos")) or 0) > 0:
            errores.append("luz_temporizada.segundos debe ser mayor que 0.")
    pm = intent.get("plumas_manual") or {}
    if not isinstance(pm, dict):
        errores.append("'plumas_manual' debe ser un objeto.")
    else:
        for m in (1, 2):
            p = pm.get(f"pluma{m}")
            if p is not None and _norm(p) not in PLUMA_MANUAL:
                errores.append(f"plumas_manual.pluma{m}='{p}' debe ser 'subir', 'bajar', 'stop' o null.")
    if not isinstance(intent.get("no_soportado") or [], list):
        errores.append("'no_soportado' debe ser una lista de textos.")
    return errores


# ---------------------------------------------------------------------------
# NORMALIZADOR: intencion -> bloque 'band' del ST
# ---------------------------------------------------------------------------
def normalizar_intencion(intent) -> tuple:
    """Devuelve (band, errores). No descarta ninguna accion: lo que el ST no
    puede representar se reporta como error en vez de omitirse.

    Mapeo de un evento de sensor (§11..§14 del ST):
        banda                     -> S_BandMode (%R16/%R17)   S_Action
        pausa_mientras_detecta    -> 0 (puede pausar)          1 (3 con luces)
        pausa_temporizada         -> 0                         2 (4 con luces)
        no_afecta sin duracion    -> 1 (solo evento)           0
        no_afecta con duracion    -> 1                         2 (4 con luces)
    La torreta y las plumas del sensor actuan mientras dura el evento, con
    cualquier accion."""
    errores = []
    band = {k: None for k in CAMPOS_BAND}
    mov = intent.get("movimiento") or {}
    mover = bool(mov.get("mover"))
    band["enable"] = mover

    if mover:
        band["direction"] = _direccion(mov.get("direccion")) or "derecha"
        band["freq_hz"] = _entero(mov.get("frecuencia_hz"))
        boton = _boton(mov.get("boton_inicio")) or "I1"
        permitidos = _botones_inicio()
        if boton not in permitidos:
            errores.append(f"El programa maestro de la banda solo arranca con "
                           f"{' o '.join(permitidos)}; no puede iniciar con {boton}.")
        band["start_button"] = boton
    elif _boton(mov.get("boton_inicio")):
        errores.append(f"Se pidio iniciar con {_boton(mov.get('boton_inicio'))}, pero la "
                       f"instruccion no mueve la banda: el boton de inicio solo arranca el movimiento.")

    pa = mov.get("paro_automatico")
    if pa:
        if not mover:
            errores.append("El paro automatico por tiempo necesita que la banda se mueva.")
        band["auto_stop_s"] = _entero(pa.get("segundos"))
        band["auto_stop_mode"] = CUENTA_AUTO.get(_norm(pa.get("cuenta") or "movimiento"), 1)

    paros = intent.get("paros") or {}
    stop_mode = (1 if paros.get("i2") else 0) + (2 if paros.get("software") else 0)
    band["stop_mode"] = stop_mode or None

    # Eventos agrupados por sensor: el ST guarda UNA configuracion por sensor.
    por_sensor = {}
    for ev in intent.get("eventos") or []:
        n = _entero(ev.get("sensor"))
        if n in (1, 2):
            por_sensor.setdefault(n, []).append(ev)
    for n, evs in sorted(por_sensor.items()):
        campos, mascara = {}, 0

        def fijar(clave, valor, texto):
            if valor is None:
                return
            if clave in campos and campos[clave] != valor:
                errores.append(f"S{n}: {texto} tiene dos valores distintos ({campos[clave]} y "
                               f"{valor}); el PLC solo guarda una configuracion por sensor.")
                return
            campos[clave] = valor

        contar_bits, contar_luces = 0, 0
        for ev in evs:
            fijar("efecto", _norm(ev.get("banda") or "no_afecta"), "el efecto sobre la banda")
            fijar("duracion", _entero(ev.get("duracion_s")), "la duracion del evento")
            fijar("conteo", _entero(ev.get("conteo")) or None, "el conteo")
            for m in (1, 2):
                p = _norm(ev.get(f"pluma{m}"))
                fijar(f"pluma{m}", PLUMA_EVENTO.get(p) if p else None, f"la pluma {m}")
            mascara |= _mascara(ev.get("luces"))
            # Acciones enclavadas al alcanzar el conteo (§14c).
            ac = ev.get("al_contar") if isinstance(ev.get("al_contar"), dict) else {}
            if ac.get("pausar_banda") or ac.get("detener_banda"):
                contar_bits |= CONTAR_BITS["pausar_banda"]
            if ac.get("detener_proceso"):
                contar_bits |= CONTAR_BITS["detener_proceso"]
            contar_luces |= _mascara(ac.get("luces"))
            fijar("contar_hold", _entero(ac.get("duracion_s")) or None,
                  "la duracion de las acciones al contar")
            d = _norm(ac.get("direccion"))
            fijar("contar_dir", CONTAR_DIR.get(d) if d else None, "la direccion al contar")
            for m in (1, 2):
                p = _norm(ac.get(f"pluma{m}"))
                fijar(f"contar_pluma{m}", PLUMA_EVENTO.get(p) if p else None, f"la pluma {m} al contar")

        efecto = campos.get("efecto", "no_afecta")
        duracion = campos.get("duracion")
        if efecto == "pausa_temporizada" and not duracion:
            errores.append(f"S{n}: la pausa temporizada necesita una duracion en segundos.")
        if efecto == "pausa_mientras_detecta" and duracion:
            errores.append(f"S{n}: 'pausa mientras detecta' no admite duracion ({duracion} s).")
        pausa = efecto != "no_afecta"
        temporizado = bool(duracion) and efecto != "pausa_mientras_detecta"
        if temporizado:
            accion = 4 if mascara else 2
        elif pausa:
            accion = 3 if mascara else 1
        else:
            accion = 0
        band[f"s{n}_action"] = accion
        band[f"s{n}_band_mode"] = 0 if pausa else 1
        band[f"wait_s{n}_s"] = duracion if temporizado else None
        band[f"count_s{n}"] = campos.get("conteo")
        band[f"torreta_s{n}"] = mascara or None
        band[f"s{n}_pluma1"] = campos.get("pluma1")
        band[f"s{n}_pluma2"] = campos.get("pluma2")

        if contar_luces:
            contar_bits |= CONTAR_BITS["luces"]
        if campos.get("contar_dir"):
            contar_bits |= CONTAR_BITS["direccion"]
            if not mover:
                errores.append(f"S{n}: cambiar la direccion al contar necesita que la banda se mueva.")
        for m in (1, 2):
            if campos.get(f"contar_pluma{m}"):
                contar_bits |= CONTAR_BITS[f"pluma{m}"]
        if contar_bits and not campos.get("conteo"):
            errores.append(f"S{n}: las acciones al llegar al conteo necesitan un conteo mayor que 0.")
        band[f"s{n}_count_action_mask"] = contar_bits or None
        band[f"s{n}_count_lamp_mask"] = contar_luces or None
        band[f"s{n}_count_dir"] = campos.get("contar_dir")
        band[f"s{n}_count_pluma1"] = campos.get("contar_pluma1")
        band[f"s{n}_count_pluma2"] = campos.get("contar_pluma2")
        if campos.get("contar_hold") and not contar_bits:
            errores.append(f"S{n}: se dio una duracion al contar pero ninguna accion al llegar al conteo.")
        band[f"s{n}_count_hold_s"] = campos.get("contar_hold") if contar_bits else None

    luces = intent.get("luces") or {}
    for estado, campo in LUCES_ESTADO.items():
        band[campo] = _mascara(luces.get(estado)) or None

    lt = intent.get("luz_temporizada") if isinstance(intent.get("luz_temporizada"), dict) else {}
    band["timed_lamp_mask"] = _mascara(lt.get("luces")) or None
    band["timed_lamp_s"] = _entero(lt.get("segundos")) if band["timed_lamp_mask"] else None

    pm = intent.get("plumas_manual") or {}
    for m in (1, 2):
        p = _norm(pm.get(f"pluma{m}"))
        band[f"pluma{m}"] = PLUMA_MANUAL.get(p) if p else None

    for texto in intent.get("no_soportado") or []:
        errores.append(f"No lo soporta el programa maestro de la banda: {texto}")
    return band, errores


# ---------------------------------------------------------------------------
# COBERTURA: intencion vs configuracion normalizada
# ---------------------------------------------------------------------------
def acciones_de_intencion(intent) -> set:
    """Acciones atomicas que pidio el usuario, segun la intencion del LLM."""
    A = set()
    mov = intent.get("movimiento") or {}
    if mov.get("mover"):
        A.add(("banda", "direccion", _direccion(mov.get("direccion")) or "derecha"))
        if _entero(mov.get("frecuencia_hz")):
            A.add(("banda", "frecuencia", _entero(mov.get("frecuencia_hz"))))
        A.add(("banda", "inicio", _boton(mov.get("boton_inicio")) or "I1"))
    pa = mov.get("paro_automatico")
    if pa:
        A.add(("banda", "paro_automatico", _entero(pa.get("segundos")),
               _norm(pa.get("cuenta") or "movimiento")))
    paros = intent.get("paros") or {}
    if paros.get("i2"):
        A.add(("paro", "I2"))
    if paros.get("software"):
        A.add(("paro", "software"))
    for ev in intent.get("eventos") or []:
        n = _entero(ev.get("sensor"))
        if n not in (1, 2):
            continue
        s = f"S{n}"
        efecto = _norm(ev.get("banda") or "no_afecta")
        dur = None if efecto == "pausa_mientras_detecta" else _entero(ev.get("duracion_s"))
        A.add((s, "banda", efecto, dur))
        if _entero(ev.get("conteo")):
            A.add((s, "conteo", _entero(ev.get("conteo"))))
        for c in _colores(ev.get("luces")):
            A.add((s, "luz", c))
        for m in (1, 2):
            p = _norm(ev.get(f"pluma{m}"))
            if p:
                A.add((s, f"pluma{m}", p))
        ac = ev.get("al_contar") if isinstance(ev.get("al_contar"), dict) else {}
        if ac.get("pausar_banda") or ac.get("detener_banda"):
            A.add((s, "al_contar", "pausar_banda"))
        if ac.get("detener_proceso"):
            A.add((s, "al_contar", "detener_proceso"))
        for c in _colores(ac.get("luces")):
            A.add((s, "al_contar_luz", c))
        if _norm(ac.get("direccion")):
            A.add((s, "al_contar_direccion", _norm(ac.get("direccion"))))
        for m in (1, 2):
            p = _norm(ac.get(f"pluma{m}"))
            if p:
                A.add((s, f"al_contar_pluma{m}", p))
        if _entero(ac.get("duracion_s")) and any(
                ac.get(k) for k in ("pausar_banda", "detener_banda", "detener_proceso", "luces",
                                    "direccion", "pluma1", "pluma2")):
            A.add((s, "al_contar_duracion", _entero(ac.get("duracion_s"))))
    luces = intent.get("luces") or {}
    for estado in LUCES_ESTADO:
        for c in _colores(luces.get(estado)):
            A.add((f"luces_{estado}", "luz", c))
    lt = intent.get("luz_temporizada") if isinstance(intent.get("luz_temporizada"), dict) else {}
    for c in _colores(lt.get("luces")):
        A.add(("luz_temporizada", c, _entero(lt.get("segundos"))))
    pm = intent.get("plumas_manual") or {}
    for m in (1, 2):
        p = _norm(pm.get(f"pluma{m}"))
        if p:
            A.add(("manual", f"pluma{m}", p))
    return A


def acciones_de_band(band) -> set:
    """Acciones atomicas que realmente quedaron en la configuracion del ST."""
    A = set()
    b = band or {}
    if b.get("enable"):
        A.add(("banda", "direccion", _direccion(b.get("direction")) or "derecha"))
        if _entero(b.get("freq_hz")):
            A.add(("banda", "frecuencia", _entero(b.get("freq_hz"))))
        A.add(("banda", "inicio", _boton(b.get("start_button")) or "I1"))
    am = _entero(b.get("auto_stop_mode"))
    if am:
        A.add(("banda", "paro_automatico", _entero(b.get("auto_stop_s")), CUENTA_AUTO_TXT.get(am, am)))
    sm = _entero(b.get("stop_mode")) or 0
    if sm & 1:
        A.add(("paro", "I2"))
    if sm & 2:
        A.add(("paro", "software"))
    for n in (1, 2):
        acc = _entero(b.get(f"s{n}_action"))
        if acc is None:
            continue
        s = f"S{n}"
        modo = _entero(b.get(f"s{n}_band_mode")) or 0
        espera = _entero(b.get(f"wait_s{n}_s"))
        if modo == 1 or acc == 0:
            efecto, dur = "no_afecta", (espera if acc in (2, 4) else None)
        elif acc in (1, 3):
            efecto, dur = "pausa_mientras_detecta", None
        else:
            efecto, dur = "pausa_temporizada", espera
        A.add((s, "banda", efecto, dur))
        if _entero(b.get(f"count_s{n}")):
            A.add((s, "conteo", _entero(b.get(f"count_s{n}"))))
        for c in _colores_de_mascara(b.get(f"torreta_s{n}")):
            A.add((s, "luz", c))
        for m in (1, 2):
            p = _entero(b.get(f"s{n}_pluma{m}"))
            if p:
                A.add((s, f"pluma{m}", PLUMA_EVENTO_TXT.get(p, p)))
        # Acciones del contador: solo cuentan las que tienen su bit en la mascara,
        # porque el PLC ignora el resto.
        bits = _entero(b.get(f"s{n}_count_action_mask")) or 0
        for clave in ("pausar_banda", "detener_proceso"):
            if bits & CONTAR_BITS[clave]:
                A.add((s, "al_contar", clave))
        if bits & CONTAR_BITS["luces"]:
            for c in _colores_de_mascara(b.get(f"s{n}_count_lamp_mask")):
                A.add((s, "al_contar_luz", c))
        if bits & CONTAR_BITS["direccion"]:
            d = _entero(b.get(f"s{n}_count_dir"))
            A.add((s, "al_contar_direccion", CONTAR_DIR_TXT.get(d, d)))
        for m in (1, 2):
            p = _entero(b.get(f"s{n}_count_pluma{m}"))
            if bits & CONTAR_BITS[f"pluma{m}"] and p:
                A.add((s, f"al_contar_pluma{m}", PLUMA_EVENTO_TXT.get(p, p)))
        if bits and _entero(b.get(f"s{n}_count_hold_s")):
            A.add((s, "al_contar_duracion", _entero(b.get(f"s{n}_count_hold_s"))))
    for estado, campo in LUCES_ESTADO.items():
        for c in _colores_de_mascara(b.get(campo)):
            A.add((f"luces_{estado}", "luz", c))
    for c in _colores_de_mascara(b.get("timed_lamp_mask")):
        A.add(("luz_temporizada", c, _entero(b.get("timed_lamp_s"))))
    for m in (1, 2):
        p = _entero(b.get(f"pluma{m}"))
        if p is not None:
            A.add(("manual", f"pluma{m}", PLUMA_MANUAL_TXT.get(p, p)))
    return A


def texto_accion(a) -> str:
    """Accion atomica -> frase legible (errores, resumenes y chat)."""
    quien, que = a[0], a[1]
    if quien == "luz_temporizada":
        return f"luz {que} durante {a[2]} s"
    if quien == "banda":
        if que == "direccion":
            return f"banda avanza a la {a[2]}"
        if que == "frecuencia":
            return f"banda a {a[2]} Hz"
        if que == "inicio":
            return f"arranque con {a[2]}"
        return f"paro automatico a los {a[2]} s ({'tiempo total' if a[3] == 'total' else 'solo movimiento'})"
    if quien == "paro":
        return f"paro con {'I2' if que == 'I2' else 'el boton software'}"
    if quien.startswith("luces_"):
        estado = {"luces_corriendo": "con la banda corriendo", "luces_detenida": "con la banda detenida",
                  "luces_mientras_i1": "mientras I1 esta presionado"}[quien]
        return f"luz {a[2]} {estado}"
    if quien == "manual":
        return f"{que.replace('pluma', 'pluma ')} {a[2]}"
    if que == "banda":
        txt = {"no_afecta": "no afecta la banda", "pausa_mientras_detecta": "pausa la banda mientras detecta",
               "pausa_temporizada": "pausa la banda"}.get(a[2], a[2])
        return f"{quien}: {txt}" + (f" ({a[3]} s)" if a[3] else "")
    if que == "conteo":
        return f"{quien}: actua al contar {a[2]}"
    if que == "al_contar":
        return f"{quien} al llegar al conteo: " + ("pausa la banda" if a[2] == "pausar_banda"
                                                    else "detiene el proceso")
    if que == "al_contar_luz":
        return f"{quien} al llegar al conteo: enciende {a[2]}"
    if que == "al_contar_duracion":
        return f"{quien} al llegar al conteo: las acciones duran {a[2]} s"
    if que == "al_contar_direccion":
        return f"{quien} al llegar al conteo: direccion {a[2]}"
    if que.startswith("al_contar_pluma"):
        return f"{quien} al llegar al conteo: pluma {que[-1]} {a[2]}"
    if que == "luz":
        return f"{quien}: luz {a[2]}"
    return f"{quien}: {que.replace('pluma', 'pluma ')} {a[2]}"


def _orden(a):
    return tuple(str(x) for x in a)


def verificar_cobertura(intent, band) -> dict:
    pedidas = acciones_de_intencion(intent)
    quedaron = acciones_de_band(band)
    return {
        "faltantes": [texto_accion(a) for a in sorted(pedidas - quedaron, key=_orden)],
        "sobrantes": [texto_accion(a) for a in sorted(quedaron - pedidas, key=_orden)],
    }


def errores_cobertura(cfg) -> list:
    """Comprobacion final antes de escribir: si el programa trae la intencion
    del LLM, la configuracion debe contener EXACTAMENTE sus acciones."""
    intent = (cfg or {}).get("intent")
    if not isinstance(intent, dict):
        return []
    cob = verificar_cobertura(intent, (cfg or {}).get("band"))
    errores = []
    if cob["faltantes"]:
        errores.append("La configuracion perdio acciones pedidas: " + "; ".join(cob["faltantes"]) + ".")
    if cob["sobrantes"]:
        errores.append("La configuracion agrego acciones que no se pidieron: "
                       + "; ".join(cob["sobrantes"]) + ".")
    return errores


def resumen_acciones(intent) -> list:
    return [texto_accion(a) for a in sorted(acciones_de_intencion(intent), key=_orden)]


# ---------------------------------------------------------------------------
# RED HEURISTICA CONTRA EL TEXTO  (solo para la auto-revision del LLM)
# ---------------------------------------------------------------------------
_RE_SENSOR = re.compile(r"\b(?:s|sensor(?:es)?)\s*(?:numero\s*)?(1|2|uno|dos)\b")
_RE_PLUMA_N = re.compile(r"\bpluma\s*(?:numero\s*)?(1|2|uno|dos)\b")
_RE_AMBAS = re.compile(r"\b(?:las\s+dos|ambas|las)\s+plumas\b|\bplumas\s*1\s*y\s*2\b")
_NUM = {"1": 1, "2": 2, "uno": 1, "dos": 2}


def revisar_contra_texto(texto, intent) -> list:
    """Diferencias evidentes entre lo que dice el texto y la intencion: un
    sensor, pluma, color, tiempo, frecuencia o conteo mencionado que no
    aparece. No decide nada: los motivos se le devuelven al LLM."""
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    avisos = []
    eventos = [ev for ev in (intent.get("eventos") or []) if isinstance(ev, dict)]
    mov = intent.get("movimiento") or {}
    pm = intent.get("plumas_manual") or {}

    sensores = {_entero(ev.get("sensor")) for ev in eventos}
    mencionados = {_NUM[m] for m in _RE_SENSOR.findall(t)}
    if re.search(r"\bambos sensores\b|\blos dos sensores\b", t):
        mencionados |= {1, 2}
    for n in sorted(mencionados - sensores):
        avisos.append(f"El texto menciona el sensor {n}, pero la intencion no tiene ningun evento de S{n}.")

    lugares = [(f"S{_entero(ev.get('sensor'))}", ev.get("pluma1"), ev.get("pluma2")) for ev in eventos]
    # Las plumas y luces que quedan al llegar al conteo tambien cuentan como usadas.
    contar = [ev["al_contar"] for ev in eventos if isinstance(ev.get("al_contar"), dict)]
    lugares += [("al_contar", ac.get("pluma1"), ac.get("pluma2")) for ac in contar]
    lugares.append(("manual", pm.get("pluma1"), pm.get("pluma2")))
    usadas = {m for _, p1, p2 in lugares for m, p in ((1, p1), (2, p2)) if p}
    for m in sorted({_NUM[x] for x in _RE_PLUMA_N.findall(t)} - usadas):
        avisos.append(f"El texto menciona la pluma {m}, pero ninguna accion la usa.")
    ambas = len(_RE_AMBAS.findall(t))
    con_ambas = sum(1 for _, p1, p2 in lugares if p1 and p2)
    con_alguna = sum(1 for _, p1, p2 in lugares if p1 or p2)
    if ambas and con_ambas < min(ambas, max(con_alguna, 1)):
        avisos.append(f"El texto pide mover las dos plumas {ambas} vez(es), pero solo {con_ambas} "
                      f"evento(s) o comando(s) incluyen pluma1 Y pluma2.")

    if not re.search(r"\bapag|\bdesactiv|\bsin luz", t):
        mascara = 0
        for ev in eventos:
            mascara |= _mascara(ev.get("luces"))
        for ac in contar:
            mascara |= _mascara(ac.get("luces"))
        if isinstance(intent.get("luz_temporizada"), dict):
            mascara |= _mascara(intent["luz_temporizada"].get("luces"))
        for lista in (intent.get("luces") or {}).values():
            mascara |= _mascara(lista)
        for patron, color in ((r"\bverde", "verde"), (r"\bamarill|\bambar", "amarilla"), (r"\broj[ao]", "roja")):
            if re.search(patron, t) and not mascara & COLOR_BIT[color]:
                avisos.append(f"El texto menciona la luz {color}, pero ninguna accion la enciende.")

    if mov.get("mover"):
        d = _direccion(mov.get("direccion"))
        if re.search(r"\bizquierda\b", t) and d != "izquierda":
            avisos.append("El texto pide la direccion izquierda, pero la intencion no la usa.")
        if re.search(r"\bderecha\b", t) and d == "izquierda":
            avisos.append("El texto pide la direccion derecha, pero la intencion usa la izquierda.")
    elif re.search(r"\bizquierda\b|\bderecha\b|\d+\s*(?:hz|hertz)\b", t):
        avisos.append("El texto da direccion o frecuencia, pero la intencion no mueve la banda.")

    for hz in re.findall(r"(\d+)\s*(?:hz|hertz)\b", t):
        if mov.get("mover") and int(hz) != _entero(mov.get("frecuencia_hz")):
            avisos.append(f"El texto pide {hz} Hz, pero la intencion usa {mov.get('frecuencia_hz')}.")
    sin_hz = re.sub(r"\d+\s*(?:hz|hertz)\b", " ", t)

    tiempos = {_entero(ev.get("duracion_s")) for ev in eventos}
    tiempos |= {_entero(ac.get("duracion_s")) for ac in contar}
    if isinstance(intent.get("luz_temporizada"), dict):
        tiempos.add(_entero(intent["luz_temporizada"].get("segundos")))
    if isinstance(mov.get("paro_automatico"), dict):
        tiempos.add(_entero(mov["paro_automatico"].get("segundos")))
    pedidos = [int(s) for s in re.findall(r"(\d+)\s*(?:segundos?|segs?|s)\b", sin_hz)]
    pedidos += [int(m) * 60 for m in re.findall(r"(\d+)\s*min(?:utos?)?\b", sin_hz)]
    for s in pedidos:
        if s not in tiempos:
            avisos.append(f"El texto menciona {s} segundos, pero ninguna accion usa ese tiempo.")

    conteos = {_entero(ev.get("conteo")) for ev in eventos}
    for c in re.findall(r"(\d+)\s*(?:piezas?|objetos?|detecciones|deteccion|cajas?|veces|productos?|paquetes?)", sin_hz):
        if int(c) not in conteos:
            avisos.append(f"El texto menciona un conteo de {c}, pero ningun evento lo usa.")
    return avisos


def pregunta_pendiente(intent):
    """Dato imprescindible que el usuario no dio: con movimiento, el ST exige
    la frecuencia (§3). No se inventa: se pregunta."""
    mov = intent.get("movimiento") or {}
    if mov.get("mover") and _entero(mov.get("frecuencia_hz")) is None:
        return {
            "slot": "frecuencia",
            "pregunta": "¿A qué frecuencia debe avanzar la banda (1 a 327 Hz)?",
            "opciones": ["20 Hz", "30 Hz", "40 Hz"],
        }
    return None


def intencion_json(intent) -> str:
    return json.dumps(intent, ensure_ascii=False)
