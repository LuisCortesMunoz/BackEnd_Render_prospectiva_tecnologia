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
