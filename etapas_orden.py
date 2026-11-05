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
            rf"una vez que(?! {_TERMINA})",           # "una vez que S2 acumula 3 ..." abre etapa
            # "... levante las plumas y haga la secuencia de que se encienda la roja"
            r"(?:y )?(?:haga|hace|inicie|inicia|arranque|empiece|empieza|comience|comienza) "
            r"(?:la |una |otra )?secuencia(?: de(?: que)?)?"]
_RX_FUERTE = re.compile(r"\b(?:" + "|".join(_FUERTES) + r")\b")
_RX_DEBIL = re.compile(r"\b(?:" + "|".join(_DEBILES) + r")\b")

# ── Otra secuencia independiente ────────────────────────────────────────────
# "Secuencia 1: ... Secuencia 2: ...", "otra secuencia", "la segunda (secuencia)":
# lo que sigue es una secuencia APARTE, no la etapa siguiente de la anterior.
# Tambien son marcadores de orden (el usuario esta describiendo secuencias).
# "repite la secuencia 3 veces" no abre otra (lookahead de "veces").
_RX_SEC_NUM = re.compile(r"\b(?:la |una )?secuencia (?:numero |no\.? |#)?(?:\d+|uno|dos|tres|cuatro|"
                         r"primera|segunda|tercera|cuarta)\b(?!\s*veces)")
_RX_SEC_OTRA = re.compile(r"\b(?:la |una )?(?:otra|segunda|tercera|cuarta|siguiente|nueva) secuencia\b")
# "la primera, ... La segunda, ...": solo si la instruccion habla de secuencias
# (si no, "sube la primera y luego la segunda" son las plumas).
_RX_SEC_ORDINAL = re.compile(r"\b(?:y )?(?:en )?la (?:primera|segunda|tercera|cuarta)\b(?!\s+(?:pluma|plumas|luz|luces|"
                             r"lampara|etapa|vez|veces|pieza|piezas|caja|cajas|banda|sensor|deteccion|accion|parte|"
                             r"senal|salida|entrada|secuencia))")
# "Ademas, cuando S2 detecte ...", "Por otro lado, al presionar I4 ...": separan
# secuencias ya detectadas, pero por si solos NO activan el modo de orden (una
# frase con puras comas no se puede partir en etapas confiables). Solo al
# empezar una oracion Y seguidos de un disparador: "...avanzando; ademas, la
# pluma 2 se eleva" es lo que pasa EN ESE paso, no otra secuencia.
_RX_SEC_APARTE = re.compile(r"(?:^|(?<=\.)|(?<=\. ))\s*(?:y )?(?:ademas|aparte|por otro lado|por separado|"
                            r"al mismo tiempo|en paralelo|independientemente)\b"
                            r"(?=,?\s*(?:cuando|en cuanto|cada vez|si |al (?:presionar|pulsar|oprimir|activar|"
                            r"detectar)|con (?:el |la )?(?:boton|pulsador|sensor|interruptor|i ?\d|s ?\d)))")


# "... luego la roja, despues inicia otra secuencia de ...": va A CONTINUACION
# de la anterior (son mas etapas de la misma), no es una secuencia aparte.
_RX_A_CONTINUACION = re.compile(r"\b(?:luego|despues|termin\w*|acab\w*|finalic\w*|enseguida|en seguida|"
                                r"continuacion|posteriormente|tras)\b[^.;:]{0,25}$")


def _marcas_otra_secuencia(t: str) -> tuple:
    """({inicio: fin} de cada 'otra secuencia', cuantas son explicitas)."""
    rxs = [_RX_SEC_NUM, _RX_SEC_OTRA] + ([_RX_SEC_ORDINAL] if "secuencia" in t else [])
    marcas, explicitas = {}, 0
    for rx in rxs:
        for m in rx.finditer(t):
            if _RX_A_CONTINUACION.search(t[:m.start()]):
                continue
            if not any(a <= m.start() < fin for a, fin in marcas.items()):
                marcas[m.start()] = m.end()
                explicitas += 1
    for m in _RX_SEC_APARTE.finditer(t):
        ini = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
        if not any(a <= ini < fin for a, fin in marcas.items()):
            marcas[ini] = m.end()
    return marcas, explicitas

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
_RX_INVIERTE = re.compile(r"\b(invier\w*|invert\w*|revers\w*|regres\w*|retroced\w*|al reves|sentido contrario|"
                         r"cambi\w* (?:de |el )?(?:sentido|direccion))\b")
_RX_TIEMPO = re.compile(rf"\b({_NUM_RX})\s*(s|seg|segs|segundos?|min|minutos?)\b")
# "luego la verde durante 1": sin unidad, en una secuencia son segundos
# ("durante un tiempo" NO es 1 s: los articulos un/una/uno no cuentan como numero aqui)
_NUM_SOLO_RX = r"\d+(?:[.,]\d+)?|" + "|".join(sorted((k for k in _NUM if k not in ("un", "una", "uno")),
                                                    key=len, reverse=True))
_RX_TIEMPO_SOLO = re.compile(rf"\b(?:durante|por) ({_NUM_SOLO_RX})\b(?!\s*(?:[.,]\d|s\b|seg|min|hz|veces|%))")
# Duracion que el usuario NO dio: se pregunta, no se inventa.
_RX_TIEMPO_INDEFINIDO = re.compile(r"\b(?:durante |por )?(?:un|cierto|algun) (?:tiempo|rato|lapso|periodo)"
                                   r"(?: determinado| definido| dado| fijo| corto| breve)?\b|"
                                   r"\bunos (?:segundos|instantes|momentos)\b|\bun momento\b")
# "se detiene 5 s antes de continuar avanzando": tras la pausa la banda REANUDA
_RX_REANUDA = re.compile(r"\b(?:antes de|y (?:luego |despues )?(?:vuelve a|sigue|continua)|para (?:luego )?)"
                         r"\s*(?:continuar|seguir|volver a|reanudar|avanzar|arrancar|moverse)?\s*"
                         r"(?:avanz\w*|en marcha|moviendose|mover\w*|su marcha|su camino)?\b|"
                         r"\b(?:reanud\w*|vuelve a avanzar|continua avanzando|sigue avanzando)\b")
# "el contador se reinicia (a cero)"
_RX_REINICIA_CONTADOR = re.compile(r"\b(?:contador|conteo|cuenta)\b[^.;]{0,25}\b(?:reinici\w*|resete\w*|"
                                   r"vuelve a (?:cero|0)|a cero|en cero|borr\w*|limpi\w*)\b|"
                                   r"\b(?:reinici\w*|resete\w*|borr\w*)\b[^.;]{0,10}\b(?:el )?(?:contador|conteo)\b")
# "una vez que S2 acumula 3 detecciones", "al contar 5 cajas", "despues de 3 piezas"
_RX_CONTEO_N = re.compile(rf"\b(?:acumul\w*|cuent\w*|cont\w*|llegue\w* a|complet\w*|despues de|al pasar|registr\w*)"
                          rf"\s+(?:a\s+)?({_NUM_RX})\s+(?:detecciones|deteccion|cajas|piezas|objetos|productos|veces|"
                          rf"pulsos|conteos)\b")
_RX_INCREMENTA = re.compile(r"\b(?:se )?(?:increment\w*|aument\w*|sum\w*|cuent\w*|registr\w*)\b[^.;]{0,15}\b(?:un |el )?"
                            r"(?:contador|conteo|uno)\b|\bcontador\b[^.;]{0,15}\b(?:increment\w*|aument\w*|sube)\b")
# "... y despues continua avanzando" justo despues de un paro: la pausa reanuda (no es otra etapa)
_RX_SIGUE_PAUSA = re.compile(r"\s*(?:continu\w*|sigue\w*|reanud\w*|vuelve a (?:avanzar|moverse|arrancar|andar))")
# "... hasta que yo detenga todo": la etapa sigue hasta el paro (no hay que preguntar)
_RX_HASTA_PARO = re.compile(r"\bhasta que (?:yo |lo |la |el usuario |se )?(?:detenga\w*|deteng\w+|pare|pares|apague|"
                            r"presione (?:el )?(?:boton de )?paro)\b(?: todo\b)?|\bhasta (?:el|un) paro\b")
_RX_PARO_GENERAL = re.compile(r"\b(?:y )?con (?:el )?(?:boton )?(?:i ?\d|s ?\d|rojo|verde|paro)\b[^.]*?"
                              r"\b(?:se )?(?:detiene|detenga|para|apaga) todo\b")
_SENAL = (r"(?:el |la )?(?:sensor |boton |pulsador |boton pulsador |interruptor )?"
          r"(s ?1|s ?2|i ?[1245]|sensor (?:1|2|uno|dos)|primer sensor|segundo sensor|verde|rojo)")
_RX_HASTA = re.compile(rf"\bhasta que {_SENAL}\b|\bhasta que (?:se )?(?:presione|pulse|oprima|detecte) {_SENAL}\b")
_DISPARO = r"(?:detecte |detectar |presione |presionar |pulse |pulsar |se active |activarse |se activa )?"
_RX_CUANDO = re.compile(rf"^\W*(?:y )?(?:cuando|en cuanto|al) {_DISPARO}{_SENAL}\b")
# "cuando S1 detecte ..." tambien abre una etapa (sin ser marcador de orden por si sola)
_RX_CUANDO_SENAL = re.compile(rf"\b(?:y )?(?:cuando|en cuanto|al) {_DISPARO}{_SENAL}\b")
_RX_CONTEO = re.compile(rf"\b(acumul\w*|cuent\w*|conteo|contador\w*|({_NUM_RX}) (?:detecciones|cajas|piezas|objetos|veces))\b")
_RX_REPITE = re.compile(r"\b(repit\w*|repet\w*|ciclo\w*|infinit\w*|una y otra vez|siempre|indefinid\w*)\b")
# Cierra una secuencia ("... y se repita el ciclo"); "cuando termine el ciclo" no.
_RX_CIERRA_CICLO = re.compile(r"\b(repit\w*|repet\w*|ciclic\w*|infinit\w*|una y otra vez|indefinid\w*)\b")
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
    sentido_previo = None
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
        # "levante la pluma 1 y la pluma 2": la segunda hereda el verbo de la primera
        sentido = sentido or sentido_previo
        sentido_previo = sentido
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
    if not out:
        out = [_numero(m.group(1)) for m in _RX_TIEMPO_SOLO.finditer(t)]
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
    otra_sec, sec_explicitas = _marcas_otra_secuencia(t)
    orden = len(_RX_FUERTE.findall(t)) + len(_RX_DEBIL.findall(t)) + sec_explicitas
    if not orden:
        return vacio
    # inicio -> donde empieza el texto de la etapa: los marcadores de orden se
    # quitan; "cuando S1 detecte" se queda (dice con que senal empieza).
    # Los de "otra secuencia" van primero: ganan el lugar si se enciman.
    marcas = dict(otra_sec)
    for rx, quita in ((_RX_FUERTE, True), (_RX_DEBIL, True), (_RX_CUANDO_SENAL, False)):
        for m in rx.finditer(t):
            if rx is _RX_DEBIL and _RX_SIGUE_PAUSA.match(t, m.end()) and _RX_PARO.search(t[max(0, m.start() - 60):m.start()]):
                # "se detiene 3 s y despues continua avanzando": es UNA pausa que reanuda, no otra etapa
                continue
            if not any(a <= m.start() < fin for a, fin in marcas.items()):
                marcas[m.start()] = m.end() if quita else m.start()
    fuerte = bool(_RX_FUERTE.search(t)) or sec_explicitas > 0
    cortes = sorted(set([0] + list(marcas)))
    trozos = []
    for k, a in enumerate(cortes):
        b = cortes[k + 1] if k + 1 < len(cortes) else len(t)
        ini = marcas.get(a, a)
        trozos.append((original[ini:b], t[ini:b], a in otra_sec))

    # Una etapa que cierra un ciclo ("... y se repita el ciclo", "hasta que yo
    # detenga todo") termina su secuencia: lo que sigue es OTRA secuencia
    # independiente ("cuando el sensor 2 detecte ... haga la secuencia ...").
    # "Secuencia 2: ..." tambien abre otra secuencia; si el marcador queda en
    # un trozo sin contenido ("La segunda,") se aplica a la siguiente etapa.
    etapas, plumas_previas, grupo, cierra, nueva = [], [], 1, False, False
    for orig, frag, abre_sec in trozos:
        nueva = nueva or abre_sec
        # "hasta que yo detenga todo" y "Con I2 se detiene todo" son el paro
        # general, no una etapa con la banda detenida.
        limpio = _RX_PARO_GENERAL.sub(lambda m: " " * len(m.group(0)), _RX_HASTA_PARO.sub(
            lambda m: " " * len(m.group(0)), frag))
        req, plumas = _recursos(limpio, dispositivo, plumas_previas)
        hasta = _RX_HASTA.search(frag)
        cuando = _RX_CUANDO.search(frag)
        # Lo que sigue al punto final es otra oracion ("... el ciclo. Quiero que")
        texto_e = re.sub(r"^(?:[\s,;:.]|y\b)+|(?:[\s,;:.]|\by)+$", "", orig)
        texto_e = re.sub(r"\.\s+[^.]*$", "", texto_e) or texto_e
        e = {"texto": texto_e, "requisitos": req,
             "tiempos": _tiempos(frag),
             "hasta": _senal(next(g for g in hasta.groups() if g)) if hasta else None,
             "hasta_paro": bool(_RX_HASTA_PARO.search(frag)),
             "entra_con": _senal(cuando.group(1)) if cuando else None,
             # lo que la etapa dice ademas de sus salidas
             "tiempo_indefinido": bool(_RX_TIEMPO_INDEFINIDO.search(frag)),
             "reanuda": bool(_RX_REANUDA.search(frag)) and bool(_RX_PARO.search(limpio)),
             "incrementa": bool(_RX_INCREMENTA.search(frag)),
             "reinicia_contador": bool(_RX_REINICIA_CONTADOR.search(frag)),
             "conteo": _conteo(frag)}
        if e["tiempo_indefinido"] and not _RX_TIEMPO.search(frag):
            e["tiempos"] = []                       # "durante un tiempo determinado": se pregunta
        # "se detiene 3 s y despues continua; ademas, la pluma 2 se eleva": ¿las plumas van
        # al detectar (durante la pausa) o al reanudar? Si se nombran DESPUES de "continua", se pregunta.
        sigue = re.search(r"\b(?:continu\w*|sigue\w*|reanud\w*|vuelve a)\b", frag)
        salidas_e = [r for r in req if r["tipo"] == "salida"]
        e["duda_acciones"] = bool(e["reanuda"] and e["tiempos"] and salidas_e and sigue and any(
            x.start() > sigue.start() for x in list(_RX_PLUMA.finditer(frag)) + list(_RX_LUZ.finditer(frag))))
        if req or e["tiempos"] or e["hasta"] or e["conteo"] or e["reinicia_contador"]:
            # (sin contenido = preambulo: "Con I1:", "A 30 Hz,")
            if (cierra or nueva) and etapas and etapas[-1]["grupo"] == grupo:
                grupo += 1
                plumas_previas = []
            nueva = False
            e["grupo"] = grupo
            etapas.append(e)
            plumas_previas = plumas or plumas_previas
            cierra = bool(_RX_CIERRA_CICLO.search(frag) or e["hasta_paro"])
    for i, e in enumerate(etapas, 1):
        e["n"] = i
    etapas = _resolver_acciones_pausa(etapas, acl or {})
    for i, e in enumerate(etapas, 1):
        e["n"] = i
    _aplicar_aclaraciones(etapas, acl or {})
    grupos = []
    for g in range(1, grupo + 1):
        de_g = [e for e in etapas if e.get("grupo") == g]
        if not de_g:
            continue
        txt = _fold(" ".join(e["texto"] for e in de_g))
        veces_g = _RX_VECES.search(txt)
        grupos.append({"n": len(grupos) + 1, "etapas": [e["n"] for e in de_g],
                       "repite": bool(_RX_REPITE.search(txt) or veces_g),
                       "veces": int(_numero(veces_g.group(1))) if veces_g else None})
        for e in de_g:
            e["grupo"] = len(grupos)
        _ciclo(grupos[-1], de_g)
        _resolver_ultimo(grupos[-1], de_g, acl or {})
        if dispositivo == "banda":
            _banda_sigue_en_marcha(de_g)
    aplica = len(etapas) >= 2 and (fuerte or (len(etapas) >= 3 and orden >= 2))
    veces = _RX_VECES.search(t)
    return {"aplica": aplica, "etapas": etapas if aplica else [], "grupos": grupos if aplica else [],
            "conteo": bool(_RX_CONTEO.search(t)),
            "repite": bool(_RX_REPITE.search(t)) or bool(veces),
            "veces": int(_numero(veces.group(1))) if veces else None}


# ── Dudas de interpretacion (se preguntan con opciones, no se suponen) ──────
def _clave_acciones(n: int) -> str:
    return f"acciones_pausa_{n}"


def _clave_ultimo(g: int) -> str:
    return f"conteo_ultimo_{g}"


def _resolver_acciones_pausa(etapas: list, acl: dict) -> list:
    """Con la respuesta a 'acciones_pausa_N': 'al reanudar' parte la etapa en la
    pausa y una etapa nueva con las acciones; 'al detectar' la deja junta."""
    out = []
    for e in etapas:
        if not e.get("duda_acciones"):
            out.append(e)
            continue
        v = _fold((acl.get(_clave_acciones(e["n"])) or {}).get("respuesta"))
        if not v:
            out.append(e)                            # sin respuesta: se pregunta
            continue
        e["duda_acciones"] = False
        if re.search(r"reanud|vuelve|avance|despues|continu", v):
            salidas = [r for r in e["requisitos"] if r["tipo"] == "salida"]
            e["requisitos"] = [r for r in e["requisitos"] if r["tipo"] != "salida"]
            out.append(e)
            out.append({**e, "texto": e["texto"] + " (al reanudar)", "requisitos": salidas, "tiempos": [],
                        "hasta": None, "hasta_paro": False, "entra_con": None, "reanuda": False,
                        "conteo": None, "reinicia_contador": False, "duda_acciones": False})
        else:
            out.append(e)                            # durante la pausa: todo en la misma etapa
    return out


def _resolver_ultimo(g: dict, de_g: list, acl: dict):
    """Ciclo con conteo cuya senal contada TAMBIEN dispara una etapa del ciclo (S2
    pausa la banda y suma al contador): al llegar a N, ¿esa etapa se hace o se salta?"""
    c, ciclo = g.get("conteo"), g.get("ciclo") or []
    if not (c and ciclo and c.get("senal")):
        return
    contada = next((e for e in de_g if e["n"] in ciclo and e["entra_con"] == c["senal"]), None)
    if contada is None:
        return
    # Solo hay conflicto si esa etapa (o las que le siguen en el ciclo) DETIENE la banda y la
    # etapa del conteo dice que sigue avanzando; si solo mueve plumas o luces, no se pregunta.
    resto = [e for e in de_g if contada["n"] <= e["n"] <= ciclo[-1]]
    if not any(r["tipo"] == "motor" and r["accion"] == "paro" for e in resto for r in e["requisitos"]):
        return
    v = _fold((acl.get(_clave_ultimo(g["n"])) or {}).get("respuesta"))
    if re.search(r"directo|salta|sin (?:pausa|detener|hacer)|no (?:la )?hace", v):
        g["ultimo"] = "directo"
    elif v:
        g["ultimo"] = "tambien"
    else:
        sig = next((e for e in de_g if e["n"] == c["etapa"]), None)
        g["duda_ultimo"] = {"contada": contada["n"], "texto_contada": contada["texto"],
                            "texto_sigue": sig["texto"] if sig else f"la etapa {c['etapa']}"}
    g["contada"] = contada["n"]


_RX_AL_DETECTAR = re.compile(r"\b(?:al detectar|en cuanto detect\w*|cuando (?:el sensor \w+ |s\d )?detect\w*|durante la "
                             r"pausa|mientras (?:esta |este )?(?:detenida|parada)|al mismo tiempo que se detiene|antes de "
                             r"(?:que )?(?:continu|reanud|avanz)\w*)\b")
_RX_AL_REANUDAR = re.compile(r"\b(?:al reanudar|cuando (?:la banda )?(?:vuelve a avanzar|reanud\w*|continu\w*|sigue)|despues "
                             r"de la pausa|al continuar|al volver a avanzar|cuando termine la pausa)\b")
_RX_DIRECTO = re.compile(r"\b(?:pasa\w* directo|sin (?:la )?pausa|sin detenerse|ya no se (?:detiene|para)|no (?:se )?"
                         r"(?:detiene|detenga|para|pare|hace la pausa)\w*(?: [a-z]+){0,3} (?:tercer|ultim|cuarta|quinta)\w*)\b")
_RX_TAMBIEN = re.compile(r"\btambien (?:se detiene|se para|hace la pausa|pausa|debe detenerse|debe pausar)\b")


def respuestas_en_textos(textos, det: dict) -> dict:
    """Respuestas a las dudas de interpretacion que el usuario ya dio en sus
    instrucciones (p. ej. una correccion: "las plumas deben moverse al detectar
    S2", "en la tercera pasa directo"). La mas reciente manda."""
    acl = {}
    for t in textos or []:
        f = _fold(t)
        menciona = bool(_RX_PLUMA.search(f) or _RX_LUZ.search(f))
        for e in det.get("etapas") or []:
            if e.get("duda_acciones") and menciona:
                if _RX_AL_REANUDAR.search(f):
                    acl[_clave_acciones(e["n"])] = {"pregunta": "", "respuesta": "Al reanudar el avance"}
                elif _RX_AL_DETECTAR.search(f):
                    acl[_clave_acciones(e["n"])] = {"pregunta": "", "respuesta": "Al detectar (durante la pausa)"}
        for g in det.get("grupos") or []:
            if g.get("duda_ultimo"):
                if _RX_DIRECTO.search(f):
                    acl[_clave_ultimo(g["n"])] = {"pregunta": "", "respuesta": "Pasa directo"}
                elif _RX_TAMBIEN.search(f):
                    acl[_clave_ultimo(g["n"])] = {"pregunta": "", "respuesta": "También lo hace y luego sigue"}
    return acl


_OPCIONES_LITERALES = {
    "al detectar (durante la pausa)": ("acciones", "Al detectar (durante la pausa)"),
    "al reanudar el avance": ("acciones", "Al reanudar el avance"),
    "tambien lo hace y luego sigue": ("ultimo", "También lo hace y luego sigue"),
    "pasa directo": ("ultimo", "Pasa directo"),
}


def con_opciones_pegadas(det: dict, texto: str, dispositivo: str, acl: dict) -> dict:
    """Los lentes PEGAN la opcion elegida al final de la instruccion (no la mandan
    como aclaracion): si el texto trae literalmente una opcion de estas dudas, es
    la respuesta (sin esto, la pregunta se repetiria para siempre)."""
    if not preguntas_dudas(det):
        return det
    f = _fold(texto)
    extra = {}
    for op, (tipo, resp) in _OPCIONES_LITERALES.items():
        if op not in f:
            continue
        if tipo == "acciones":
            for e in det["etapas"]:
                if e.get("duda_acciones"):
                    extra[_clave_acciones(e["n"])] = {"pregunta": "", "respuesta": resp}
        else:
            for g in det["grupos"]:
                if g.get("duda_ultimo"):
                    extra[_clave_ultimo(g["n"])] = {"pregunta": "", "respuesta": resp}
    if not extra:
        return det
    return detectar(texto, dispositivo, {**extra, **(acl or {})})


def con_respuestas_de_sesion(det: dict, texto: str, dispositivo: str, acl: dict, textos) -> dict:
    """Si alguna instruccion POSTERIOR responde una duda de interpretacion (o
    cambia la respuesta que se dio antes), se vuelve a detectar con ella: lo
    dicho despues pesa sobre lo de antes."""
    if not textos:
        return det
    sin_dudas = {k: v for k, v in (acl or {}).items()
                 if not str(k).startswith(("acciones_pausa_", "conteo_ultimo_"))}
    extra = respuestas_en_textos(textos, detectar(texto, dispositivo, sin_dudas))
    if not extra:
        return det
    return detectar(texto, dispositivo, {**(acl or {}), **extra})


def preguntas_dudas(det: dict) -> list:
    """Preguntas de interpretacion (van ANTES que las de 'cuando termina', porque
    su respuesta puede partir etapas y cambiar la numeracion)."""
    out = []
    for e in det.get("etapas") or []:
        if e.get("duda_acciones"):
            out.append({"id": _clave_acciones(e["n"]), "campo": _clave_acciones(e["n"]),
                        "pregunta": (f"En la etapa {e['n']} («{e['texto'][:90]}»), ¿las plumas/luces se mueven al "
                                     f"detectar (durante la pausa) o cuando la banda vuelve a avanzar?"),
                        "opciones": ["Al detectar (durante la pausa)", "Al reanudar el avance"]})
    for g in det.get("grupos") or []:
        d = g.get("duda_ultimo")
        if d:
            out.append({"id": _clave_ultimo(g["n"]), "campo": _clave_ultimo(g["n"]),
                        "pregunta": (f"Al llegar a {g['conteo']['n']} en {g['conteo']['senal']}, ¿también hace "
                                     f"«{d['texto_contada'][:70]}» antes de «{d['texto_sigue'][:60]}», o pasa directo?"),
                        "opciones": ["También lo hace y luego sigue", "Pasa directo"]})
    return out


def _conteo(frag: str):
    """{"n", "senal"} si la etapa empieza al llegar a un conteo."""
    m = _RX_CONTEO_N.search(frag)
    if not m:
        return None
    s = re.search(_SENAL, frag[max(0, m.start() - 40):m.end() + 25])
    return {"n": int(_numero(m.group(1))), "senal": _senal(s.group(1)) if s else None}


def _ciclo(g: dict, de_g: list):
    """Conteo dentro de una secuencia: las etapas que se repiten con cada
    objeto (desde la primera que espera un sensor) hasta la que empieza al
    llegar a N. g["ciclo"] = [n...], g["conteo"] = {"n", "senal", "etapa"}."""
    g["ciclo"], g["conteo"] = [], None
    k = next((i for i, e in enumerate(de_g) if e.get("conteo")), None)
    if k is None or k == 0:
        return
    c = dict(de_g[k]["conteo"])
    if not c.get("senal"):
        c["senal"] = next((e["entra_con"] for e in reversed(de_g[:k]) if e["entra_con"] in ("S1", "S2")), None)
    ini = next((i for i in range(1, k) if de_g[i]["entra_con"] in ("S1", "S2")), None)
    if ini is None:
        ini = next((i for i in range(k) if de_g[i]["entra_con"] == c["senal"]), k - 1)
    g["ciclo"] = [e["n"] for e in de_g[ini:k]]
    g["conteo"] = {**c, "etapa": de_g[k]["n"]}


def _grupo(etapas, e) -> list:
    """Las etapas de la misma secuencia que e (sin grupo = todas)."""
    return [x for x in etapas if x.get("grupo", 1) == e.get("grupo", 1)]


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
            # "se detiene 5 s antes de continuar avanzando": despues vuelve a avanzar
            en_marcha = en_marcha or (mot[0] == "paro" and bool(e.get("reanuda")))
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
    if e.get("tiempo_indefinido") and not (e["tiempos"] or e["hasta"]):
        return False                       # "durante un tiempo determinado": hay que preguntarlo
    if e["tiempos"] or e["hasta"] or e.get("hasta_paro") or _solo_plumas(e) or _solo_paro(e):
        return True
    sig = etapas[i + 1] if i + 1 < len(etapas) else None
    return bool(sig and sig.get("grupo", 1) == e.get("grupo", 1) and sig["entra_con"])


def preguntas(det: dict, dispositivo: str = "banda") -> list:
    """Una pregunta (con opciones) por cada etapa que no dice cuando termina."""
    out = []
    if not det.get("aplica"):
        return out
    dudas = preguntas_dudas(det)
    if dudas:
        return dudas
    etapas = det["etapas"]
    for i, e in enumerate(etapas):
        if _sabe_cuando_termina(etapas, i):
            continue
        ultima = e is _grupo(etapas, e)[-1]
        if e.get("tiempo_indefinido"):
            out.append({"id": _clave_fin(e["n"]), "campo": _clave_fin(e["n"]),
                        "pregunta": f"No dijiste cuánto dura la etapa {e['n']} («{e['texto']}»). ¿Cuánto tiempo?",
                        "opciones": ["2 s", "3 s", "5 s", "10 s", "15 s"]})
            continue
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
    grupos = det.get("grupos") or []
    if dispositivo == "banda" and len(grupos) > 1:
        return _bloque_varias(det)
    filas = []
    for e in det["etapas"]:
        fin = []
        if e["tiempos"]:
            fin.append(f"dura {', '.join(_fmt(x) for x in e['tiempos'])} s")
        if e["hasta"]:
            fin.append(f"termina cuando {e['hasta']} se activa")
        if e.get("hasta_paro"):
            fin.append("sigue hasta el paro")
        if e["entra_con"] and e["n"] == det["etapas"][0]["n"]:
            fin.append(f"la secuencia ARRANCA con {e['entra_con']} (inicio = {e['entra_con']})")
        elif e["entra_con"]:
            fin.append(f"empieza cuando {e['entra_con']} se activa (el paso anterior avanza con flanco de esa senal)")
        if e.get("conteo"):
            fin.append(f"empieza cuando {e['conteo'].get('senal') or 'el sensor'} llega a {e['conteo']['n']}")
        if e.get("reanuda"):
            fin.append("despues la banda VUELVE a avanzar")
        if e.get("incrementa"):
            fin.append("aqui cuenta el objeto")
        if e.get("reinicia_contador"):
            fin.append("el contador vuelve a cero")
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
        reglas += _reglas_estado()
        g = (det.get("grupos") or [{}])[0]
        if g.get("conteo") and g.get("ciclo"):
            reglas += _receta_ciclo(det, g)
        elif det.get("conteo"):
            reglas += (" Hay un CONTEO: las etapas que se repiten con cada objeto hasta llegar a N forman un ciclo "
                       "dentro de la secuencia (el ultimo paso del ciclo vuelve con \"siguiente\" al primero) y un "
                       "contador CTn de esa senal (p. ej. S2, modo una_vez) sale del ciclo con \"alternativa\" "
                       "{\"senal\":\"CTn\",\"ir_a\":<paso siguiente al ciclo>}; reinicia el contador con "
                       + ("reset = SQ1_CICLO (la secuencia se repite y nunca llega a SQ1_FIN). Modo "
                          + (f"repetir con {det['veces']} repeticiones." if det["veces"] else "infinita con su paro.")
                          if det["veces"] or det["repite"] else
                          "reset = SQ1_FIN (o el paso que lo pida). Modo una_vez."))
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


def _reglas_estado() -> str:
    """Estados y transiciones: lo que suele salir mal en secuencias largas."""
    return (" TRANSICIONES: \"cuando S1 detecta\" = el paso que espera ese objeto avanza con \"flanco\" de S1 (si "
            "la banda debe moverse mientras espera, ese paso lleva \"marcha\"); \"durante N s\" / \"despues de "
            "N s\" = \"tiempo\" con N. ESTADOS: la banda solo avanza en los pasos con \"marcha\": si debe seguir "
            "avanzando entre etapas, cada paso lleva marcha; \"se detiene N s antes de continuar\" = un paso sin "
            "marcha de N s y el siguiente con marcha. Una pluma que ya se movio se queda donde quedo (no repitas "
            "su salida en pasos posteriores salvo que deba moverse otra vez). FIN: si la ultima etapa detiene la "
            "banda, la secuencia termina ahi (modo una_vez) y la banda queda detenida hasta un nuevo inicio; no "
            "pongas motor.arranque ni nada que la rearranque o repita la secuencia sola.")


def _receta_ciclo(det: dict, g: dict) -> str:
    c = g["conteo"]
    ciclo = g["ciclo"]
    fin_ciclo = ciclo[-1]
    sig = next((e for e in det["etapas"] if e["n"] == c["etapa"]), None)
    # Una secuencia que se repite NUNCA llega a SQ1_FIN: con ese reset el
    # contador se quedaba en N y en la 2a vuelta la accion salia con cada
    # objeto. SQ1_CICLO pulsa al cerrar cada vuelta (probado en el ST v7).
    sq = f"SQ{g.get('n') or 1}"
    if g.get("veces"):
        reset, modo = f"{sq}_CICLO", f"Modo repetir con {g['veces']} repeticiones"
    elif g.get("repite"):
        reset, modo = f"{sq}_CICLO", "Modo infinita con su paro"
    else:
        reset, modo = f"{sq}_FIN", "Modo una_vez"
    cuando = ("al terminar cada vuelta, para que la siguiente cuente desde cero" if reset.endswith("_CICLO")
              else "para que la siguiente corrida cuente desde cero")
    if g.get("ultimo") == "directo" and g.get("contada"):
        # el objeto N NO hace la etapa que dispara la senal contada: decide ANTES de ella
        k = g["contada"]
        return (f" CICLO CON CONTEO: las etapas {ciclo[0]}..{fin_ciclo} se repiten con CADA objeto; al llegar "
                f"{c.get('senal') or 'el sensor'} a {c['n']} se pasa DIRECTO a la etapa {c['etapa']} (el objeto "
                f"{c['n']} NO hace la etapa {k}). Receta (probada en el PLC): contador CT1 {{\"entrada\":\""
                f"{c.get('senal') or 'S2'}\",\"preset\":{c['n']},\"modo\":\"una_vez\",\"reset\":\"{reset}\"}}; "
                f"justo DESPUES del paso que espera {c.get('senal')} (antes del primer paso de la etapa {k}) un PASO "
                f"DE DECISION {{\"salidas\":[],\"motor\":[],\"avanza\":\"tiempo\",\"segundos\":0,\"alternativa\":"
                f"{{\"senal\":\"CT1\",\"ir_a\":<primer paso de la etapa {c['etapa']}>}}}} (si no, sigue a la etapa "
                f"{k}); el ultimo paso de la etapa {fin_ciclo} vuelve con \"siguiente\" al paso donde empieza la "
                f"etapa {ciclo[0]} (el que espera su objeto). El contador se reinicia con {reset} {cuando}. {modo}.")
    return (f" CICLO CON CONTEO: las etapas {ciclo[0]}..{fin_ciclo} se repiten con CADA objeto; la etapa "
            f"{c['etapa']} empieza cuando {c.get('senal') or 'el sensor'} llega a {c['n']}. Receta (probada en el "
            f"PLC): contador CT1 {{\"entrada\":\"{c.get('senal') or 'S2'}\",\"preset\":{c['n']},"
            f"\"modo\":\"una_vez\",\"reset\":\"{reset}\"}}; los pasos del ciclo en orden; despues del ultimo "
            f"paso de la etapa {fin_ciclo} un PASO DE DECISION {{\"salidas\":[],\"motor\":[],\"avanza\":"
            f"\"tiempo\",\"segundos\":0,\"siguiente\":<paso donde empieza la etapa {ciclo[0]} (el que espera "
            f"su objeto)>,\"alternativa\":{{\"senal\":\"CT1\",\"ir_a\":<primer paso de la etapa "
            f"{c['etapa']}>}}}}. Asi el objeto {c['n']} tambien hace la etapa {fin_ciclo} y luego sigue la "
            f"{c['etapa']}. No pongas la alternativa en un paso con acciones (las saltaria). El contador se "
            f"reinicia con {reset} {cuando} (no lo reinicies con I2 salvo que el usuario lo pida). {modo}.")


def _fila(e) -> str:
    fin = []
    if e["tiempos"]:
        fin.append(f"dura {', '.join(_fmt(x) for x in e['tiempos'])} s")
    if e["hasta"]:
        fin.append(f"termina cuando {e['hasta']} se activa")
    if e.get("hasta_paro"):
        fin.append("sigue hasta el paro")
    if e["entra_con"]:
        fin.append(f"empieza cuando {e['entra_con']} se activa")
    if not fin and _solo_plumas(e):
        fin.append(f"sin tiempo dicho: {PLUMA_S} s de recorrido de la pluma")
    req = "; ".join(r["desc"] for r in e["requisitos"]) or "sin salidas (espera)"
    return f"{e['n']}) «{e['texto']}» -> {req}" + (f" [{'; '.join(fin)}]" if fin else "")


def _bloque_varias(det: dict) -> str:
    """Varias secuencias INDEPENDIENTES en la misma instruccion (banda)."""
    partes = []
    for g in det["grupos"]:
        de_g = [e for e in det["etapas"] if e["n"] in g["etapas"]]
        modo = (f"se repite {g['veces']} veces: modo repetir" if g["veces"] else
                "se repite: modo infinita con su paro" if g["repite"] else
                "modo una_vez (o infinita si sigue hasta el paro)")
        partes.append(f"SECUENCIA SQ{g['n']} ({modo}):\n" + "\n".join(_fila(e) for e in de_g))
    reglas = ("Son secuencias INDEPENDIENTES: arma una secuencia por bloque (SQ1, SQ2...), cada una con su "
              "inicio y su paro, con un paso por etapa en ese orden. No las juntes en una sola. Si dos usan las "
              "mismas luces, la que arranca despues detiene a la otra (su inicio va en el paro de la otra). Un "
              "movimiento de plumas al arrancar una secuencia puede ir como evento o timer de su senal en vez "
              "de paso. Cada paso lleva TODAS las salidas encendidas en esa etapa y \"marcha\" si la banda "
              "avanza. Si una secuencia mueve la banda, NO pongas motor.arranque." + _reglas_estado())
    return ("ACCIONES EN ORDEN (el usuario pidio estas secuencias):\n" + "\n\n".join(partes) + "\n" + reglas)


def _fmt(x) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


# ── Verificacion ────────────────────────────────────────────────────────────
def _cadenas_banda(eng: dict) -> list:
    """Pasos de cada secuencia en orden de ejecucion (SQ2 que arranca con
    SQ1_FIN va detras de los de SQ1)."""
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
    return cadenas


def _es_decision(p) -> bool:
    return (isinstance(p, dict) and isinstance(p.get("alternativa"), dict) and not p.get("salidas")
            and not p.get("motor") and not float(p.get("segundos") or 0))


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
    if dispositivo != "banda":
        sq = (cfg or {}).get("sequence") or {}
        pasos = [{"salidas": p.get("outputs") or [], "segundos": p.get("duration_s")}
                 for p in lm._lista(sq.get("steps")) if isinstance(p, dict)]
        if not pasos:
            return [f"Pediste {len(etapas)} acciones en orden y el JSON no tiene \"sequence\": arma un paso por "
                    f"etapa en ese orden y deja \"outputs\": []."]
        cumple = lambda p, r: bool(set(p["salidas"]) & r["q"]) if r["tipo"] == "salida" else True
        return _verificar_cadena(etapas, pasos, cumple, dispositivo)

    eng = ((cfg or {}).get("band") or {}).get("engine") or {}
    # Los pasos de decision (0 s, sin acciones, con alternativa) no son etapas.
    cadenas = [[p for p in c if not _es_decision(p)] for c in _cadenas_banda(eng)]
    if not cadenas:
        return [f"Pediste {len(etapas)} acciones en orden y el JSON no tiene una secuencia: arma SQ1 con un "
                f"paso por etapa en ese orden (no las encadenes con eventos ni timers sueltos)."]
    # Salidas que el programa maneja FUERA de las secuencias (timers, eventos).
    fuera = set((eng.get("salidas") or {}).keys()) if isinstance(eng.get("salidas"), dict) else set()
    problemas = []
    for g in det.get("grupos") or [{"etapas": [e["n"] for e in etapas]}]:
        de_g = [e for e in etapas if e["n"] in g["etapas"]]
        # "cuando S2 detecte, sube las plumas y haga la secuencia...": las plumas
        # del arranque pueden ir como timer/evento de la senal, fuera de la secuencia.
        if (len(de_g) > 1 and de_g[0]["entra_con"] and _solo_plumas(de_g[0])
                and all(r["q"] & fuera for r in de_g[0]["requisitos"] if r["tipo"] == "salida")):
            de_g = de_g[1:]
        # Cada secuencia pedida se compara con la secuencia del JSON que mejor la cumple.
        problemas += min((_verificar_cadena(de_g, c, _cumple_banda, "banda") for c in cadenas), key=len)
    return problemas


def _verificar_cadena(etapas, pasos, cumple, dispositivo) -> list:
    seg = lambda p: p.get("segundos")
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
    """Nota corta para el usuario (el detalle tecnico se queda en el log)."""
    etapas = sorted({int(n) for p in problemas for n in re.findall(r"\betapa (\d+)", p)})
    cuales = (f" (etapa{'s' if len(etapas) > 1 else ''} "
              + ", ".join(str(n) for n in etapas[:-1]) + (" y " if len(etapas) > 1 else "")
              + f"{etapas[-1]})") if etapas else ""
    return (f"Puede que el orden no quede exacto{cuales}. Pruébalo y, si no hace lo que pediste, "
            "pide corregirlo.")


# ── Reparacion sin modelo: la espera del sensor quedo un paso corrida ───────
def reparar_esperas(resp: dict, det: dict):
    """El modelo suele poner "cuando S1 detecte" en el MISMO paso que hace la
    accion (las plumas se mueven de inmediato y LUEGO espera a S1) en vez de
    en el paso anterior. Aqui, sin llamar otra vez al modelo: el paso anterior
    a cada etapa que entra con una senal pasa a avanzar con flanco de esa
    senal. Devuelve una copia reparada, o None si no hubo nada que reparar.
    Quien llama debe volver a validar y verificar la copia antes de usarla."""
    import copy
    if not det.get("aplica") or not isinstance(resp, dict):
        return None
    rep = copy.deepcopy(resp)
    cadenas = [[p for p in c if isinstance(p, dict) and not _es_decision(p)] for c in _cadenas_banda(rep)]
    if not cadenas:
        return None
    cambios = 0
    for g in det.get("grupos") or [{"etapas": [e["n"] for e in det["etapas"]]}]:
        de_g = [e for e in det["etapas"] if e["n"] in g["etapas"]]
        mejor = min(cadenas, key=lambda c: len(_verificar_cadena(de_g, c, _cumple_banda, "banda")))
        pos, previo = 0, -1
        for e in de_g:
            j = next((k for k in range(pos, len(mejor)) if all(_cumple_banda(mejor[k], r) for r in e["requisitos"])),
                     None)
            if j is None:
                break
            s = e.get("entra_con")
            if s and previo >= 0 and j > 0 and not any(_mismo(p.get("senal"), s) for p in mejor[previo:j]):
                ant = mejor[j - 1]
                ant["avanza"], ant["senal"] = "flanco", s
                ant.pop("segundos", None)
                cambios += 1
            pos, previo = (j, j - 1) if _solo_paro(e) else (j + 1, j)
    return rep if cambios else None


# ── Etapas de la instruccion original (correcciones) ───────────────────────
def separar_aclaraciones(texto: str):
    """'texto [etapa_5_fin: 4 s; freq_hz: 20 Hz]' (como lo manda el front en el
    historial) -> ('texto', {clave: {"pregunta": "", "respuesta": valor}})."""
    m = re.search(r"\s*\[([^\[\]]*)\]\s*$", str(texto or ""))
    if not m:
        return str(texto or ""), {}
    acl = {}
    for par in m.group(1).split(";"):
        if ":" in par:
            k, v = par.split(":", 1)
            if k.strip() and v.strip():
                acl[k.strip()] = {"pregunta": "", "respuesta": v.strip()}
    return texto[:m.start()], acl


def de_historial(historial, dispositivo: str = "banda", acl: dict = None, posteriores=None) -> dict:
    """Las etapas de la instruccion MAS RECIENTE del historial que pidio acciones
    en orden ("la secuencia esta mal" se revisa contra lo que se pidio)."""
    hist = list(historial or [])
    for i in range(len(hist) - 1, -1, -1):
        texto, propias = separar_aclaraciones(hist[i])
        a = {**propias, **(acl or {})}
        d = detectar(texto, dispositivo, a)
        if d["aplica"]:
            # las instrucciones posteriores (correcciones) y la actual pueden responder sus dudas
            despues = [separar_aclaraciones(x)[0] for x in hist[i + 1:]] + list(posteriores or [])
            return con_respuestas_de_sesion(d, texto, dispositivo, a, despues)
    return detectar("", dispositivo, acl)
