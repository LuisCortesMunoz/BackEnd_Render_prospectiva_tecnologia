"""
test_receta_y_vivos.py — 2a ronda del banco de pruebas con Groq real:

  1. receta_conteo: "contar N -> avanzar X s -> parar -> acciones en orden ->
     reiniciar" se arma SIN el modelo y se verifica con 2 ciclos completos
  2. huella: el ORDEN de las plumas se juzga aunque la correccion hable del
     contador o de un tiempo (banco C11 t2: la correccion junto las plumas)
  3. "parpadea" se verifica en la simulacion (banco C5)
  4. requisitos y etapas vivos: "la amarilla en lugar de la roja", "cambia los
     10 s por 6 s" actualizan lo que se exige

Corre sin Groq (modelo simulado) y sin PLC (lavo_sim):  python test_receta_y_vivos.py
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_rec_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_rec.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import cambios_texto as ct
import comportamiento as co
import edicion_incremental as ei
import etapas_orden as eo
import huella_banda as hb
import lavo_motor as lm
import receta_conteo as rc
import simulacion_etapas as se

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_rec.json")
app.BANDA_MOTOR_V7 = True

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


T11 = ("Necesito que revises y corrijas la lógica de la banda transportadora para que avance hacia la derecha y, "
       "cuando el Sensor 2 acumule 3 detecciones, la banda continúe avanzando durante 6 segundos y después se "
       "detenga automáticamente. Una vez detenida, la pluma 2 debe descender y la pluma 1 debe elevarse, respetando "
       "ese orden. Finalmente, el contador debe reiniciarse automáticamente a cero y permanecer en ese estado hasta "
       "que comience una nueva secuencia. Revisa especialmente la lógica de reinicio del contador, ya que actualmente "
       "presenta fallas frecuentes y no siempre se restablece correctamente.")
T11b = "Revisa, el contador no se reinicia: en el segundo ciclo la banda ya no se detiene a las 3 detecciones."
T3 = ("Al presionar I1 la banda avanza a 30 Hz. Cada vez que el sensor 2 detecta una caja se cuenta. Cuando el "
      "contador llegue a 3, la banda avanza 5 segundos más y luego se detiene sola, y el contador se reinicia.")
T3b = "Cambia el conteo a 5 cajas en lugar de 3."
T3c = "Además, cuando se detenga por el conteo, que se encienda la luz roja durante 4 segundos."
T1 = ("Al presionar el pulsador I1, la banda se pone en marcha a 20 Hz. Cuando el Sensor 1 detecta un objeto, la "
      "pluma 1 se eleva. Al activarse el Sensor 2, la banda se detiene durante 3 segundos y después continúa; una vez "
      "que el Sensor 2 acumula 3 detecciones, la banda avanza 5 segundos y se detiene.")
HZ = {"freq_hz": {"pregunta": "", "respuesta": "20 Hz"}}

# ---------------------------------------------------------------------------
escenario("1. Receta de conteo: lectura")
p = rc.detectar([T11], HZ)
check(p and p["conteo"] == 3 and p["senal"] == "S2" and p["segundos"] == 6 and p["arranque"] == "I1",
      "caso real: 3 en S2, sigue 6 s, arranca con I1")
check([q for q, _ in p["acciones"]] == ["Q7", "Q8"], "acciones en orden: pluma 2 baja y DESPUES pluma 1 sube")
check(rc.detectar([T11, T11b], HZ) is not None, "la queja 'ya no se detiene a las 3' no quita el paro (se rearma igual)")
p3 = rc.detectar([T3], {})
check(p3 and p3["hz"] == 30 and p3["acciones"] == [], "C3: 30 Hz del texto, sin acciones")
check(rc.detectar([T3, T3b], {})["conteo"] == 5, "C3: 'cambia el conteo a 5' -> 5")
check(rc.detectar([T3, T3b, T3c], {})["acciones"] == [("Q5", 4.0)], "C3: 'cuando se detenga, la roja 4 s' se agrega")
check(rc.detectar([T1], HZ) is None, "con pausas y acciones por otro sensor (banco C1): no aplica")
check(rc.detectar([T3 + " La luz roja parpadea al contar."], {}) is None, "con parpadeo: no aplica")
check(rc.detectar(["Con I1 arranca a 30 Hz y con I2 se detiene."], {}) is None, "sin conteo: no aplica")
check(rc.detectar([T3, "Al llegar a 3 la banda ya no se detiene."], {}) is None, "'ya no se detiene': no aplica")

escenario("1. Receta de conteo: programa y verificacion (2 ciclos)")
for nombre, plan in (("caso real", rc.detectar([T11], HZ)), ("C3 con la roja 4 s", rc.detectar([T3, T3b, T3c], {}))):
    r = rc.armar(plan)
    cfg = lm.cfg_desde_respuesta(r)
    check(app.validar_logica_banda(cfg) == [] and rc.verificar(cfg, plan) == [],
          f"{nombre}: valida y cumple 2 ciclos completos")
    check(r["contadores"][0]["reset"] == "SQ1_CICLO" and "SQ1_CICLO" in str(r["motor"]["paro"]),
          f"{nombre}: contador y paro con SQ1_CICLO (no se traba, no vuelve a avanzar sola)")
mal = rc.armar(rc.detectar([T11], HZ))
mal["secuencias"][0]["pasos"][2]["salidas"] = ["Q7", "Q8"]
mal["secuencias"][0]["pasos"].pop(3)
f = rc.verificar(lm.cfg_desde_respuesta(mal), rc.detectar([T11], HZ))
check(f and "orden" in f[0], f"plumas juntas: la verificacion lo detecta ({f[:1]})")

escenario("1. Receta en la generacion (sin llamar al modelo)")
llamadas = []


def _llm(msgs):
    llamadas.append(msgs)
    raise AssertionError("no se debia llamar al modelo")


app.llamar_modelo_json = _llm


def generar(texto, acl=None, prev=None, historial=None):
    llamadas.clear()
    ctx = None
    if prev is not None:
        ctx = app.ContextoLadder(programa_anterior={"metadata": {"name": "p", "engine_config": prev}},
                                 historial=historial or [])
    return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", contexto=ctx,
                                                            aclaraciones=acl)))


r = generar(T11)
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "freq_hz" and not llamadas,
      "sin frecuencia: se pregunta (sin el modelo)")
r = generar(T11, acl=HZ)
check(r.status == "ok" and r.analysis["fuente"] == "receta" and not llamadas, "con frecuencia: receta, 0 llamadas")
check(r.logic["band"]["engine"]["motor"]["arranque"] == "I1", "arranca con I1")
r2 = generar(T11b, prev=r.logic, historial=[T11 + " [freq_hz: 20 Hz]"])
check(r2.status == "ok" and r2.analysis["fuente"] == "receta" and not llamadas,
      "la correccion 'el contador no se reinicia' se rearma con la receta (sin el modelo)")

# ---------------------------------------------------------------------------
escenario("2. Huella: el orden de las plumas aunque se hable del contador")
buena = lm.cfg_desde_respuesta(rc.armar(rc.detectar([T11], HZ)))
juntas = lm.cfg_desde_respuesta(mal)
zona = ei.localizar(T11b)
check("conteo" in zona["modulos"], f"zona de la correccion: {zona['modulos']}")
f = hb.fuera_de_zona(hb.comparar(hb.huella(buena), hb.huella(juntas)), zona)
check(f and all(x["canal"] == "plumas_orden" for x in f), "plumas juntas: se detecta por el ORDEN (sin tiempos)")
check("antes la pluma 2 (baja) -> la pluma 1 (sube)" in hb.mensaje(f), "el mensaje dice el orden de antes y el de ahora")
r5 = rc.armar(rc.detectar([T3, T3b], {}))
f = hb.fuera_de_zona(hb.comparar(hb.huella(lm.cfg_desde_respuesta(rc.armar(rc.detectar([T3], {})))),
                                 hb.huella(lm.cfg_desde_respuesta(r5))), ei.localizar(T3b))
check(f == [], "cambiar el conteo de 3 a 5 (mueve el momento de todo) no se toma como rotura")

# ---------------------------------------------------------------------------
escenario("3. Parpadeo verificado")
T5 = ("Secuencia 1: al presionar I1, la banda avanza 10 segundos, se detiene 3 segundos y vuelve a avanzar 5 segundos "
      "y se detiene. Secuencia 2: cuando el sensor 2 detecta una caja, la luz roja parpadea durante 4 segundos.")
check(co.parpadeo_en(T5) == [{"q": "Q5", "senal": "S2"}], "'la roja parpadea cuando S2 detecta' -> Q5 con S2")


def prog5(parpadea, luz="Q5", t1=10):
    sq2 = {"id": "SQ2", "inicio": "S2", "modo": "una_vez", "exclusiva": False, "pasos": [{"avanza": "tiempo", "segundos": 4}]}
    r = {"name": "c5", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
         "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
             {"motor": ["marcha"], "avanza": "tiempo", "segundos": t1}, {"motor": ["paro"], "avanza": "tiempo", "segundos": 3},
             {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5}, {"motor": ["paro"], "avanza": "tiempo", "segundos": 0.5}]},
             sq2]}
    if parpadea:
        r["timers"] = [{"id": "T1", "tipo": "INTERMITENTE", "entrada": "SQ2_ACTIVA", "segundos": 0.5, "segundos_off": 0.5}]
        r["salidas"] = {luz: "T1"}
    else:
        sq2["pasos"][0]["salidas"] = [luz]
    return lm.cfg_desde_respuesta(r)


req = [x for x in co.requisitos([T5]) if x["tipo"] == "parpadea"]
check(co.verificar(prog5(True), req) == [], "con timer INTERMITENTE: parpadea, cumple")
f = co.verificar(prog5(False), req)
check(f and "PARPADEAR" in f[0] and "INTERMITENTE" in f[0], "fija (banco C5): se detecta y se dice como hacerlo")
check(se.verificar(prog5(True), eo.detectar(T5, "banda", {})) == [], "la simulacion de etapas acepta la luz que parpadea")

# ---------------------------------------------------------------------------
escenario("4. Requisitos y etapas vivos")
AM = "La luz de la secuencia 2 ahora debe ser la amarilla en lugar de la roja."
SEIS = "En la secuencia 1 cambia los 10 segundos por 6 segundos."
def _qt(d):
    return {k: d[k] for k in ("q", "t")}       # "s" (senales) se prueba en test_ronda10


check(_qt(ct.sustituciones(AM)) == {"q": [("Q5", "Q4")], "t": []}
      and _qt(ct.sustituciones(SEIS)) == {"q": [], "t": [(10.0, 6.0)]},
      "lectura: roja -> amarilla; 10 s -> 6 s")
check(ct.sustituciones("cambia la roja por la verde")["q"] == [("Q5", "Q3")], "'cambia la roja por la verde'")
reqs = co.requisitos([T5, SEIS, AM])
check([x.get("q") for x in reqs if x["tipo"] == "parpadea"] == ["Q4"], "el parpadeo exigido pasa a la amarilla")
d = eo.de_historial([T5, SEIS], "banda", {}, posteriores=[AM])
check(d["etapas"][0]["tiempos"][0] == 6.0, "etapas del historial: la secuencia 1 ya exige 6 s (no 10)")
check(any("Q4" in r["q"] for r in d["etapas"][1]["requisitos"] if r["tipo"] == "salida")
      and "amarilla" in d["etapas"][1]["texto"], "etapas del historial: la secuencia 2 exige la amarilla")
check(se.verificar(prog5(True, "Q4", 6), d) == [], "la version con 6 s y amarilla cumple las etapas VIGENTES")
f = co.verificar(prog5(True, "Q5", 6), [x for x in reqs if x["tipo"] == "parpadea"])
check(bool(f) and "amarilla" in f[0], f"la que se quedo con la roja no cumple lo VIGENTE ({f[:1]})")
check(co.verificar(prog5(True, "Q4", 6), [x for x in reqs if x["tipo"] == "parpadea"]) == [],
      "la amarilla que parpadea si")

print(f"\nTODAS LAS PRUEBAS DE RECETA Y REQUISITOS VIVOS PASARON ({OK} comprobaciones).")
