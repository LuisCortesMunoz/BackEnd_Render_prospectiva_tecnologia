"""
metricas_lavo.py — mediciones de cada generacion de logica.

Por peticion: tiempo total, tiempo de busqueda en memoria, tiempo del modelo,
tokens enviados (estimados y, si Groq los reporta, reales), llamadas al
modelo, fuente (memoria o modelo), partes de contexto usadas y errores de
validacion. Se guardan en RAM (ultimas 500) para GET /memoria/metricas; al
reiniciar Render se pierden (son metricas de operacion, no memoria).
"""

import collections
import contextvars
import time

_actual = contextvars.ContextVar("lavo_metricas", default=None)
HISTORIAL = collections.deque(maxlen=500)


def iniciar(dispositivo: str = "") -> dict:
    m = {"dispositivo": dispositivo, "_t0": time.perf_counter(), "t_memoria_ms": 0.0, "t_modelo_ms": 0.0,
         "llamadas_modelo": 0, "tokens_prompt_estimados": 0, "tokens_prompt_reales": 0,
         "tokens_respuesta": 0, "errores_validacion": 0, "fuente": "modelo", "contexto": [],
         "memorias_usadas": [], "procesos_usados": []}
    _actual.set(m)
    return m


def actual():
    return _actual.get()


def sumar(campo: str, valor) -> None:
    m = _actual.get()
    if m is not None:
        m[campo] = m.get(campo, 0) + valor


def poner(campo: str, valor) -> None:
    m = _actual.get()
    if m is not None:
        m[campo] = valor


def cerrar(status: str = "ok") -> dict:
    """Resumen publico de la peticion en curso (y lo guarda en el historial)."""
    m = _actual.get()
    if m is None:
        return {}
    r = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in m.items() if not k.startswith("_")}
    r["t_total_ms"] = round((time.perf_counter() - m["_t0"]) * 1000, 1)
    r["status"] = status
    if not m.get("_cerrada"):
        HISTORIAL.append(dict(r))
        m["_cerrada"] = True
    return r


def resumen() -> dict:
    """Agregados del historial en RAM, por dispositivo y en total."""
    def agg(filas):
        n = len(filas)
        if not n:
            return {"peticiones": 0}
        prom = lambda k: round(sum(f.get(k, 0) for f in filas) / n, 1)
        reuso = sum(1 for f in filas if f.get("fuente") == "memoria")
        return {"peticiones": n, "reutilizadas_de_memoria": reuso,
                "porcentaje_reutilizacion": round(100 * reuso / n, 1),
                "t_total_ms_prom": prom("t_total_ms"), "t_memoria_ms_prom": prom("t_memoria_ms"),
                "t_modelo_ms_prom": prom("t_modelo_ms"), "llamadas_modelo_prom": prom("llamadas_modelo"),
                "tokens_prompt_estimados_prom": prom("tokens_prompt_estimados"),
                "tokens_prompt_reales_prom": prom("tokens_prompt_reales"),
                "errores_validacion_total": sum(f.get("errores_validacion", 0) for f in filas),
                "por_status": dict(collections.Counter(f.get("status") for f in filas))}
    filas = list(HISTORIAL)
    return {"total": agg(filas),
            "banda": agg([f for f in filas if f.get("dispositivo") == "banda"]),
            "maletin": agg([f for f in filas if f.get("dispositivo") == "maletin"]),
            "nota": "En RAM: se reinicia con cada despliegue o reinicio del backend."}
