"""
test_ronda8.py — el verificador interno no da falsas alarmas (auditoria del banco,
Pruebas banda/auditar.py: 39 -> 1 en 231 logicas, 0 fallas sin detectar).

  1. arranque con espera previa ("primero la amarilla 3 s y despues arranca"):
     las pruebas esperan a que la banda corra (antes: "la banda no arranca")
  2. orden con varias secuencias: cada secuencia pedida se compara con la del JSON
     que arranca con su misma senal; una etapa con varios tiempos ocupa varios
     pasos; una luz manejada fuera de la secuencia por SQk_PASOi cuenta en el paso
  3. una secuencia pedida de una sola etapa con disparo puede ir con timers
  4. un paso de 0 s (decision) no cierra la etapa
  5. "con I2 se reinicia el contador": el nuevo ciclo es I2 y luego I1
  y en cada caso, que una logica que SI falla se sigue detectando.

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda8.py
"""

import contextlib
import copy
import io
import json
import logging

logging.disable(logging.WARNING)

import comportamiento as co
import etapas_orden as eo
import intencion_banda

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


def intencion(texto):
    with contextlib.redirect_stdout(io.StringIO()):
        return intencion_banda.analizar(texto, [], {}, None, False)


def verificar_c(cfg, reqs):
    with contextlib.redirect_stdout(io.StringIO()):
        return co.verificar(cfg, reqs)


def cfg_de(engine, hz=30):
    return {"band": {"enable": True, "direction": "derecha", "freq_hz": hz, "stop_mode": 0,
                     "engine": {"version": 7, **engine}}}


# ── 1. C8: la amarilla 3 s antes de arrancar ─────────────────────────────────
T8 = ("Al presionar I1, primero se enciende la luz amarilla durante 3 segundos y después la banda arranca a 35 Hz. "
      "Al presionar I2 la banda se detiene y se enciende la luz roja durante 5 segundos.")
C8 = cfg_de({"secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pausa_apaga": True,
                              "pasos": [{"salidas": ["Q4"], "motor": [], "avanza": "tiempo", "segundos": 3},
                                        {"salidas": [], "motor": ["marcha"], "avanza": "senal", "senal": "I2"},
                                        {"salidas": ["Q5"], "motor": [], "avanza": "tiempo", "segundos": 5,
                                         "siguiente": "fin"}]}]}, 35)
escenario("1. arranque con espera previa (banco C8)")
reqs8 = intencion(T8)["requisitos"]
check(any(r["tipo"] == "para_con" for r in reqs8), "se prueba que I2 la detiene")
check(verificar_c(C8, reqs8) == [], "la logica del banco (amarilla 3 s y luego marcha) cumple: sin falsa alarma")
no_para = copy.deepcopy(C8)
no_para["band"]["engine"]["secuencias"][0]["pasos"][1]["senal"] = "I3"
check(any("NO se detiene" in f for f in verificar_c(no_para, reqs8)),
      "tras la espera la prueba SI corre: si I2 no la detiene se detecta")
lenta = copy.deepcopy(C8)
lenta["band"]["engine"]["secuencias"][0]["pasos"][0]["segundos"] = 8
check(verificar_c(lenta, reqs8) == [], "con 8 s de espera tambien espera a que arranque")

# ── 2. C5: dos secuencias, una con varios tiempos y otra que parpadea ───────
T5 = ("Secuencia 1: al presionar I1, la banda avanza 10 segundos, se detiene 3 segundos y vuelve a avanzar 5 "
      "segundos y se detiene. Secuencia 2: cuando el sensor 2 detecta una caja, la luz roja parpadea durante 4 "
      "segundos.")
_SQ1 = {"id": "SQ1", "inicio": "I1", "modo": "una_vez", "exclusiva": True, "pausa_apaga": True, "pasos": [
    {"salidas": [], "motor": ["marcha"], "avanza": "tiempo", "segundos": 10},
    {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 3},
    {"salidas": [], "motor": ["marcha"], "avanza": "tiempo", "segundos": 5, "siguiente": "fin"},
    {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 0, "siguiente": "fin"}]}
# r11b: la reparacion de parpadeo saca la roja del paso y la maneja con un timer de SQ2_PASO1
C5_PASO = cfg_de({"timers": [{"id": "T1", "tipo": "INTERMITENTE", "entrada": "SQ2_PASO1", "segundos": 0.5,
                              "segundos_off": 0.5}],
                  "salidas": {"Q5": "T1"},
                  "secuencias": [_SQ1, {"id": "SQ2", "inicio": "S2", "modo": "una_vez", "exclusiva": True,
                                        "pausa_apaga": True, "pasos": [
                                            {"salidas": [], "motor": [], "avanza": "tiempo", "segundos": 4,
                                             "siguiente": "fin"}]}]}, 10)
escenario("2. orden con dos secuencias (banco C5)")
et5 = intencion(T5)["etapas"]
check(et5["aplica"] and len(et5["etapas"]) == 2, "dos etapas, una por secuencia")
check(eo.verificar(C5_PASO, et5) == [], "r11b: SQ1 con 10/3/5 s y la roja por SQ2_PASO1 respetan el orden")
mal_t = copy.deepcopy(C5_PASO)
mal_t["band"]["engine"]["secuencias"][0]["pasos"][1]["segundos"] = 7
f = eo.verificar(mal_t, et5)
check(len(f) == 1 and "etapa 1" in f[0] and "3 s" in f[0], "si la pausa dura 7 s en vez de 3 se detecta en la etapa 1")
sin_luz = copy.deepcopy(C5_PASO)
sin_luz["band"]["engine"]["salidas"] = {}
check(any("etapa 2" in x for x in eo.verificar(sin_luz, et5)), "si nada enciende la roja se detecta en la etapa 2")

# ── 3. C5 r8a: la secuencia 2 hecha solo con timers del sensor ──────────────
C5_FUERA = cfg_de({"timers": [{"id": "T1", "tipo": "PULSO", "entrada": "I5", "segundos": 4},
                              {"id": "T2", "tipo": "INTERMITENTE", "entrada": "T1", "segundos": 0.5,
                               "segundos_off": 0.5}],
                   "salidas": {"Q5": "T2"}, "secuencias": [_SQ1]}, 10)
escenario("3. secuencia de una etapa armada con timers (banco C5 r8a)")
check(eo.verificar(C5_FUERA, et5) == [], "la roja por T2 <- T1 <- S2 cuenta como la secuencia 2")
otro = copy.deepcopy(C5_FUERA)
otro["band"]["engine"]["timers"][0]["entrada"] = "I4"
check(any("etapa 2" in x for x in eo.verificar(otro, et5)), "si la dispara S1 en vez de S2 se detecta")

# ── 4. paso de decision de 0 s antes de los 5 s de avance ──────────────────
escenario("4. un paso de 0 s no cierra la etapa (banco C1 t1)")
etapa = {"n": 1, "texto": "al contar 3 avanza 5 s", "requisitos": [{"tipo": "motor", "accion": "marcha", "desc": "marcha"}],
         "tiempos": [5.0], "hasta": None, "entra_con": None}
pasos = [{"motor": ["marcha"], "segundos": 0, "salidas": []}, {"motor": ["marcha"], "segundos": 5, "salidas": []}]
check(eo._verificar_cadena([etapa], pasos, eo._cumple_banda, "banda") == [], "los 5 s del paso siguiente cuentan")
pasos_mal = [{"motor": ["marcha"], "segundos": 0, "salidas": []}, {"motor": ["marcha"], "segundos": 8, "salidas": []}]
check(eo._verificar_cadena([etapa], pasos_mal, eo._cumple_banda, "banda") != [], "si dura 8 s se detecta")

# ── 5. C10: "con I2 se reinicia el contador" ────────────────────────────────
T10 = ("I1 arranca la banda a 30 Hz. Cuando el sensor 1 detecta, la luz verde se enciende 2 segundos. El sensor 2 "
       "cuenta cajas y al llegar a 4 se enciende la luz roja y la banda se detiene. Con I2 se reinicia el contador y "
       "se apaga la luz roja.")
C10 = cfg_de({"motor": {"arranque": "I1", "paro": "C2", "modo": "enclavado"},
              "condiciones": [{"id": "C2", "op": "OR", "entradas": ["I2", "M1"], "flanco": "nivel"},
                              {"id": "C3", "op": "OR", "entradas": ["I1", "I2"], "flanco": "nivel"}],
              "memorias": [{"id": "M1", "set": "CT1", "reset": "C3", "prioridad": "reset"}],
              "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "I4", "segundos": 2.0}],
              "contadores": [{"id": "CT1", "entrada": "I5", "reset": "I2", "preset": 4, "modo": "una_vez"}],
              "salidas": {"Q3": "T1", "Q5": "CT1"}})
# r7a: tras I2 hay que pulsar I1 DOS veces para que vuelva a arrancar (defecto real)
C10_DOBLE = cfg_de({"motor": {"arranque": "I1", "paro": "C1", "modo": "enclavado"},
                    "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "I4", "segundos": 2.0}],
                    "contadores": [{"id": "CT1", "entrada": "I5", "reset": "I2", "preset": 4, "modo": "una_vez"}],
                    "condiciones": [{"id": "C1", "op": "OR", "entradas": ["I2", "M1"], "flanco": "nivel"},
                                    {"id": "C2", "op": "AND", "entradas": ["CT1"], "flanco": "subida"}],
                    "memorias": [{"id": "M1", "set": "C2", "reset": "I1", "prioridad": "set"},
                                 {"id": "M2", "set": "CT1", "reset": "I2", "prioridad": "set"}],
                    "salidas": {"Q3": "T1", "Q5": "M2"}})
escenario("5. reinicio con boton (banco C10)")
reqs10 = intencion(T10)["requisitos"]
pc = next(r for r in reqs10 if r["tipo"] == "para_sola")
check(pc.get("boton_reset") == "I2" and pc.get("rearranque"), "el nuevo ciclo empieza con I2 (boton de reinicio)")
check(verificar_c(C10, reqs10) == [], "r8b: I2 reinicia y luego I1 arranca: sin falsa alarma")
check(any("I2 y luego I1" in f for f in verificar_c(C10_DOBLE, reqs10)),
      "r7a: si tras I2 + I1 la banda no arranca se detecta (defecto real)")

print(f"\nTODAS LAS PRUEBAS DE LA RONDA 8 PASARON ({OK} comprobaciones).")
