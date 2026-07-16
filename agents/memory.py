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
    """True si el ejemplo esta en el esquema engine-config (nuevo)."""
    return (isinstance(e, dict)
            and isinstance(e.get("datos"), dict)
            and isinstance(e["datos"].get("engine_config"), dict))


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
