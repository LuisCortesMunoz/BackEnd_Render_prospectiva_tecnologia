"""
test_ronda9.py — pasos 6 (cobertura) y 3 (ediciones directas) del plan; casos del
conjunto nuevo N1-N12 y del banco que fallaban SIN aviso.

  1. cobertura: lo que pide el mensaje actual esta en la logica (N12: la receta ignoraba
     "cambia de sentido"; N2: "12 s" no estaba) y no da falsas alarmas (C6 con invertir)
  2. C9: la pluma de la pausa se prueba en cada vuelta, tambien la ultima
  3. "mejor que sean 12 segundos" actualiza el requisito con tiempo (antes exigia 8 s)
  4. ediciones: Hz, color, tiempo con el anterior (y en una secuencia), tiempo nuevo por
     contexto, duracion de una luz, repeticiones, conteo; y NO se aplican si el mensaje
     pide algo mas
  5. de punta a punta: /generar-logica aplica la edicion sin el modelo y la receta que no
     cubre el mensaje ya no se entrega

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda9.py
"""

import asyncio
import contextlib
import copy
import io
import logging
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_r9_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_r9.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

logging.disable(logging.WARNING)

with contextlib.redirect_stdout(io.StringIO()):
    import app
import cambios_texto as ct
import cobertura
import comportamiento as co
import ediciones
import lavo_motor as lm
import receta_conteo

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_r9.json")
app.BANDA_MOTOR_V7 = True
OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


def cfg(resp):
    with contextlib.redirect_stdout(io.StringIO()):
        c = lm.cfg_desde_respuesta(resp)
    c["respuesta_llm"] = resp
    return c


def banda(hz, d="derecha", **k):
    return {"banda": {"mover": True, "direccion": d, "frecuencia_hz": hz}, **k}


MOT = {"arranque": "I1", "paro": "I2", "modo": "enclavado"}

# ── 1. cobertura ────────────────────────────────────────────────────────────
escenario("1. cobertura del mensaje actual")
T12 = ("I1 arranca la banda a 30 Hz hacia la derecha. Al contar 4 cajas en el sensor 2, la banda cambia de "
       "sentido durante 5 segundos y después se detiene.")
sin_inv = banda(30, motor={"arranque": "I1", "modo": "enclavado", "paro": "SQ1_CICLO"},
                contadores=[{"id": "CT1", "entrada": "S2", "preset": 4, "modo": "una_vez", "reset": "SQ1_CICLO"}],
                secuencias=[{"id": "SQ1", "inicio": "CT1", "modo": "una_vez", "pasos": [
                    {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5},
                    {"motor": ["paro"], "avanza": "tiempo", "segundos": 0.5}]}])
check(cobertura.faltantes(T12, cfg(sin_inv)) == ["cambio de sentido"], "N12: sin invertir se detecta")
con_inv = copy.deepcopy(sin_inv)
con_inv["secuencias"][0]["pasos"][0]["motor"] = ["marcha", "dir_2"]
check(cobertura.faltantes(T12, cfg(con_inv)) == [], "N12: con dir_2 durante los 5 s cumple")
T2 = "Al pulsar I1 la banda corre a 40 Hz durante 8 segundos y se para sola."
N2 = banda(40, motor={"arranque": "I1", "paro": "C1", "modo": "enclavado"},
           condiciones=[{"id": "C1", "op": "OR", "entradas": ["I2", "T1"], "flanco": "nivel"}],
           timers=[{"id": "T1", "tipo": "TON", "entrada": "BANDA_CORRIENDO", "segundos": 8}])
check(cobertura.faltantes("Mejor que sean 12 segundos.", cfg(N2)) == ["12 s"], "N2 t2: si quedo en 8 s falta 12 s")
check(cobertura.faltantes(T2, cfg(N2)) == [], "N2 t1: 40 Hz y 8 s estan")
check(cobertura.faltantes("Cambia los 8 segundos por 12 segundos.", cfg(N2)) == ["12 s"],
      "el tiempo viejo de 'por' no se exige, el nuevo si")
T6 = ("I1 arranca la banda hacia la derecha a 25 Hz. Cuando el sensor 2 detecta una caja, la banda invierte el "
      "sentido y avanza 5 segundos a la izquierda.")
C6 = banda(25, motor=MOT, secuencias=[{"id": "SQ1", "inicio": "S2", "modo": "una_vez", "pasos": [
    {"motor": ["marcha", "invertir"], "avanza": "tiempo", "segundos": 5}]}])
check(cobertura.faltantes(T6, cfg(C6)) == [], "C6: con 'invertir' la izquierda queda cubierta (sin falsa alarma)")
check(cobertura.faltantes("Quiero que con el botón verde arranque a 25 hertz.", cfg(banda(25, motor=MOT))) == [],
      "'boton verde' es un boton, no la luz verde")
check(cobertura.faltantes("Ya no uses la pluma 2.", cfg(banda(25, motor=MOT))) == [], "lo que se quita no se exige")
check("parpadeo" in cobertura.faltantes("Que la amarilla parpadee mientras avanza.",
                                        cfg(banda(25, motor=MOT, salidas={"Q4": "BANDA_CORRIENDO"}))),
      "parpadeo sin timer INTERMITENTE se detecta")

# ── 2. C9: la pluma en cada vuelta ──────────────────────────────────────────
escenario("2. pluma de la pausa en cada vuelta (banco C9 r12a)")
H9 = "Al presionar I1 la banda avanza a 30 Hz hasta que el sensor 2 detecta una caja; entonces se detiene 4 " \
     "segundos, la pluma 1 sube y la banda vuelve a avanzar. Esto se repite 3 veces y después la banda se detiene."
reqs9 = co.requisitos([H9])
pl = next(r for r in reqs9 if r["tipo"] == "pluma_pausa")
check(pl.get("veces") == 3, "la pluma de la pausa se prueba 3 veces")
C9_SALTA = banda(30, motor={"modo": "enclavado", "paro": "I3"},
                 contadores=[{"id": "CT1", "entrada": "I5", "preset": 3, "modo": "una_vez"}],
                 secuencias=[{"id": "SQ1", "inicio": "I1", "paro": "I3", "modo": "una_vez", "pasos": [
                     {"motor": ["marcha"], "avanza": "senal", "senal": "I5"},
                     {"salidas": ["Q8"], "motor": ["paro"], "avanza": "tiempo", "segundos": 4, "siguiente": 4,
                      "alternativa": {"senal": "CT1", "ir_a": 3}},
                     {"motor": ["paro"], "avanza": "tiempo", "segundos": 0, "siguiente": "fin"},
                     {"motor": ["marcha"], "avanza": "tiempo", "segundos": 0, "siguiente": 1}]}])
f = co.verificar(cfg(C9_SALTA), [pl])
check(len(f) == 1 and "3 de 3" in f[0], "r12a: en la 3a deteccion salta a 'fin' sin la pluma: se detecta")
C9_BIEN = banda(30, motor={"modo": "enclavado", "paro": "I3"},
                secuencias=[{"id": "SQ1", "inicio": "I1", "paro": "I3", "modo": "repetir", "repeticiones": 3,
                             "pasos": [{"motor": ["marcha"], "avanza": "senal", "senal": "I5"},
                                       {"salidas": ["Q8"], "motor": ["paro"], "avanza": "tiempo", "segundos": 4}]}])
check(co.verificar(cfg(C9_BIEN), [pl]) == [], "la que sube la pluma las 3 veces cumple")

# ── 3. tiempo nuevo sin el anterior ────────────────────────────────────────
escenario("3. 'mejor que sean 12 segundos' actualiza el requisito")
check(ct.tiempo_nuevo("Mejor que sean 12 segundos.") == (set(), 12.0), "se lee el tiempo nuevo sin contexto")
check(ct.tiempo_nuevo("Que la luz verde dure solo 3 segundos.") == ({"verde"}, 3.0), "y con contexto")
check(ct.tiempo_nuevo("Cambia los 10 segundos por 6 segundos.") is None, "si dice el anterior lo cubre sustituciones")
ps = [r for r in co.requisitos([T2, "Mejor que sean 12 segundos."]) if r["tipo"] == "para_sola"]
check(len(ps) == 1 and ps[0]["segundos"] == 12.0, "N2: el paro solo se exige a los 12 s (antes 8)")

escenario("3b. paro por conteo: 'deja la banda andando' y el boton como sujeto")
check(co.paro_por_conteo("Arranca con I1. El sensor 2 cuenta cajas; al llegar a 6 la roja se enciende y la banda "
                         "sigue avanzando. El pulsador rojo la detiene.") == {},
      "'sigue avanzando' al contar: no se exige que se detenga")
check(co.paro_por_conteo("Arranca con I1. El sensor 2 cuenta cajas; al llegar a 6 se prende la amarilla. I2 detiene "
                         "la banda.") == {}, "'I2 detiene la banda' es el paro del boton, no del conteo")
pc6 = co.paro_por_conteo("Arranca con I1. El sensor 2 cuenta cajas; al llegar a 6 la banda se detiene. El botón I2 la "
                         "detiene también.")
check(pc6.get("conteo") == 6, "si el conteo SI la detiene se sigue leyendo")
T_SIGUE = ("Arranca con I1. El sensor 2 cuenta cajas; al llegar a 6 la roja se enciende y la banda sigue avanzando. "
           "El pulsador rojo la detiene.")
rs = [r for r in co.requisitos([T_SIGUE]) if r["tipo"] == "sigue_al_contar"]
check(len(rs) == 1 and rs[0]["conteo"] == 6, "'sigue avanzando' al contar crea el requisito de que SIGA")
para_al_6 = banda(30, motor={"arranque": "I1", "paro": "C1", "modo": "enclavado"},
                  condiciones=[{"id": "C1", "op": "OR", "entradas": ["I2", "CT1"], "flanco": "nivel"}],
                  contadores=[{"id": "CT1", "entrada": "S2", "preset": 6, "modo": "una_vez", "reset": "I1"}],
                  salidas={"Q5": "CT1"})
check(co.verificar(cfg(para_al_6), rs) != [], "una logica que se detiene al contar 6 se detecta")
sigue_al_6 = copy.deepcopy(para_al_6)
sigue_al_6["motor"]["paro"] = "I2"
check(co.verificar(cfg(sigue_al_6), rs) == [], "y la que sigue avanzando cumple")

# ── 4. ediciones directas ──────────────────────────────────────────────────
escenario("4. ediciones sobre la vigente")
r = ediciones.aplicar("Mejor que sean 12 segundos.", N2)
check(r and r[0]["timers"][0]["segundos"] == 12, "N2: 8 s -> 12 s")
check(N2["timers"][0]["segundos"] == 8, "la vigente no se modifica")
r = ediciones.aplicar("Cambia la velocidad a 45 Hz.", N2)
check(r and r[0]["banda"]["frecuencia_hz"] == 45, "Hz")
N5 = banda(35, motor=MOT, memorias=[{"id": "M1", "set": "CT1", "reset": "I2", "prioridad": "set"}],
           contadores=[{"id": "CT1", "entrada": "S1", "reset": "I2", "preset": 5, "modo": "ciclico"}],
           salidas={"Q3": "M1"})
r = ediciones.aplicar("Que la luz verde dure solo 3 segundos.", N5)
t_v = next(t for t in r[0]["timers"] if t["id"] == r[0]["salidas"]["Q3"]) if r else None
check(t_v and t_v["tipo"] == "PULSO" and t_v["entrada"] == "M1" and t_v["segundos"] == 3,
      "N5: la verde sin tiempo pasa por un PULSO de 3 s")
DOS = banda(10, secuencias=[
    {"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [{"motor": ["marcha"], "avanza": "tiempo", "segundos": 10}]},
    {"id": "SQ2", "inicio": "S2", "modo": "una_vez", "pasos": [{"salidas": ["Q5"], "avanza": "tiempo", "segundos": 10}]}])
r = ediciones.aplicar("En la secuencia 1 cambia los 10 segundos por 6 segundos.", DOS)
check(r and r[0]["secuencias"][0]["pasos"][0]["segundos"] == 6 and r[0]["secuencias"][1]["pasos"][0]["segundos"] == 10,
      "'en la secuencia 1': solo cambia ahi")
r = ediciones.aplicar("La luz de la secuencia 2 ahora debe ser la amarilla en lugar de la roja.", DOS)
check(r and r[0]["secuencias"][1]["pasos"][0]["salidas"] == ["Q4"], "color: roja -> amarilla")
C8 = banda(35, secuencias=[{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
    {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 3},
    {"motor": ["marcha"], "avanza": "senal", "senal": "I2"},
    {"salidas": ["Q5"], "avanza": "tiempo", "segundos": 5, "siguiente": "fin"}]}])
r = ediciones.aplicar("La espera con la amarilla debe ser de 5 segundos.", C8)
check(r and [p.get("segundos") for p in r[0]["secuencias"][0]["pasos"]] == [5, None, 5],
      "'la espera con la amarilla': la amarilla manda (no la roja de 5 s)")
REP = copy.deepcopy(C9_BIEN)
r = ediciones.aplicar("En vez de 3 veces que sean 2.", REP)
check(r and r[0]["secuencias"][0]["repeticiones"] == 2, "repeticiones")
CT = banda(30, motor=MOT, contadores=[{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez", "reset": "I1"}],
           salidas={"Q5": "CT1"})
r = ediciones.aplicar("Cambia el conteo a 5 cajas en lugar de 3.", CT)
check(r and r[0]["contadores"][0]["preset"] == 5, "conteo")
check(ediciones.aplicar("Mejor que sean 12 segundos y que se prenda la roja.", N2) is None,
      "si pide algo mas (la roja) no se aplica: lo resuelve el modelo")
check(ediciones.aplicar("Cambia la velocidad a 45 Hz y que el sensor 1 la detenga.", N2) is None,
      "Hz + sensor nuevo: no se aplica")
check(ediciones.aplicar("Que dure 5 segundos.", C8) is None, "sin contexto y con varios tiempos no se adivina")

# ── 5. de punta a punta ────────────────────────────────────────────────────
escenario("5. /generar-logica: edicion sin el modelo; receta que no cubre ya no se entrega")
llamadas = []


def _llm(msgs):
    llamadas.append(msgs)
    raise AssertionError("no se debia llamar al modelo")


app.llamar_modelo_json = _llm
app.llamar_extractor_json = _llm
vig = cfg(N2)
vig["name"] = "N2"
ctx = {"programa_anterior": {"metadata": {"name": "N2", "engine_config": vig}}, "historial": [T2]}
res = asyncio.run(app.generar_logica(app.LogicaRequest(texto="Mejor que sean 12 segundos.", device="banda",
                                                       contexto=ctx)))
check(res.status == "ok" and res.analysis.get("fuente") == "edicion" and not llamadas,
      f"N2 t2: edicion directa, 0 llamadas ({res.status}, {res.analysis.get('fuente')})")
check(any("8 s -> 12 s" in w for w in res.warnings), "avisa el cambio aplicado")
T1_ = next(t for t in res.logic["band"]["engine"]["timers"] if t["id"] == "T1")
check(T1_["segundos"] == 12, "la logica entregada para a los 12 s")
plan = receta_conteo.detectar([T12], {})
if plan:
    with contextlib.redirect_stdout(io.StringIO()):
        rr = app._respuesta_receta(T12, plan, [T12], {"requisitos": [], "etapas": {"aplica": False}}, None)
    check(rr is None, "N12: la receta (sin cambio de sentido) ya no se entrega")
else:
    check(True, "N12: la receta ya no la toma")

print(f"\nTODAS LAS PRUEBAS DE LA RONDA 9 PASARON ({OK} comprobaciones).")
