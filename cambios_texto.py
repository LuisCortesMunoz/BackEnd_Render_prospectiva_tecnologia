"""
cambios_texto.py — sustituciones que una instruccion POSTERIOR hace sobre lo ya
pedido, para que los requisitos y las etapas del historial no exijan lo viejo:

    "la luz ahora debe ser la amarilla en lugar de la roja"   -> Q5 -> Q4
    "cambia la roja por la verde"                              -> Q5 -> Q3
    "cambia los 10 segundos por 6 segundos"                    -> 10 s -> 6 s
    "que sean 6 s en vez de 10 s"                              -> 10 s -> 6 s
    "que arranque con I2 en lugar de I1"                       -> I1 -> I2
    "cambia el sensor 1 por el sensor 2"                       -> S1 -> S2

Hablado (lentes) la unidad no se repite y los numeros llegan con palabras:
    "cambia los 5 segundos a 6", "cambia los cinco segundos a seis",
    "cambia los 5 segundos que avanza a 6", "que avance 6 segundos en vez de 5",
    "cambia el tiempo de 5 a 6 segundos", "6 segundos, no 5"   -> 5 s -> 6 s
    "cambia los 30 Hz a 45", "cambia de 30 a 45 Hz"            -> 30 Hz -> 45 Hz

sustituciones(texto) -> {"q": [(Q_viejo, Q_nuevo)], "t": [(s_viejo, s_nuevo)],
                         "s": [(senal_vieja, senal_nueva)], "hz": [(hz_viejo, hz_nuevo)]}

corregir(texto) arregla errores de dictado y pasa a digitos los numeros dichos con
palabras cuando son un dato ("cinco segundos" -> "5 segundos", "a seis." -> "a 6.",
"cuarenta y cinco hertz" -> "45 hertz", "sensor dos" -> "sensor 2"); "las dos plumas"
o "una vez que" no se tocan.
"""

import re
import unicodedata

_Q = {"verde": "Q3", "amarilla": "Q4", "amarillo": "Q4", "roja": "Q5", "rojo": "Q5"}
_COLOR = r"(verde|amarill[ao]|roj[ao])"
_NUM = r"(\d+(?:[.,]\d+)?)"
_SEG = r"\s*(?:s|seg|segundos?)\b"
_ART = r"(?:(?:la|el|los|las)\s+)?(?:luz\s+)?"
# Boton (I1/I2) o sensor (S1/S2); "boton verde/rojo" no (se confunde con las luces).
_SEN = r"(?:(?:el|la|del)\s+)?((?:boton\s+|pulsador\s+)?i\s?[12]|(?:sensor\s?|s\s?)[12])\b"
_RX_S = [
    # Y en lugar/vez de Z
    re.compile(rf"\b{_SEN}\s+en\s+(?:lugar|vez)\s+del?\s+{_SEN}"),
    # cambia Z por Y
    re.compile(rf"\bcambi\w*\s+{_SEN}\s+por\s+{_SEN}"),
    # en lugar de Z, (usa|con|que sea) Y
    re.compile(rf"\ben\s+(?:lugar|vez)\s+del?\s+{_SEN}\s*,?\s*(?:usa|use|utiliza|con|que sea|sea|pon\w*)\s+{_SEN}"),
]


def _canon_senal(x: str) -> str:
    """"boton i2" -> I2, "sensor 1" / "s1" -> S1."""
    x = re.sub(r"\s+", "", x)
    return ("S" if x.startswith(("sensor", "s")) else "I") + x[-1]

_RX_Q = [
    # Y en lugar/vez de Z
    re.compile(rf"\b{_ART}{_COLOR}\s+en\s+(?:lugar|vez)\s+de\s+{_ART}{_COLOR}\b"),
    # cambia Z por Y
    re.compile(rf"\bcambi\w*\s+{_ART}{_COLOR}\s+por\s+{_ART}{_COLOR}\b"),
]
# Cambios de un valor por otro: (?P<v>) el viejo, (?P<n>) el nuevo. Hablado la unidad
# casi nunca se repite ("cambia los 5 segundos a 6"): basta con que la tenga UNO de los
# dos, y el otro no puede ser de otra cosa (Hz, veces, cajas...) ni una etiqueta
# ("la pluma 2", "el sensor 1").
_N = r"\d+(?:[.,]\d+)?"
_OTRA_UNIDAD = (r"%|hz|hertz|hercios|veces|vez|cajas?|piezas?|objetos?|productos?|detecciones|vueltas?|"
                r"ciclos?|min|minutos?|grados?|metros?|cm|s|seg|segs|segundos?")
_SIN_UNIDAD = rf"(?!\d)(?![.,]\d)(?!\s*(?:{_OTRA_UNIDAD})\b)"
_NO_ETIQUETA = (r"(?<![a-z])(?<!pluma )(?<!sensor )(?<!secuencia )(?<!paso )(?<!boton )(?<!salida )"
                r"(?<!entrada )(?<!contador )")
_SEG_OPC = rf"(?:{_SEG}|{_SIN_UNIDAD})"
_VERBO_CAMBIO = r"(?:cambi|aument|reduc|modific|ajust)\w*"
_RELL = r"[^\d.;]{0,40}?"            # palabras de por medio, sin otro numero ni fin de oracion


def _sub_valor(unidad: str, opcional: str) -> list:
    """Patrones de "valor viejo -> valor nuevo" para una unidad (segundos o Hz)."""
    return [
        # 6 s en lugar/vez de 5 (s)
        re.compile(rf"\b(?P<n>{_N}){unidad}\s+en\s+(?:lugar|vez)\s+de\s+(?:los\s+)?(?P<v>{_N}){opcional}"),
        # cambia los 5 s (que avanza) a/por 6 (s)
        re.compile(rf"\b{_VERBO_CAMBIO}\s+{_RELL}(?P<v>{_N}){unidad}{_RELL}\b(?:a|por)\s+(?:los\s+|unos\s+)?"
                   rf"{_NO_ETIQUETA}(?P<n>{_N}){opcional}"),
        # cambia el tiempo de 5 (s) a 6 s
        re.compile(rf"\b{_VERBO_CAMBIO}\s+{_RELL}\bde\s+(?P<v>{_N}){opcional}\s+a\s+(?P<n>{_N}){unidad}"),
        # en lugar de 5 s, que sean / que avance 6 (s)   |   en lugar de 5, 6 s
        re.compile(rf"\ben\s+(?:lugar|vez)\s+de\s+(?:los\s+)?(?P<v>{_N}){unidad}\s*,?{_RELL}{_NO_ETIQUETA}"
                   rf"(?P<n>{_N}){opcional}"),
        re.compile(rf"\ben\s+(?:lugar|vez)\s+de\s+(?:los\s+)?(?P<v>{_N}){_SIN_UNIDAD}\s*,?{_RELL}{_NO_ETIQUETA}"
                   rf"(?P<n>{_N}){unidad}"),
        # 6 s, no 5   |   no 5 s sino 6
        re.compile(rf"\b(?P<n>{_N}){unidad}\s*,?\s*(?:y\s+)?no\s+(?:los\s+|de\s+)?(?P<v>{_N}){opcional}"),
        re.compile(rf"\bno\s+(?:los\s+|de\s+)?(?P<v>{_N}){unidad}\s*,?\s*sino\s+(?:de\s+)?(?P<n>{_N}){opcional}"),
    ]


_RX_T = _sub_valor(_SEG, _SEG_OPC)
_HZ = r"\s*(?:hz|hertz|hercios)\b"
_RX_HZ_SUB = _sub_valor(_HZ, rf"(?:{_HZ}|{_SIN_UNIDAD})")


# "La espera con la amarilla debe ser de 5 segundos", "que la roja dure 4 s": nuevo
# tiempo SIN decir el anterior; se aplica por CONTEXTO (la luz/pluma que nombra).
_RX_TIEMPO_NUEVO = re.compile(rf"\b(?:debe|tiene que|deberia)\s+(?:ser|durar|tardar)\s+(?:de\s+)?{_NUM}{_SEG}|"
                              rf"\bque\s+(?:sea|dure|tarde)\s+(?:de\s+)?{_NUM}{_SEG}|"
                              rf"\b(?:dure|durar|tarde)\s+{_NUM}{_SEG}")
_RX_CONTEXTO = re.compile(r"\b(verde|amarill[ao]|roj[ao]|pluma\s*[12]|espera|pausa|retardo|avance|marcha)\b")


# "Mejor que sean 12 segundos", "que la verde dure solo 3 s", "la espera debe ser de 5 s":
# un tiempo NUEVO sin decir el anterior, con o sin contexto (luz, pluma, espera...).
_RX_TIEMPO_SOLO = re.compile(rf"\b(?:(?:mejor|ahora)\s+)?que\s+(?:sean|duren?|tarden?|esperen?)\s+(?:solo\s+|solamente\s+|unos\s+)?"
                             rf"(?:de\s+)?{_NUM}{_SEG}|"
                             rf"\b(?:mejor|ahora)\s+(?:sean|pon\w*)\s+{_NUM}{_SEG}|"
                             rf"\b(?:debe|deben|tiene que|tienen que|deberia)\s+(?:ser|durar|tardar)\s+(?:de\s+)?"
                             rf"(?:solo\s+)?{_NUM}{_SEG}|"
                             rf"\b(?:dure|duren|durar|tarde)\s+(?:solo\s+|solamente\s+)?{_NUM}{_SEG}|"
                             # "cambia el tiempo a 6 s", "cambia la espera de la pluma a 4 segundos"
                             rf"\b{_VERBO_CAMBIO}\s+(?:(?:el|la|los)\s+)?(?:tiempo|duracion|retardo|espera|pausa|"
                             rf"avance|marcha)\b[^\d.;]{{0,30}}?\b(?:a|por)\s+(?:unos\s+)?{_NUM}{_SEG}|"
                             # "cambialo a 6 segundos"
                             rf"\bcambi\w*(?:lo|la|los|las)\s+a\s+(?:unos\s+)?{_NUM}{_SEG}|"
                             # "ahora que avance 6 segundos"
                             rf"\b(?:mejor|ahora)\s+que\s+(?:avance|avancen|siga|ande|se mueva|espere|se detenga|"
                             rf"dure|tarde)\s+(?:solo\s+|solamente\s+|unos\s+)?(?:de\s+)?{_NUM}{_SEG}|"
                             # el mensaje entero es "con 6 segundos" (lo que queda de "regresala como
                             # estaba pero con 6 segundos" en los lentes)
                             rf"^\s*(?:pero\s+)?con\s+(?:solo\s+)?{_NUM}{_SEG}\s*[.!]?\s*$")


def tiempo_nuevo(texto: str):
    """(palabras de contexto, segundos) si el texto fija UN tiempo nuevo sin decir el
    anterior ("mejor que sean 12 segundos"); None si no (o si dice el anterior)."""
    t = _fold(corregir(texto))
    if any(rx.search(t) for rx in _RX_T):
        return None
    ms = list(_RX_TIEMPO_SOLO.finditer(t))
    if len(ms) != 1:
        return None
    m = ms[0]
    n = next(g for g in m.groups() if g)
    oracion = re.split(r"[.;]", t[:m.start()])[-1] + t[m.start():m.end()]
    ctx = {re.sub(r"\s+", " ", c) for c in _RX_CONTEXTO.findall(oracion)}
    return ctx, _f(n)


def tiempo_por_contexto(texto: str) -> list:
    """[(palabras de contexto, segundos nuevos)] de frases que fijan un tiempo sin decir el anterior."""
    t = _fold(corregir(texto))
    if any(rx.search(t) for rx in _RX_T):
        return []                                    # ya dice el anterior: lo cubre sustituciones()
    out = []
    for m in _RX_TIEMPO_NUEVO.finditer(t):
        n = next(g for g in m.groups() if g)
        oracion = re.split(r"[.;]", t[:m.start()])[-1] + t[m.start():m.end()]
        ctx = {re.sub(r"\s+", " ", c) for c in _RX_CONTEXTO.findall(oracion)}
        if ctx:
            out.append((ctx, _f(n)))
    return out


# Errores de escritura frecuentes (dictado, teclado de los lentes) que confunden al
# modelo y al extractor ("se detenga por sis ola" lo leian como un dato). Se corrigen
# en el texto ANTES de usarlo; lo demas queda tal cual.
_TYPOS = [
    (re.compile(r"\bpor\s+sis\s+ola\b", re.I), "por sí sola"),
    (re.compile(r"\bsis\s+ola\b", re.I), "sí sola"),
    (re.compile(r"\bpor\s*sis\b", re.I), "por sí"),
    (re.compile(r"\bporsi\b", re.I), "por sí"),
    (re.compile(r"\bbotn\b", re.I), "botón"),
    (re.compile(r"\bboto\b", re.I), "botón"),
    (re.compile(r"\bsu?saurio\b", re.I), "usuario"),
    (re.compile(r"\busaurio\b", re.I), "usuario"),
    (re.compile(r"\bsensr\b", re.I), "sensor"),
    (re.compile(r"\bsenosr\b", re.I), "sensor"),
    (re.compile(r"\bsegudnos\b", re.I), "segundos"),
]


# ── Numeros dichos con palabras (la voz de los lentes los escribe asi) ──────
_PAL_NUM = {
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9,
    "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintiuna": 21,
    "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26,
    "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100,
}
_DECENAS = ("treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa")
_UNO = ("uno", "una", "un")


def _patron(palabra: str) -> str:
    """'veintiseis' -> 'v[eé][ií]nt...' (con o sin acento)."""
    return "".join({"a": "[aá]", "e": "[eé]", "i": "[ií]", "o": "[oó]", "u": "[uúü]"}.get(c, c) for c in palabra)


def _alternativa(palabras) -> str:
    return "(?:" + "|".join(_patron(p) for p in sorted(palabras, key=len, reverse=True)) + ")"


_PN = _alternativa(_PAL_NUM)
_PU = _alternativa(_UNO)
_UNIDAD_DATO = (r"segundos?|segs?|minutos?|mins?|hz|hertz|hercios|veces|cajas?|piezas?|objetos?|productos?|"
                r"detecciones|vueltas?|ciclos?")
_RX_PAL = [
    # "treinta y cinco" -> 35
    (re.compile(rf"\b({_alternativa(_DECENAS)})\s+y\s+({_PN}|{_PU})\b", re.I), "compuesto"),
    # con unidad detras: "cinco segundos", "cuarenta hz", "tres cajas", "un segundo"
    (re.compile(rf"\b({_PN})(?=\s+(?:{_UNIDAD_DATO})\b)", re.I), "solo"),
    (re.compile(rf"\b({_PU})(?=\s+(?:segundos?|minutos?)\b)", re.I), "solo"),
    # etiqueta delante: "sensor dos", "pluma uno", "secuencia dos"
    (re.compile(rf"\b(sensor|pluma|secuencia|salida|entrada|bot[oó]n|contador|paso)\s+({_PN}|uno)\b", re.I),
     "etiqueta"),
    # valor suelto al final de la frase: "a seis.", "por seis", "que sean seis", "en vez de cinco"
    (re.compile(rf"\b(a|por|sean|sea|de)\s+({_PN})(?=\s*(?:[.,;:!?]|$|\b(?:y|e|pero|por favor|porfa|gracias|"
                rf"mejor|ahora|entonces|sino|no)\b))", re.I), "etiqueta"),
    # antes de otro valor: "de tres a 5", "entre cinco y 6 segundos" ("las dos a la vez" no)
    (re.compile(rf"(?<!\blas )(?<!\blos )\b({_PN})(?=\s+(?:a|por|y|o|hasta)\s+\d)", re.I), "solo"),
]


def _valor(palabra: str) -> int:
    p = _fold(palabra)
    return 1 if p in _UNO else _PAL_NUM[p]


def numeros_a_digitos(texto: str) -> str:
    t = str(texto or "")
    for rx, modo in _RX_PAL:
        if modo == "compuesto":
            t = rx.sub(lambda m: str(_valor(m.group(1)) + _valor(m.group(2))), t)
        elif modo == "etiqueta":
            t = rx.sub(lambda m: f"{m.group(1)} {_valor(m.group(2))}", t)
        else:
            t = rx.sub(lambda m: str(_valor(m.group(1))), t)
    return t


def corregir(texto: str) -> str:
    t = str(texto or "")
    for rx, bien in _TYPOS:
        t = rx.sub(bien, t)
    return numeros_a_digitos(t)


def _fold(t) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _f(x) -> float:
    return float(x.replace(",", "."))


def _pares(rxs, t: str) -> list:
    """[(viejo, nuevo)] sin repetir, en el orden en que aparecen."""
    out = []
    for rx in rxs:
        for m in rx.finditer(t):
            par = (_f(m.group("v")), _f(m.group("n")))
            if par not in out:
                out.append(par)
    return out


def sustituciones(texto: str) -> dict:
    t = _fold(corregir(texto))
    q = []
    m = _RX_Q[0].search(t)
    if m:
        q.append((_Q[m.group(2)], _Q[m.group(1)]))       # (viejo, nuevo)
    m = _RX_Q[1].search(t)
    if m:
        q.append((_Q[m.group(1)], _Q[m.group(2)]))
    s = _pares(_RX_T, t)
    hz = _pares(_RX_HZ_SUB, t)
    sen = []
    m = _RX_S[0].search(t)
    if m:
        sen.append((_canon_senal(m.group(2)), _canon_senal(m.group(1))))
    for rx in _RX_S[1:]:
        m = rx.search(t)
        if m:
            sen.append((_canon_senal(m.group(1)), _canon_senal(m.group(2))))
    return {"q": [x for x in q if x[0] != x[1]], "t": [x for x in s if x[0] != x[1]],
            "s": [x for x in sen if x[0] != x[1]], "hz": [x for x in hz if x[0] != x[1]]}
