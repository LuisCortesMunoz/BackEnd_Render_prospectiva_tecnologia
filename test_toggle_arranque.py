"""
test_toggle_arranque.py — etapa 1 de la banda: alternancia con un boton,
arranque con comas y salidas con dos dueños.

Corre sin Groq (modelo simulado) y sin PLC (ST v7 real en lavo_sim):
    python test_toggle_arranque.py

Caso real (4 nov): "Al presionar el boton verde, la banda comienza a avanzar y,
al presionarlo por segunda vez, se detiene". Ningun detector lo entendia (la
coma rompia el de arranque), el prompt no tenia receta y las formas intuitivas
(paro = I1 AND BANDA_CORRIENDO, biestable con memorias) dejan la banda parada.
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_tg_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_tg.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import comportamiento as co
import lavo_motor as lm

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_tg.json")
app.BANDA_MOTOR_V7 = True
app.CONSTRUCTORES_BANDA = False      # estas pruebas son del camino del modelo (los constructores: test_constructores.py)

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


def generar(texto, *resp):
    respuestas[:] = list(resp)
    llamadas.clear()
    return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda")))


def sistema(i=0):
    return "\n".join(m["content"] for m in llamadas[i] if m["role"] == "system")


def ultimo_usuario(i):
    return [m["content"] for m in llamadas[i] if m["role"] == "user"][-1]


A = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [], "simplificado": [],
     "no_soportado": [], "requiere_confirmacion": False}
BANDA = {"mover": True, "direccion": "derecha", "frecuencia_hz": 30}


def prog(**kw):
    return {"name": "Alternancia", "banda": dict(BANDA), "analisis": A, "preguntas": [], **kw}


TOGGLE_BIEN = prog(secuencias=[{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
    {"motor": ["marcha"], "avanza": "flanco", "senal": "I1"}]}])
TOGGLE_AND = prog(motor={"arranque": "I1", "paro": "C1"}, condiciones=[
    {"id": "C1", "op": "AND", "entradas": ["I1", "BANDA_CORRIENDO"], "flanco": "subida"}])
TOGGLE_MEM = prog(memorias=[{"id": "M1", "set": "C1", "reset": "C2", "prioridad": "reset"}],
                  condiciones=[{"id": "C1", "op": "AND", "entradas": ["I1", "NOT M1"], "flanco": "subida"},
                               {"id": "C2", "op": "AND", "entradas": ["I1", "M1"], "flanco": "subida"}],
                  motor={"arranque": "M1", "paro": "NOT M1", "modo": "mientras"})
SOLO_ARRANCA = prog(motor={"arranque": "I1", "paro": "I2"})

TEXTO = ("Al presionar el botón verde, la banda comienza a avanzar y, al presionarlo por segunda vez, se "
         "detiene")


def verif(p, texto=TEXTO):
    return co.verificar(lm.cfg_desde_respuesta(p), co.requisitos([texto]))


# ---------------------------------------------------------------------------
escenario("1. Lectura: arranque con coma y alternancia")
r = co.requisitos([TEXTO])
check(any(x["tipo"] == "arranca_con" and x["senal"] == "I1" for x in r), "arranque con I1 aunque haya coma")
check(any(x["tipo"] == "toggle" and x["senal"] == "I1" for x in r), f"alternancia con I1 ({r})")
for t, b in [("Con I1 la banda avanza y si lo vuelvo a presionar se para", "I1"),
             ("La banda arranca con el boton verde y con el mismo boton se detiene", "I1"),
             ("Que I2 alterne la banda: un pulso la mueve y el siguiente la detiene", "I2"),
             ("Al pulsar I1 la banda avanza; al pulsarlo otra vez se apaga", "I1")]:
    check(co.toggle_en(t) == {"senal": b}, f"alternancia: '{t}'")
for t in ("Con I1 la banda avanza y con I2 se detiene", "Prende la verde y al presionarlo otra vez se apaga",
          "La banda avanza a 30 Hz con I1", "Al presionar otra vez la banda avanza"):
    check(not co.toggle_en(t), f"no es alternancia: '{t}'")
check(any(x["tipo"] == "para_con" and x["senal"] == "I2"
          for x in co.requisitos(["Al presionar el boton rojo, la banda se detiene"])), "paro con coma (boton rojo)")
check(not any(x["tipo"] == "para_con" for x in co.requisitos(["Con I1 la banda arranca y con I2 se detiene"])
              if x["senal"] == "I1"), "'arranca con I1 y con I2 se detiene' no hace a I1 paro")

# ---------------------------------------------------------------------------
escenario("2. Simulando el PLC: solo la receta de alternancia funciona")
check(verif(TOGGLE_BIEN) == [], "secuencia de 1 paso que espera otro flanco de I1: alterna")
for nombre, p in (("paro = I1 AND BANDA_CORRIENDO", TOGGLE_AND), ("biestable con memorias", TOGGLE_MEM),
                  ("solo arranque I1 / paro I2", SOLO_ARRANCA)):
    f = verif(p)
    check(f and any("alternar" in x for x in f), f"{nombre}: se detecta ({[x[:70] for x in f]})")

# ---------------------------------------------------------------------------
escenario("3. De punta a punta: receta en el prompt y correccion con lo simulado")
r = generar(TEXTO, TOGGLE_AND, TOGGLE_BIEN)
check("ALTERNANCIA con I1" in sistema(0) and "\"avanza\":\"flanco\",\"senal\":\"I1\"" in sistema(0),
      "la receta de alternancia va en el prompt")
check(len(llamadas) == 2 and "alternar" in ultimo_usuario(1), "segunda vuelta con lo que se observo")
check(r.status == "ok" and r.analysis["comportamiento"]["fallas"] == [], "la version entregada alterna")
check(any("toggle" in p for p in r.analysis["comportamiento"]["probado"]), "y queda registrado que se probo")

# ---------------------------------------------------------------------------
escenario("4. Una salida con dos dueños: 'salidas' y una secuencia exclusiva")
dos = prog(salidas={"Q3": "SIEMPRE"}, secuencias=[{"id": "SQ1", "inicio": "S1", "modo": "una_vez", "pasos": [
    {"salidas": ["Q4"], "segundos": 2}, {"salidas": ["Q3"], "segundos": 2}]}])
av = lm.validar_motor(lm.cfg_desde_respuesta(dos)["band"])[1]
check(any("Q3 esta en 'salidas' y en la secuencia SQ1" in a for a in av), f"se avisa ({av})")
uno = prog(salidas={"Q3": "SIEMPRE"}, secuencias=[{"id": "SQ1", "inicio": "S1", "modo": "una_vez", "pasos": [
    {"salidas": ["Q4"], "segundos": 2}]}])
check(not any("en 'salidas' y en la secuencia" in a for a in lm.validar_motor(lm.cfg_desde_respuesta(uno)["band"])[1]),
      "sin aviso si la secuencia no usa esa salida")

print(f"\nTODAS LAS PRUEBAS DE ALTERNANCIA Y ARRANQUE PASARON ({OK} comprobaciones).")
