"""profile_registry.py — Registro de perfiles de dispositivo (PLC).

Fase 0 de la arquitectura del agente (ver ARQUITECTURA_AGENTE.md).

Un "perfil" describe TODO lo que el motor logico necesita saber de un PLC:
entradas, salidas, capacidades soportadas (modos, timers, counters, secuencia),
limites y datos de conexion. Es la FUENTE UNICA de la que derivan el vocabulario
y los validadores, para que agregar un PLC nuevo sea solo agregar un perfil.

Este modulo SOLO carga y valida la estructura de los perfiles. La derivacion del
vocabulario (conjuntos de entradas/salidas/modos, rangos, etc.) vive en
engine_spec.py, que consume el dict que devuelve este registro.

IMPORTANTE (Fase 0): este modulo es ADITIVO. No modifica el flujo actual de
app.py; se integra en fases posteriores. El mapeo fisico Modbus sigue en
plc_maestro.py (no se duplica aqui).
"""

import os
import json
import threading

PROFILES_DIR   = os.environ.get("PROFILES_DIR", "profiles")
DEFAULT_PROFILE = os.environ.get("DEFAULT_PROFILE", "maletin_basico")

_cache = {}
_lock  = threading.Lock()


class ProfileError(ValueError):
    """Perfil inexistente o con estructura invalida."""


def _ruta(profile_id: str) -> str:
    return os.path.join(PROFILES_DIR, f"{profile_id}.json")


def validar_perfil(p: dict) -> list:
    """Comprueba que el perfil tenga la estructura minima. Devuelve lista de
    errores (vacia = ok). No valida logica de PLC, solo forma del perfil."""
    errores = []
    if not isinstance(p, dict):
        return ["El perfil no es un objeto JSON."]
    if not p.get("id"):
        errores.append("Falta 'id'.")
    for campo in ("inputs", "outputs"):
        v = p.get(campo)
        if not isinstance(v, list) or not v:
            errores.append(f"Falta '{campo}' o esta vacio.")
        else:
            for i, item in enumerate(v):
                if not isinstance(item, dict) or not item.get("id"):
                    errores.append(f"{campo}[{i}] no tiene 'id'.")
    caps = p.get("capabilities")
    if not isinstance(caps, dict):
        errores.append("Falta 'capabilities' o no es un objeto.")
    else:
        if not isinstance(caps.get("modes"), list) or not caps["modes"]:
            errores.append("capabilities.modes debe ser una lista no vacia.")
    return errores


def cargar_perfil(profile_id: str = None, refrescar: bool = False) -> dict:
    """Carga un perfil por id (sin extension). Cachea el resultado.
    Lanza ProfileError si no existe o su estructura es invalida."""
    pid = (profile_id or DEFAULT_PROFILE).strip()
    with _lock:
        if not refrescar and pid in _cache:
            return _cache[pid]
    ruta = _ruta(pid)
    if not os.path.exists(ruta):
        raise ProfileError(f"No existe el perfil '{pid}' en '{ruta}'.")
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ProfileError(f"No se pudo leer el perfil '{pid}': {e}")
    errores = validar_perfil(datos)
    if errores:
        raise ProfileError(f"Perfil '{pid}' invalido: " + "; ".join(errores))
    with _lock:
        _cache[pid] = datos
    return datos


def listar_perfiles() -> list:
    """Ids de los perfiles disponibles en PROFILES_DIR."""
    if not os.path.isdir(PROFILES_DIR):
        return []
    return sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(PROFILES_DIR)
        if f.endswith(".json")
    )


def perfil_por_defecto() -> dict:
    """Atajo: carga el perfil por defecto (maletin_basico)."""
    return cargar_perfil(DEFAULT_PROFILE)
