"""
aclaraciones.py — respuestas a las preguntas de aclaracion, separadas del texto.

Cuando una instruccion es incompleta, el backend pregunta (needs_clarification)
y el usuario elige una opcion. El front ya NO pega esa opcion al texto: manda
la instruccion original intacta y, aparte, las respuestas en
LogicaRequest.aclaraciones:

    {"freq_hz": {"pregunta": "¿A qué frecuencia...?", "respuesta": "20 Hz"},
     "equipo": "Banda transportadora"}

(cada valor puede ser la respuesta sola o {pregunta, respuesta}). Aqui se
normalizan, se le dan al modelo como datos ya decididos y se usan para no
volver a preguntar lo que ya se respondio.
"""

import re
import unicodedata

# Distintos nombres del mismo parametro (preguntas deterministas y del modelo).
ALIAS = {
    "freq_hz": "freq_hz", "frecuencia": "freq_hz", "frecuencia_hz": "freq_hz", "velocidad": "freq_hz",
    "hz": "freq_hz",
    "direccion": "direccion", "sentido": "direccion", "direction": "direccion",
    "equipo": "equipo", "device": "equipo", "dispositivo": "equipo",
    "output": "output", "salida": "output",
    "input": "input", "entrada": "input",
    "proceso": "proceso",
}


def _fold(s) -> str:
    t = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").strip()


def clave(nombre) -> str:
    """Nombre canonico del parametro ('frecuencia_hz' -> 'freq_hz')."""
    k = re.sub(r"[^a-z0-9_]+", "_", _fold(nombre)).strip("_")
    return ALIAS.get(k, k)


def clave_pregunta(q: dict) -> str:
    """Parametro al que responde una pregunta (campo, slot o id)."""
    if not isinstance(q, dict):
        return ""
    return clave(q.get("campo") or q.get("slot") or q.get("id") or "")


def normalizar(raw) -> dict:
    """{clave: {"pregunta", "respuesta"}} a partir de lo que mande el front."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if isinstance(v, dict):
            resp, preg = v.get("respuesta", v.get("valor")), v.get("pregunta") or ""
        else:
            resp, preg = v, ""
        if resp in (None, "") or not clave(k):
            continue
        out[clave(k)] = {"pregunta": str(preg).strip(), "respuesta": str(resp).strip()}
    return out


def bloque_modelo(acl: dict) -> str:
    """Texto que acompaña a la instruccion cuando hay respuestas."""
    if not acl:
        return ""
    filas = []
    for k, v in acl.items():
        filas.append(f"- {k}: {v['respuesta']}" + (f"  (pregunta: {v['pregunta']})" if v["pregunta"] else ""))
    return ("DATOS QUE EL USUARIO YA ACLARO (forman parte de la instruccion; usalos tal cual en el JSON "
            "y no vuelvas a preguntar por ellos):\n" + "\n".join(filas))


def mensaje_usuario(texto: str, acl: dict) -> str:
    """Instruccion para el modelo: el texto original + los datos aclarados."""
    partes = [texto]
    b = bloque_modelo(acl)
    if b:
        partes.append(b)
    partes.append("Responde SOLO con el JSON del esquema indicado.")
    return "\n\n".join(partes)


def sin_respondidas(preguntas: list, acl: dict) -> list:
    """Quita las preguntas cuyo parametro ya se respondio."""
    return [q for q in (preguntas or []) if clave_pregunta(q) not in acl]


def frecuencia(acl: dict):
    """Hz elegidos (int) o None."""
    v = (acl.get("freq_hz") or {}).get("respuesta")
    m = re.search(r"\d+(?:[.,]\d+)?", str(v or ""))
    return int(round(float(m.group(0).replace(",", ".")))) if m else None


def direccion(acl: dict):
    v = _fold((acl.get("direccion") or {}).get("respuesta"))
    if "izq" in v:
        return "izquierda"
    if "der" in v:
        return "derecha"
    return None


def equipo(acl: dict):
    v = _fold((acl.get("equipo") or {}).get("respuesta"))
    if v.startswith("banda") or "transportador" in v:
        return "banda"
    if v.startswith("maletin"):
        return "maletin"
    return None


def id_de_opcion(acl: dict, k: str):
    """'Q10 (verde)' -> 'Q10' para entradas y salidas del maletin."""
    v = (acl.get(k) or {}).get("respuesta")
    m = re.match(r"\s*([A-Za-z]+\d+)", str(v or ""))
    return m.group(1).upper() if m else None


def aplicar_banda_v7(candidato: dict, acl: dict) -> dict:
    """Frecuencia y direccion elegidas mandan sobre lo que haya puesto el modelo
    (solo si la banda se mueve). Lo demas lo integra el modelo."""
    if not acl or not isinstance(candidato, dict):
        return candidato
    b = candidato.get("banda")
    if not isinstance(b, dict) or not b.get("mover"):
        return candidato
    f, d = frecuencia(acl), direccion(acl)
    if f is not None:
        b["frecuencia_hz"] = f
    if d is not None:
        b["direccion"] = d
    return candidato


def autoriza_catalogo(acl: dict) -> bool:
    """El usuario eligio "Autorizar ampliar el catalogo" en la pregunta del proceso."""
    return "autorizar ampliar el catalogo" in _fold((acl.get("proceso") or {}).get("respuesta"))
