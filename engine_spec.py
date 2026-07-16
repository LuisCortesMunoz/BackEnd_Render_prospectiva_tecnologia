"""engine_spec.py — Vocabulario del motor logico DERIVADO de un perfil.

Fase 0 de la arquitectura del agente (ver ARQUITECTURA_AGENTE.md).

En vez de tener los conjuntos ENGINE_INPUTS / ENGINE_OUTPUTS / ENGINE_MODES /
TIMER_TYPES / COUNTER_TYPES / SEQ_MODES / SEQ_MAX_STEPS hardcodeados y repetidos
(hoy estan en app.py, validate.js y plc_maestro.py), este modulo los DERIVA de un
perfil de dispositivo (profile_registry). Asi, agregar un PLC con mas I/O o mas
capacidades es solo agregar/editar su perfil.

Cada funcion recibe el dict del perfil (el que devuelve profile_registry) y
devuelve el mismo tipo de dato que hoy usa app.py, para poder integrarse sin
cambiar el comportamiento. La equivalencia exacta con las constantes actuales
esta cubierta por test_engine_spec.py.
"""

from typing import Optional


# ─── Conjuntos de vocabulario ─────────────────────────────────────

def entradas_validas(perfil: dict) -> set:
    """Conjunto de entradas aceptadas (ids + el centinela 'NINGUNA')."""
    ids = {str(e["id"]).upper() for e in perfil.get("inputs", []) if e.get("id")}
    none = perfil.get("input_none")
    if none:
        ids.add(str(none).upper())
    return ids


def salidas_validas(perfil: dict) -> set:
    """Conjunto de salidas aceptadas: los ids MAS sus alias en mayuscula
    (el 'label', p. ej. Q10 acepta tambien 'VERDE')."""
    out = set()
    for o in perfil.get("outputs", []):
        if o.get("id"):
            out.add(str(o["id"]).upper())
        if o.get("label"):
            out.add(str(o["label"]).upper())
    return out


def modos(perfil: dict) -> set:
    return {str(m).lower() for m in perfil.get("capabilities", {}).get("modes", [])}


def tipos_timer(perfil: dict) -> set:
    return set((perfil.get("capabilities", {}).get("timers") or {}).keys())


def tipos_counter(perfil: dict) -> set:
    return set((perfil.get("capabilities", {}).get("counters") or {}).keys())


def modos_secuencia(perfil: dict) -> set:
    seq = perfil.get("capabilities", {}).get("sequence") or {}
    return {str(m).lower() for m in seq.get("modes", [])}


def secuencia_habilitada(perfil: dict) -> bool:
    return bool((perfil.get("capabilities", {}).get("sequence") or {}).get("enabled"))


def max_pasos_secuencia(perfil: dict) -> int:
    return int((perfil.get("capabilities", {}).get("sequence") or {}).get("max_steps", 0))


def max_entradas_por_salida(perfil: dict) -> int:
    return int((perfil.get("limits") or {}).get("max_inputs_per_output", 2))


# ─── Canonicalizacion y rangos ────────────────────────────────────

def canon_salida(perfil: dict, nombre) -> Optional[str]:
    """Devuelve el id canonico de una salida a partir de su id o su alias
    (label). Ej.: 'VERDE' -> 'Q10'. None si no pertenece al perfil."""
    u = str(nombre).upper()
    for o in perfil.get("outputs", []):
        oid = str(o.get("id", "")).upper()
        lab = str(o.get("label", "")).upper()
        if u == oid or (lab and u == lab):
            return o["id"]
    return None


def rango_timer(perfil: dict, tipo: str):
    """[low, high] permitido para timer.preset_s de un tipo dado, o None."""
    t = (perfil.get("capabilities", {}).get("timers") or {}).get(str(tipo).lower())
    return list(t.get("preset_s")) if t and "preset_s" in t else None


def rango_counter(perfil: dict, tipo: str):
    """[low, high] permitido para counter.preset de un tipo dado, o None."""
    c = (perfil.get("capabilities", {}).get("counters") or {}).get(str(tipo).lower())
    return list(c.get("preset")) if c and "preset" in c else None


def rango_duracion_secuencia(perfil: dict):
    """[low, high] permitido para sequence.steps[].duration_s, o None."""
    seq = perfil.get("capabilities", {}).get("sequence") or {}
    return list(seq.get("duration_s")) if "duration_s" in seq else None
