"""
comportamiento.py — ¿la logica HACE lo que pidio el usuario? (banda v7)

validar_logica_banda comprueba que el JSON se puede cargar; aqui se comprueba
el COMPORTAMIENTO: se corre el texto ST real del programa maestro v7 en el
simulador (lavo_sim, scans de 10 ms) y se prueban los comportamientos que se
pueden leer de las instrucciones de la sesion:

  arranca_con  "arranca con I1", "al presionar I1 avanza"
  para_con     "con I2 se detiene", "I2 la para"
  para_sola    "haz que se detenga por si sola", "que se pare sola a los 10 s",
               "que se detenga sola cuando S1 detecte"

  requisitos(textos)       -> lo que se puede probar (de todas las instrucciones)
  verificar(cfg, reqs)     -> [] si cumple; si no, que falla (en palabras)
  paro_solo_sin_condicion  -> pide paro automatico sin decir cuando (se pregunta)
  pregunta_paro_solo()     -> pregunta con opciones de un clic

Solo se prueba lo que el texto dice sin ambiguedad: un requisito que no se
reconoce no se inventa (el resto lo sigue revisando el validador de siempre).
"""

import re
import unicodedata

import lavo_motor as lm

# Tope de tiempo simulado por prueba (s): sin condicion dicha, la banda debe
# detenerse sola antes de esto.
MAX_SOLA_S = 45
# Tope de tiempo REAL para todas las pruebas de una respuesta (s): lo que no
# alcance a probarse se omite (no se reporta como falla).
PRESUPUESTO_S = 15
_ARRANQUE_S = 2.0       # marcha tras arrancar antes de probar
_FRENO_S = 1.5          # lo que tarda en verse el paro (rampa del VFD)

_NUM = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
        "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15, "veinte": 20, "treinta": 30}
_NUM_RX = r"\d+(?:[.,]\d+)?|" + "|".join(sorted(_NUM, key=len, reverse=True))


def _fold(texto) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t)


def _numero(s) -> float:
    s = str(s).replace(",", ".")
    return float(_NUM.get(s, s))


def _senal(s: str):
    s = (s or "").replace(" ", "")
    s = {"sensor1": "S1", "sensoruno": "S1", "primersensor": "S1", "sensor2": "S2", "sensordos": "S2",
         "segundosensor": "S2", "botonverde": "I1", "botonrojo": "I2", "verde": "I1", "rojo": "I2",
         "i4": "S1", "i5": "S2"}.get(s, s)
    s = s.upper()
    return s if re.fullmatch(r"[IS]\d", s) else None


# ── Lectura de las instrucciones ───────────────────────────────────────────
_VERBO_PARO = r"(?:deten\w*|deteng\w*|detien\w*|par[ae]\w*|frene\w*|fren[ae]\w*)"
_SOLA = (r"(?:por si (?:sola|solo|misma|mismo)|sola|solita|automatic\w*|en automatico|"
         r"sin (?:que (?:yo |nadie |el usuario )?(?:la |lo )?(?:toque|presione|pulse|apriete|haga nada|intervenga|"
         r"detenga)\w*|intervencion\w*|presionar|pulsar|tocar|apretar)(?: (?:nada|ningun boton|botones))?)")
_RX_PARO_SOLA = re.compile(rf"\b{_VERBO_PARO}\b[^.;]{{0,40}}?\b{_SOLA}|\b{_SOLA}\b[^.;]{{0,25}}?\b{_VERBO_PARO}\b|"
                           r"\bparo automatico\b|\bauto ?paro\b")
_RX_SIN_PARO_SOLA = re.compile(rf"\b(?:ya no|quita\w*|elimina\w*|sin)\b[^.;]{{0,30}}(?:paro automatico|"
                               rf"{_VERBO_PARO}[^.;]{{0,15}}\b(?:sola|por si sola))")
_RX_TIEMPO = re.compile(rf"\b(?:a los|despues de|tras|luego de|en|pasados?|al cabo de|cada|durante)?\s*"
                        rf"({_NUM_RX})\s*(s|seg|segs|segundos?|min|minutos?)\b")
_RX_SENSOR = re.compile(r"\b(?:cuando|al|en cuanto|si)\s+(?:el |la )?(?:sensor\s*)?(s ?1|s ?2|sensor (?:1|2|uno|dos)|"
                        r"primer sensor|segundo sensor)\b|\b(s ?1|s ?2|sensor (?:1|2|uno|dos))\s+(?:detect\w*|vea|lea)")
_RX_CONTEO = re.compile(rf"\b(?:cuente|contar|cuenta|cont\w*|llegue a|lleguen|despues de|al pasar|pasen)\s+"
                        rf"({_NUM_RX})\s+(?:cajas|piezas|objetos|productos|detecciones)\b")
# Entre el boton y el verbo no puede haber OTRO boton ("arranca con I1 y con I2 se detiene").
_SIN_OTRO = r"(?:(?!\b(?:con|i ?\d|s ?\d|boton|y|e|o)\b)[^.;,])"
_RX_PARO_BOTON = re.compile(rf"\b(?:con|al (?:presionar|pulsar|oprimir)|presionando|pulsando|cuando (?:presione|pulse))\s+"
                            rf"(?:el |la )?(?:boton )?(i ?[12]|boton rojo|boton verde|rojo|verde)\b{_SIN_OTRO}{{0,40}}?\b"
                            rf"{_VERBO_PARO}|\b(i ?[12])\s+(?:la |lo )?(?:detiene|para|apaga|frena)\b")
_RX_ARRANQUE_BOTON = re.compile(rf"\b(?:con|al (?:presionar|pulsar|oprimir)|presionando|pulsando|cuando (?:presione|pulse))\s+"
                                rf"(?:el |la )?(?:boton )?(i ?[12]|boton verde|boton rojo)\b{_SIN_OTRO}{{0,40}}?\b"
                                r"(?:arranc\w*|avanc\w*|avanz\w*|muev\w*|se pone en marcha)|"
                                r"\barranca\w* con (?:el |la )?(?:boton )?(i ?[12])\b")


# "Cuando S2 cuente 3 cajas prende la roja 5 s y que el contador se reinicie solo",
# "cada 3 piezas en S1 sube la pluma", "al llegar a 4 detecciones ...".
_NO_TIEMPO = r"(?!\s*(?:[.,]\d|s\b|seg|segundos?|min|minutos?|hz|hertz|%|veces por))"
_RX_CUENTA_N = re.compile(rf"\b(?:cuente\w*|contar|cuenta|cont[oae]\w*|llegu\w* a|llegar a|lleg[ao] a|acumul\w*|"
                          rf"complet\w*|registr\w*|cada|despues de|al pasar|pasen)\s+(?:a\s+)?({_NUM_RX})\b{_NO_TIEMPO}"
                          rf"(?:\s+(cajas|piezas|objetos|productos|detecciones|conteos|pulsos))?")
_RX_HAY_CONTEO = re.compile(r"\b(contador\w*|conteo\w*|cuent\w*|cont[ae]\w*|acumul\w*|detecciones|cajas|piezas|"
                            r"objetos|productos)\b")
# Se reinicia SOLO (vuelve a contar). "se reinicia con I2 / al presionar" es manual.
_RX_REINICIA_SOLO = re.compile(r"\b(?:reinici\w*|resete\w*|reestable\w*|vuelv\w* a (?:contar|cero|0|empezar|iniciar)|"
                               r"(?:empie|comien)\w* de nuevo|de nuevo|otra vez|nuevamente|se repit\w*|"
                               r"vuelv\w* a cont\w*)\b|\bcada (?=\d|un|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)")
_RX_REINICIA_MANUAL = re.compile(r"\b(?:reinici\w*|resete\w*)\b[^.;]{0,25}\b(?:con|al (?:presionar|pulsar|oprimir)|"
                                 r"presionando|pulsando|cuando (?:presione|pulse))\b")
_RX_SIN_CONTEO = re.compile(r"\b(?:quita\w*|elimina\w*|borra\w*|ya no)\b[^.;]{0,20}\b(?:el contador|contador|conteo|"
                            r"cuente|contar)\b")
_RX_SENSOR_CUENTA = re.compile(r"\b(s ?1|s ?2|sensor (?:1|2|uno|dos)|primer sensor|segundo sensor)\b")


def conteo_en(texto: str) -> dict:
    """{"conteo": N, "senal": S1|S2, "reinicia": bool} si el texto pide contar
    N objetos en un sensor (o {} si no lo dice sin ambiguedad)."""
    t = _fold(texto)
    m = _RX_CUENTA_N.search(t)
    if not m or not (m.group(2) or _RX_HAY_CONTEO.search(t)):
        return {}
    n = int(_numero(m.group(1)))
    if not 1 < n <= 20:
        return {}
    # El sensor que cuenta: el mas cercano antes de "cuente N" (o, si no, el primero que se nombre).
    previos = [x for x in _RX_SENSOR_CUENTA.finditer(t[:m.start()])]
    s = _senal((previos[-1] if previos else _RX_SENSOR_CUENTA.search(t) or m).group(1)) \
        if (previos or _RX_SENSOR_CUENTA.search(t)) else None
    if s not in ("S1", "S2"):
        return {}
    reinicia = bool(_RX_REINICIA_SOLO.search(t)) and not _RX_REINICIA_MANUAL.search(t)
    return {"conteo": n, "senal": s, "reinicia": reinicia}


_RX_CTX_BANDA = re.compile(r"\b(banda|cinta|transportadora|motor|hz|hertz)\b")
_RX_CTX_OTRO = re.compile(r"\b(luz|luces|lampara\w*|torreta|verde|amarill\w*|roj[ao]s?|pluma\w*|secuencia\w*|"
                          r"semaforo|q\d+)\b")


def _oraciones(texto: str) -> list:
    return [o for o in re.split(r"[.;\n]+", _fold(texto)) if o.strip()]


def condicion_paro_solo(texto: str) -> dict:
    """{"segundos"|"senal"|"conteo": ...} dicho en el texto (o {} si no lo dice)."""
    t = _fold(texto)
    m = _RX_CONTEO.search(t)
    if m:
        return {"conteo": int(_numero(m.group(1))), "senal": "S2" if "s1" not in t and "sensor 1" not in t else "S1"}
    m = _RX_SENSOR.search(t)
    if m:
        return {"senal": _senal(m.group(1) or m.group(2))}
    m = _RX_TIEMPO.search(t)
    if m:
        n = _numero(m.group(1))
        return {"segundos": n * 60 if m.group(2).startswith("min") else n}
    return {}


def pide_paro_solo(texto: str) -> bool:
    return bool(_RX_PARO_SOLA.search(_fold(texto)))


def requisitos(textos) -> list:
    """Comportamientos que se pueden probar, de la instruccion mas antigua a
    la mas reciente (la mas reciente manda: 'ya no se detenga sola' lo quita)."""
    reqs = []
    for texto in textos or []:
        # Conteo: se lee de la instruccion completa ("... al contar 3 prende la roja. Que el
        # contador se reinicie solo" son dos oraciones). La mas reciente manda.
        if _RX_SIN_CONTEO.search(_fold(texto)):
            reqs = [r for r in reqs if r["tipo"] != "conteo"]
        else:
            c = conteo_en(texto)
            if c:
                reqs = [r for r in reqs if r["tipo"] != "conteo"]
                reqs.append({"tipo": "conteo", **c, "texto": _fold(texto).strip()[:160]})
        for o in _oraciones(texto):
            # Solo oraciones sobre la BANDA: "al presionar I1 enciende la verde"
            # o "que la roja se detenga sola" hablan de luces, no del motor.
            banda = bool(_RX_CTX_BANDA.search(o))
            otro = bool(_RX_CTX_OTRO.search(re.sub(r"boton (?:verde|rojo)", "", o)))
            if not banda and otro:
                continue
            if _RX_SIN_PARO_SOLA.search(o):
                reqs = [r for r in reqs if r["tipo"] != "para_sola"]
                continue
            if not banda and not reqs:
                # sin sujeto ni requisitos previos de la banda: no se adivina
                if not _RX_PARO_SOLA.search(o):
                    continue
            if _RX_PARO_SOLA.search(o):
                # "no se detiene sola, revisa" sin condicion conserva la que ya se habia dicho
                cond = condicion_paro_solo(o) or next(
                    ({k: v for k, v in r.items() if k in ("segundos", "senal", "conteo")}
                     for r in reqs if r["tipo"] == "para_sola"), {})
                reqs = [r for r in reqs if r["tipo"] != "para_sola"]
                reqs.append({"tipo": "para_sola", **cond, "texto": o.strip()})
            for m in _RX_PARO_BOTON.finditer(o):
                s = _senal(m.group(1) or m.group(2))
                if s and not any(r["tipo"] == "para_con" and r["senal"] == s for r in reqs):
                    reqs.append({"tipo": "para_con", "senal": s, "texto": o.strip()})
            for m in _RX_ARRANQUE_BOTON.finditer(o):
                s = _senal(m.group(1) or m.group(2))
                if s:
                    reqs = [r for r in reqs if r["tipo"] != "arranca_con"]
                    reqs.append({"tipo": "arranca_con", "senal": s, "texto": o.strip()})
    # "al contar 3 cajas se detenga sola" ya lo prueba para_sola (con su conteo).
    if any(r["tipo"] == "para_sola" and r.get("conteo") for r in reqs):
        reqs = [r for r in reqs if r["tipo"] != "conteo"]
    return reqs


def de_aclaracion(acl: dict) -> dict:
    """Respuesta a la pregunta 'paro_solo' -> condicion."""
    v = (acl or {}).get("paro_solo") or {}
    return condicion_paro_solo(v.get("respuesta") or "") if v else {}


def de_sesion(historial, texto: str, acl: dict) -> list:
    """Requisitos de toda la sesion + la respuesta a 'paro_solo' si la hay."""
    reqs = requisitos(list(historial or []) + [texto])
    falla = _fold(((acl or {}).get("falla") or {}).get("respuesta"))
    if "sola" in falla and not any(r["tipo"] == "para_sola" for r in reqs):
        reqs.append({"tipo": "para_sola", "texto": "la banda no se detiene sola"})
    cond = de_aclaracion(acl)
    if cond:
        reqs = [r for r in reqs if r["tipo"] != "para_sola"]
        reqs.append({"tipo": "para_sola", **cond, "texto": acl["paro_solo"]["respuesta"]})
    return reqs


def paro_solo_sin_condicion(reqs: list, acl: dict) -> bool:
    """Se pide que la banda se detenga sola, pero nadie dijo cuando."""
    if "paro_solo" in (acl or {}):
        return False
    return any(r["tipo"] == "para_sola" and not any(k in r for k in ("segundos", "senal", "conteo"))
               for r in reqs)


def pregunta_paro_solo() -> dict:
    return {"id": "paro_solo", "campo": "paro_solo",
            "pregunta": "¿Cuándo debe detenerse la banda sola (sin presionar ningún botón)?",
            "opciones": ["A los 10 s de marcha", "A los 30 s de marcha", "Cuando S1 detecte",
                         "Cuando S2 detecte", "Al contar 3 cajas en S2"]}


def bloque_modelo(reqs: list) -> str:
    """Lo que el modelo debe cumplir (y que despues se prueba simulando)."""
    filas = []
    for r in reqs:
        if r["tipo"] == "para_sola":
            if r.get("conteo"):
                c = (f"al contar {r['conteo']} cajas en {r['senal']}: contador CTn de {r['senal']} (modo una_vez, "
                     f"preset {r['conteo']}, reset = la senal de arranque) y CTn en el paro del motor")
            elif r.get("senal"):
                c = f"cuando {r['senal']} detecte: {r['senal']} en el paro del motor"
            elif r.get("segundos"):
                c = (f"a los {_fmt(r['segundos'])} s de marcha: timer TON con entrada BANDA_CORRIENDO y "
                     f"{_fmt(r['segundos'])} s, y ese timer en el paro del motor")
            else:
                c = "sin que nadie presione un boton (timer TON de BANDA_CORRIENDO en el paro del motor)"
            filas.append(f"- La banda se DETIENE SOLA {c}. Conserva el paro manual que ya hubiera: si motor.paro "
                         f"ya tenia una senal (p. ej. I2), usa una condicion OR con las dos. Si la banda la mueve "
                         f"una secuencia, el paro automatico va en el \"paro\" de la secuencia.")
        elif r["tipo"] == "conteo":
            n, s = r["conteo"], r["senal"]
            if r.get("reinicia"):
                filas.append(
                    f"- Al contar {n} en {s} pasa lo pedido y el contador VUELVE A CERO SOLO (la siguiente vez "
                    f"tambien debe pasar al llegar otra vez a {n}, no antes). Receta probada en el PLC: contador "
                    f"{{\"entrada\":\"{s}\",\"preset\":{n},\"modo\":\"ciclico\"}} (pulso al llegar a {n} y vuelve "
                    f"a 0 solo) y la accion disparada por ese CTn: con duracion = timer PULSO de entrada CTn con "
                    f"esos segundos (o evento \"cuando\": CTn con duracion_s); detener la banda = CTn en el paro. "
                    f"Otra forma valida: modo una_vez con reset = el timer de la accion. NO uses reset SQ1_FIN si "
                    f"no hay secuencia, ni I2 salvo que el usuario lo pida.")
            else:
                filas.append(
                    f"- Al contar {n} en {s} pasa lo pedido. Contador {{\"entrada\":\"{s}\",\"preset\":{n},"
                    f"\"modo\":\"una_vez\"}} con reset = la senal de arranque (al arrancar de nuevo cuenta desde "
                    f"cero); NO uses reset SQ1_FIN si no hay secuencia.")
        elif r["tipo"] == "para_con":
            filas.append(f"- {r['senal']} detiene la banda.")
        elif r["tipo"] == "arranca_con":
            filas.append(f"- {r['senal']} arranca la banda.")
    if not filas:
        return ""
    return ("COMPORTAMIENTO QUE SE VA A PROBAR SIMULANDO EL PLC (tiene que cumplirse):\n" + "\n".join(filas))


def _fmt(x) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


# ── Prueba en el simulador ─────────────────────────────────────────────────
def _entradas_arranque(band) -> list:
    """Entradas a pulsar para arrancar la banda ([] = arranca sola al cargar)."""
    try:
        cfg = lm.compilar_motor(band)
    except ValueError:
        return ["I1"]
    if not cfg[lm.H_MOVE]:
        return []
    eng = lm.normalizar_motor((band or {}).get("engine") or {})
    origen = lm.nombre_senal(cfg[lm.M_RUN])
    if not origen:
        seq = next((s for s in lm._lista(eng.get("secuencias")) if isinstance(s, dict)
                    and any("marcha" in (p.get("motor") or []) for p in lm._lista(s.get("pasos"))
                            if isinstance(p, dict))), {})
        origen = seq.get("inicio")
    op, entradas, _auto = lm._fuentes_arranque(origen, eng, set())
    botones = [x for x in entradas if x in ("I1", "I2", "I4", "I5")]
    if op == "y":
        return botones
    return botones[:1]


def _sim(band):
    import lavo_sim
    s = lavo_sim.SimV7()
    err, campo = s.cargar({"band": band})
    if err:
        raise ValueError(f"el PLC simulado rechazo la configuracion (error {err}, campo {campo})")
    return s


def _arrancar(s, entradas):
    if not entradas:
        s.correr(0.5)
        return
    for e in entradas:
        s.poner(**{e: True})
    s.correr(0.3)
    for e in entradas:
        s.poner(**{e: False})


def _corre(s) -> bool:
    return s.motor() in ("dir1", "dir2")


def verificar(cfg: dict, reqs: list) -> list:
    """Problemas de comportamiento (lista vacia = cumple lo que se pudo probar)."""
    if not reqs:
        return []
    band = (cfg or {}).get("band") or {}
    if not ((band.get("engine") or {}).get("version") == 7 or lm.es_motor_v7(cfg or {})):
        return []
    problemas = []
    arranque = _entradas_arranque(band)
    pedido = next((r for r in reqs if r["tipo"] == "arranca_con"), None)
    if pedido:
        arranque = [pedido["senal"]]
    import time
    limite = time.monotonic() + PRESUPUESTO_S
    try:
        for r in reqs:
            if time.monotonic() > limite:
                break                     # sin tiempo: lo que falta no se reporta como falla
            s = _sim(band)
            if r["tipo"] == "conteo":
                # No exige que la banda corra (un conteo puede manejar solo luces o plumas).
                s._band_conteo = band
                _arrancar(s, arranque)
                s.correr(_ARRANQUE_S)
                problemas += _probar_conteo(s, r, arranque)
                continue
            if r["tipo"] == "arranca_con":
                _arrancar(s, [r["senal"]])
                s.correr(_ARRANQUE_S)
                if not _corre(s):
                    problemas.append(f"Al presionar {r['senal']} la banda no arranca.")
                continue
            _arrancar(s, arranque)
            s.correr(_ARRANQUE_S)
            if not _corre(s):
                problemas.append("La banda no arranca" + (f" con {', '.join(arranque)}" if arranque else "")
                                 + f", asi que no se puede comprobar: «{r['texto']}».")
                continue
            if r["tipo"] == "para_con":
                s.pulsar(r["senal"])
                s.correr(_FRENO_S)
                if _corre(s):
                    problemas.append(f"Al presionar {r['senal']} la banda NO se detiene.")
            elif r["tipo"] == "para_sola":
                problemas += _probar_para_sola(s, r)
    except ValueError as e:
        problemas.append(str(e))
    return problemas


_ESPERA_FIN_ACCION_S = 20     # lo maximo que se espera a que termine la accion del conteo


def _estado(s) -> set:
    """Lo observable: salidas encendidas y si la banda corre."""
    return set(s.salidas()) | {"BANDA_CORRIENDO" if _corre(s) else "BANDA_DETENIDA"}


def _ronda(s, sensor, n) -> list:
    """Pasa n objetos por el sensor; el estado despues de cada uno."""
    estados = []
    for _ in range(n):
        s.poner(**{sensor: True})
        s.correr(0.3)
        s.poner(**{sensor: False})
        s.correr(0.4)
        estados.append(_estado(s))
    return estados


def _contador_atorado(s, senal) -> bool:
    """Algun contador de ese sensor sigue activo (salida del contador en LSIG[88 + k])."""
    band = getattr(s, "_band_conteo", None) or {}
    eng = lm.normalizar_motor((band or {}).get("engine") or {})
    for c in lm._lista(eng.get("contadores")):
        if not isinstance(c, dict):
            continue
        try:
            k = int(str(c.get("id", "")).upper().replace("CT", "")) - 1
            if lm.codigo_senal(c.get("entrada")) == lm.codigo_senal(senal) and 0 <= k < 16 and s.var("LSIG", 88 + k):
                return True
        except (ValueError, TypeError):
            continue
    return False


def _probar_conteo(s, r, arranque) -> list:
    """Ronda 1: al objeto N cambia algo que no habia pasado con los anteriores.
    Ronda 2 (si se reinicia solo): cuando termina esa accion se vuelve a contar
    y ese cambio aparece OTRA VEZ al objeto N, no antes."""
    n, sensor = int(r["conteo"]), ("I4" if r["senal"] == "S1" else "I5")
    antes = [_estado(s)] + _ronda(s, sensor, n - 1)
    al_n = _ronda(s, sensor, 1)[0]
    cambio = al_n - set().union(*antes)
    if "BANDA_DETENIDA" in cambio and "BANDA_CORRIENDO" not in set().union(*antes):
        cambio.discard("BANDA_DETENIDA")          # nunca corrio: no es un efecto del conteo
    if not cambio:
        return [f"Al contar {n} en {r['senal']} no pasa nada (ninguna salida ni la banda cambian)."]
    if not r.get("reinicia"):
        return []
    # Esperar a que termine la accion; si detuvo la banda, se vuelve a arrancar.
    t = 0.0
    while cambio & _estado(s) - {"BANDA_DETENIDA"} and t < _ESPERA_FIN_ACCION_S:
        s.correr(0.5)
        t += 0.5
    if cambio & _estado(s) - {"BANDA_DETENIDA"}:
        # La accion no termina sola ("prende la roja" sin tiempo): no hay 2a vuelta que probar,
        # pero si el contador sigue ACTIVO (CTn en LSIG[88 + n-1]) es que nunca se reinicio.
        if _contador_atorado(s, r["senal"]):
            return [f"El contador no vuelve a cero solo: despues de llegar a {n} se queda activo y lo que "
                    f"enciende no se apaga (para reiniciarse solo usa modo ciclico, y la accion con su duracion "
                    f"como timer PULSO del contador)."]
        return []
    if "BANDA_DETENIDA" in cambio:
        _arrancar(s, arranque)
        s.correr(_ARRANQUE_S)
    ronda2 = _ronda(s, sensor, n)
    temprano = next((k + 1 for k, e in enumerate(ronda2[:-1]) if cambio & e - {"BANDA_DETENIDA"}
                     or ("BANDA_DETENIDA" in cambio and "BANDA_DETENIDA" in e)), None)
    if temprano:
        return [f"El contador no vuelve a cero solo: en la segunda vuelta la accion de llegar a {n} ya sale con "
                f"el objeto {temprano} (el reset del contador no se activa; sin secuencia no uses SQ1_FIN)."]
    if not cambio & ronda2[-1]:
        return [f"El contador no vuelve a cero solo: la segunda vez que llegan {n} objetos a {r['senal']} ya no "
                f"pasa nada (el contador se quedo arriba; para reiniciarse solo usa modo ciclico o reset = el "
                f"timer de la accion)."]
    return []


def _probar_para_sola(s, r) -> list:
    if r.get("conteo"):
        sensor = "I4" if r["senal"] == "S1" else "I5"
        for k in range(int(r["conteo"])):
            if not _corre(s):
                return [f"La banda se detiene antes de contar {r['conteo']} cajas (se detuvo en la caja {k})."]
            s.pulsar(sensor, 0.3)
            s.correr(0.7)
        s.correr(_FRENO_S)
        return [] if not _corre(s) else [f"La banda NO se detiene sola al contar {r['conteo']} cajas en {r['senal']}."]
    if r.get("senal"):
        s.pulsar(r["senal"], 0.3)
        s.correr(_FRENO_S)
        return [] if not _corre(s) else [f"Cuando {r['senal']} detecta, la banda NO se detiene sola."]
    if r.get("segundos"):
        n = float(r["segundos"])
        antes = max(0.0, n - 1.0 - _ARRANQUE_S)
        if antes:
            s.correr(antes)
            if not _corre(s):
                return [f"La banda se detiene antes de los {_fmt(n)} s pedidos."]
        s.correr(max(0.0, n - _ARRANQUE_S - antes) + _FRENO_S + 0.5)
        return [] if not _corre(s) else [f"La banda NO se detiene sola a los {_fmt(n)} s de marcha."]
    for _ in range(int(MAX_SOLA_S / 5)):
        s.correr(5)
        if not _corre(s):
            return []
    return [f"La banda NO se detiene sola: despues de {MAX_SOLA_S} s sin tocar ningun boton sigue en marcha "
            f"(su paro solo depende de botones)."]
