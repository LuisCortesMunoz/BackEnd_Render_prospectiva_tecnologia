"""
preguntas_banda.py — preguntas de aclaracion con OPCIONES (banda v7).

Una instruccion de movimiento incompleta ("Quiero mover la banda") se
completa con preguntas que se responden con un clic: arranque, direccion,
frecuencia y paro. Las opciones salen del hardware real (lavo_motor.HW_BANDA:
botones, sensores, rango del VFD); solo se pregunta lo que falta (no esta en
el texto, en las respuestas ya dadas ni en la logica activa).

Con todo respondido el JSON se arma aqui mismo, sin llamar al modelo: sobre
la logica activa si la hay (edicion incremental) o desde cero.

Tambien completa con opciones las preguntas del modelo que lleguen sin ellas.
"""

import copy
import re
import unicodedata

import aclaraciones as ac
import lavo_motor as lm


def _fold(s) -> str:
    t = unicodedata.normalize("NFD", str(s or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


# ── Opciones desde el hardware ─────────────────────────────────────────────
def _entradas_operables() -> list:
    """Botones y sensores de la banda (sin el paro de emergencia)."""
    ops = []
    for iid, e in lm.HW_BANDA["entradas"].items():
        if e.get("emergencia"):
            continue
        if e.get("sensor"):
            ops.append(f"{e['alias']} ({e['desc']})")
        else:
            ops.append(f"{iid} ({e['desc'].split(' /')[0].replace('boton', 'botón')})")
    return ops


AUTOMATICO = "Automático al cargar el programa"
TEMPORIZADOR = "Temporizador"
CONTINUO = "Movimiento continuo (solo el paro de emergencia I3)"
FRECUENCIAS = ["10 Hz", "20 Hz", "30 Hz", "40 Hz"]
TIEMPOS = ["5 s", "10 s", "30 s", "60 s"]


def pregunta(clave: str, excluir=()) -> dict:
    """Pregunta con opciones para un parametro de movimiento."""
    if clave == "arranque":
        q = {"pregunta": "¿Con qué arranca la banda?", "opciones": _entradas_operables() + [AUTOMATICO]}
    elif clave == "direccion":
        q = {"pregunta": "¿Hacia dónde se mueve la banda?", "opciones": ["Derecha", "Izquierda"]}
    elif clave == "freq_hz":
        q = {"pregunta": "¿A qué frecuencia debe moverse la banda?", "opciones": list(FRECUENCIAS)}
    elif clave == "paro":
        q = {"pregunta": "¿Cómo se detiene la banda?",
             "opciones": [o for o in _entradas_operables() if o.split(" ")[0] not in excluir]
             + [TEMPORIZADOR, CONTINUO]}
    elif clave == "tiempo_paro_s":
        q = {"pregunta": "¿Cuántos segundos se mueve antes de detenerse?", "opciones": list(TIEMPOS)}
    else:
        raise ValueError(clave)
    return {"id": clave, "campo": clave, **q}


def senal(respuesta):
    """'I1 (boton verde)' -> 'I1', 'S1 (sensor S1)' -> 'S1', automatico -> 'PRIMER_CICLO'."""
    t = _fold(respuesta)
    if "automatic" in t or "al cargar" in t:
        return "PRIMER_CICLO"
    m = re.match(r"\s*([is]\d)\b", t)
    return m.group(1).upper() if m else None


# ── ¿Es un movimiento basico incompleto? ───────────────────────────────────
_RX_MOVER = re.compile(r"\b(mueve\w*|mover|muevan|mueva|avanz\w*|arranc\w*|corr[ae]\w*|gir[ae]\w*|"
                       r"pon(?:er)? (?:a )?(?:andar|funcionar|en marcha)|en marcha|"
                       r"(?:enciende|prende|activa)\w* (?:la )?banda)\b")
_RX_BANDA = re.compile(r"\b(banda|transportadora|cinta)\b")
# Con cualquiera de estas piezas ya no es "solo moverla": decide el modelo.
_RX_OTRO = re.compile(r"\b(torreta|luz|luces|lampara|verde|amarill\w*|roj[ao]s?|pluma\w*|cuent\w*|contador\w*|"
                      r"caja\w*|pieza\w*|producto\w*|secuencia\w*|paso\w*|luego|despues|repit\w*|veces|"
                      r"parpade\w*|pausa\w*|espera\w*|cuando|invierte|invertir|vuelve|proceso|"
                      r"empaquet\w*|clasific\w*|llenad\w*|etiquet\w*)\b")
_RX_HZ = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:hz|hertz)\b")
_RX_DIR = re.compile(r"\b(derecha|izquierda)\b")
_RX_ARRANQUE = re.compile(r"\b(i1|i2|s1|s2|boton|sensor\w*|automatic\w*|al cargar|de inmediato|siempre|"
                          r"al presionar|al pulsar)\b")
_RX_PARO = re.compile(r"\b(para\b|paro|detien\w*|deten\w*|deteng\w*|hasta|durante|\d+\s*(?:s|seg\w*|min\w*)\b|"
                      r"continu\w*|sin parar|indefinid\w*|siempre)")


def es_movimiento_basico(texto: str) -> bool:
    t = _fold(texto)
    return bool(_RX_MOVER.search(t) and _RX_BANDA.search(t) and not _RX_OTRO.search(t))


def _conocidos_texto(texto: str) -> set:
    t = _fold(texto)
    k = set()
    if _RX_HZ.search(t):
        k.add("freq_hz")
    if _RX_DIR.search(t):
        k.add("direccion")
    if _RX_ARRANQUE.search(t):
        k.add("arranque")
    if _RX_PARO.search(t):
        k.add("paro")
    return k


def _conocidos_logica(prev) -> set:
    """Lo que ya fija la logica activa (si la banda ya se mueve, todo)."""
    b = (prev or {}).get("banda") or {}
    if not b.get("mover"):
        return set()
    k = {"direccion", "paro"}
    if b.get("frecuencia_hz") not in (None, ""):
        k.add("freq_hz")
    if ((prev or {}).get("motor") or {}).get("arranque"):
        k.add("arranque")
    return k


def analizar(texto: str, prev, acl: dict) -> dict:
    """{"aplica", "preguntas"} para una instruccion de movimiento basico.
    aplica=False -> sigue el flujo de siempre (modelo)."""
    if not es_movimiento_basico(texto):
        return {"aplica": False, "preguntas": []}
    conocidos = _conocidos_texto(texto) | _conocidos_logica(prev)
    faltan = [c for c in ("arranque", "direccion", "freq_hz", "paro") if c not in conocidos and c not in acl]
    # El texto o la logica ya lo dicen todo: lo resuelve el modelo como siempre.
    if not faltan and not any(c in acl for c in ("arranque", "direccion", "freq_hz", "paro")):
        return {"aplica": False, "preguntas": []}
    excl = (senal(acl["arranque"]["respuesta"]),) if "arranque" in acl else ()
    preguntas = [pregunta(c, excluir=excl) for c in faltan]
    if "paro" in acl and _fold(acl["paro"]["respuesta"]).startswith("temporizador") and "tiempo_paro_s" not in acl:
        preguntas.append(pregunta("tiempo_paro_s"))
    return {"aplica": True, "preguntas": preguntas,
            # solo se arma aqui si el texto no trae datos que este modulo no lee
            "construible": not (_conocidos_texto(texto) - {"direccion"})}


def construir(texto: str, prev, acl: dict) -> dict:
    """JSON del motor v7 (formato del modelo) con las respuestas, sobre `prev`."""
    resp = copy.deepcopy(prev) if prev else {"name": ""}
    for k in ("analisis", "preguntas", "no_soportado"):
        resp.pop(k, None)
    banda = resp.setdefault("banda", {})
    banda["mover"] = True
    d = ac.direccion(acl) or ("izquierda" if "izquierda" in _fold(texto) else None) or banda.get("direccion") or "derecha"
    banda["direccion"] = d
    f = ac.frecuencia(acl)
    if f is not None:
        banda["frecuencia_hz"] = f
    motor = resp.setdefault("motor", {})
    if "arranque" in acl:
        motor["arranque"] = senal(acl["arranque"]["respuesta"]) or "I1"
    motor.setdefault("modo", "enclavado")
    hecho = []
    if "paro" in acl:
        p = _fold(acl["paro"]["respuesta"])
        if p.startswith("temporizador"):
            m = re.search(r"\d+", str((acl.get("tiempo_paro_s") or {}).get("respuesta") or ""))
            usados = {str(t.get("id")) for t in resp.get("timers") or [] if isinstance(t, dict)}
            tid = next(f"T{i}" for i in range(1, 17) if f"T{i}" not in usados)
            resp.setdefault("timers", []).append({"id": tid, "tipo": "TON", "entrada": "BANDA_CORRIENDO",
                                                  "reset": None, "segundos": int(m.group(0)) if m else 10})
            motor["paro"] = tid
            hecho.append(f"se detiene a los {resp['timers'][-1]['segundos']} s de marcha")
        elif "continuo" in p:
            motor.pop("paro", None)
            hecho.append("movimiento continuo (paro de emergencia I3)")
        else:
            motor["paro"] = senal(acl["paro"]["respuesta"])
            hecho.append(f"paro con {motor['paro']}")
    if not resp.get("name"):
        resp["name"] = f"Banda {d} {banda.get('frecuencia_hz') or ''} Hz".replace("  ", " ")
    resp["analisis"] = {"intencion": "mover la banda", "compatibilidad": "total",
                        "conservado": [f"arranque {motor.get('arranque') or 'I1'}", f"direccion {d}",
                                       f"{banda.get('frecuencia_hz')} Hz"] + hecho,
                        "modificado": [], "simplificado": [], "no_soportado": [], "requiere_confirmacion": False}
    resp["preguntas"] = []
    return resp


# ── Preguntas del modelo sin opciones ──────────────────────────────────────
_OPCIONES_POR_TEMA = [
    (re.compile(r"segundo|tiempo|espera|durac|retardo"), ["3 s", "5 s", "10 s", "30 s"]),
    (re.compile(r"repeti|veces|ciclos"), ["1", "2", "3", "5", "10"]),
    (re.compile(r"cantidad|cajas|piezas|productos|cuantas|cuantos"), ["1", "3", "5", "10"]),
    (re.compile(r"sensor"), ["S1 (sensor S1)", "S2 (sensor S2)"]),
    (re.compile(r"pluma"), ["Pluma 1", "Pluma 2", "Las dos plumas"]),
    (re.compile(r"luz|lampara|torreta|salida"), ["Q3 (torreta verde)", "Q4 (torreta amarilla)", "Q5 (torreta roja)"]),
    (re.compile(r"boton|entrada|arranc|inicia"), None),   # -> entradas reales
]


def completar_opciones(preguntas: list) -> list:
    """Cada pregunta con opciones para elegir con un clic (del hardware real)."""
    out = []
    for q in preguntas or []:
        q = dict(q)
        if not q.get("opciones"):
            k = ac.clave_pregunta(q)
            if k in ("arranque", "direccion", "freq_hz", "paro", "tiempo_paro_s"):
                q["opciones"] = pregunta(k)["opciones"]
            else:
                tema = _fold(f"{q.get('campo', '')} {q.get('pregunta', '')}")
                for rx, ops in _OPCIONES_POR_TEMA:
                    if rx.search(tema):
                        q["opciones"] = list(ops) if ops else _entradas_operables()
                        break
        out.append(q)
    return out
