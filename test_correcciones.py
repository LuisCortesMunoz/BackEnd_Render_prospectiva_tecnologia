"""
test_correcciones.py — paro automatico ("que se detenga por si sola") y
correcciones ("revisa", "no esta funcionando", "corrige").

Corre sin Groq (modelo simulado) y sin PLC: la validacion final simula el ST
real del programa maestro v7 con lavo_sim.
    python test_correcciones.py
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_corr_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_corr.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import comportamiento as cp
import edicion_incremental as ei
import lavo_motor as lm

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_corr.json")
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


def generar(texto, *resp, prev=None, historial=None, acl=None):
    respuestas[:] = list(resp)
    llamadas.clear()
    ctx = None
    if prev is not None:
        c = lm.cfg_desde_respuesta(prev)
        c["respuesta_llm"] = prev
        ctx = app.ContextoLadder(programa_anterior={"metadata": {"name": prev.get("name"), "engine_config": c}},
                                 historial=historial or [])
    req = app.LogicaRequest(texto=texto, device="banda", aclaraciones=acl, contexto=ctx)
    return asyncio.run(app.generar_logica(req))


def contenido(msgs) -> str:
    return "\n".join(str(m.get("content")) for m in msgs)


ANALISIS = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [],
            "simplificado": [], "no_soportado": [], "requiere_confirmacion": False}
BASE = {"name": "Banda I1/I2", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
        "motor": {"arranque": "I1", "paro": "I2", "modo": "enclavado"}, "analisis": ANALISIS, "preguntas": []}
SOLA_10 = {**copy.deepcopy(BASE), "name": "Banda se detiene sola",
           "motor": {"arranque": "I1", "paro": "C1", "modo": "enclavado"},
           "condiciones": [{"id": "C1", "op": "OR", "entradas": ["I2", "T1"], "flanco": "nivel"}],
           "timers": [{"id": "T1", "tipo": "TON", "entrada": "BANDA_CORRIENDO", "reset": None, "segundos": 10}],
           "analisis": {**ANALISIS, "diagnostico": "el paro solo dependia de I2; se agrego T1 (10 s de marcha)"}}
# Error tipico: un timer suelto que NO esta en el paro (la banda nunca se detiene sola)
SOLA_MAL = {**copy.deepcopy(BASE), "timers": [{"id": "T1", "tipo": "TON", "entrada": "BANDA_CORRIENDO",
                                                "reset": None, "segundos": 10}]}
HIST = ["Quiero que la banda avance a la derecha a 30 Hz, arranca con I1 y con I2 se detiene"]

# ─────────────────────────────────────────────────────────────────────────
escenario("1. Lectura de las instrucciones")
for t in ["haz que la banda se detenga por sí sola", "que se pare sola", "que se detenga automáticamente",
          "sin que nadie la toque se detenga", "paro automático"]:
    check(cp.pide_paro_solo(t), f"paro automatico: «{t}»")
for t in ["con I2 se detiene", "arranca con I1", "agrega la luz roja"]:
    check(not cp.pide_paro_solo(t), f"no es paro automatico: «{t}»")
check(cp.condicion_paro_solo("que se pare sola a los 10 segundos") == {"segundos": 10.0}, "a los 10 s")
check(cp.condicion_paro_solo("que se detenga sola cuando el sensor 1 detecte") == {"senal": "S1"}, "cuando S1 detecte")
check(cp.condicion_paro_solo("se detenga sola al contar 3 cajas")["conteo"] == 3, "al contar 3 cajas")
r = cp.requisitos(HIST)
check(sorted((x["tipo"], x["senal"]) for x in r) == [("arranca_con", "I1"), ("para_con", "I2")],
      f"arranca con I1 / para con I2, sin confundir los botones ({r})")
r = cp.requisitos(["que se detenga sola a los 10 s", "no se detiene sola, revisa"])
check(r[-1].get("segundos") == 10, "la correccion conserva la condicion dicha antes")
check(not [x for x in cp.requisitos(["que se detenga sola a los 10 s", "ya no quiero que se detenga sola"])
           if x["tipo"] == "para_sola"], "'ya no ... sola' quita el requisito")
for t in ["revisa", "checa", "no está funcionando", "corrige", "no se detiene", "sigue igual", "está mal"]:
    check(ei.es_correccion(t), f"correccion: «{t}»")
for t in ["agrega la luz roja", "haz que la banda se detenga por sí sola", "cambia el tiempo a 5 s"]:
    check(not ei.es_correccion(t), f"no es correccion: «{t}»")

# ─────────────────────────────────────────────────────────────────────────
escenario("2. Simulacion: la validacion final distingue lo que funciona de lo que no")
reqs10 = [{"tipo": "para_sola", "segundos": 10, "texto": "x"}] + cp.requisitos(HIST)
check(cp.verificar(lm.cfg_desde_respuesta(SOLA_10), reqs10) == [], "T1 en el paro: se detiene sola a los 10 s y con I2")
f = cp.verificar(lm.cfg_desde_respuesta(SOLA_MAL), reqs10)
check(any("NO se detiene sola" in x for x in f), f"timer suelto: se detecta que no se detiene sola ({f})")
check(ei.misma_logica("banda", BASE, copy.deepcopy(BASE) | {"name": "otro nombre"}), "mismo programa con otro nombre = misma logica")
check(not ei.misma_logica("banda", BASE, SOLA_10), "otro paro = logica distinta")

# ─────────────────────────────────────────────────────────────────────────
escenario("3. 'Haz que la banda se detenga por si sola' sin decir cuando: se pregunta con opciones")
r = generar("haz que la banda se detenga por sí sola", prev=BASE, historial=HIST)
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "paro_solo" and not llamadas,
      "pregunta 'paro_solo' sin llamar al modelo")
check(len(r.questions[0]["opciones"]) >= 4, "con opciones de un clic")

escenario("4. Con la respuesta: el modelo recibe la regla y la logica se prueba simulando")
acl = {"paro_solo": {"pregunta": "¿Cuándo?", "respuesta": "A los 10 s de marcha"}}
r = generar("haz que la banda se detenga por sí sola", SOLA_10, prev=BASE, historial=HIST, acl=acl)
check(r.status == "ok", f"ok ({r.status}, {r.warnings})")
check("DETIENE SOLA" in contenido(llamadas[0]) and "10 s" in contenido(llamadas[0]), "el prompt lleva el comportamiento a probar")
check(r.analysis["comportamiento"]["fallas"] == [], "la simulacion confirma que cumple")

escenario("5. Si el modelo arma un paro que no funciona, se le devuelve lo que fallo al simular")
r = generar("haz que la banda se detenga por sí sola", SOLA_MAL, SOLA_10, prev=BASE, historial=HIST, acl=acl)
check(len(llamadas) == 2 and "NO hace lo pedido" in contenido(llamadas[1]), "segunda vuelta con la falla simulada")
check(r.status == "ok" and r.analysis["comportamiento"]["fallas"] == [], "la version final si cumple")

escenario("6. Si nunca cumple, se entrega con un aviso corto (sin pedir confirmar)")
r = generar("haz que la banda se detenga por sí sola", SOLA_MAL, SOLA_MAL, SOLA_MAL, prev=BASE, historial=HIST, acl=acl)
check(r.status == "ok" and r.analysis["comportamiento"]["fallas"],
      f"se entrega y la falla queda registrada ({r.status})")
check(any("pide corregirlo" in w for w in r.warnings), f"aviso corto para pedir la correccion: {r.warnings}")
check(not any("alternativa" in w.lower() for w in r.warnings), "no se presenta como alternativa")

# ─────────────────────────────────────────────────────────────────────────
escenario("7. Correccion: 'no esta funcionando' cambia las reglas y lleva el diagnostico de la simulacion")
hist = HIST + ["que se detenga sola a los 10 segundos"]
r = generar("no está funcionando, revisa", SOLA_10, prev=SOLA_MAL, historial=hist)
c = contenido(llamadas[0])
check("NO funciona como pidio" in c and "no la rehagas" not in c, "reglas de correccion (ya no 'no la rehagas')")
check("DIAGNOSTICO AUTOMATICO" in c and "NO se detiene sola" in c, "el diagnostico simulado va al modelo")
check(r.status == "ok" and any(w.startswith("Diagnóstico:") for w in r.warnings),
      f"se muestra el diagnostico al usuario ({r.warnings})")
check(r.analysis.get("correccion") is True, "marcada como correccion")

escenario("8. Correccion: devolver la misma logica no se acepta")
r = generar("revisa, sigue sin detenerse sola", SOLA_MAL, SOLA_10, prev=SOLA_MAL, historial=hist)
check(len(llamadas) == 2 and "MISMA logica" in contenido(llamadas[1]), "se le pide cambiar la parte que falla")
check(r.status == "ok", "la segunda version (distinta y que cumple) se entrega")

escenario("9. Correccion sin falla detectable y el modelo insiste en lo mismo: se pregunta que falla")
r = generar("checa la lógica", BASE, BASE, prev=BASE, historial=HIST)
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "falla", "pregunta que es lo que no funciona")
acl_f = {"falla": {"pregunta": "¿Qué no funciona?", "respuesta": "No se detiene sola"}}
r = generar("checa la lógica", prev=BASE, historial=HIST, acl=acl_f)
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "paro_solo",
      "'No se detiene sola' lleva a preguntar cuando debe detenerse")

escenario("10. Correccion: rehacer la parte que falla puede quitar piezas sin forzar a restaurarlas")
CON_EVENTO = {**copy.deepcopy(SOLA_MAL), "eventos": [{"cuando": "S1", "espera_s": 0, "duracion_s": None,
                                                     "acciones": {"roja": "encender"}}]}
NUEVO = {**copy.deepcopy(SOLA_10), "eventos": CON_EVENTO["eventos"]}
r = generar("no funciona, corrige", NUEVO, prev=CON_EVENTO, historial=hist)
check(len(llamadas) == 1 and r.status == "ok", "una sola llamada: no se pidio restaurar piezas")
check("evento cuando S1" not in str(r.warnings), "lo que no tenia que ver con la falla (evento S1) se conservo")
check(r.analysis["comportamiento"]["fallas"] == [], "y la simulacion confirma el paro automatico")

print(f"\nTODAS LAS PRUEBAS DE CORRECCIONES PASARON ({OK} comprobaciones).")
