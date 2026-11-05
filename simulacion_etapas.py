"""
simulacion_etapas.py — validacion INTEGRAL de una secuencia pedida en etapas.

etapas_orden.verificar mira el JSON (que pasos hay y en que orden). Aqui se
comprueba lo que HACE: se corre el texto ST real del programa maestro v7
(lavo_sim) y se le cuenta la historia que conto el usuario, etapa por etapa:

  - se pulsa la senal de inicio (I1, o la que diga la primera etapa);
  - en cada etapa que empieza "cuando S1/S2 detecta" se pasa un objeto por
    ese sensor; las que duran N s se esperan N s; las que terminan "hasta que
    S1 detecte" reciben ese objeto;
  - un CICLO con conteo ("una vez que S2 acumula 3") se recorre N veces;
  - al final se espera sin tocar nada: la banda no debe rearrancar ni la
    secuencia repetirse sola; con conteo se corre todo una SEGUNDA vez (el
    contador debe volver a cero).

En la traza (una muestra cada 0.1 s: banda en marcha y salidas Q3..Q9) cada
etapa debe aparecer en orden, dentro de su ventana de tiempo, con todo lo que
pide a la vez (luces, plumas, banda en marcha o detenida) y durar lo pedido.

  verificar(cfg, det) -> [] si cumple; si no, que fallo y que se observo.

Solo la banda v7 (el maletin se simula en el navegador).
"""

import time

import etapas_orden as eo
import lavo_motor as lm

MUESTRA_S = 0.1
PRESUPUESTO_S = 20          # tiempo REAL maximo de todas las corridas
HOLGURA_S = 1.2             # margen de las ventanas (rampa del VFD, decimas)
ESPERA_OBJETO_S = 2.5       # lo que corre una etapa que espera el siguiente objeto
FINAL_S = 6.0               # quietud que se exige al terminar
_SENALES = ("I1", "I2", "S1", "S2")


class _Traza:
    def __init__(self, sim):
        self.sim = sim
        self.t = 0.0
        self.muestras = []      # (t, marcha, salidas)

    def correr(self, segundos):
        for _ in range(max(1, int(round(segundos / MUESTRA_S)))):
            self.sim.correr(MUESTRA_S)
            self.t += MUESTRA_S
            self.muestras.append((self.t, self.sim.motor() in ("dir1", "dir2"), frozenset(self.sim.salidas())))

    def pulsar(self, senal):
        self.sim.poner(**{senal: True})
        self.correr(0.3)
        self.sim.poner(**{senal: False})


def _cumple(muestra, reqs) -> bool:
    _, marcha, sal = muestra
    for r in reqs:
        if r["tipo"] == "salida":
            if not (sal & r["q"]):
                return False
        elif r["accion"] in ("marcha", "invertir"):
            if not marcha:
                return False
        elif r["accion"] == "paro" and marcha:
            return False
    return True


def _describir(muestras) -> str:
    if not muestras:
        return "nada"
    marcha = sum(1 for m in muestras if m[1]) / len(muestras)
    vistas = sorted({q for m in muestras for q in m[2]}, key=lambda q: int(q[1:]))
    banda = ("la banda en marcha" if marcha > 0.9 else "la banda detenida" if marcha < 0.1
             else "la banda a ratos en marcha")
    return banda + (", salidas " + ", ".join(eo._DESC_Q.get(q, q) for q in vistas) if vistas else ", sin salidas")


def _plan(det: dict, g: dict) -> list:
    """Etapas en el orden en que deben ocurrir (el ciclo con conteo, N veces)."""
    de_g = [e for e in det["etapas"] if e["n"] in g["etapas"]]
    c, ciclo = g.get("conteo"), g.get("ciclo") or []
    if not (c and ciclo and c.get("n")):
        return [{"e": e, "vuelta": 0, "opcional": False} for e in de_g]
    pre = [e for e in de_g if e["n"] < ciclo[0]]
    cic = [e for e in de_g if e["n"] in ciclo]
    post = [e for e in de_g if e["n"] >= c["etapa"]]
    plan = [{"e": e, "vuelta": 0, "opcional": False} for e in pre]
    n = max(1, min(int(c["n"]), 6))
    ultimo = g.get("ultimo")                  # respuesta del usuario: "tambien" | "directo" | None
    for k in range(1, n + 1):
        contada = False
        for e in cic:
            es_contada = k == n and e["entra_con"] == c.get("senal")
            if ultimo == "directo" and (contada or es_contada):
                if es_contada:
                    # el objeto N solo dispara la senal; su etapa NO debe ocurrir
                    plan.append({"e": e, "vuelta": k, "opcional": True, "solo_disparo": True})
                contada = True
                continue
            contada = contada or es_contada
            # Sin respuesta del usuario, lo que dispara la senal contada en la ultima
            # vuelta puede saltarse o hacerse: las dos valen. Con respuesta, se exige.
            plan.append({"e": e, "vuelta": k, "opcional": contada and ultimo is None})
    plan += [{"e": e, "vuelta": 0, "opcional": False} for e in post]
    return plan


def _duracion(paso, siguiente) -> float:
    e = paso["e"]
    if e["tiempos"]:
        return max(e["tiempos"]) + 0.6
    if e["hasta"]:
        return 1.5
    if eo._solo_paro(e):
        return 0.4
    if siguiente is not None and (siguiente["e"]["entra_con"] or siguiente["e"].get("conteo")):
        return ESPERA_OBJETO_S
    if eo._solo_plumas(e):
        return eo.PLUMA_S + 0.6
    return 2.0


def _correr_plan(tr: _Traza, plan: list, inicio: str) -> list:
    """Ejecuta la historia y devuelve las ventanas [(paso, t0, t1)]."""
    ventanas = []
    for i, paso in enumerate(plan):
        e = paso["e"]
        sig = plan[i + 1] if i + 1 < len(plan) else None
        t0 = tr.t
        senal = e["entra_con"] if i else (e["entra_con"] or inicio)
        if paso.get("solo_disparo"):
            tr.pulsar(senal)
            tr.correr(0.3)
            ventanas.append((paso, t0, tr.t))
            continue
        if senal in _SENALES and not (i == 0 and not e["entra_con"] and senal != inicio):
            tr.pulsar(senal)
        tr.correr(_duracion(paso, sig))
        if e["hasta"] in _SENALES:
            tr.pulsar(e["hasta"])
            tr.correr(0.5)
        if e.get("reanuda") and sig is not None and not sig["e"].get("conteo"):
            tr.correr(1.5)                 # despues de la pausa, la banda debe volver a avanzar
        ventanas.append((paso, t0, tr.t))
    return ventanas


def _revisar(tr: _Traza, ventanas: list, prefijo: str) -> list:
    # Cada etapa debe aparecer DESPUES de la anterior (orden) y antes del final
    # de su ventana (si tarda mas de lo pedido, falla). No se exige que empiece
    # justo cuando el guion lo esperaba: el PLC puede ir algo adelantado.
    problemas = []
    cursor = 0
    m = tr.muestras
    t_desde = m[0][0] if m else 0.0
    for idx, (paso, t0, t1) in enumerate(ventanas):
        e = paso["e"]
        sig = ventanas[idx + 1][0] if idx + 1 < len(ventanas) else None
        if not e["requisitos"]:
            continue
        if paso.get("solo_disparo"):
            # "pasa directo": al llegar a N, la etapa que dispara esa senal NO debe ocurrir
            tramo = [x for x in m if t0 <= x[0] <= t0 + 1.5]
            if tramo and any(_cumple(x, e["requisitos"]) for x in tramo):
                problemas.append(f"{prefijo}Etapa {e['n']} (objeto {paso['vuelta']}) «{e['texto']}»: al llegar al "
                                 f"conteo debia pasar directo y la etapa se repitio.")
                break
            continue
        limite = t1 + HOLGURA_S
        j = next((k for k in range(cursor, len(m)) if m[k][0] <= limite
                  and _cumple(m[k], e["requisitos"])), None)
        if j is None:
            if paso["opcional"]:
                continue
            tramo = [x for x in m if min(t0, t_desde) <= x[0] <= limite]
            vuelta = f" (objeto {paso['vuelta']})" if paso["vuelta"] else ""
            problemas.append(f"{prefijo}Etapa {e['n']}{vuelta} «{e['texto']}»: se esperaba "
                             + "; ".join(r["desc"] for r in e["requisitos"])
                             + f", pero se observo {_describir(tramo)}.")
            break                           # lo demas falla en cascada
        # Duracion: el estado de la banda (o las salidas) se sostiene lo pedido.
        if e["tiempos"]:
            # tramo continuo REAL en que se sostiene (hacia atras hasta la etapa anterior)
            clave = [r for r in e["requisitos"] if r["tipo"] == "motor"] or e["requisitos"]
            a, k = j, j
            while a - 1 >= cursor and _cumple(m[a - 1], clave):
                a -= 1
            while k < len(m) and _cumple(m[k], clave):
                k += 1
            dura = (k - a) * MUESTRA_S
            pedido = max(e["tiempos"])
            if dura + HOLGURA_S < pedido and not paso["opcional"]:
                problemas.append(f"{prefijo}Etapa {e['n']} «{e['texto']}»: dura {dura:.1f} s y se pidieron "
                                 f"{eo._fmt(pedido)} s.")
                break
        # Tras la pausa la banda vuelve a avanzar... salvo que lo siguiente sea la etapa del
        # conteo (el objeto N), que manda sobre el estado de la banda.
        if e.get("reanuda") and not (sig is not None and sig["e"].get("conteo")):
            despues = [x for x in m if t1 - 1.5 <= x[0] <= t1 + 0.2]
            if despues and not any(x[1] for x in despues) and not paso["opcional"]:
                problemas.append(f"{prefijo}Etapa {e['n']} «{e['texto']}»: despues de la pausa la banda debia "
                                 f"volver a avanzar y se quedo detenida.")
                break
        cursor = j + 1
        t_desde = m[j][0]
    return problemas


def _quieta_al_final(tr: _Traza, ultima: dict, prefijo: str) -> list:
    t0 = tr.t
    tr.correr(FINAL_S)
    tramo = [x for x in tr.muestras if x[0] > t0 + 1.0]
    para = any(r["tipo"] == "motor" and r["accion"] == "paro" for r in ultima["requisitos"]) or not any(
        r["tipo"] == "motor" and r["accion"] in ("marcha", "invertir") for r in ultima["requisitos"])
    out = []
    if para and any(x[1] for x in tramo):
        out.append(f"{prefijo}Al terminar la ultima etapa la banda vuelve a arrancar sola (debia quedarse detenida).")
    pedidas = set().union(*[r["q"] for r in ultima["requisitos"] if r["tipo"] == "salida"]) if any(
        r["tipo"] == "salida" for r in ultima["requisitos"]) else set()
    extra = {q for x in tramo[len(tramo) // 2:] for q in x[2]} - pedidas
    if extra:
        out.append(f"{prefijo}Al terminar, sin tocar nada, se activan solas "
                   + ", ".join(eo._DESC_Q.get(q, q) for q in sorted(extra, key=lambda q: int(q[1:])))
                   + " (la secuencia se repite o quedo algo encendido).")
    return out


def verificar(cfg: dict, det: dict, presupuesto_s: float = PRESUPUESTO_S) -> list:
    """Problemas del comportamiento simulado frente a las etapas pedidas."""
    if not (det or {}).get("aplica"):
        return []
    band = (cfg or {}).get("band") or {}
    if (band.get("engine") or {}).get("version") != 7:
        return []
    import lavo_sim
    limite = time.monotonic() + presupuesto_s
    problemas = []
    for g in det.get("grupos") or []:
        if time.monotonic() > limite:
            break
        plan = _plan(det, g)
        if len(plan) < 2:
            continue
        sim = lavo_sim.SimV7()
        err, campo = sim.cargar({"band": band})
        if err:
            return [f"El PLC simulado rechazo la configuracion (error {err}, campo {campo})."]
        tr = _Traza(sim)
        tr.correr(0.5)
        inicio = plan[0]["e"]["entra_con"] or "I1"
        prefijo = f"[SQ{g['n']}] " if len(det["grupos"]) > 1 else ""
        corridas = 2 if (g.get("conteo") or any(e.get("reinicia_contador") for e in (p["e"] for p in plan))) else 1
        for k in range(corridas):
            if time.monotonic() > limite:
                break
            pre = prefijo + ("En la segunda corrida: " if k else "")
            n0 = len(tr.muestras)
            ventanas = _correr_plan(tr, plan, inicio)
            tr_k = _Traza(sim)
            tr_k.muestras, tr_k.t = tr.muestras[n0:], tr.t
            fallas = _revisar(tr_k, ventanas, pre)
            sigue = g.get("repite") or any(p["e"].get("hasta_paro") for p in plan)
            if not fallas and not sigue:           # "hasta que yo la detenga": seguir es lo pedido
                fallas = _quieta_al_final(tr, plan[-1]["e"], pre)
            if fallas and k and g.get("conteo"):
                fallas = [fallas[0] + " La primera corrida funciono: lo mas probable es que el contador no "
                          "vuelva a cero al terminar (reset = SQ1_FIN)."] + fallas[1:]
            problemas += fallas
            if fallas:
                break
    return problemas
