"""agents/validators.py — Validacion SEMANTICA de reflexion (Fase 2).

Complementa al validador SINTACTICO de app.py (validar_logica_config). Este
detecta INCOHERENCIAS que el sintactico no ve y que producirian un programa que
"valida" pero no hace lo que se pidio, y comprueba que la config no use
CAPACIDADES que el perfil del PLC no soporta (integra la Fase 0).

Filosofia conservadora (para no romper lo que ya funciona):
- 'errors'  -> incoherencias reales que conviene que el modelo corrija. El bucle
              de app.py se las realimenta para auto-correccion. Si aun asi no se
              corrigen, app.py hace un fallback LENIENT: acepta el candidato
              sintacticamente valido y convierte estos problemas en avisos (nunca
              rechaza algo que el flujo anterior habria aceptado).
- 'warnings'-> observaciones no bloqueantes (se muestran como avisos).

Es 100% determinista y guiado por el perfil (no llama al LLM).
"""


def _tiene_entrada_base(lg: dict) -> bool:
    """True si la logica base tiene una entrada que produzca pulsos/estado
    (necesaria para que un contador tenga algo que contar)."""
    mode = str((lg or {}).get("mode", "off")).lower()
    if mode == "directo":
        return bool(lg.get("source"))
    if mode == "enclavado":
        return bool(lg.get("start"))
    if mode == "combinacional":
        return bool(lg.get("a") or lg.get("b"))
    return False


def validar_semantica(cfg: dict, perfil: dict) -> dict:
    """Devuelve {'errors': [...], 'warnings': [...]}."""
    errors, warnings = [], []
    if not isinstance(cfg, dict):
        return {"errors": ["La configuracion no es un objeto."], "warnings": []}

    caps        = perfil.get("capabilities", {}) if isinstance(perfil, dict) else {}
    modes_ok    = {str(m).lower() for m in caps.get("modes", [])}
    timers_ok   = {str(k).lower() for k in (caps.get("timers") or {}).keys()}
    counters_ok = {str(k).lower() for k in (caps.get("counters") or {}).keys()}
    seq_ok      = bool((caps.get("sequence") or {}).get("enabled"))

    outputs = cfg.get("outputs") or []
    seq     = cfg.get("sequence")

    for o in outputs:
        if not isinstance(o, dict):
            continue
        tag = f"salida {o.get('output', '?')}"
        lg   = o.get("logic") or {}
        mode = str(lg.get("mode", "off")).lower()

        # 1. Capacidad: el perfil debe soportar el modo usado.
        if mode != "off" and modes_ok and mode not in modes_ok:
            errors.append(f"{tag}: el PLC de este perfil no soporta el modo '{mode}'.")

        # 2. Contador: requiere una logica base con entrada (con 'off' no hay pulsos
        #    que contar y la salida nunca prenderia).
        ct = o.get("counter")
        if ct:
            ctype = str(ct.get("type", "")).lower()
            if counters_ok and ctype and ctype not in counters_ok:
                errors.append(f"{tag}: el perfil no soporta el contador '{ctype}'.")
            if not _tiene_entrada_base(lg):
                errors.append(f"{tag}: el contador necesita una logica base con entrada "
                              f"(source en 'directo' o start en 'enclavado'); con 'off' "
                              f"no hay pulsos que contar.")

        # 3. Timer: capacidad soportada por el perfil.
        tm = o.get("timer")
        if tm:
            ttype = str(tm.get("type", "")).lower()
            if timers_ok and ttype and ttype not in timers_ok:
                errors.append(f"{tag}: el perfil no soporta el timer '{ttype}'.")

    # 4. Secuencia: soportada por el perfil, y excluyente con timers por salida.
    if seq:
        if not seq_ok:
            errors.append("El perfil no soporta secuencias temporizadas (sequence).")
        pasos = seq.get("steps") if isinstance(seq, dict) else None
        if pasos and any(isinstance(o, dict) and o.get("timer") for o in outputs):
            warnings.append("Hay una 'sequence' y ademas timers por salida; la "
                            "secuencia gobierna el tiempo. Considera dejar las salidas "
                            "sin timer para evitar conflictos.")

    return {"errors": errors, "warnings": warnings}
