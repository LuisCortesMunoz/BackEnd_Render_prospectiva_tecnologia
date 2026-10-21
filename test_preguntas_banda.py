"""
test_preguntas_banda.py — preguntas de aclaracion con opciones (banda v7).

Corre sin Groq (modelo simulado) y sin PLC:
    python test_preguntas_banda.py

"Quiero mover la banda" -> arranque, direccion, frecuencia y paro con opciones
del hardware real; con todo respondido el JSON se arma sin el modelo, sobre la
logica activa si la hay.
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_preg_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_preg.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import aclaraciones as ac
import app
import lavo_motor as lm
import preguntas_banda as pb

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_preg.json")
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


def generar(texto, *resp, acl=None, contexto=None):
    respuestas[:] = list(resp)
    llamadas.clear()
    req = app.LogicaRequest(texto=texto, device="banda", aclaraciones=acl, contexto=contexto)
    return asyncio.run(app.generar_logica(req))


def campos(r):
    return [ac.clave_pregunta(q) for q in r.questions]


def resp(**kv):
    return {k: {"respuesta": v} for k, v in kv.items()}


def motor(r):
    return r.logic["respuesta_llm"]


TEXTO = "Quiero mover la banda"
TODAS = dict(arranque="I1 (botón verde)", direccion="Derecha", freq_hz="20 Hz", paro="I2 (botón rojo)")

# ---------------------------------------------------------------------------
escenario("1. 'Quiero mover la banda': cuatro preguntas con opciones del hardware")
r = generar(TEXTO)
check(r.status == "needs_clarification" and not llamadas, "pregunta sin llamar al modelo")
check(campos(r) == ["arranque", "direccion", "freq_hz", "paro"], "arranque, direccion, frecuencia y paro")
check(all(len(q["opciones"]) >= 2 for q in r.questions), "todas con opciones (nada que escribir)")
arr = r.questions[0]["opciones"]
check([o.split(" ")[0] for o in arr[:4]] == ["I1", "I2", "S1", "S2"] and "Automático" in arr[-1],
      "arranque: botones y sensores reales + automatico")
check(not any(o.startswith("I3") for q in r.questions for o in q["opciones"]), "nunca ofrece I3 (emergencia)")
check(r.questions[2]["opciones"] == ["10 Hz", "20 Hz", "30 Hz", "40 Hz"], "frecuencias 10..40 Hz")
paro = r.questions[3]["opciones"]
check(pb.TEMPORIZADOR in paro and pb.CONTINUO in paro and "S1 (sensor S1)" in paro, "paro: boton, sensor, temporizador o continuo")

# ---------------------------------------------------------------------------
escenario("2. Todas respondidas: JSON sin modelo")
r = generar(TEXTO, acl=resp(**TODAS))
check(r.status == "ok" and not llamadas, "sin llamar al modelo")
check(r.analysis.get("fuente") == "aclaraciones", "fuente: aclaraciones")
b = r.logic["band"]
check(b["enable"] and b["direction"] == "derecha" and b["freq_hz"] == 20, "derecha a 20 Hz")
check(motor(r)["motor"]["arranque"] == "I1" and motor(r)["motor"]["paro"] == "I2", "arranca con I1 y para con I2")
check(not app.validar_logica_banda(r.logic), "pasa la validacion del PLC")

# ---------------------------------------------------------------------------
escenario("3. Respuestas parciales: no se repregunta lo ya respondido")
r = generar(TEXTO, acl=resp(arranque="S1 (sensor S1)", direccion="Izquierda"))
check(campos(r) == ["freq_hz", "paro"], "solo faltan frecuencia y paro")
r = generar("Mueve la banda a la izquierda a 30 Hz")
check(campos(r) == ["arranque", "paro"], "lo que dice el texto tampoco se pregunta")

# ---------------------------------------------------------------------------
escenario("4. Paro por temporizador: pregunta el tiempo y arma el timer")
a4 = dict(TODAS, paro=pb.TEMPORIZADOR)
r = generar(TEXTO, acl=resp(**a4))
check(campos(r) == ["tiempo_paro_s"] and r.questions[0]["opciones"] == pb.TIEMPOS, "pregunta cuantos segundos")
r = generar(TEXTO, acl=resp(**a4, tiempo_paro_s="10 s"))
t = motor(r)["timers"][0]
check(r.status == "ok" and t["tipo"] == "TON" and t["segundos"] == 10 and motor(r)["motor"]["paro"] == t["id"],
      "TON de 10 s sobre la marcha y paro con ese timer")

# ---------------------------------------------------------------------------
escenario("5. Continuo y automatico")
r = generar(TEXTO, acl=resp(**dict(TODAS, arranque=pb.AUTOMATICO, paro=pb.CONTINUO)))
check(r.status == "ok" and motor(r)["motor"]["arranque"] == "CFG_LISTA", "automatico = CFG_LISTA (arranca con el VFD listo)")
check("paro" not in motor(r)["motor"], "continuo: sin paro (queda el de emergencia I3)")

# ---------------------------------------------------------------------------
escenario("6. Misma senal para arrancar y parar: se repregunta solo el paro")
r = generar(TEXTO, acl=resp(**dict(TODAS, paro="I1 (botón verde)")))
check(r.status == "needs_clarification" and campos(r) == ["paro"] and r.questions[0].get("reemplaza"),
      "vuelve a preguntar el paro (marcado para reemplazar la respuesta)")
check(not any(o.startswith("I1") for o in r.questions[0]["opciones"]), "sin ofrecer la senal del arranque")

# ---------------------------------------------------------------------------
escenario("7. Edicion incremental: se agrega el movimiento a la logica activa")
torreta = {"name": "Verde con I1", "banda": {"mover": False}, "salidas": {"Q3": "I1", "Q4": "T1"},
           "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "S1", "segundos": 2}]}
base = generar("enciende la verde con I1 y la amarilla 2 s con S1", copy.deepcopy(torreta))
ctx = app.ContextoLadder(programa_anterior={"metadata": {"engine_config": base.logic}},
                         historial=["enciende la verde con I1 y la amarilla 2 s con S1"])
r = generar(TEXTO, contexto=ctx)
check(campos(r) == ["arranque", "direccion", "freq_hz", "paro"], "la logica activa no movia la banda: pregunta todo")
r = generar(TEXTO, contexto=ctx, acl=resp(**dict(TODAS, arranque="I2 (botón rojo)", paro=pb.TEMPORIZADOR),
                                          tiempo_paro_s="30 s"))
m = motor(r)
check(r.status == "ok" and not llamadas, "armada sin modelo")
check(m["salidas"] == {"Q3": "I1", "Q4": "T1"} and m["timers"][0] == torreta["timers"][0], "conserva la torreta y su T1")
check(m["timers"][1]["id"] == "T2" and m["motor"]["paro"] == "T2", "el timer nuevo usa un id libre (T2)")
check(r.analysis.get("edicion") is True, "marcada como edicion")
ctx2 = app.ContextoLadder(programa_anterior={"metadata": {"engine_config": r.logic}}, historial=[TEXTO])
r2 = generar(TEXTO, {**m, "analisis": {"compatibilidad": "total"}}, contexto=ctx2)
check(len(llamadas) == 1 and r2.status == "ok", "si la banda ya se mueve no pregunta nada: decide el modelo")

# ---------------------------------------------------------------------------
escenario("8. Preguntas del modelo sin opciones: se completan")
abierta = {"name": "", "banda": {"mover": False}, "preguntas": [
    {"campo": "espera_s", "pregunta": "¿Cuánto tiempo espera?"},
    {"campo": "repeticiones", "pregunta": "¿Cuántas veces se repite?"},
    {"campo": "sensor", "pregunta": "¿Qué sensor detecta la caja?"}]}
r = generar("Cuando llegue una caja, espera y repite la secuencia de la torreta", abierta)
ops = {q["campo"]: q["opciones"] for q in r.questions}
check(ops["espera_s"] == ["3 s", "5 s", "10 s", "30 s"] and ops["repeticiones"][0] == "1"
      and ops["sensor"] == ["S1 (sensor S1)", "S2 (sensor S2)"], "tiempo, repeticiones y sensor con opciones")
check("nunca preguntas abiertas" in app.SYSTEM_PROMPT_BANDA_V7, "el prompt pide opciones siempre")

# ---------------------------------------------------------------------------
escenario("9. Instrucciones que no son solo mover la banda siguen con el modelo")
for t in ("Cuando S1 detecte pausa la banda 3 s", "Mueve la banda y prende la verde",
          "Haz un proceso de empaquetado"):
    check(not pb.es_movimiento_basico(t), f"'{t}' no se intercepta")
check(lm.HW_BANDA["entradas"]["I3"].get("emergencia"), "I3 sigue siendo la emergencia del hardware")

print(f"\nTODAS LAS PRUEBAS DE PREGUNTAS CON OPCIONES PASARON ({OK} comprobaciones).")
