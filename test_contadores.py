"""
test_contadores.py — contadores que se reinician solos (banda v7).

Corre sin Groq (modelo simulado) y sin PLC: las pruebas de comportamiento
corren el ST real del programa maestro v7 (lavo_sim).
    python test_contadores.py

Casos reales (4 nov):
  - "al llegar a 3 se ejecute tal y luego se reinicie solo": un contador
    una_vez sin reset (o con reset I2) solo funcionaba la primera vez;
  - con reset SQ1_FIN y SIN secuencia el JSON no cargaba y el modelo lo
    cambiaba a I2 ("para cumplir con hardware");
  - en una secuencia que se repite, reset SQ1_FIN nunca llega: en la 2a vuelta
    la accion salia con CADA objeto.
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_ct_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_ct.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import comportamiento as co
import etapas_orden as eo
import lavo_motor as lm
import lavo_sim as ls

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_ct.json")
app.BANDA_MOTOR_V7 = True

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
BASE = {"name": "Roja cada 3", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
        "motor": {"arranque": "I1", "paro": "I2"}, "analisis": A, "preguntas": []}


def prog(**extra):
    return {**copy.deepcopy(BASE), **extra}


CICLICO = prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "ciclico"}],
               timers=[{"id": "T1", "tipo": "PULSO", "entrada": "CT1", "segundos": 5}], salidas={"Q5": "T1"})
RESET_T1 = prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez", "reset": "T1"}],
                timers=[{"id": "T1", "tipo": "PULSO", "entrada": "CT1", "segundos": 5}], salidas={"Q5": "T1"})
SIN_RESET = prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez"}],
                 timers=[{"id": "T1", "tipo": "PULSO", "entrada": "CT1", "segundos": 5}], salidas={"Q5": "T1"})
RESET_I2 = prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez", "reset": "I2"}],
                timers=[{"id": "T1", "tipo": "PULSO", "entrada": "CT1", "segundos": 5}], salidas={"Q5": "T1"})
EVENTO = prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "ciclico"}],
              eventos=[{"cuando": "CT1", "duracion_s": 5, "acciones": {"roja": "encender"}}])
FIJA = prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez"}], salidas={"Q5": "CT1"})
SQ_FIN = prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez", "reset": "SQ1_FIN"}],
              eventos=[{"cuando": "CT1", "duracion_s": 5, "acciones": {"roja": "encender"}}])

TEXTO = ("Con I1 la banda avanza. Cuando S2 cuente 3 cajas prende la roja 5 segundos y luego que el contador se "
         "reinicie solo")


def verif(p, texto=TEXTO):
    return co.verificar(lm.cfg_desde_respuesta(p), co.requisitos([texto]))


# ---------------------------------------------------------------------------
escenario("1. Lectura: contar N en un sensor y si se reinicia solo")
r = co.requisitos([TEXTO])
check({"tipo": "conteo", "conteo": 3, "senal": "S2", "reinicia": True}.items()
      <= next(x for x in r if x["tipo"] == "conteo").items(), "contar 3 en S2, se reinicia solo")
check(co.conteo_en("Cada 3 piezas en el sensor 1 sube la pluma 1") == {"conteo": 3, "senal": "S1", "reinicia": True},
      "'cada 3 piezas' = se repite (se reinicia solo)")
check(co.conteo_en("Al llegar a 4 detecciones en S2 la banda se detiene y el contador se reinicia con I2")
      == {"conteo": 4, "senal": "S2", "reinicia": False}, "'se reinicia con I2' es manual")
check(co.conteo_en("Al llegar a 5 en S2 enciende la verde. Despues el contador vuelve a cero")["reinicia"],
      "'vuelve a cero' en otra oracion")
for t in ("Prende la verde durante 5 segundos", "La banda avanza a 30 Hz con I1",
          "Cada 5 segundos prende la roja", "Despues de 3 segundos sube la pluma 1"):
    check(not co.conteo_en(t), f"no es conteo: '{t}'")
check(not any(x["tipo"] == "conteo" for x in co.requisitos(
    ["Al presionar I1 la banda avanza; cuando S2 cuente 3 cajas se detiene sola"])),
      "'al contar 3 se detiene sola' lo prueba para_sola (sin repetirlo)")
check(not any(x["tipo"] == "conteo" for x in co.requisitos([TEXTO, "quita el contador"])),
      "'quita el contador' lo quita")

# ---------------------------------------------------------------------------
escenario("2. Simulando el PLC: los que se reinician solos pasan, los demas no")
check(verif(CICLICO) == [], "modo ciclico + timer PULSO de 5 s")
check(verif(RESET_T1) == [], "modo una_vez con reset = el timer de la accion")
check(verif(EVENTO) == [], "evento 'cuando CT1' de 5 s con modo ciclico")
f = verif(SIN_RESET)
check(f and "no vuelve a cero" in f[0] and "segunda vez" in f[0], f"sin reset: solo funciona una vez ({f})")
check(verif(RESET_I2) and "no vuelve a cero" in verif(RESET_I2)[0], "reset I2: tampoco se reinicia solo")
f = verif(FIJA)
check(f and "se queda activo" in f[0], f"la roja fija del contador: el contador nunca se reinicia ({f})")
check(verif(SIN_RESET, "Con I1 la banda avanza. Cuando S2 cuente 3 cajas prende la roja 5 segundos") == [],
      "si NO pide reiniciarse, contar una vez esta bien")
check(verif(prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "ciclico"}]),
            TEXTO) and "no pasa nada" in verif(prog(contadores=[{"id": "CT1", "entrada": "S2", "preset": 3,
                                                                 "modo": "ciclico"}]), TEXTO)[0],
      "un contador que no hace nada al llegar a 3 se detecta")

# ---------------------------------------------------------------------------
escenario("3. SQ1_FIN sin secuencia: el error le dice al modelo como arreglarlo (no I2)")
err = app.validar_logica_banda(lm.cfg_desde_respuesta(SQ_FIN))
check(err and "SQ1_FIN" in err[0] and "ciclico" in err[0] and "I2" in err[0], f"pista en el error ({err})")

# ---------------------------------------------------------------------------
escenario("4. De punta a punta: el modelo recibe la receta y se le devuelve lo que fallo al simular")
r = generar(TEXTO, SIN_RESET, CICLICO)
check("VUELVE A CERO SOLO" in sistema(0) and "\"modo\":\"ciclico\"" in sistema(0), "la receta va en el prompt")
check(len(llamadas) == 2 and "no vuelve a cero" in ultimo_usuario(1), "segunda vuelta con la falla simulada")
check(r.status == "ok" and r.analysis["comportamiento"]["fallas"] == [], "la version final se reinicia sola")
check(not any("alternativa" in w.lower() for w in r.warnings), "sin alternativas")
r = generar(TEXTO, SQ_FIN, CICLICO)
check(len(llamadas) == 2 and "ciclico" in ultimo_usuario(1), "con SQ1_FIN sin secuencia: la correccion trae la pista")
check(r.status == "ok" and r.logic["band"]["engine"]["contadores"][0]["modo"] == "ciclico", "y queda en ciclico")

# ---------------------------------------------------------------------------
escenario("5. Secuencia que se REPITE con conteo: reset SQ1_CICLO (SQ1_FIN nunca llega)")
d = eo.detectar("Primero con I1 la banda avanza hasta que S2 detecte, luego cuando el sensor 2 acumula 3 "
                "detecciones prende la roja 3 segundos y se repita el ciclo", "banda", {})
bloque = eo.bloque_modelo(d, "banda")
if d["grupos"] and d["grupos"][0].get("conteo"):
    check("SQ1_CICLO" in bloque and "Modo infinita" in bloque and "SQ1_FIN para" not in bloque,
          "la receta del ciclo usa SQ1_CICLO y modo infinita")
else:
    check("SQ1_CICLO" in bloque, f"conteo que se repite -> SQ1_CICLO ({bloque[-300:]})")
d1 = eo.detectar("Primero con I1 la banda avanza hasta que S2 detecte, luego cuando el sensor 2 acumula 3 "
                 "detecciones prende la roja 3 segundos y al final se detiene", "banda", {})
check("SQ1_FIN" in eo.bloque_modelo(d1, "banda") and "SQ1_CICLO" not in eo.bloque_modelo(d1, "banda"),
      "si se corre una sola vez sigue con SQ1_FIN")


def vueltas(modo, reset):
    p = prog(name="ciclo", motor={},
             contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez", "reset": reset}],
             secuencias=[{"id": "SQ1", "inicio": "I1", "paro": "I2", "modo": modo, "repeticiones": 3, "pasos": [
                 {"motor": ["marcha"], "avanza": "flanco", "senal": "S2"},
                 {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0, "siguiente": 1,
                  "alternativa": {"senal": "CT1", "ir_a": 3}},
                 {"salidas": ["Q5"], "motor": [], "avanza": "tiempo", "segundos": 3}]}])
    s = ls.SimV7()
    s.cargar(lm.cfg_desde_respuesta(p)["band"])
    s.pulsar("I1")
    s.correr(1)
    out = []
    for _ in range(2):
        hits = []
        for k in range(3):
            s.poner(I5=True); s.correr(0.3); s.poner(I5=False); s.correr(0.3)
            hits.append(s.q("Q5"))
        out.append([k + 1 for k, h in enumerate(hits) if h])
        s.correr(3.5)
    return out


check(vueltas("infinita", "SQ1_FIN") == [[3], [1, 2, 3]], "con SQ1_FIN la 2a vuelta sale con cada caja (el error)")
check(vueltas("infinita", "SQ1_CICLO") == [[3], [3]], "con SQ1_CICLO sale en la 3a caja de cada vuelta")
check(vueltas("repetir", "SQ1_CICLO") == [[3], [3]], "tambien en modo repetir")

print(f"\nTODAS LAS PRUEBAS DE CONTADORES PASARON ({OK} comprobaciones).")
