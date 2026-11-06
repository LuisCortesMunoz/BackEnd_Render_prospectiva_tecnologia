"""
test_rapidez.py — generacion de la banda mas rapida SIN cambiar lo que se revisa.

Corre sin Groq (modelo simulado) y sin PLC (ST v7 real en lavo_sim):
    python test_rapidez.py

  B  el ST se ejecuta con variables locales (banda_st_interprete): el estado
     completo del PLC es IDENTICO scan a scan al del ejecutor anterior (v7 y v6)
  A  cache_simulacion: el mismo programa con la misma prueba no se vuelve a
     simular (diagnostico, linea base, validacion, regresion); resultados
     iguales, copias independientes, y lo cortado por tiempo no se guarda
  C  tope de tiempo (LAVO_TIEMPO_MAX_S): sin tiempo para otro intento se
     entrega lo mejor que hay o se conserva la vigente; con 0 no hay tope
"""

import asyncio
import copy
import os
import random
import tempfile
import time

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_rap_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_rap.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import banda_st_interprete as si
import cache_simulacion as cs
import comportamiento as co
import etapas_orden as eo
import lavo_motor as lm
import lavo_sim as ls
import regresion_banda as rg
import simulacion_etapas as se

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_rap.json")
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


# Caso de referencia (5 nov), el mismo de test_correcciones_regresion.
T = ("Al presionar el pulsador I1, la banda transportadora se pone en marcha a una frecuencia de 20 Hz. Cuando el "
     "Sensor 1 detecta un objeto, la pluma 1 se eleva y la pluma 2 desciende. Posteriormente, al activarse el "
     "Sensor 2, la banda se detiene durante 3 segundos y después continúa avanzando; además, la pluma 2 se eleva, "
     "la pluma 1 desciende y se registra un conteo. Una vez que el Sensor 2 acumula 3 detecciones, la banda "
     "continúa avanzando únicamente durante 5 segundos y luego se detiene automáticamente. Finalmente, la pluma 1 "
     "se eleva, la pluma 2 desciende y el contador se reinicia a cero, respetando ese orden.")
DETECTAR = {"pregunta": "", "respuesta": "Al detectar (durante la pausa)"}
TAMBIEN = {"pregunta": "", "respuesta": "También lo hace y luego sigue"}
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
TAMBIEN_DET = prog([S1_ESPERA, PLUMAS_S1,
                    {"salidas": ["Q6", "Q9"], "motor": [], "avanza": "tiempo", "segundos": 3},
                    {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0, "siguiente": 1,
                     "alternativa": {"senal": "CT1", "ir_a": 5}}] + FINAL)
DIRECTO_DET = prog([S1_ESPERA, PLUMAS_S1,
                    {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0,
                     "alternativa": {"senal": "CT1", "ir_a": 5}},
                    {"salidas": ["Q6", "Q9"], "motor": [], "avanza": "tiempo", "segundos": 3, "siguiente": 1}]
                   + FINAL)
SIN_ARRANQUE = copy.deepcopy(DIRECTO_DET)
SIN_ARRANQUE["secuencias"][0]["inicio"] = "S2"
ORIG = T + " [acciones_pausa_3: Al detectar (durante la pausa); conteo_ultimo_1: También lo hace y luego sigue]"
CORR = "esta mal: en la tercera deteccion ya no se detiene, pasa directo a los 5 segundos"

# ---------------------------------------------------------------------------
escenario("1. B: ejecutor del ST con variables locales = mismo estado scan a scan")


def _estado(V):
    out = {}
    for k, v in V.items():
        if isinstance(v, dict):
            out[k] = {i: x for i, x in v.items() if x}
        elif v:
            out[k] = v
    return out


with open(ls.ST_V7, encoding="utf-8") as f:
    viejo_v7 = si.compilar_st(f.read(), locales=False)[0]
check(si.compilar_st("A := B + 1;")[2].splitlines() == ["def scan(V):", "    v_A = V['A']", "    v_B = V['B']",
                                                        "    v_A = v_B + 1", "    V['A'] = v_A"],
      "el ejecutor usa variables locales (y solo toca V al entrar y al salir)")
scans = 0
for p in (TAMBIEN_DET, DIRECTO_DET, SIN_ARRANQUE):
    cfg = lm.cfg_desde_respuesta(p)
    for semilla in range(2):
        rnd = random.Random(semilla)
        a, b = ls.SimV7(), ls.SimV7()
        b._scan = viejo_v7
        a.cargar(cfg)
        b.cargar(cfg)
        assert _estado(a.V) == _estado(b.V), "distinto tras cargar"
        for _ in range(2500):
            if rnd.random() < 0.02:
                k, v = rnd.choice(["I1", "I2", "I3", "S1", "S2"]), rnd.random() < 0.5
                a.poner(**{k: v})
                b.poner(**{k: v})
            a.scan()
            b.scan()
            scans += 1
            assert _estado(a.V) == _estado(b.V) and a.vfd_hz == b.vfd_hz, f"distinto en el scan {scans}"
check(True, f"v7: estado completo del PLC identico en {scans} scans (3 programas, entradas al azar)")

with open(si.ST_V6, encoding="utf-8") as f:
    viejo_v6 = si.compilar_st(f.read(), locales=False)[0]
import banda_motor as bm
ec = bm.compilar_motor({"enable": True, "freq_hz": 30, "direction": "derecha", "engine": {}})
a, b = si.SimST(ec), si.SimST(ec)
b._scan = viejo_v6
rnd = random.Random(7)
for n in range(3000):
    if rnd.random() < 0.02:
        k, v = rnd.choice(["I1", "I2", "I3", "S1", "S2"]), rnd.random() < 0.5
        a.poner(**{k: v})
        b.poner(**{k: v})
    a.scan()
    b.scan()
    assert _estado(a.V) == _estado(b.V), f"v6 distinto en el scan {n}"
check(True, "v6: estado identico en 3000 scans")

cfg = lm.cfg_desde_respuesta(TAMBIEN_DET)
d = eo.detectar(T, "banda", {"acciones_pausa_3": DETECTAR, "conteo_ultimo_1": TAMBIEN})
sim = ls.SimV7()
sim.cargar(cfg)
t0 = time.perf_counter()
sim.correr(10)
por_scan = (time.perf_counter() - t0) / 1000 * 1000
print(f"      ({por_scan:.2f} ms por scan)")

# ---------------------------------------------------------------------------
escenario("2. A: la misma simulacion no se repite")
cs.limpiar()
t0 = time.perf_counter()
f1 = se.verificar(cfg, d)
t1 = time.perf_counter() - t0
check(f1 == [] and cs.estadistica == {"aciertos": 0, "fallos": 1}, "1a vez: se simula (cumple)")
t0 = time.perf_counter()
f2 = se.verificar(cfg, d)
t2 = time.perf_counter() - t0
check(f2 == f1 and cs.estadistica["aciertos"] == 1 and t2 < t1 / 10,
      f"2a vez: mismo resultado desde el cache ({t1:.2f} s -> {t2 * 1000:.1f} ms)")
check(rg.bateria(cfg, [], d) == {"secuencia SQ1": []} and cs.estadistica["aciertos"] == 2,
      "la bateria de regresion reutiliza la simulacion del diagnostico")

# un programa que falla: el resultado del cache es el mismo y es una copia
dd = eo.detectar(T, "banda", {"acciones_pausa_3": DETECTAR, "conteo_ultimo_1": {"pregunta": "", "respuesta": "Pasa directo"}})
cfg_t = lm.cfg_desde_respuesta(TAMBIEN_DET)
g1 = se.verificar(cfg_t, dd)
g2 = se.verificar(cfg_t, dd)
check(g1 and g1 == g2, f"una falla sale igual del cache ({g1[0][:60]}...)")
g2.append("basura")
check(se.verificar(cfg_t, dd) == g1, "el cache entrega copias (modificar el resultado no lo altera)")

cfg_otro = lm.cfg_desde_respuesta(DIRECTO_DET)
antes = cs.estadistica["fallos"]
check(se.verificar(cfg_otro, dd) == [] and cs.estadistica["fallos"] == antes + 1,
      "otro programa no usa el resultado de otro (se simula)")

reqs = [{"tipo": "arranca_con", "senal": "I1", "texto": ""}]
c1 = co.verificar(lm.cfg_desde_respuesta(SIN_ARRANQUE), reqs)
a0 = cs.estadistica["aciertos"]
c2 = co.verificar(lm.cfg_desde_respuesta(SIN_ARRANQUE), reqs)
check(c1 and c1 == c2 and cs.estadistica["aciertos"] == a0 + 1, f"comportamiento tambien ({c1[0]})")
b = rg.bateria(lm.cfg_desde_respuesta(SIN_ARRANQUE), reqs, dd)
check(b["comportamiento: arranca_con I1"] == c1, "y la bateria (requisito por requisito) lo reutiliza")

cs.limpiar()
check(se.verificar(cfg_t, dd, presupuesto_s=-1) == [] and not cs._datos,
      "una prueba cortada por tiempo no se guarda")
f, completo = se._verificar_grupo(cfg_t["band"], dd, dd["grupos"][0], time.monotonic() - 1)
check(not completo, "un grupo cortado por tiempo se marca incompleto")
check(se.verificar(cfg_t, dd) == g1, "despues se simula completo y da lo mismo")

# ---------------------------------------------------------------------------
escenario("3. A: una correccion completa simula menos")
respuestas, llamadas = [], []
espera_modelo = [0.0]


def _llm(msgs):
    llamadas.append(copy.deepcopy(msgs))
    if espera_modelo[0]:
        time.sleep(espera_modelo[0])
    if not respuestas:
        raise AssertionError("el modelo fue llamado sin respuesta preparada")
    return copy.deepcopy(respuestas.pop(0))


app.llamar_modelo_json = _llm


def generar(texto, *resp, prev=None, historial=None):
    respuestas[:] = list(resp)
    llamadas.clear()
    c = lm.cfg_desde_respuesta(prev)
    c["respuesta_llm"] = prev
    ctx = app.ContextoLadder(programa_anterior={"metadata": {"name": prev.get("name"), "engine_config": c}},
                             historial=historial or [])
    return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", contexto=ctx)))


cs.limpiar()
r = generar(CORR, SIN_ARRANQUE, DIRECTO_DET, prev=TAMBIEN_DET, historial=[ORIG])
check(r.status == "ok" and r.analysis["fuente"] == "modelo" and len(llamadas) == 2,
      "la correccion da el mismo resultado que antes (rompe -> corrige)")
check(cs.estadistica["aciertos"] >= 2,
      f"y reutiliza simulaciones: {cs.estadistica['aciertos']} aciertos, {cs.estadistica['fallos']} simuladas")

# ---------------------------------------------------------------------------
escenario("4. C: tope de tiempo")
cs.limpiar()
app.TIEMPO_MAX_S, app.MARGEN_FINAL_S = 1.0, 0.0
app.TIEMPO_FALLA_S = 0.0          # sin el reintento por falla clara (lo prueba test_ronda4)
espera_modelo[0] = 0.6
r = generar(CORR, SIN_ARRANQUE, DIRECTO_DET, prev=TAMBIEN_DET, historial=[ORIG])
check(len(llamadas) == 1, "sin tiempo para otro intento: no se llama otra vez al modelo")
check(r.status == "ok" and r.analysis["fuente"] == "conservada"
      and r.logic["band"]["engine"]["secuencias"][0]["inicio"] == "I1",
      "lo unico que hubo rompia el arranque: se conserva la vigente")
check(r.analysis["metricas"].get("tope_tiempo") == 1, "queda en las metricas (tope_tiempo)")

app.TIEMPO_MAX_S = 0
r = generar(CORR, SIN_ARRANQUE, DIRECTO_DET, prev=TAMBIEN_DET, historial=[ORIG])
check(len(llamadas) == 2 and r.analysis["fuente"] == "modelo", "LAVO_TIEMPO_MAX_S=0: sin tope (reintenta)")

app.TIEMPO_MAX_S = 100.0
check(app._hay_tiempo_para_reintento(None), "el primer intento siempre se hace")
app.TIEMPO_MAX_S, app.MARGEN_FINAL_S = 100.0, 10.0
app.TIEMPO_FALLA_S = 90.0
espera_modelo[0] = 0.0

print(f"\nTODAS LAS PRUEBAS DE RAPIDEZ PASARON ({OK} comprobaciones).")
