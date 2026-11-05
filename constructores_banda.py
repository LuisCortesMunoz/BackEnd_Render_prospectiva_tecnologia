"""
constructores_banda.py — armar la logica de la banda SIN el modelo (etapa 3).

Para las partes que se entienden sin ambiguedad se arman los bloques con
recetas probadas en el ST v7 (lavo_sim), en vez de pedirle al modelo el JSON
completo:

  Motor    arranque / paro con boton o sensor, alternancia con un boton,
           pausa N s, paro automatico a los N s, Hz y direccion
  Conteo   contador de S1/S2 con reinicio solo, con boton o ambos, y lo que
           pasa al llegar a N
  Torreta  luces con una senal (con duracion, hasta que otra la apague,
           parpadeando N s) o mientras la banda avanza / esta detenida
  Plumas   subir / bajar con una senal (con o sin duracion, con espera)

Todo se traduce a "eventos" del motor v7 (lavo_motor.expandir_eventos ya los
compila a timers / memorias / salidas y reparte los ids sin choques), mas el
contador, la secuencia de la alternancia y los timers propios.

REGLA CONSERVADORA: se lee frase por frase. Si en una frase sobra una palabra
que no se entiende, ESA frase no se arma (va al modelo como siempre). Solo si
TODAS las frases quedan entendidas se arma el programa completo; quien llama
lo valida y lo simula antes de entregarlo.

  leer(texto)                      -> {"frases": [...], "entendidas", "pendientes"}
  construir(texto, intencion, acl) -> {"resp": JSON del motor v7 (formato del modelo) | None,
                                       "pregunta": pregunta con opciones | None,
                                       "piezas": [fragmentos de las frases entendidas],
                                       "motivo": por que no se armo todo}
"""

import re

import aclaraciones as ac
import comportamiento as co
import preguntas_banda

_N = co._NUM_RX
_SEG = r"(?:s|seg|segs|segundos?|min|mins|minutos?)\b"
_SENSOR = r"(?:el |la )?(?:sensor ?(?:1|2|uno|dos)|s ?[12]|primer sensor|segundo sensor)"
_BOTON = r"(?:el |la )?(?:boton |pulsador )?(?:i ?[12]|boton verde|boton rojo)"
_LUZ = r"(verde|amarill[ao]s?|roj[ao]s?)"
_PLUMA = r"(?:la |las )?(?:pluma ?(?:1|2|uno|dos)|primera pluma|segunda pluma|plumas|ambas plumas|las dos plumas|dos plumas)"
_BANDA = r"(?:(?:la |el )?(?:banda|cinta|motor)(?: transportadora)? )?"

# Palabras que no cambian el significado. Las de ORDEN ("luego", "despues",
# "primero"...) NO estan: una frase con orden no se arma aqui (son etapas).
_RELLENO = {"y", "e", ",", "la", "el", "los", "las", "lo", "que", "se", "de", "del", "a", "en", "un", "una",
            "quiero", "haz", "hacer", "has", "tambien", "ademas", "entonces", "por", "favor", "programa",
            "logica", "transportadora", "esta", "este", "debe", "deberia", "necesito", "me", "gustaria",
            "inmediatamente", "automaticamente", "su", "sus", "le", "les", ":"}

_PIEZAS = [
    # alternancia: "al presionarlo por segunda vez", "con el mismo boton"
    ("toggle", re.compile(r"(?:y )?(?:al |si |cuando )?(?:lo |la )?(?:vuelv\w* a |volver a )?(?:presion|puls|oprim|apriet)"
                          r"\w*(?:lo|la)?(?: por| una)? (?:segunda vez|otra vez|de nuevo|nuevamente)|"
                          r"(?:y )?(?:al |si |cuando )?(?:lo |la )?(?:vuelv\w* a|volver a) (?:presion|puls|oprim|apret)\w*|"
                          r"(?:y )?con (?:el )?mismo (?:boton|pulsador)")),
    # reset del contador (la semantica la lee comportamiento.conteo_en)
    ("reset", re.compile(r"(?:y )?(?:que )?(?:el )?(?:contador|conteo|cuenta) (?:se )?(?:reinici|resete|reinici|borr|vuelv|pon)"
                         r"\w*(?: a| en)?(?: cero| 0)?(?: (?:solo|sola|solito|por si solo|automaticamente))?"
                         rf"(?: (?:y )?(?:tambien )?(?:con|al (?:presionar|pulsar|oprimir)) {_BOTON})?")),
    # disparos
    ("conteo", re.compile(rf"(?:cuando |al |una vez que |si )?(?:{_SENSOR} )?(?:cuente\w*|cuenta|acumul\w*|llegue a|"
                          rf"lleg\w* a|registr\w*|complet\w*) ({_N})(?: (?:cajas|piezas|objetos|detecciones|productos))?"
                          rf"(?: (?:en|del|que pasen por) {_SENSOR})?|"
                          rf"cada ({_N}) (?:cajas|piezas|objetos|detecciones|productos)(?: (?:en|del|que pasen por) {_SENSOR})?")),
    ("sensor", re.compile(rf"(?:cuando|al|en cuanto|si|cada vez que|una vez que) ({_SENSOR})(?: (?:detect\w*|se activ\w*|"
                          rf"vea|lea|sienta|registr\w*))?(?: (?:una?|la|el) (?:caja|pieza|objeto|producto))?|"
                          rf"al (?:detectar|activarse) ({_SENSOR})(?: (?:una?|la|el) (?:caja|pieza|objeto))?")),
    ("boton", re.compile(rf"(?:al (?:presionar|pulsar|oprimir)|cuando (?:se )?(?:presione|pulse|oprima)|presionando|"
                         rf"pulsando) ({_BOTON}|(?:el |la )?(?:boton )?(?:verde|rojo))|con ({_BOTON})")),
    # banda
    ("paro_solo", re.compile(rf"{_BANDA}(?:se )?(?:deten\w*|detien\w*|par[ae]\w*|frene) (?:sola|por si sola|automaticamente)"
                             rf"(?: (?:a los|despues de|tras|al cabo de) ({_N}) ?({_SEG})(?: de marcha)?)?")),
    ("mientras", re.compile(r"(?:mientras|cuando) (?:la )?(?:banda|cinta)(?: transportadora)? (?:(?:este|esta) )?"
                            r"(?:(en marcha|avanzando|avance|avanza|corra|se mueva|moviendose)|(detenida|parada|se detenga|"
                            r"no avance))")),
    ("marcha", re.compile(rf"{_BANDA}(?:se )?(?:avanz\w*|avanc\w*|arranc\w*|pone en marcha|ponga en marcha|comienz\w* a "
                          r"(?:avanzar|moverse|girar)|empiez\w* a (?:avanzar|moverse)|muev\w*|inicia|gira|corre)\b")),
    ("paro", re.compile(rf"{_BANDA}(?:se )?(?:detien\w*|deten\w*|para|pare|frena|frene)\b")),
    # torreta y plumas
    ("luz", re.compile(rf"(?:se )?(prend\w*|enciend\w*|encend\w*|activ\w*|ilumin\w*|apag\w*) (?:la |el )?(?:luz |lampara |"
                       rf"torreta )?{_LUZ}\b|(?:la )?(?:luz |lampara )?{_LUZ} (?:se )?(prende|enciende|apaga|activa)\b")),
    ("pluma", re.compile(rf"(?:se )?(sub\w*|levant\w*|elev\w*|baj\w*|desciend\w*|descend\w*|alz\w*) {_PLUMA}|"
                         rf"{_PLUMA} (?:se )?(sub\w*|levant\w*|elev\w*|baj\w*|desciend\w*|descend\w*|alz\w*)")),
    # modificadores
    ("parpadeo", re.compile(r"(?:parpade\w*|intermitente\w*|destell\w*)")),
    ("queda", re.compile(r"(?:y )?(?:se )?(?:queda|quede|mantiene|mantenga)\w*(?: encendid\w*| prendid\w*)?")),
    ("espera", re.compile(rf"(?:despues de|tras|luego de|pasados?|al cabo de) ({_N}) ?({_SEG})")),
    ("tiempo", re.compile(rf"(?:durante |por )?({_N}) ?({_SEG})")),
    ("hz", re.compile(rf"(?:a |con |en )?({_N}) ?(?:hz|hertz)\b")),
    ("dir", re.compile(r"(?:hacia |a |para )?la (derecha|izquierda)")),
]


def _segundos(n, unidad) -> float:
    v = co._numero(n)
    return v * 60 if str(unidad).startswith("min") else v


def _senal_txt(txt):
    t = re.sub(r"\b(?:el|la|boton|pulsador)\b", " ", txt or "")
    t = re.sub(r"\s+", " ", t).strip()
    return co._senal(t)


def _pluma(txt) -> list:
    if re.search(r"plumas|ambas|dos plumas", txt):
        return ["pluma1", "pluma2"]
    return ["pluma2" if re.search(r"\b(2|dos)\b|segunda", txt) else "pluma1"]


def _leer_frase(f: str) -> dict:
    """Escanea una frase (texto plegado) de izquierda a derecha."""
    piezas, sobra, pos = [], [], 0
    f = re.sub(r",", " , ", f)
    f = re.sub(r"\s+", " ", f).strip()
    while pos < len(f):
        if f[pos] == " ":
            pos += 1
            continue
        for tipo, rx in _PIEZAS:
            m = rx.match(f, pos)
            if m and m.end() > pos and (m.end() == len(f) or not f[m.end()].isalnum()):
                piezas.append((tipo, m))
                pos = m.end()
                break
        else:
            fin = f.find(" ", pos)
            fin = len(f) if fin < 0 else fin
            palabra = f[pos:fin]
            if palabra not in _RELLENO:
                sobra.append(palabra)
            pos = fin
    return {"texto": f, "piezas": piezas, "sobra": sobra}


def leer(texto: str) -> dict:
    frases = [_leer_frase(o) for o in re.split(r"[.;\n]+", co._fold(texto)) if o.strip()]
    return {"frases": frases, "entendidas": [f for f in frases if not f["sobra"] and f["piezas"]],
            "pendientes": [f for f in frases if f["sobra"] or not f["piezas"]]}


# ── De piezas a bloques ─────────────────────────────────────────────────────
class _NoSeArma(Exception):
    pass


def _grupos(frase) -> list:
    """[{disparo, acciones[], ...}] : cada disparo con las acciones que le siguen
    (o que lo preceden en la misma frase: 'prende la roja cuando S1 detecte')."""
    grupos, actual, pendientes = [], None, []
    for tipo, m in frase["piezas"]:
        if tipo in ("sensor", "boton", "conteo"):
            actual = {"disparo": (tipo, m), "acciones": [], "espera": 0.0, "toggle": False}
            if pendientes:
                actual["acciones"] += pendientes
                pendientes = []
            grupos.append(actual)
        elif tipo == "toggle":
            if actual is None:
                raise _NoSeArma("alternancia sin boton")
            # marca en su lugar: lo que sigue ("se detiene") es el 2o pulso del mismo boton
            actual["acciones"].append({"tipo": "toggle"})
        elif tipo == "espera":
            if actual is None:
                raise _NoSeArma("espera sin disparo")
            actual["espera"] = _segundos(m.group(1), m.group(2))
        elif tipo in ("tiempo", "parpadeo", "queda"):
            destino = (actual["acciones"] if actual and actual["acciones"] else pendientes)
            if not destino:
                raise _NoSeArma(f"'{m.group(0)}' sin accion")
            destino[-1].setdefault("mods", []).append((tipo, m))
        elif tipo in ("reset", "hz", "dir", "paro_solo", "mientras"):
            grupos.append({"disparo": (tipo, m), "acciones": [], "espera": 0.0, "toggle": False})
            if tipo == "mientras" and pendientes:
                grupos[-1]["acciones"] += pendientes
                pendientes = []
            elif tipo == "mientras" and actual is not None and actual["acciones"]:
                raise _NoSeArma("'mientras' despues de otro disparo")
        else:                                         # marcha, paro, luz, pluma
            accion = {"tipo": tipo, "m": m}
            (actual["acciones"] if actual is not None else pendientes).append(accion)
    if pendientes:
        # "la banda avanza a 30 Hz" sin disparo: no se adivina con que arranca
        raise _NoSeArma("acciones sin disparo")
    return grupos


def _luz(accion) -> tuple:
    """(verbo, recurso) de una pieza 'luz' (sus dos formas de frase)."""
    m = accion["m"]
    verbo = m.group(1) or m.group(4) or ""
    color = co._fold(m.group(2) or m.group(3) or "")
    rec = "verde" if color.startswith("verd") else "amarilla" if color.startswith("amar") else "roja"
    return verbo, rec


_Q_LUZ = {"verde": "Q3", "amarilla": "Q4", "roja": "Q5"}


def _dur(accion):
    for tipo, m in accion.get("mods", []):
        if tipo == "tiempo":
            return _segundos(m.group(1), m.group(2))
    return None


def _tiene(accion, mod):
    return any(t == mod for t, _ in accion.get("mods", []))


def construir(texto: str, intencion: dict, acl: dict = None) -> dict:
    acl = acl or {}
    out = {"resp": None, "pregunta": None, "piezas": [], "motivo": ""}
    lectura = leer(texto)
    if not lectura["frases"]:
        out["motivo"] = "sin texto"
        return out
    if intencion["etapas"]["aplica"]:
        out["motivo"] = "acciones en orden (secuencia): las arma el modelo"
        return out
    if lectura["pendientes"]:
        out["motivo"] = "frases no entendidas: " + " | ".join(
            f"{f['texto'][:50]} (sobra: {', '.join(f['sobra'][:4])})" for f in lectura["pendientes"])
    try:
        partes = [(f, _grupos(f)) for f in lectura["entendidas"]]
    except _NoSeArma as e:
        out["motivo"] = (out["motivo"] + "; " if out["motivo"] else "") + str(e)
        return out
    try:
        resp = _armar(texto, [g for _, gs in partes for g in gs], acl)
    except _NoSeArma as e:
        out["motivo"] = (out["motivo"] + "; " if out["motivo"] else "") + str(e)
        return out
    out["piezas"] = [{k: v for k, v in resp.items() if k not in ("name", "analisis", "preguntas", "_pregunta") and v}]
    if not lectura["pendientes"]:
        if resp.get("_pregunta"):
            # falta un dato (los Hz): se pregunta con opciones y se arma con la respuesta
            out["pregunta"] = resp.pop("_pregunta")
        else:
            out["resp"] = resp
    return out


def _armar(texto: str, grupos: list, acl: dict) -> dict:
    eventos, salidas, timers, contadores, secuencias = [], {}, [], [], []
    hz = dirc = None
    mueve = False
    toggle = None
    arranques, paros = [], []
    conteo = co.conteo_en(texto)
    hay_apagar = {_luz(a)[1] for g in grupos for a in g["acciones"]
                  if a["tipo"] == "luz" and re.match(r"apag", _luz(a)[0])}

    def nuevo_timer(**kw) -> str:
        tid = f"T{len(timers) + 1}"
        timers.append({"id": tid, "reset": None, **kw})
        return tid

    def disparo_de(g):
        tipo, m = g["disparo"]
        if tipo == "boton":
            s = _senal_txt(m.group(1) or m.group(2))
            if s not in ("I1", "I2"):
                raise _NoSeArma(f"boton no reconocido: {m.group(0)}")
            return s
        if tipo == "sensor":
            s = _senal_txt(m.group(1) or m.group(2))
            if s not in ("S1", "S2"):
                raise _NoSeArma(f"sensor no reconocido: {m.group(0)}")
            return s
        if tipo == "conteo":
            if not conteo:
                raise _NoSeArma("conteo sin sensor claro")
            if not contadores:
                c = {"id": "CT1", "entrada": conteo["senal"], "preset": conteo["conteo"]}
                if conteo["reinicia"]:
                    c.update(modo="ciclico", reset=conteo.get("boton"))
                elif conteo.get("boton"):
                    c.update(modo="una_vez", reset=conteo["boton"])
                else:
                    c.update(modo="una_vez", reset=None)       # se ajusta al arranque mas abajo
                contadores.append(c)
            return "CT1"
        raise _NoSeArma(f"disparo '{tipo}'")

    for g in grupos:
        tipo, m = g["disparo"]
        if tipo == "hz":
            hz = co._numero(m.group(1))
            continue
        if tipo == "dir":
            dirc = m.group(1)
            continue
        if tipo == "reset":
            if not conteo:
                raise _NoSeArma("reset de contador sin contador")
            continue
        if tipo == "paro_solo":
            if not m.group(1):
                raise _NoSeArma("paro automatico sin tiempo")
            tid = nuevo_timer(tipo="TON", entrada="BANDA_CORRIENDO", segundos=_segundos(m.group(1), m.group(2)))
            eventos.append({"cuando": tid, "acciones": {"banda": "detener"}})
            continue
        if tipo == "mientras":
            cond = "BANDA_CORRIENDO" if m.group(1) else "BANDA_DETENIDA"
            for a in g["acciones"]:
                if a["tipo"] != "luz" or re.match(r"apag", _luz(a)[0]):
                    raise _NoSeArma("'mientras' solo con encender luces")
                q = _Q_LUZ[_luz(a)[1]]
                if _tiene(a, "parpadeo"):
                    salidas[q] = nuevo_timer(tipo="INTERMITENTE", entrada=cond, segundos=0.5, segundos_off=0.5)
                else:
                    salidas[q] = cond
            continue
        senal = disparo_de(g)
        en_toggle = False
        for a in g["acciones"]:
            if a["tipo"] == "toggle":
                en_toggle = True
                continue
            d = _dur(a)
            ev = {"cuando": senal, "espera_s": g["espera"] or 0, "duracion_s": d}
            if a["tipo"] == "marcha":
                mueve = True
                if en_toggle:
                    raise _NoSeArma("despues de 'otra vez' se esperaba que se detenga")
                arranques.append(senal)
                ev["acciones"] = {"banda": "avanzar"}
            elif a["tipo"] == "paro":
                if en_toggle:
                    if senal not in ("I1", "I2"):
                        raise _NoSeArma("alternancia con algo que no es un boton")
                    toggle = senal
                    continue
                paros.append(senal)
                ev["acciones"] = {"banda": "pausar" if d else "detener"}
            elif a["tipo"] == "luz":
                verbo, rec = _luz(a)
                if re.match(r"apag", verbo):
                    ev["acciones"] = {rec: "apagar"}
                else:
                    if _tiene(a, "parpadeo"):
                        if not d:
                            raise _NoSeArma("parpadeo sin duracion")
                        pulso = nuevo_timer(tipo="PULSO", entrada=senal, segundos=d)
                        salidas[_Q_LUZ[rec]] = nuevo_timer(tipo="INTERMITENTE", entrada=pulso, segundos=0.5,
                                                           segundos_off=0.5)
                        continue
                    if not d and not _tiene(a, "queda") and rec not in hay_apagar \
                            and not (senal.startswith("CT") and contadores and contadores[0]["modo"] == "una_vez"
                                     and not contadores[0].get("reset")):
                        # "con I1 prende la verde": ¿mientras se presiona o se queda? no se adivina
                        raise _NoSeArma(f"luz {rec} sin duracion ni quien la apague")
                    ev["acciones"] = {rec: "encender"}
            elif a["tipo"] == "pluma":
                verbo = a["m"].group(1) or a["m"].group(2) or ""
                txt = a["m"].group(0)
                estado = "bajar" if re.match(r"baj|desc", verbo) else "subir"
                ev["acciones"] = {p: estado for p in _pluma(txt)}
            else:
                raise _NoSeArma(f"accion '{a['tipo']}'")
            eventos.append({k: v for k, v in ev.items() if v not in (None, 0, 0.0) or k == "acciones"})

    if toggle:
        mueve = True
        arranques = [a for a in arranques if a != toggle]
        eventos = [e for e in eventos if not (e["cuando"] == toggle and e["acciones"] == {"banda": "avanzar"})]
        if len(secuencias) >= 2:
            raise _NoSeArma("sin secuencia libre para la alternancia")
        sq = {"id": "SQ1", "inicio": toggle, "modo": "una_vez",
              "pasos": [{"motor": ["marcha"], "avanza": "flanco", "senal": toggle}]}
        otros_paros = [p for p in paros if p != toggle]
        if otros_paros:
            sq["paro"] = otros_paros[0]
            eventos = [e for e in eventos if e["acciones"] != {"banda": "detener"}]
        secuencias.append(sq)
    if contadores and contadores[0]["modo"] == "una_vez" and not contadores[0].get("reset") and arranques:
        contadores[0]["reset"] = arranques[0]           # al arrancar de nuevo cuenta desde cero
    if contadores and conteo.get("reinicia") and not conteo.get("boton"):
        contadores[0].pop("reset", None)
    if not (eventos or salidas or secuencias):
        raise _NoSeArma("no hay acciones")
    if mueve and not (arranques or toggle):
        raise _NoSeArma("la banda se mueve pero no se sabe con que arranca")

    resp = {"name": _nombre(arranques, toggle, contadores, eventos, salidas), "analisis": {
        "intencion": texto[:200], "compatibilidad": "total", "conservado": [], "modificado": [],
        "simplificado": [], "no_soportado": [], "requiere_confirmacion": False}, "preguntas": []}
    if mueve:
        f = ac.frecuencia(acl) if acl else None
        hz = hz or f
        if not hz:
            resp["_pregunta"] = preguntas_banda.pregunta("freq_hz")
        resp["banda"] = {"mover": True, "direccion": ac.direccion(acl) or dirc or "derecha",
                         "frecuencia_hz": hz}
    else:
        resp["banda"] = {"mover": False}
    for clave, valor in (("timers", timers), ("contadores", contadores), ("eventos", eventos),
                         ("salidas", salidas), ("secuencias", secuencias)):
        if valor:
            resp[clave] = valor
    return resp


def _nombre(arranques, toggle, contadores, eventos, salidas) -> str:
    partes = []
    if toggle:
        partes.append(f"Alternancia con {toggle}")
    elif arranques:
        partes.append(f"Banda con {arranques[0]}")
    if contadores:
        partes.append(f"conteo {contadores[0]['preset']} en {contadores[0]['entrada']}")
    luces = sorted({r for e in eventos for r in e["acciones"] if r in ("verde", "amarilla", "roja")}
                   | {q for q in salidas})
    if luces:
        partes.append("luces " + ", ".join(luces))
    if any(r.startswith("pluma") for e in eventos for r in e["acciones"]):
        partes.append("plumas")
    return " · ".join(partes)[:60] or "Programa banda"


def bloque_piezas(piezas: list) -> str:
    """Para el modelo, cuando solo una parte de la instruccion se entendio."""
    import json
    return ("PIEZAS YA ARMADAS Y PROBADAS (de las frases que el sistema entendio): incluyelas TAL CUAL en tu JSON "
            "(mismos bloques y senales) y arma solo lo demas de la instruccion, sin repetir sus salidas ni sus "
            "ids:\n" + json.dumps(piezas, ensure_ascii=False, separators=(",", ":")))
