"""
test_secuencias_complejas.py — secuencias que combinan sensores, plumas,
pausas, contadores con ciclo, avance por tiempo y paro automatico.

Corre sin Groq (modelo simulado) y sin PLC: la validacion integral corre el
ST real del programa maestro v7 (lavo_sim) con la historia de las etapas.
    python test_secuencias_complejas.py
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_seqc_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_seqc.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import etapas_orden as eo
import lavo_motor as lm
import simulacion_etapas as se

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_seqc.json")
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


def contenido(msgs) -> str:
    return "\n".join(str(m.get("content")) for m in msgs)


TEXTO = ("Al presionar el pulsador I1, la banda transportadora se pone en marcha a una frecuencia de 20 Hz. Cuando "
         "el Sensor 1 detecta un objeto, la pluma 1 se eleva y la pluma 2 desciende. Posteriormente, cuando el "
         "Sensor 2 detecta un objeto, la banda se detiene durante 5 segundos antes de continuar avanzando; además, "
         "la pluma 2 se eleva, la pluma 1 desciende y se incrementa un contador. Una vez que el Sensor 2 acumula 3 "
         "detecciones, la banda se detiene durante 5 segundos, después avanza únicamente durante un tiempo "
         "determinado y finalmente se detiene automáticamente. A continuación, la pluma 1 se eleva, la pluma 2 "
         "desciende y el contador se reinicia a cero, respetando ese orden.")
# BIEN mueve las plumas DURANTE la pausa de S2 y el objeto 3 TAMBIEN hace la pausa (respuestas a las dudas)
DUDAS = {"acciones_pausa_3": {"pregunta": "", "respuesta": "Al detectar (durante la pausa)"},
         "conteo_ultimo_1": {"pregunta": "", "respuesta": "También lo hace y luego sigue"}}
ACL = {**DUDAS, "etapa_5_fin": {"pregunta": "¿Cuánto tiempo?", "respuesta": "4 s"}}
ANALISIS = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [],
            "simplificado": [], "no_soportado": [], "requiere_confirmacion": False}


def resp(pasos, reset="SQ1_FIN", motor=None):
    return {"name": "Banda con conteo", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 20},
            "motor": motor or {}, "condiciones": [], "memorias": [], "timers": [], "salidas": {}, "eventos": [],
            "contadores": [{"id": "CT1", "entrada": "S2", "descuenta": None, "reset": reset, "preset": 3,
                            "modo": "una_vez"}],
            "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I2", "modo": "una_vez", "exclusiva": True,
                            "pausa_apaga": True, "pasos": pasos}],
            "analisis": ANALISIS, "preguntas": []}


BIEN = [{"salidas": [], "motor": ["marcha"], "avanza": "flanco", "senal": "S1"},
        {"salidas": ["Q8", "Q7"], "motor": ["marcha"], "avanza": "tiempo", "segundos": 2},
        {"salidas": [], "motor": ["marcha"], "avanza": "flanco", "senal": "S2"},
        {"salidas": ["Q6", "Q9"], "motor": [], "avanza": "tiempo", "segundos": 5},
        {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0, "siguiente": 1,
         "alternativa": {"senal": "CT1", "ir_a": 6}},
        {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 5},
        {"salidas": [], "motor": ["marcha"], "avanza": "tiempo", "segundos": 4},
        {"salidas": ["Q8", "Q7"], "motor": [], "avanza": "tiempo", "segundos": 2}]
CORRECTA = resp(BIEN)
SIN_RESET = resp(BIEN, reset=None)
REARRANCA = resp(BIEN, motor={"arranque": "SQ1_FIN", "modo": "enclavado"})
NO_REANUDA = resp(BIEN[:4] + [{**BIEN[4], "siguiente": 3}] + BIEN[5:])
AVANZA_10 = resp(BIEN[:6] + [{**BIEN[6], "segundos": 10}] + BIEN[7:])

# ─────────────────────────────────────────────────────────────────────────
escenario("1. Interpretacion de la instruccion compleja")
d = eo.detectar(TEXTO, "banda", {})
check(d["aplica"] and len(d["etapas"]) == 7, f"7 etapas en orden ({len(d['etapas'])})")
e = {x["n"]: x for x in d["etapas"]}
check(e[1]["entra_con"] == "I1", "'el pulsador I1' arranca la secuencia")
check(e[2]["entra_con"] == "S1" and e[3]["entra_con"] == "S2", "S1 y S2 disparan las etapas 2 y 3")
check(e[3]["tiempos"] == [5.0] and e[3]["reanuda"] and e[3]["incrementa"],
      "etapa 3: pausa 5 s, despues la banda continua, y cuenta el objeto")
check(e[4]["conteo"] == {"n": 3, "senal": "S2"} and e[4]["tiempos"] == [5.0], "etapa 4: al llegar S2 a 3, pausa 5 s")
check(e[5]["tiempos"] == [] and e[5]["tiempo_indefinido"], "'durante un tiempo determinado' NO se inventa (antes salia 1 s)")
check(e[7]["reinicia_contador"], "etapa 7: el contador vuelve a cero")
g = d["grupos"][0]
check(g["ciclo"] == [2, 3] and g["conteo"]["etapa"] == 4, f"ciclo 2-3 que se repite por objeto ({g['ciclo']})")
q = eo.preguntas(d, "banda")
check([x["campo"] for x in q] == ["acciones_pausa_3", "conteo_ultimo_1"], "primero las dudas de interpretacion")
q = eo.preguntas(eo.detectar(TEXTO, "banda", DUDAS), "banda")
check([x["campo"] for x in q] == ["etapa_5_fin"] and q[0]["opciones"], "luego solo pregunta cuanto avanza (con opciones)")
d2 = eo.detectar(TEXTO, "banda", ACL)
check(eo.preguntas(d2, "banda") == [] and d2["etapas"][4]["tiempos"] == [4.0], "con la respuesta ya no pregunta")
b = eo.bloque_modelo(d2, "banda")
check("PASO DE DECISION" in b and "CT1" in b and "SQ1_FIN" in b, "el modelo recibe la receta del ciclo con conteo")
check("ARRANCA con I1" in b and "VUELVE a avanzar" in b and "contador vuelve a cero" in b,
      "y el inicio, la reanudacion y el reinicio del contador")
check("TRANSICIONES" in b and "ESTADOS" in b and "FIN:" in b, "y las reglas de transiciones, estados y fin")

# ─────────────────────────────────────────────────────────────────────────
escenario("2. Validacion integral: la historia completa simulada en el ST")
check(se.verificar(lm.cfg_desde_respuesta(CORRECTA), d2) == [], "la logica correcta cumple (3 objetos, 2 corridas)")
ALT = copy.deepcopy(BIEN)
del ALT[4]
ALT[3] = {**ALT[3], "siguiente": 1, "alternativa": {"senal": "CT1", "ir_a": 5}}
D_DIR = eo.detectar(TEXTO, "banda", {**ACL, "conteo_ultimo_1": {"pregunta": "", "respuesta": "Pasa directo"}})
check(se.verificar(lm.cfg_desde_respuesta(resp(ALT)), D_DIR) == [],
      "la variante con alternativa directa vale si el objeto 3 pasa directo")
check(se.verificar(lm.cfg_desde_respuesta(resp(ALT)), d2) != [],
      "y no vale si el usuario dijo que el objeto 3 tambien hace la pausa")
for nombre, r, clave in (("contador sin reset", SIN_RESET, "segunda corrida"),
                         ("rearranca sola al final", REARRANCA, "vuelve a arrancar sola"),
                         ("no reanuda tras la pausa", NO_REANUDA, "Etapa 2"),
                         ("avanza 10 s en vez de 4", AVANZA_10, "Etapa 6")):
    f = se.verificar(lm.cfg_desde_respuesta(r), d2)
    check(f and clave in f[0], f"{nombre}: se detecta ({f[:1]})")
check("contador" in se.verificar(lm.cfg_desde_respuesta(SIN_RESET), d2)[0], "y se sugiere la causa (contador)")

escenario("3. Sin falsos positivos en otras secuencias correctas")
B = lambda sq, mover=True: {"name": "x", "banda": {"mover": mover, "direccion": "derecha", "frecuencia_hz": 30},
                            "motor": {}, "secuencias": sq}
casos = [
    ("Primero enciende la verde 5 segundos, despues la amarilla 3 segundos y al final la roja hasta que presione I2",
     {}, B([{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
         {"salidas": ["Q3"], "avanza": "tiempo", "segundos": 5}, {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 3},
         {"salidas": ["Q5"], "avanza": "flanco", "senal": "I2"}]}], mover=False)),
    ("Primero avanza la banda a la derecha a 30 Hz, cuando el sensor 1 detecte se detiene 3 segundos y luego "
     "regresa a la izquierda", {"etapa_3_fin": {"pregunta": "", "respuesta": "5 s"}},
     B([{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
         {"salidas": [], "motor": ["marcha"], "avanza": "flanco", "senal": "S1"},
         {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 3},
         {"salidas": [], "motor": ["marcha", "dir_2"], "avanza": "tiempo", "segundos": 5}]}])),
    ("Al presionar I1 enciende la verde 5 s, luego la amarilla 2 s, luego la roja 8 s y se repite", {},
     B([{"id": "SQ1", "inicio": "I1", "paro": "I2", "modo": "infinita", "exclusiva": True, "pasos": [
         {"salidas": ["Q3"], "avanza": "tiempo", "segundos": 5}, {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 2},
         {"salidas": ["Q5"], "avanza": "tiempo", "segundos": 8}]}], mover=False)),
    ("Primero la banda avanza hasta que S1 detecte, cuando termine sube la pluma 1 y para cerrar prende la roja 5 s",
     {}, B([{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
         {"salidas": [], "motor": ["marcha"], "avanza": "flanco", "senal": "S1"},
         {"salidas": ["Q8"], "motor": [], "avanza": "tiempo", "segundos": 2},
         {"salidas": ["Q5"], "motor": [], "avanza": "tiempo", "segundos": 5}]}])),
]
for texto, acl, r in casos:
    dd = eo.detectar(texto, "banda", acl)
    check(dd["aplica"] and eo.preguntas(dd, "banda") == [] and se.verificar(lm.cfg_desde_respuesta(r), dd) == [],
          f"correcta y sin quejas: «{texto[:60]}…»")

# ─────────────────────────────────────────────────────────────────────────
escenario("4. Flujo completo: falta un dato critico -> se pregunta antes de generar")
r = generar(TEXTO, acl=DUDAS)
check(r.status == "needs_clarification" and [q["campo"] for q in r.questions] == ["etapa_5_fin"] and not llamadas,
      "pregunta la duracion del avance sin llamar al modelo")

escenario("5. El modelo se equivoca: la simulacion integral se lo devuelve y corrige")
r = generar(TEXTO, SIN_RESET, CORRECTA, acl=ACL)
check(len(llamadas) == 2 and "NO hace lo pedido" in contenido(llamadas[1]) and "segunda corrida" in contenido(llamadas[1]),
      "segunda vuelta con lo que fallo al simular")
check("PASO DE DECISION" in contenido(llamadas[0]), "la receta del ciclo va desde la primera llamada")
check(r.status == "ok" and r.analysis["comportamiento"]["fallas"] == []
      and "etapas (historia completa)" in r.analysis["comportamiento"]["probado"], "la version entregada cumple")

escenario("6. Si nunca cumple, se entrega con un aviso corto (sin pedir confirmar)")
r = generar(TEXTO, REARRANCA, REARRANCA, REARRANCA, acl=ACL)
check(r.status == "ok" and r.analysis["comportamiento"]["fallas"], "se entrega y la falla queda registrada")
check(any(w.startswith("Al simularlo") and "pide corregirlo" in w for w in r.warnings),
      f"aviso corto: {r.warnings}")

escenario("7. Correccion: 'la secuencia esta mal' se revisa contra la instruccion original")
hist = [TEXTO + " [acciones_pausa_3: Al detectar (durante la pausa); conteo_ultimo_1: También lo hace y luego sigue; etapa_5_fin: 4 s]"]
r = generar("la secuencia está mal, revisa", CORRECTA, prev=NO_REANUDA, historial=hist)
c = contenido(llamadas[0])
check("ACCIONES EN ORDEN" in c and "PASO DE DECISION" in c, "el modelo recibe las etapas de la instruccion original")
check("DIAGNOSTICO AUTOMATICO" in c and "Etapa 2" in c, "con el diagnostico de la simulacion de la logica vigente")
check(r.status == "ok" and r.analysis["comportamiento"]["fallas"] == [], "y la correccion se valida con la historia completa")
r = generar("no funciona, corrige", NO_REANUDA, CORRECTA, prev=NO_REANUDA, historial=hist)
check(len(llamadas) == 2 and "MISMA logica" in contenido(llamadas[1]), "repetir la solucion incorrecta no se acepta")

escenario("8. Separar las aclaraciones del historial")
t, a = eo.separar_aclaraciones("Haz algo [etapa_5_fin: 4 s; freq_hz: 20 Hz]")
check(t == "Haz algo" and a["etapa_5_fin"]["respuesta"] == "4 s" and a["freq_hz"]["respuesta"] == "20 Hz",
      "'texto [clave: valor; ...]' -> texto + aclaraciones")

print(f"\nTODAS LAS PRUEBAS DE SECUENCIAS COMPLEJAS PASARON ({OK} comprobaciones).")
