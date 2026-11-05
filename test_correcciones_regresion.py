"""
test_correcciones_regresion.py — secuencias complejas y correcciones (M1..M4).

Corre sin Groq (modelo simulado) y sin PLC (ST v7 real en lavo_sim):
    python test_correcciones_regresion.py

Caso de referencia (5 nov): S1 mueve plumas; S2 pausa 3 s, mueve plumas y
cuenta; a las 3 detecciones avanza 5 s, se detiene y cierra con plumas.
  M1  las dudas de interpretacion se preguntan (¿plumas durante la pausa o al
      reanudar? ¿el objeto 3 tambien hace la pausa o pasa directo?) y la
      simulacion exige lo respondido
  M2  una correccion que responde esas dudas cambia lo que se verifica
  M3  la correccion se localiza (zona) o se pregunta con las etapas reales
  M4  la version nueva solo reemplaza a la vigente si no rompe nada que pasaba;
      si no, se conserva la vigente
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_reg_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_reg.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import edicion_incremental as ei
import etapas_orden as eo
import lavo_motor as lm
import regresion_banda as rg
import simulacion_etapas as se

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_reg.json")
app.BANDA_MOTOR_V7 = True
app.CONSTRUCTORES_BANDA = False

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

T = ("Al presionar el pulsador I1, la banda transportadora se pone en marcha a una frecuencia de 20 Hz. Cuando el "
     "Sensor 1 detecta un objeto, la pluma 1 se eleva y la pluma 2 desciende. Posteriormente, al activarse el "
     "Sensor 2, la banda se detiene durante 3 segundos y después continúa avanzando; además, la pluma 2 se eleva, "
     "la pluma 1 desciende y se registra un conteo. Una vez que el Sensor 2 acumula 3 detecciones, la banda "
     "continúa avanzando únicamente durante 5 segundos y luego se detiene automáticamente. Finalmente, la pluma 1 "
     "se eleva, la pluma 2 desciende y el contador se reinicia a cero, respetando ese orden.")
DETECTAR = {"pregunta": "", "respuesta": "Al detectar (durante la pausa)"}
REANUDAR = {"pregunta": "", "respuesta": "Al reanudar el avance"}
TAMBIEN = {"pregunta": "", "respuesta": "También lo hace y luego sigue"}
DIRECTO = {"pregunta": "", "respuesta": "Pasa directo"}
A = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [], "simplificado": [],
     "no_soportado": [], "requiere_confirmacion": False}


def prog(pasos):
    return {"name": "Plumas y conteo", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 20},
            "contadores": [{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez", "reset": "SQ1_FIN"}],
            "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I2", "modo": "una_vez", "pasos": pasos}],
            "analisis": A, "preguntas": []}


S1_ESPERA = {"motor": ["marcha"], "avanza": "flanco", "senal": "S1"}
PLUMAS_S1 = {"salidas": ["Q8", "Q7"], "motor": ["marcha"], "avanza": "flanco", "senal": "S2"}
FINAL = [{"motor": ["marcha"], "avanza": "tiempo", "segundos": 5},
         {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0.5},
         {"salidas": ["Q8", "Q7"], "avanza": "tiempo", "segundos": 2}]
# Plumas DURANTE la pausa, el objeto 3 TAMBIEN pausa
TAMBIEN_DET = prog([S1_ESPERA, PLUMAS_S1,
                    {"salidas": ["Q6", "Q9"], "motor": [], "avanza": "tiempo", "segundos": 3},
                    {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0, "siguiente": 1,
                     "alternativa": {"senal": "CT1", "ir_a": 5}}] + FINAL)
# Plumas DURANTE la pausa, el objeto 3 pasa DIRECTO
DIRECTO_DET = prog([S1_ESPERA, PLUMAS_S1,
                    {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0,
                     "alternativa": {"senal": "CT1", "ir_a": 5}},
                    {"salidas": ["Q6", "Q9"], "motor": [], "avanza": "tiempo", "segundos": 3, "siguiente": 1}]
                   + FINAL)
# Plumas AL REANUDAR, el objeto 3 pasa DIRECTO
DIRECTO_REA = prog([S1_ESPERA, PLUMAS_S1,
                    {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0,
                     "alternativa": {"senal": "CT1", "ir_a": 6}},
                    {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 3},
                    {"salidas": ["Q6", "Q9"], "motor": ["marcha"], "avanza": "tiempo", "segundos": 2,
                     "siguiente": 1}] + FINAL)
# Rompe el arranque (lo que ya funcionaba): la secuencia empieza con I2
SIN_ARRANQUE = copy.deepcopy(DIRECTO_DET)
SIN_ARRANQUE["secuencias"][0]["inicio"] = "S2"


def det(acl):
    return eo.detectar(T, "banda", acl)


def sim(p, d):
    return se.verificar(lm.cfg_desde_respuesta(p), d)


def generar(texto, *resp, prev=None, historial=None, acl=None):
    respuestas[:] = list(resp)
    llamadas.clear()
    ctx = None
    if prev is not None:
        c = lm.cfg_desde_respuesta(prev)
        c["respuesta_llm"] = prev
        ctx = app.ContextoLadder(programa_anterior={"metadata": {"name": prev.get("name"), "engine_config": c}},
                                 historial=historial or [])
    return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", aclaraciones=acl,
                                                            contexto=ctx)))


def usuario(i):
    return [m["content"] for m in llamadas[i] if m["role"] == "user"][-1]


def sistema(i):
    return "\n".join(m["content"] for m in llamadas[i] if m["role"] == "system")


# ---------------------------------------------------------------------------
escenario("1. M1: las dudas de interpretacion se preguntan (no se suponen)")
d = det({})
q = eo.preguntas(d, "banda")
check([x["campo"] for x in q] == ["acciones_pausa_3", "conteo_ultimo_1"], f"dos preguntas ({[x['campo'] for x in q]})")
check(all(len(x["opciones"]) == 2 for x in q), "con opciones de un clic")
check(d["etapas"][2]["reanuda"] and d["etapas"][2]["tiempos"] == [3.0] and d["etapas"][2]["incrementa"],
      "'se detiene 3 s y despues continua' es UNA pausa que reanuda (y 'se registra un conteo' cuenta)")
dr = det({"acciones_pausa_3": REANUDAR, "conteo_ultimo_1": DIRECTO})
check(len(dr["etapas"]) == 7 and dr["grupos"][0]["ultimo"] == "directo", "al reanudar: las plumas van en otra etapa")
dd = det({"acciones_pausa_3": DETECTAR, "conteo_ultimo_1": TAMBIEN})
check(len(dd["etapas"]) == 6 and dd["grupos"][0]["ultimo"] == "tambien", "al detectar: plumas junto con la pausa")
check("pasa DIRECTO" in eo.bloque_modelo(dr, "banda") and "PASO DE DECISION" in eo.bloque_modelo(dd, "banda"),
      "la receta del ciclo sigue la respuesta")
r = generar(T)
check(r.status == "needs_clarification" and not llamadas, "de punta a punta: pregunta antes de llamar al modelo")
import intencion_banda as ib
i = ib.analizar(T + " Al detectar (durante la pausa) Pasa directo")
check(eo.preguntas(i["etapas"], "banda") == [] and i["etapas"]["grupos"][0]["ultimo"] == "directo",
      "lentes: las opciones pegadas al final del texto cuentan como respuesta (no se repite la pregunta)")
i = ib.analizar(T + " Al detectar (durante la pausa)")
check([q["campo"] for q in eo.preguntas(i["etapas"], "banda")] == ["conteo_ultimo_1"],
      "lentes: con una respuesta pegada solo queda la otra pregunta")

escenario("2. M1: la simulacion exige lo respondido (flancos, pausa 3 s, conteo, 5 s, paro, cierre)")
check(sim(TAMBIEN_DET, dd) == [], "plumas en la pausa + objeto 3 tambien pausa: cumple")
check(sim(DIRECTO_REA, dr) == [], "plumas al reanudar + objeto 3 directo: cumple")
check(sim(DIRECTO_REA, dd) != [] and sim(TAMBIEN_DET, dr) != [], "cada programa falla con la otra lectura")
ddir = det({"acciones_pausa_3": DETECTAR, "conteo_ultimo_1": DIRECTO})
f = sim(TAMBIEN_DET, ddir)
check(f and ("directo" in f[0] or "Etapa" in f[0]), f"si debia pasar directo y pausa en el 3: se detecta ({f[0][:80]})")
check(sim(DIRECTO_DET, ddir) == [], "plumas en la pausa + directo: cumple")

# ---------------------------------------------------------------------------
escenario("3. M2: la correccion responde la duda y cambia lo que se verifica")
ORIG = T + " [acciones_pausa_3: Al detectar (durante la pausa); conteo_ultimo_1: También lo hace y luego sigue]"
d2 = eo.de_historial([ORIG], "banda", {}, posteriores=["esta mal: en la tercera ya no se detiene, pasa directo"])
check(d2["grupos"][0]["ultimo"] == "directo", "'en la tercera pasa directo' cambia la respuesta anterior")
d3 = eo.de_historial([ORIG], "banda", {}, posteriores=["revisa, las plumas deben moverse al reanudar el avance"])
check(len(d3["etapas"]) == 7, "'las plumas al reanudar' parte la etapa")
check(eo.de_historial([ORIG], "banda", {}, posteriores=["revisa el contador"])["grupos"][0]["ultimo"] == "tambien",
      "una correccion que no habla de eso no cambia nada")

# ---------------------------------------------------------------------------
escenario("4. M3: localizar la parte a corregir")
loc = ei.localizar("revisa el contador", dd)
check("conteo" in loc["modulos"] and 3 in loc["etapas"], f"'revisa el contador' -> conteo, etapa 3 ({loc})")
loc = ei.localizar("la pluma 2 no sube cuando S2 detecta", dd)
check("plumas" in loc["modulos"] and 3 in loc["etapas"], f"'pluma 2 ... S2' -> etapa 3 ({loc['etapas']})")
check(ei.localizar("la banda no se detiene a los 5 segundos", dd)["etapas"] == [4], "'5 segundos' -> etapa 4")
check(ei.localizar("revisa por favor", dd)["vago"], "'revisa por favor' es vago")
check(ei.localizar("revisa", dd, ["Etapa 5 «...»: se esperaba ..."])["etapas"] == [5], "la simulacion apunta la etapa")
q = ei.pregunta_falla_etapas(dd)
check(q["campo"] == "falla" and q["opciones"][0].startswith("Etapa 1") and q["opciones"][-1] == "Otra cosa",
      "pregunta con las etapas reales")
check("ZONA A CORREGIR" in ei.bloque_zona(ei.localizar("revisa el contador", dd), dd), "zona para el modelo")

HIST = [ORIG]
r = generar("revisa por favor", prev=TAMBIEN_DET, historial=HIST)
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "falla" and not llamadas,
      "correccion vaga con la logica cumpliendo: pregunta que parte (sin llamar al modelo)")

# ---------------------------------------------------------------------------
escenario("5. M4: regresion — arregla sin romper, o se conserva la vigente")
base = rg.bateria(lm.cfg_desde_respuesta(TAMBIEN_DET), [{"tipo": "arranca_con", "senal": "I1", "texto": ""}], ddir)
nuevo = rg.bateria(lm.cfg_desde_respuesta(DIRECTO_DET), [{"tipo": "arranca_con", "senal": "I1", "texto": ""}], ddir)
cmp = rg.comparar(base, nuevo)
check(cmp["arregladas"] == ["secuencia SQ1"] and not cmp["regresiones"] and rg.aceptable(cmp, base, nuevo),
      "la version que arregla la secuencia sin romper el arranque es aceptable")
roto = rg.bateria(lm.cfg_desde_respuesta(SIN_ARRANQUE), [{"tipo": "arranca_con", "senal": "I1", "texto": ""}], ddir)
cmp = rg.comparar(base, roto)
check(cmp["regresiones"] and not rg.aceptable(cmp, base, roto), f"la que rompe el arranque no ({cmp['regresiones']})")

CORR = "esta mal: en la tercera deteccion ya no se detiene, pasa directo a los 5 segundos"
r = generar(CORR, SIN_ARRANQUE, DIRECTO_DET, prev=TAMBIEN_DET, historial=HIST)
check(len(llamadas) == 2 and "ROMPE" in usuario(1), "la que rompe vuelve al modelo diciendo que rompio")
check("ZONA A CORREGIR" in sistema(0), "el modelo recibe la zona a corregir")
check(r.status == "ok" and r.analysis["fuente"] == "modelo"
      and r.logic["band"]["engine"]["secuencias"][0]["pasos"][2].get("alternativa"),
      "la version final arregla la tercera deteccion (decide antes de la pausa)")

r = generar(CORR, SIN_ARRANQUE, SIN_ARRANQUE, SIN_ARRANQUE, prev=TAMBIEN_DET, historial=HIST)
check(r.status == "ok" and r.analysis["fuente"] == "conservada", f"si nunca mejora: se conserva ({r.analysis['fuente']})")
check(r.logic["band"]["engine"]["secuencias"][0]["inicio"] == "I1", "la logica entregada es la vigente")
check(any("No apliqué el cambio" in w for w in r.warnings), f"y se avisa en una linea ({r.warnings})")
check(len(llamadas) == 3 and "puedes REHACER la secuencia completa" in usuario(2),
      "desde el 2o intento se permite un cambio estructural")

print(f"\nTODAS LAS PRUEBAS DE CORRECCIONES Y REGRESION PASARON ({OK} comprobaciones).")
