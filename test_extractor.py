"""
test_extractor.py — etapa 5: reglas comunes, pruebas por regla y extractor con IA.

Corre sin Groq (extractor y modelo simulados) y sin PLC (ST v7 real en lavo_sim):
    python test_extractor.py

Cuando el lector de patrones no entiende toda la instruccion, una llamada
corta traduce la frase a REGLAS; los constructores las arman y se prueba cada
regla simulando. El modelo de generacion solo entra si no se pueden armar.
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_ext_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_ext.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import constructores_banda as cb
import extractor_banda as eb
import lavo_motor as lm
import reglas_banda as rb

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_ext.json")
app.BANDA_MOTOR_V7 = True
app.CONSTRUCTORES_BANDA = True
app.EXTRACTOR_BANDA = True

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


respuestas, llamadas = [], []          # modelo de generacion
ext_resp, ext_llamadas = [], []        # extractor


def _llm(msgs):
    llamadas.append(copy.deepcopy(msgs))
    if not respuestas:
        raise AssertionError("el modelo fue llamado sin respuesta preparada")
    return copy.deepcopy(respuestas.pop(0))


def _ext(msgs):
    ext_llamadas.append(copy.deepcopy(msgs))
    if not ext_resp:
        raise AssertionError("el extractor fue llamado sin respuesta preparada")
    r = ext_resp.pop(0)
    if isinstance(r, Exception):
        raise r
    return copy.deepcopy(r)


app.llamar_modelo_json = _llm
app.llamar_extractor_json = _ext


def generar(texto, extractor=(), modelo=(), acl=None):
    ext_resp[:] = list(extractor)
    respuestas[:] = list(modelo)
    llamadas.clear()
    ext_llamadas.clear()
    return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", aclaraciones=acl)))


def sistema(i=0):
    return "\n".join(m["content"] for m in llamadas[i] if m["role"] == "system")


def ultimo_usuario(i):
    return [m["content"] for m in llamadas[i] if m["role"] == "user"][-1]


A = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [], "simplificado": [],
     "no_soportado": [], "requiere_confirmacion": False}
TXT = ("Dale arranque con el verde a 25 Hz y que la roja avise tres segundos cada vez que el primer sensor "
       "vea algo")
REGLAS = {"secuencia": False,
          "reglas": [{"cuando": {"boton": "I1"}, "hacer": [{"recurso": "banda", "accion": "avanzar"}],
                      "fragmento": "Dale arranque con el verde"},
                     {"cuando": {"sensor": "S1"}, "hacer": [{"recurso": "roja", "accion": "encender",
                                                              "duracion_s": 3}],
                      "fragmento": "que la roja avise tres segundos cada vez que el primer sensor vea algo"}],
          "motor": {"hz": 25, "direccion": None}, "alternancia": None, "paro_solo_s": None, "conteo": None,
          "dudas": []}

# ---------------------------------------------------------------------------
escenario("1. Validar las reglas (lo que venga de la IA)")
r = rb.validar(REGLAS, TXT)
check(len(r["reglas"]) == 2 and r["motor"]["hz"] == 25, "reglas buenas: se aceptan y normalizan")
for nombre, cambio, palabra in [
        ("fragmento inventado", lambda d: d["reglas"][1].update(fragmento="que la pluma 2 suba al final"),
         "no se encuentra"),
        ("I3 como disparo", lambda d: d["reglas"][0].update(cuando={"boton": "I3"}), "I3"),
        ("Hz fuera de rango", lambda d: d["motor"].update(hz=500), "1..327"),
        ("recurso que no existe", lambda d: d["reglas"][1]["hacer"][0].update(recurso="sirena"), "sirena"),
        ("pluma que enciende", lambda d: d["reglas"][1]["hacer"][0].update(recurso="pluma1"), "pluma1"),
        ("duracion negativa", lambda d: d["reglas"][1]["hacer"][0].update(duracion_s=-2), "fuera de rango"),
        ("conteo sin que contar", lambda d: d["reglas"][1].update(cuando={"conteo": True}), "que contar")]:
    d = copy.deepcopy(REGLAS)
    cambio(d)
    try:
        rb.validar(d, TXT)
        check(False, nombre)
    except ValueError as e:
        check(any(palabra in x for x in e.args[0]), f"{nombre}: se rechaza ({e.args[0][0][:70]})")

PRES = {"reglas": [{"cuando": {"presionado": "boton verde"}, "hacer": [{"recurso": "amarilla", "accion": "encender"}],
                    "fragmento": "prende la amarilla solo mientras se presiona el boton verde"}]}
r = rb.validar(PRES, "prende la amarilla solo mientras se presiona el boton verde")
check(r["reglas"][0]["cuando"] == {"presionado": "I1"}, "'mientras se presiona' (boton verde = I1)")
try:
    rb.validar({"reglas": [{"cuando": {"presionado": "I1"}, "hacer": [{"recurso": "banda", "accion": "avanzar"}],
                            "fragmento": "la banda avanza mientras se presiona I1"}]},
               "la banda avanza mientras se presiona I1")
    check(False, "presionado con la banda")
except ValueError as e:
    check(any("solo enciende luces" in x for x in e.args[0]), "'mientras se presiona' con la banda: se rechaza")
check(rb.verificar(lm.cfg_desde_respuesta(cb.armar_desde_reglas(r)), r) == [],
      "armado y probado: enciende al presionar y se apaga al soltar")

escenario("2. El extractor: secuencia, dudas, errores")
ext_resp[:] = [{"secuencia": True}]
check(eb.extraer(TXT, {}, _ext)["secuencia"], "acciones en orden -> va al flujo de secuencias")
ext_resp[:] = [{"reglas": [], "dudas": [{"campo": "amarilla i1", "pregunta": "¿Como queda la amarilla?",
                                         "opciones": ["Mientras se presiona", "Se queda", "5 s"]}]}]
d = eb.extraer("prende la amarilla con I1", {}, _ext)["dudas"]
check(d and d[0]["campo"] == "extr_amarilla_i1" and len(d[0]["opciones"]) == 3, "duda -> pregunta con opciones")
ext_resp[:] = [{"reglas": [], "dudas": [{"campo": "amarilla_i1", "pregunta": "?", "opciones": ["a", "b"]}]}]
check(eb.extraer("x", {"extr_amarilla_i1": {"respuesta": "a"}}, _ext)["dudas"] == [],
      "una duda ya respondida no se vuelve a preguntar")
check("RESPUESTAS DEL USUARIO" in eb.mensajes("x", {"extr_a": {"respuesta": "Se queda"}})[1]["content"],
      "las respuestas viajan al extractor")
ext_resp[:] = [RuntimeError("429")]
check(eb.extraer(TXT, {}, _ext)["problemas"], "si el extractor falla, no se rompe nada")

# ---------------------------------------------------------------------------
escenario("3. Pruebas por regla en el ST simulado")
resp = cb.armar_desde_reglas(rb.validar(REGLAS, TXT))
cfg = lm.cfg_desde_respuesta(resp)
check(rb.verificar(cfg, rb.validar(REGLAS, TXT)) == [], "lo armado desde las reglas cumple cada regla")
mal = copy.deepcopy(resp)
for e in mal["eventos"]:
    if "roja" in e["acciones"]:
        e["duracion_s"] = 8
f = rb.verificar(lm.cfg_desde_respuesta(mal), rb.validar(REGLAS, TXT))
check(f and "sigue encendida" in f[0], f"una duracion equivocada se detecta ({f})")

# ---------------------------------------------------------------------------
escenario("4. De punta a punta: el lector no entiende, el extractor si")
r = generar(TXT, extractor=[REGLAS])
check(len(ext_llamadas) == 1 and not llamadas, "una sola llamada (la corta del extractor), sin el modelo de generacion")
check(r.status == "ok" and r.analysis["fuente"] == "extractor", f"programa armado desde las reglas ({r.status})")
check("reglas (2)" in r.analysis["comportamiento"]["probado"] and r.analysis["comportamiento"]["fallas"] == [],
      "se probo cada regla simulando")
check(r.logic["band"]["freq_hz"] == 25, "a 25 Hz")

r = generar("prende la amarilla con el boton verde de la caja",
            extractor=[{"reglas": [], "dudas": [{"campo": "amarilla_i1", "pregunta": "¿Cómo queda la amarilla?",
                                                 "opciones": ["Mientras se presiona", "Se queda encendida", "5 s"]}]}])
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "extr_amarilla_i1" and not llamadas,
      "duda del extractor -> pregunta con opciones (sin suponer)")

SIN_HZ = copy.deepcopy(REGLAS)
SIN_HZ["motor"]["hz"] = None
r = generar(TXT.replace(" a 25 Hz", ""), extractor=[SIN_HZ])
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "freq_hz", "sin Hz: se preguntan")

# ---------------------------------------------------------------------------
escenario("5. Reglas no armables: guian al modelo y se prueban")
TXT2 = "Dale arranque con el verde a 30 Hz y que la verde se prenda cuando el primer sensor vea algo"
GUIA = {"reglas": [{"cuando": {"boton": "I1"}, "hacer": [{"recurso": "banda", "accion": "avanzar"}],
                    "fragmento": "Dale arranque con el verde"},
                   {"cuando": {"sensor": "S1"}, "hacer": [{"recurso": "verde", "accion": "encender"}],
                    "fragmento": "que la verde se prenda cuando el primer sensor vea algo"}],
        "motor": {"hz": 30}, "dudas": []}
SOLO_MOTOR = {"name": "x", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
              "motor": {"arranque": "I1"}, "analisis": A, "preguntas": []}
CON_VERDE = dict(copy.deepcopy(SOLO_MOTOR), eventos=[{"cuando": "S1", "acciones": {"verde": "encender"}}])
r = generar(TXT2, extractor=[GUIA], modelo=[SOLO_MOTOR, CON_VERDE])
check("REGLAS ENTENDIDAS" in sistema(0), "el modelo recibe las reglas como guia")
check(len(llamadas) == 2 and "Q3 no enciende" in ultimo_usuario(1), "la regla que no cumple vuelve como correccion")
check(r.status == "ok" and r.analysis["comportamiento"]["fallas"] == [] and r.analysis["reglas_guia"],
      "la version final cumple las reglas")

# ---------------------------------------------------------------------------
escenario("6. Defensas: contradicciones, errores y el interruptor")
PARCIAL = "Con I1 la banda avanza a 30 Hz y con I2 se detiene. Haz que la roja avise algo bonito con el primer sensor"
CONTRA = {"reglas": [{"cuando": {"boton": "I2"}, "hacer": [{"recurso": "banda", "accion": "avanzar"}],
                      "fragmento": "Con I1 la banda avanza a 30 Hz"}], "motor": {"hz": 30}, "dudas": []}
OK_MOTOR = {"name": "x", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
            "motor": {"arranque": "I1", "paro": "I2"}, "analisis": A, "preguntas": [],
            "eventos": [{"cuando": "S1", "duracion_s": 2, "acciones": {"roja": "encender"}}]}
r = generar(PARCIAL, extractor=[CONTRA], modelo=[OK_MOTOR])
check(len(llamadas) == 1 and "PIEZAS YA ARMADAS" in sistema(0),
      "si el extractor contradice lo entendido se ignora (el modelo recibe las piezas del lector)")
r = generar(PARCIAL, extractor=[RuntimeError("429")], modelo=[OK_MOTOR])
check(len(llamadas) == 1 and r.status == "ok", "si el extractor falla, sigue el flujo de siempre")
r = generar("Al presionar el botón verde, la banda comienza a avanzar a 30 Hz y, al presionarlo por segunda vez, "
            "se detiene")
check(not ext_llamadas and not llamadas and r.status == "ok", "si el lector entiende todo, el extractor no se llama")
app.EXTRACTOR_BANDA = False
r = generar(PARCIAL, modelo=[OK_MOTOR])
check(not ext_llamadas and len(llamadas) == 1, "LAVO_EXTRACTOR=0 lo apaga")
app.EXTRACTOR_BANDA = True

print(f"\nTODAS LAS PRUEBAS DEL EXTRACTOR PASARON ({OK} comprobaciones).")
