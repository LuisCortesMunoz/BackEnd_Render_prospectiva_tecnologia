"""
receta_conteo.py — receta FIJA (sin IA) del patron mas pedido de la banda:

    arranca (I1)  ->  cuenta N objetos en S1/S2 con la banda avanzando
    -> al llegar a N la banda sigue X s y se detiene sola
    -> acciones EN ORDEN (plumas / luces), una tras otra
    -> el contador vuelve a 0 y todo queda listo para el siguiente ciclo (I1)

El modelo lo armaba distinto cada vez (sin arranque, reset con SQ1_FIN que deja
el contador trabado, plumas al mismo tiempo, la banda que vuelve a avanzar sola).
Aqui se arma siempre igual, con la estructura probada en el ST real:

    motor:      arranque I1 (o el boton dicho), paro = I2 (si se dice) OR SQ1_CICLO
    CT1:        entrada S, preset N, una_vez, reset SQ1_CICLO (al terminar todo)
    SQ1:        inicio CT1, exclusiva:  [marcha X s] [paro] [accion 1] [accion 2] ...

Solo se aplica si TODO lo que pide el texto cabe en el patron; si hay pausas por
sensor, repeticiones, otras secuencias, parpadeos o acciones por otro sensor,
devuelve None y lo resuelve el modelo como siempre.

    detectar(textos, acl) -> plan | None
    armar(plan)           -> respuesta (formato del modelo)
    verificar(cfg, plan)  -> [] si cumple (2 ciclos completos en lavo_sim)
"""

import re
import unicodedata

import comportamiento as co

PLUMA_S = 3.0                     # una pluma sin tiempo dicho: 3 s de recorrido
_Q_PLUMA = {("1", "sube"): "Q8", ("1", "baja"): "Q9", ("2", "sube"): "Q6", ("2", "baja"): "Q7"}
_Q_LUZ = {"verde": "Q3", "amarilla": "Q4", "roja": "Q5"}
_NOMBRE = {"Q3": "luz verde", "Q4": "luz amarilla", "Q5": "luz roja", "Q6": "pluma 2 sube", "Q7": "pluma 2 baja",
           "Q8": "pluma 1 sube", "Q9": "pluma 1 baja"}

_SUBE = r"(?:se eleva\w*|elev\w*|sub\w*|levant\w*|abr\w*)"
_BAJA = r"(?:desciend\w*|descend\w*|baj\w*|cierr\w*|cerr\w*)"
_NUMP = r"(1|2|uno|dos)"
_RX_PLUMA = re.compile(
    rf"\bpluma\s+{_NUMP}\b(?:\s+(?:debe|tiene que|deben|va a))?\s+(?:a\s+)?(?:se\s+)?(?:(?P<s1>{_SUBE})|(?P<b1>{_BAJA}))|"
    rf"\b(?:(?P<s2>{_SUBE})|(?P<b2>{_BAJA}))\s+(?:la\s+)?pluma\s+{_NUMP}\b")
_RX_LUZ = re.compile(r"\b(?:enciend\w*|encend\w*|prend\w*)\s+(?:la\s+)?(?:luz\s+)?(verde|amarilla|roja)\b|"
                     r"\b(?:luz\s+)?(verde|amarilla|roja)\s+(?:se\s+)?(?:enciend\w*|encend\w*|prend\w*)\b")
_RX_DURA = re.compile(r"^\s*(?:durante|por)\s+(\d+(?:[.,]\d+)?|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|"
                      r"diez)\s*(?:s|seg|segundos?)\b")
# Lo que el patron NO cubre: si aparece, no se usa la receta.
_RX_FUERA = re.compile(
    r"\bparpade\w*|\bintermit\w*|\brepit\w*|\bveces\b|\bsecuencia\s+[12]\b|\botra secuencia\b|\binviert\w*|"
    r"\bizquierda\b.*\bderecha\b|\bpausa\w*|\bdetiene\s+(?:durante\s+)?\d+\s*(?:s|seg)|"
    r"\bdetenga\s+(?:durante\s+)?\d+\s*(?:s|seg)|\b(?:continu\w*|sigue|siga|reanud\w*)\s+(?:avanzando\s+)?(?:despues|luego)\b")
_RX_SENSOR = re.compile(r"\b(?:sensor\s*|s\s?)([12])\b")
_RX_HZ = re.compile(r"\b(\d{1,3})\s*(?:hz|hertz)\b")
_RX_PARO_I2 = re.compile(r"\b(?:con|al (?:presionar|pulsar))\s+(?:el\s+)?(?:boton\s+)?i\s?2\b[^.;]{0,30}\b(?:deten\w*|detien\w*|par\w*)|"
                         r"\bi\s?2\s+(?:la\s+)?(?:detiene|para|apaga)\b")
_RX_DESPUES_PARO = re.compile(r"\b(?:una vez detenid\w*|al detenerse|despues de detenerse|cuando se deteng\w*|"
                              r"ya detenid\w*|al quedar detenid\w*|luego de detenerse|tras detenerse|finalmente)\b")


def _fold(t) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t)


def _sin_aclaraciones(t: str) -> str:
    return re.sub(r"\s*\[[^\]]*\]\s*$", "", str(t or ""))


def _num(s) -> float:
    return float(co._numero(s))


def _acciones(t: str) -> list:
    """Acciones de plumas y luces en el orden en que aparecen: [(Q, segundos|None, pos)]."""
    out = []
    for m in _RX_PLUMA.finditer(t):
        n = next(g for g in (m.group(1), m.group(6)) if g)
        n = {"uno": "1", "dos": "2"}.get(n, n)
        sube = bool(m.group("s1") or m.group("s2"))
        d = _RX_DURA.match(t[m.end():])
        out.append((_Q_PLUMA[(n, "sube" if sube else "baja")], _num(d.group(1)) if d else None, m.start()))
    for m in _RX_LUZ.finditer(t):
        color = m.group(1) or m.group(2)
        d = _RX_DURA.match(t[m.end():])
        out.append((_Q_LUZ[color], _num(d.group(1)) if d else None, m.start()))
    return sorted(out, key=lambda x: x[2])


def detectar(textos, acl: dict = None) -> dict:
    """El plan si TODA la sesion cabe en el patron; None si no."""
    # Respuestas de aclaracion de instrucciones anteriores ("texto [freq_hz: 20 Hz]").
    hz_hist = None
    for x in textos or []:
        mh = re.search(r"\[[^\]]*(?:freq_hz|frecuencia_hz)\s*:\s*(\d{1,3})", str(x or ""))
        if mh:
            hz_hist = int(mh.group(1))
    textos = [_sin_aclaraciones(x) for x in (textos or []) if str(x or "").strip()]
    if not textos:
        return None
    todo = " ".join(_fold(x) for x in textos)
    if _RX_FUERA.search(todo) or co.pausa_por_deteccion(todo):
        return None                     # pausas por deteccion ("se espere 3 s"): no es este patron
    # El conteo y el paro: de la instruccion que los dice (la ultima que los diga).
    pc, base = None, None
    for x in textos:
        p = co.paro_por_conteo(x)
        if p:
            pc, base = p, _fold(x)
    if not pc or co.quita_paro(todo):
        return None
    n = pc["conteo"]
    for x in textos:
        c = co.cambio_conteo(x)
        if c and not co.conteo_en(x):
            n = c
    # Ningun OTRO sensor ni otra accion por sensor (eso no lo arma la receta).
    sensores = {s for s in _RX_SENSOR.findall(todo)}
    if sensores - {pc["senal"][1]}:
        return None
    # Acciones despues del paro, en orden: las de la instruccion del conteo despues del
    # paro, y las de instrucciones posteriores que digan "cuando se detenga / al detenerse".
    acciones = []
    mc = co._RX_CUENTA_N.search(base)      # desde el conteo ("acumule 3"), no desde un "30 Hz"
    inicio = mc.end() if mc else 0
    m = re.search(r"\b(?:deten\w*|detien\w*|deteng\w*|se para|para)\b", base[inicio:])
    corte = inicio + (m.end() if m else 0)
    antes = _acciones(base[:corte])
    if antes:
        return None                     # luces/plumas antes del paro: no es este patron
    acciones += [(q, d) for q, d, _ in _acciones(base[corte:])]
    for x in textos:
        f = _fold(x)
        if f == base:
            continue
        if _acciones(f):
            if not _RX_DESPUES_PARO.search(f):
                return None
            acciones += [(q, d) for q, d, _ in _acciones(f)]
    # Arranque, paro con boton, sentido y velocidad.
    hz = None
    for x in textos:
        mh = _RX_HZ.search(_fold(x))
        if mh:
            hz = int(mh.group(1))
    for k in ("freq_hz", "banda.frecuencia_hz"):
        v = ((acl or {}).get(k) or {})
        r = v.get("respuesta") if isinstance(v, dict) else v
        mh = re.search(r"(\d{1,3})", str(r or ""))
        if mh and hz is None:              # lo dicho en el texto manda sobre la aclaracion
            hz = int(mh.group(1))
    if hz is None:
        hz = hz_hist
    boton = "I2" if re.search(r"\b(?:con|al (?:presionar|pulsar))\s+(?:el\s+)?(?:boton\s+)?i\s?2\b[^.;]{0,40}"
                               r"\b(?:arranc\w*|avanc\w*|avanz\w*|marcha)", todo) else "I1"
    return {"conteo": n, "senal": pc["senal"], "segundos": float(pc.get("segundos") or 0),
            "acciones": acciones, "hz": hz, "direccion": "izquierda" if re.search(r"\bizquierda\b", todo) else "derecha",
            "arranque": boton, "paro_i2": bool(_RX_PARO_I2.search(todo)) and boton != "I2"}


def armar(plan: dict) -> dict:
    pasos = []
    if plan["segundos"] > 0:
        pasos.append({"motor": ["marcha"], "avanza": "tiempo", "segundos": plan["segundos"]})
    pasos.append({"motor": ["paro"], "avanza": "tiempo", "segundos": 0.5})
    luces_fin = []
    for q, d in plan["acciones"]:
        if q in ("Q3", "Q4", "Q5") and d is None:
            luces_fin.append(q)          # luz sin tiempo: queda encendida hasta el siguiente ciclo
            continue
        pasos.append({"motor": ["paro"], "salidas": [q], "avanza": "tiempo", "segundos": d or PLUMA_S})
    r = {
        "name": f"Conteo {plan['conteo']} en {plan['senal']} y paro",
        "banda": {"mover": True, "direccion": plan["direccion"], "frecuencia_hz": plan["hz"]},
        "motor": {"arranque": plan["arranque"], "modo": "enclavado"},
        "condiciones": [], "timers": [], "memorias": [], "eventos": [],
        "contadores": [{"id": "CT1", "entrada": plan["senal"], "preset": plan["conteo"], "modo": "una_vez",
                        "reset": "SQ1_CICLO"}],
        "salidas": {q: "SQ1_FIN" for q in luces_fin},
        "secuencias": [{"id": "SQ1", "inicio": "CT1", "modo": "una_vez", "exclusiva": True, "pasos": pasos}],
        "analisis": {
            "intencion": (f"Arranca con {plan['arranque']}; al contar {plan['conteo']} en {plan['senal']} la banda "
                          + (f"sigue {plan['segundos']:g} s y " if plan["segundos"] else "")
                          + "se detiene; " + (", luego ".join(_NOMBRE[q] for q, _ in plan["acciones"]) + "; "
                                              if plan["acciones"] else "")
                          + "el contador vuelve a 0 al terminar y queda listo para el siguiente ciclo."),
            "compatibilidad": "total", "conservado": [], "modificado": [], "simplificado": [], "no_soportado": [],
            "requiere_confirmacion": False},
        "preguntas": [],
    }
    if plan["paro_i2"]:
        r["condiciones"] = [{"id": "C1", "op": "OR", "entradas": ["I2", "SQ1_CICLO"], "flanco": "nivel"}]
        r["motor"]["paro"] = "C1"
    else:
        r["motor"]["paro"] = "SQ1_CICLO"
    return r


def verificar(cfg: dict, plan: dict) -> list:
    """Dos ciclos completos en el ST real: corre con N-1, al N sigue X s y para,
    acciones en orden, queda detenida, y el 2o ciclo (arranque normal) igual."""
    import lavo_sim
    s = lavo_sim.SimV7()
    err, campo = s.cargar({"band": cfg.get("band") or {}})
    if err:
        return [f"el PLC simulado rechazo la configuracion (error {err}, campo {campo})"]
    sensor = "I4" if plan["senal"] == "S1" else "I5"
    corre = lambda: s.motor() in ("dir1", "dir2")  # noqa: E731
    orden = [q for q, d in plan["acciones"] if not (q in ("Q3", "Q4", "Q5") and d is None)]
    s.correr(0.5)
    for ciclo in (1, 2):
        s.pulsar(plan["arranque"], 0.3)
        s.correr(2)
        if not corre():
            return [f"Ciclo {ciclo}: {plan['arranque']} no arranca la banda."]
        for k in range(plan["conteo"] - 1):
            s.pulsar(sensor, 0.3)
            s.correr(0.7)
            if not corre():
                return [f"Ciclo {ciclo}: la banda se detiene antes de contar {plan['conteo']} (caja {k + 1})."]
        s.pulsar(sensor, 0.3)
        if plan["segundos"] > 1.5:
            s.correr(plan["segundos"] - 1.3)
            if not corre():
                return [f"Ciclo {ciclo}: al contar {plan['conteo']} se detiene antes de los {plan['segundos']:g} s."]
            s.correr(2.6)
        else:
            s.correr(1.5)
        if corre():
            return [f"Ciclo {ciclo}: no se detiene al terminar los {plan['segundos']:g} s."]
        primera, t = {}, 0.0                      # cuando aparece cada accion por primera vez
        while t < sum((d or PLUMA_S) for _, d in plan["acciones"]) + 4:
            for q in s.salidas():
                if q in orden and q not in primera:
                    primera[q] = t
            if corre():
                return [f"Ciclo {ciclo}: la banda vuelve a avanzar sola durante las acciones."]
            s.correr(0.2)
            t += 0.2
        tiempos = [primera.get(q) for q in orden]
        if None in tiempos or any(b <= a for a, b in zip(tiempos, tiempos[1:])):
            vistos = sorted(primera, key=primera.get)
            return [f"Ciclo {ciclo}: las acciones no salen una despues de otra en orden "
                    f"(se vio {vistos} en {[round(primera[q], 1) for q in vistos]} s, se pidio {orden})."]
        s.correr(4)
        if corre() or any(q in s.salidas() for q in orden):
            return [f"Ciclo {ciclo}: al terminar no queda detenida y quieta."]
    return []
