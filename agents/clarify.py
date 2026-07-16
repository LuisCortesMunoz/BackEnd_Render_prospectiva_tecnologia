"""agents/clarify.py — Deteccion de prompts ambiguos / incompletos (Fase 1).

Analiza la peticion del usuario contra el PERFIL del dispositivo y determina si
falta informacion CRITICA para generar logica sin inventar datos. En vez de
adivinar (p. ej. "prende una lampara" -> inventar I1/Q10), el agente pide solo
lo indispensable.

Diseno conservador para NO romper lo que ya funciona:
- Solo se consideran criticos la SALIDA y la ENTRADA (los dos anclajes mas
  comunes que faltan). El tipo de activacion y el paro conservan los defaults
  actuales del generador (no bloquean).
- Si la peticion ya nombra una salida y una entrada concretas, NO es ambigua y
  el flujo sigue exactamente igual que hoy.
- Es 100% determinista (regex + palabras clave derivadas del perfil); no hace
  llamadas al LLM.

Devuelve un dict de analisis; app.py decide con el campo 'ambiguous'.
"""

import re
import unicodedata

# Palabras genericas (referencias sin especificar). Son agnosticas del perfil.
_LAMP_WORDS = ("lampara", "luz", "foco", "led", "bombilla", "salida")
_BTN_WORDS  = ("boton", "pulsador", "interruptor", "selector", "entrada", "sensor")

# Palabras clave de intencion (solo para poblar 'slots'; NO bloquean por si solas).
_LATCH = ("enclav", "se queda", "quede", "se mantiene", "mantenga", "permanece",
          "sigue prendid", "retencion", "sello", "latch", "hasta que", "hasta presionar")
_DIRECT = ("mientras", "momentane", "solo cuando", "presionar y soltar")
_SEQ = ("semaforo", "secuencia", "uno tras otro", "primero", "despues", "luego",
        "al mismo tiempo", "etapa")
_COUNT = ("cuenta", "conteo", "pulsos", " veces")
_OFF_ALL = ("apaga todo", "apagar todo", "todo apagado", "apaga las lamparas",
            "apagar las lamparas")

_TIME_RE = re.compile(r"(\d+)\s*(segundos?|seg|s|minutos?|min|m)\b")


def _fold(s: str) -> str:
    """minusculas sin acentos, para comparar de forma robusta."""
    s = (s or "").lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _find_inputs(t: str, perfil: dict) -> list:
    found = []
    for e in perfil.get("inputs", []):
        iid = str(e.get("id", ""))
        if iid and re.search(rf"\b{re.escape(_fold(iid))}\b", t):
            found.append(e["id"])
    return list(dict.fromkeys(found))


def _find_outputs(t: str, perfil: dict) -> list:
    found = []
    for o in perfil.get("outputs", []):
        oid = str(o.get("id", ""))
        lab = _fold(o.get("label", ""))
        if oid and re.search(rf"\b{re.escape(_fold(oid))}\b", t):
            found.append(o["id"])
        elif lab and re.search(rf"\b{re.escape(lab)}\b", t):
            found.append(o["id"])
    return list(dict.fromkeys(found))


def _find_time_s(t: str):
    m = _TIME_RE.search(t)
    if not m:
        return None
    n = int(m.group(1))
    unidad = m.group(2)
    return n * 60 if unidad.startswith("m") and unidad != "s" else n


def _pregunta_salida(perfil: dict) -> dict:
    return {
        "slot": "output",
        "pregunta": "¿Cuál salida quieres controlar?",
        "opciones": [f"{o['id']} ({o.get('label','')})".strip()
                     for o in perfil.get("outputs", [])],
    }


def _pregunta_entrada(perfil: dict) -> dict:
    return {
        "slot": "input",
        "pregunta": "¿Con qué entrada se activa?",
        "opciones": [f"{e['id']} ({e.get('label','')})".strip()
                     for e in perfil.get("inputs", [])
                     if e.get("clase", "digital") == "digital"],
    }


def analizar_peticion(texto: str, perfil: dict, contexto=None) -> dict:
    """Analiza la peticion contra el perfil. Devuelve:
      {ambiguous, missing, questions, assumptions, slots}
    'ambiguous' True => faltan datos criticos (salida o entrada); app.py debe
    responder needs_clarification en vez de generar (para no inventar)."""
    t = _fold(texto)

    inputs  = _find_inputs(t, perfil)
    outputs = _find_outputs(t, perfil)
    n_out   = len(perfil.get("outputs", []))

    lamp_word = any(w in t for w in _LAMP_WORDS)
    btn_word  = any(w in t for w in _BTN_WORDS)
    off_all   = any(w in t for w in _OFF_ALL)
    is_seq    = any(w in t for w in _SEQ)
    counting  = any(w in t for w in _COUNT)
    latched   = any(w in t for w in _LATCH)
    direct    = any(w in t for w in _DIRECT)
    time_s    = _find_time_s(t)

    slots = {
        "inputs": inputs, "outputs": outputs, "time_s": time_s,
        "counting": counting, "sequence": is_seq,
        "latched": latched, "direct": direct, "off_all": off_all,
        "lamp_word": lamp_word, "btn_word": btn_word,
    }

    missing = []
    # SALIDA: si no se nombra una salida concreta y hay mas de una posible, es
    # ambigua. Con "apagar todo" no hace falta una salida especifica.
    if not outputs and not off_all and n_out > 1:
        missing.append("output")
    # ENTRADA: se necesita una entrada, salvo que sea un "apagar todo".
    # (Las secuencias tambien requieren su entrada de arranque.)
    if not inputs and not off_all:
        missing.append("input")

    questions = []
    if "output" in missing:
        questions.append(_pregunta_salida(perfil))
    if "input" in missing:
        questions.append(_pregunta_entrada(perfil))

    return {
        "ambiguous": bool(missing),
        "missing": missing,
        "questions": questions,
        "assumptions": [],   # Fase 1: se pregunta en vez de asumir
        "slots": slots,
    }
