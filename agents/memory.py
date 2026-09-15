"""agents/memory.py — Render de ejemplos de feedback para el flujo engine-config (Fase 3).

La memoria de feedback (memoria/ejemplos.json) mezcla ejemplos del esquema VIEJO
(id 'ej_', campo generated_ladder_json) y del esquema NUEVO engine-config
(id 'lej_', campo datos.engine_config). El flujo /generar-logica solo debe
inyectar los del esquema NUEVO para no contaminar el contrato (por eso el autor
lo tenia desactivado).

La SELECCION la hace app.ejemplos_relevantes (ya filtra a accepted/corrected y
puntua por tags/palabras). Aqui solo se FILTRAN los del esquema nuevo y se
RENDERIZAN. Determinista.
"""

import json


def es_ejemplo_logica(e: dict) -> bool:
    """True si el ejemplo esta en el esquema engine-config (nuevo) del maletin.
    Los ejemplos de la banda (device='banda') tienen otro PLC y otro esquema:
    nunca se inyectan en el prompt del maletin."""
    return (isinstance(e, dict)
            and e.get("device") != "banda"
            and isinstance(e.get("datos"), dict)
            and isinstance(e["datos"].get("engine_config"), dict))


def es_ejemplo_banda(e: dict) -> bool:
    """True si el ejemplo es de la banda (intencion + configuracion canonica)."""
    return (isinstance(e, dict)
            and e.get("device") == "banda"
            and isinstance(e.get("datos"), dict)
            and isinstance(e["datos"].get("engine_config"), dict))


def bloque_banda_prompt(ejemplos: list) -> str:
    """Bloque de ejemplos de la BANDA validados por el usuario (👍 o corregidos).

    Se muestra la INTENCION que se acepto, porque es lo que el LLM de la banda
    devuelve; la traduccion a registros la sigue haciendo el normalizador."""
    ejemplos = [e for e in (ejemplos or []) if es_ejemplo_banda(e)]
    if not ejemplos:
        return ""
    partes = [
        "EJEMPLOS DE LA BANDA VALIDADOS POR EL USUARIO (interacciones previas):",
        "Si la peticion es parecida, entiendela igual que en estos ejemplos; si hay una "
        "correccion del usuario, respetala. Responde con el mismo esquema de intencion.",
    ]
    for i, e in enumerate(ejemplos, 1):
        cfg = e["datos"]["engine_config"]
        partes.append(f"\n--- Ejemplo {i} [{e.get('status')}] ---")
        partes.append(f"Peticion: {e.get('user_prompt', '')}")
        if e.get("user_correction"):
            partes.append(f"Correccion del usuario: {e['user_correction']}")
        if e.get("error_explanation"):
            partes.append(f"Error a evitar: {e['error_explanation']}")
        intencion = {"name": cfg.get("name"), "intencion": cfg.get("intent") or {}}
        partes.append("JSON: " + json.dumps(intencion, ensure_ascii=False, separators=(",", ":"))[:1500])
    return "\n".join(partes)


def filtrar_logica(ejemplos: list) -> list:
    """Conserva solo los ejemplos del esquema engine-config."""
    return [e for e in (ejemplos or []) if es_ejemplo_logica(e)]


def bloque_logica_prompt(ejemplos: list) -> str:
    """Bloque de ejemplos validados (engine-config) para reforzar el prompt."""
    ejemplos = filtrar_logica(ejemplos)
    if not ejemplos:
        return ""
    partes = [
        "EJEMPLOS VALIDADOS POR EL USUARIO (misma arquitectura engine-config):",
        "Imita estas soluciones cuando la peticion sea similar; respeta el mismo esquema.",
    ]
    for i, e in enumerate(ejemplos, 1):
        cfg = e["datos"]["engine_config"]
        partes.append(f"\n--- Ejemplo {i} [{e.get('status')}] ---")
        partes.append(f"Peticion: {e.get('user_prompt', '')}")
        if e.get("user_correction"):
            partes.append(f"Correccion del usuario: {e['user_correction']}")
        partes.append("engine_config: "
                      + json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))[:1200])
    return "\n".join(partes)
