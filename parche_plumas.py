"""
parche_plumas.py — agrega a la logica VIGENTE lo que se pide de una pluma en la
pausa por deteccion, sin el modelo (banco C9 t3).

  "...se detiene 4 segundos, la pluma 1 sube y la banda vuelve a avanzar" (vigente)
  "La pluma 1 debe bajar cuando la banda vuelve a avanzar."             (nuevo)

El modelo tendia a REEMPLAZAR la subida por la bajada o a rehacer todo y romper
la repeticion. Aqui solo se agrega, por cada accion que falte, un timer y su
salida (no un evento: el reset cruzado de los eventos apagaria la accion que la
pluma ya hacia):

  al reanudar   Ta RETARDO(S, N s) -> Tb PULSO(Ta, 3 s) -> Q de la accion
  en la pausa   Tc PULSO(S, min(3, N) s)                 -> Q de la accion

(S = sensor de la pausa, N = segundos de la pausa). Si la Q ya tenia una regla en
'salidas', se juntan con OR. Lo demas queda igual; quien lo llama valida, simula
TODOS los requisitos y solo lo entrega si cumple.

parchear(resp_vigente, reqs, cfg_vigente) -> (resp_nueva, avisos) | None
"""

import copy

import comportamiento
import lavo_motor as lm

DURACION_S = 3.0


def _faltan(cfg_vigente, reqs) -> list:
    """Requisitos de pluma que la logica vigente no cumple (simulando)."""
    return [r for r in reqs if r.get("tipo") == "pluma_pausa" and comportamiento.verificar(cfg_vigente, [r])]


def parchear(resp_vigente, reqs, cfg_vigente):
    if not isinstance(resp_vigente, dict) or cfg_vigente is None:
        return None
    try:
        faltan = _faltan(cfg_vigente, reqs)
    except ValueError:
        return None
    if not faltan:
        return None
    resp = copy.deepcopy(resp_vigente)
    avisos = []
    try:
        for r in faltan:
            seg = float(r["segundos"])
            if r["momento"] == "reanuda":
                ta = lm._siguiente_id(resp, "timers", "T", lm.N_TIMER)
                ta.update(tipo="RETARDO", entrada=r["senal"], reset=None, segundos=seg)
                tb = lm._siguiente_id(resp, "timers", "T", lm.N_TIMER)
                tb.update(tipo="PULSO", entrada=ta["id"], reset=None, segundos=DURACION_S)
                senal = tb["id"]
                avisos.append(f"la pluma {r['pluma']} {r['accion']} {DURACION_S:g} s cuando la banda vuelve a "
                              f"avanzar tras la pausa por {r['senal']}")
            else:
                tc = lm._siguiente_id(resp, "timers", "T", lm.N_TIMER)
                tc.update(tipo="PULSO", entrada=r["senal"], reset=None, segundos=min(DURACION_S, seg))
                senal = tc["id"]
                avisos.append(f"la pluma {r['pluma']} {r['accion']} durante la pausa por {r['senal']}")
            sal = dict(resp.get("salidas") or {})
            q = r["q"]
            previa = next((v for k, v in sal.items() if lm.salida_canonica(k) == q and v not in (None, "")), None)
            sal = {k: v for k, v in sal.items() if lm.salida_canonica(k) != q}
            sal[q] = lm._o_logico(resp, ([previa] if previa else []) + [senal])
            resp["salidas"] = sal
    except (ValueError, TypeError, KeyError):
        return None
    return resp, avisos
