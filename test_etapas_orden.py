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
_llamar_real = app.llamar_modelo_json


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
check(g.kw.get("reasoning_effort") == "medium", "gpt-oss con reasoning_effort acotado")

# ---------------------------------------------------------------------------
escenario("11. Caso real: S1 / S2 con plumas, conteo de 3, 5 s y paro")
TR = ("Al presionar el pulsador I1, la banda transportadora se pone en marcha a una frecuencia de 20 Hz. "
      "Cuando el Sensor 1 detecta un objeto, la pluma 1 se eleva y la pluma 2 desciende. Posteriormente, al "
      "activarse el Sensor 2, la pluma 2 se eleva y la pluma 1 desciende, registrando un conteo. Una vez que el "
      "Sensor 2 acumula 3 detecciones, la banda continúa avanzando durante 5 segundos y luego se detiene "
      "automáticamente; a continuación, la pluma 1 se eleva, la pluma 2 desciende y el contador se reinicia a "
      "cero en ese orden.")
d = eo.detectar(TR, "banda")
E = d["etapas"]
check(len(E) == 6 and d["conteo"], f"seis etapas y un conteo ({len(E)})")
check(reqs(E[1]) == [["Q8"], ["Q7"], "marcha"] and E[1]["entra_con"] == "S1",
      f"2: con S1 pluma 1 SUBE (Q8) y pluma 2 baja (Q7), la banda sigue ({reqs(E[1])})")
check(reqs(E[2]) == [["Q6"], ["Q9"], "marcha"] and E[2]["entra_con"] == "S2",
      f"3: con S2 pluma 2 sube (Q6) y pluma 1 baja (Q9) ({reqs(E[2])})")
check(reqs(E[3]) == ["marcha"] and E[3]["tiempos"] == [5.0], "4: sigue avanzando 5 s")
check(reqs(E[4]) == ["paro"] and reqs(E[5]) == [["Q8"], ["Q7"]], "5: se detiene; 6: pluma 1 sube, pluma 2 baja")
check(not eo.preguntas(d, "banda"), "no pregunta nada (paro inmediato, plumas con su recorrido)")
check("CONTEO" in eo.bloque_modelo(d, "banda"), "el modelo recibe como armar el ciclo con el contador")

# La secuencia que pide la instruccion, ejecutada en el ST real.
CICLO = {"name": "Plumas S1/S2, 3 cajas", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 20},
         "contadores": [{"id": "CT1", "entrada": "S2", "reset": "SQ1_FIN", "preset": 3, "modo": "una_vez"}],
         "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
             {"motor": ["marcha"], "avanza": "senal", "senal": "S1", "alternativa": {"senal": "CT1", "ir_a": 5}},
             {"motor": ["marcha"], "salidas": ["Q8", "Q7"], "avanza": "tiempo", "segundos": 2},
             {"motor": ["marcha"], "avanza": "senal", "senal": "S2"},
             {"motor": ["marcha"], "salidas": ["Q6", "Q9"], "avanza": "tiempo", "segundos": 2, "siguiente": 1},
             {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5},
             {"salidas": ["Q8", "Q7"], "avanza": "tiempo", "segundos": 2}]}]}
c = cfg(CICLO)
check(not app.validar_logica_banda(c), f"cargable ({app.validar_logica_banda(c)})")
check(eo.verificar(c, d, "banda") == [], f"respeta el orden ({eo.verificar(c, d, 'banda')})")
sim = cargar(c["band"])
sim.pulsar("I1")
sim.correr(1.5)
check(sim.motor() == "dir1", "I1: la banda avanza")
for caja in (1, 2, 3):
    sim.pulsar("S1")
    sim.correr(0.5)
    check(sim.q("Q8") and sim.q("Q7") and sim.motor() == "dir1", f"caja {caja}, S1: pluma 1 sube, pluma 2 baja, sigue")
    sim.correr(2)
    sim.pulsar("S2")
    sim.correr(0.5)
    check(sim.q("Q6") and sim.q("Q9") and sim.motor() == "dir1", f"caja {caja}, S2: pluma 2 sube, pluma 1 baja")
    sim.correr(2.5)
check(sim.motor() == "dir1" and sim.estado()["secuencias"][0]["paso"] == 5, "tras 3 detecciones: 5 s mas de banda")
sim.correr(5)
check(sim.motor() == "paro" and sim.q("Q8") and sim.q("Q7"), "se detiene; pluma 1 sube y pluma 2 baja")
sim.correr(3)
check(not sim.salidas() and sim.motor() == "paro", "termina: banda detenida, plumas quietas")
est = sim.estado()
check(est["contadores_acc"][0] == 0 and not est["contadores"][0], f"el contador vuelve a cero ({est['contadores_acc'][0]})")

# ---------------------------------------------------------------------------
escenario("12. Respuesta vacia de gpt-oss (se gasto los tokens razonando)")


class _Resp:
    def __init__(self, texto, fin, ts):
        self.choices = [type("C", (), {"message": type("M", (), {"content": texto})(), "finish_reason": fin})()]
        self.usage = type("U", (), {"prompt_tokens": 5000, "completion_tokens": ts})()


class _GroqVacio:
    def __init__(self, salidas):
        self.salidas, self.kws = list(salidas), []
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.kws.append(kw)
        x = self.salidas.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


import json as _json
import httpx
from groq import APIStatusError


def _429(segundos):
    r = httpx.Response(429, headers={"retry-after": str(segundos)}, request=httpx.Request("POST", "https://x"))
    return APIStatusError("rate limit", response=r, body=None)


app.groq_client = _GroqVacio([_Resp("", "length", 2400), _Resp(_json.dumps(BIEN), "stop", 900)])
try:
    out = _llamar_real([{"role": "user", "content": "x"}])
    gq = app.groq_client
finally:
    app.groq_client = original
check(out["name"] == BIEN["name"], "la segunda llamada devuelve el JSON")
check([k.get("reasoning_effort") for k in gq.kws] == ["medium", "low"], "reintenta con reasoning_effort=low")

app.groq_client = _GroqVacio([_Resp("", "length", 2400), _429(0.01), _Resp(_json.dumps(BIEN), "stop", 900)])
try:
    out = _llamar_real([{"role": "user", "content": "x"}])
finally:
    app.groq_client = original
check(out["name"] == BIEN["name"], "si Groq pide esperar (429 con retry-after corto), espera y reintenta")

app.groq_client = _GroqVacio([_Resp("", "length", 2400), _Resp("", "length", 2400)])
try:
    _llamar_real([{"role": "user", "content": "x"}])
    msg = ""
except app.SinRespuesta as e:
    msg = str(e)
finally:
    app.groq_client = original
check("sin espacio" in msg, "dos vacias: error claro (no 'JSON invalido' sin detalle)")


def _sin_respuesta(msgs):
    llamadas.append(msgs)
    raise app.SinRespuesta("El modelo se quedo sin espacio para responder.")


app.llamar_modelo_json = _sin_respuesta
llamadas.clear()
try:
    asyncio.run(app.generar_logica(app.LogicaRequest(texto=TEXTO, device="banda")))
    st = None
except app.HTTPException as e:
    st, det = e.status_code, e.detail
app.llamar_modelo_json = _llm
check(st == 422 and "sin espacio" in det and len(llamadas) == 1,
      f"la auto-revision no repite la misma llamada ({st}, {len(llamadas)} llamadas)")

escenario("13. Dos secuencias independientes en la misma instruccion")
DOS = ("Quiero que al presionar I1 se encienda la luz amarilla durante 5 segundos, luego la roja durante 2 "
       "segundos y luego la verde durante 1 segundo y se repita el ciclo. Quiero que cuando el sensor 2 "
       "detecte, levante la pluma 1 y la pluma 2 y haga la secuencia de que se encienda la luz roja durante "
       "3 segundos, luego la verde durante 1 y luego la amarilla durante 5 hasta que yo detenga todo. Con el "
       "boton I2 se detiene todo.")
d = eo.detectar(DOS, "banda", {})
check(d["aplica"] and [g["etapas"] for g in d["grupos"]] == [[1, 2, 3], [4, 5, 6, 7]],
      f"se separan en dos secuencias: {[g['etapas'] for g in d['grupos']]}")
check(d["grupos"][0]["repite"] and not d["grupos"][1]["repite"], "el ciclo se repite solo en la primera")
check(eo.preguntas(d, "banda") == [], f"no pregunta lo que ya se dijo: {eo.preguntas(d, 'banda')}")
e6, e7 = d["etapas"][5], d["etapas"][6]
check(e6["tiempos"] == [1.0], f"'durante 1' sin unidad = 1 s ({e6['tiempos']})")
check(e7["hasta_paro"] and all(r["tipo"] != "motor" for r in e7["requisitos"]),
      "'hasta que yo detenga todo' = sigue hasta el paro, no es un paso con la banda detenida")
check(reqs(d["etapas"][3]) == [["Q8"], ["Q6"]], f"las dos plumas suben ({reqs(d['etapas'][3])})")
check("SQ2" in eo.bloque_modelo(d, "banda") and "INDEPENDIENTES" in eo.bloque_modelo(d, "banda"),
      "el modelo recibe una secuencia por bloque")
dos_band = {"enable": False, "engine": {"version": 7,
    "memorias": [{"id": "M1", "set": "S2", "reset": "I2", "prioridad": "reset"}],
    "condiciones": [{"id": "C1", "op": "OR", "entradas": ["M1", "I2"], "flanco": "nivel"}],
    "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "S2", "reset": None, "segundos": 2.0}],
    "salidas": {"Q6": "T1", "Q8": "T1"},
    "secuencias": [
        {"id": "SQ1", "inicio": "I1", "paro": "C1", "modo": "infinita", "pasos": [
            {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 5},
            {"salidas": ["Q5"], "avanza": "tiempo", "segundos": 2},
            {"salidas": ["Q3"], "avanza": "tiempo", "segundos": 1}]},
        {"id": "SQ2", "inicio": "M1", "paro": "I2", "modo": "infinita", "pasos": [
            {"salidas": ["Q5"], "avanza": "tiempo", "segundos": 3},
            {"salidas": ["Q3"], "avanza": "tiempo", "segundos": 1},
            {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 5}]}]}}
check(eo.verificar({"band": dos_band}, d, "banda") == [],
      f"cada secuencia se compara con la suya: {eo.verificar({'band': dos_band}, d, 'banda')}")
mal = copy.deepcopy(dos_band)
mal["engine"]["secuencias"][1]["pasos"][0]["segundos"] = 9
check(len(eo.verificar({"band": mal}, d, "banda")) == 1, "un tiempo mal en SQ2 se detecta")

escenario("14. Regresar = invertir; 'cuando termine el ciclo' no parte la secuencia")
d = eo.detectar("Primero avanza la banda a la derecha a 30 Hz, cuando el sensor 1 detecte se detiene "
                "3 segundos y luego regresa a la izquierda", "banda", {})
check(len(d["etapas"]) == 3 and d["etapas"][2]["requisitos"][0]["accion"] == "invertir",
      "la tercera etapa (regresa) no se pierde")
d = eo.detectar("Primero avanza la banda, cuando termine el ciclo sube la pluma 1 y al final prende la roja 5 s",
                "banda", {})
check(len(d["grupos"]) == 1, "una sola secuencia")

escenario("15. 'Secuencia 1 / Secuencia 2', 'la primera / la segunda', 'Ademas': secuencias aparte")
grupos = lambda t: [g["etapas"] for g in eo.detectar(t, "banda", {})["grupos"]]
SEC12 = ("Secuencia 1: con I1 avanza la banda 10 segundos y luego se detiene. Secuencia 2: cuando el sensor 2 "
         "detecte, sube la pluma 2 y despues prende la amarilla 4 segundos")
d = eo.detectar(SEC12, "banda", {})
check([g["etapas"] for g in d["grupos"]] == [[1, 2], [3, 4]], f"'Secuencia 1/2' se separan: {grupos(SEC12)}")
check(d["etapas"][0]["texto"] == "con I1 avanza la banda 10 segundos", "el marcador no queda en el texto de la etapa")
check(d["etapas"][2]["entra_con"] == "S2", "la segunda arranca con S2")
check(eo.preguntas(d, "banda") == [], f"sin preguntas: {eo.preguntas(d, 'banda')}")
DOS_ORD = ("Quiero dos secuencias: la primera, con I1 la banda avanza hasta que S1 detecte. La segunda, cuando S2 "
           "detecte prende la verde 3 segundos y luego la roja 2 segundos")
check(grupos(DOS_ORD) == [[1], [2, 3]], f"'la primera / la segunda' (antes no se detectaba): {grupos(DOS_ORD)}")
ADEMAS = ("Primero prende la verde 2 s y luego la roja 2 s. Ademas, cuando S2 detecte sube la pluma 1 y luego "
          "prende la amarilla 3 s")
check(grupos(ADEMAS) == [[1, 2], [3, 4]], f"'Ademas,' abre otra secuencia: {grupos(ADEMAS)}")
check(grupos("Secuencia uno: prende la verde 2 s y luego la roja 1 s. Otra secuencia: con S1 sube la pluma 1 "
             "y luego prende la amarilla 2 s") == [[1, 2], [3, 4]], "'otra secuencia' tambien")
# Lo que NO debe partir
check(grupos("Primero la verde 2 segundos, luego la amarilla 2 segundos y luego la roja 2 segundos, repite la "
             "secuencia 3 veces") == [[1, 2, 3]], "'repite la secuencia 3 veces' no abre otra")
check(grupos("Al presionar I1 enciende la verde 2 s y luego la roja 2 s, despues inicia otra secuencia de que la "
             "amarilla prenda 4 s y luego la verde 1 s") == [[1, 2, 3, 4]],
      "'despues inicia otra secuencia' va a continuacion (la misma)")
check(grupos("Primero sube la primera pluma, luego la segunda pluma y al final prende la roja 3 segundos")
      == [[1, 2, 3]], "'la segunda pluma' no es otra secuencia")
check(not eo.detectar("Cuando S1 detecte una pieza la banda se detiene, sube la pluma 1, espera 3 segundos, baja "
                      "la pluma y la banda sigue. Ademas, cada vez que S2 cuente 5 piezas prende la roja",
                      "banda", {})["aplica"], "'Ademas' solo no activa el modo de orden (frase con puras comas)")
check(grupos("Primero la banda avanza hasta que S1 detecte; ademas, sube la pluma 1. Luego prende la roja 3 s y "
             "al final la verde 2 s") == [[1, 2, 3]],
      "'; ademas, sube la pluma' es lo que pasa en ese paso, no otra secuencia")
check(grupos("Primero prende la verde 2 s y luego la roja 2 s. Ademas, sube la pluma 1 y luego prende la "
             "amarilla 3 s") == [[1, 2, 3]], "'Ademas,' sin disparador (cuando/al presionar/con I1) no separa")
check(grupos(DOS) == [[1, 2, 3], [4, 5, 6, 7]], "el caso del escenario 13 no cambia")

# De punta a punta: el modelo recibe SQ1 y SQ2 y la respuesta pasa la verificacion y carga en el ST
bloque = eo.bloque_modelo(d, "banda")
check("SQ1" in bloque and "SQ2" in bloque and "INDEPENDIENTES" in bloque, "el modelo recibe dos bloques")
SEC12_BIEN = {"name": "Dos secuencias", "banda": M30,
              "secuencias": [
                  {"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
                      {"motor": ["marcha"], "avanza": "tiempo", "segundos": 10},
                      {"salidas": [], "avanza": "tiempo", "segundos": 0.5}]},
                  {"id": "SQ2", "inicio": "S2", "modo": "una_vez", "pasos": [
                      {"salidas": ["Q6"], "avanza": "tiempo", "segundos": 2},
                      {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 4}]}],
              "analisis": {"intencion": "dos secuencias", "compatibilidad": "total", "conservado": [],
                           "modificado": [], "simplificado": [], "no_soportado": [], "requiere_confirmacion": False},
              "preguntas": []}
r = generar(SEC12, SEC12_BIEN)
check(r.status == "ok" and len(llamadas) == 1, f"genera a la primera ({r.status}, {len(llamadas)} llamadas)")
check("SECUENCIA SQ2" in "\n".join(m["content"] for m in llamadas[0] if m["role"] == "system"),
      "el prompt (system) lleva la segunda secuencia")
check(eo.verificar(cfg(SEC12_BIEN), d, "banda") == [], f"verifica: {eo.verificar(cfg(SEC12_BIEN), d, 'banda')}")
cargar(cfg(SEC12_BIEN)["band"])

print(f"\nTODAS LAS PRUEBAS DE ACCIONES EN ORDEN PASARON ({OK} comprobaciones).")
