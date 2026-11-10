"""
cobertura.py — lo que pide el MENSAJE ACTUAL aparece en la logica (banda v7).

Las pruebas simuladas (comportamiento, etapas) solo cubren lo que saben leer;
lo demas puede perderse sin aviso (banco N12: la receta de conteo ignoraba
"cambia de sentido"; N2: "mejor que sean 12 segundos" se quedaba en 8 s). Aqui
se comprueba algo mas simple y general: cada dato concreto del mensaje esta en
la logica compilada.

  tiempos    "8 segundos", "2 min"        -> algun tiempo de la logica vale eso
  Hz         "a 25 hertz"                 -> frecuencia de la banda
  sentido    "invierte", "cambia de sentido", "reversa" -> invertir / el otro sentido
             "hacia la izquierda" / "derecha"            -> ese sentido en algun lado
  luces      verde / amarilla / roja      -> su salida se usa
  plumas     "pluma 1" / "pluma 2"        -> alguna de sus salidas se usa
  parpadeo   "parpadea", "intermitente"   -> un timer INTERMITENTE

Lo que el mismo mensaje reemplaza ("6 s en vez de 10 s", "la amarilla en lugar de
la roja") o quita ("ya no uses la pluma 2") no se exige. Solo mira el mensaje
actual: lo del historial lo cuidan la regresion y los requisitos.

faltantes(texto, cfg) -> ["12 s", "cambio de sentido", ...]   ([] = todo esta)
"""

import json
import re
import unicodedata

import cambios_texto

_Q_COLOR = {"verde": "Q3", "amarill": "Q4", "roj": "Q5"}
_Q_PLUMA = {"1": ("Q8", "Q9"), "2": ("Q6", "Q7")}
_CLAVES_TIEMPO = {"segundos", "segundos_off", "duracion_s", "espera_s", "paro_inversion_s"}

_NEG = re.compile(r"\b(?:ya no|quita\w*|elimina\w*|sin|borra\w*|deja de|no uses|no use)\b[^.;]{0,25}$")
_RX_SEG = re.compile(r"(\d+(?:[.,]\d+)?)\s*(segundos?|seg|s|minutos?|min)\b")
_RX_HZ = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:hz|hertz)\b")
_RX_INV = re.compile(r"\b(?:inviert\w*|invertir|invierta\w*|cambi\w* (?:de |el )?sentido|sentido contrario|"
                     r"(?:en |de )?reversa|hacia atras|retroced\w*)\b")
_RX_DIR = re.compile(r"\b(?:hacia|a|para) la (izquierda|derecha)\b")
_RX_COLOR = re.compile(r"\b(verde|amarill[ao]s?|roj[ao]s?)\b")
_RX_BOTON = re.compile(r"\b(?:boton|botones|pulsador|pulsadores)\s+(?:de\s+)?(?:color\s+)?$")
_RX_PLUMA = re.compile(r"\bpluma\s*(1|2|uno|dos)\b")
_RX_PARPADEA = re.compile(r"\b(?:parpade\w*|intermit\w*|destell\w*)\b")


def _fold(t) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _negado(t: str, pos: int) -> bool:
    return bool(_NEG.search(t[max(0, pos - 40):pos]))


def _tiempos(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            if k in _CLAVES_TIEMPO and isinstance(v, (int, float)) and not isinstance(v, bool):
                out.add(round(float(v), 2))
            else:
                _tiempos(v, out)
    elif isinstance(o, list):
        for x in o:
            _tiempos(x, out)
    return out


def faltantes(texto: str, cfg: dict) -> list:
    band = (cfg or {}).get("band") or {}
    eng = band.get("engine") or {}
    if not eng:
        return []
    t = _fold(cambios_texto.corregir(texto))
    s_eng = json.dumps(eng, ensure_ascii=False).upper()
    subs = cambios_texto.sustituciones(texto)
    viejos_t = {round(a, 2) for a, _ in subs["t"]}
    viejos_q = {a for a, _ in subs["q"]}
    out = []

    tiempos = _tiempos(eng, set())
    for m in _RX_SEG.finditer(t):
        if _negado(t, m.start()):
            continue
        v = float(m.group(1).replace(",", ".")) * (60 if m.group(2).startswith("min") else 1)
        v = round(v, 2)
        if v in viejos_t or v <= 0:
            continue
        if not any(abs(v - x) < 0.06 for x in tiempos):
            out.append(f"{v:g} s")

    hz = [float(m.group(1).replace(",", ".")) for m in _RX_HZ.finditer(t) if not _negado(t, m.start())]
    if hz and band.get("enable") is not False:
        f = band.get("freq_hz")
        if f is None or not any(abs(float(f) - x) < 0.6 for x in hz):
            out.append(f"{hz[-1]:g} Hz")

    base = "dir_2" if str(band.get("direction") or "derecha").lower().startswith("izq") else "dir_1"
    otro = "DIR_1" if base == "dir_2" else "DIR_2"
    for m in _RX_INV.finditer(t):
        if not _negado(t, m.start()):
            if "INVERTIR" not in s_eng and otro not in s_eng:
                out.append("cambio de sentido")
            break
    for m in _RX_DIR.finditer(t):
        if _negado(t, m.start()) or re.search(r"\ben (?:lugar|vez) de\s*$", t[max(0, m.start() - 15):m.start()]):
            continue
        lado = m.group(1)
        q = "DIR_2" if lado == "izquierda" else "DIR_1"
        # "invierte el sentido y avanza a la izquierda": con invertir cualquier lado queda cubierto.
        if not (str(band.get("direction") or "derecha").lower().startswith(lado[:3]) or q in s_eng
                or "INVERTIR" in s_eng):
            out.append(f"sentido {lado}")

    vistos = set()
    for m in _RX_COLOR.finditer(t):
        antes = t[max(0, m.start() - 22):m.start()]
        # "con el verde", "el rojo la apaga": con articulo masculino es el BOTON (la luz es
        # "la verde", "la roja", "luz/lampara/foco ...").
        if _negado(t, m.start()) or _RX_BOTON.search(antes) or re.search(r"\b(?:el|del|al)\s+$", antes):
            continue
        q = next(v for k, v in _Q_COLOR.items() if m.group(1).startswith(k))
        if q in viejos_q or q in vistos:
            continue
        vistos.add(q)
        if f'"{q}"' not in s_eng and f"{q}" not in s_eng:
            out.append(f"luz {m.group(1)} ({q})")

    for m in _RX_PLUMA.finditer(t):
        if _negado(t, m.start()):
            continue
        n = "1" if m.group(1) in ("1", "uno") else "2"
        if not any(q in s_eng for q in _Q_PLUMA[n]):
            out.append(f"pluma {n}")
            break

    m = _RX_PARPADEA.search(t)
    if m and not _negado(t, m.start()) and "INTERMITENTE" not in s_eng:
        out.append("parpadeo")
    return list(dict.fromkeys(out))


def mensaje(falta: list) -> str:
    return ("La logica NO incluye lo que el usuario pidio en este mensaje: " + "; ".join(falta)
            + ". Agregalo (sin perder lo demas). Devuelve el JSON COMPLETO, sin texto extra.")


def aviso(falta: list) -> str:
    return ("No encontré en la lógica: " + ", ".join(falta)
            + ". Revísalo y, si falta, pide agregarlo.")
