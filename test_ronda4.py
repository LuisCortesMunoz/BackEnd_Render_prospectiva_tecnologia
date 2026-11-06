"""
test_ronda4.py — 4a ronda del banco de pruebas con Groq real (programas tomados
de lo que devolvio Render):

  1. "se detiene N s y luego continua" en CADA deteccion se verifica (C9, C4, C7)
  2. "la espera con la amarilla debe ser de 5 s" cambia el tiempo de esa etapa (C8)
  3. paso de pluma de 0 s: se le dan 3 s para que alcance a moverse (C2)
  4. con una falla clara se permite reintentar aunque el intento fuera lento (C9)

Corre sin Groq (modelo simulado) y sin PLC (lavo_sim):  python test_ronda4.py
"""

import asyncio
import copy
import os
import tempfile
import time

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_r4_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_r4.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import cambios_texto as ct
import comportamiento as co
import etapas_orden as eo
import lavo_motor as lm
import lavo_sim as ls
import reparaciones_banda as rb

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_r4.json")
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


# ---------------------------------------------------------------------------
escenario("1. Pausa en cada deteccion")
T9 = ("Al presionar I1 la banda avanza a 30 Hz hasta que el sensor 2 detecta una caja; entonces se detiene 4 "
      "segundos, la pluma 1 sube y la banda vuelve a avanzar. Esto se repite 3 veces y después la banda se detiene.")
T4 = "Agrega que cuando el sensor 1 detecte una caja la banda se detenga 4 segundos y luego continúe sola."
T7 = "Agrega que cuando el sensor 2 detecte, la banda se detenga 2 segundos y luego siga."
check(co.pausa_por_deteccion(T9) == {"senal": "S2", "segundos": 4.0}, "C9: S2, 4 s (con ';' entre el sensor y la pausa)")
check(co.pausa_por_deteccion(T4) == {"senal": "S1", "segundos": 4.0}, "C4: S1, 4 s")
check(co.pausa_por_deteccion(T7) == {"senal": "S2", "segundos": 2.0}, "C7: S2, 2 s")
check(co.pausa_por_deteccion("Cuando el sensor 2 detecta, la banda se detiene.") == {},
      "sin tiempo ni 'luego sigue' no es una pausa")
r9 = [x for x in co.requisitos([T9]) if x["tipo"] == "pausa_sensor"]
check(r9 and r9[0]["ciclos"] == 2, "con 'se repite 3 veces' se prueban 2 pausas (la 3a vuelta termina)")
check(not any(x["tipo"] == "pausa_sensor" for x in co.requisitos([T9, "En vez de 3 veces que sean 1."])) or
      [x for x in co.requisitos([T9, "En vez de 3 veces que sean 2."]) if x["tipo"] == "pausa_sensor"][0]["ciclos"] == 1,
      "con 2 vueltas se prueba 1 pausa")
BIEN = {"name": "c9", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30}, "motor": {"paro": "I3"},
        "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I3", "modo": "repetir", "repeticiones": 3,
                        "exclusiva": True, "pasos": [
                            {"motor": ["marcha"], "avanza": "senal", "senal": "I5"},
                            {"salidas": ["Q8"], "motor": ["paro"], "avanza": "tiempo", "segundos": 4},
                            {"motor": ["marcha"], "avanza": "tiempo", "segundos": 0}]}]}
SALTA = {"name": "c9", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30}, "motor": {"paro": "I3"},
         "condiciones": [{"id": "C1", "op": "NOR", "entradas": ["CT1"], "flanco": "nivel"}],
         "contadores": [{"id": "CT1", "entrada": "I5", "reset": "I1", "preset": 3, "modo": "nivel"}],
         "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
             {"motor": ["marcha"], "avanza": "senal", "senal": "I5", "siguiente": 2},
             {"motor": ["paro"], "salidas": ["Q8"], "avanza": "tiempo", "segundos": 4,
              "alternativa": {"senal": "C1", "ir_a": 3}, "siguiente": "fin"},
             {"motor": ["marcha"], "avanza": "senal", "senal": "I5", "siguiente": 2}]}]}
req = [x for x in co.requisitos([T9]) if x["tipo"] in ("arranca_con", "pausa_sensor", "para_tras_repetir")]
check(co.verificar(lm.cfg_desde_respuesta(BIEN), req) == [], "C9 de la ronda 1 (pausa 4 s y para tras 3): cumple")
f = co.verificar(lm.cfg_desde_respuesta(SALTA), req)
check(f and "NO se detiene 4 s" in f[0], f"C9 de la ronda 3B (salta la pausa con una alternativa): se detecta")
T1 = ("Al presionar el pulsador I1, la banda se pone en marcha a 20 Hz. Cuando el Sensor 1 detecta un objeto, la "
      "pluma 1 se eleva y la pluma 2 desciende. Posteriormente, al activarse el Sensor 2, la banda se detiene durante "
      "3 segundos y después continúa avanzando.")
import intencion_banda as ib
i1 = ib.analizar(T1, [], {}, None, False)
check(not i1["etapas"]["aplica"] or not any(x["tipo"] == "pausa_sensor" for x in i1["requisitos"]),
      "en acciones en orden (S1 antes que S2) la pausa la prueba la historia, no la prueba suelta")

# ---------------------------------------------------------------------------
escenario("2. Tiempo nuevo por contexto (C8)")
T8 = ("Al presionar I1, primero se enciende la luz amarilla durante 3 segundos y después la banda arranca a 35 Hz. "
      "Al presionar I2 la banda se detiene y se enciende la luz roja durante 5 segundos.")
check(ct.tiempo_por_contexto("La espera con la amarilla debe ser de 5 segundos.") == [({"espera", "amarilla"}, 5.0)],
      "'la espera con la amarilla debe ser de 5 s' -> amarilla, 5 s")
check(ct.tiempo_por_contexto("cambia los 3 s por 5 s") == [], "si dice el anterior lo cubre la sustitucion normal")
d = eo.de_historial([T8], "banda", {}, posteriores=["La espera con la amarilla debe ser de 5 segundos."])
check([e["tiempos"] for e in d["etapas"]] == [[5.0], [], [5.0]], "la etapa de la amarilla pasa de 3 a 5 s; la roja sigue en 5")
d = eo.de_historial([T8], "banda", {}, posteriores=["La espera debe ser de 7 segundos."])
check([e["tiempos"] for e in d["etapas"]] == [[3.0], [], [5.0]], "sin luz/pluma que la identifique no se toca (ambiguo)")
d = eo.de_historial([T8], "banda", {}, posteriores=["Que la roja dure 8 segundos."])
check([e["tiempos"] for e in d["etapas"]] == [[3.0], [], [8.0]], "'que la roja dure 8 s' -> la etapa de la roja")

# ---------------------------------------------------------------------------
escenario("3. Pluma de 0 s (C2)")
C2 = {"name": "c2", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
      "motor": {"arranque": "I1", "paro": "I2", "modo": "enclavado"},
      "salidas": {"Q3": "BANDA_CORRIENDO", "Q5": "BANDA_DETENIDA"},
      "secuencias": [{"id": "SQ1", "inicio": "I4", "modo": "una_vez", "exclusiva": True, "pasos": [
          {"salidas": ["Q8"], "motor": [], "avanza": "tiempo", "segundos": 3, "siguiente": 2},
          {"salidas": ["Q9"], "motor": [], "avanza": "tiempo", "segundos": 0, "siguiente": "fin"}]}]}
r2, av = rb.aplicar(C2, "")
check(r2["secuencias"][0]["pasos"][1]["segundos"] == 3.0 and av, "pluma 1 baja en 0 s -> 3 s (con aviso)")
s = ls.SimV7()
s.cargar(lm.cfg_desde_respuesta(r2))
s.correr(0.5); s.pulsar("I1"); s.correr(1.5); s.pulsar("S1"); s.correr(3.5)
check("Q9" in s.salidas(), "ahora la pluma 1 baja de verdad (Q9 se mantiene)")
SIGUE = copy.deepcopy(C2)
SIGUE["secuencias"][0]["pasos"] = [{"salidas": ["Q8"], "avanza": "tiempo", "segundos": 0},
                                   {"salidas": ["Q8"], "avanza": "tiempo", "segundos": 3}]
check(rb.aplicar(SIGUE, "")[1] == [], "si el paso siguiente mantiene la pluma, no se toca")
SENAL = copy.deepcopy(C2)
SENAL["secuencias"][0]["pasos"][1] = {"salidas": ["Q9"], "avanza": "senal", "senal": "I5"}
check(rb.aplicar(SENAL, "")[1] == [], "un paso que espera una senal no se toca")

# ---------------------------------------------------------------------------
escenario("4. Falla clara: reintento aunque el intento fuera lento")
T2 = "Con I1 la banda arranca a 30 Hz hacia la derecha y con I2 se detiene."
SIN_PARO = {"name": "x", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
            "motor": {"arranque": "I1", "modo": "enclavado"}, "analisis": {}, "preguntas": []}
CON_PARO = copy.deepcopy(SIN_PARO)
CON_PARO["motor"]["paro"] = "I2"
respuestas, llamadas = [], []


def _llm(msgs):
    llamadas.append(1)
    time.sleep(0.6)
    return copy.deepcopy(respuestas.pop(0))


app.llamar_modelo_json = _llm
app.TIEMPO_MAX_S, app.MARGEN_FINAL_S = 1.0, 0.0
app.TIEMPO_FALLA_S = 0.0
respuestas[:], llamadas[:] = [SIN_PARO, CON_PARO], []
r = asyncio.run(app.generar_logica(app.LogicaRequest(texto=T2, device="banda")))
check(len(llamadas) == 1, "con el tope de antes: el intento lento no deja reintentar")
app.TIEMPO_FALLA_S = 60.0
respuestas[:], llamadas[:] = [SIN_PARO, CON_PARO], []
r = asyncio.run(app.generar_logica(app.LogicaRequest(texto=T2, device="banda")))
check(len(llamadas) == 2 and r.logic["band"]["engine"]["motor"].get("paro") == "I2",
      "con falla clara (I2 no detiene) se reintenta y se entrega la corregida")
app.TIEMPO_MAX_S, app.MARGEN_FINAL_S, app.TIEMPO_FALLA_S = 100.0, 10.0, 90.0

print(f"\nTODAS LAS PRUEBAS DE LA RONDA 4 PASARON ({OK} comprobaciones).")
