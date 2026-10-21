# test_lavo_v7.py — pruebas OFFLINE del programa maestro v7 (motor LAVO general).
#
# Que se prueba y COMO (ninguna prueba toca un PLC fisico):
#   - Interpretacion: /generar-logica con el LLM SIMULADO (respuestas fijas que
#     imitan al modelo). Prueba el flujo del backend (validacion, auto-revision,
#     compatibilidad, confirmacion), NO la calidad del modelo real (Groq).
#   - JSON / esquema / registros: lavo_motor (validador y compilador).
#   - Correspondencia con el ST: se EJECUTA el texto real de
#     cscape/ST_banda_v7_lavo.txt con banda_st_interprete (simulacion por scans).
#   - Carga: plc_banda.aplicar_config contra un Modbus SIMULADO conectado a los
#     registros del ST (BEGIN -> espera -> COMMIT -> confirmacion).
#   - Regresion: con el motor v7 sin config, el ST v7 == ST v5 scan a scan.
#
# Uso:  python test_lavo_v7.py

import asyncio
import os
import random
import tempfile

os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_lavo_v7.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import banda_st_interprete as si
import lavo_motor as lm
import lavo_sim as ls
import plc_banda as pb

OK = 0
TABLA = []          # (escenario, comprobaciones, resultado)


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1


def escenario(nombre):
    TABLA.append([nombre, OK, None])
    print(f"\n== {nombre}")


def cerrar():
    TABLA[-1][2] = OK - TABLA[-1][1]
    print(f"   OK ({TABLA[-1][2]} comprobaciones)")


def banda(engine, mover=False, freq=30, direccion="derecha"):
    b = {"enable": mover, "engine": {"version": 7, **engine}}
    if mover:
        b.update(direction=direccion, freq_hz=freq)
    return b


def cargar(b, sim=None):
    """Carga el programa con el plan REAL de plc_banda sobre el PLC simulado."""
    sim = sim or ls.SimV7()
    if sim.n == 0:
        sim.correr(0.3)
    plc = pb.BandaPLC(ip="127.0.0.1")
    plc.client = sim.modbus
    plc._esperar = sim.correr
    pb.aplicar_config(plc, {"device": "banda", "band": b, "outputs": []})
    sim.correr(0.2)
    return sim


def tiempo_hasta(sim, cond, maximo=30.0):
    """Segundos simulados hasta que cond(sim) sea verdadera (o None)."""
    t = 0.0
    while t <= maximo:
        if cond(sim):
            return round(t, 2)
        sim.scan()
        t += ls.SCAN_S
    return None


# ---------------------------------------------------------------------------
escenario("0. Contrato, determinismo y equivalencia de validacion ST <-> Python")
# ---------------------------------------------------------------------------
eng = {"motor": {"arranque": "I1", "paro": "I2"},
       "salidas": {"verde": "BANDA_CORRIENDO", "Q5": "BANDA_DETENIDA"},
       "contadores": [{"id": "CT2", "entrada": "S1", "preset": 3}, {"id": "CT1", "entrada": "S2", "preset": 2}]}
eng_alias = {"motor": {"paro": "boton rojo", "arranque": "11"},
             "salidas": {"Q5": "detenida", "q3": "corriendo"},
             "contadores": [{"id": "ct1", "entrada": "sensor2", "preset": "2"}, {"id": "CT2", "entrada": "I4", "preset": 3}]}
a, b2 = lm.compilar_motor(banda(eng, True)), lm.compilar_motor(banda(eng_alias, True))
check(a == b2, "mismo significado (otro orden, alias, voz '11') -> registros identicos")
check(len(a) == lm.CFG_LEN and a[lm.H_FORMAT] == 7 and a[lm.H_LEN] == 640, "cabecera del bloque")
check(lm.error_plc(a) == (0, 0), "el bloque compilado pasa la validacion del PLC (Python)")
check(a[lm.H_CHK] == lm.checksum(a) and 1 <= a[lm.H_ID] <= 32767, "checksum e id deterministas")
check(lm.codigo_senal("NOT S1") == 1004 and lm.codigo_senal("SQ2 paso 3") == lm.SENALES["SQ2_PASO3"],
      "codigos de senal (NOT +1000, pasos)")
_err = lm.validar_motor(banda({"secuencias": [{"id": "SQ1", "inicio": "I1", "pasos": [
    {"motor": ["avanzar", None, ""], "segundos": 1}, {"motor": ["volar"], "segundos": 1}]}]}, True))[0]
check(_err == ["SQ1 paso 2: accion de motor 'volar' no existe (usa marcha, paro, dir_1, dir_2, invertir)."],
      "una accion de motor desconocida se rechaza (alias y vacios no)")

# Equivalencia: el ST (interpretado) reporta EXACTAMENTE el error de error_plc().
random.seed(7)
base = lm.compilar_motor(banda({
    "motor": {"arranque": "I1", "paro": "I2"},
    "timers": [{"id": "T1", "tipo": "INTERMITENTE", "entrada": "SIEMPRE", "segundos": 0.5}],
    "contadores": [{"id": "CT1", "entrada": "S1", "reset": "I2", "preset": 2}],
    "salidas": {"Q4": "T1", "Q5": "CT1"},
    "secuencias": [{"id": "SQ1", "inicio": "S2", "modo": "repetir", "repeticiones": 2, "pasos": [
        {"salidas": ["Q3"], "motor": ["marcha"], "segundos": 2},
        {"salidas": ["Q8"], "avanza": "flanco", "senal": "S1", "alternativa": {"senal": "I2", "ir_a": "fin"}}]}]},
    True))
casos = []
for ofs, val in [(0, 6), (2, 600), (1, 0), (lm.H_FREQ, 400), (lm.M_RUN, 3), (lm.M_RUN, 1003), (lm.M_RUN, 150),
                 (lm.O_OUT, 26), (lm.O_OUT + 9, 26), (lm.H_ESTOP_LAMPS, 1 << 7), (lm.O_TIMER, 7),
                 (lm.O_TIMER + lm.TIMER_W + 0, 1), (lm.O_COUNT + 3, 0), (lm.O_SEQ + lm.SQ_START, 3),
                 (lm.O_SEQ + lm.SQ_REPEAT, 0), (lm.O_SEQ + lm.SEQ_HDR + lm.ST_OUT, (1 << 7) | (1 << 8)),
                 (lm.O_SEQ + lm.SEQ_HDR + lm.ST_NEXT, 9), (lm.O_SEQ + lm.SEQ_HDR + lm.ST_OUT, 1 << 10),
                 (lm.H_MOVE, 0), (12, 5), (639, 1), (lm.O_COND + 5, 3), (lm.O_SEQ + lm.SQ_NSTEPS, 0)]:
    c = list(base)
    c[ofs] = val
    if ofs != lm.H_CHK:
        c[lm.H_CHK] = lm.checksum(c)
    casos.append(c)
for _ in range(12):
    c = list(base)
    for _k in range(random.randint(1, 3)):
        c[random.randrange(4, lm.CFG_LEN)] = random.choice([0, 1, 5, 17, 100, 1001, 4095, 32767, -1])
    c[lm.H_CHK] = lm.checksum(c)
    casos.append(c)
c = list(base)
c[lm.H_CHK] = (c[lm.H_CHK] + 1) % 32749
casos.append(c)                                   # checksum malo
sim = ls.SimV7()
sim.correr(0.2)
plc = pb.BandaPLC(ip="127.0.0.1")
plc.client = sim.modbus
plc._esperar = sim.correr
iguales = 0
for c in casos:
    esperado = lm.error_plc(c, staged_id=c[lm.H_ID])
    plc.comando_v7(lm.CMD_BEGIN)
    plc.write_band_block(pb.R(lm.STAGE_BASE), [v & 0xFFFF for v in c])
    plc.comando_v7(lm.CMD_COMMIT, c[lm.H_ID])
    sim.correr(0.1)
    st = sim.V["LSTA"]
    obtenido = (st[lm.S_ERR], st[lm.S_ERRFIELD]) if st[lm.S_TX] == lm.TX_REJ else (0, 0)
    assert obtenido == esperado, (f"ST {obtenido} != Python {esperado}: "
                                  f"{lm.explicar_error(*obtenido)} / {lm.explicar_error(*esperado)}")
    iguales += 1
check(iguales == len(casos), f"{iguales} bloques (validos e invalidos): el ST rechaza con el mismo codigo y campo")
invalidos = sum(1 for c in casos if lm.error_plc(c)[0])
check(invalidos >= 15, f"la muestra incluye {invalidos} bloques que el PLC debe rechazar")
cerrar()


# ---------------------------------------------------------------------------
escenario("1. I1 como arranque y despues como paro")
# ---------------------------------------------------------------------------
sim = cargar(banda({"motor": {"arranque": "I1", "paro": "I2"}}, True))
check(sim.estado()["config_activa"] and sim.estado()["cfg_lista"], "config aplicada y VFD listo")
sim.pulsar("I1")
sim.correr(1)
check(sim.motor() == "dir1" and sim.vfd_hz > 15, "1a: I1 arranca")
sim.pulsar("I2")
check(sim.motor() == "paro", "1a: I2 detiene")
# misma entrada, nueva configuracion: ahora I1 es el PARO y S1 el arranque
cargar(banda({"motor": {"arranque": "S1", "paro": "I1"}}, True), sim)
sim.pulsar("I1")
sim.correr(0.5)
check(sim.motor() == "paro", "1b: con la config nueva I1 ya no arranca")
sim.pulsar("S1")
sim.correr(0.5)
check(sim.motor() == "dir1", "1b: S1 arranca")
sim.pulsar("I1")
check(sim.motor() == "paro", "1b: I1 ahora detiene")
# un solo boton: I1 arranca y la siguiente pulsacion para (biestable con memoria)
toggle = banda({"condiciones": [{"id": "C1", "op": "AND", "entradas": ["I1"], "flanco": "subida"},
                                {"id": "C2", "op": "AND", "entradas": ["C1", "NOT M1"]},
                                {"id": "C3", "op": "AND", "entradas": ["C1", "M1"]}],
                "memorias": [{"id": "M1", "set": "C2", "reset": "C3"}],
                "motor": {"arranque": "M1", "modo": "mientras"}}, True)
cargar(toggle, sim)
sim.pulsar("I1")
sim.correr(0.5)
check(sim.motor() == "dir1", "1c: primera pulsacion de I1 arranca")
sim.pulsar("I1")
sim.correr(0.5)
check(sim.motor() == "paro", "1c: segunda pulsacion de I1 detiene")
sim.pulsar("I1")
sim.correr(0.5)
sim.poner(I3=True)
sim.correr(0.3)
sim.poner(I3=False)
sim.correr(1)
check(sim.motor() == "paro" and not sim.var("MBit"), "1c: I3 borra el enclavamiento y nada arranca solo")
cerrar()


# ---------------------------------------------------------------------------
escenario("2. I2 como reset de contador")
# ---------------------------------------------------------------------------
sim = cargar(banda({"contadores": [{"id": "CT1", "entrada": "S1", "reset": "I2", "preset": 3}],
                    "salidas": {"Q5": "CT1"}}))
for _ in range(2):
    sim.pulsar("S1")
    sim.correr(0.3)
check(not sim.q("Q5") and sim.estado()["contadores_acc"][0] == 2, "2 cajas: aun no llega")
sim.pulsar("S1")
sim.correr(0.3)
check(sim.q("Q5") and sim.estado()["contadores_acc"][0] == 3, "3a caja: roja encendida")
sim.pulsar("I2")
sim.correr(0.2)
check(not sim.q("Q5") and sim.estado()["contadores_acc"][0] == 0, "I2 reinicia la cuenta")
cerrar()


# ---------------------------------------------------------------------------
escenario("3. Q10 -> Q11 -> Q12 con intervalos configurables")
# ---------------------------------------------------------------------------
import app
app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_lavo_v7.json")
app.BANDA_MOTOR_V7 = True
respuestas = []
capturado = {}


def _llm(msgs):
    capturado["msgs"] = msgs
    return respuestas.pop(0)


app.llamar_modelo_json = _llm


def generar(texto, contexto=None):
    req = app.LogicaRequest(texto=texto, device="banda", contexto=contexto)
    return asyncio.run(app.generar_logica(req))


texto3 = "Enciende Q10 2 s, luego Q11 3 s y luego Q12 4 s cuando presione I1"
# (a) el modelo propone la alternativa y la declara
respuestas[:] = [{"name": "Q10-Q11-Q12 en torreta", "banda": {"mover": False},
                  "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
                      {"salidas": ["Q3"], "segundos": 2}, {"salidas": ["Q4"], "segundos": 3},
                      {"salidas": ["Q5"], "segundos": 4}]}],
                  "analisis": {"intencion": "tres luces en orden", "compatibilidad": "parcial",
                               "conservado": ["orden", "2/3/4 s", "inicio I1"],
                               "modificado": ["Q10, Q11 y Q12 no existen: se usan Q3, Q4 y Q5"],
                               "requiere_confirmacion": True}}]
r = generar(texto3)
check(r.status == "needs_confirmation" and r.questions[0]["id"] == "confirmar_alternativa",
      "pide confirmacion antes de usar la alternativa")
check(r.logic["requiere_confirmacion"] and r.logic["confirmado"] is False, "la alternativa viaja marcada")
check(any("Q10" in x for x in r.analysis["compatibilidad"]["modificado"]), "explica que se modifico")
check(r.analysis["registros"]["campos_activos"] > 0, "registros calculados para revision")
# (b) el modelo cambia las salidas SIN decirlo: el backend lo detecta
respuestas[:] = [{"name": "x", "banda": {"mover": False},
                  "secuencias": [{"id": "SQ1", "inicio": "I1", "pasos": [
                      {"salidas": ["Q3"], "segundos": 2}, {"salidas": ["Q4"], "segundos": 3},
                      {"salidas": ["Q5"], "segundos": 4}]}]}]
r2 = generar(texto3)
check(r2.status == "needs_confirmation"
      and any("Q10" in x for x in r2.analysis["compatibilidad"]["modificado"]),
      "deteccion determinista: Q10..Q12 nombradas pero no usadas -> confirmacion")
# (c) /aplicar-plc rechaza la alternativa sin confirmar
try:
    app.aplicar_plc(app.AplicarPLCRequest(logic=r.logic, device="banda", ip="127.0.0.1"))
    check(False, "no debe cargar una alternativa sin confirmar")
except app.HTTPException as e:
    check(e.status_code == 409, "409: falta la confirmacion humana")
check(r.logic["band"]["engine"]["confirmado"] is False, "la marca viaja tambien dentro de band.engine")
try:
    app.banda_config(app.BandaConfigRequest(band=r.logic["band"], ip="127.0.0.1"))
    check(False, "el panel de la banda tampoco debe cargarla sin confirmar")
except app.HTTPException as e:
    check(e.status_code == 409, "409 tambien por /banda/config (el panel solo manda 'band')")
# (d) comportamiento real en el ST (con la alternativa aceptada)
sim = cargar(r.logic["band"])
sim.pulsar("I1", 0.1)
t1 = tiempo_hasta(sim, lambda s: s.q("Q4"))
t2 = tiempo_hasta(sim, lambda s: s.q("Q5"))
t3 = tiempo_hasta(sim, lambda s: not s.q("Q5"))
check(t1 is not None and 1.7 <= t1 + 0.1 <= 2.15, f"Q3 (en lugar de Q10) dura ~2 s ({t1})")
check(t2 is not None and 2.85 <= t2 <= 3.15 and not sim.q("Q3"), f"Q4 dura 3 s y Q3 ya apago ({t2})")
check(t3 is not None and 3.85 <= t3 <= 4.15, f"Q5 dura 4 s ({t3})")
check(sim.estado()["secuencias"][0]["estado"] == "terminada", "una sola vez: termina")
cerrar()


# ---------------------------------------------------------------------------
escenario("4. Secuencia de torreta ciclica (hasta orden de paro)")
# ---------------------------------------------------------------------------
sim = cargar(banda({"secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I2", "modo": "infinita", "pasos": [
    {"salidas": ["verde"], "segundos": 1}, {"salidas": ["amarilla"], "segundos": 1},
    {"salidas": ["roja", "verde"], "segundos": 1}]}]}))
sim.pulsar("I1", 0.1)
vistos = []
for _ in range(80):
    sim.correr(0.1)
    o = tuple(sorted(sim.salidas()))
    if not vistos or vistos[-1] != o:
        vistos.append(o)
check(vistos[:4] == [("Q3",), ("Q4",), ("Q3", "Q5"), ("Q3",)], f"orden V -> A -> R+V -> V ({vistos[:4]})")
check(sim.estado()["secuencias"][0]["ciclos"] >= 2, "cuenta vueltas completas")
sim.pulsar("I2")
check(sim.salidas() == set() and sim.estado()["secuencias"][0]["estado"] == "inactiva", "I2 la detiene")
sim.correr(2)
check(sim.salidas() == set(), "no vuelve a arrancar sola")
cerrar()


# ---------------------------------------------------------------------------
escenario("5. Banda: arranque -> paro -> arranque")
# ---------------------------------------------------------------------------
sim = cargar(banda({"motor": {"arranque": "I1", "paro": "I2"},
                    "salidas": {"Q3": "BANDA_CORRIENDO", "Q5": "BANDA_DETENIDA"}}, True, freq=25))
historia = []
for paso in ("I1", "I2", "I1"):
    sim.pulsar(paso)
    sim.correr(1.5)
    historia.append((sim.motor(), tuple(sorted(sim.salidas()))))
check(historia == [("dir1", ("Q3",)), ("paro", ("Q5",)), ("dir1", ("Q3",))], f"secuencia de estados {historia}")
check(abs(sim.vfd_hz - 25) < 0.5, "consigna 25 Hz (x100 en %R504)")
cerrar()


# ---------------------------------------------------------------------------
escenario("6. Banda con ciclos repetitivos (5 s marcha / 3 s paro, 3 veces)")
# ---------------------------------------------------------------------------
sim = cargar(banda({"secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "repetir", "repeticiones": 3, "pasos": [
    {"motor": ["marcha"], "segundos": 5}, {"motor": ["paro"], "segundos": 3}]}],
    "salidas": {"Q4": "SQ1_FIN"}}, True))
sim.pulsar("I1", 0.1)
marcha = []
prev = None
for _ in range(300):
    sim.correr(0.1)
    m = sim.motor() == "dir1"
    if m != prev:
        marcha.append((round(sim.n * ls.SCAN_S, 1), m))
        prev = m
cambios_on = [t for t, m in marcha if m]
check(len(cambios_on) == 3, f"3 arranques ({marcha})")
check(sim.estado()["secuencias"][0]["ciclos"] == 3 and sim.q("Q4"), "3 ciclos y SQ1_FIN enciende la amarilla")
check(sim.motor() == "paro", "al terminar la banda queda detenida")
cerrar()


# ---------------------------------------------------------------------------
escenario("7. Contador que activa una salida al llegar a su preset (sube/baja, nivel)")
# ---------------------------------------------------------------------------
sim = cargar(banda({"contadores": [{"id": "CT1", "entrada": "S1", "descuenta": "S2", "preset": 2, "modo": "nivel"}],
                    "salidas": {"Q4": "CT1"}}))
sim.pulsar("S1"); sim.correr(0.2); sim.pulsar("S1"); sim.correr(0.2)
check(sim.q("Q4"), "2 piezas -> Q4")
sim.pulsar("S2"); sim.correr(0.2)
check(not sim.q("Q4") and sim.estado()["contadores_acc"][0] == 1, "S2 descuenta -> Q4 se apaga (modo nivel)")
cerrar()


# ---------------------------------------------------------------------------
escenario("8. Temporizador que desactiva una salida")
# ---------------------------------------------------------------------------
sim = cargar(banda({"memorias": [{"id": "M1", "set": "I1", "reset": "T1"}],
                    "timers": [{"id": "T1", "tipo": "TON", "entrada": "M1", "segundos": 4}],
                    "salidas": {"Q3": "M1"}}))
sim.pulsar("I1", 0.1)
t = tiempo_hasta(sim, lambda s: not s.q("Q3"))
check(t is not None and 3.8 <= t <= 4.2, f"Q3 se apaga sola a los ~4 s ({t})")
cerrar()


# ---------------------------------------------------------------------------
escenario("9. Condiciones AND y OR entre entradas")
# ---------------------------------------------------------------------------
sim = cargar(banda({"condiciones": [{"id": "C1", "op": "AND", "entradas": ["I1", "S1"]},
                                    {"id": "C2", "op": "OR", "entradas": ["I2", "S2"]},
                                    {"id": "C3", "op": "XOR", "entradas": ["I1", "S2"]}],
                    "salidas": {"Q4": "C1", "Q5": "C2", "Q3": "C3"}}))
tabla = {}
for i1 in (0, 1):
    for s1 in (0, 1):
        for i2 in (0, 1):
            for s2 in (0, 1):
                sim.poner(I1=i1, S1=s1, I2=i2, S2=s2)
                sim.correr(0.25)
                tabla[(i1, s1, i2, s2)] = (sim.q("Q4"), sim.q("Q5"), sim.q("Q3"))
check(all(v[0] == (k[0] and k[1]) for k, v in tabla.items()), "Q4 = I1 AND S1 (16 combinaciones)")
check(all(v[1] == (k[2] or k[3]) for k, v in tabla.items()), "Q5 = I2 OR S2")
check(all(v[2] == (k[0] != k[3]) for k, v in tabla.items()), "Q3 = I1 XOR S2")
sim.poner(I1=0, S1=0, I2=0, S2=0)
cerrar()


# ---------------------------------------------------------------------------
escenario("10. Secuencia condicionada por una entrada fisica (ramas y flancos)")
# ---------------------------------------------------------------------------
prog10 = banda({"secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
    {"salidas": ["Q3"], "segundos": 3, "alternativa": {"senal": "S1", "ir_a": 3}},
    {"salidas": ["Q4"], "avanza": "flanco", "senal": "S2"},
    {"salidas": ["Q5"], "segundos": 1}]}]})
sim = cargar(prog10)
sim.pulsar("I1", 0.1)
sim.correr(3.2)
check(sim.salidas() == {"Q4"}, "sin S1: verde 3 s y pasa a la amarilla")
sim.correr(2)
check(sim.salidas() == {"Q4"}, "la amarilla espera el flanco de S2")
sim.pulsar("S2", 0.2)
check(sim.salidas() == {"Q5"}, "S2 la hace avanzar")
sim.pulsar("I1", 0.1)
check(sim.salidas() == {"Q5"}, "un inicio con la secuencia activa no la reinicia")
sim.correr(1.5)
check(sim.estado()["secuencias"][0]["estado"] == "terminada", "termina tras el paso 3")
sim.pulsar("I1", 0.1)
sim.correr(1)
sim.pulsar("S1", 0.2)
check(sim.salidas() == {"Q5"}, "con S1 durante el paso 1 salta directo al paso 3")
cerrar()


# ---------------------------------------------------------------------------
escenario("11. Combinacion de torreta y banda (dos secuencias independientes)")
# ---------------------------------------------------------------------------
sim = cargar(banda({"secuencias": [
    {"id": "SQ1", "inicio": "I1", "paro": "I2", "modo": "infinita", "pasos": [
        {"motor": ["marcha"], "segundos": 4}, {"motor": ["paro"], "segundos": 2}]},
    {"id": "SQ2", "inicio": "I1", "paro": "I2", "modo": "infinita", "pasos": [
        {"salidas": ["Q3"], "segundos": 1}, {"salidas": ["Q4"], "segundos": 1}]}],
    "salidas": {"Q5": "BANDA_DETENIDA"}}, True))
sim.pulsar("I1", 0.1)
muestras = []
for _ in range(70):
    sim.correr(0.1)
    muestras.append((sim.motor(), sim.q("Q3"), sim.q("Q4"), sim.q("Q5")))
check(any(m[0] == "dir1" and m[1] for m in muestras) and any(m[0] == "dir1" and m[2] for m in muestras),
      "la torreta alterna mientras la banda corre")
check(any(m[0] == "paro" and m[3] for m in muestras), "roja con la banda detenida (logica + secuencias)")
check(sim.estado()["secuencias"][1]["ciclos"] >= 2 and sim.estado()["secuencias"][0]["ciclos"] >= 1,
      "las dos secuencias avanzan a su ritmo")
sim.pulsar("I2")
check(sim.motor() == "paro" and not sim.q("Q3") and not sim.q("Q4"), "I2 detiene ambas")
cerrar()


# ---------------------------------------------------------------------------
escenario("12. Solicitud ambigua")
# ---------------------------------------------------------------------------
respuestas[:] = [{"name": "", "banda": {"mover": False},
                  "preguntas": [{"campo": "comportamiento", "pregunta": "¿Qué debe hacer la banda?",
                                 "opciones": ["Avanzar con I1", "Secuencia de torreta"]}]}]
r = generar("haz que funcione")
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "comportamiento" and r.logic == {},
      "pregunta en vez de inventar")
respuestas[:] = [{"name": "x", "banda": {"mover": True, "frecuencia_hz": None}, "motor": {"arranque": "I1"}}]
r = generar("que la banda avance con I1")
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "freq_hz", "sin Hz: pregunta la frecuencia")
cerrar()


# ---------------------------------------------------------------------------
escenario("13. Solicitud parcialmente compatible")
# ---------------------------------------------------------------------------
respuestas[:] = [{"name": "30 Hz con pausa", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
                  "motor": {"arranque": "I1", "paro": "I2", "pausa": "T1"},
                  "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "S1", "segundos": 5}],
                  "analisis": {"intencion": "al detectar S1 bajar a 10 Hz 5 s", "compatibilidad": "parcial",
                               "conservado": ["arranque I1", "paro I2", "5 s al detectar S1"],
                               "simplificado": ["no se puede cambiar la velocidad en marcha: en vez de bajar a 10 Hz "
                                                "la banda se detiene 5 s"],
                               "requiere_confirmacion": True}}]
r = generar("A 30 Hz con I1; cuando S1 detecte baja a 10 Hz durante 5 s y vuelve a 30; I2 para")
check(r.status == "needs_confirmation" and r.analysis["compatibilidad"]["compatibilidad"] == "parcial",
      "alternativa con confirmacion")
check(r.analysis["compatibilidad"]["simplificado"], "explica que se simplifico")
logic = dict(r.logic, confirmado=True)
d = app.aplicar_plc(app.AplicarPLCRequest(logic=logic, device="banda", dry_run=True))
check(d["status"] == "dry-run" and any("cargar_motor_v7" in p for p in d["plan"]), "plan de carga v7 (dry-run)")
sim = cargar(logic["band"])
sim.pulsar("I1")
sim.correr(1)
sim.pulsar("S1", 0.2)
sim.correr(1)
check(sim.motor() == "paro", "la alternativa: S1 pausa la banda")
sim.correr(5)
check(sim.motor() == "dir1", "y reanuda sola a los 5 s")
cerrar()


# ---------------------------------------------------------------------------
escenario("14. Configuracion con registros invalidos")
# ---------------------------------------------------------------------------
malos = [
    (banda({"timers": [{"id": "T1", "tipo": "TON", "segundos": 2}]}), "falta 'entrada'"),
    (banda({"contadores": [{"id": "CT1", "entrada": "S1", "preset": 0}], "salidas": {"Q4": "CT1"}}), "preset 0"),
    (banda({"motor": {"arranque": "I1"}}, True, freq=400), "fuera de [1, 327]"),
    (banda({"timers": [{"id": "T17", "tipo": "TON", "entrada": "I1", "segundos": 1}]}), "invalido"),
    (banda({"salidas": {"Q4": "C3"}}), "no esta definido"),
    (banda({"secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "infinita",
                            "pasos": [{"salidas": ["Q3"], "avanza": "tiempo", "segundos": 0}]}]}), "sin ninguna espera"),
]
for prog, txt in malos:
    errs, _ = lm.validar_motor(prog)
    check(any(txt in e for e in errs), f"validador: {txt} ({errs})")
# un bloque corrupto llega al PLC: lo rechaza y la config anterior sigue corriendo
sim = cargar(banda({"salidas": {"Q4": "SIEMPRE"}}))
activa = sim.estado()["config_activa"]
plc = pb.BandaPLC(ip="127.0.0.1")
plc.client = sim.modbus
plc._esperar = sim.correr
corrupto = lm.compilar_motor(banda({"salidas": {"Q3": "I1"}}))
corrupto[lm.O_OUT + 2] = 777
corrupto[lm.H_CHK] = lm.checksum(corrupto)
plc.comando_v7(lm.CMD_BEGIN)
plc.write_band_block(pb.R(lm.STAGE_BASE), corrupto)
plc.comando_v7(lm.CMD_COMMIT, corrupto[lm.H_ID])
sim.correr(0.1)
e = sim.estado()
check(e["transaccion"] == "rechazada" and e["error_codigo"] == lm.E_RANGE and "salidas.Q3" in e["error"],
      f"el PLC rechaza: {e['error']}")
check(e["config_activa"] == activa and sim.q("Q4"), "la configuracion anterior sigue activa")
try:
    plc.cargar_motor_v7(corrupto)
    check(False, "el backend no debe mandar un bloque invalido")
except ValueError as ex:
    check("fuera de rango" in str(ex), "el backend lo detiene antes de escribir")
cerrar()


# ---------------------------------------------------------------------------
escenario("15. Solicitud con recursos incompatibles")
# ---------------------------------------------------------------------------
for prog, txt in [
        (banda({"salidas": {"Q11": "I1"}}), "Q11 no esta cableada"),
        (banda({"condiciones": [{"id": "C1", "op": "AND", "entradas": ["I7", "I1"]}], "salidas": {"Q4": "C1"}}),
         "I7 no existe"),
        (banda({"motor": {"arranque": "I3"}}, True), "paro de emergencia"),
        (banda({"motor": {"arranque": "NOT I3"}}, True), "paro de emergencia"),
        (banda({"secuencias": [{"id": "SQ1", "inicio": "I1", "pasos": [{"salidas": ["Q8", "Q9"], "segundos": 1}]}]}),
         "no puede subir y bajar"),
        (banda({"opciones": {"luces_paro": ["Q8"]}}), "no es una lampara"),
        (banda({"secuencias": [{"id": "SQ1", "inicio": "I1", "pasos": [{"motor": ["marcha"], "segundos": 1}]}]}),
         "no tiene movimiento")]:
    errs, _ = lm.validar_motor(prog)
    check(any(txt in e for e in errs), f"rechaza: {txt}")
respuestas[:] = [{"name": "x", "banda": {"mover": False}, "salidas": {"Q11": "I7"}}] * 3
r = generar("enciende la salida analogica Q11 con la entrada I7")
check(r.status == "not_supported" and r.analysis["compatibilidad"]["compatibilidad"] == "ninguna",
      "sin alternativa segura: informa la limitacion")
cerrar()


# ---------------------------------------------------------------------------
escenario("16. Interrupcion de comunicacion durante la transferencia")
# ---------------------------------------------------------------------------
sim = cargar(banda({"salidas": {"Q4": "SIEMPRE"}}))
anterior = sim.estado()["config_activa"]
nuevo = banda({"salidas": {"Q5": "SIEMPRE"}})
plc = pb.BandaPLC(ip="127.0.0.1")
plc.client = sim.modbus
plc._esperar = sim.correr
sim.modbus.fallar_en = sim.modbus.peticiones + 6        # se corta a mitad de la escritura del bloque
try:
    plc.cargar_motor_v7(lm.compilar_motor(nuevo))
    check(False, "debe fallar")
except ConnectionError:
    check(True, "la carga falla por la desconexion")
sim.modbus.fallar_en = None
check(sim.estado()["transaccion"] == "recibiendo" and sim.estado()["config_activa"] == anterior and sim.q("Q4"),
      "el PLC sigue con la config anterior; la transaccion queda abierta")
sim.correr(61)
check(sim.estado()["transaccion"] == "tiempo agotado" and sim.q("Q4"), "a los 60 s el PLC la cierra (TIMEOUT)")
res = plc.cargar_motor_v7(lm.compilar_motor(nuevo))
sim.correr(0.2)
check(res["verificada"] and sim.q("Q5") and not sim.q("Q4"), "el reintento aplica y verifica la config nueva")
# corte justo despues del COMMIT: el PLC ya aplico; el estado lo confirma
otro = lm.compilar_motor(banda({"salidas": {"Q3": "SIEMPRE"}}))
plc2 = pb.BandaPLC(ip="127.0.0.1")
plc2.client = sim.modbus
plc2._esperar = sim.correr
plc2.comando_v7(lm.CMD_BEGIN)
plc2.write_band_block(pb.R(lm.STAGE_BASE), otro)
plc2.write_band_block(pb.R(lm.CMD_BASE + 1), [lm.CMD_COMMIT, otro[lm.H_ID]])
plc2._w(pb.R(lm.CMD_BASE), sim.V["LCMD"][0] + 1)
sim.correr(0.2)                                       # sin leer la confirmacion (se "corto")
check(plc2.leer_motor_v7()["config_activa"] == otro[lm.H_ID], "al reconectar, el estado confirma el id aplicado")
cerrar()


# ---------------------------------------------------------------------------
escenario("17. Regresion: sin config v7, el ST v7 == ST v5 (scan a scan)")
# ---------------------------------------------------------------------------
V5 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cscape", "ST_banda_v5.txt")
SALIDAS_FIS = ("PHQ3", "PHQ4", "PHQ5", "PHQ6", "PHQ7", "PHQ8", "PHQ9", "VFD_CONTROL",
               "VFD_FREQCALC", "VFD_RESETREG", "BANDSTATUS", "STOPREASON", "CFGREADY_REG",
               "BANDENABLE_REG", "S1_COUNTACCUM", "S2_COUNTACCUM", "DIRCMD")


def programa_v5(regs, ruta):
    s = si.SimST([0] * 260, freq_hz=regs.get("FREQREQUEST", 30), dir_cmd=regs.get("DIRCMD", 1),
                 stop_mode=regs.get("STOPMODE", 0), ruta=ruta)
    for k, v in regs.items():
        s.V[k] = v
        s.V["R_" + k] = v
    return s


CASOS_V5 = [
    {"S1_ENABLE": 1, "S1_ACTION": 2, "S1_TIMERPRESET": 3, "S1_TORRETAMASK": 2, "TORRETARUN": 1},
    {"S1_ENABLE": 1, "S1_ACTION": 1, "S2_ENABLE": 1, "S2_ACTION": 0, "S2_PLUMA1CMD": 1,
     "S1_COUNTPRESET": 2, "S1_COUNTACTIONMASK": 8 + 4, "S1_COUNTDIRTARGET": 3, "S1_COUNTLAMPMASK": 4,
     "TORRETAIDLE": 4, "STOPMODE": 1},
    {"DIRCMD": 0, "FREQREQUEST": 0, "S2_ENABLE": 1, "S2_ACTION": 0, "S2_TORRETAMASK": 7,
     "I1_LAMPMASK": 1, "TIMEDLAMPMASK": 2, "TIMEDLAMPPRESET": 2},
    {"STOPMODE": 3, "SOFTSTOPCMD": 0, "AUTOSTOPMODE": 1, "AUTOSTOPPRESET": 4, "TORRETARUN": 2, "TORRETAIDLE": 1},
]
GUION = [("I1", 0.3), (None, 2), ("S1", 0.6), (None, 4), ("S2", 0.4), (None, 2), ("S1", 0.3),
         (None, 1), ("I2", 0.3), (None, 1), ("I1", 0.3), (None, 1), ("I3", 0.5), (None, 1),
         ("I1", 0.3), ("S1", 0.3), (None, 6)]
for n, regs in enumerate(CASOS_V5, 1):
    va, vb = programa_v5(regs, V5), programa_v5(regs, ls.ST_V7)
    difs, vistos = 0, set()
    for entrada, dur in GUION:
        if entrada:
            va.poner(**{entrada: True}); vb.poner(**{entrada: True})
        for _ in range(int(round(dur / si.SCAN_S))):
            va.scan(); vb.scan()
            vistos.add((va.V["VFD_CONTROL"], va.V["PHQ3"], va.V["PHQ4"], va.V["PHQ5"]))
            if any(va.V[x] != vb.V[x] for x in SALIDAS_FIS):
                difs += 1
        if entrada:
            va.poner(**{entrada: False}); vb.poner(**{entrada: False})
    check(difs == 0, f"caso v5 {n}: {difs} scans con salidas distintas entre v5 y v7")
    check(len(vistos) > 1, f"caso v5 {n}: el guion ejercita la logica")
    check(vb.V["LSTA"][0] == 7 and vb.V["LSTA"][lm.S_ENGINE] == 0, "v7 se identifica y queda en modo v5")
# volver de v7 a v5: el plan v5 descarga el motor (UNLOAD) y no escribe %R200
sim = cargar(banda({"salidas": {"Q4": "SIEMPRE"}}))
plc = pb.BandaPLC(ip="127.0.0.1")
plc.client = sim.modbus
plc._esperar = sim.correr
sim.modbus.log.clear()
pb.aplicar_config(plc, {"device": "banda", "band": {"enable": False, "torreta_idle": 4}, "outputs": []})
sim.correr(0.5)
check(not sim.estado()["activo"] and sim.salidas() == {"Q5"}, "programa v5 tras v7: UNLOAD y corre v5")
check(all(r != 200 for _t, r, _v in sim.modbus.log), "no se escribio %R200 en un PLC v7")
cerrar()


# ---------------------------------------------------------------------------
escenario("18. Compatibilidad v6 -> v7, timers especiales, paro software y botones de la app")
# ---------------------------------------------------------------------------
v6 = {"enable": True, "direction": "derecha", "freq_hz": 30, "engine": {
    "motor": {"arranque": "I1", "invertir": "T1"},
    "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 3}],
    "secuencia": {"inicio": "S2", "modo": "una_vez", "pasos": [{"acciones": ["amarilla"], "segundos": 1}]}}}
v7 = dict(v6, engine=lm.desde_v6(v6["engine"]))
check(lm.validar_motor(v7)[0] == [] and v7["engine"]["secuencias"][0]["pasos"][0]["salidas"] == ["Q4"],
      "un programa v6 se convierte a v7 sin perder nada")
sim = cargar(v7)
sim.pulsar("I1")
sim.correr(1)
sim.pulsar("S1", 0.2)
t = tiempo_hasta(sim, lambda s: s.estado()["motor"] == "frenando para invertir")
check(t is not None and 2.5 <= t <= 3.1, f"RETARDO de 3 s -> invierte ({t})")
t = tiempo_hasta(sim, lambda s: s.motor() == "dir2", 12)
check(t is not None, "frena a cero y arranca hacia la direccion 2")
errs_app, _ = lm.validar_motor(banda({"motor": {"arranque": "APP1"}}, True))
check(any("APP1 no existe" in e for e in errs_app), f"APP1..APP4 retirados: no se aceptan ({errs_app})")
arr = lm.describir_arranque(banda({"motor": {"arranque": "C2"},
                                   "condiciones": [{"id": "C2", "op": "AND", "entradas": ["I1", "NOT S1"]}]}, True))
check(arr["boton"] == "I1" and "botón verde (I1)" in arr["texto"], f"arranque C2 = I1 Y NOT S1 -> I1 ({arr})")
arr = lm.describir_arranque(banda({"motor": {"arranque": "C1"},
                                   "condiciones": [{"id": "C1", "op": "OR", "entradas": ["I1", "S2"]}]}, True))
check("presiona el botón verde (I1) o espera a que el sensor S2 (I5) detecte" in arr["texto"],
      f"arranque C1 = I1 O S2 ({arr})")
arr = lm.describir_arranque(banda({"secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
    {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5}]}]}, True))
check(arr["boton"] == "I1", f"marcha en la secuencia: se pulsa su inicio I1 ({arr})")
sal_plumas = {"Q9": "T1", "Q7": "T1", "Q8": "T2", "Q6": "T2"}
for resets, debe in [(({"reset": "S2"}, {"reset": "S1"}), True), (({}, {}), False),
                     (({"reset": "T2"}, {"reset": "T1"}), False)]:
    prog = banda({"motor": {"arranque": "I1"}, "salidas": sal_plumas, "timers": [
        {"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 5, **resets[0]},
        {"id": "T2", "tipo": "RETARDO", "entrada": "S2", "segundos": 3, **resets[1]}]}, True)
    errs, _ = lm.validar_motor(prog)
    ok_reset = not any("reset cruzado" in e for e in errs)
    check(ok_reset == debe, f"plumas con dos RETARDO, resets {resets}: {'valido' if debe else 'rechazado'} ({errs})")
    if debe:
        sim = cargar(prog)
        sim.pulsar("I1")
        for _ in range(2):
            sim.pulsar("S1", 0.3)
            sim.correr(5.5)
            check(sim.q("Q9") and sim.q("Q7"), "S1 + 5 s: bajan las dos plumas")
            sim.pulsar("S2", 0.3)
            sim.correr(3.5)
            check(sim.q("Q8") and sim.q("Q6"), "S2 + 3 s: suben las dos plumas (cada ciclo)")
check(pb.texto_arranque({"band": banda({"motor": {"arranque": "S1"}}, True)})
      == "La banda arranca cuando el sensor S1 (I4) detecte.", "aviso de la web con un sensor de arranque")
sim = cargar(banda({"timers": [{"id": "T1", "tipo": "INTERMITENTE", "entrada": "S2", "segundos": 0.5,
                                "segundos_off": 0.3},
                               {"id": "T2", "tipo": "TONR", "entrada": "I1", "reset": "I2", "segundos": 2}],
                    "salidas": {"Q4": "T1", "Q5": "T2"}}))
plc = pb.BandaPLC(ip="127.0.0.1")
plc.client = sim.modbus
plc._esperar = sim.correr
sim.poner(S2=True)
cambios, prev = 0, None
for _ in range(40):
    sim.correr(0.05)
    if sim.q("Q4") != prev:
        cambios += 1
        prev = sim.q("Q4")
check(cambios >= 4, f"INTERMITENTE con S2 ({cambios} cambios en 2 s)")
sim.poner(S2=False)
sim.pulsar("I1", 1.0)
sim.correr(0.5)
check(not sim.q("Q5"), "TONR: 1 s acumulado, aun no")
sim.pulsar("I1", 1.2)
check(sim.q("Q5"), "TONR: suma 1 s + 1.2 s -> activo (retentivo)")
sim = cargar(banda({"motor": {"arranque": "I1"}, "salidas": {"Q3": "SIEMPRE", "Q8": "SIEMPRE"}}, True))
plc.client = sim.modbus
plc._esperar = sim.correr
sim.pulsar("I1")
sim.correr(1)
plc.paro_software_v7(True)
sim.correr(0.3)
check(sim.motor() == "paro" and sim.q("Q3") and not sim.q("Q8"),
      "paro software: motor y pluma fuera, la lampara sigue su logica")
plc.paro_software_v7(False)
sim.correr(1)
check(sim.motor() == "paro", "al liberarlo nada arranca solo")
check(sim.estado()["scans_por_s"] == 100 and sim.estado()["heartbeat"] > 0, "diagnostico: scans/s y heartbeat")
cerrar()


# ---------------------------------------------------------------------------
escenario("19. Modificar un programa existente (contexto del editor)")
# ---------------------------------------------------------------------------
respuestas[:] = [{"name": "verde con I1", "banda": {"mover": False}, "salidas": {"Q3": "I1"},
                  "analisis": {"compatibilidad": "total"}}]
r = generar("enciende la verde con I1")
check(r.status == "ok" and r.logic["band"]["engine"]["salidas"] == {"Q3": "I1"}, "programa base")
ctx = app.ContextoLadder(programa_anterior=r.program, historial=["enciende la verde con I1"])
respuestas[:] = [{"name": "verde I1 + roja S1", "banda": {"mover": False}, "salidas": {"Q3": "I1", "Q5": "S1"}}]
r2 = generar("agrega que la roja encienda con S1", contexto=ctx)
mensajes = " ".join(m["content"] for m in capturado["msgs"])
check(r2.status == "ok" and "LOGICA ACTIVA DE LA BANDA" in mensajes and '"Q3": "I1"' in mensajes,
      "el programa anterior (v7) viaja al modelo para modificarlo")
check(r2.logic["band"]["engine"]["salidas"] == {"Q3": "I1", "Q5": "S1"}, "conserva lo anterior y agrega lo nuevo")
prog_v6 = {"metadata": {"engine_config": {"device": "banda", "band": {"enable": False, "engine": {
    "salidas": {"amarilla": "S2"}}}}}}
respuestas[:] = [{"name": "x", "banda": {"mover": False}, "salidas": {"Q4": "S2", "Q3": "I1"}}]
r3 = generar("y la verde con I1", contexto=app.ContextoLadder(programa_anterior=prog_v6, historial=[]))
mensajes = " ".join(m["content"] for m in capturado["msgs"])
check(r3.status == "ok" and '"Q4": "S2"' in mensajes, "un programa v6 abierto se convierte a v7 como contexto")
cerrar()

escenario("20. Eventos: plumas por sensor armadas por el sistema (reset cruzado)")
texto_ev = ("Quiero que la banda avance a 30 Hz y cuando el sensor uno detecte se espere 5 segundos y baje las dos "
            "plumas y cuando el sensor dos detecte se espere 3 segundos y suba las dos plumas")
respuestas[:] = [{"name": "S1 baja, S2 sube", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
                  "motor": {"arranque": "I1"},
                  "eventos": [{"cuando": "S1", "espera_s": 5, "acciones": {"pluma1": "bajar", "pluma2": "bajar"}},
                              {"cuando": "S2", "espera_s": 3, "acciones": {"pluma1": "subir", "pluma2": "subir"}}]}]
r_ev = generar(texto_ev)
check(r_ev.status == "ok", f"eventos -> programa cargable ({r_ev.status} {r_ev.warnings})")
eng_ev = r_ev.logic["band"]["engine"]
check([(t["tipo"], t["entrada"], t["reset"]) for t in eng_ev["timers"]]
      == [("RETARDO", "S1", "S2"), ("RETARDO", "S2", "S1")], "dos RETARDO con reset cruzado por ENTRADA")
check("eventos" in r_ev.logic["respuesta_llm"], "la respuesta del modelo conserva 'eventos' (memoria y ediciones)")
sim = cargar(r_ev.logic["band"])
sim.pulsar("I1")
for ciclo in (1, 2):
    sim.pulsar("S1", 0.3)
    sim.correr(5.5)
    check(sim.q("Q9") and sim.q("Q7") and not sim.q("Q8"), f"ciclo {ciclo}: S1 + 5 s bajan las dos plumas")
    sim.pulsar("S2", 0.3)
    sim.correr(3.5)
    check(sim.q("Q8") and sim.q("Q6") and not sim.q("Q9"), f"ciclo {ciclo}: S2 + 3 s suben las dos plumas")
check(sim.motor() == "dir1", "la banda sigue avanzando")
cfg_luz = lm.cfg_desde_respuesta({"banda": {"mover": False}, "eventos": [
    {"cuando": "S1", "acciones": {"verde": "encender"}}, {"cuando": "S2", "acciones": {"verde": "apagar"}}]})
check(cfg_luz["band"]["engine"]["memorias"] == [{"id": "M1", "set": "S1", "reset": "S2", "prioridad": "reset"}]
      and cfg_luz["band"]["engine"]["salidas"] == {"Q3": "M1"}, "sin espera: memoria set S1 / reset S2")
for evs, error in (([{"cuando": "S1", "acciones": {"pluma3": "bajar"}}], "no existe"),
                   ([{"acciones": {"roja": "encender"}}], "falta 'cuando'"),
                   ([{"cuando": "S1", "acciones": {"pluma1": "bajar"}}, {"cuando": "s1", "acciones": {"pluma1": "subir"}}],
                    "a la vez")):
    try:
        lm.cfg_desde_respuesta({"banda": {"mover": False}, "eventos": evs})
        msg = ""
    except ValueError as e:
        msg = str(e)
    check(error in msg, f"evento invalido -> error claro ({msg})")
respuestas[:] = [{"name": "x", "banda": {"mover": False}, "eventos": [{"cuando": "S1", "acciones": {"pluma9": "bajar"}}]},
                 {"name": "x", "banda": {"mover": False}, "eventos": [{"cuando": "S1", "acciones": {"pluma1": "bajar"}}]}]
r_ev = generar("cuando S1 detecte baja la pluma 1")
check(r_ev.status == "ok" and r_ev.logic["band"]["engine"]["salidas"] == {"Q9": "M1"},
      "un evento invalido se corrige en la auto-revision")
cerrar()

escenario("21. Eventos de la banda: se detiene N s y vuelve a avanzar en cada deteccion")
respuestas[:] = [{"name": "S1 3 s, S2 5 s", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
                  "motor": {"arranque": "I1"},
                  "eventos": [{"cuando": "S1", "duracion_s": 3, "acciones": {"banda": "pausar", "roja": "encender"}},
                              {"cuando": "S2", "duracion_s": 5, "acciones": {"banda": "detener"}}]}]
r_ev = generar("la banda avanza a 30 Hz; cuando S1 detecte se detiene 3 s con la roja y vuelve a avanzar; "
               "cuando S2 detecte se detiene 5 s y vuelve a avanzar")
check(r_ev.status == "ok", f"pausas por sensor -> programa cargable ({r_ev.status} {r_ev.warnings})")
eng_ev = r_ev.logic["band"]["engine"]
check(eng_ev["motor"]["pausa"] == "C1" and eng_ev["condiciones"][0]["entradas"] == ["T1", "T2"]
      and [t["tipo"] for t in eng_ev["timers"]][:2] == ["PULSO", "PULSO"],
      "detener con duracion_s = pausa por PULSO (no paro)")
sim = cargar(r_ev.logic["band"])
sim.pulsar("I1")
sim.correr(2)
for sen, seg in (("S1", 3), ("S2", 5), ("S1", 3)):
    sim.pulsar(sen, 0.3)
    sim.correr(1)
    check(sim.motor() == "paro", f"{sen}: la banda se detiene")
    sim.correr(seg + 1.5)
    check(sim.motor() == "dir1", f"{sen}: tras {seg} s vuelve a avanzar sola")
check(not sim.q("Q5"), "la roja solo dura la pausa de S1")
try:
    lm.cfg_desde_respuesta({"banda": {"mover": True, "frecuencia_hz": 30},
                            "eventos": [{"cuando": "S1", "acciones": {"banda": "pausar"}}]})
    msg = ""
except ValueError as e:
    msg = str(e)
check("necesita duracion_s" in msg, f"pausar sin tiempo -> error claro ({msg})")
cfg_av = lm.cfg_desde_respuesta({"banda": {"mover": True, "frecuencia_hz": 30}, "motor": {"arranque": "I1"},
                                 "eventos": [{"cuando": "S1", "acciones": {"banda": "avanzar"}}]})
check(cfg_av["band"]["engine"]["motor"]["arranque"] == "C1"
      and cfg_av["band"]["engine"]["condiciones"][0]["entradas"] == ["I1", "S1"],
      "avanzar por evento se suma al arranque del modelo (OR)")
cerrar()

escenario("22. Regresion: lo que funcionaba con los prompts v5/v6, ahora en v7")
M30 = {"mover": True, "direccion": "derecha", "frecuencia_hz": 30}


def caso_v7(texto, resp):
    """Pasa la respuesta del modelo por /generar-logica y la carga en el ST."""
    respuestas[:] = [dict(resp, name=resp.get("name") or texto[:40])]
    r = generar(texto)
    check(r.status == "ok", f"'{texto[:50]}...' -> ok ({r.status} {r.warnings} {r.questions})")
    return cargar(r.logic["band"])


def hasta(sim, cond, maximo=15.0):
    return tiempo_hasta(sim, cond, maximo) is not None


# v6-1: espera y cambia el sentido en cada deteccion
sim = caso_v7("Arranca con I1 a la derecha a 30 Hz; cuando S1 detecte una caja espera 3 s y cambia el sentido.",
              {"banda": M30, "motor": {"arranque": "I1"},
               "eventos": [{"cuando": "S1", "espera_s": 3, "acciones": {"banda": "invertir"}}]})
sim.pulsar("I1")
sim.correr(1)
sim.pulsar("S1")
check(hasta(sim, lambda s: s.motor() == "dir2"), "v6-1: S1 + 3 s invierte a dir2")
sim.correr(1)
sim.pulsar("S1")
check(hasta(sim, lambda s: s.motor() == "dir1"), "v6-1: el siguiente S1 vuelve a invertir")

# v6-2: cada sensor mueve la banda hacia un lado
sim = caso_v7("A 30 Hz: cuando el sensor 1 detecte la banda se mueve a la derecha y cuando el sensor 2 detecte "
              "se mueve a la izquierda.",
              {"banda": M30, "eventos": [{"cuando": "S1", "acciones": {"banda": "derecha"}},
                                         {"cuando": "S2", "acciones": {"banda": "izquierda"}}]})
sim.correr(1)
check(sim.motor() == "paro", "v6-2: quieta hasta que un sensor detecte")
sim.pulsar("S1")
check(hasta(sim, lambda s: s.motor() == "dir1"), "v6-2: S1 -> derecha")
sim.pulsar("S2")
check(hasta(sim, lambda s: s.motor() == "dir2"), "v6-2: S2 -> izquierda")
sim.pulsar("S1")
check(hasta(sim, lambda s: s.motor() == "dir1"), "v6-2: S1 otra vez -> derecha")

# v6-3: plumas con espera + luces de estado
sim = caso_v7("Mueve la banda a la derecha a 20 Hz; cuando S1 detecte espera 20 s y sube las dos plumas; cuando S2 "
              "detecte espera 10 s y bajalas. Verde en movimiento y roja detenida.",
              {"banda": dict(M30, frecuencia_hz=20), "motor": {"arranque": "I1"},
               "salidas": {"verde": "BANDA_CORRIENDO", "roja": "BANDA_DETENIDA"},
               "eventos": [{"cuando": "S1", "espera_s": 20, "acciones": {"pluma1": "subir", "pluma2": "subir"}},
                           {"cuando": "S2", "espera_s": 10, "acciones": {"pluma1": "bajar", "pluma2": "bajar"}}]})
check(sim.q("Q5") and not sim.q("Q3"), "v6-3: roja con la banda detenida")
sim.pulsar("I1")
sim.correr(1)
check(sim.q("Q3") and not sim.q("Q5"), "v6-3: verde en movimiento")
sim.pulsar("S1")
sim.correr(20.5)
check(sim.q("Q8") and sim.q("Q6"), "v6-3: S1 + 20 s suben las plumas")
sim.pulsar("S2")
sim.correr(10.5)
check(sim.q("Q9") and sim.q("Q7") and not sim.q("Q8"), "v6-3: S2 + 10 s bajan las plumas")

# v6-4: condicion AND + contador con reset
sim = caso_v7("Cuando presione el boton verde y S2 este activo enciende la amarilla; S1 cuenta 3 cajas y prende la "
              "roja hasta presionar I2.",
              {"banda": {"mover": False}, "condiciones": [{"id": "C1", "op": "AND", "entradas": ["I1", "S2"]}],
               "contadores": [{"id": "CT1", "entrada": "S1", "reset": "I2", "preset": 3, "modo": "una_vez"}],
               "salidas": {"amarilla": "C1", "roja": "CT1"}})
sim.poner(I1=True, S2=True)
sim.correr(0.5)
check(sim.q("Q4"), "v6-4: I1 y S2 -> amarilla")
sim.poner(I1=False, S2=False)
sim.correr(0.5)
for _ in range(3):
    sim.pulsar("S1")
    sim.correr(0.3)
check(sim.q("Q5"), "v6-4: 3 cajas -> roja")
sim.pulsar("I2")
sim.correr(0.5)
check(not sim.q("Q5"), "v6-4: I2 reinicia la cuenta")

# v6-5: paro por I2 o S2, pluma 2 s con S1
sim = caso_v7("A 25 Hz arranca con I1; se detiene cuando el boton rojo o S2 detecten. Cuando S1 detecte, sube la "
              "pluma 1 por 2 s.",
              {"banda": dict(M30, frecuencia_hz=25), "motor": {"arranque": "I1"},
               "eventos": [{"cuando": "I2", "acciones": {"banda": "detener"}},
                           {"cuando": "S2", "acciones": {"banda": "detener"}},
                           {"cuando": "S1", "duracion_s": 2, "acciones": {"pluma1": "subir"}}]})
sim.pulsar("I1")
sim.correr(1)
sim.pulsar("S1")
sim.correr(1)
check(sim.q("Q8") and sim.motor() == "dir1", "v6-5: S1 sube la pluma 1 sin parar la banda")
sim.correr(1.5)
check(not sim.q("Q8"), "v6-5: la pluma solo 2 s")
sim.pulsar("S2")
check(hasta(sim, lambda s: s.motor() == "paro", 3), "v6-5: S2 detiene")
sim.pulsar("I1")
check(hasta(sim, lambda s: s.motor() == "dir1", 3), "v6-5: I1 vuelve a arrancar")
sim.pulsar("I2")
check(hasta(sim, lambda s: s.motor() == "paro", 3), "v6-5: I2 detiene")

# v6-6: secuencia de luces disparada por S1
sim = caso_v7("Cuando S1 detecte una caja: luz amarilla, espera 3 s, luz verde 5 s y apaga.",
              {"banda": {"mover": False}, "secuencias": [{"id": "SQ1", "inicio": "S1", "modo": "una_vez",
                                                          "exclusiva": True, "pasos": [
                  {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 3},
                  {"salidas": ["Q3"], "avanza": "tiempo", "segundos": 5}]}]})
sim.pulsar("S1")
sim.correr(1)
check(sim.q("Q4") and not sim.q("Q3"), "v6-6: amarilla primero")
sim.correr(3)
check(sim.q("Q3") and not sim.q("Q4"), "v6-6: luego verde")
sim.correr(5)
check(not sim.q("Q3") and not sim.q("Q4"), "v6-6: y apaga")

# v6-7: ida y vuelta
sim = caso_v7("Con el boton verde la banda avanza a la derecha a 30 Hz; cuando S1 detecte espera 2 s, prende la "
              "amarilla y cambia de sentido; cuando S2 detecte la caja detén la banda y prende la verde.",
              {"banda": M30, "motor": {"arranque": "I1"},
               "eventos": [{"cuando": "S1", "espera_s": 2, "acciones": {"amarilla": "encender", "banda": "invertir"}},
                           {"cuando": "S2", "acciones": {"banda": "detener", "verde": "encender",
                                                         "amarilla": "apagar"}}]})
sim.pulsar("I1")
sim.correr(1)
sim.pulsar("S1")
sim.correr(2.5)
check(sim.q("Q4"), "v6-7: S1 + 2 s amarilla")
check(hasta(sim, lambda s: s.motor() == "dir2"), "v6-7: y cambia de sentido")
sim.pulsar("S2")
check(hasta(sim, lambda s: s.motor() == "paro", 3), "v6-7: S2 detiene")
check(sim.q("Q3") and not sim.q("Q4"), "v6-7: verde encendida, amarilla apagada")

# v5-1: enclavar con I1, apagar con I2
sim = caso_v7("El boton verde enciende la lampara verde y se queda prendida; el rojo la apaga.",
              {"banda": {"mover": False}, "eventos": [{"cuando": "I1", "acciones": {"verde": "encender"}},
                                                      {"cuando": "I2", "acciones": {"verde": "apagar"}}]})
sim.pulsar("I1")
sim.correr(1)
check(sim.q("Q3"), "v5-1: I1 enciende y se queda")
sim.pulsar("I2")
sim.correr(0.5)
check(not sim.q("Q3"), "v5-1: I2 apaga")

# v5-2: contar pulsos de I1
sim = caso_v7("Cuenta los pulsos de I1; al llegar a 5 la lampara verde se enciende y se queda enclavada.",
              {"banda": {"mover": False}, "contadores": [{"id": "CT1", "entrada": "I1", "preset": 5,
                                                          "modo": "una_vez"}],
               "salidas": {"verde": "CT1"}})
for _ in range(4):
    sim.pulsar("I1")
    sim.correr(0.3)
check(not sim.q("Q3"), "v5-2: con 4 pulsos sigue apagada")
sim.pulsar("I1")
sim.correr(2)
check(sim.q("Q3"), "v5-2: al 5o pulso queda encendida")

# v5-3: secuencia de torreta
sim = caso_v7("Al presionar I1 enciende la verde 5 s, luego la amarilla 2 s, luego la roja 8 s.",
              {"banda": {"mover": False}, "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez",
                                                          "exclusiva": True, "pasos": [
                  {"salidas": ["Q3"], "avanza": "tiempo", "segundos": 5},
                  {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 2},
                  {"salidas": ["Q5"], "avanza": "tiempo", "segundos": 8}]}]})
sim.pulsar("I1")
sim.correr(1)
verde = sim.q("Q3")
sim.correr(5)
amarilla = sim.q("Q4")
sim.correr(2)
check(verde and amarilla and sim.q("Q5"), "v5-3: verde, amarilla y roja en orden")

# v5-4: pluma con cada deteccion + contador de piezas
sim = caso_v7("Cada que el sensor 1 detecte sube la pluma 1 y cuando cuente 3 piezas enciende la amarilla.",
              {"banda": {"mover": False},
               "eventos": [{"cuando": "S1", "duracion_s": 2, "acciones": {"pluma1": "subir"}}],
               "contadores": [{"id": "CT1", "entrada": "S1", "preset": 3, "modo": "una_vez"}],
               "salidas": {"amarilla": "CT1"}})
for _ in range(3):
    sim.pulsar("S1")
    sim.correr(0.5)
    check(sim.q("Q8"), "v5-4: S1 sube la pluma 1")
    sim.correr(2)
check(sim.q("Q4"), "v5-4: 3 piezas -> amarilla")

# v5-5: plumas sin espera
sim = caso_v7("Cuando el sensor 1 detecte, sube las dos plumas y cuando el sensor 2 detecte, baja las dos plumas.",
              {"banda": {"mover": False}, "eventos": [
                  {"cuando": "S1", "acciones": {"pluma1": "subir", "pluma2": "subir"}},
                  {"cuando": "S2", "acciones": {"pluma1": "bajar", "pluma2": "bajar"}}]})
for _ in range(2):
    sim.pulsar("S1")
    sim.correr(0.5)
    check(sim.q("Q8") and sim.q("Q6"), "v5-5: S1 sube")
    sim.pulsar("S2")
    sim.correr(0.5)
    check(sim.q("Q9") and sim.q("Q7") and not sim.q("Q8"), "v5-5: S2 baja")

# v5-6: pausa con roja y plumas, luego S2 baja
sim = caso_v7("Avanza la banda a la derecha a 30 Hz, cuando S1 detecte detente 5 segundos, prende la roja y sube "
              "las dos plumas; despues continua y cuando S2 detecte baja las dos plumas.",
              {"banda": M30, "motor": {"arranque": "I1"}, "eventos": [
                  {"cuando": "S1", "duracion_s": 5, "acciones": {"banda": "pausar", "roja": "encender"}},
                  {"cuando": "S1", "acciones": {"pluma1": "subir", "pluma2": "subir"}},
                  {"cuando": "S2", "acciones": {"pluma1": "bajar", "pluma2": "bajar"}}]})
sim.pulsar("I1")
sim.correr(1)
sim.pulsar("S1")
sim.correr(1)
check(sim.motor() == "paro" and sim.q("Q5") and sim.q("Q8"), "v5-6: S1 detiene, roja y plumas arriba")
check(hasta(sim, lambda s: s.motor() == "dir1", 6) and not sim.q("Q5"), "v5-6: a los 5 s continua")
sim.pulsar("S2")
sim.correr(0.5)
check(sim.q("Q9") and sim.q("Q7"), "v5-6: S2 baja las plumas")

# v5-7: contador detiene todo
sim = caso_v7("Avanza a la derecha a 30 Hz; si el contador de S1 llega a 5, detén todo el proceso y enciende la "
              "amarilla.",
              {"banda": M30, "motor": {"arranque": "I1"},
               "contadores": [{"id": "CT1", "entrada": "S1", "preset": 5, "modo": "una_vez"}],
               "eventos": [{"cuando": "CT1", "acciones": {"banda": "detener", "amarilla": "encender"}}]})
sim.pulsar("I1")
sim.correr(1)
for _ in range(5):
    sim.pulsar("S1")
    sim.correr(0.3)
check(hasta(sim, lambda s: s.motor() == "paro", 3) and sim.q("Q4"), "v5-7: a las 5 cajas se detiene con amarilla")
cerrar()

escenario("23. Instrucciones generales: plantillas de proceso con operacion robusta")
import plantillas_proceso as pp
import contexto_ramificado as cr

# deteccion por raices (antes: "clasifique", "empaca" o "control de calidad" no se reconocian)
for texto, esperado in [("Haz que la banda clasifique productos", "clasificacion"), ("Empaca de 6 en 6", "empaquetado"),
                        ("Simula un control de calidad", "inspeccion"), ("Arma una estación de sellado", "estacion_trabajo"),
                        ("Haz un proceso de pintura", "estacion_trabajo"), ("Simula paletizado de cajas", "paletizado"),
                        ("Simula una línea de empaquetado", "empaquetado"), ("Etiquete las botellas", "etiquetado"),
                        ("Simula un almacén que recibe paquetes", "alimentacion_distribucion")]:
    hall = pp.plantillas_mencionadas(texto)
    check(hall[:1] == [esperado], f"'{texto}' -> {esperado} ({hall})")
check(pp.plantillas_mencionadas("Simula una línea de empaquetado") == ["empaquetado"],
      "la linea de produccion generica no se suma a un proceso especifico")
check(cr.proceso_desconocido("Arma una estación de sellado") is None, "sellado ya no se pregunta: es una estacion")
check(cr.proceso_desconocido("Haz un proceso de minería") == "mineria", "un proceso sin plantilla se sigue preguntando")

# el bloque de plantillas solo va en instrucciones generales
_, plan = cr.mensajes_sistema_banda_v7(app.SYSTEM_PROMPT_BANDA_V7, "Simula una línea de empaquetado", [])
check(plan["procesos"] == ["empaquetado"] and any(c.startswith("plantillas_proceso") for c in plan["contexto"]),
      "instruccion general -> plantillas")
_, plan = cr.mensajes_sistema_banda_v7(app.SYSTEM_PROMPT_BANDA_V7,
                                      "Con I1 avanza a 30 Hz y cuando S1 detecte prende la roja 3 s", [])
check(not plan["procesos"] and not any(c.startswith("plantillas") for c in plan["contexto"]),
      "instruccion especifica -> sin plantillas")

# todas las plantillas se expanden a un programa valido para el PLC
for pid in pp.PLANTILLAS:
    c = lm.cfg_desde_respuesta({"name": pid, "banda": M30, "proceso": {"id": pid}})
    check(not lm.validar_motor(c["band"])[0], f"plantilla {pid} valida")
for spec, error in [({"id": "volar"}, "no existe"), ({"id": "llenado", "parametros": {"color": 3}}, "desconocidos"),
                    ({"id": "llenado", "parametros": {"sensor": "S3"}}, "S1 o S2"),
                    ({"id": "empaquetado", "parametros": {"piezas": 0}}, "entero")]:
    try:
        lm.cfg_desde_respuesta({"name": "x", "banda": M30, "proceso": spec})
        check(False, f"{spec} deberia fallar")
    except ValueError as e:
        check(error in str(e), f"{spec['id']}: {e}")


def caso_general(texto, proceso):
    respuestas[:] = [{"name": proceso["id"], "banda": M30, "proceso": proceso,
                      "analisis": {"intencion": texto, "compatibilidad": "parcial", "conservado": [],
                                   "modificado": ["valores tipicos"], "simplificado": [], "no_soportado": [],
                                   "requiere_confirmacion": True}}]
    r = generar(texto)
    check(r.status == "needs_confirmation" and r.analysis["proceso"]["id"] == proceso["id"],
          f"'{texto}' -> plantilla {proceso['id']} con confirmacion")
    band = r.logic["band"]
    band["engine"]["confirmado"] = True
    return r, cargar(band)


# empaquetado de 4: lote, pluma, torreta, atasco y paro
r, sim = caso_general("Simula una línea de empaquetado de 4 piezas", {"id": "empaquetado", "parametros": {"piezas": 4}})
check("pausa_s = 3" in r.analysis["proceso"]["supuestos"] and any("supuestos" in w for w in r.warnings),
      "reporta los valores supuestos")
sim.correr(0.5)
check(sim.motor() == "paro" and sim.salidas() == {"Q4"}, "lista: detenida con amarilla")
sim.pulsar("I1")
sim.correr(0.5)
check(sim.motor() == "dir1" and sim.salidas() == {"Q3"}, "I1: en marcha con verde")
for _ in range(3):
    sim.pulsar("S1")
    sim.correr(0.5)
check(sim.motor() == "dir1", "3 piezas: sigue")
sim.pulsar("S1")
sim.correr(0.5)
check(sim.motor() == "paro" and sim.q("Q9") and sim.q("Q4"), "4a pieza: para, la pluma deposita, amarilla")
check(hasta(sim, lambda s: s.motor() == "dir1", 10), "termina el lote y continua sola")
for _ in range(3):
    sim.pulsar("S1")
    sim.correr(0.5)
check(sim.motor() == "dir1", "el conteo empezo de nuevo")
sim.poner(S2=True)
check(hasta(sim, lambda s: s.motor() == "paro" and s.q("Q5"), 12), "S2 ocupado 10 s en marcha: atasco, paro y roja")
sim.poner(S2=False)
sim.correr(2)
check(sim.motor() == "paro" and sim.q("Q5"), "la falla se mantiene hasta reconocerla")
sim.pulsar("I1")
sim.correr(0.5)
check(sim.q("Q4") and not sim.q("Q5"), "I1 reconoce la falla")
sim.pulsar("I1")
sim.correr(0.5)
check(sim.motor() == "dir1", "otro I1 arranca")
sim.pulsar("I2")
sim.correr(0.5)
check(sim.motor() == "paro", "I2 detiene")

# llenado: el paro manda aunque la estacion este trabajando
r, sim = caso_general("Haz un proceso de llenado", {"id": "llenado"})
sim.pulsar("I1")
sim.correr(0.5)
sim.pulsar("S1")
sim.correr(0.5)
check(sim.motor() == "paro" and sim.q("Q9"), "S1: la estacion detiene y baja la pluma")
sim.pulsar("I2")
sim.correr(15)
check(sim.motor() == "paro" and not sim.q("Q9"), "tras I2 la estacion termina (pluma arriba) y la banda no arranca")
sim.pulsar("S1")
sim.correr(0.5)
check(not sim.q("Q9"), "con la banda detenida por I2, S1 no baja la pluma")
r, sim = caso_general("Haz un proceso de llenado", {"id": "llenado"})
sim.pulsar("S1")
sim.correr(0.5)
check(not sim.q("Q9"), "sin arrancar la banda, S1 no baja la pluma")
sim.pulsar("I1")
sim.correr(0.5)
sim.poner(S1=True)
check(hasta(sim, lambda s: s.motor() == "dir1" and s.q("Q3"), 15) and not sim.q("Q9"),
      "la pieza que sigue en S1 al terminar la estacion no la repite")

# empaquetado: con la banda detenida no se cuentan piezas
r, sim = caso_general("Simula una línea de empaquetado de 2 piezas", {"id": "empaquetado", "parametros": {"piezas": 2}})
for _ in range(2):
    sim.pulsar("S1")
    sim.correr(0.5)
sim.pulsar("I1")
sim.correr(0.5)
sim.pulsar("S1")
sim.correr(0.5)
check(sim.motor() == "dir1" and not sim.q("Q9"), "las piezas con la banda detenida no cuentan para el lote")
sim.pulsar("S1")
sim.correr(0.5)
check(sim.motor() == "paro" and sim.q("Q9"), "con la banda en marcha el lote de 2 se completa")

# paletizado: espera al operador
r, sim = caso_general("Simula paletizado de 3 cajas", {"id": "paletizado", "parametros": {"piezas": 3}})
sim.pulsar("I1")
sim.correr(0.5)
for _ in range(3):
    sim.pulsar("S1")
    sim.correr(0.5)
sim.correr(10)
check(sim.motor() == "paro" and sim.q("Q4"), "pallet completo: espera al operador con amarilla")
sim.pulsar("I1")
sim.correr(0.5)
check(sim.motor() == "dir1", "I1 retira el pallet y continua")

# acumulacion: S2 ocupado detiene sin falla y reanuda al liberarse
r, sim = caso_general("Simula un almacén que recibe paquetes", {"id": "alimentacion_distribucion"})
sim.pulsar("I1")
sim.correr(0.5)
sim.poner(S2=True)
sim.correr(15)
check(sim.motor() == "paro" and not sim.q("Q5"), "final ocupado: espera sin marcar atasco")
sim.poner(S2=False)
sim.correr(1)
check(sim.motor() == "dir1", "al liberarse continua")
cerrar()

app.BANDA_MOTOR_V7 = False


# ---------------------------------------------------------------------------
escenario("24. Corte de energia: la ultima config v7 se conserva")
# ---------------------------------------------------------------------------
def energizar(sim):
    """Apagar y encender: lo que esta en %R (LACT, LI, LCK...) se conserva;
    LB y LSIG (internas, sin tag) se pierden; entradas sueltas."""
    for a in ("LB", "LSIG"):
        sim.V[a] = type(sim.V[a])()
    sim.vfd_hz = 0.0
    sim.fst = True
    sim.poner(**{k: False for k in sim.u})
    sim.correr(4)


mover = {"enable": True, "direction": "derecha", "freq_hz": 30, "engine": {"version": 7}}
sim = ls.SimV7()
sim.correr(0.3)
sim.cargar(mover)
ident = sim.estado()["config_activa"]
energizar(sim)
check(sim.estado()["activo"] and sim.estado()["config_activa"] == ident, "tras energizar sigue la misma config v7")
check(sim.motor() == "paro", "tras energizar la banda NO arranca sola")
sim.pulsar("I1")
sim.correr(2)
check(sim.motor() == "dir1" and sim.vfd_hz > 15, "I1 arranca con la config restaurada")

sim = ls.SimV7()
sim.correr(0.3)
sim.cargar({"enable": False, "engine": {"version": 7, "salidas": {"Q3": "S1"}}})
energizar(sim)
sim.poner(S1=True)
sim.correr(0.5)
check(sim.estado()["activo"] and sim.salidas() == {"Q3"}, "config sin movimiento: S1 sigue prendiendo Q3")
sim.poner(S1=False)

sim = ls.SimV7()
sim.correr(0.3)
sim.cargar(mover)
plc = pb.BandaPLC(ip="127.0.0.1")
plc.client = sim.modbus
plc._esperar = sim.correr
plc.descargar_v7()
sim.correr(0.5)
energizar(sim)
check(not sim.estado()["activo"], "tras UNLOAD y corte vuelve v5 (no revive el v7 descargado)")

sim = ls.SimV7()
sim.correr(0.3)
sim.cargar(mover)
sim.V["LACT"][lm.H_MOVE] += 1
energizar(sim)
check(not sim.estado()["activo"], "LACT alterada (checksum no coincide): no se restaura, corre v5")
cerrar()

print("\n" + "=" * 78)
print(f"{'Escenario':<66}{'comprob.':>10}")
for nombre, _ini, n in TABLA:
    print(f"{nombre[:66]:<66}{n:>10}")
print("=" * 78)
print(f"TODAS LAS PRUEBAS DEL MOTOR LAVO v7 PASARON ({OK} comprobaciones).")
