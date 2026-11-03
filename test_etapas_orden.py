"""
test_etapas_orden.py — varias acciones EN ORDEN ("primero A, cuando termine B,
para cerrar C") en la banda v7 y el maletin.

Corre sin Groq (modelo simulado) y sin PLC (los escenarios de la banda se
ejecutan sobre el ST real con lavo_sim):
    python test_etapas_orden.py
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_etapas_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_etapas.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import contexto_ramificado as cr
import etapas_orden as eo
import lavo_motor as lm
import lavo_sim as ls
import memoria_validada as mv
import preguntas_banda as pb

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_etapas.json")
app.BANDA_MOTOR_V7 = True
app.BANDA_MOTOR_CONFIGURABLE = False

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1


def escenario(nombre):
    print(f"\n== {nombre}")


respuestas, llamadas = [], []


def _llm(msgs):
    llamadas.append(copy.deepcopy(msgs))
    if not respuestas:
        raise AssertionError("el modelo fue llamado sin respuesta preparada")
    return copy.deepcopy(respuestas.pop(0))


app.llamar_modelo_json = _llm


def generar(texto, *resp, device="banda", acl=None):
    respuestas[:] = list(resp)
    llamadas.clear()
    req = app.LogicaRequest(texto=texto, device=device, aclaraciones=acl)
    return asyncio.run(app.generar_logica(req))


def cfg(resp):
    return lm.cfg_desde_respuesta(resp)


def cargar(band):
    sim = ls.SimV7()
    err, _ = sim.cargar(band)
    check(err == 0, f"la configuracion carga en el ST (error {err})")
    return sim


def reqs(e):
    return [sorted(r["q"]) if r["tipo"] == "salida" else r["accion"] for r in e["requisitos"]]


M30 = {"mover": True, "direccion": "derecha", "frecuencia_hz": 30}
TEXTO = ("Con I1: primero la banda avanza a 30 Hz hasta que S1 detecte, cuando termine sube la pluma 1 "
         "y para cerrar prende la roja 5 segundos.")
BIEN = {"name": "Avanza, pluma, roja", "banda": M30,
        "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
            {"motor": ["marcha"], "avanza": "senal", "senal": "S1"},
            {"salidas": ["Q8"], "avanza": "tiempo", "segundos": 2},
            {"salidas": ["Q5"], "avanza": "tiempo", "segundos": 5}]}],
        "analisis": {"intencion": "tres acciones", "compatibilidad": "total", "conservado": [], "modificado": [],
                     "simplificado": [], "no_soportado": [], "requiere_confirmacion": False}, "preguntas": []}
CON_EVENTOS = {"name": "eventos", "banda": M30, "motor": {"arranque": "I1"},
               "eventos": [{"cuando": "S1", "duracion_s": 2, "acciones": {"banda": "detener", "pluma1": "subir"}},
                           {"cuando": "NOT Q8", "duracion_s": 5, "acciones": {"roja": "encender"}}],
               "analisis": {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [],
                            "simplificado": [], "no_soportado": [], "requiere_confirmacion": False}, "preguntas": []}

# ---------------------------------------------------------------------------
escenario("1. Deteccion de etapas")
d = eo.detectar(TEXTO, "banda")
check(d["aplica"] and len(d["etapas"]) == 3, "tres etapas")
e1, e2, e3 = d["etapas"]
check(reqs(e1) == ["marcha"] and e1["hasta"] == "S1", "1: la banda avanza hasta S1")
check(reqs(e2) == [["Q8"]], "2: pluma 1 sube (Q8)")
check(reqs(e3) == [["Q5"]] and e3["tiempos"] == [5.0], "3: roja 5 s")
check(not eo.preguntas(d, "banda"), "todas dicen cuando terminan (la pluma usa su recorrido)")

d = eo.detectar("Al presionar el botón verde la banda avanza; cuando el sensor 1 detecte se detiene y baja la "
                "pluma 2; cuando termine, súbela y finalmente la banda vuelve a avanzar 5 s", "banda")
check(len(d["etapas"]) == 4, "cuatro etapas (con 'cuando S1 detecte' como corte)")
check(d["etapas"][1]["entra_con"] == "S1" and "paro" in reqs(d["etapas"][1]) and ["Q7"] in reqs(d["etapas"][1]),
      "2: entra con S1, detiene la banda y baja la pluma 2")
check(reqs(d["etapas"][2]) == [["Q6"]], "3: 'subela' = la pluma 2 de la etapa anterior")
check(not any(["Q3"] in reqs(e) for e in d["etapas"]), "'boton verde' no es la luz verde")
check(not eo.preguntas(d, "banda"), "sin preguntas")

for t in ("Prende la verde y luego la roja", "Cuando S1 detecte baja la pluma 1; cuando S2 detecte súbela",
          "La banda transportadora avanza con I1 a 30 Hz y se detiene con I2",
          "Con I1 prende la verde, después de 5 s la roja"):
    check(not eo.detectar(t, "banda")["aplica"], f"no aplica: '{t}'")
check(eo.detectar("Al presionar I1 la amarilla 5 s, luego la roja 2 s y luego la verde 1 s y se repita el ciclo",
                  "banda")["repite"], "tres etapas con 'luego' y repeticion")
dm = eo.detectar("Con I1 primero prende la verde 5 s, cuando termine la amarilla 2 s y por último la roja 8 s",
                 "maletin")
check([reqs(e) for e in dm["etapas"]] == [[["Q10"]], [["Q11"]], [["Q12"]]], "maletin: Q10, Q11, Q12")

# ---------------------------------------------------------------------------
escenario("2. Etapa sin fin -> pregunta con opciones; la respuesta la completa")
T2 = "primero prende la verde, cuando termine la amarilla 3 s y para cerrar la roja 2 s"
r = generar(T2)
check(r.status == "needs_clarification" and not llamadas, "pregunta sin llamar al modelo")
check([q["campo"] for q in r.questions] == ["etapa_1_fin"] and "Cuando S1 detecte" in r.questions[0]["opciones"],
      "solo la etapa 1, con opciones de tiempo y sensores")
bien2 = {"name": "V-A-R", "banda": {"mover": False},
         "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
             {"salidas": ["Q3"], "avanza": "tiempo", "segundos": 5},
             {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 3},
             {"salidas": ["Q5"], "avanza": "tiempo", "segundos": 2}]}]}
r = generar(T2, bien2, acl={"etapa_1_fin": {"respuesta": "A los 5 s"}})
check(r.status == "ok" and len(llamadas) == 1, f"con la respuesta genera a la primera ({r.status})")
check("etapa_1_fin: A los 5 s" in llamadas[0][-1]["content"], "el modelo recibe la respuesta como dato")

# ---------------------------------------------------------------------------
escenario("3. Verificacion del orden")
check(eo.verificar(cfg(BIEN), eo.detectar(TEXTO, "banda"), "banda") == [], "la secuencia correcta pasa")
p = eo.verificar(cfg(CON_EVENTOS), eo.detectar(TEXTO, "banda"), "banda")
check(len(p) == 1 and "no tiene una secuencia" in p[0], "con eventos: falta la secuencia")
mal = copy.deepcopy(BIEN)
mal["secuencias"][0]["pasos"][1:] = mal["secuencias"][0]["pasos"][2:0:-1]     # roja antes que la pluma
p = eo.verificar(cfg(mal), eo.detectar(TEXTO, "banda"), "banda")
check(p and "etapa 3" in p[0], f"orden invertido detectado ({p})")
mal = copy.deepcopy(BIEN)
mal["secuencias"][0]["pasos"][2]["segundos"] = 3
p = eo.verificar(cfg(mal), eo.detectar(TEXTO, "banda"), "banda")
check(p and "dura 5 s" in p[0], "tiempo distinto detectado")
mal = copy.deepcopy(BIEN)
mal["secuencias"][0]["pasos"][0].update(avanza="tiempo", segundos=5, senal=None)
p = eo.verificar(cfg(mal), eo.detectar(TEXTO, "banda"), "banda")
check(p and "senal S1" in p[0], "senal de fin distinta detectada")
dos = {"banda": M30, "secuencias": [
    {"id": "SQ1", "inicio": "I1", "pasos": [{"motor": ["marcha"], "avanza": "senal", "senal": "S1"},
                                             {"salidas": ["Q8"], "segundos": 2}]},
    {"id": "SQ2", "inicio": "SQ1_FIN", "pasos": [{"salidas": ["Q5"], "segundos": 5}]}]}
check(eo.verificar(cfg(dos), eo.detectar(TEXTO, "banda"), "banda") == [], "SQ2 encadenada con SQ1_FIN cuenta")

# ---------------------------------------------------------------------------
escenario("4. /generar-logica: eventos sin orden -> se pide corregir -> secuencia")
r = generar(TEXTO, CON_EVENTOS, BIEN)
check(r.status == "ok" and len(llamadas) == 2, f"corregido en la segunda vuelta ({r.status} {len(llamadas)})")
sistema = "\n".join(m["content"] for m in llamadas[0] if m["role"] == "system")
check("ACCIONES EN ORDEN" in sistema and "1) «la banda avanza a 30 Hz hasta que S1 detecte»" in sistema,
      "el modelo recibe las etapas numeradas")
check("no tiene una secuencia" in llamadas[1][-1]["content"], "la revision explica el problema de orden")
sim = cargar(r.logic["band"])
sim.pulsar("I1")
sim.correr(1)
check(sim.motor() == "dir1", "I1: la banda avanza")
sim.poner(S1=True)
sim.correr(0.5)
sim.poner(S1=False)
check(sim.motor() == "paro" and sim.q("Q8"), "S1: se detiene y sube la pluma 1")
sim.correr(2)
check(sim.q("Q5") and not sim.q("Q8"), "luego la roja")
sim.correr(5.5)
check(not sim.salidas() and sim.motor() == "paro", "al final todo queda apagado y la banda detenida")

escenario("5. Si insiste en el orden equivocado: alternativa por confirmar (no se carga sola)")
mal = copy.deepcopy(BIEN)
mal["secuencias"][0]["pasos"][1:] = mal["secuencias"][0]["pasos"][2:0:-1]
r = generar(TEXTO, mal, mal, mal)
check(r.status == "needs_confirmation", f"needs_confirmation ({r.status})")
check(any("orden pedido" in x for x in r.analysis["compatibilidad"]["modificado"]), "explica que el orden no quedo")

# ---------------------------------------------------------------------------
escenario("6. motor.arranque + secuencia que mueve la banda: no vuelve a arrancar sola")
con_arranque = dict(copy.deepcopy(BIEN), motor={"arranque": "I1"})
c = cfg(con_arranque)
check("motor" not in c["band"]["engine"], "el arranque repetido se quita (la secuencia manda el motor)")
sim = cargar(c["band"])
sim.pulsar("I1")
sim.correr(1)
sim.poner(S1=True)
sim.correr(0.5)
sim.poner(S1=False)
sim.correr(9)
check(sim.estado()["secuencias"][0]["estado"] == "terminada" and sim.motor() == "paro",
      "al terminar la secuencia la banda sigue detenida")
otro = dict(copy.deepcopy(BIEN), motor={"arranque": "I2"})
avisos = app.avisos_logica_banda(cfg(otro))
check(any("volver a avanzar sola" in a for a in avisos), "con otra senal de arranque se avisa")
sigue = dict(copy.deepcopy(BIEN), motor={"arranque": "SQ1_FIN"})
check(not any("volver a avanzar sola" in a for a in app.avisos_logica_banda(cfg(sigue))),
      "SQ1_FIN (seguir al terminar) no avisa")

# ---------------------------------------------------------------------------
escenario("7. Eventos: detener con otras acciones no reanuda; tiempo propio por accion")
ev = cfg({"banda": M30, "motor": {"arranque": "I1"},
          "eventos": [{"cuando": "S1", "duracion_s": 3, "acciones": {"banda": "detener", "pluma1": "subir"}}]})
sim = cargar(ev["band"])
sim.pulsar("I1")
sim.correr(1)
sim.pulsar("S1")
sim.correr(1)
check(sim.motor() == "paro" and sim.q("Q8"), "S1: se detiene y sube la pluma")
sim.correr(4)
check(sim.motor() == "paro" and not sim.q("Q8"), "la pluma dura 3 s y la banda NO reanuda")
solo = cfg({"banda": M30, "motor": {"arranque": "I1"},
            "eventos": [{"cuando": "S1", "duracion_s": 3, "acciones": {"banda": "detener"}}]})
check(solo["band"]["engine"]["motor"].get("pausa"), "detener solo con duracion_s sigue siendo pausa")
propio = cfg({"banda": M30, "motor": {"arranque": "I1"},
              "eventos": [{"cuando": "S1", "acciones": {"banda": "detener",
                                                        "pluma1": {"estado": "subir", "duracion_s": 2},
                                                        "roja": {"estado": "encender", "duracion_s": 4}}}]})
sim = cargar(propio["band"])
sim.pulsar("I1")
sim.correr(1)
sim.pulsar("S1")
sim.correr(1)
check(sim.q("Q8") and sim.q("Q5") and sim.motor() == "paro", "pluma y roja encendidas, banda detenida")
sim.correr(1.5)
check(not sim.q("Q8") and sim.q("Q5"), "la pluma dura 2 s y la roja sigue")
sim.correr(2)
check(not sim.q("Q5") and sim.motor() == "paro", "la roja dura 4 s; la banda no reanuda")

# ---------------------------------------------------------------------------
escenario("8. Con memorias parecidas la guia y los ejemplos de secuencia no se pierden")
mem = [(0.6, {"instruccion": "Con I1 la banda avanza y S1 baja las plumas", "json_modelo": CON_EVENTOS,
              "validacion": {"fisica": {"estado": "no_realizada"}}})] * 2
msgs, plan = cr.mensajes_sistema_banda_v7(app.SYSTEM_PROMPT_BANDA_V7, TEXTO, mem, en_orden=True)
todo = "\n".join(m["content"] for m in msgs)
check("GUIA RAPIDA" in todo and "EJEMPLOS" in todo, "guia y ejemplos presentes")
check("NO copies su estructura" in todo and todo.count("--- Memoria") == 1, "una sola memoria, como referencia")
msgs, _ = cr.mensajes_sistema_banda_v7(app.SYSTEM_PROMPT_BANDA_V7, "S1 baja las plumas", mem)
check("EJEMPLOS" not in "\n".join(m["content"] for m in msgs), "sin etapas: el flujo de memorias de siempre")
check("VARIAS ACCIONES EN ORDEN" in cr.partir_prompt_v7(app.SYSTEM_PROMPT_BANDA_V7)["nucleo"],
      "las reglas de orden van en el nucleo (siempre)")
check("secuencia" in mv.etiquetas_texto("primero sube la pluma, cuando termine prende la roja y al final para"),
      "'primero / cuando termine / al final' cuentan como secuencia en la memoria")
check(not pb.es_movimiento_basico("Primero la banda avanza y al terminar se detiene"),
      "acciones en orden no se arman como 'solo mover la banda'")

# ---------------------------------------------------------------------------
escenario("9. Maletin: salidas sueltas -> se pide la secuencia en orden")
TM = "Con I1 primero prende la verde 5 s, cuando termine la amarilla 2 s y por último la roja 8 s"
suelto = {"name": "x", "device_profile": "maletin_basico", "system": {"enable": True, "global_stop": None},
          "outputs": [{"output": "Q10", "logic": {"mode": "directo", "source": "I1"},
                       "timer": {"type": "pulse", "preset_s": 5}, "counter": None}]}
seq = {"name": "Semaforo", "device_profile": "maletin_basico", "system": {"enable": True, "global_stop": None},
       "sequence": {"start": "I1", "mode": "once", "reset": None, "steps": [
           {"outputs": ["Q10"], "duration_s": 5}, {"outputs": ["Q11"], "duration_s": 2},
           {"outputs": ["Q12"], "duration_s": 8}]}, "outputs": []}
r = generar(TM, suelto, seq, device="maletin")
check(r.status == "ok" and len(llamadas) == 2 and r.logic.get("sequence"), f"corregido a sequence ({r.status})")
check("ACCIONES EN ORDEN" in "\n".join(m["content"] for m in llamadas[0] if m["role"] == "system"),
      "el maletin tambien recibe las etapas")

# ---------------------------------------------------------------------------
escenario("10. Temperatura baja y configurable")


class _Groq:
    def __init__(self):
        self.kw = None
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.kw = kw
        return None


g, original = _Groq(), app.groq_client
app.groq_client = g
try:
    app._crear_completion([{"role": "user", "content": "x"}], 100)
finally:
    app.groq_client = original
check(g.kw["temperature"] == app.TEMPERATURA == 0.2, "generacion con temperatura 0.2 (GROQ_TEMPERATURE)")

print(f"\nTODAS LAS PRUEBAS DE ACCIONES EN ORDEN PASARON ({OK} comprobaciones).")
