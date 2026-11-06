"""
cache_simulacion.py — no repetir una simulacion que ya se hizo.

En una correccion el mismo programa se simula varias veces con la misma
prueba: la version vigente para el diagnostico y otra vez para la linea base
de regresion; el candidato elegido en la validacion y otra vez en la
regresion final. La simulacion es determinista (el ST real en lavo_sim, sin
azar ni reloj de pared), asi que el resultado depende solo de lo que se carga
en el PLC (cfg["band"]) y de la prueba: se guarda y se reutiliza.

Solo se guardan resultados COMPLETOS: si una prueba se corto por el
presupuesto de tiempo, no entra al cache (la siguiente vez se corre entera).

  obtener(espacio, band, prueba) -> lista de fallas o None (no esta)
  guardar(espacio, band, prueba, fallas)
"""

import collections
import copy
import hashlib
import json
import threading

MAX_ENTRADAS = 256

_datos = collections.OrderedDict()
_candado = threading.Lock()
estadistica = {"aciertos": 0, "fallos": 0}


def _clave(espacio: str, band, prueba) -> str:
    txt = json.dumps([espacio, band, prueba], sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(txt.encode("utf-8")).hexdigest()


def obtener(espacio: str, band, prueba):
    k = _clave(espacio, band, prueba)
    with _candado:
        if k in _datos:
            _datos.move_to_end(k)
            estadistica["aciertos"] += 1
            return copy.deepcopy(_datos[k])
        estadistica["fallos"] += 1
    return None


def guardar(espacio: str, band, prueba, fallas) -> None:
    k = _clave(espacio, band, prueba)
    with _candado:
        _datos[k] = copy.deepcopy(fallas)
        _datos.move_to_end(k)
        while len(_datos) > MAX_ENTRADAS:
            _datos.popitem(last=False)


def limpiar() -> None:
    with _candado:
        _datos.clear()
        estadistica.update(aciertos=0, fallos=0)
