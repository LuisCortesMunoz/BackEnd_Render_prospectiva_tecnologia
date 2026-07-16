"""agents/experts.py — Expertos dinamicos (Fase 3).

Los "expertos" NO son modelos separados: son MODULOS DE PROMPT (fragmentos de
guia) por tipo de logica. Segun la intencion detectada en la peticion (reusa el
analisis de slots de clarify) y las capacidades del perfil, se componen los
fragmentos relevantes y se inyectan como refuerzo del system prompt.

Los fragmentos REFUERZAN reglas que ya estan en SYSTEM_PROMPT_LOGICA (no las
contradicen); solo las hacen prominentes para la peticion concreta. Los
validadores (Fases 1/2) siguen custodiando la salida. Es determinista.
"""

from agents import clarify

# Fragmentos de guia por experto. Consistentes con SYSTEM_PROMPT_LOGICA.
FRAGMENTOS = {
    "temporizador":
        "TEMPORIZADOR: usa timer {type: on_delay|pulse, preset_s en SEGUNDOS}. "
        "on_delay retrasa el encendido; pulse enciende y se apaga solo al vencer el "
        "tiempo. Convierte minutos a segundos.",
    "contador":
        "CONTADOR: usa counter {type: up|up_held, preset, reset_input}. La logica "
        "base DEBE tener entrada (source en 'directo' o start en 'enclavado'): esos "
        "son los pulsos que se cuentan. 'se enclava/queda encendida al llegar al "
        "conteo' -> up_held (NO uses mode 'enclavado').",
    "enclavamiento":
        "ENCLAVAMIENTO: usa logic.mode 'enclavado' con start (arranque) y stop "
        "(paro). La salida se queda encendida al soltar el arranque y se apaga con "
        "el paro. Si el enclavamiento lo produce un conteo, hazlo con el contador.",
    "combinacional":
        "COMBINACIONAL: si DOS entradas gobiernan una salida, usa logic.mode "
        "'combinacional' con a, b y op (OR|AND). En AND no uses enable. Usa "
        "latched:true si la combinacion debe quedar retenida.",
    "secuencia":
        "SECUENCIA/SEMAFORO: si las salidas se encienden EN ORDEN por tiempo, usa el "
        "bloque 'sequence' {start, mode, steps:[{outputs, duration_s}]} y deja "
        "outputs:[]. No uses timers por salida para esto.",
}


def seleccionar_expertos(slots: dict, perfil: dict) -> list:
    """Decide que expertos aplican segun los slots detectados y las capacidades
    del perfil. Devuelve ids en orden estable."""
    caps = perfil.get("capabilities", {}) if isinstance(perfil, dict) else {}
    tiene_timers   = bool(caps.get("timers"))
    tiene_counters = bool(caps.get("counters"))
    seq_ok         = bool((caps.get("sequence") or {}).get("enabled"))

    ids = []
    if slots.get("sequence") and seq_ok:
        ids.append("secuencia")
    if slots.get("counting") and tiene_counters:
        ids.append("contador")
    if slots.get("time_s") is not None and tiene_timers:
        ids.append("temporizador")
    if slots.get("latched"):
        ids.append("enclavamiento")
    # Combinacional: dos entradas y NO es enclavamiento/conteo/secuencia.
    if (len(slots.get("inputs", [])) >= 2 and not slots.get("latched")
            and not slots.get("counting") and not slots.get("sequence")):
        ids.append("combinacional")
    return ids


def componer_guia(texto: str, perfil: dict) -> str:
    """Analiza la peticion y devuelve el bloque de guia de expertos (o "")."""
    slots = clarify.analizar_peticion(texto, perfil).get("slots", {})
    ids = seleccionar_expertos(slots, perfil)
    fr = [FRAGMENTOS[i] for i in ids if i in FRAGMENTOS]
    if not fr:
        return ""
    return "GUIA DE EXPERTOS (recordatorios para ESTA peticion):\n- " + "\n- ".join(fr)
