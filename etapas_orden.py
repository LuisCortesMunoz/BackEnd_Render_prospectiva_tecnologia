"""
etapas_orden.py — instrucciones con VARIAS ACCIONES EN ORDEN.

"Primero la banda avanza hasta que S1 detecte, cuando termine sube la pluma 1
y para cerrar prende la roja 5 s" son tres etapas que deben ejecutarse una
detras de otra. El modelo las armaba a veces con eventos sueltos (sin orden
real), juntaba etapas o perdia alguna. Aqui, sin modelo y de forma
determinista:

  detectar(texto, dispositivo, acl) -> las etapas, con lo que nombra cada una
      (banda, plumas, luces), su tiempo y la senal que la cierra;
  preguntas(etapas, dispositivo)    -> que etapa no dice cuando termina
      (pregunta con opciones de un clic);
  bloque_modelo(etapas, dispositivo) -> las etapas numeradas para el prompt;
  verificar(cfg, etapas, dispositivo) -> problemas de orden del JSON ya
      compilado: falta la secuencia, una etapa no aparece despues de la
      anterior, un tiempo o una senal no coinciden.

Solo se activa con marcadores de orden claros ("primero", "cuando termine",
"al final", "para cerrar"...) o con tres etapas o mas: "prende la verde y
luego la roja" sigue el flujo de siempre.
"""

import re
import unicodedata

import lavo_motor as lm

PLUMA_S = 2          # recorrido de una pluma sin tiempo dicho (= plantillas_proceso.PLUMA_S)

_NUM = {"un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
        "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "quince": 15,
        "veinte": 20, "treinta": 30}
_NUM_RX = r"\d+(?:[.,]\d+)?|" + "|".join(sorted(_NUM, key=len, reverse=True))


def _fold(texto: str) -> str:
    """Minusculas y sin acentos, CON LA MISMA LONGITUD que el original (los
    indices de las coincidencias sirven para cortar el texto original)."""
    out = []
    for c in str(texto or ""):
        b = unicodedata.normalize("NFD", c.lower())
        out.append(b[0] if b else c)
    return "".join(out)


# ── Marcadores de orden ─────────────────────────────────────────────────────
_TERMINA = r"(?:termine|terminen|acabe|finalice|se complete|concluya)"
_FUERTES = [
    r"primero", r"primeramente", r"en primer lugar", r"para (?:empezar|comenzar|iniciar)",
    r"al (?:inicio|principio)",
    rf"(?:y )?cuando (?:eso |esto |esa accion |la accion |lo anterior |todo eso )?{_TERMINA}",
    rf"una vez que {_TERMINA}", r"al (?:terminar|acabar|finalizar)(?: eso| esto| lo anterior)?",
    r"terminando(?: eso| esto)?", r"a continuacion", r"enseguida", r"en seguida", r"posteriormente",
    r"en (?:segundo|tercer|cuarto) lugar",
    r"por ultimo", r"finalmente", r"al final", r"(?:y )?para (?:cerrar|terminar|finalizar|acabar)",
]
_DEBILES = [r"(?:y )?luego(?! de (?:" + _NUM_RX + r"))", r"(?:y )?despues de (?:eso|esto|lo anterior)",
            r"(?:y )?despues(?! de)", r"y entonces", r"tras (?:eso|esto)",
            rf"una vez que(?! {_TERMINA})"]           # "una vez que S2 acumula 3 ..." abre etapa
_RX_FUERTE = re.compile(r"\b(?:" + "|".join(_FUERTES) + r")\b")
_RX_DEBIL = re.compile(r"\b(?:" + "|".join(_DEBILES) + r")\b")

# ── Recursos ────────────────────────────────────────────────────────────────
_RX_BOTON = re.compile(r"\bboton(?:es)? (?:verde|rojo)\b")
_RX_LUZ = re.compile(r"\b(verde|amarill[ao]s?|roj[ao]s?|q(?:3|4|5|10|11|12))\b")
_RX_ON = re.compile(r"\b(enciend\w*|encend\w*|prend\w*|ilumin\w*|activ\w*|pon\w*)\b")
_RX_OFF = re.compile(r"\b(apag\w*|desactiv\w*|quita\w*)\b")
_RX_PLUMA = re.compile(r"\b(?:(primera|segunda) )?plumas?(?:\s?(1|2|uno|dos)\b)?|\bambas plumas\b|\blas dos plumas\b")
_RX_SUBE = re.compile(r"\b(sub\w*|levant\w*|elev\w*|alz\w*|ascien\w*|abr\w*|arriba)\b")
_RX_BAJA = re.compile(r"\b(baj\w*|cierr\w*|desciend\w*|descend\w*|abajo)\b")
_RX_FIN_CLAUSULA = re.compile(r"[,;.:]| y | e ")
_RX_MARCHA = re.compile(r"\b(avanz\w*|marcha)\b")
_RX_MARCHA_BANDA = re.compile(r"\b(muev\w*|mover|arranc\w*|corr(?:e|a|er|iendo)|gir\w*)\b")   # solo con "banda"
_RX_BANDA = re.compile(r"\b(banda|cinta|motor)\b")
_RX_PARO = re.compile(r"\b(deten\w*|detien\w*|deteng\w*|fren\w*|paro(?! de emergencia))\b|"
                      r"\bpar(?:a|e|ar)\s+(?:la\s+)?(?:banda|motor|cinta)\b|"
                      r"\b(?:banda|motor|cinta)\s+(?:se\s+)?(?:para|pare|parar)\b")
_RX_INVIERTE = re.compile(r"\b(invier\w*|invert\w*|revers\w*|cambi\w* (?:de |el )?(?:sentido|direccion))\b")
_RX_TIEMPO = re.compile(rf"\b({_NUM_RX})\s*(s|seg|segs|segundos?|min|minutos?)\b")
_SENAL = r"(?:el |la )?(?:sensor |boton )?(s ?1|s ?2|i ?[1245]|sensor (?:1|2|uno|dos)|primer sensor|segundo sensor|verde|rojo)"
_RX_HASTA = re.compile(rf"\bhasta que {_SENAL}\b|\bhasta que (?:se )?(?:presione|pulse|oprima|detecte) {_SENAL}\b")
_DISPARO = r"(?:detecte |detectar |presione |presionar |pulse |pulsar |se active |activarse |se activa )?"
_RX_CUANDO = re.compile(rf"^\W*(?:y )?(?:cuando|en cuanto|al) {_DISPARO}{_SENAL}\b")
# "cuando S1 detecte ..." tambien abre una etapa (sin ser marcador de orden por si sola)
_RX_CUANDO_SENAL = re.compile(rf"\b(?:y )?(?:cuando|en cuanto|al) {_DISPARO}{_SENAL}\b")
_RX_CONTEO = re.compile(rf"\b(acumul\w*|cuent\w*|conteo|contador\w*|({_NUM_RX}) (?:detecciones|cajas|piezas|objetos|veces))\b")
_RX_REPITE = re.compile(r"\b(repit\w*|repet\w*|ciclo\w*|infinit\w*|una y otra vez|siempre|indefinid\w*)\b")
_RX_VECES = re.compile(rf"\b({_NUM_RX}) veces\b")

# luces por dispositivo (Q10..Q12 en la banda se mapean a la torreta)
_LUCES = {
    "banda": {"verde": "Q3", "amarill": "Q4", "roj": "Q5", "q3": "Q3", "q4": "Q4", "q5": "Q5",
              "q10": "Q3", "q11": "Q4", "q12": "Q5"},
    "maletin": {"verde": "Q10", "amarill": "Q11", "roj": "Q12", "q10": "Q10", "q11": "Q11", "q12": "Q12"},
}
_PLUMA_Q = {(1, "sube"): "Q8", (1, "baja"): "Q9", (2, "sube"): "Q6", (2, "baja"): "Q7"}
_DESC_Q = {"Q3": "verde (Q3)", "Q4": "amarilla (Q4)", "Q5": "roja (Q5)", "Q6": "pluma 2 sube (Q6)",
           "Q7": "pluma 2 baja (Q7)", "Q8": "pluma 1 sube (Q8)", "Q9": "pluma 1 baja (Q9)",
           "Q10": "verde (Q10)", "Q11": "amarilla (Q11)", "Q12": "roja (Q12)"}


def _numero(s) -> float:
    s = str(s).replace(",", ".")
    return float(_NUM.get(s, s))


def _senal(s: str):
    """Texto de una senal ('sensor 1', 's 2', 'boton verde', 'i 1') -> S1/S2/I1/I2."""
    s = s.replace(" ", "")
    s = {"sensor1": "s1", "sensoruno": "s1", "primersensor": "s1", "sensor2": "s2", "sensordos": "s2",
         "segundosensor": "s2", "verde": "i1", "rojo": "i2", "i4": "s1", "i5": "s2"}.get(s, s)
    return s.upper() if re.fullmatch(r"[si]\d", s) else None


def _verbo_cercano(t: str, pos: int, rx_a, rx_b):
    """'a' o 'b' segun el verbo mas cercano ANTES de pos (o, si no hay, despues)."""
    antes = [("a", m.end()) for m in rx_a.finditer(t, 0, pos)] + [("b", m.end()) for m in rx_b.finditer(t, 0, pos)]
    if antes:
        return max(antes, key=lambda x: x[1])[0]
    despues = [("a", m.start()) for m in rx_a.finditer(t, pos)] + [("b", m.start()) for m in rx_b.finditer(t, pos)]
    return min(despues, key=lambda x: x[1])[0] if despues else None


def _verbo_pluma(t: str, m, hasta: int, desde: int):
    """'a' (sube) / 'b' (baja) para una mencion de pluma: el verbo que la sigue
    en la misma clausula ("la pluma 1 se eleva y la pluma 2 desciende") o, si
    no hay, el que la precede desde la mencion anterior ("baja las plumas")."""
    fin = _RX_FIN_CLAUSULA.search(t, m.end(), hasta)
    tramo = t[m.end():fin.start() if fin else hasta]
    s, b = _RX_SUBE.search(tramo), _RX_BAJA.search(tramo)
    if s or b:
        return "a" if s and (not b or s.start() < b.start()) else "b"
    antes = t[desde:m.start()]
    s, b = list(_RX_SUBE.finditer(antes)), list(_RX_BAJA.finditer(antes))
    if s or b:
        return "a" if s and (not b or s[-1].end() > b[-1].end()) else "b"
    return None


def _recursos(t: str, dispositivo: str, plumas_previas):
    """Requisitos de una etapa: [{"tipo": "salida", "q": {..alternativas..}} | {"tipo": "motor", ...}]."""
    req, plumas = [], []
    t = _RX_BOTON.sub(lambda m: " " * len(m.group(0)), t)       # "boton verde" no es la luz verde
    for m in _RX_LUZ.finditer(t):
        clave = next((k for k in _LUCES[dispositivo] if m.group(1).startswith(k)), None)
        if not clave:
            continue
        if _verbo_cercano(t, m.start(), _RX_ON, _RX_OFF) == "b":
            continue                                             # "apaga la verde": no se exige encendida
        q = _LUCES[dispositivo][clave]
        if not any(r.get("q") == {q} for r in req):
            req.append({"tipo": "salida", "q": {q}, "desc": _DESC_Q[q]})
    if dispositivo != "banda":
        return req, plumas
    menciones = list(_RX_PLUMA.finditer(t))
    for k, m in enumerate(menciones):
        txt = m.group(0)
        if "ambas" in txt or "dos plumas" in txt or re.search(r"\bplumas\b", txt):
            nums = [1, 2]
        elif m.group(1) or m.group(2):
            nums = [1 if (m.group(1) == "primera" or m.group(2) in ("1", "uno")) else 2]
        else:
            nums = [1, 2] if not plumas_previas else list(plumas_previas)
            nums = nums if len(nums) == 1 else [0]                 # "la pluma": cualquiera de las dos
        plumas += [n for n in nums if n and n not in plumas]
        sentido = _verbo_pluma(t, m, menciones[k + 1].start() if k + 1 < len(menciones) else len(t),
                               menciones[k - 1].end() if k else 0)
        for n in nums:
            sentidos = ["sube", "baja"] if sentido is None else ["sube" if sentido == "a" else "baja"]
            alts = {_PLUMA_Q[(k, s)] for k in ([1, 2] if n == 0 else [n]) for s in sentidos}
            req.append({"tipo": "salida", "q": alts,
                        "desc": " o ".join(_DESC_Q[q] for q in sorted(alts, key=lambda q: int(q[1:])))})
    if not menciones and plumas_previas and (_RX_SUBE.search(t) or _RX_BAJA.search(t)):
        # "cuando termine, bajala": la pluma de la etapa anterior
        sentido = "sube" if _verbo_cercano(t, len(t), _RX_SUBE, _RX_BAJA) == "a" else "baja"
        for n in plumas_previas:
            q = _PLUMA_Q[(n, sentido)]
            req.append({"tipo": "salida", "q": {q}, "desc": _DESC_Q[q]})
        plumas = list(plumas_previas)
    hay = {k: rx.search(t) for k, rx in (("marcha", _RX_MARCHA), ("paro", _RX_PARO), ("invertir", _RX_INVIERTE))}
    if not hay["marcha"] and _RX_BANDA.search(t):
        hay["marcha"] = _RX_MARCHA_BANDA.search(t)
    if hay["invertir"]:
        req.append({"tipo": "motor", "accion": "invertir", "desc": "la banda cambia de sentido"})
    elif hay["marcha"] and (not hay["paro"] or hay["marcha"].start() < hay["paro"].start()):
        req.append({"tipo": "motor", "accion": "marcha", "desc": "la banda en marcha (motor: marcha)"})
    elif hay["paro"]:
        req.append({"tipo": "motor", "accion": "paro", "desc": "la banda detenida (paso sin marcha)"})
    return req, plumas


def _tiempos(t: str) -> list:
    out = []
    for m in _RX_TIEMPO.finditer(t):
        n = _numero(m.group(1))
        out.append(n * 60 if m.group(2).startswith("min") else n)
    return out


# ── Deteccion ───────────────────────────────────────────────────────────────
def detectar(texto: str, dispositivo: str = "banda", acl: dict = None) -> dict:
    """{"aplica", "etapas": [{"n", "texto", "requisitos", "tiempos", "hasta", "entra_con"}],
        "repite", "veces"}."""
    vacio = {"aplica": False, "etapas": [], "conteo": False, "repite": False, "veces": None}
    if dispositivo not in _LUCES:
        return vacio
    original = str(texto or "")
    t = _fold(original)
    orden = len(_RX_FUERTE.findall(t)) + len(_RX_DEBIL.findall(t))
    if not orden:
        return vacio
    # inicio -> donde empieza el texto de la etapa: los marcadores de orden se
    # quitan; "cuando S1 detecte" se queda (dice con que senal empieza).
    marcas = {}
    for rx, quita in ((_RX_FUERTE, True), (_RX_DEBIL, True), (_RX_CUANDO_SENAL, False)):
        for m in rx.finditer(t):
            if not any(a <= m.start() < fin for a, fin in marcas.items()):
                marcas[m.start()] = m.end() if quita else m.start()
    fuerte = bool(_RX_FUERTE.search(t))
    cortes = sorted(set([0] + list(marcas)))
    trozos = []
    for k, a in enumerate(cortes):
        b = cortes[k + 1] if k + 1 < len(cortes) else len(t)
        ini = marcas.get(a, a)
        trozos.append((original[ini:b], t[ini:b]))

    etapas, plumas_previas = [], []
    for orig, frag in trozos:
        req, plumas = _recursos(frag, dispositivo, plumas_previas)
        hasta = _RX_HASTA.search(frag)
        cuando = _RX_CUANDO.search(frag)
        e = {"texto": re.sub(r"^(?:[\s,;:.]|y\b)+|(?:[\s,;:.]|\by)+$", "", orig), "requisitos": req,
             "tiempos": _tiempos(frag),
             "hasta": _senal(next(g for g in hasta.groups() if g)) if hasta else None,
             "entra_con": _senal(cuando.group(1)) if cuando else None}
        if req or e["tiempos"] or e["hasta"]:       # sin contenido = preambulo ("Con I1:", "A 30 Hz,")
            etapas.append(e)
            plumas_previas = plumas or plumas_previas
    for i, e in enumerate(etapas, 1):
        e["n"] = i
    _aplicar_aclaraciones(etapas, acl or {})
    if dispositivo == "banda":
        _banda_sigue_en_marcha(etapas)
    aplica = len(etapas) >= 2 and (fuerte or (len(etapas) >= 3 and orden >= 2))
    veces = _RX_VECES.search(t)
    return {"aplica": aplica, "etapas": etapas if aplica else [],
            "conteo": bool(_RX_CONTEO.search(t)),
            "repite": bool(_RX_REPITE.search(t)) or bool(veces),
            "veces": int(_numero(veces.group(1))) if veces else None}


def _banda_sigue_en_marcha(etapas):
    """"Se pone en marcha" sin fin: la banda sigue avanzando en las etapas
    siguientes (plumas, luces) hasta una que la detenga. En una secuencia la
    banda solo avanza en los pasos con "marcha", asi que esas etapas la piden.
    "Avanza hasta que S1 detecte" o "avanza 5 s" terminan con la etapa."""
    en_marcha = False
    for e in etapas:
        mot = [r["accion"] for r in e["requisitos"] if r["tipo"] == "motor"]
        if mot:
            en_marcha = mot[0] in ("marcha", "invertir") and not (e["hasta"] or e["tiempos"])
        elif en_marcha:
            e["requisitos"].append({"tipo": "motor", "accion": "marcha", "desc": "la banda sigue en marcha"})


def _clave_fin(n: int) -> str:
    return f"etapa_{n}_fin"


def _aplicar_aclaraciones(etapas, acl):
    """'etapa_2_fin' = 'A los 5 s' | 'Cuando S1 detecte' | 'Hasta que presione I2'."""
    for e in etapas:
        v = (acl.get(_clave_fin(e["n"])) or {}).get("respuesta")
        if not v:
            continue
        f = _fold(v)
        s = re.search(r"\b([si]\s?\d)\b", f)
        if s:
            e["hasta"] = _senal(s.group(1))
        elif _tiempos(f):
            e["tiempos"] = _tiempos(f)
        else:
            m = re.search(r"\d+(?:[.,]\d+)?", f)
            if m:
                e["tiempos"] = [_numero(m.group(0))]


def _solo_plumas(e) -> bool:
    """La etapa mueve plumas (con la banda detenida o en marcha, sin luces): dura el recorrido de la pluma."""
    plumas = [r for r in e["requisitos"] if r["tipo"] == "salida" and r["q"] <= {"Q6", "Q7", "Q8", "Q9"}]
    otros = [r for r in e["requisitos"] if r not in plumas and r["tipo"] != "motor"]
    return bool(plumas) and not otros


def _solo_paro(e) -> bool:
    """'luego se detiene': inmediato, puede ir en el mismo paso que la etapa siguiente."""
    return bool(e["requisitos"]) and all(r["tipo"] == "motor" and r["accion"] == "paro" for r in e["requisitos"])


def _sabe_cuando_termina(etapas, i) -> bool:
    e = etapas[i]
    if e["tiempos"] or e["hasta"] or _solo_plumas(e) or _solo_paro(e):
        return True
    return i + 1 < len(etapas) and bool(etapas[i + 1]["entra_con"])


def preguntas(det: dict, dispositivo: str = "banda") -> list:
    """Una pregunta (con opciones) por cada etapa que no dice cuando termina."""
    out = []
    if not det.get("aplica"):
        return out
    etapas = det["etapas"]
    for i, e in enumerate(etapas):
        if _sabe_cuando_termina(etapas, i):
            continue
        ultima = i == len(etapas) - 1
        if dispositivo == "banda":
            ops = ["A los 3 s", "A los 5 s", "A los 10 s", "Cuando S1 detecte", "Cuando S2 detecte"]
            if ultima:
                ops = ["3 s", "5 s", "10 s", "Hasta que presione I2"]
        else:
            ops = ["1 s", "3 s", "5 s", "10 s"]
        q = (f"¿Cuánto dura la última etapa («{e['texto']}»)?" if ultima else
             f"¿Cuándo termina la etapa {e['n']} («{e['texto']}») para pasar a la siguiente?")
        out.append({"id": _clave_fin(e["n"]), "campo": _clave_fin(e["n"]), "pregunta": q, "opciones": ops})
    return out


def bloque_modelo(det: dict, dispositivo: str = "banda") -> str:
    """Las etapas numeradas y como armarlas, para el prompt."""
    if not det.get("aplica"):
        return ""
    filas = []
    for e in det["etapas"]:
        fin = []
        if e["tiempos"]:
            fin.append(f"dura {', '.join(_fmt(x) for x in e['tiempos'])} s")
        if e["hasta"]:
            fin.append(f"termina cuando {e['hasta']} se activa")
        if e["entra_con"]:
            fin.append(f"empieza cuando {e['entra_con']} se activa (el paso anterior avanza con esa senal)")
        if not fin and _solo_plumas(e) and dispositivo == "banda":
            fin.append(f"sin tiempo dicho: {PLUMA_S} s de recorrido de la pluma")
        if not fin and _solo_paro(e):
            fin.append("inmediato: puede ir en el mismo paso que la etapa siguiente")
        req = "; ".join(r["desc"] for r in e["requisitos"]) or "sin salidas (espera)"
        filas.append(f"{e['n']}) «{e['texto']}» -> {req}" + (f" [{'; '.join(fin)}]" if fin else ""))
    if dispositivo == "banda":
        reglas = ("Arma UNA secuencia (SQ1) con un paso por etapa, en ESTE orden (si una etapa necesita esperar, "
                  "agrega un paso de espera). No uses eventos ni timers sueltos para encadenarlas. Cada paso "
                  "lleva TODAS las salidas que deben estar encendidas en esa etapa (una luz que sigue encendida "
                  "se repite en los pasos siguientes) y \"marcha\" en cada paso en que la banda deba avanzar. "
                  "Si la secuencia mueve la banda, NO pongas motor.arranque.")
        if det.get("conteo"):
            reglas += (" Hay un CONTEO: las etapas que se repiten con cada objeto hasta llegar a N forman un ciclo "
                       "dentro de la secuencia (el ultimo paso del ciclo vuelve con \"siguiente\" al primero) y un "
                       "contador CTn de esa senal (p. ej. S2, modo una_vez) sale del ciclo con \"alternativa\" "
                       "{\"senal\":\"CTn\",\"ir_a\":<paso siguiente al ciclo>}; reinicia el contador con "
                       "reset = SQ1_FIN (o el paso que lo pida). Modo una_vez.")
        elif det["veces"]:
            reglas += f" Se repite {det['veces']} veces: modo repetir."
        elif det["repite"]:
            reglas += " Se repite: modo infinita con su paro."
        else:
            reglas += " Una sola vez: modo una_vez."
    else:
        reglas = ("Arma \"sequence\" con un paso por etapa, en ESTE orden, y \"outputs\": []. Cada paso lleva "
                  "TODAS las salidas encendidas en esa etapa (una luz que sigue encendida se repite en los pasos "
                  "siguientes) y su duration_s.")
        reglas += " Se repite: mode loop." if det["repite"] else " Una sola vez: mode once."
    return ("ACCIONES EN ORDEN (el usuario pidio estas etapas, una despues de otra):\n"
            + "\n".join(filas) + "\n" + reglas)


def _fmt(x) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


# ── Verificacion ────────────────────────────────────────────────────────────
def _pasos_banda(eng: dict) -> list:
    """Pasos en orden de ejecucion: SQ1 y, si SQ2 arranca con SQ1_FIN, los de SQ2 despues."""
    seqs = {s.get("id"): s for s in lm._lista(eng.get("secuencias")) if isinstance(s, dict)}
    if not seqs:
        return []

    def cod(x):
        try:
            return lm.codigo_senal(x)
        except (ValueError, TypeError):
            return None

    cadenas = []
    for sid, s in seqs.items():
        otra = next((o for oid, o in seqs.items() if oid != sid and cod(o.get("inicio")) == cod(f"{sid}_FIN")), None)
        if any(cod(s.get("inicio")) == cod(f"{oid}_FIN") for oid in seqs if oid != sid):
            continue                                       # va encadenada detras de otra
        cadenas.append(lm._lista(s.get("pasos")) + (lm._lista(otra.get("pasos")) if otra else []))
    return max(cadenas, key=len) if cadenas else []


def _cumple_banda(paso, req) -> bool:
    sal, mot = set(paso.get("salidas") or []), set(paso.get("motor") or [])
    if req["tipo"] == "salida":
        return bool(sal & req["q"])
    if req["accion"] == "marcha":
        return bool(mot & {"marcha", "invertir", "dir_1", "dir_2"})
    if req["accion"] == "invertir":
        return bool(mot & {"invertir", "dir_1", "dir_2"})
    return "marcha" not in mot                              # paro: el paso no mueve la banda


def _mismo(a, b) -> bool:
    try:
        return lm.codigo_senal(a) == lm.codigo_senal(b)
    except (ValueError, TypeError):
        return False


def verificar(cfg: dict, det: dict, dispositivo: str = "banda") -> list:
    """Problemas de orden del JSON compilado (lista vacia = respeta las etapas)."""
    if not det.get("aplica"):
        return []
    etapas = det["etapas"]
    if dispositivo == "banda":
        eng = ((cfg or {}).get("band") or {}).get("engine") or {}
        pasos = _pasos_banda(eng)
        cumple = _cumple_banda
        seg = lambda p: p.get("segundos")
        if not pasos:
            return [f"Pediste {len(etapas)} acciones en orden y el JSON no tiene una secuencia: arma SQ1 con un "
                    f"paso por etapa en ese orden (no las encadenes con eventos ni timers sueltos)."]
    else:
        sq = (cfg or {}).get("sequence") or {}
        pasos = [{"salidas": p.get("outputs") or [], "segundos": p.get("duration_s")}
                 for p in lm._lista(sq.get("steps")) if isinstance(p, dict)]
        cumple = lambda p, r: bool(set(p["salidas"]) & r["q"]) if r["tipo"] == "salida" else True
        seg = lambda p: p.get("segundos")
        if not pasos:
            return [f"Pediste {len(etapas)} acciones en orden y el JSON no tiene \"sequence\": arma un paso por "
                    f"etapa en ese orden y deja \"outputs\": []."]

    problemas, pos, previo = [], 0, -1
    for e in etapas:
        j = next((k for k in range(pos, len(pasos))
                  if isinstance(pasos[k], dict) and all(cumple(pasos[k], r) for r in e["requisitos"])), None)
        if j is None:
            donde = "despues de la etapa anterior" if e["n"] > 1 else "en ningun paso"
            problemas.append(f"La etapa {e['n']} («{e['texto']}») no aparece {donde}: necesita un paso propio con "
                             + ("; ".join(r["desc"] for r in e["requisitos"]) or "su espera") + ".")
            continue
        tramo = [p for p in pasos[previo + 1:j + 1] if isinstance(p, dict)]
        for x in e["tiempos"]:
            if not any(_num_igual(seg(p), x) for p in tramo):
                problemas.append(f"La etapa {e['n']} («{e['texto']}») dura {_fmt(x)} s, pero su paso ({j + 1}) "
                                 f"dura {_fmt(seg(pasos[j]) or 0)} s.")
        if dispositivo == "banda" and e["hasta"]:
            if not any(_mismo(p.get("senal"), e["hasta"]) for p in pasos[j:j + 2] if isinstance(p, dict)):
                problemas.append(f"La etapa {e['n']} («{e['texto']}») termina cuando {e['hasta']} se activa: su paso "
                                 f"({j + 1}) debe avanzar por senal {e['hasta']}.")
        if dispositivo == "banda" and e["entra_con"] and previo >= 0:
            if not any(_mismo(p.get("senal"), e["entra_con"]) for p in pasos[previo:j] if isinstance(p, dict)):
                problemas.append(f"La etapa {e['n']} empieza cuando {e['entra_con']} se activa: el paso anterior "
                                 f"({previo + 1}) debe avanzar por senal {e['entra_con']}.")
        # "se detiene" no ocupa paso propio: la etapa siguiente puede ir en el mismo
        pos, previo = (j, j - 1) if dispositivo == "banda" and _solo_paro(e) else (j + 1, j)
    return problemas


def _num_igual(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.05
    except (TypeError, ValueError):
        return False


def mensaje_corregir(problemas: list) -> str:
    return ("El JSON anterior no respeta el ORDEN de las acciones que pidio el usuario:\n- "
            + "\n- ".join(problemas)
            + "\nCorrigelo: una secuencia con un paso por etapa, en ese orden, sin perder nada de lo pedido. "
              "Devuelve el JSON COMPLETO, sin texto extra.")


def aviso(problemas: list) -> str:
    return "El orden pedido no quedo exacto, revisalo antes de cargar: " + " ".join(problemas)
