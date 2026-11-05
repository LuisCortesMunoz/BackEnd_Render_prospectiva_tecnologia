"""
test_constructores.py — etapa 3: armar la logica de la banda sin el modelo.

Corre sin Groq (modelo simulado) y sin PLC (ST v7 real en lavo_sim):
    python test_constructores.py

Lo que se entiende sin ambiguedad (motor, alternancia, conteo, torreta,
plumas) se arma con recetas probadas, se valida y se simula. Lo que no se
entiende va al modelo como siempre (con las piezas ya armadas de las frases
que si se entendieron).
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_cons_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_cons.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import comportamiento as co
import constructores_banda as cb
import intencion_banda as ib
import lavo_motor as lm
import lavo_sim as ls

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_cons.json")
app.BANDA_MOTOR_V7 = True
app.CONSTRUCTORES_BANDA = True
app.EXTRACTOR_BANDA = False          # el extractor con IA se prueba en test_extractor.py

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


respuestas, llamadas = [], []


def _llm(msgs):
    llamadas.append(copy.deepcopy(msgs))
    if not respuestas:
        raise AssertionError("el modelo fue llamado sin respuesta preparada")
    return copy.deepcopy(respuestas.pop(0))


app.llamar_modelo_json = _llm


def generar(texto, *resp, acl=None, prev=None):
    respuestas[:] = list(resp)
    llamadas.clear()
    ctx = None
    if prev is not None:
        c = lm.cfg_desde_respuesta(prev)
        c["respuesta_llm"] = prev
        ctx = app.ContextoLadder(programa_anterior={"metadata": {"name": "x", "engine_config": c}}, historial=["x"])
    return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", aclaraciones=acl,
                                                            contexto=ctx)))


def armar(t, acl=None):
    return cb.construir(t, ib.analizar(t, acl=acl), acl or {})


def sim_de(t):
    c = lm.cfg_desde_respuesta(armar(t)["resp"])
    s = ls.SimV7()
    s.cargar(c["band"])
    s.correr(0.5)
    return c, s


corre = lambda s: s.motor() in ("dir1", "dir2")

TOGGLE = ("Al presionar el botón verde, la banda comienza a avanzar a 30 Hz y, al presionarlo por segunda vez, se "
          "detiene")

# ---------------------------------------------------------------------------
escenario("1. Que se arma, que se pregunta y que va al modelo")
arma = ["Con I1 la banda avanza a 30 Hz y con I2 se detiene", TOGGLE,
        "Con I1 la banda avanza a 20 Hz hacia la izquierda. Cuando S2 cuente 3 cajas prende la roja 5 segundos y "
        "que el contador se reinicie solo",
        "Con I1 la banda avanza a 30 Hz. Cuando S1 detecte una caja sube la pluma 1 durante 3 segundos",
        "Con I1 la banda avanza a 30 Hz y con I2 se detiene. La verde se enciende mientras la banda avanza",
        "Con I1 la banda avanza a 30 Hz. Cuando el sensor 2 detecte, la banda se detiene 5 segundos",
        "Con I1 la banda avanza a 30 Hz y se detiene sola a los 10 segundos",
        "Cuando S1 detecte prende la roja parpadeando 4 segundos",
        "Con I1 prende la verde y con I2 se apaga la verde",
        "Al presionar el pulsador I1 la banda avanza a 25 Hz",
        # los lentes pegan la opcion elegida al final del texto
        "Con I1 la banda avanza y con I2 se detiene Derecha 30 Hz",
        "Prende la amarilla con el boton verde Solo mientras se presiona I1",
        "Con I2 sube la pluma 1 mientras se mantiene presionado"]
for t in arma:
    r = armar(t)
    check(r["resp"] is not None and not r["pregunta"], f"se arma: '{t[:60]}'")
    check(app.validar_logica_banda(lm.cfg_desde_respuesta(r["resp"])) == [], "  y valida en el PLC")
r = armar("Con I1 la banda avanza y con I2 se detiene Izquierda 20 Hz")
check(r["resp"]["banda"]["direccion"] == "izquierda" and r["resp"]["banda"]["frecuencia_hz"] == 20,
      "'Izquierda 20 Hz' pegado por los lentes: izquierda a 20 Hz")
check(armar("Prende la amarilla con el boton verde solo mientras se presiona")["resp"]["salidas"] == {"Q4": "I1"},
      "'mientras se presiona' sin nombrar el boton: el del disparo (I1)")
modelo = ["Con I1 prende la verde",                                         # ¿mientras o se queda? ambiguo
          "Prende la roja con I1 mientras se presiona I2",                  # ¿I1 o I2?
          "Cuando S1 detecte prende la roja 3 s y luego la verde 2 s",      # orden: secuencia
          "Haz que la banda haga una danza bonita con I1",
          "Primero con I1 la banda avanza hasta que S1 detecte, luego sube la pluma 1 y al final prende la roja 3 s"]
for t in modelo:
    r = armar(t)
    check(r["resp"] is None and not r["pregunta"], f"va al modelo: '{t[:60]}' ({r['motivo'][:60]})")
r = armar("Con I1 la banda avanza")
check(r["resp"] is None and r["pregunta"]["campo"] == "freq_hz", "sin Hz: se pregunta con opciones")
r = armar("Con I1 la banda avanza", acl={"freq_hz": {"pregunta": "Hz", "respuesta": "40 Hz"}})
check(r["resp"]["banda"]["frecuencia_hz"] == 40, "con la respuesta se arma a 40 Hz")

# ---------------------------------------------------------------------------
escenario("2. Lo armado hace lo pedido (ST v7 simulado)")
c, s = sim_de(TOGGLE)
v = []
for _ in range(3):
    s.pulsar("I1", 0.3)
    s.correr(2)
    v.append(corre(s))
check(v == [True, False, True], f"alternancia: marcha, paro, marcha ({v})")
c, s = sim_de("Con I1 la banda avanza a 20 Hz hacia la izquierda. Cuando S2 cuente 3 cajas prende la roja 5 "
              "segundos y que el contador se reinicie solo")
s.pulsar("I1")
s.correr(2)
check(s.motor() == "dir2", "conteo: avanza a la izquierda")
roja = []
for _ in range(6):
    s.pulsar("I5", 0.3)
    s.correr(0.3)
    roja.append(s.q("Q5"))
    if s.q("Q5"):
        s.correr(5.5)
check(roja == [False, False, True, False, False, True], f"la roja en la 3a y la 6a caja ({roja})")
c, s = sim_de("Con I1 la banda avanza a 30 Hz. Cuando S1 detecte una caja sube la pluma 1 durante 3 segundos")
s.pulsar("I1")
s.correr(1)
s.pulsar("I4")
s.correr(0.3)
q8 = s.q("Q8")
s.correr(3)
check(q8 and not s.q("Q8"), "pluma 1 sube 3 s con S1")
c, s = sim_de("Con I1 la banda avanza a 30 Hz. Cuando el sensor 2 detecte, la banda se detiene 5 segundos")
s.pulsar("I1")
s.correr(2)
s.pulsar("I5")
s.correr(1.5)
parada = not corre(s)
s.correr(5)
check(parada and corre(s), "pausa de 5 s con S2 y sigue")
c, s = sim_de("Con I1 la banda avanza a 30 Hz y con I2 se detiene. La verde se enciende mientras la banda avanza")
s.pulsar("I1")
s.correr(2)
on = s.q("Q3")
s.pulsar("I2")
s.correr(2)
check(on and not s.q("Q3"), "verde solo mientras la banda avanza")
c, s = sim_de("Cuando S1 detecte prende la roja parpadeando 4 segundos")
s.pulsar("I4", 0.2)
v = []
for _ in range(45):
    s.correr(0.1)
    v.append(s.q("Q5"))
s.correr(1)
check(sum(1 for a, b in zip(v, v[1:]) if a != b) >= 4 and not s.q("Q5"), "roja parpadea 4 s y se apaga")
c, s = sim_de("Prende la amarilla con el boton verde Solo mientras se presiona I1")
s.poner(I1=True)
s.correr(0.5)
on = s.q("Q4")
s.poner(I1=False)
s.correr(0.5)
check(on and not s.q("Q4"), "amarilla solo mientras se presiona I1")
c, s = sim_de("Con I1 prende la verde y con I2 se apaga la verde")
s.pulsar("I1")
s.correr(3)
on = s.q("Q3")
s.pulsar("I2")
s.correr(0.5)
check(on and not s.q("Q3"), "verde se queda hasta I2")

# ---------------------------------------------------------------------------
escenario("3. De punta a punta en /generar-logica")
r = generar(TOGGLE)
check(r.status == "ok" and not llamadas, f"alternancia: se entrega SIN llamar al modelo ({r.status})")
check(r.analysis["fuente"] == "constructor" and r.analysis["comportamiento"]["fallas"] == [],
      "fuente = constructor y la simulacion cumple")
check("toggle I1" in r.analysis["comportamiento"]["probado"], "se probo la alternancia")
r = generar("Con I1 la banda avanza y con I2 se detiene")
check(r.status == "needs_clarification" and "freq_hz" in [q["campo"] for q in r.questions] and not llamadas,
      "sin Hz: pregunta direccion y Hz (flujo de movimiento de siempre, sin llamar al modelo)")
r = generar("Con I1 la banda avanza y con I2 se detiene",
            acl={"freq_hz": {"pregunta": "Hz", "respuesta": "30 Hz"},
                 "direccion": {"pregunta": "dir", "respuesta": "Izquierda"}})
check(r.status == "ok" and not llamadas and r.logic["band"]["freq_hz"] == 30
      and r.logic["band"]["direction"] == "izquierda", "con las respuestas lo arma el constructor")

PARCIAL = ("Con I1 la banda avanza a 30 Hz y con I2 se detiene. Haz que la torreta haga un baile divertido")
MODELO_OK = {"name": "x", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
             "motor": {"arranque": "I1", "paro": "I2"},
             "analisis": {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [],
                          "simplificado": [], "no_soportado": [], "requiere_confirmacion": False}, "preguntas": []}
r = generar(PARCIAL, MODELO_OK)
sistema = "\n".join(m["content"] for m in llamadas[0] if m["role"] == "system")
check(len(llamadas) == 1 and "PIEZAS YA ARMADAS" in sistema and "\"banda\":\"avanzar\"" in sistema,
      "parte entendida: el modelo recibe las piezas ya armadas")
r = generar("Agrega que con I1 la banda avanza a 30 Hz", MODELO_OK, prev=MODELO_OK)
check(len(llamadas) == 1, "con logica activa (edicion) lo resuelve el modelo como siempre")
app.CONSTRUCTORES_BANDA = False
r = generar(TOGGLE, dict(armar(TOGGLE)["resp"]))
check(len(llamadas) == 1 and r.status == "ok", "LAVO_CONSTRUCTORES=0 apaga los constructores (va al modelo)")
app.CONSTRUCTORES_BANDA = True

print(f"\nTODAS LAS PRUEBAS DE CONSTRUCTORES PASARON ({OK} comprobaciones).")
