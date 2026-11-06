"""
intencion_banda.py — la INTENCION de una instruccion de la banda, en un solo formato.

Antes cada detector leia el texto por su cuenta y _generar_logica_banda_v7
juntaba los resultados a mano:

  preguntas_banda   "quiero mover la banda": arranque, direccion, Hz, paro
  etapas_orden      acciones en orden, varias secuencias, ciclos con conteo
  comportamiento    arranque/paro con boton, paro automatico, conteo, alternancia

Aqui se llaman TAL CUAL (no cambia lo que entienden ni lo que se le pide al
modelo) y lo que entendieron queda en una estructura comun:

  analizar(texto, historial, acl, prev) -> intencion
  {
    "texto", "correccion",
    "movimiento":  resultado de preguntas_banda.analizar,
    "etapas":      resultado de etapas_orden.detectar (o de_historial en una correccion),
    "etapas_de_historial": bool,
    "requisitos":  comportamientos que se prueban simulando (comportamiento),
    "motor":       {"arranque", "paro": [...], "alternancia", "paro_solo", "hz", "direccion"},
    "conteos":     [{"senal", "n", "reinicia", "boton", "en_secuencia"}],
    "secuencias":  [{"n", "etapas", "repite", "veces", "conteo"}],
    "modulos":     ["motor", "conteo", "secuencias", ...]  (que partes usa la instruccion),
    "faltan":      preguntas pendientes (con opciones), en el orden en que se hacen,
    "conflictos":  contradicciones entre partes (solo se registran por ahora),
  }

  bloques_modelo(intencion) -> mensajes de sistema con las recetas (los mismos de antes)
  resumen(intencion)        -> version corta para analysis["intencion"] y el log

Es la base de la etapa 3 (constructores sin modelo por modulo) y de la 4
(guias del prompt solo para los modulos que se usan).
"""

import re
import unicodedata

import comportamiento
import etapas_orden
import preguntas_banda

_RX_HZ = re.compile(r"\b(\d{1,3})\s*(?:hz|hertz)\b")


def _fold(texto) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def analizar(texto: str, historial=None, acl: dict = None, prev=None, correccion: bool = False) -> dict:
    """Corre los detectores de siempre y junta lo que entendieron."""
    acl = acl or {}
    historial = list(historial or [])
    # Acciones en orden pedidas en ESTA instruccion; en una correccion o edicion
    # ("la secuencia esta mal", "agrega la roja al final") las de la instruccion
    # original del historial, con lo que respondan las instrucciones posteriores
    # (especificacion viva: lo dicho despues pesa sobre lo de antes).
    etapas = etapas_orden.detectar(texto, "banda", acl)
    etapas = etapas_orden.con_opciones_pegadas(etapas, texto, "banda", acl)   # lentes: "... Pasa directo"
    de_historial = False
    if (correccion or prev) and not etapas["aplica"] and historial:
        etapas = etapas_orden.de_historial(historial, "banda", acl, posteriores=[texto])
        de_historial = etapas["aplica"]
    # Comportamientos que se pueden probar simulando el PLC (de toda la sesion).
    reqs = comportamiento.de_sesion(historial, texto, acl)
    if etapas["aplica"]:
        # En una secuencia el paro automatico y el conteo son etapas: los prueba la
        # simulacion de etapas (con sus tiempos y la senal anterior), no la prueba suelta.
        reqs = [r for r in reqs if r["tipo"] not in ("para_sola", "conteo", "sigue_al_contar")]
    mov = preguntas_banda.analizar(texto, prev, acl)

    intencion = {
        "texto": texto, "correccion": correccion,
        "movimiento": mov, "etapas": etapas, "etapas_de_historial": de_historial,
        "requisitos": reqs,
    }
    intencion["motor"] = _motor(texto, reqs, acl)
    if not intencion["motor"]["arranque"] and etapas.get("aplica") and etapas["etapas"]:
        # "Al presionar el pulsador I1, la banda se pone en marcha..." como 1a etapa: la secuencia arranca con I1.
        primera = etapas["etapas"][0].get("entra_con")
        if primera in ("I1", "I2"):
            intencion["motor"]["arranque"] = primera
    intencion["conteos"] = _conteos(texto, historial, etapas)
    intencion["secuencias"] = _secuencias(etapas)
    intencion["modulos"] = _modulos(intencion)
    intencion["faltan"] = _faltan(intencion, acl)
    intencion["conflictos"] = conflictos(intencion)
    return intencion


# ── Partes ──────────────────────────────────────────────────────────────────
def _motor(texto: str, reqs: list, acl: dict) -> dict:
    t = _fold(texto)
    arranque = next((r["senal"] for r in reqs if r["tipo"] == "arranca_con"), None)
    alternancia = next((r["senal"] for r in reqs if r["tipo"] == "toggle"), None)
    paros = [r["senal"] for r in reqs if r["tipo"] == "para_con"]
    sola = next(({k: v for k, v in r.items() if k in ("segundos", "senal", "conteo")}
                 for r in reqs if r["tipo"] == "para_sola"), None)
    hz = _RX_HZ.search(t)
    direccion = ("izquierda" if re.search(r"\bizquierda\b", t) else
                 "derecha" if re.search(r"\bderecha\b", t) else None)
    return {"arranque": arranque, "paro": paros, "alternancia": alternancia,
            "paro_solo": sola, "hz": int(hz.group(1)) if hz else None, "direccion": direccion,
            "aclarado": sorted(k for k in ("arranque", "direccion", "freq_hz", "paro", "paro_solo") if k in acl)}


def _conteos(texto: str, historial: list, etapas: dict) -> list:
    out = []
    for t in historial + [texto]:
        c = comportamiento.conteo_en(t)
        if c:
            out = [{"senal": c["senal"], "n": c["conteo"], "reinicia": c["reinicia"], "boton": c.get("boton"),
                    "en_secuencia": False, **({"boton_invalido": c["boton_invalido"]}
                                              if c.get("boton_invalido") else {})}]
    for g in etapas.get("grupos") or []:
        if g.get("conteo"):
            c = g["conteo"]
            out.append({"senal": c.get("senal"), "n": c.get("n"), "reinicia": bool(g.get("repite")),
                        "boton": None, "en_secuencia": True, "secuencia": g["n"]})
    if any(c["en_secuencia"] for c in out):
        out = [c for c in out if c["en_secuencia"]]      # el mismo conteo leido dos veces
    return out


def _secuencias(etapas: dict) -> list:
    if not etapas.get("aplica"):
        return []
    return [{"n": g["n"], "etapas": list(g["etapas"]), "repite": bool(g.get("repite")),
             "veces": g.get("veces"), "conteo": bool(g.get("conteo"))} for g in etapas.get("grupos") or []]


def _modulos(i: dict) -> list:
    m = i["motor"]
    mods = []
    if i["movimiento"].get("aplica") or any(m[k] for k in ("arranque", "paro", "alternancia", "paro_solo", "hz",
                                                           "direccion")):
        mods.append("motor")
    if i["conteos"]:
        mods.append("conteo")
    if i["secuencias"]:
        mods.append("secuencias")
    return mods


def _faltan(i: dict, acl: dict) -> list:
    """Preguntas pendientes, en el mismo orden en que el flujo las hace."""
    if comportamiento.paro_solo_sin_condicion(i["requisitos"], acl):
        return [comportamiento.pregunta_paro_solo()]
    mov = i["movimiento"]
    if mov.get("aplica") and mov.get("preguntas"):
        return list(mov["preguntas"])
    if i["etapas"]["aplica"]:
        return etapas_orden.preguntas(i["etapas"], "banda")
    return []


# ── Conflictos entre partes ─────────────────────────────────────────────────
def conflictos(i: dict) -> list:
    """Contradicciones que se pueden ver ANTES de generar. Por ahora solo se
    registran (analysis["intencion"]); no cambian lo que se genera."""
    out = []
    m = i["motor"]
    if m["arranque"] and m["arranque"] in m["paro"] and m["alternancia"] != m["arranque"]:
        out.append(f"{m['arranque']} arranca y detiene la banda sin pedir alternancia.")
    for c in i["conteos"]:
        if c.get("boton_invalido"):
            out.append(f"El contador no se puede reiniciar con {c['boton_invalido']} (paro de emergencia).")
        if c.get("boton") and c["boton"] in m["paro"]:
            out.append(f"{c['boton']} detiene la banda y tambien borra la cuenta.")
    if m["alternancia"] and i["secuencias"] and len(i["secuencias"]) >= 2:
        out.append("La alternancia necesita una secuencia y la instruccion ya usa las dos (SQ1 y SQ2).")
    return out


# ── Lo que recibe el modelo ─────────────────────────────────────────────────
def bloques_modelo(i: dict) -> list:
    """Mensajes de sistema con las recetas (los mismos que antes armaba app.py)."""
    msgs = []
    if i["etapas"]["aplica"]:
        msgs.append({"role": "system", "content": etapas_orden.bloque_modelo(i["etapas"], "banda")})
    if i["requisitos"]:
        msgs.append({"role": "system", "content": comportamiento.bloque_modelo(i["requisitos"])})
    return msgs


def resumen(i: dict) -> dict:
    """Version corta (sin textos largos) para analysis["intencion"] y el log."""
    return {"modulos": i["modulos"], "motor": {k: v for k, v in i["motor"].items() if v},
            "conteos": i["conteos"], "secuencias": i["secuencias"],
            "pruebas": [r["tipo"] + (f" {r['senal']}" if r.get("senal") else "") for r in i["requisitos"]],
            "faltan": [q.get("campo") or q.get("id") for q in i["faltan"]], "conflictos": i["conflictos"]}
