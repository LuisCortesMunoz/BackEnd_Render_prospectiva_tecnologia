"""
ediciones.py — cambios de VALOR sobre la logica vigente, sin el modelo (banda v7).

Cuando la instruccion solo cambia un dato de lo que ya existe, el modelo tendia a
rehacer todo (100 s y a veces rompia lo demas) o a no aplicarlo (banco N2: "mejor
que sean 12 segundos" se quedaba en 8 s). Aqui se aplica directo sobre el JSON
vigente:

  tiempo con el anterior   "cambia los 10 s por 6 s", "6 s en vez de 10 s"
  tiempo nuevo             "mejor que sean 12 segundos", "que la verde dure solo 3 s",
                           "la espera debe ser de 5 s"  -> el UNICO tiempo que
                           corresponde (la luz / pluma que nombra manda; si no nombra
                           ninguna, espera / marcha; si no dice nada, el unico tiempo)
  duracion de una luz      "que la verde dure 3 s" y la verde no tenia tiempo ->
                           su regla pasa por un timer PULSO de 3 s
  frecuencia               "cambia la velocidad a 45 Hz"
  color                    "la amarilla en lugar de la roja", "cambia la roja por la verde"
  conteo                   "cambia el conteo a 5"
  repeticiones             "en vez de 3 veces que sean 2", "que se repita 4 veces"
  "en la secuencia N ..."  el cambio solo se hace en esa secuencia

Solo si el mensaje NO pide nada mas (si nombra sensores, plumas, otras luces,
acciones nuevas... lo resuelve el modelo) y si el dato a cambiar es UNO sin duda.
Quien lo llama valida, simula toda la sesion y compara con la vigente.

aplicar(texto, resp_vigente) -> (resp_nueva, [que se cambio]) | None
"""

import copy
import re
import unicodedata

import cambios_texto
import comportamiento
import etapas_orden
import lavo_motor as lm

_NOMBRE_Q = {"Q3": "verde", "Q4": "amarilla", "Q5": "roja", "Q6": "pluma2", "Q7": "pluma2", "Q8": "pluma1",
             "Q9": "pluma1"}
_EVENTO_Q = {"verde": "Q3", "amarilla": "Q4", "roja": "Q5"}
_CTX_Q = {"verde": {"Q3"}, "amarilla": {"Q4"}, "amarillo": {"Q4"}, "roja": {"Q5"}, "rojo": {"Q5"},
          "pluma 1": {"Q8", "Q9"}, "pluma1": {"Q8", "Q9"}, "pluma 2": {"Q6", "Q7"}, "pluma2": {"Q6", "Q7"}}
_CTX_PAUSA = {"espera", "pausa", "retardo"}
_CTX_MARCHA = {"avance", "marcha"}

# Lo que NO es un cambio de valor (si queda en el mensaje, lo resuelve el modelo).
_RX_OTRO = re.compile(r"\b(?:sensor\w*|s ?[12]|i ?[1-9]|detect\w*|pluma\w*|verde|amarill\w*|roj[ao]s?|luz|luces|"
                      r"parpade\w*|intermit\w*|inviert\w*|izquierda|derecha|sentido|contador\w*|cont\w*|cuent\w*|"
                      r"caja\w*|pieza\w*|hz|hertz|segundos?|seg|minutos?|veces|repit\w*|repet\w*|arranc\w*|"
                      r"deten\w*|detien\w*|para|pare|pausa\w*|espera\w*|agreg\w*|anad\w*|quita\w*|"
                      r"elimina\w*|ademas|tambien|secuencia\w*|boton\w*|enciend\w*|prend\w*|apag\w*|sub\w*|baj\w*|"
                      r"\d+)\b")
_RELLENO = re.compile(r"\b(?:la|el|los|las|que|de|del|en|a|al|y|ahora|mejor|solo|solamente|por favor|"
                      r"cambia\w*|cambi\w*|pon\w*|haz|hazlo|quiero|debe|deben|ser|sea|sean|dure\w*|durar|"
                      r"velocidad|frecuencia|vaya|ande|corra|lugar|vez|luz|ya|no|unos|tiempo|con)\b")
_RX_SQ = re.compile(r"\bsecuencia\s*(\d)\b")
_RX_HZ = re.compile(r"\b(?:cambi\w*|pon\w*|ahora|que (?:vaya|ande|corra|avance|sea)|sube\w*|baja\w*|"
                    r"aument\w*|reduc\w*|velocidad|frecuencia)\b[^.;]{0,40}?(\d+(?:[.,]\d+)?)\s*(?:hz|hertz)\b")
_RX_VECES = re.compile(r"\ben vez de (\d+)(?: veces)?\s*,?\s*(?:que sean|sean) (\d+)(?: veces)?|"
                       r"\bque se repita\w* (\d+) veces")


def _fold(t) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _solo_esto(t: str, spans, ctx=()) -> bool:
    """Quitando lo reconocido (spans), las palabras de contexto y el relleno, no queda
    nada que pida otra cosa."""
    resto = t
    for a, b in sorted(spans, reverse=True):
        resto = resto[:a] + " " + resto[b:]
    for c in ctx:
        resto = re.sub(rf"\b{re.escape(c)}\b", " ", resto)
    resto = _RELLENO.sub(" ", resto)
    return not _RX_OTRO.search(resto)


# ── Tiempos de la logica y a que estan ligados ─────────────────────────────
def _tiempos(resp: dict) -> list:
    """[(contenedor, clave, valor, {recursos})]: cada tiempo editable del JSON y lo que
    controla (salidas Q, 'pausa', 'marcha' y la secuencia SQn si es un paso)."""
    out = []
    origenes = etapas_orden._origenes(resp)
    sal = resp.get("salidas") if isinstance(resp.get("salidas"), dict) else {}
    motor = resp.get("motor") if isinstance(resp.get("motor"), dict) else {}
    for t in lm._lista(resp.get("timers")):
        if not isinstance(t, dict) or t.get("segundos") in (None, ""):
            continue
        tid = str(t.get("id") or "").upper()
        rec = {lm.salida_canonica(q) for q, regla in sal.items() if tid in origenes(regla)}
        for campo, nombre in (("pausa", "pausa"), ("paro", "marcha"), ("arranque", "pausa")):
            if tid in origenes(motor.get(campo)):
                rec.add(nombre)
        out.append((t, "segundos", float(t["segundos"]), rec))
    for s in lm._lista(resp.get("secuencias")):
        for p in lm._lista((s or {}).get("pasos")):
            if not isinstance(p, dict) or not float(p.get("segundos") or 0):
                continue
            rec = {lm.salida_canonica(q) for q in lm._lista(p.get("salidas"))} | {str(s.get("id") or "").upper()}
            rec.add("marcha" if "marcha" in [str(m).lower() for m in lm._lista(p.get("motor"))] else "pausa")
            out.append((p, "segundos", float(p["segundos"]), rec))
    for ev in lm._lista(resp.get("eventos")):
        if not isinstance(ev, dict):
            continue
        acc = ev.get("acciones") if isinstance(ev.get("acciones"), dict) else {}
        rec = {_EVENTO_Q[k] for k in acc if k in _EVENTO_Q}
        rec |= {q for k in acc for q in _CTX_Q.get(str(k).lower(), ())}
        if "banda" in acc:
            rec.add("pausa")
        if ev.get("duracion_s") not in (None, "", 0):
            out.append((ev, "duracion_s", float(ev["duracion_s"]), rec))
        if ev.get("espera_s") not in (None, "", 0):
            out.append((ev, "espera_s", float(ev["espera_s"]), {"pausa"}))
    return out


def _ctx_recursos(ctx) -> set:
    rec = set()
    for c in ctx:
        rec |= _CTX_Q.get(c, set())
        if c in _CTX_PAUSA:
            rec.add("pausa")
        if c in _CTX_MARCHA:
            rec.add("marcha")
    return rec


_GRUPO_Q = {"Q8": "pluma1", "Q9": "pluma1", "Q6": "pluma2", "Q7": "pluma2"}


def _grupos(cand) -> set:
    """Recursos que controlan esos tiempos (pluma 1, pluma 2, cada luz, pausa, marcha)."""
    out = set()
    for _, _, _, rec in cand:
        out |= {_GRUPO_Q.get(r, r) for r in rec if not str(r).startswith("SQ")}
    return out


def _tiempo_nuevo(resp, ctx, n, sq=None):
    tiempos = [x for x in _tiempos(resp) if not sq or sq in x[3]]
    quiere = _ctx_recursos(ctx)
    # La luz / pluma que nombra manda; "espera" / "marcha" solo si no nombra ninguna.
    quiere_q = {q for q in quiere if q.startswith("Q")}
    if quiere_q:
        cand = [x for x in tiempos if x[3] & quiere_q]
    else:
        cand = [x for x in tiempos if not quiere or x[3] & quiere]
    if len(cand) == 1:
        cont, clave, viejo, _ = cand[0]
        if abs(viejo - n) < 0.05:
            return None
        cont[clave] = n
        return [f"{viejo:g} s -> {n:g} s"]
    # El mismo tiempo repetido para UN solo recurso (el timer de la pluma 1 y la duracion
    # de su evento, ambos 3 s): es un solo dato para el usuario, se cambian todos.
    if len(cand) > 1 and len({round(x[2], 2) for x in cand}) == 1 and len(_grupos(cand)) == 1:
        viejo = cand[0][2]
        if abs(viejo - n) < 0.05:
            return None
        for cont, clave, _, _ in cand:
            cont[clave] = n
        return [f"{viejo:g} s -> {n:g} s"]
    # "que la verde dure 3 s" y la verde no tiene tiempo: su regla pasa por un PULSO.
    luces = [q for q in quiere_q if q in ("Q3", "Q4", "Q5")]
    sal = resp.get("salidas") if isinstance(resp.get("salidas"), dict) else {}
    if not cand and len(luces) == 1 and quiere_q == {luces[0]}:
        q = luces[0]
        clave = next((k for k in sal if lm.salida_canonica(k) == q and sal[k] not in (None, "")), None)
        if clave is None:
            return None
        t = lm._siguiente_id(resp, "timers", "T", lm.N_TIMER)
        t.update(tipo="PULSO", entrada=sal[clave], reset=None, segundos=n)
        sal[clave] = t["id"]
        return [f"la luz {_NOMBRE_Q[q]} dura {n:g} s"]
    return None


def _tiempo_viejo(resp, viejo, nuevo, sq=None):
    hechos = 0
    for cont, clave, v, rec in _tiempos(resp):
        if abs(v - viejo) < 0.05 and (not sq or sq in rec):
            cont[clave] = nuevo
            hechos += 1
    return [f"{viejo:g} s -> {nuevo:g} s"] if hechos else None


def _color(resp, viejo, nuevo):
    if viejo not in repr(resp):
        return None
    nombre_v, nombre_n = _NOMBRE_Q[viejo], _NOMBRE_Q[nuevo]

    def cambiar(o):
        if isinstance(o, dict):
            return {(nuevo if k == viejo else (nombre_n if k == nombre_v else k)): cambiar(v) for k, v in o.items()}
        if isinstance(o, list):
            return [cambiar(x) for x in o]
        return nuevo if o == viejo else o
    nuevo_resp = cambiar(resp)
    resp.clear()
    resp.update(nuevo_resp)
    return [f"luz {nombre_v} -> {nombre_n}"]


def aplicar(texto: str, resp_vigente: dict):
    if not isinstance(resp_vigente, dict):
        return None
    t = _fold(cambios_texto.corregir(texto))
    resp = copy.deepcopy(resp_vigente)
    hecho, spans, ctx = None, [], set()
    m_sq = _RX_SQ.search(t)
    sq = f"SQ{m_sq.group(1)}" if m_sq else None
    extra = [m_sq.span()] if m_sq else []

    subs = cambios_texto.sustituciones(texto)
    if subs["q"] and not subs["t"]:
        viejo, nuevo = subs["q"][0]
        spans = [m.span() for rx in cambios_texto._RX_Q for m in rx.finditer(t)]
        hecho = _color(resp, viejo, nuevo)
    elif subs["t"] and not subs["q"]:
        viejo, nuevo = subs["t"][0]
        spans = [m.span() for rx in cambios_texto._RX_T for m in rx.finditer(t)]
        hecho = _tiempo_viejo(resp, viejo, nuevo, sq)
    else:
        tn = cambios_texto.tiempo_nuevo(texto)
        m_hz = _RX_HZ.search(t)
        m_v = _RX_VECES.search(t)
        n_conteo = comportamiento.cambio_conteo(texto)
        if tn and not m_hz:
            ctx, n = tn
            spans = [m.span() for m in cambios_texto._RX_TIEMPO_SOLO.finditer(t)]
            hecho = _tiempo_nuevo(resp, ctx, n, sq)
        elif m_hz and not tn:
            spans = [m_hz.span()]
            b = resp.setdefault("banda", {})
            nuevo = float(m_hz.group(1).replace(",", "."))
            if b.get("frecuencia_hz") is not None and abs(float(b["frecuencia_hz"]) - nuevo) > 0.5:
                b["frecuencia_hz"] = int(round(nuevo))
                hecho = [f"{nuevo:g} Hz"]
        elif m_v:
            spans = [m_v.span()]
            n = int(m_v.group(2) or m_v.group(3))
            seqs = [s for s in lm._lista(resp.get("secuencias")) if isinstance(s, dict)
                    and str(s.get("modo") or "").lower() == "repetir"]
            if len(seqs) == 1 and int(seqs[0].get("repeticiones") or 0) != n:
                seqs[0]["repeticiones"] = n
                hecho = [f"se repite {n} veces"]
        elif n_conteo:
            cts = [c for c in lm._lista(resp.get("contadores")) if isinstance(c, dict)]
            m_c = comportamiento._RX_CAMBIA_CONTEO.search(t)
            if m_c:
                fin = re.search(r"[.;]|$", t[m_c.end():])
                spans = [(m_c.start(), m_c.end() + fin.start())]
            if len(cts) == 1 and int(cts[0].get("preset") or 0) != int(n_conteo):
                cts[0]["preset"] = int(n_conteo)
                hecho = [f"conteo a {int(n_conteo)}"]
    if not hecho or not spans:
        return None
    if not _solo_esto(t, spans + extra, ctx):
        return None
    return resp, hecho
