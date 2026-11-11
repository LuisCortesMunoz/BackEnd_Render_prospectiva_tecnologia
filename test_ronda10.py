"""
test_ronda10.py — correcciones como las dice el usuario (lentes / web, 10 nov).

Al corregir ("esta mal, cambiala", "quitala", "no lo hace bien") el sistema estorbaba
aunque el modelo lo hiciera bien:
  1. clasificar: el regex anterior no veia 13/30 quejas ("no lo hace bien", "hay un
     error") y tomaba como queja 4/8 ordenes ("Ademas la roja no se enciende al
     arrancar"); ahora tres casos: vaga / falla / con_dato
  2. cobertura exigia lo que el mensaje QUITA o REEMPLAZA (15/17 frases): "no quiero la
     roja, quitala", "en lugar de la pluma 1 usa la 2", "5 s, no 3 s" — tambien sin
     puntuacion (voz)
  3. ediciones: el mismo tiempo repetido para UN recurso (timer + evento de la pluma) se
     cambia completo ("mejor que sean 5 s"); con recursos distintos no se adivina
  4. huella: si el pedido es de tiempo, el modulo que depende de ese tiempo (la pluma de
     3 -> 5 s) puede cambiar, incluso el orden de sus movimientos
  5. de punta a punta: "esta mal, cambiala a 45 Hz" sin el modelo; "no lo esta haciendo
     bien" pregunta; "quitala" no insiste en volver a ponerla; una edicion que no cambio
     nada lo avisa
  6. cambiar el boton de arranque ("que arranque con I2", "inicie con I2", "el boton de
     arranque a I2"): la instruccion mas reciente manda; el paro con el boton que ahora
     arranca deja de exigirse; "ahora que se detenga con I1" reemplaza el paro;
     "en lugar de I1" / "cambia el sensor 1 por el 2" actualizan las senales

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda10.py
"""

import asyncio
import contextlib
import copy
import io
import logging
import os
import re
import tempfile
import unicodedata

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_r10_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_r10.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

logging.disable(logging.WARNING)

with contextlib.redirect_stdout(io.StringIO()):
    import app
import cambios_texto as ct
import cobertura
import comportamiento as co
import edicion_incremental as ei
import ediciones
import huella_banda as hb
import lavo_motor as lm

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_r10.json")
app.BANDA_MOTOR_V7 = True
OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


def voz(t: str) -> str:
    """Como puede llegar de la transcripcion: sin acentos ni puntuacion."""
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[.,;:!?¿¡]", "", t)


def cfg(resp):
    with contextlib.redirect_stdout(io.StringIO()):
        c = lm.cfg_desde_respuesta(resp)
    c["respuesta_llm"] = resp
    return c


# Banco C2 t1 (r13a): I1/I2, verde en marcha, roja detenida, pluma 1 sube 3 s con S1.
T_C2 = ("Con I1 la banda arranca a 30 Hz hacia la derecha y con I2 se detiene. La luz verde debe estar encendida "
        "mientras la banda avanza y la luz roja mientras está detenida. Cuando el sensor 1 detecta una caja, la "
        "pluma 1 sube durante 3 segundos y luego baja.")
C2 = {"name": "Banda con luces y pluma", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
      "motor": {"arranque": "I1", "paro": "I2", "modo": "enclavado"},
      "condiciones": [{"id": "C1", "op": "AND", "entradas": ["T1"], "flanco": "bajada"}], "memorias": [],
      "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "I4", "reset": None, "segundos": 3}], "contadores": [],
      "salidas": {"Q3": "BANDA_CORRIENDO", "Q5": "BANDA_DETENIDA"},
      "eventos": [{"cuando": "I4", "espera_s": 0, "duracion_s": 3, "acciones": {"pluma1": {"estado": "subir"}}},
                  {"cuando": "C1", "espera_s": 0, "duracion_s": None, "acciones": {"pluma1": {"estado": "bajar"}}}],
      "secuencias": [], "opciones": {"filtro_sensores_s": 0.1, "filtro_botones_s": 0, "pluma_max_s": 0,
                                     "luces_paro": ["Q5"]}}


def mod(f):
    r = copy.deepcopy(C2)
    f(r)
    return r


SIN_ROJA = mod(lambda r: (r.__setitem__("salidas", {"Q3": "BANDA_CORRIENDO"}),
                          r["opciones"].__setitem__("luces_paro", [])))
SIN_VERDE = mod(lambda r: r.__setitem__("salidas", {"Q5": "BANDA_DETENIDA"}))


def _p2(r):
    for e in r["eventos"]:
        e["acciones"] = {"pluma2": e["acciones"]["pluma1"]}


CON_P2 = mod(_p2)
SIN_PLUMA = mod(lambda r: (r.__setitem__("eventos", []), r.__setitem__("timers", []),
                           r.__setitem__("condiciones", [])))
CINCO = mod(lambda r: (r["timers"][0].__setitem__("segundos", 5), r["eventos"][0].__setitem__("duracion_s", 5)))
AMARILLA = mod(lambda r: (r.__setitem__("salidas", {"Q3": "BANDA_CORRIENDO", "Q4": "BANDA_DETENIDA"}),
                          r["opciones"].__setitem__("luces_paro", ["Q4"])))

# ── 1. clasificar ──────────────────────────────────────────────────────────
escenario("1. que es una queja (y de que tipo)")
QUEJAS = ["No lo está haciendo bien.", "No lo hace bien.", "No está bien.", "No hace lo que pedí.", "No es correcto.",
          "Lo hiciste mal.", "Así no es.", "No sale bien.", "Está mal, cámbiala.", "No jala.", "No hizo caso.",
          "No la detiene.", "La banda no se detiene.", "No se detiene la banda.", "No detiene la banda con I2.",
          "No se apagó la luz.", "La luz no se apaga.", "La pluma no baja.", "No baja la pluma.",
          "No está haciendo nada.", "Sigue sin hacer lo que le pedí.", "No me hizo caso.", "Eso no es lo que quería.",
          "Está incorrecto.", "Hay un error.", "No quedó como quería.", "Algo está mal.", "Tiene un error.",
          "Se equivocó.", "No sirve."]
for q in QUEJAS:
    check(ei.es_correccion(q) and ei.es_correccion(voz(q)), f"queja: «{q}» (tambien por voz)")
ORDENES = ["Cambia: al llegar a 4 la banda ya no se detiene, solo se enciende la luz roja.",
           "Agrega que la banda no se detenga en el sensor 1.", "Ahora que la pluma no suba.",
           "Que la luz verde no se apague mientras avanza.", "Que la banda no arranque si no se presiona I1.",
           "Además la roja no se enciende al arrancar, solo al parar.", "La pluma 2 tampoco debe moverse.",
           "No quiero la luz roja, quítala.", "No, mejor que sean 5 segundos.", "Cambia la velocidad a 45 Hz."]
for o in ORDENES:
    check(not ei.es_correccion(o), f"orden, no queja: «{o}»")
for t, clase in [("No lo está haciendo bien.", "vaga"), ("Está mal, cámbiala.", "vaga"),
                 ("Está mal, con I2 no se detiene.", "falla"),
                 ("La luz verde se queda prendida cuando la banda está parada, corrígelo.", "falla"),
                 ("Está mal, la luz verde ya no enciende con el sensor 1.", "falla"),
                 ("No funciona: al presionar I1 otra vez la banda ya no se detiene. Revisa sin quitar la pausa del "
                  "sensor 1.", "falla"),
                 ("Está mal, la pluma 1 debe bajar, no subir.", "con_dato"),
                 ("Está mal, cámbiala a 45 Hz.", "con_dato"), ("esta mal cambiala a 45 hz", "con_dato"),
                 ("Está mal, en lugar de la pluma 1 usa la pluma 2.", "con_dato"),
                 ("No funciona, agrega que la luz amarilla encienda 2 segundos cuando el sensor 2 detecte una caja.",
                  "con_dato")]:
    check(ei.clasificar(t) == clase, f"{clase}: «{t}» ({ei.clasificar(t)})")
check(ei.sin_queja("Está mal, cámbiala a 45 Hz.") == "cámbiala a 45 Hz", "sin_queja deja solo lo que pide")
check(ei.sin_queja("No lo está haciendo bien.") == "", "una queja vaga queda vacia")
for t in ["revisa", "checa", "no está funcionando", "corrige", "no se detiene", "sigue igual", "está mal"]:
    check(ei.es_correccion(t), f"sigue siendo queja: «{t}»")
for t in ["agrega la luz roja", "haz que la banda se detenga por sí sola", "cambia el tiempo a 5 s"]:
    check(not ei.es_correccion(t), f"sigue sin ser queja: «{t}»")
for t in ["No quiero la luz roja", "La roja sobra", "No uses la pluma 2"]:
    check(ei.pide_quitar(t), f"puede quitar piezas: «{t}»")
check(not ei.pide_quitar("Cambia el último tiempo de 5 a 8 segundos"), "cambiar un tiempo no quita piezas")

# ── 2. cobertura ───────────────────────────────────────────────────────────
escenario("2. cobertura no exige lo que se quita o se reemplaza")
QUITA = [("No quiero la luz roja, quítala.", SIN_ROJA), ("La luz roja quítala.", SIN_ROJA),
         ("Que no se encienda la luz roja.", SIN_ROJA), ("No debe prender la roja.", SIN_ROJA),
         ("La roja sobra.", SIN_ROJA), ("Está mal, la luz verde no debe encender.", SIN_VERDE),
         ("Está mal, en lugar de la pluma 1 usa la pluma 2.", CON_P2), ("Usa la pluma 2, no la pluma 1.", CON_P2),
         ("No es la pluma 1, es la pluma 2.", CON_P2), ("Cambia la pluma 1 por la pluma 2.", CON_P2),
         ("No uses ninguna pluma.", SIN_PLUMA), ("La pluma 1 no se debe mover.", SIN_PLUMA),
         ("Está mal, debe subir 5 segundos, no 3 segundos.", CINCO), ("Que sean 5 segundos y no 3 segundos.", CINCO),
         ("Pon la amarilla, no la roja.", AMARILLA), ("La roja no, la amarilla.", AMARILLA),
         ("Cambia la roja por la amarilla.", AMARILLA)]
for t, resp in QUITA:
    c = cfg(resp)
    check(cobertura.faltantes(t, c) == [] and cobertura.faltantes(voz(t), c) == [],
          f"no exige lo quitado: «{t}» ({cobertura.faltantes(t, c)} / voz {cobertura.faltantes(voz(t), c)})")
BASE = cfg(C2)
for t, esperado in [("Agrega que la amarilla encienda 2 segundos.", ["2 s", "luz amarilla (Q4)"]),
                    ("Quita la roja y pon la amarilla 3 segundos.", ["luz amarilla (Q4)"]),
                    ("Usa la pluma 2, no la pluma 1.", ["pluma 2"]), ("Cambia la velocidad a 45 Hz.", ["45 Hz"]),
                    ("Que la banda no se detenga y la luz amarilla siga encendida.", ["luz amarilla (Q4)"]),
                    ("Mejor que sean 7 segundos.", ["7 s"]), ("Que vaya hacia la izquierda.", ["sentido izquierda"]),
                    ("Cuando el sensor 2 detecte, la pluma 2 baja 4 segundos.", ["4 s", "pluma 2"])]:
    f = cobertura.faltantes(t, BASE)
    check(all(e in f for e in esperado), f"sigue exigiendo lo nuevo: «{t}» -> {f}")

# ── 3. ediciones ───────────────────────────────────────────────────────────
escenario("3. el mismo tiempo para un solo recurso se cambia completo")
r = ediciones.aplicar("No, mejor que sean 5 segundos.", C2)
check(r is not None and r[0]["timers"][0]["segundos"] == 5 and r[0]["eventos"][0]["duracion_s"] == 5,
      "timer y evento de la pluma 1: 3 s -> 5 s")
DOS = mod(lambda r: r.__setitem__("eventos", r["eventos"] + [
    {"cuando": "I5", "espera_s": 0, "duracion_s": 3, "acciones": {"amarilla": "encender"}}]))
check(ediciones.aplicar("Mejor que sean 5 segundos.", DOS) is None,
      "3 s de la pluma y 3 s de la amarilla: no se adivina cual")
r = ediciones.aplicar("cámbiala a 45 Hz", C2)
check(r is not None and r[0]["banda"]["frecuencia_hz"] == 45, "'cámbiala a 45 Hz' es un cambio de frecuencia")

# ── 4. huella ──────────────────────────────────────────────────────────────
escenario("4. huella: lo que depende del tiempo pedido no es 'cambio no pedido'")
check(hb.modulos_por_tiempos(C2, CINCO) == {"plumas"}, f"3 -> 5 s del timer/evento = plumas "
                                                      f"({hb.modulos_por_tiempos(C2, CINCO)})")
check(hb.modulos_por_tiempos(C2, copy.deepcopy(C2)) == set(), "sin cambio de tiempo: nada")
check(hb.modulos_por_tiempos(C2, "x") == set() and hb.modulos_por_tiempos(None, C2) == set(),
      "con otra forma no lanza")
dif_orden = [{"modulo": "plumas", "escenario": "I1 y 5 cajas en S1", "canal": "plumas_orden", "sin_tiempo": True,
              "antes": "a", "despues": "b"}]
zona_t = {"modulos": ["tiempos"], "etapas": [], "vago": False}
check(hb.fuera_de_zona(dif_orden, zona_t) == dif_orden, "sin las versiones se juzga como antes")
check(hb.fuera_de_zona(dif_orden, zona_t, C2, CINCO) == [], "con el tiempo de la pluma cambiado: es lo pedido")
check(hb.fuera_de_zona(dif_orden, zona_t, C2, AMARILLA) == dif_orden,
      "si el tiempo no cambio, el orden de las plumas sigue protegido")

# ── 5. de punta a punta ────────────────────────────────────────────────────
escenario("5. /generar-logica con las frases del usuario")
llamadas = []
respuesta = {"r": None}


def _llm(msgs):
    llamadas.append(msgs)
    if respuesta["r"] is None:
        raise AssertionError("no se debia llamar al modelo")
    return copy.deepcopy(respuesta["r"])


app.llamar_modelo_json = _llm
app.llamar_extractor_json = lambda m: (_ for _ in ()).throw(ValueError("sin extractor"))


def pedir(texto, resp_modelo=None):
    llamadas.clear()
    respuesta["r"] = resp_modelo
    vig = cfg(copy.deepcopy(C2))
    ctx = {"programa_anterior": {"metadata": {"name": "C2", "engine_config": vig}}, "historial": [T_C2]}
    with contextlib.redirect_stdout(io.StringIO()):
        return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", contexto=ctx)))


res = pedir("Está mal, cámbiala a 45 Hz.")
check(res.status == "ok" and res.analysis.get("fuente") == "edicion" and not llamadas
      and res.logic["band"]["freq_hz"] == 45, f"'Está mal, cámbiala a 45 Hz': edicion directa, 0 llamadas "
                                               f"({res.status}, {res.analysis.get('fuente')}, {len(llamadas)})")
res = pedir("No lo está haciendo bien.")
check(res.status == "needs_clarification" and not llamadas, "'No lo está haciendo bien': pregunta qué falla")
res = pedir("No, mejor que sean 5 segundos.")
check(res.status == "ok" and res.analysis.get("fuente") == "edicion" and not llamadas,
      "'No, mejor que sean 5 segundos': edicion directa")
res = pedir("No quiero la luz roja, quítala.", SIN_ROJA)
check(res.status == "ok" and len(llamadas) == 1 and not any("No encontré" in w for w in res.warnings),
      f"'quítala': se entrega sin insistir en volver a ponerla ({len(llamadas)} llamadas, {res.warnings})")
res = pedir("Está mal, en lugar de la pluma 1 usa la pluma 2.", CON_P2)
check(res.status == "ok" and len(llamadas) == 1 and not any("No encontré" in w for w in res.warnings),
      f"'en lugar de la pluma 1 usa la pluma 2': a la primera ({len(llamadas)} llamadas)")
res = pedir("Que la luz verde encienda mientras la banda avanza.", copy.deepcopy(C2))
check(res.status == "ok" and any("La lógica quedó igual" in w for w in res.warnings),
      "una edicion que no cambio nada lo avisa (nunca en silencio)")

# ── 6. cambiar el boton de arranque (lentes: 'que arranque con I2' seguia en I1) ──
escenario("6. la instruccion mas reciente manda sobre el boton de arranque y de paro")


def reqs_de(hist, texto):
    return sorted(r["tipo"] + " " + str(r.get("senal")) for r in co.de_sesion(hist, texto, {}))


for t1 in ["Quiero que la banda inicie al presionar I1.", "Que la banda arranque al presionar I1 a 30 Hz.",
           "Al presionar I1 la banda avanza a 30 Hz y con I2 se detiene."]:
    for t2 in ["Que arranque con I2.", "Ahora quiero que arranque al presionar I2.", "Cambia para que arranque con I2.",
               "Mejor que inicie con I2.", "Que inicie con I2 en lugar de I1.", "Está mal, debe arrancar con I2.",
               "Cambia el botón de arranque a I2.", voz("Que la banda inicie al presionar I2.")]:
        r = reqs_de([t1], t2)
        check("arranca_con I2" in r and "arranca_con I1" not in r and "para_con I2" not in r,
              f"«{t1[:30]}…» + «{t2}» -> {r}")
H = ["Al presionar I1 la banda avanza a 30 Hz y con I2 se detiene."]
check(reqs_de(H, "Ahora que se detenga con I1.") == ["arranca_con I1", "para_con I1"],
      "'ahora que se detenga con I1' reemplaza el paro con I2")
check(reqs_de(H, "Además, con I1 también se detiene.") == ["arranca_con I1", "para_con I1", "para_con I2"],
      "'tambien con I1' lo suma")
check(reqs_de(H, "Agrega que la luz roja encienda con I2.") == ["arranca_con I1", "para_con I2"],
      "hablar de una luz con I2 no toca el arranque ni el paro")
check(ct.sustituciones("Que arranque con I2 en lugar de I1.")["s"] == [("I1", "I2")], "I1 -> I2 (en lugar de)")
check(ct.sustituciones("En lugar del sensor 1, usa el sensor 2.")["s"] == [("S1", "S2")], "S1 -> S2 (en lugar del)")
check(ct.sustituciones("Cambia el sensor 1 por el sensor 2.")["s"] == [("S1", "S2")], "S1 -> S2 (cambia por)")
r = co.de_sesion(["Al presionar I1 la banda avanza a 30 Hz. Cuando el sensor 1 detecta una caja, la banda se detiene "
                  "3 segundos y luego sigue."], "Que la pausa sea con el sensor 2 en lugar del sensor 1.", {})
check([x["senal"] for x in r if x["tipo"] == "pausa_sensor"] == ["S2"], "la pausa pasa al sensor 2")

# De punta a punta: la logica vigente la armo el constructor (eventos I1 avanzar / I2 detener)
# y el modelo cambia el arranque a I2: antes se conservaba la vigente ("rompia para_con I2").
VIG = {"name": "Banda con I1", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
       "eventos": [{"cuando": "I1", "acciones": {"banda": "avanzar"}}, {"cuando": "I2", "acciones": {"banda": "detener"}}]}
NUEVA = copy.deepcopy(VIG)
NUEVA["eventos"] = [{"cuando": "I2", "acciones": {"banda": "avanzar"}}, {"cuando": "I1", "acciones": {"banda": "detener"}}]
llamadas.clear()
respuesta["r"] = NUEVA
vig = cfg(copy.deepcopy(VIG))
ctx = {"programa_anterior": {"metadata": {"name": "x", "engine_config": vig}}, "historial": H}
with contextlib.redirect_stdout(io.StringIO()):
    res = asyncio.run(app.generar_logica(app.LogicaRequest(texto="Que arranque con I2.", device="banda", contexto=ctx)))
mot = res.logic["band"]["engine"].get("motor") or {}
check(res.status == "ok" and res.analysis.get("fuente") == "modelo" and mot.get("arranque") == "I2"
      and len(llamadas) == 1, f"'Que arranque con I2' se aplica a la primera ({res.analysis.get('fuente')}, {mot}, "
                              f"{len(llamadas)} llamadas)")

print(f"\nTODAS LAS PRUEBAS DE LA RONDA 10 PASARON ({OK} comprobaciones).")
