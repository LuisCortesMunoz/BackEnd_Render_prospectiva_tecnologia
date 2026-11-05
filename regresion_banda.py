"""
regresion_banda.py — una edicion o correccion solo entra si MEJORA la logica.

Con logica activa, cada version nueva se compara con la anterior corriendo la
MISMA bateria de pruebas (la especificacion viva de la sesion) en el ST v7
simulado:

  bateria(cfg, reqs, etapas, reglas) -> {id_prueba: [fallas]}   ([] = pasa)
      - un requisito de comportamiento por prueba (arranque, paro, paro solo,
        conteo, alternancia...)
      - una secuencia (historia completa etapa por etapa) por prueba
      - una regla del extractor por prueba
  comparar(antes, despues) -> {"regresiones", "arregladas", "siguen", "nuevas"}
  aceptable(cmp, antes, despues) -> la version nueva NO rompe nada que pasaba y
      no tiene mas fallas que la anterior.
  mensaje(cmp, despues) -> lo que se le devuelve al modelo para que corrija.

Si al final la version nueva no es aceptable, app.py conserva la anterior.
"""

import comportamiento
import reglas_banda
import simulacion_etapas


def _id_req(r: dict) -> str:
    return " ".join(x for x in (r["tipo"], r.get("senal") or "",
                                str(r.get("segundos") or r.get("conteo") or "")) if x)


def bateria(cfg: dict, reqs: list, etapas: dict = None, reglas: dict = None) -> dict:
    res = {}
    for r in reqs or []:
        res[f"comportamiento: {_id_req(r)}"] = comportamiento.verificar(cfg, [r])
    if etapas and etapas.get("aplica"):
        for g in etapas.get("grupos") or []:
            res[f"secuencia SQ{g['n']}"] = simulacion_etapas.verificar(cfg, {**etapas, "grupos": [g]})
    if reglas:
        for i, x in enumerate(reglas.get("reglas") or [], 1):
            res[f"regla {i}"] = reglas_banda.verificar(cfg, {**reglas, "reglas": [x]})
    return res


def comparar(antes: dict, despues: dict) -> dict:
    out = {"regresiones": [], "arregladas": [], "siguen": [], "nuevas": []}
    for k, f in despues.items():
        if k not in antes:
            if f:
                out["nuevas"].append(k)
        elif not antes[k] and f:
            out["regresiones"].append(k)
        elif antes[k] and not f:
            out["arregladas"].append(k)
        elif antes[k] and f:
            out["siguen"].append(k)
    return out


def fallas(res: dict) -> int:
    return sum(1 for f in res.values() if f)


def aceptable(cmp: dict, antes: dict, despues: dict) -> bool:
    """No rompe nada que funcionaba y no queda peor que antes."""
    return not cmp["regresiones"] and fallas(despues) <= fallas(antes)


def mensaje(cmp: dict, despues: dict) -> str:
    partes = []
    if cmp["regresiones"]:
        partes.append("Se simulo la version ANTERIOR y la NUEVA con las mismas pruebas. La nueva ROMPE lo que ya "
                      "funcionaba:\n- " + "\n- ".join(f"{k}: {despues[k][0]}" for k in cmp["regresiones"]))
    pendientes = [k for k in cmp["siguen"] + cmp["nuevas"] if despues.get(k)]
    if pendientes:
        partes.append("Se simulo el programa del PLC con ese JSON y NO hace lo pedido:\n- " + "\n- ".join(f"{k}: {despues[k][0]}" for k in pendientes))
    return ("\n".join(partes) + "\nCorrige SOLO lo necesario y conserva lo que funcionaba. Devuelve el JSON COMPLETO, "
            "sin texto extra.")


def resumen(cmp: dict) -> str:
    """Una linea para el usuario (cuando se conserva la version anterior)."""
    if cmp["regresiones"]:
        return "rompía lo que ya funcionaba (" + ", ".join(cmp["regresiones"][:3]) + ")"
    pend = cmp["siguen"] + cmp["nuevas"]
    return "no arreglaba lo pedido (" + ", ".join(pend[:3]) + ")" if pend else "no pasó las pruebas"
