"""
validador_esquema.py — validador minimo de JSON Schema (subconjunto draft-07).

Cubre lo que usan esquemas/memoria_schema.json y esquemas/proceso_schema.json:
type (incluida la lista ["string", "null"]), required, properties, enum e
items. Sin dependencias nuevas (no hace falta instalar jsonschema en Render).
"""

import json
import os

AQUI = os.path.dirname(os.path.abspath(__file__))

_TIPOS = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}


def _es_tipo(valor, tipo) -> bool:
    if tipo == "integer" and isinstance(valor, bool):
        return False                      # True no es un entero en JSON
    if tipo == "number" and isinstance(valor, bool):
        return False
    return isinstance(valor, _TIPOS[tipo])


def validar(valor, esquema: dict, ruta: str = "$") -> list:
    """Lista de errores ("$.campo: motivo"); vacia si el valor cumple."""
    errores = []
    tipo = esquema.get("type")
    if tipo is not None:
        tipos = tipo if isinstance(tipo, list) else [tipo]
        if not any(_es_tipo(valor, t) for t in tipos):
            return [f"{ruta}: se esperaba {'/'.join(tipos)}"]
    if "enum" in esquema and valor not in esquema["enum"]:
        errores.append(f"{ruta}: {valor!r} no esta en {esquema['enum']}")
    if isinstance(valor, dict):
        for campo in esquema.get("required", []):
            if campo not in valor:
                errores.append(f"{ruta}: falta '{campo}'")
        for campo, sub in (esquema.get("properties") or {}).items():
            if campo in valor:
                errores.extend(validar(valor[campo], sub, f"{ruta}.{campo}"))
    if isinstance(valor, list) and isinstance(esquema.get("items"), dict):
        for i, x in enumerate(valor):
            errores.extend(validar(x, esquema["items"], f"{ruta}[{i}]"))
    return errores


_cache = {}


def cargar(nombre: str) -> dict:
    """Esquema de esquemas/<nombre> (cacheado)."""
    if nombre not in _cache:
        with open(os.path.join(AQUI, "esquemas", nombre), encoding="utf-8") as f:
            _cache[nombre] = json.load(f)
    return _cache[nombre]
