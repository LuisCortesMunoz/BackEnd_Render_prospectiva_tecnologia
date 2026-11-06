"""
reglas_banda.py — el formato comun de REGLAS de la banda: validarlo y probarlo.

Las reglas salen del lector de patrones (constructores_banda) o del extractor
con IA (extractor_banda). Aqui:

  validar(data, texto)   -> reglas normalizadas, o ValueError con lo que esta mal
                            (senales que no existen, rangos, una regla que no sale
                            del texto del usuario...). Lo usa sobre todo el extractor.
  requisitos(reglas)     -> requisitos de comportamiento.py (alternancia, paro solo,
                            conteo, arranque/paro con boton) que se prueban simulando.
  verificar(cfg, reglas) -> una prueba por regla en el ST v7 simulado (lavo_sim):
                            disparar y ver que pasa lo que dice, cuanto dura, etc.
                            [] = cumple lo que se pudo probar.

Asi TODO lo entendido se prueba, no solo lo que reconocia algun detector.
"""

import re
import time

import cache_simulacion
import comportamiento as co

RECURSOS = {"banda": ("avanzar", "detener", "pausar"),
            "verde": ("encender", "apagar"), "amarilla": ("encender", "apagar"), "roja": ("encender", "apagar"),
            "pluma1": ("subir", "bajar"), "pluma2": ("subir", "bajar")}
_Q = {("verde", "encender"): "Q3", ("amarilla", "encender"): "Q4", ("roja", "encender"): "Q5",
      ("pluma1", "subir"): "Q8", ("pluma1", "bajar"): "Q9", ("pluma2", "subir"): "Q6", ("pluma2", "bajar"): "Q7"}
_SIM = {"S1": "I4", "S2": "I5", "I1": "I1", "I2": "I2"}
PRESUPUESTO_S = 12          # tiempo REAL maximo de las pruebas de reglas
MAX_REGLAS = 12


# ── Validar ─────────────────────────────────────────────────────────────────
def _boton(x):
    s = co._senal(re.sub(r"\b(?:el|la|boton|pulsador)\b", "", co._fold(x)).strip()) if x else None
    return s if s in ("I1", "I2") else None


def _sensor(x):
    s = co._senal(re.sub(r"\b(?:el|la)\b", "", co._fold(x)).strip()) if x else None
    return s if s in ("S1", "S2") else None


def _segs(x, campo, problemas, maximo=600):
    if x in (None, "", 0, False):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        problemas.append(f"{campo}: '{x}' no es un numero de segundos.")
        return None
    if not 0 < v <= maximo:
        problemas.append(f"{campo}: {v} s fuera de rango (0..{maximo}).")
        return None
    return v


def _respaldo(fragmento: str, texto: str) -> bool:
    """La regla sale del texto: casi todas las palabras de su fragmento estan en el."""
    pf = [w for w in re.findall(r"[a-z0-9]+", co._fold(fragmento)) if len(w) > 2]
    if not pf:
        return False
    pt = set(re.findall(r"[a-z0-9]+", co._fold(texto)))
    return sum(1 for w in pf if w in pt) / len(pf) >= 0.7


def validar(data: dict, texto: str = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError(["la respuesta no es un objeto JSON"])
    p = []
    out = {"reglas": [], "motor": {"hz": None, "direccion": None}, "alternancia": None, "paro_solo_s": None,
           "conteo": None}
    reglas = data.get("reglas") or []
    if not isinstance(reglas, list) or len(reglas) > MAX_REGLAS:
        raise ValueError([f"'reglas' debe ser una lista de hasta {MAX_REGLAS}"])
    for i, r in enumerate(reglas, 1):
        tag = f"regla {i}"
        if not isinstance(r, dict):
            p.append(f"{tag}: no es un objeto.")
            continue
        c = r.get("cuando") or {}
        cuando = None
        if not isinstance(c, dict):
            p.append(f"{tag}: 'cuando' debe ser un objeto.")
        elif c.get("boton"):
            cuando = {"boton": _boton(c["boton"])} if _boton(c["boton"]) else None
            if not cuando:
                p.append(f"{tag}: el boton '{c['boton']}' no existe (I1 verde, I2 rojo; I3 es el paro de emergencia).")
        elif c.get("sensor"):
            cuando = {"sensor": _sensor(c["sensor"])} if _sensor(c["sensor"]) else None
            if not cuando:
                p.append(f"{tag}: el sensor '{c['sensor']}' no existe (S1 o S2).")
        elif c.get("presionado"):
            cuando = {"presionado": _boton(c["presionado"])} if _boton(c["presionado"]) else None
            if not cuando:
                p.append(f"{tag}: el boton '{c['presionado']}' no existe (I1 o I2).")
        elif c.get("conteo"):
            cuando = {"conteo": True}
        elif c.get("banda") in ("en_marcha", "detenida"):
            cuando = {"banda": c["banda"]}
        else:
            p.append(f"{tag}: 'cuando' sin boton, sensor, conteo ni estado de la banda.")
        hacer = []
        for j, h in enumerate(r.get("hacer") or [], 1):
            if not isinstance(h, dict):
                p.append(f"{tag}.hacer[{j}]: no es un objeto.")
                continue
            rec, acc = co._fold(h.get("recurso")).replace(" ", ""), co._fold(h.get("accion"))
            rec = {"pluma": "pluma1", "luzverde": "verde", "luzroja": "roja", "luzamarilla": "amarilla",
                   "motor": "banda"}.get(rec, rec)
            if rec not in RECURSOS or acc not in RECURSOS[rec]:
                p.append(f"{tag}.hacer[{j}]: '{h.get('recurso')}' no puede '{h.get('accion')}'.")
                continue
            hacer.append({"recurso": rec, "accion": acc,
                          "duracion_s": _segs(h.get("duracion_s"), f"{tag} duracion", p),
                          "espera_s": _segs(h.get("espera_s"), f"{tag} espera", p),
                          "parpadeo": bool(h.get("parpadeo")), "queda": bool(h.get("queda"))})
        if not hacer and not p:
            p.append(f"{tag}: no dice que hacer.")
        if cuando and "presionado" in cuando and any((h["recurso"], h["accion"]) not in _Q for h in hacer):
            p.append(f"{tag}: 'mientras se presiona' solo enciende luces o mueve plumas.")
        frag = str(r.get("fragmento") or "")
        if texto is not None and not _respaldo(frag, texto):
            p.append(f"{tag}: no se encuentra en la instruccion ('{frag[:60]}').")
        if cuando and hacer:
            out["reglas"].append({"cuando": cuando, "hacer": hacer, "fragmento": frag})
    m = data.get("motor") or {}
    if m.get("hz") not in (None, ""):
        try:
            hz = float(m["hz"])
            if not 1 <= hz <= 327:
                raise ValueError
            out["motor"]["hz"] = hz
        except (TypeError, ValueError):
            p.append(f"motor.hz: '{m.get('hz')}' fuera de 1..327 Hz.")
    if m.get("direccion"):
        d = co._fold(m["direccion"])
        if d not in ("derecha", "izquierda"):
            p.append(f"motor.direccion: '{m['direccion']}' debe ser derecha o izquierda.")
        else:
            out["motor"]["direccion"] = d
    if data.get("alternancia"):
        out["alternancia"] = _boton(data["alternancia"])
        if not out["alternancia"]:
            p.append(f"alternancia: '{data['alternancia']}' no es un boton (I1 o I2).")
    out["paro_solo_s"] = _segs(data.get("paro_solo_s"), "paro_solo_s", p, maximo=3600)
    c = data.get("conteo")
    if c:
        try:
            n = int(c.get("n"))
            if not 1 < n <= 100:
                raise ValueError
            sen = _sensor(c.get("senal"))
            if not sen:
                raise TypeError
            out["conteo"] = {"senal": sen, "n": n, "reinicia": bool(c.get("reinicia")),
                             "boton": _boton(c.get("boton")) if c.get("boton") else None}
            if c.get("boton") and not out["conteo"]["boton"]:
                p.append(f"conteo.boton: '{c['boton']}' no puede reiniciar el contador (usa I1 o I2).")
        except (TypeError, ValueError, AttributeError):
            p.append("conteo: necesita senal S1/S2 y n entre 2 y 100.")
    if any("conteo" in r["cuando"] for r in out["reglas"]) and not out["conteo"]:
        p.append("hay reglas con 'conteo' pero no se dice que contar.")
    if not (out["reglas"] or out["alternancia"] or out["paro_solo_s"]):
        p.append("no hay ninguna regla.")
    if p:
        raise ValueError(p)
    return out


# ── Requisitos de comportamiento.py desde las reglas ───────────────────────
def requisitos(r: dict) -> list:
    reqs = []
    if r.get("alternancia"):
        reqs.append({"tipo": "toggle", "senal": r["alternancia"], "texto": "alternancia"})
    if r.get("paro_solo_s"):
        reqs.append({"tipo": "para_sola", "segundos": r["paro_solo_s"], "texto": "paro automatico"})
    c = r.get("conteo")
    if c and any("conteo" in x["cuando"] for x in r.get("reglas") or []):
        reqs.append({"tipo": "conteo", "conteo": c["n"], "senal": c["senal"], "reinicia": c["reinicia"],
                     "boton": c.get("boton"), "texto": "conteo"})
    for x in r.get("reglas") or []:
        b = x["cuando"].get("boton")
        for h in x["hacer"]:
            if b and h["recurso"] == "banda" and h["accion"] == "avanzar" and b != r.get("alternancia"):
                reqs.append({"tipo": "arranca_con", "senal": b, "texto": x.get("fragmento", "")})
            if b and h["recurso"] == "banda" and h["accion"] == "detener" and b != r.get("alternancia"):
                reqs.append({"tipo": "para_con", "senal": b, "texto": x.get("fragmento", "")})
    return reqs


def combinar(reqs: list, extra: list) -> list:
    """reqs + los de extra que no esten ya (mismo tipo y senal)."""
    clave = lambda q: (q["tipo"], q.get("senal"))
    vistos = {clave(q) for q in reqs}
    return list(reqs) + [q for q in extra if clave(q) not in vistos]


# ── Pruebas por regla en el ST simulado ────────────────────────────────────
def _arranque(r: dict):
    if r.get("alternancia"):
        return r["alternancia"]
    for x in r.get("reglas") or []:
        if x["cuando"].get("boton") and any(h["recurso"] == "banda" and h["accion"] == "avanzar" for h in x["hacer"]):
            return x["cuando"]["boton"]
    return None


def _corre(s) -> bool:
    return s.motor() in ("dir1", "dir2")


def _sim(band):
    import lavo_sim
    s = lavo_sim.SimV7()
    err, campo = s.cargar({"band": band})
    if err:
        raise ValueError(f"el PLC simulado rechazo la configuracion (error {err}, campo {campo})")
    s.correr(0.5)
    return s


def _pulsar(s, senal):
    s.pulsar(_SIM[senal], 0.3)


def verificar(cfg: dict, r: dict, presupuesto_s: float = PRESUPUESTO_S) -> list:
    band = (cfg or {}).get("band") or {}
    if (band.get("engine") or {}).get("version") != 7 or not r:
        return []
    # El mismo programa con las mismas reglas da lo mismo: se reutiliza (cache).
    fallas = cache_simulacion.obtener("reglas", band, r)
    if fallas is None:
        fallas, completo = _verificar(band, r, presupuesto_s)
        if completo:
            cache_simulacion.guardar("reglas", band, r, fallas)
    return fallas


def _verificar(band: dict, r: dict, presupuesto_s: float):
    """(fallas, completo); completo=False si se acabo el tiempo."""
    reglas = r.get("reglas") or []
    # Una salida que manejan varias reglas no se prueba sola (depende del orden de los disparos).
    usos = {}
    for x in reglas:
        for h in x["hacer"]:
            usos[h["recurso"]] = usos.get(h["recurso"], 0) + 1
    arranque = _arranque(r)
    limite = time.monotonic() + presupuesto_s
    problemas = []
    try:
        for x in reglas:
            c = x["cuando"]
            if "conteo" in c:
                continue                          # lo prueba comportamiento (conteo, 2 rondas)
            for h in x["hacer"]:
                if time.monotonic() > limite:
                    return problemas, False       # sin tiempo: lo que falta no se reporta
                if "banda" in c:
                    problemas += _probar_mientras(band, c["banda"], h, arranque)
                    continue
                if "presionado" in c:
                    problemas += _probar_presionado(band, c["presionado"], h)
                    continue
                if h["recurso"] != "banda" and usos[h["recurso"]] > 1 and h["accion"] != "apagar":
                    continue
                senal = c.get("boton") or c.get("sensor")
                if h["recurso"] == "banda":
                    problemas += _probar_banda(band, senal, h, arranque)
                elif h["accion"] == "apagar":
                    problemas += _probar_apagar(band, senal, h, reglas)
                else:
                    problemas += _probar_salida(band, senal, h)
    except ValueError as e:
        problemas.append(str(e))
    return problemas, True


def _desc(senal, h) -> str:
    extra = f" {h['duracion_s']:g} s" if h.get("duracion_s") else ""
    return f"«con {senal}: {h['recurso']} {h['accion']}{extra}»"


def _probar_salida(band, senal, h) -> list:
    q = _Q[(h["recurso"], h["accion"])]
    s = _sim(band)
    _pulsar(s, senal)
    s.correr((h.get("espera_s") or 0) + 0.4)
    if h.get("parpadeo"):
        v = []
        for _ in range(int(min(h.get("duracion_s") or 3, 4) * 10)):
            s.correr(0.1)
            v.append(s.q(q))
        if sum(1 for a, b in zip(v, v[1:]) if a != b) < 2:
            return [f"Regla {_desc(senal, h)}: la salida {q} no parpadea."]
    elif not s.q(q):
        return [f"Regla {_desc(senal, h)}: al activarse {senal} la salida {q} no enciende."]
    if h.get("duracion_s"):
        s.correr(h["duracion_s"] + 0.6)
        if s.q(q):
            return [f"Regla {_desc(senal, h)}: {q} sigue encendida despues de {h['duracion_s']:g} s."]
    return []


def _probar_apagar(band, senal, h, reglas) -> list:
    q = _Q[(h["recurso"], "encender")]
    prende = next((x["cuando"].get("boton") or x["cuando"].get("sensor") for x in reglas
                   for y in x["hacer"] if y["recurso"] == h["recurso"] and y["accion"] == "encender"
                   and (x["cuando"].get("boton") or x["cuando"].get("sensor"))), None)
    if not prende or prende == senal:
        return []
    s = _sim(band)
    _pulsar(s, prende)
    s.correr(0.5)
    if not s.q(q):
        return []                                  # lo reporta la prueba del encendido
    _pulsar(s, senal)
    s.correr(0.5)
    return [] if not s.q(q) else [f"Regla {_desc(senal, h)}: al activarse {senal} la luz no se apaga."]


def _probar_banda(band, senal, h, arranque) -> list:
    s = _sim(band)
    if h["accion"] == "avanzar":
        _pulsar(s, senal)
        s.correr(2.0)
        return [] if _corre(s) else [f"Regla {_desc(senal, h)}: al activarse {senal} la banda no avanza."]
    if not arranque or arranque == senal:
        return []                                  # no se sabe arrancarla aparte: lo prueban otras pruebas
    _pulsar(s, arranque)
    s.correr(2.0)
    if not _corre(s):
        return []                                  # lo reporta la prueba del arranque
    _pulsar(s, senal)
    s.correr(1.6 + (h.get("espera_s") or 0))
    if _corre(s):
        return [f"Regla {_desc(senal, h)}: al activarse {senal} la banda no se detiene."]
    if h["accion"] == "pausar" and h.get("duracion_s"):
        s.correr(h["duracion_s"] + 1.0)
        if not _corre(s):
            return [f"Regla {_desc(senal, h)}: la banda no vuelve a avanzar despues de {h['duracion_s']:g} s."]
    return []


def _probar_presionado(band, boton, h) -> list:
    """Presionado: la salida enciende; al soltar, se apaga."""
    q = _Q[(h["recurso"], h["accion"])]
    s = _sim(band)
    s.poner(**{_SIM[boton]: True})
    s.correr(0.5)
    presionado = s.q(q)
    s.poner(**{_SIM[boton]: False})
    s.correr(0.5)
    if not presionado:
        return [f"Regla «{q} mientras se presiona {boton}»: al presionar {boton} no enciende."]
    if s.q(q):
        return [f"Regla «{q} mientras se presiona {boton}»: sigue encendida al soltar {boton}."]
    return []


def _probar_mientras(band, estado, h, arranque) -> list:
    q = _Q[(h["recurso"], "encender")]
    if not arranque:
        return []
    s = _sim(band)
    quieta = s.q(q)
    _pulsar(s, arranque)
    s.correr(2.0)
    if not _corre(s):
        return []
    if h.get("parpadeo"):
        v = []
        for _ in range(20):
            s.correr(0.1)
            v.append(s.q(q))
        en_marcha = sum(1 for a, b in zip(v, v[1:]) if a != b) >= 2
    else:
        en_marcha = s.q(q)
    if estado == "en_marcha" and (quieta or not en_marcha):
        return [f"Regla «{h['recurso']} mientras la banda avanza»: {q} "
                + ("se enciende con la banda detenida." if quieta else "no enciende con la banda en marcha.")]
    if estado == "detenida" and (not quieta or en_marcha):
        return [f"Regla «{h['recurso']} con la banda detenida»: {q} no sigue el estado de la banda."]
    return []


def contradice(parcial: dict, completo: dict) -> list:
    """Lo que el lector de patrones SI entendio (parcial) y el extractor no trae
    igual (completo): [(cuando, recurso, accion)]. Vacio = no se contradicen."""
    if not parcial:
        return []
    clave = lambda r: {(tuple(sorted(x["cuando"].items())), h["recurso"], h["accion"])
                       for x in (r.get("reglas") or []) for h in x["hacer"]}
    faltan = sorted(clave(parcial) - clave(completo))
    if parcial.get("alternancia") and parcial["alternancia"] != completo.get("alternancia"):
        faltan.append(("alternancia", parcial["alternancia"], ""))
    return faltan
