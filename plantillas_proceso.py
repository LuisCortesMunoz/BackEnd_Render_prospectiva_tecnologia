"""
============================================================================
PLANTILLAS DE PROCESO DE LA BANDA (motor v7) — solo instrucciones GENERALES
============================================================================
Cuando la instruccion no dicta la secuencia ("simula una linea de
empaquetado"), el modelo ya no arma timers, contadores y secuencias a mano:
elige una plantilla y sus parametros

    "proceso": {"id": "empaquetado", "parametros": {"piezas": 4}}

y este modulo la expande, de forma determinista, a bloques del motor v7.
Cada plantilla se arma con pocos MODULOS probados en el simulador del ST
(test_lavo_v7.py, escenario 24):

  estacion    un sensor detiene la banda N s (operacion simulada), con una
              pluma opcional que baja al empezar y sube al terminar.
  lote        un sensor cuenta piezas; cada N detiene la banda N s (o hasta
              que el operador presione I1), con una pluma opcional.
  desvio      un sensor cuenta piezas; cada N la pluma baja, deja pasar la
              pieza y sube (la banda no se detiene).
  acumulacion el sensor del final ocupado mas de N s pausa la banda hasta
              que se libere.

Y SIEMPRE la capa de OPERACION ROBUSTA (para todo proceso general):
  - I1 arranca e I2 detiene (o lo que el usuario haya dicho);
  - supervision de atasco: S1 o S2 ocupado mas de atasco_s con la banda en
    marcha -> falla: la banda se detiene y la roja enciende; I1 la reconoce;
  - el paro y la falla mandan sobre cualquier etapa automatica;
  - torreta estandar: verde = en marcha, amarilla = detenida sin falla
    (lista o estacion trabajando), roja = falla (y con el paro de emergencia).

Los procesos del catalogo (procesos_industriales/, nivel 3) siguen siendo
la referencia investigada; aqui solo esta COMO se simulan en esta banda.
============================================================================
"""

import copy
import re
import unicodedata

import lavo_motor as lm

BAJA = {"pluma1": "Q9", "pluma2": "Q7"}
SUBE = {"pluma1": "Q8", "pluma2": "Q6"}
SENSORES = ("S1", "S2")
COMUNES = {"atasco_s": 10}
PLUMA_S = 2


# ---------------------------------------------------------------------------
# Modulos
# ---------------------------------------------------------------------------
def _secuencia(eng, inicio, pasos):
    sq = lm._siguiente_id(eng, "secuencias", "SQ", lm.N_SEQ)
    sq.update(inicio=inicio, modo="una_vez", exclusiva=True, pasos=pasos)
    return sq


def _con_pluma(pasos, pluma, pluma_s, motor):
    """Envuelve 'pasos' con la pluma bajando antes y subiendo despues."""
    if not pluma:
        return pasos
    m = {"motor": motor} if motor else {}
    return ([dict(m, salidas=[BAJA[pluma]], avanza="tiempo", segundos=pluma_s)] + pasos
            + [dict(m, salidas=[SUBE[pluma]], avanza="tiempo", segundos=pluma_s)])


def estacion(eng, sensor, tiempo_s, pluma=None, pluma_s=PLUMA_S):
    pasos = [{"motor": ["paro"], "avanza": "tiempo", "segundos": tiempo_s}]
    _secuencia(eng, sensor, _con_pluma(pasos, pluma, pluma_s, ["paro"]))


def _contador(eng, sensor, n):
    if n == 1:
        return sensor
    ct = lm._siguiente_id(eng, "contadores", "CT", lm.N_COUNT)
    ct.update(entrada=sensor, preset=n, modo="ciclico")
    return ct["id"]


def lote(eng, sensor, piezas, pausa_s, continuar="tiempo", pluma=None, pluma_s=PLUMA_S):
    if continuar == "boton":
        pasos = [{"motor": ["paro"], "avanza": "flanco", "senal": "I1"}]
    else:
        pasos = [{"motor": ["paro"], "avanza": "tiempo", "segundos": pausa_s}]
    _secuencia(eng, _contador(eng, sensor, piezas), _con_pluma(pasos, pluma, pluma_s, ["paro"]))


def desvio(eng, sensor, cada, pluma, paso_s, pluma_s=PLUMA_S):
    pasos = [{"avanza": "tiempo", "segundos": paso_s}]
    _secuencia(eng, _contador(eng, sensor, cada), _con_pluma(pasos, pluma, pluma_s, None))


def acumulacion(eng, sensor, espera_s):
    t = lm._siguiente_id(eng, "timers", "T", lm.N_TIMER)
    t.update(tipo="TON", entrada=sensor, segundos=espera_s)
    motor = eng.setdefault("motor", {})
    motor["pausa"] = lm._o_logico(eng, [x for x in (motor.get("pausa"), t["id"]) if x])


# ---------------------------------------------------------------------------
# Plantillas: parametros por defecto (los que el modelo puede cambiar) y como
# se arman. "catalogo" = proceso de referencia de procesos_industriales/.
# ---------------------------------------------------------------------------
def _p_estacion(p):
    return [("estacion", p["sensor"], p["tiempo_s"], p["pluma"])]


PLANTILLAS = {
    "transporte": {
        "nombre": "transporte continuo supervisado", "catalogo": "transporte",
        "parametros": {},
        "modulos": lambda p: [],
        "sinonimos": ["transporte", "transportar", "trasladar", "traslado", "mover cajas", "mueve cajas", "mover piezas", "mueve piezas", "mueve productos",
                      "mover productos", "conveyor", "banda continua", "trabaje sola"]},
    "estacion_trabajo": {
        "nombre": "estacion de trabajo (S1 detiene la banda tiempo_s mientras se opera la pieza)",
        "catalogo": None,
        "parametros": {"sensor": "S1", "tiempo_s": 4, "pluma": None},
        "modulos": _p_estacion,
        "sinonimos": ["estacion de trabajo", "sellado", "sellar", "sella", "pintura", "pintar", "pintado",
                      "taladrado", "taladrar", "perforar", "soldadura", "soldar", "horneado", "hornear",
                      "corte", "cortar", "pulido", "pulir", "lavado", "lavar", "secado", "secar",
                      "grabado", "grabar", "prensado", "prensar", "estampado", "estampar", "atornillar",
                      "maquinado", "procesar piezas", "operacion"]},
    "llenado": {
        "nombre": "estacion de llenado (S1 detiene, la pluma simula la boquilla)", "catalogo": "llenado",
        "parametros": {"sensor": "S1", "tiempo_s": 5, "pluma": "pluma1"},
        "modulos": _p_estacion,
        "sinonimos": ["llenado", "llenar", "llena", "llenadora", "dosificar", "dosificado", "dosificacion",
                      "envasado", "envasar", "filling"]},
    "etiquetado": {
        "nombre": "estacion de etiquetado (S1 detiene la banda tiempo_s)", "catalogo": "etiquetado",
        "parametros": {"sensor": "S1", "tiempo_s": 2, "pluma": None},
        "modulos": _p_estacion,
        "sinonimos": ["etiquetado", "etiquetar", "etiqueta", "etiquetadora", "rotulado", "rotular",
                      "marcado", "labeling"]},
    "ensamble": {
        "nombre": "estacion de ensamble indexada (S1 detiene, la pluma hace de tope)", "catalogo": "ensamble",
        "parametros": {"sensor": "S1", "tiempo_s": 8, "pluma": "pluma1"},
        "modulos": _p_estacion,
        "sinonimos": ["ensamble", "ensamblar", "ensamblaje", "ensamblado", "montaje", "montar", "assembly",
                      "indexado", "indexada", "avance por pasos"]},
    "inspeccion": {
        "nombre": "inspeccion / control de calidad (S1 detiene tiempo_s para revisar la pieza)",
        "catalogo": None,
        "parametros": {"sensor": "S1", "tiempo_s": 3, "pluma": None},
        "modulos": _p_estacion,
        "sinonimos": ["inspeccion", "inspeccionar", "control de calidad", "calidad", "revisar piezas",
                      "verificacion", "verificar piezas"]},
    "embotellado": {
        "nombre": "embotellado: llenado en S1 (pluma1) y taponado en S2 (pluma2)", "catalogo": "embotellado",
        "parametros": {"tiempo_llenado_s": 4, "tiempo_tapado_s": 2},
        "modulos": lambda p: [("estacion", "S1", p["tiempo_llenado_s"], "pluma1"),
                              ("estacion", "S2", p["tiempo_tapado_s"], "pluma2")],
        "sinonimos": ["embotellado", "embotellar", "embotelladora", "botella", "botellas", "bottling",
                      "refresco", "refrescos", "envasado de bebidas", "linea de bebidas"]},
    "empaquetado": {
        "nombre": "empaquetado: S1 cuenta piezas; cada 'piezas' la banda para pausa_s y la pluma deposita el lote",
        "catalogo": "empaquetado",
        "parametros": {"sensor": "S1", "piezas": 6, "pausa_s": 3, "pluma": "pluma1"},
        "modulos": lambda p: [("lote", p["sensor"], p["piezas"], p["pausa_s"], "tiempo", p["pluma"])],
        "sinonimos": ["empaquetado", "empaquetar", "empaqueta", "empaquetadora", "empacado", "empacar",
                      "empaca", "empaque", "encajado", "encajar", "encajadora", "case packer",
                      "encartonado", "cartoning", "linea de empaque"]},
    "conteo_agrupacion": {
        "nombre": "conteo por lotes: S1 cuenta; cada 'piezas' la banda para pausa_s", "catalogo": "conteo_agrupacion",
        "parametros": {"sensor": "S1", "piezas": 10, "pausa_s": 3},
        "modulos": lambda p: [("lote", p["sensor"], p["piezas"], p["pausa_s"], "tiempo", None)],
        "sinonimos": ["conteo", "contar productos", "agrupacion", "agrupar", "lote", "lotes", "loteado",
                      "batching", "dosificar por cantidad"]},
    "paletizado": {
        "nombre": "paletizado: S1 cuenta cajas; con 'piezas' la banda para hasta que el operador presiona I1",
        "catalogo": None,
        "parametros": {"sensor": "S1", "piezas": 12},
        "modulos": lambda p: [("lote", p["sensor"], p["piezas"], 0, "boton", None)],
        "sinonimos": ["paletizado", "paletizar", "paletizadora", "pallet", "pallets", "palet", "tarima",
                      "tarimas", "estibado", "estibar"]},
    "clasificacion": {
        "nombre": "clasificacion por alternancia: cada 'cada' piezas en S1 la pluma desvia la pieza",
        "catalogo": "clasificacion",
        "parametros": {"sensor": "S1", "cada": 2, "pluma": "pluma1", "paso_s": 3},
        "modulos": lambda p: [("desvio", p["sensor"], p["cada"], p["pluma"], p["paso_s"])],
        "sinonimos": ["clasificacion", "clasificar", "clasifica", "clasificadora", "separar", "separacion",
                      "sorting", "desvio", "desviar", "rechazo", "rechazar", "seleccionar piezas"]},
    "alimentacion_distribucion": {
        "nombre": "alimentacion con acumulacion: si S2 (final) queda ocupado espera_s la banda espera a que se libere",
        "catalogo": "alimentacion_distribucion",
        "parametros": {"sensor": "S2", "espera_s": 1},
        "modulos": lambda p: [("acumulacion", p["sensor"], p["espera_s"])],
        "sinonimos": ["alimentacion", "alimentar", "alimentador", "distribucion", "distribuir", "acumulacion",
                      "acumular", "buffer", "pulmon", "abastecer", "almacen", "recepcion", "recibir paquetes"]},
    "linea_produccion": {
        "nombre": "linea de produccion: estacion en S1 (tiempo_s) y lote en S2 (cada 'piezas' para pausa_s)",
        "catalogo": "linea_produccion",
        "parametros": {"tiempo_s": 3, "piezas": 5, "pausa_s": 3},
        "modulos": lambda p: [("estacion", "S1", p["tiempo_s"], None),
                              ("lote", "S2", p["piezas"], p["pausa_s"], "tiempo", None)],
        "sinonimos": ["linea de produccion", "produccion", "proceso productivo", "linea industrial",
                      "manufactura", "fabrica", "planta", "simula una linea"]},
}
GENERICA = "linea_produccion"          # solo si no hay una mas especifica
MODULOS = {"estacion": estacion, "lote": lote, "desvio": desvio, "acumulacion": acumulacion}


# ---------------------------------------------------------------------------
# Deteccion por raices (item 3): "clasifique" = "clasificar", "empaca" = "empacar"
# ---------------------------------------------------------------------------
_VACIAS = {"de", "del", "la", "el", "los", "las", "un", "una", "unos", "unas", "en", "y", "a", "con"}
_SUFIJOS = sorted(["aciones", "iciones", "ciones", "acion", "icion", "cion", "adoras", "adores", "adora",
                   "ador", "amiento", "imiento", "ando", "iendo", "ados", "adas", "idos", "idas", "ado",
                   "ada", "ido", "ida", "ar", "er", "ir", "es", "os", "as", "e", "a", "o", "s"],
                  key=len, reverse=True)


def _sin_acentos(t):
    t = unicodedata.normalize("NFD", str(t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def raiz(palabra: str) -> str:
    w = _sin_acentos(palabra)
    w = re.sub(r"qu(?=[ei])", "c", w)
    w = re.sub(r"gu(?=[ei])", "g", w)
    for s in _SUFIJOS:
        if w.endswith(s) and len(w) - len(s) >= 4:
            return w[:-len(s)]
    return w


def raices(texto: str) -> list:
    return [raiz(p) for p in re.findall(r"[a-z0-9]+", _sin_acentos(texto)) if p not in _VACIAS]


def _contiene(r_texto, r_frase):
    n = len(r_frase)
    return n > 0 and any(r_texto[i:i + n] == r_frase for i in range(len(r_texto) - n + 1))


def plantillas_mencionadas(texto: str) -> list:
    """ids de plantillas que la instruccion nombra, la mas especifica primero.
    La linea de produccion generica solo queda si no hay otra."""
    rt = raices(texto)
    hallados = []
    for pid, pl in PLANTILLAS.items():
        mejor = max((len(" ".join(raices(s))) for s in pl["sinonimos"] if _contiene(rt, raices(s))), default=0)
        if mejor:
            hallados.append((mejor, pid))
    hallados.sort(key=lambda x: -x[0])
    ids = [pid for _, pid in hallados]
    if len(ids) > 1 and GENERICA in ids:
        ids.remove(GENERICA)
    return ids


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------
def _num(v, nombre, entero=False, minimo=0.1):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"proceso: '{nombre}' debe ser un numero (llego {v!r}).")
    if entero:
        if x != int(x) or x < 1:
            raise ValueError(f"proceso: '{nombre}' debe ser un entero >= 1 (llego {v!r}).")
        return int(x)
    if x < minimo:
        raise ValueError(f"proceso: '{nombre}' debe ser >= {minimo} s (llego {v!r}).")
    return int(x) if x == int(x) else x


def parametros(pid, dados) -> tuple:
    """(parametros finales, supuestos) con validacion de nombres y valores."""
    pl = PLANTILLAS[pid]
    base = {**pl["parametros"], **COMUNES}
    dados = dict(dados or {})
    extra = sorted(set(dados) - set(base))
    if extra:
        raise ValueError(f"proceso {pid}: parametros desconocidos {extra} (usa {sorted(base)}).")
    p = {**base, **{k: v for k, v in dados.items() if v is not None or k == "pluma"}}
    for k, v in list(p.items()):
        if k == "sensor":
            s = lm.nombre_canonico(v)
            s = {"I4": "S1", "I5": "S2"}.get(s, s)
            if s not in SENSORES:
                raise ValueError(f"proceso {pid}: sensor '{v}' no existe (S1 o S2).")
            p[k] = s
        elif k == "pluma":
            if v in (None, "", "ninguna", False):
                p[k] = None
            else:
                pv = re.sub(r"[^a-z0-9]", "", _sin_acentos(v))
                if pv not in BAJA:
                    raise ValueError(f"proceso {pid}: pluma '{v}' no existe (pluma1, pluma2 o null).")
                p[k] = pv
        elif k in ("piezas", "cada"):
            p[k] = _num(v, k, entero=True)
        elif k == "atasco_s":
            p[k] = 0 if v in (0, "0", False) else _num(v, k, minimo=2)
        else:
            p[k] = _num(v, k)
    supuestos = [f"{k} = {p[k]}" for k in base if k not in dados]
    return p, supuestos


def operacion_robusta(eng, atasco_s):
    """Arranque/paro, supervision de atasco, paro prioritario y torreta estandar."""
    motor = eng.setdefault("motor", {})
    motor.setdefault("arranque", "I1")
    motor.setdefault("modo", "enclavado")
    paro = [motor.get("paro") or "I2"]
    falla = None
    if atasco_s:
        tons = []
        for s in SENSORES:
            c = lm._siguiente_id(eng, "condiciones", "C", lm.N_COND)
            c.update(op="AND", entradas=[s, "BANDA_CORRIENDO"], flanco="nivel")
            t = lm._siguiente_id(eng, "timers", "T", lm.N_TIMER)
            t.update(tipo="TON", entrada=c["id"], segundos=atasco_s)
            tons.append(t["id"])
        m = lm._siguiente_id(eng, "memorias", "M", lm.N_MEM)
        m.update(set=lm._o_logico(eng, tons), reset=motor["arranque"], prioridad="set")
        falla = m["id"]
        paro.append(falla)
    motor["paro"] = lm._o_logico(eng, paro)
    sal = eng.setdefault("salidas", {})
    usadas = {lm.salida_canonica(q) for q in sal}
    if "Q3" not in usadas:
        sal["Q3"] = "BANDA_CORRIENDO"
    if "Q4" not in usadas:
        if falla:
            c = lm._siguiente_id(eng, "condiciones", "C", lm.N_COND)
            c.update(op="AND", entradas=["BANDA_DETENIDA", f"NOT {falla}"], flanco="nivel")
            sal["Q4"] = c["id"]
        else:
            sal["Q4"] = "BANDA_DETENIDA"
    if falla and "Q5" not in usadas:
        sal["Q5"] = falla
    op = eng.setdefault("opciones", {})
    op.setdefault("luces_paro", ["Q5"])
    return falla


def expandir_proceso(spec, eng, mover=True) -> tuple:
    """'proceso' del modelo -> bloques en eng. Devuelve (eng, resumen)."""
    if not isinstance(spec, dict) or not spec.get("id"):
        raise ValueError("proceso: debe ser {\"id\": ..., \"parametros\": {...}}.")
    pid = re.sub(r"[^a-z_]", "", _sin_acentos(spec["id"]).replace(" ", "_"))
    if pid not in PLANTILLAS:
        raise ValueError(f"proceso '{spec['id']}' no existe; usa uno de: {', '.join(PLANTILLAS)}.")
    if not mover:
        raise ValueError(f"proceso {pid}: la banda debe moverse (banda.mover = true).")
    p, supuestos = parametros(pid, spec.get("parametros"))
    eng = copy.deepcopy(eng)
    for mod in PLANTILLAS[pid]["modulos"](p):
        MODULOS[mod[0]](eng, *mod[1:])
    operacion_robusta(eng, p["atasco_s"])
    return eng, {"id": pid, "nombre": PLANTILLAS[pid]["nombre"], "parametros": p, "supuestos": supuestos}


# ---------------------------------------------------------------------------
# Texto para el modelo (solo en peticiones generales)
# ---------------------------------------------------------------------------
def menu_plantillas() -> str:
    lineas = []
    for pid, pl in PLANTILLAS.items():
        par = ", ".join(f"{k}={v if v is not None else 'null'}" for k, v in pl["parametros"].items())
        lineas.append(f"- {pid}: {pl['nombre']}" + (f" · {par}" if par else ""))
    return "\n".join(lineas)
