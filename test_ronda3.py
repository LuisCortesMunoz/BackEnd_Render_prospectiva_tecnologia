"""
test_ronda3.py — 3a ronda del banco de pruebas con Groq real (programas tomados
de lo que devolvio Render):

  1. reparaciones tambien en lo que arman los constructores/extractor, y si el
     texto pide cambiar de sentido y lo armado no, lo resuelve el modelo (C6)
  2. "se repite N veces y despues se detiene" se verifica (C9)
  3. JSON ilegible en el ultimo intento: se entrega la ultima version valida
     en lugar de un error (C5 t3)
  4. luz que debia parpadear y quedo fija: se repara con un timer INTERMITENTE (C5)

Corre sin Groq (modelo simulado) y sin PLC (lavo_sim):  python test_ronda3.py
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_r3_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_r3.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import comportamiento as co
import intencion_banda as ib
import lavo_motor as lm
import lavo_sim as ls
import reparaciones_banda as rb

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_r3.json")
app.BANDA_MOTOR_V7 = True
app.CONSTRUCTORES_BANDA = False
app.EXTRACTOR_BANDA = False

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


def sim(resp):
    s = ls.SimV7()
    s.cargar(lm.cfg_desde_respuesta(resp))
    s.correr(0.5)
    return s


# ---------------------------------------------------------------------------
escenario("1. Constructores/extractor: reparaciones y sentido (banco C6)")
T6 = ("I1 arranca la banda hacia la derecha a 25 Hz. Cuando el sensor 2 detecta una caja, la banda invierte el "
      "sentido y avanza hacia la izquierda durante 5 segundos y luego se detiene. I2 detiene todo.")
intencion = ib.analizar(T6, [], {}, None, False)
EXTRACTOR_MAL = {"banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 25.0},
                 "eventos": [{"cuando": "I1", "acciones": {"banda": "avanzar"}},
                             {"cuando": "S2", "duracion_s": 5.0, "acciones": {"banda": "avanzar"}},
                             {"cuando": "I2", "acciones": {"banda": "detener"}}]}
check(app._respuesta_constructor(T6, EXTRACTOR_MAL, [], intencion, fuente="extractor") is None,
      "lo que armo el extractor pierde la inversion: no se entrega (lo resuelve el modelo)")
R6 = {"name": "C6", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 25},
      "motor": {"arranque": "I1", "paro": "I2", "modo": "enclavado"},
      "secuencias": [{"id": "SQ1", "inicio": "S2", "paro": "I2", "modo": "una_vez", "exclusiva": True, "pasos": [
          {"motor": ["invertir"], "avanza": "tiempo", "segundos": 0},
          {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5},
          {"motor": ["paro"], "avanza": "tiempo", "segundos": 0, "siguiente": "fin"}]}]}
r = app._respuesta_constructor(T6, R6, [], intencion, fuente="extractor")
check(r is not None and any("Ajuste automático" in w and "SQ1_CICLO" in w for w in r.warnings),
      "con la inversion: se entrega con el ajuste del paro al terminar la secuencia")
s = ls.SimV7()
s.cargar(r.logic["band"])
s.pulsar("I1"); s.correr(2.5); s.pulsar("S2"); s.correr(9)
check(s.motor() == "paro", "y tras la reversa de 5 s se queda detenida")

# ---------------------------------------------------------------------------
escenario("2. 'Se repite N veces y despues se detiene' (banco C9)")
T9 = ("Al presionar I1 la banda avanza a 30 Hz hasta que el sensor 2 detecta una caja; entonces se detiene 4 "
      "segundos, la pluma 1 sube y la banda vuelve a avanzar. Esto se repite 3 veces y después la banda se detiene.")
check(co.repite_y_para(T9) == {"veces": 3, "senal": "S2"}, "lectura: 3 vueltas con S2")
r9 = co.requisitos([T9, "En vez de 3 veces que sean 2."])
check([(x["veces"]) for x in r9 if x["tipo"] == "para_tras_repetir"] == [2], "'en vez de 3 veces que sean 2' -> 2")
BIEN = {"name": "c9", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30}, "motor": {"paro": "I3"},
        "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I3", "modo": "repetir", "repeticiones": 3,
                        "exclusiva": True, "pasos": [
                            {"motor": ["marcha"], "avanza": "senal", "senal": "I5"},
                            {"salidas": ["Q8"], "motor": ["paro"], "avanza": "tiempo", "segundos": 4},
                            {"motor": ["marcha"], "avanza": "tiempo", "segundos": 0}]}]}
MAL = {"name": "c9", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
       "motor": {"arranque": "I1", "modo": "enclavado", "paro": "I3"},
       "eventos": [{"cuando": "I5", "duracion_s": 4, "acciones": {"banda": "pausar", "pluma1": "subir"}},
                   {"cuando": "SQ1_FIN", "acciones": {"banda": "detener"}}],
       "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I3", "modo": "repetir", "repeticiones": 3,
                       "exclusiva": True, "pasos": [{"avanza": "senal", "senal": "I5", "siguiente": 2},
                                                    {"avanza": "tiempo", "segundos": 0, "siguiente": 1}]}]}
req = [x for x in co.requisitos([T9]) if x["tipo"] in ("para_tras_repetir", "arranca_con")]
check(co.verificar(lm.cfg_desde_respuesta(BIEN), req) == [], "la version de la ronda 1 (para tras 3 vueltas): cumple")
f = co.verificar(lm.cfg_desde_respuesta(MAL), req)
check(f and "NO se queda detenida" in f[0], f"la de la ronda 2 (vuelve a avanzar): se detecta ({f[0][:70]}...)")

# ---------------------------------------------------------------------------
escenario("3. JSON ilegible en el ultimo intento (banco C5 t3)")
T2 = "Con I1 la banda arranca a 30 Hz hacia la derecha y con I2 se detiene."
SIN_PARO = {"name": "x", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
            "motor": {"arranque": "I1", "modo": "enclavado"}, "analisis": {}, "preguntas": []}
respuestas = []


def _llm(msgs):
    x = respuestas.pop(0)
    if isinstance(x, Exception):
        raise x
    return copy.deepcopy(x)


app.llamar_modelo_json = _llm
respuestas[:] = [SIN_PARO, ValueError("Expecting ',' delimiter"), ValueError("Expecting ',' delimiter")]
r = asyncio.run(app.generar_logica(app.LogicaRequest(texto=T2, device="banda")))
check(r.status == "ok" and r.logic["band"]["engine"]["motor"]["arranque"] == "I1",
      "se entrega la ultima version que cargaba (no un error 422)")
check(any("Al simularlo" in w for w in r.warnings), "con el aviso de lo que la simulacion no vio cumplido (I2)")
respuestas[:] = [ValueError("x"), ValueError("x"), ValueError("x")]
try:
    asyncio.run(app.generar_logica(app.LogicaRequest(texto=T2, device="banda")))
    check(False, "sin ninguna version valida deberia fallar")
except Exception as e:
    check(getattr(e, "status_code", None) == 422, "sin ninguna version valida sigue siendo error 422")

# ---------------------------------------------------------------------------
escenario("4. Luz fija que debia parpadear (banco C5)")
T5 = ("Secuencia 1: al presionar I1, la banda avanza 10 segundos, se detiene 3 segundos y vuelve a avanzar 5 "
      "segundos y se detiene. Secuencia 2: cuando el sensor 2 detecta una caja, la luz roja parpadea durante 4 segundos.")
SEQ1 = {"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
    {"motor": ["marcha"], "avanza": "tiempo", "segundos": 10}, {"motor": [], "avanza": "tiempo", "segundos": 3},
    {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5}, {"motor": [], "avanza": "tiempo", "segundos": 0, "siguiente": "fin"}]}


def c5(salidas, timers, luz="Q5"):
    return {"name": "c5", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
            "salidas": salidas, "timers": timers,
            "secuencias": [SEQ1, {"id": "SQ2", "inicio": "S2", "modo": "una_vez", "exclusiva": True, "pasos": [
                {"salidas": [luz], "motor": [], "avanza": "tiempo", "segundos": 4, "siguiente": "fin"}]}]}


def parpadea(resp, q="Q5"):
    s = sim(resp)
    s.pulsar("S2", 0.3)
    v = []
    for _ in range(50):
        s.correr(0.1)
        v.append(q in s.salidas())
    return sum(1 for a, b in zip(v, v[1:]) if b and not a) >= 2


casos = [
    ("fija en el paso, sin timer", c5({}, [])),
    ("timer INTERMITENTE con S2 (instante) + fija en el paso exclusivo (ronda 2)",
     c5({"Q5": "T4"}, [{"id": "T4", "tipo": "INTERMITENTE", "entrada": "S2", "segundos": 0.5, "segundos_off": 0.5}])),
    ("timer INTERMITENTE con un reset que lo apaga + fija en el paso (ronda 1)",
     c5({"Q5": "T1"}, [{"id": "T1", "tipo": "INTERMITENTE", "entrada": "S2", "reset": "T2", "segundos": 0.5,
                        "segundos_off": 0.5}, {"id": "T2", "tipo": "PULSO", "entrada": "S2", "segundos": 4}])),
]
for nombre, resp in casos:
    check(not parpadea(resp), f"{nombre}: NO parpadea")
    r2, av = rb.aplicar(resp, T5, [T5])
    check(av and parpadea(r2) and not any("Q5" in (p.get("salidas") or []) for p in r2["secuencias"][1]["pasos"]),
          f"  reparada: parpadea mientras dura el paso ({av[0][:50]}...)")
AM = "La luz de la secuencia 2 ahora debe ser la amarilla en lugar de la roja."
r2, av = rb.aplicar(c5({}, [], luz="Q4"), T5 + " " + AM, [T5, AM])
check(av and parpadea(r2, "Q4"), "con 'la amarilla en lugar de la roja': repara la amarilla (lo vigente)")
BIEN5 = c5({"Q5": "T1"}, [{"id": "T1", "tipo": "INTERMITENTE", "entrada": "SQ2_PASO1", "segundos": 0.5,
                           "segundos_off": 0.5}])
BIEN5["secuencias"][1]["pasos"][0]["salidas"] = []
check(rb.aplicar(BIEN5, T5, [T5])[1] == [], "si ya parpadea bien, no se toca")
check(rb.aplicar(c5({}, []), "La roja enciende 4 s con S2.", ["La roja enciende 4 s con S2."])[1] == [],
      "si no se pidio parpadeo, no se toca")

print(f"\nTODAS LAS PRUEBAS DE LA RONDA 3 PASARON ({OK} comprobaciones).")
