"""
edicion_incremental.py — la siguiente instruccion EDITA la logica activa.

El front guarda una sola logica activa por sesion y, en cada instruccion de
modificacion, manda su JSON completo (programa_anterior.metadata.engine_config)
y la lista de instrucciones ya aplicadas (historial). Aqui se arma lo que
recibe el modelo y se comprueba, de forma determinista, que la version nueva
no perdio piezas que el usuario no pidio quitar.

No toca el PLC, el mapeo Modbus ni la memoria validada.
"""

import collections
import json
import re
import unicodedata

# Instrucciones previas que se le muestran al modelo (el JSON va completo).
MAX_CAMBIOS_PROMPT = 8


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


# "quita", "elimina", "ya no", "cambia S1 por S2", "en vez de"... Con estas
# palabras desaparecer una pieza puede ser justo lo pedido.
_RX_QUITAR = re.compile(
    r"\b(quita\w*|quitar|elimina\w*|borra\w*|remueve\w*|retira\w*|suprime\w*|ya no|"
    r"reemplaza\w*|sustituye\w*|en vez de|en lugar de|cambia\w* .+ por|solo deja|deja solo|"
    r"unicamente|nada mas|desde cero|de nuevo todo|nueva logica|logica nueva|otra logica)\b")


def pide_quitar(texto: str) -> bool:
    """True si la instruccion puede quitar o sustituir piezas de la logica."""
    return bool(_RX_QUITAR.search(_norm(texto)))


def _lista_cambios(historial) -> str:
    previas = [str(p).strip() for p in (historial or []) if str(p).strip()][-MAX_CAMBIOS_PROMPT:]
    return "\n".join(f"{i}. {p}" for i, p in enumerate(previas, 1))


REGLAS_EDICION = (
    "La nueva instruccion EDITA esta logica; no es una logica independiente:\n"
    "- Conserva TODO lo que la instruccion no pide cambiar: entradas, salidas, timers, contadores, "
    "condiciones, memorias, eventos, secuencias y sus pasos, con sus ids y valores.\n"
    "- Agrega solo lo nuevo, cambia solo lo pedido y quita solo lo que se pide quitar (y lo que "
    "dependa unicamente de eso).\n"
    "- \"Corrige\" o \"no funciona\" = ajusta la parte indicada de ESTA logica; no la rehagas.\n"
    "- Referencias como \"el ultimo tiempo\", \"ese timer\" o \"el segundo paso\": resuelvelas con las "
    "instrucciones anteriores (la mas reciente manda). Si hay varias piezas posibles y el contexto no "
    "decide, pregunta en \"preguntas\" en vez de adivinar.\n"
    "- Devuelve el JSON COMPLETO actualizado (lo anterior vigente + lo nuevo), nunca solo el cambio."
)


def mensajes_edicion(prev: dict, historial, titulo: str, respuesta: str) -> list:
    """Par user/assistant que antecede a la instruccion nueva cuando hay
    logica activa. `prev` es el JSON vigente en el formato que el modelo
    devuelve (para que lo edite en su propio esquema)."""
    partes = []
    cambios = _lista_cambios(historial)
    if cambios:
        partes.append("Instrucciones ya aplicadas en esta sesion (de la mas antigua a la mas reciente):\n"
                      + cambios)
    partes.append(f"{titulo}\n{REGLAS_EDICION}\nJSON vigente:\n" + json.dumps(prev, ensure_ascii=False))
    return [{"role": "user", "content": "\n\n".join(partes)},
            {"role": "assistant", "content": respuesta}]


# ---------------------------------------------------------------------------
# Piezas de una logica (para detectar perdidas no pedidas)
# ---------------------------------------------------------------------------
def _piezas_v7(resp: dict) -> collections.Counter:
    """Piezas de un JSON del motor v7 en el formato del modelo (bloques)."""
    c = collections.Counter()
    if not isinstance(resp, dict):
        return c
    for clave, nombre in (("condiciones", "condicion"), ("memorias", "memoria"),
                          ("timers", "timer"), ("contadores", "contador")):
        for b in resp.get(clave) or []:
            if isinstance(b, dict) and b.get("id"):
                c[f"{nombre} {b['id']}"] += 1
    for q, senal in (resp.get("salidas") or {}).items():
        if senal:
            c[f"salida {q}"] += 1
    for ev in resp.get("eventos") or []:
        if isinstance(ev, dict) and ev.get("cuando"):
            c[f"evento cuando {ev['cuando']}"] += 1
    for sq in resp.get("secuencias") or []:
        if not isinstance(sq, dict):
            continue
        sid = sq.get("id") or "SQ"
        c[f"secuencia {sid}"] += 1
        for i, _ in enumerate(sq.get("pasos") or [], 1):
            c[f"paso {i} de {sid}"] += 1
    for k, v in (resp.get("motor") or {}).items():
        if v not in (None, "", [], False):
            c[f"motor.{k}"] += 1
    return c


def _piezas_maletin(cfg: dict) -> collections.Counter:
    c = collections.Counter()
    if not isinstance(cfg, dict):
        return c
    for o in cfg.get("outputs") or []:
        if not isinstance(o, dict):
            continue
        q = str(o.get("output") or "").upper()
        if (o.get("logic") or {}).get("mode", "off") != "off":
            c[f"salida {q}"] += 1
        if o.get("timer"):
            c[f"timer de {q}"] += 1
        if o.get("counter"):
            c[f"contador de {q}"] += 1
    for i, _ in enumerate((cfg.get("sequence") or {}).get("steps") or [], 1):
        c[f"paso {i} de la secuencia"] += 1
    return c


def piezas_perdidas(dispositivo: str, prev: dict, nuevo: dict) -> list:
    """Piezas que estaban en la logica activa y ya no estan en la nueva."""
    f = _piezas_v7 if dispositivo == "banda" else _piezas_maletin
    falta = f(prev) - f(nuevo)
    return sorted(falta.elements())


def mensaje_restaurar(perdidas: list) -> str:
    return ("Esa version PERDIO piezas de la logica activa que el usuario no pidio quitar:\n- "
            + "\n- ".join(perdidas)
            + "\nRestauralas tal como estaban en el JSON vigente y aplica SOLO el cambio pedido. "
              "Devuelve el JSON COMPLETO, sin texto extra.")


def aviso_perdidas(perdidas: list) -> str:
    return ("Se quitaron piezas de la logica activa que la instruccion no pedia quitar: "
            + ", ".join(perdidas) + ". Revisa antes de usarla.")
