"""
test_ronda6.py — el ejemplo final del usuario sale con la receta fija, sin el modelo:

  "Quiero avanzar la banda con i1 a 40 hz a la derecha. Cada que el sensor uno
   detecte se espere 3 segundos antes de avanzar. Aparte quiero lograr que cuando
   el temporizador uno llegue a un contador de 3, se haga la secuencia de que
   avance la banda durante 5 segundos, se detenga por sis ola y reinicie el
   contador para reiniciar el ciclo esperando a que el susaurio presione el botn i1"

  1. receta_conteo cubre "pausa de N s en cada deteccion + al contar X avanza Y s,
     se detiene y reinicia el ciclo (I1)" y lo verifica con 2 ciclos completos
  2. los errores de escritura se corrigen ANTES del modelo/extractor
  3. de punta a punta: /generar-logica lo arma sin llamar al modelo

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda6.py
"""

import asyncio
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_r6_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_r6.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import cambios_texto as ct
import comportamiento as co
import lavo_motor as lm
import lavo_sim as ls
import receta_conteo as rc

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_r6.json")
app.BANDA_MOTOR_V7 = True

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


T = ("Quiero avanzar la banda con i1 a 40 hz a la derecha. Cada que el sensor uno detecte se espere 3 segundos "
     "antes de avanzar. Aparte quiero lograr que cuando el temporizador uno llegue a un contador de 3, se haga la "
     "secuencia de que avance la banda durante 5 segundos, se detenga por sis ola y reinicie el contador para "
     "reiniciar el ciclo esperando a que el susaurio presione el botn i1")

# ---------------------------------------------------------------------------
escenario("1. Receta con pausa por deteccion")
p = rc.detectar([T], {})
check(p and p["conteo"] == 3 and p["senal"] == "S1" and p["segundos"] == 5 and p["pausa"] == 3 and p["hz"] == 40
      and p["arranque"] == "I1", f"plan: 3 en S1, pausa 3 s, avanza 5 s, 40 Hz, I1")
r = rc.armar(p)
check(r["eventos"] == [{"cuando": "S1", "duracion_s": 3.0, "acciones": {"banda": "pausar"}}],
      "la pausa de cada deteccion es un evento del motor")
check([x.get("segundos") for x in r["secuencias"][0]["pasos"]] == [3.0, 5.0, 0.5],
      "SQ1: pausa de la caja 3, avanza 5 s, paro")
cfg = lm.cfg_desde_respuesta(r)
check(app.validar_logica_banda(cfg) == [] and rc.verificar(cfg, p) == [], "valida y cumple 2 ciclos completos")
reqs = [x for x in co.requisitos([T]) if x["tipo"] != "conteo"]
check(co.verificar(cfg, reqs) == [], "y los requisitos de la sesion (pausa, paro por conteo, nuevo ciclo)")

s = ls.SimV7()
s.cargar(cfg)
s.correr(0.5)
s.pulsar("I1"); s.correr(2)
corre = lambda: s.motor() in ("dir1", "dir2")  # noqa: E731
ok = corre()
for k in range(2):
    s.pulsar("I4"); s.correr(1.2); ok = ok and not corre()
    s.correr(3.0); ok = ok and corre()
s.pulsar("I4"); s.correr(1.2); ok = ok and not corre()
s.correr(3.5); ok = ok and corre()
s.correr(5.5); ok = ok and not corre()
s.correr(8); ok = ok and not corre()
s.pulsar("I1"); s.correr(2); ok = ok and corre()
check(ok, "traza: I1 avanza; cada S1 pausa ~3 s; la 3a: pausa, 5 s y para; espera I1 y vuelve a empezar")

check(rc.detectar(["Con I1 avanza a 30 Hz. Cada que el sensor 2 detecte se espere 3 segundos antes de avanzar. "
                   "Cuando el sensor 1 cuente 3 cajas la banda avanza 5 segundos y se detiene sola."], {}) is None,
      "pausa en OTRO sensor que el que cuenta: no aplica la receta")
check(rc.detectar(["Con I1 avanza a 30 Hz. Al contar 3 cajas en S1 la banda se detiene. La banda hace una pausa de 2 s."],
                  {}) is None, "una pausa que no es por deteccion: no aplica")
T3 = ("Al presionar I1 la banda avanza a 30 Hz. Cada vez que el sensor 2 detecta una caja se cuenta. Cuando el "
      "contador llegue a 3, la banda avanza 5 segundos más y luego se detiene sola, y el contador se reinicia.")
check(rc.detectar([T3], {})["pausa"] == 0, "sin pausa: la receta de siempre (C3)")

# ---------------------------------------------------------------------------
escenario("2. Errores de escritura antes del modelo/extractor")
check(ct.corregir("se detenga por sis ola") == "se detenga por sí sola", "'por sis ola' -> 'por sí sola'")
check(ct.corregir("que el susaurio presione el botn i1") == "que el usuario presione el botón i1",
      "'susaurio' -> 'usuario', 'botn' -> 'botón'")
check(ct.corregir("La banda Avanza con I1") == "La banda Avanza con I1", "lo demas queda tal cual")

# ---------------------------------------------------------------------------
escenario("3. De punta a punta: sin llamar al modelo")
llamadas = []


def _llm(msgs):
    llamadas.append(msgs)
    raise AssertionError("no se debia llamar al modelo")


app.llamar_modelo_json = _llm
app.llamar_extractor_json = _llm
r = asyncio.run(app.generar_logica(app.LogicaRequest(texto=T, device="banda")))
check(r.status == "ok" and r.analysis.get("fuente") == "receta" and not llamadas,
      f"/generar-logica: receta, 0 llamadas ({r.status}, {r.analysis.get('fuente')})")
eng = r.logic["band"]["engine"]
pausa_t = next((t for t in eng.get("timers") or [] if t["id"] == eng["motor"].get("pausa")), None)
check(eng["motor"]["arranque"] == "I1" and r.logic["band"]["freq_hz"] == 40
      and pausa_t and pausa_t["entrada"] == "S1" and pausa_t["segundos"] == 3.0,
      "con I1 a 40 Hz y la pausa de 3 s por S1 (motor.pausa = timer de S1)")

print(f"\nTODAS LAS PRUEBAS DE LA RONDA 6 PASARON ({OK} comprobaciones).")
