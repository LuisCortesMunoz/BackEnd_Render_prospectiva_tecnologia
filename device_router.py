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
    r"\btorreta\b", r"\bplumas?\b",
    r"\bfrecuencias?\b", r"\b\d+(?:[.,]\d+)?\s*hz\b", r"\bhertz\b",
    r"\bderecha\b", r"\bizquierda\b", r"\bhorario\b", r"\bantihorario\b",
    r"\bavanz", r"\bparo automatico\b", r"\bparo (?:por )?software\b",
]

# Nombre explicito del equipo (regla prioritaria de detectar_dispositivo).
_NOMBRE_BANDA_RE = re.compile(r"\bbandas?\b|\btransportadora?s?\b")
_NOMBRE_MALETIN_RE = re.compile(r"\bmaletin(?:es)?\b")

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


# PROCESO INDUSTRIAL (capa B, contexto industrial): vocabulario CONCEPTUAL que
# solo tiene sentido sobre una linea transportadora. El maletin no transporta,
# no clasifica ni empaqueta nada, asi que estas palabras apuntan a la banda.
# Se consulta DESPUES de los terminos exclusivos de los dos equipos: un termino
# del maletin ("enclavamiento", "secuencia", "contador", I1/I2/I7) sigue
# ganando, de modo que ninguna instruccion que antes iba al maletin cambia.
PROCESO_TERMS = [
    r"\bempaqu", r"\bempac", r"\bembalaj", r"\bencajad",
    r"\bpaletiz", r"\bpalletiz", r"\bpallets?\b", r"\btarimas?\b",
    r"\bclasific", r"\bsorting\b", r"\bdesviador", r"\bdesvi[ao]\b",
    r"\balmacen", r"\bbodega", r"\blogistic", r"\bpaqueteria",
    r"\blotes?\b", r"\bbatch\b", r"\bproduccion\b", r"\bmanufactur",
    r"\btransport", r"\bembotell", r"\benvasad", r"\bllenad",
    r"\binspeccion", r"\brechaz", r"\bbuffer\b", r"\bacumula",
    r"\bcongestion", r"\bestacion(?:es)?\b", r"\bautomotriz\b", r"\bautomotive\b",
    r"\bcajas?\b", r"\bpaquetes?\b", r"\bbotellas?\b",
    # Referencias conceptuales por industria (§12): solo sirven para inferir
    # industria y arquetipos, nunca para copiar el proceso real de la empresa.
    r"\bpepsico\b", r"\bpepsi\b", r"\bcoca[\s-]?cola\b", r"\bnestle\b", r"\bbimbo\b",
    r"\bamazon\b", r"\bdhl\b", r"\bfedex\b", r"\bups\b",
    r"\btoyota\b", r"\bford\b", r"\bvolkswagen\b", r"\bbmw\b",
    r"\bsamsung\b", r"\bfoxconn\b",
]

_BANDA_RE = [re.compile(p) for p in BANDA_TERMS]
_PROCESO_RE = [re.compile(p) for p in PROCESO_TERMS]
_MALETIN_RE = [re.compile(p) for p in MALETIN_TERMS]
_COMUNES_RE = [re.compile(p) for p in COMUNES_TERMS]


def _aciertos(texto: str, patrones) -> int:
    return sum(1 for r in patrones if r.search(texto))


def detectar_dispositivo(texto: str) -> dict:
    """¿A que equipo pertenece la instruccion?

    Devuelve {"device": "maletin"|"banda"|None, "motivo": str}.
    device None significa AMBIGUA: hay que preguntarle al usuario."""
    t = normalizar(texto)

    # REGLA PRIORITARIA: nombrar el equipo decide. "banda" (o "transportadora")
    # sin "maletin" es SIEMPRE la banda, aunque la frase traiga I1/I2 u otras
    # palabras que por si solas apuntarian al maletin ("banda prende la verde
    # con I1", "deten la banda con I2"). Con los dos nombres se pregunta.
    nombra_banda = bool(_NOMBRE_BANDA_RE.search(t))
    nombra_maletin = bool(_NOMBRE_MALETIN_RE.search(t))
    if nombra_banda and nombra_maletin:
        return {"device": None, "motivo": "nombra la banda y el maletin"}
    if nombra_banda:
        return {"device": BANDA, "motivo": "la instruccion nombra la banda"}

    banda = _aciertos(t, _BANDA_RE)
    maletin = _aciertos(t, _MALETIN_RE)

    if banda and not maletin:
        return {"device": BANDA, "motivo": "terminos exclusivos de la banda"}
    if maletin and not banda:
        return {"device": MALETIN, "motivo": "terminos exclusivos del maletin"}
    if banda and maletin:
        return {"device": None, "motivo": "mezcla terminos de los dos equipos"}

    # Sin terminos exclusivos de ningun equipo: un proceso industrial
    # (empaquetado, clasificacion, paletizado, "tipo PepsiCo"...) solo lo puede
    # ejecutar la banda. Este nivel es NUEVO y va por debajo de todo lo
    # anterior, asi que no altera ninguna deteccion que ya funcionaba.
    if _aciertos(t, _PROCESO_RE):
        return {"device": BANDA, "motivo": "describe un proceso industrial de linea"}

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
