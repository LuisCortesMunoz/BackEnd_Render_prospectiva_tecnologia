"""
test_huella_reparaciones.py — lo que el banco de pruebas con Groq real (5 nov)
encontro que fallaba al pedir cambios, y su arreglo:

  1. huella_banda: comparar lo que HACE la version vigente y la nueva (misma
     bateria de escenarios en el ST real): cambios fuera de lo pedido y
     cambios sin efecto vuelven al modelo; lo que persiste se avisa.
  2. comportamiento.requisitos: la instruccion mas reciente manda ("cambia el
     conteo a 5", "ya no se detiene"); arranque por defecto con I1; tras un
     paro automatico I1 debe volver a arrancar.
  3. reparaciones_banda: contador trabado con SQk_FIN, banda que no arranca y
     banda que vuelve a avanzar sola al terminar la secuencia.

Corre sin Groq (modelo simulado) y sin PLC (lavo_sim):  python test_huella_reparaciones.py
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_hue_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_hue.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import comportamiento as co
import huella_banda as hb
import lavo_motor as lm
import lavo_sim as ls
import reparaciones_banda as rb

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_hue.json")
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


A = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [], "simplificado": [],
     "no_soportado": [], "requiere_confirmacion": False}

# Caso real del usuario (banco C11): lo que devolvio el modelo.
T11 = ("Necesito que revises y corrijas la lógica de la banda transportadora para que avance hacia la derecha y, "
       "cuando el Sensor 2 acumule 3 detecciones, la banda continúe avanzando durante 6 segundos y después se "
       "detenga automáticamente. Una vez detenida, la pluma 2 debe descender y la pluma 1 debe elevarse, respetando "
       "ese orden. Finalmente, el contador debe reiniciarse automáticamente a cero y permanecer en ese estado hasta "
       "que comience una nueva secuencia. Revisa especialmente la lógica de reinicio del contador, ya que actualmente "
       "presenta fallas frecuentes y no siempre se restablece correctamente.")
R11 = {"name": "C11", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 20}, "motor": {},
       "contadores": [{"id": "CT1", "entrada": "I5", "reset": "SQ1_FIN", "preset": 3, "modo": "una_vez"}],
       "secuencias": [{"id": "SQ1", "inicio": "CT1", "modo": "una_vez", "exclusiva": True, "pasos": [
           {"motor": ["marcha"], "avanza": "tiempo", "segundos": 6},
           {"salidas": ["Q7"], "avanza": "tiempo", "segundos": 2},
           {"salidas": ["Q8"], "avanza": "tiempo", "segundos": 2}]}],
       "analisis": A, "preguntas": []}
# Banco C6: invierte con S2, avanza 5 s a la izquierda y se detiene; I2 detiene todo.
T6 = ("I1 arranca la banda hacia la derecha a 25 Hz. Cuando el sensor 2 detecta una caja, la banda invierte el "
      "sentido y avanza hacia la izquierda durante 5 segundos y luego se detiene. I2 detiene todo.")
R6 = {"name": "C6", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 25},
      "motor": {"arranque": "I1", "paro": "I2", "modo": "enclavado"},
      "secuencias": [{"id": "SQ1", "inicio": "I5", "paro": "I2", "modo": "una_vez", "exclusiva": True, "pasos": [
          {"motor": ["invertir"], "avanza": "tiempo", "segundos": 0, "siguiente": 2},
          {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5, "siguiente": 3},
          {"motor": ["paro"], "avanza": "tiempo", "segundos": 0, "siguiente": "fin"}]}],
      "analisis": A, "preguntas": []}
# Pausa con S2 que SIGUE (banco C7): no se debe "reparar".
T7 = "La banda arranca con I1 a 20 Hz y se detiene con I2. Agrega que cuando el sensor 2 detecte, la banda se detenga 2 segundos y luego siga."
R7 = {"name": "C7", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 20},
      "motor": {"arranque": "I1", "paro": "I2", "modo": "enclavado"},
      "secuencias": [{"id": "SQ1", "inicio": "S2", "modo": "una_vez", "exclusiva": True, "pasos": [
          {"motor": ["paro"], "avanza": "tiempo", "segundos": 2}]}], "analisis": A, "preguntas": []}


def sim(resp):
    s = ls.SimV7()
    s.cargar(lm.cfg_desde_respuesta(resp))
    return s


def marcha(s):
    return s.motor() in ("dir1", "dir2")


# ---------------------------------------------------------------------------
escenario("3. Reparaciones deterministas")
r, av = rb.aplicar(R11, T11)
check(r["contadores"][0]["reset"] == "SQ1_CICLO", "contador con reset SQ1_FIN y secuencia que arranca con el: SQ1_CICLO")
check(r["motor"].get("arranque") == "I1", "la banda no tenia arranque (nada la movia antes de contar): I1")
check(r["motor"].get("paro") == "SQ1_CICLO", "termina detenida: paro del motor con SQ1_CICLO")
check(len(av) == 3 and R11["motor"] == {}, "3 avisos y la respuesta original no se toca")

s = sim(r)
s.pulsar("I1"); s.correr(2)
ok = marcha(s)
for ciclo in range(2):
    if ciclo:
        s.pulsar("I1"); s.correr(2)
    for _ in range(2):
        s.pulsar("S2"); s.correr(0.7)
    ok = ok and marcha(s)
    s.pulsar("S2"); s.correr(4.5)
    ok = ok and marcha(s)
    s.correr(2.5)
    ok = ok and not marcha(s)
    s.correr(6)
    ok = ok and not marcha(s)
check(ok, "caso real reparado: arranca con I1, 3 cajas, sigue 6 s, para, y el 2o ciclo funciona igual (contador en 0)")

r, av = rb.aplicar(R6, T6)
check(r["motor"]["paro"].startswith("C") and any(c["entradas"] == ["I2", "SQ1_CICLO"] for c in r["condiciones"]),
      "C6: paro = I2 OR SQ1_CICLO (condicion nueva)")
s = sim(r)
s.pulsar("I1"); s.correr(2.5); s.pulsar("S2"); s.correr(9)
check(not marcha(s), "C6 reparado: tras la reversa de 5 s se queda detenida (antes seguia en reversa sin fin)")

check(rb.aplicar(R7, T7)[1] == [], "pausa 'se detenga 2 s y luego siga': no se repara (debe seguir)")
T_sensor = "Cuando el sensor 1 detecte una caja, la banda avanza 5 segundos y se detiene."
R_sensor = {"banda": {"mover": True}, "motor": {}, "secuencias": [{"id": "SQ1", "inicio": "S1", "pasos": [
    {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5}, {"motor": ["paro"], "avanza": "tiempo", "segundos": 0}]}]}
check(rb.aplicar(R_sensor, T_sensor)[1] == [], "arranque por sensor: no se agrega I1 ni paro")
check(rb.aplicar(R11, T11 + " La banda funciona siempre en marcha.")[0]["motor"].get("arranque") is None,
      "'siempre en marcha' cuenta como arranque dicho (no se agrega I1)")
check(rb.aplicar(R11, T11)[0]["motor"].get("arranque") == "I1",
      "'no siempre se restablece' no es 'siempre en marcha'")

# ---------------------------------------------------------------------------
escenario("2. Requisitos: lo mas reciente manda")
T3 = ("Al presionar I1 la banda avanza a 30 Hz. Cada vez que el sensor 2 detecta una caja se cuenta. Cuando el "
      "contador llegue a 3, la banda avanza 5 segundos más y luego se detiene sola, y el contador se reinicia.")
r3 = co.requisitos([T3, "Cambia el conteo a 5 cajas en lugar de 3."])
check(all(x.get("conteo") == 5 for x in r3 if x.get("conteo")) and any(x["tipo"] == "para_sola" for x in r3),
      f"'cambia el conteo a 5': el paro por conteo pasa a 5 ({[(x['tipo'], x.get('conteo')) for x in r3]})")
check(co.cambio_conteo("En vez de 3 cajas que sean 4") == 4 and co.cambio_conteo("cambia el tiempo a 5 segundos") is None,
      "'en vez de 3 que sean 4' -> 4; un tiempo no es un conteo")
T10 = ("I1 arranca la banda a 30 Hz. El sensor 2 cuenta cajas y al llegar a 4 se enciende la luz roja y la banda se "
       "detiene. Con I2 se reinicia el contador.")
r10 = co.requisitos([T10, "Cambia: al llegar a 4 la banda ya no se detiene, solo se enciende la luz roja."])
check(not any(x["tipo"] == "para_sola" for x in r10) and any(x["tipo"] == "sigue_al_contar" and x["conteo"] == 4
                                                              for x in r10),
      f"'ya no se detiene': sin paro automatico y se exige que siga al contar 4 ({[x['tipo'] for x in r10]})")
r_q = co.requisitos([T3, "No se detiene sola, revisa."])
check(any(x["tipo"] == "para_sola" for x in r_q), "'no se detiene sola, revisa' (queja) conserva el paro")
check(any(x["tipo"] == "arranca_con" and x.get("defecto") for x in co.requisitos([T11])),
      "sin arranque dicho: se exige I1 (por defecto)")
check(not any(x.get("defecto") for x in co.requisitos([T_sensor])), "arranque por sensor: sin I1 por defecto")

# sigue_al_contar en la simulacion
SIGUE = {"name": "s", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
         "motor": {"arranque": "I1", "paro": "I2"},
         "contadores": [{"id": "CT1", "entrada": "S2", "preset": 4, "modo": "una_vez", "reset": "I2"}],
         "eventos": [{"cuando": "CT1", "acciones": {"roja": "encender"}}], "analisis": A, "preguntas": []}
PARA = copy.deepcopy(SIGUE)
PARA["condiciones"] = [{"id": "C1", "op": "OR", "entradas": ["CT1", "I2"], "flanco": "nivel"}]
PARA["motor"]["paro"] = "C1"
req = [x for x in r10 if x["tipo"] == "sigue_al_contar"]
check(co.verificar(lm.cfg_desde_respuesta(SIGUE), req) == [], "sigue al contar 4: cumple")
f = co.verificar(lm.cfg_desde_respuesta(PARA), req)
check(f and "ya NO se detenga" in f[0], f"si se sigue deteniendo (banco C10), se detecta ({f[:1]})")

# rearranque tras paro por conteo (banco C3: paro = I2 OR I3 OR T1, T1 RETARDO de CT1)
C3MAL = {"name": "c3", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
         "motor": {"arranque": "I1", "paro": "C1", "modo": "enclavado"},
         "condiciones": [{"id": "C1", "op": "OR", "entradas": ["I2", "I3", "T1"], "flanco": "nivel"}],
         "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "CT1", "reset": "I1", "segundos": 5}],
         "contadores": [{"id": "CT1", "entrada": "I5", "reset": "T1", "preset": 3, "modo": "una_vez"}],
         "analisis": A, "preguntas": []}
f = co.verificar(lm.cfg_desde_respuesta(C3MAL), co.requisitos([T3]))
check(bool(f), f"banco C3: la logica del banco no pasa ({f[:1]})")
solo_paro = [x for x in co.requisitos([T3]) if x["tipo"] in ("para_sola", "arranca_con")]
f = co.verificar(lm.cfg_desde_respuesta(C3MAL), solo_paro)
check(f and "ya no vuelve a arrancar" in f[0], f"banco C3: tras el paro por conteo I1 ya no arranca: se detecta ({f[:1]})")
C3BIEN = copy.deepcopy(C3MAL)
C3BIEN["timers"][0]["tipo"] = "PULSO"
C3BIEN["timers"][0]["entrada"] = "CT1"
C3BIEN["timers"] = [{"id": "T1", "tipo": "RETARDO", "entrada": "CT1", "reset": "CT1", "segundos": 5}]
C3BIEN["condiciones"][0]["entradas"] = ["I2", "I3", "T2"]
C3BIEN["timers"] = [{"id": "T1", "tipo": "RETARDO", "entrada": "CT1", "segundos": 5},
                    {"id": "T2", "tipo": "PULSO", "entrada": "T1", "segundos": 0.5}]
C3BIEN["contadores"][0]["reset"] = "T2"
f = co.verificar(lm.cfg_desde_respuesta(C3BIEN), solo_paro)
check(f == [], f"con un paro que se libera (pulso), I1 la vuelve a arrancar ({f[:1]})")

# ---------------------------------------------------------------------------
escenario("1. Huella: comparar lo que hace la version vigente y la nueva")
T5 = ("Secuencia 1: al presionar I1, la banda avanza 10 segundos, se detiene 3 segundos y vuelve a avanzar 5 "
      "segundos y se detiene. Secuencia 2: cuando el sensor 2 detecta una caja, la luz roja se enciende 4 segundos.")


def prog5(t1, luz):
    return {"name": "c5", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
            "secuencias": [
                {"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
                    {"motor": ["marcha"], "avanza": "tiempo", "segundos": t1},
                    {"motor": [], "avanza": "tiempo", "segundos": 3},
                    {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5},
                    {"motor": [], "avanza": "tiempo", "segundos": 0.5, "siguiente": "fin"}]},
                {"id": "SQ2", "inicio": "S2", "modo": "una_vez", "exclusiva": True, "pasos": [
                    {"salidas": [luz], "avanza": "tiempo", "segundos": 4, "siguiente": "fin"}]}],
            "analisis": A, "preguntas": []}


def cfg(resp):
    c = lm.cfg_desde_respuesta(resp)
    c["respuesta_llm"] = resp
    return c


V6R, V6A, V10A = cfg(prog5(6, "Q5")), cfg(prog5(6, "Q4")), cfg(prog5(10, "Q4"))
h6r = hb.huella(V6R)
check(hb.comparar(h6r, hb.huella(cfg(prog5(6, "Q5")))) == [], "el mismo programa: sin diferencias")
zona_luz = {"modulos": ["torreta"], "etapas": [], "vago": False}
d_ok = hb.comparar(h6r, hb.huella(V6A))
check(d_ok and hb.fuera_de_zona(d_ok, zona_luz) == [], "roja -> amarilla: cambia solo la torreta (lo pedido)")
d_mal = hb.comparar(h6r, hb.huella(V10A))
fuera = hb.fuera_de_zona(d_mal, zona_luz)
check(fuera and all(x["modulo"] == "motor" for x in fuera),
      f"banco C5: al cambiar el color la secuencia 1 volvio a 10 s: se detecta en la banda ({len(fuera)})")
check("I1" in hb.mensaje(fuera) and "EXACTAMENTE" in hb.mensaje(fuera), "mensaje al modelo con el escenario")
check(hb.fuera_de_zona(d_mal, {"modulos": ["tiempos", "motor"], "etapas": [], "vago": False}) == [],
      "si el usuario habla de tiempos no se juzga (cambia el momento de todo)")
h45 = hb.huella(cfg({**prog5(6, "Q5"), "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 45}}))
check(any(x["canal"] == "banda" and "45 Hz" in x["despues"] for x in hb.comparar(h6r, h45)),
      "un cambio de velocidad se ve en la huella (no es 'sin efecto')")

# ---------------------------------------------------------------------------
escenario("1. Huella en la generacion (modelo simulado)")
respuestas, llamadas = [], []


def _llm(msgs):
    llamadas.append(copy.deepcopy(msgs))
    if not respuestas:
        raise AssertionError("el modelo fue llamado sin respuesta preparada")
    return copy.deepcopy(respuestas.pop(0))


app.llamar_modelo_json = _llm


def generar(texto, *resp, prev, historial):
    respuestas[:] = list(resp)
    llamadas.clear()
    c = cfg(prev)
    ctx = app.ContextoLadder(programa_anterior={"metadata": {"name": prev.get("name"), "engine_config": c}},
                             historial=historial)
    return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", contexto=ctx)))


TH = ("Con I1 la banda arranca a 30 Hz hacia la derecha y con I2 se detiene. Cuando el sensor 2 detecta una caja, "
      "la luz roja se enciende durante 4 segundos.")


def progh(hz, luz):
    return {"name": "h", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": hz},
            "motor": {"arranque": "I1", "paro": "I2", "modo": "enclavado"},
            "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "S2", "segundos": 4}],
            "salidas": {luz: "T1"}, "analisis": A, "preguntas": []}


PEDIDO = "La luz ahora debe ser la amarilla en lugar de la roja."
# Este cambio de color lo resuelve la edicion directa (ediciones.py, sin el modelo); aqui se
# apaga para probar la huella en el camino del modelo.
r = generar(PEDIDO, prev=progh(30, "Q5"), historial=[TH])
check(not llamadas and r.analysis.get("fuente") == "edicion" and "Q4" in r.logic["band"]["engine"]["salidas"],
      "el cambio de color sale por edicion directa, sin el modelo")
_ed = app.ediciones.aplicar
app.ediciones.aplicar = lambda *a, **k: None
r = generar(PEDIDO, progh(45, "Q4"), progh(30, "Q4"), prev=progh(30, "Q5"), historial=[TH])
check(len(llamadas) == 2 and "CAMBIO cosas que el usuario no pidio" in llamadas[1][-1]["content"]
      and "Hz" in llamadas[1][-1]["content"],
      "la version que ademas cambia la velocidad (motor) vuelve al modelo con lo que cambio")
check(r.status == "ok" and r.logic["band"]["freq_hz"] == 30 and "Q4" in r.logic["band"]["engine"]["salidas"],
      "se entrega la que solo cambia el color (sigue a 30 Hz)")
r = generar(PEDIDO, progh(30, "Q4"), prev=progh(30, "Q5"), historial=[TH])
check(len(llamadas) == 1 and r.status == "ok", "lo pedido (solo la torreta cambia): se acepta a la primera")
r = generar(PEDIDO, progh(45, "Q4"), progh(45, "Q4"), progh(45, "Q4"), prev=progh(30, "Q5"), historial=[TH])
check(r.status == "ok" and any("Además de lo pedido cambió" in w for w in r.warnings),
      f"si insiste, se entrega con un aviso de lo que cambio sin pedirlo ({len(llamadas)} llamadas)")
app.ediciones.aplicar = _ed

print(f"\nTODAS LAS PRUEBAS DE HUELLA Y REPARACIONES PASARON ({OK} comprobaciones).")
