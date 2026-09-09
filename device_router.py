"""
============================================================================
SELECTOR DE EQUIPO  (MODO MALETIN / MODO BANDA)
============================================================================

Decide a que PLC pertenece una instruccion en lenguaje natural ANTES de
generar nada:

    instruccion -> identificar equipo -> maletin | banda | (ambigua: preguntar)

Es el espejo en Python de assets/js/ladder/equipment.js del frontend: mismos
vocabularios y mismo criterio, para que el backend y el editor nunca decidan
cosas distintas sobre el mismo texto.

Regla central: NUNCA adivinar. Si la instruccion puede aplicar a los dos
equipos ("enciende una lampara", "usa un temporizador de 5 segundos"), se
devuelve None y quien llama pregunta "¿maletin o banda?".
============================================================================
"""

import re
import unicodedata


MALETIN = "maletin"
BANDA = "banda"
DISPOSITIVOS = (MALETIN, BANDA)


def normalizar(texto: str) -> str:
    """Minusculas y sin acentos, para que las expresiones sean simples."""
    t = str(texto or "").lower()
    t = unicodedata.normalize("NFD", t)
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# --- Vocabularios ----------------------------------------------------------
# EXCLUSIVO de la banda: si aparece, la instruccion es de la banda.
BANDA_TERMS = [
    r"\bbandas?\b", r"\btransportador", r"\bcintas?\b",
    r"\bvfd\b", r"\bvariador",
    r"\bs\s?[12]\b", r"\bsensor(?:es)?\s*(?:1|2|uno|dos)\b",
    r"\btorreta\b",
    r"\bfrecuencias?\b", r"\b\d+(?:[.,]\d+)?\s*hz\b", r"\bhertz\b",
    r"\bderecha\b", r"\bizquierda\b", r"\bhorario\b", r"\bantihorario\b",
]

# EXCLUSIVO del maletin: si aparece, la instruccion es del maletin.
MALETIN_TERMS = [
    r"\bmaletin\b",
    r"\bi\s?1\b", r"\bi\s?2\b", r"\bi\s?7\b",
    r"\benclav", r"\bcontador", r"\bsecuencia", r"\bsemaforo",
    r"\bparo de emergencia\b",
]

# COMPARTIDO: existe en los dos equipos, no decide por si solo. Su presencia
# sin ningun termino exclusivo es justo el caso ambiguo.
COMUNES_TERMS = [
    r"\blampara", r"\bluces?\b", r"\bluz\b",
    r"\bq\s?1[012]\b", r"\bverde\b", r"\bamarilla\b", r"\broj[ao]\b",
    r"\bsensor", r"\bmotor", r"\bsistema\b", r"\bpieza",
    r"\bi\s?3\b", r"\bi\s?4\b",
    r"\btemporizador", r"\btimer\b", r"\bsalida\b", r"\bentrada\b",
    # "boton"/"pulsador" no deciden solos: el PLC de la banda no tiene botones,
    # pero "activa una salida cuando se presione un boton" debe preguntar, no
    # asumir. Si la frase trae ademas I1/I2/I7 o la palabra "maletin", si hay
    # termino exclusivo y se resuelve sin preguntar.
    r"\bbot(?:on|ones)\b", r"\bpulsador", r"\bselector",
]

_BANDA_RE = [re.compile(p) for p in BANDA_TERMS]
_MALETIN_RE = [re.compile(p) for p in MALETIN_TERMS]
_COMUNES_RE = [re.compile(p) for p in COMUNES_TERMS]


def _aciertos(texto: str, patrones) -> int:
    return sum(1 for r in patrones if r.search(texto))


def detectar_dispositivo(texto: str) -> dict:
    """¿A que equipo pertenece la instruccion?

    Devuelve {"device": "maletin"|"banda"|None, "motivo": str}.
    device None significa AMBIGUA: hay que preguntarle al usuario."""
    t = normalizar(texto)
    banda = _aciertos(t, _BANDA_RE)
    maletin = _aciertos(t, _MALETIN_RE)

    if banda and not maletin:
        return {"device": BANDA, "motivo": "terminos exclusivos de la banda"}
    if maletin and not banda:
        return {"device": MALETIN, "motivo": "terminos exclusivos del maletin"}
    if banda and maletin:
        return {"device": None, "motivo": "mezcla terminos de los dos equipos"}

    # Sin terminos exclusivos: solo se pregunta si hay algo que de verdad
    # pueda ir en cualquiera de los dos. Sin ninguna señal se conserva el
    # comportamiento historico (el maletin).
    if _aciertos(t, _COMUNES_RE):
        return {"device": None, "motivo": "solo terminos comunes a los dos equipos"}
    return {"device": MALETIN, "motivo": "sin senales de banda: flujo del maletin"}


def normalizar_dispositivo(valor):
    """Acepta lo que mande el frontend ('banda', 'Maletín', 'maletin_basico'…)
    y lo reduce al identificador canonico, o None si no se reconoce."""
    if valor is None:
        return None
    t = normalizar(valor).strip()
    if not t:
        return None
    if t.startswith("banda") or "transportador" in t or "cinta" in t:
        return BANDA
    if t.startswith("maletin"):
        return MALETIN
    return None


def resolver_dispositivo(texto: str, device=None) -> dict:
    """Punto UNICO de decision del equipo.

    Prioridad: lo que el usuario ya eligio explicitamente (device) por encima
    de cualquier deteccion sobre el texto. Asi, una vez seleccionado el equipo
    tras una pregunta de desambiguacion, el flujo se queda en ese PLC."""
    explicito = normalizar_dispositivo(device)
    if explicito:
        return {"device": explicito, "motivo": "seleccionado por el usuario",
                "ambiguo": False}
    det = detectar_dispositivo(texto)
    return {"device": det["device"], "motivo": det["motivo"],
            "ambiguo": det["device"] is None}


def pregunta_equipo() -> dict:
    """Pregunta de desambiguacion, con el mismo formato que ya consumen
    chat.js / copilot.js para 'needs_clarification'."""
    return {
        "slot": "equipo",
        "pregunta": "¿Quieres programar el maletin o la banda transportadora?",
        "opciones": ["Maletin", "Banda transportadora"],
    }
