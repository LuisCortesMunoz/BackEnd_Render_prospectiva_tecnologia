"""
huella_banda.py — que HACE un programa de la banda, para comparar dos versiones.

La bateria de regresion (regresion_banda) solo protege lo que se entendio del
texto como requisito. Lo demas (una luz por condicion, una pluma por evento, la
velocidad, un tiempo de una secuencia) podia romperse en una correccion sin que
nada lo notara. Aqui se simula el ST REAL (lavo_sim) con una bateria FIJA de
escenarios, la misma para la version vigente y la nueva, y se compara lo que
pasa con el motor, las luces (Q3..Q5) y las plumas (Q6..Q9):

  huella(cfg)                    -> {escenario: {canal: [(t, valor), ...]}}
  comparar(h_antes, h_despues)   -> [{"modulo", "escenario", "canal", "antes", "despues"}]
  fuera_de_zona(difs, zona)      -> las diferencias en modulos que el usuario NO pidio tocar
  mensaje(difs) / aviso(difs)    -> texto para el modelo / para el usuario

Determinista: el mismo programa da la misma huella (se guarda en cache_simulacion).
"""

import cache_simulacion
import lavo_motor as lm

MUESTRA_S = 0.25
REDONDEO_S = 0.5            # los cambios se comparan a medio segundo (timers por pulso de 1 s)
TOLERANCIA_S = 1.0          # un cambio corrido hasta 1 s no cuenta como distinto

# Canal -> modulo de edicion_incremental.localizar
CANALES = {"banda": "motor", "Q3": "torreta", "Q4": "torreta", "Q5": "torreta",
           "Q6": "plumas", "Q7": "plumas", "Q8": "plumas", "Q9": "plumas"}
NOMBRE = {"banda": "la banda", "Q3": "la luz verde", "Q4": "la luz amarilla", "Q5": "la luz roja",
          "Q6": "la pluma 2 (sube)", "Q7": "la pluma 2 (baja)", "Q8": "la pluma 1 (sube)", "Q9": "la pluma 1 (baja)"}

# (nombre, guion): ("I1", s) pulso de 0.3 s y espera s; ("t", s) espera.
ESCENARIOS = [
    ("sin tocar nada", [("t", 6)]),
    ("I1", [("I1", 4), ("t", 4)]),
    ("I1 y luego I2", [("I1", 3), ("I2", 4)]),
    ("I1 dos veces", [("I1", 3), ("I1", 4)]),
    ("I2 solo", [("I2", 5)]),
    ("I1 y una caja en S1", [("I1", 2), ("S1", 9)]),
    ("I1 y una caja en S2", [("I1", 2), ("S2", 9)]),
    ("I1 y 5 cajas en S2", [("I1", 2), ("S2", 1.2), ("S2", 1.2), ("S2", 1.2), ("S2", 1.2), ("S2", 11)]),
    ("I1 y 5 cajas en S1", [("I1", 2), ("S1", 1.2), ("S1", 1.2), ("S1", 1.2), ("S1", 1.2), ("S1", 8)]),
    ("I1 y paro de emergencia I3", [("I1", 2), ("I3+", 3), ("I3-", 3)]),
]


def _valor_banda(sim) -> str:
    m = sim.motor()
    if m not in ("dir1", "dir2"):
        return "paro"
    return f"{m}@{int(round(sim.var('VFD_FREQCALC') / 100))}"


def _correr(cfg: dict, guion: list) -> dict:
    import lavo_sim
    sim = lavo_sim.SimV7()
    err, _ = sim.cargar({"band": cfg.get("band") or {}})
    if err:
        return {"error": [(0.0, str(err))]}
    canales = {c: [] for c in CANALES}
    t = 0.0

    def muestra():
        sal = sim.salidas()
        for c in CANALES:
            v = _valor_banda(sim) if c == "banda" else (c in sal)
            serie = canales[c]
            if not serie or serie[-1][1] != v:
                serie.append((round(round(t / REDONDEO_S) * REDONDEO_S, 2), v))

    def correr(seg):
        nonlocal t
        for _ in range(max(1, int(round(seg / MUESTRA_S)))):
            sim.correr(MUESTRA_S)
            t += MUESTRA_S
            muestra()

    muestra()
    for accion, seg in guion:
        if accion.endswith("+") or accion.endswith("-"):
            sim.poner(**{accion[:-1]: accion.endswith("+")})       # mantener / soltar (hongo I3)
        elif accion != "t":
            sim.poner(**{accion: True})
            correr(0.3)
            sim.poner(**{accion: False})
            seg = max(0.0, seg - 0.3)
        if seg:
            correr(seg)
    return canales


def huella(cfg: dict) -> dict:
    band = (cfg or {}).get("band") or {}
    if (band.get("engine") or {}).get("version") != 7 and not lm.es_motor_v7(cfg or {}):
        return {}
    h = cache_simulacion.obtener("huella", band, [e[0] for e in ESCENARIOS])
    if h is not None:
        return h
    h = {}
    for nombre, guion in ESCENARIOS:
        h[nombre] = {c: [list(x) for x in serie] for c, serie in _correr(cfg, guion).items()}
    cache_simulacion.guardar("huella", band, [e[0] for e in ESCENARIOS], h)
    return h


def _iguales(a: list, b: list) -> bool:
    """Misma secuencia de valores con tiempos parecidos (+-TOLERANCIA_S)."""
    if len(a) != len(b):
        return False
    return all(x[1] == y[1] and abs(x[0] - y[0]) <= TOLERANCIA_S for x, y in zip(a, b))


def _texto(serie: list, canal: str) -> str:
    if canal == "banda":
        fmt = {v: (("avanza" if v.startswith("dir1") else "en reversa") + f" a {v.split('@')[1]} Hz")
               for _, v in serie if isinstance(v, str) and "@" in v}
        fmt["paro"] = "detenida"
    else:
        fmt = {True: "encendida" if canal in ("Q3", "Q4", "Q5") else "activa", False: "apagada"
               if canal in ("Q3", "Q4", "Q5") else "inactiva"}
    partes = [f"{fmt.get(v, v)} a los {t:g} s" for t, v in serie[1:6]]
    inicio = fmt.get(serie[0][1], serie[0][1]) if serie else "?"
    return inicio + ("; " + ", ".join(partes) if partes else " todo el tiempo")


_PLUMAS = ("Q6", "Q7", "Q8", "Q9")


def _orden_plumas(esc: dict) -> list:
    """Orden en que se mueven las plumas, SIN tiempos: la lista de conjuntos de
    salidas de pluma activas (sin repetir consecutivos). Pluma 2 y luego pluma 1
    [{Q7}, set(), {Q8}] no es lo mismo que las dos a la vez [{Q7, Q8}]."""
    eventos = []
    for c in _PLUMAS:
        for t, v in esc.get(c) or []:
            eventos.append((t, c, v))
    estado, out = set(), []
    for t in sorted({e[0] for e in eventos}):
        for _, c, v in [e for e in eventos if e[0] == t]:
            (estado.add if v else estado.discard)(c)
        actual = frozenset(estado)
        if actual and (not out or out[-1] != actual):
            out.append(actual)
    return out


def _texto_orden(orden: list) -> str:
    if not orden:
        return "sin mover plumas"
    return " -> ".join(" y ".join(NOMBRE[q] for q in sorted(paso)) for paso in orden)


def comparar(antes: dict, despues: dict) -> list:
    difs = []
    if not antes or not despues:
        return difs
    for esc in antes:
        if esc not in despues:
            continue
        for c in CANALES:
            a, b = antes[esc].get(c) or [], despues[esc].get(c) or []
            if not _iguales(a, b):
                difs.append({"modulo": CANALES[c], "escenario": esc, "canal": c,
                             "antes": _texto(a, c), "despues": _texto(b, c)})
        oa, ob = _orden_plumas(antes[esc]), _orden_plumas(despues[esc])
        if oa != ob:
            difs.append({"modulo": "plumas", "escenario": esc, "canal": "plumas_orden", "sin_tiempo": True,
                         "antes": _texto_orden(oa), "despues": _texto_orden(ob)})
    return difs


def fuera_de_zona(difs: list, zona: dict) -> list:
    """Diferencias en modulos que el usuario no menciono (zona vaga: no se juzga).
    - Si habla de tiempos o del contador, eso mueve el MOMENTO de todo: la banda
      queda permitida y de lo demas solo se juzga el ORDEN de las plumas.
    - Una luz que cambia en un escenario donde tambien cambio lo pedido puede ser
      consecuencia (verde = BANDA_CORRIENDO): no se juzga. Las plumas si."""
    mods = set((zona or {}).get("modulos") or [])
    if not mods:
        return []
    temporal = bool(mods & {"tiempos", "conteo"})
    permitidos = mods | ({"motor"} if temporal else set())
    con_pedido = {d["escenario"] for d in difs if d["modulo"] in permitidos and d["canal"] != "plumas_orden"}
    out = []
    for d in difs:
        if d["modulo"] in permitidos:
            continue
        if temporal and d["canal"] != "plumas_orden":
            continue
        if not temporal and d["canal"] == "plumas_orden" and any(
                x["escenario"] == d["escenario"] and x["modulo"] == "plumas" and x["canal"] != "plumas_orden"
                for x in out):
            continue                      # ya se reporto con tiempos
        if d["modulo"] == "torreta" and d["escenario"] in con_pedido:
            continue
        out.append(d)
    return out


def _lineas(difs: list, n: int = 6) -> list:
    vistos, out = set(), []
    for d in difs:
        k = (d["escenario"], d["canal"])
        if k in vistos:
            continue
        vistos.add(k)
        quien = "el orden de las plumas" if d["canal"] == "plumas_orden" else NOMBRE[d["canal"]]
        out.append(f"Con «{d['escenario']}», {quien}: antes {d['antes']}; ahora {d['despues']}.")
        if len(out) >= n:
            break
    return out


def mensaje(difs: list) -> str:
    return ("Se simulo la version ANTERIOR y la NUEVA con los mismos escenarios y la nueva CAMBIO cosas que el "
            "usuario no pidio tocar:\n- " + "\n- ".join(_lineas(difs))
            + "\nDeja esas partes EXACTAMENTE como estaban en el JSON vigente (mismos ids, pasos, tiempos y senales) "
              "y aplica solo el cambio pedido. Devuelve el JSON COMPLETO, sin texto extra.")


MENSAJE_SIN_EFECTO = (
    "Se simulo la version ANTERIOR y la NUEVA con los mismos escenarios y se comportan IGUAL: el cambio pedido no "
    "tuvo ningun efecto. Revisa que la parte pedida realmente cambie lo que hace la banda (senales, pasos, tiempos "
    "o salidas) y devuelve el JSON COMPLETO, sin texto extra.")


def aviso(difs: list) -> str:
    return "Además de lo pedido cambió: " + " ".join(_lineas(difs, 3)) + " Si no es lo que querías, pide corregirlo."
