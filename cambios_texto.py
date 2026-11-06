"""
cambios_texto.py — sustituciones que una instruccion POSTERIOR hace sobre lo ya
pedido, para que los requisitos y las etapas del historial no exijan lo viejo:

    "la luz ahora debe ser la amarilla en lugar de la roja"   -> Q5 -> Q4
    "cambia la roja por la verde"                              -> Q5 -> Q3
    "cambia los 10 segundos por 6 segundos"                    -> 10 s -> 6 s
    "que sean 6 s en vez de 10 s"                              -> 10 s -> 6 s

sustituciones(texto) -> {"q": [(Q_viejo, Q_nuevo)], "t": [(s_viejo, s_nuevo)]}
"""

import re
import unicodedata

_Q = {"verde": "Q3", "amarilla": "Q4", "amarillo": "Q4", "roja": "Q5", "rojo": "Q5"}
_COLOR = r"(verde|amarill[ao]|roj[ao])"
_NUM = r"(\d+(?:[.,]\d+)?)"
_SEG = r"\s*(?:s|seg|segundos?)\b"
_ART = r"(?:(?:la|el|los|las)\s+)?(?:luz\s+)?"

_RX_Q = [
    # Y en lugar/vez de Z
    re.compile(rf"\b{_ART}{_COLOR}\s+en\s+(?:lugar|vez)\s+de\s+{_ART}{_COLOR}\b"),
    # cambia Z por Y
    re.compile(rf"\bcambi\w*\s+{_ART}{_COLOR}\s+por\s+{_ART}{_COLOR}\b"),
]
_RX_T = [
    re.compile(rf"\b{_NUM}{_SEG}\s+en\s+(?:lugar|vez)\s+de\s+(?:los\s+)?{_NUM}{_SEG}"),
    re.compile(rf"\bcambi\w*\s+(?:los\s+)?{_NUM}{_SEG}\s+(?:por|a)\s+(?:los\s+)?{_NUM}{_SEG}"),
    re.compile(rf"\ben\s+(?:lugar|vez)\s+de\s+(?:los\s+)?{_NUM}{_SEG}\s*,?\s*(?:que\s+sean|sean|pon\w*)\s+{_NUM}{_SEG}"),
]


# "La espera con la amarilla debe ser de 5 segundos", "que la roja dure 4 s": nuevo
# tiempo SIN decir el anterior; se aplica por CONTEXTO (la luz/pluma que nombra).
_RX_TIEMPO_NUEVO = re.compile(rf"\b(?:debe|tiene que|deberia)\s+(?:ser|durar|tardar)\s+(?:de\s+)?{_NUM}{_SEG}|"
                              rf"\bque\s+(?:sea|dure|tarde)\s+(?:de\s+)?{_NUM}{_SEG}|"
                              rf"\b(?:dure|durar|tarde)\s+{_NUM}{_SEG}")
_RX_CONTEXTO = re.compile(r"\b(verde|amarill[ao]|roj[ao]|pluma\s*[12]|espera|pausa|retardo|avance|marcha)\b")


def tiempo_por_contexto(texto: str) -> list:
    """[(palabras de contexto, segundos nuevos)] de frases que fijan un tiempo sin decir el anterior."""
    t = _fold(texto)
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


def _fold(t) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _f(x) -> float:
    return float(x.replace(",", "."))


def sustituciones(texto: str) -> dict:
    t = _fold(texto)
    q, s = [], []
    m = _RX_Q[0].search(t)
    if m:
        q.append((_Q[m.group(2)], _Q[m.group(1)]))       # (viejo, nuevo)
    m = _RX_Q[1].search(t)
    if m:
        q.append((_Q[m.group(1)], _Q[m.group(2)]))
    m = _RX_T[0].search(t)
    if m:
        s.append((_f(m.group(2)), _f(m.group(1))))
    for rx in _RX_T[1:]:
        m = rx.search(t)
        if m:
            s.append((_f(m.group(1)), _f(m.group(2))))
    return {"q": [x for x in q if x[0] != x[1]], "t": [x for x in s if x[0] != x[1]]}
