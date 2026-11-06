"""
reparaciones_banda.py — ajustes deterministas (sin IA) a la respuesta del modelo
para la banda v7, para choques conocidos entre el bloque motor, las secuencias
y los contadores (comprobados en el ST real con lavo_sim / el banco de pruebas):

1. CONTADOR TRABADO: una secuencia que ARRANCA con CTn y un CTn con
   reset = SQk_FIN. SQk_FIN queda activo al terminar (hasta que la secuencia
   vuelva a arrancar), asi que el contador se queda en 0 para siempre y la
   secuencia ya no vuelve a arrancar. Se usa SQk_CICLO (pulso al terminar).

2. LA BANDA NO ARRANCA: el texto pide que la banda avance (y no dice con que
   arranca), pero nada la pone en marcha antes del contador/sensor (solo una
   secuencia que arranca con CTn, Tn, SQ...). Se aplica la regla de siempre:
   arranca con I1 (motor.arranque = I1, enclavado).

3. VUELVE A AVANZAR SOLA: el bloque motor arranca con un boton (enclavado) y
   una secuencia que mueve la banda termina con la banda DETENIDA. Al terminar
   la secuencia el motor vuelve a seguir al boton y la banda arranca sola (en
   la direccion que dejo la secuencia). Se agrega SQk_CICLO al paro del motor:
   al terminar se queda detenida hasta un nuevo arranque.

4. LUZ FIJA QUE DEBIA PARPADEAR: se pidio que una luz parpadee y quedo fija en
   pasos de una secuencia (o en 'salidas'). Se quita de esos pasos y se maneja
   con un timer INTERMITENTE cuya entrada es ese paso (o esa regla).

aplicar(respuesta, texto, textos) -> (respuesta_reparada, [avisos])
Nunca quita nada de lo pedido; si no aplica, devuelve la respuesta igual.
"""

import copy
import re
import unicodedata

_SEN_INTERNA = re.compile(r"^(?:NOT\s+)?(?:CT\d+|T\d+|SQ\d+(?:_\w+)?|C\d+|M\d+)$", re.I)
_RX_AVANZA = re.compile(r"\b(avanc\w*|avanz\w*|se mueva|muev\w*|ponga en marcha|pone en marcha|arranqu\w*|"
                        r"arranc\w*|marcha)\b")
# Lo ULTIMO que el texto pide del motor: detenerse o seguir ("se detenga 2 s y luego
# siga" es una pausa: al terminar la secuencia la banda debe seguir).
_RX_PARA = re.compile(r"\b(?:deten\w*|detien\w*|deteng\w*|pare|paren|queda\w* detenid\w*|quede\w* detenid\w*|"
                      r"se para|y para|luego para|despues para)\b")
_RX_SIGUE = re.compile(r"\b(?:continu\w*|sigue|siga|sigan|reanud\w*|retom\w*|vuelv\w* a avanzar|"
                       r"vuelv\w* a moverse)\b")
_RX_ARRANQUE_DICHO = re.compile(r"\b(i ?[12]|boton|pulsador|al presionar|al pulsar|al encender|siempre (?:en marcha|avanzando|encendida|activa)|(?:avance|avanza|corra|corre|gire|gira|funcione|este en marcha) siempre|"
                                r"automaticamente al (?:iniciar|cargar)|en cuanto se cargue)\b")


def _fold(t) -> str:
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _s(x) -> str:
    return str(x or "").strip().upper().replace(" ", "")


def _lista(x):
    return x if isinstance(x, list) else []


def _mueve(seq: dict) -> bool:
    return any("marcha" in [str(a).lower() for a in _lista((p or {}).get("motor"))] or
               "invertir" in [str(a).lower() for a in _lista((p or {}).get("motor"))]
               for p in _lista(seq.get("pasos")))


def _termina_detenida(seq: dict) -> bool:
    pasos = [p for p in _lista(seq.get("pasos")) if isinstance(p, dict)]
    if not pasos:
        return False
    ultimo = pasos[-1]
    mot = [str(a).lower() for a in _lista(ultimo.get("motor"))]
    return "marcha" not in mot


def _repite(seq: dict) -> bool:
    return str(seq.get("modo") or "una_vez").lower() not in ("una_vez", "")


def _reset_contador(r: dict, avisos: list):
    seqs = [s for s in _lista(r.get("secuencias")) if isinstance(s, dict)]
    for c in _lista(r.get("contadores")):
        if not isinstance(c, dict):
            continue
        rst = _s(c.get("reset"))
        m = re.fullmatch(r"(SQ\d+)_FIN", rst)
        if not m:
            continue
        sid = m.group(1)
        if any(_s(s.get("id")) == sid and _s(s.get("inicio")) == _s(c.get("id")) for s in seqs):
            c["reset"] = f"{sid}_CICLO"
            avisos.append(f"{c.get('id')} se reinicia con {sid}_CICLO (al terminar {sid}): con {sid}_FIN se quedaba "
                          f"en cero y la secuencia ya no volvia a arrancar.")


def _arranque_por_defecto(r: dict, texto: str, avisos: list):
    banda = r.get("banda") or {}
    if banda.get("mover") is False:
        return
    t = _fold(texto)
    if not _RX_AVANZA.search(t) or _RX_ARRANQUE_DICHO.search(t):
        return
    motor = r.get("motor") if isinstance(r.get("motor"), dict) else {}
    if str(motor.get("arranque") or "").strip():
        return
    if any(isinstance(e, dict) and "banda" in (e.get("acciones") or {}) for e in _lista(r.get("eventos"))):
        return
    seqs = [s for s in _lista(r.get("secuencias")) if isinstance(s, dict)]
    # Alguna secuencia que mueve la banda arranca con una entrada fisica: ya hay arranque.
    if any(_mueve(s) and not _SEN_INTERNA.match(_s(s.get("inicio"))) and _s(s.get("inicio")) for s in seqs):
        return
    motor["arranque"] = "I1"
    motor.setdefault("modo", "enclavado")
    r["motor"] = motor
    avisos.append("La banda arranca con I1 (no se indico con qué arranca y nada la ponía en marcha antes de "
                  "contar o detectar).")


def _ultimo_es_paro(texto: str) -> bool:
    t = _fold(texto)
    p = [m.start() for m in _RX_PARA.finditer(t)]
    c = [m.start() for m in _RX_SIGUE.finditer(t)]
    return bool(p) and (not c or p[-1] > c[-1])


def _paro_al_terminar(r: dict, texto: str, avisos: list):
    if not _ultimo_es_paro(texto):
        return
    motor = r.get("motor") if isinstance(r.get("motor"), dict) else None
    if not motor or not str(motor.get("arranque") or "").strip():
        return
    if str(motor.get("modo") or "enclavado").lower() not in ("enclavado", ""):
        return
    arr = _s(motor.get("arranque"))
    for s in [s for s in _lista(r.get("secuencias")) if isinstance(s, dict)]:
        sid = _s(s.get("id"))
        if not sid or not _mueve(s) or not _termina_detenida(s) or _repite(s) or arr.startswith(sid + "_"):
            continue
        ciclo = f"{sid}_CICLO"
        paro = str(motor.get("paro") or "").strip()
        conds = r.setdefault("condiciones", []) if isinstance(r.get("condiciones"), list) else None
        if conds is None:
            r["condiciones"] = conds = []
        if not paro:
            motor["paro"] = ciclo
        else:
            # ya incluido (directo o dentro de una condicion OR)
            if _s(paro) == ciclo:
                continue
            cond = next((c for c in conds if isinstance(c, dict) and _s(c.get("id")) == _s(paro)), None)
            if cond is not None and str(cond.get("op") or "").upper() == "OR":
                if ciclo not in [_s(x) for x in _lista(cond.get("entradas"))]:
                    cond["entradas"] = _lista(cond.get("entradas")) + [ciclo]
            else:
                usados = {int(m.group(1)) for c in conds if isinstance(c, dict)
                          for m in [re.fullmatch(r"C(\d+)", _s(c.get("id")))] if m}
                n = next((k for k in range(1, 17) if k not in usados), None)
                if n is None:
                    continue
                conds.append({"id": f"C{n}", "op": "OR", "entradas": [paro, ciclo], "flanco": "nivel"})
                motor["paro"] = f"C{n}"
        avisos.append(f"Al terminar {sid} la banda se queda detenida (paro con {ciclo}): antes el motor volvía a "
                      f"seguir a {motor.get('arranque')} y avanzaba sola.")


def _parpadeo(r: dict, textos: list, avisos: list):
    import comportamiento
    pedidos = [x for x in comportamiento.requisitos(textos) if x["tipo"] == "parpadea"]
    timers = [t for t in _lista(r.get("timers")) if isinstance(t, dict)]
    salidas = r.get("salidas") if isinstance(r.get("salidas"), dict) else {}
    for p in pedidos:
        q = p["q"]
        actual = _s(salidas.get(q))
        existente = next((t for t in timers if _s(t.get("id")) == actual
                          and str(t.get("tipo") or "").upper().startswith("INTERMIT")), None)
        pasos = []
        for s in [s for s in _lista(r.get("secuencias")) if isinstance(s, dict)]:
            for i, paso in enumerate(_lista(s.get("pasos")), 1):
                if isinstance(paso, dict) and q in [_s(x) for x in _lista(paso.get("salidas"))]:
                    pasos.append((paso, f"{_s(s.get('id'))}_PASO{i}"))
        if existente is not None and not pasos:
            continue                                # ya parpadea y nada la deja fija
        if pasos:
            for paso, _ in pasos:
                paso["salidas"] = [x for x in _lista(paso.get("salidas")) if _s(x) != q]
            entradas = [sen for _, sen in pasos]
        elif actual:
            entradas = [actual]
        else:
            continue
        usados_t = {int(m.group(1)) for t in timers for m in [re.fullmatch(r"T(\d+)", _s(t.get("id")))] if m}
        nt = int(actual[1:]) if existente is not None else next((k for k in range(1, 17) if k not in usados_t), None)
        if nt is None:
            continue
        entrada = entradas[0]
        if len(entradas) > 1:
            conds = r.setdefault("condiciones", []) if isinstance(r.get("condiciones"), list) else None
            if conds is None:
                r["condiciones"] = conds = []
            usados_c = {int(m.group(1)) for c in conds if isinstance(c, dict)
                        for m in [re.fullmatch(r"C(\d+)", _s(c.get("id")))] if m}
            nc = next((k for k in range(1, 17) if k not in usados_c), None)
            if nc is None:
                continue
            conds.append({"id": f"C{nc}", "op": "OR", "entradas": entradas, "flanco": "nivel"})
            entrada = f"C{nc}"
        if existente is not None:
            existente["entrada"] = entrada          # el timer del modelo, con la entrada que dura lo pedido
            existente["reset"] = None               # la entrada ya marca cuanto dura (un reset lo apagaba)
        else:
            timers.append({"id": f"T{nt}", "tipo": "INTERMITENTE", "entrada": entrada, "segundos": 0.5,
                           "segundos_off": 0.5})
        r["timers"] = timers
        salidas[q] = f"T{nt}"
        r["salidas"] = salidas
        avisos.append(f"{q} parpadea (timer T{nt} intermitente mientras {entrada}): estaba fija.")


def aplicar(respuesta: dict, texto: str = "", textos: list = None) -> tuple:
    if not isinstance(respuesta, dict):
        return respuesta, []
    r = copy.deepcopy(respuesta)
    avisos = []
    try:
        _parpadeo(r, list(textos) if textos else [texto], avisos)
        _reset_contador(r, avisos)
        _arranque_por_defecto(r, texto, avisos)
        _paro_al_terminar(r, texto, avisos)
    except (TypeError, ValueError, AttributeError):
        return respuesta, []
    return (r, avisos) if avisos else (respuesta, [])
