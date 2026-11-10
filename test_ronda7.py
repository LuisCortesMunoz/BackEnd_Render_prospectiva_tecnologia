"""
test_ronda7.py — banco C9 r7a: el modelo numero los pasos de la secuencia desde 0.

  "Al presionar I1 la banda avanza a 30 Hz hasta que el sensor 2 detecta una caja;
   entonces se detiene 4 segundos, la pluma 1 sube y la banda vuelve a avanzar.
   Esto se repite 3 veces y después la banda se detiene."

  El modelo armo SQ1 repetir 3: [marcha hasta S2 -> siguiente 1] [paro + Q8 4 s
  -> siguiente 0]. En el motor v7 el paso 1 es el primero, asi que el paso 1
  saltaba a si mismo y la banda nunca paraba ni subia la pluma.

  1. reparaciones_banda._indices_base0 corrige los destinos (incl. alternativa)
  2. no toca secuencias bien numeradas
  3. en lavo_sim la logica reparada hace los 3 ciclos y se detiene
  4. C9 t3: lo que hace la pluma en la pausa y al reanudar se prueba simulando
  5. si la vigente cumple todo menos eso, parche_plumas lo agrega sin el modelo

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda7.py
"""

import asyncio
import contextlib
import copy
import io
import logging
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_r7_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_r7.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

logging.disable(logging.WARNING)

with contextlib.redirect_stdout(io.StringIO()):
    import app
import lavo_motor as lm
import lavo_sim as ls
import parche_plumas as pp
import comportamiento as co
import reparaciones_banda as rb

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


def c9(repeticiones=3, sig1=1, sig2=0):
    return {"motor": {"paro": "I3"},
            "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I3", "reset": None, "pausa": None,
                            "modo": "repetir", "repeticiones": repeticiones, "exclusiva": True, "pausa_apaga": True,
                            "pasos": [{"motor": ["marcha"], "avanza": "senal", "senal": "I5", "siguiente": sig1},
                                      {"salidas": ["Q8"], "motor": ["paro"], "avanza": "tiempo", "segundos": 4,
                                       "siguiente": sig2}]}]}


def sim(resp):
    s = ls.SimV7()
    band = {"enable": True, "direction": "derecha", "freq_hz": 30, "stop_mode": 0,
            "engine": {"version": 7, "motor": resp["motor"], "secuencias": resp["secuencias"]}}
    with contextlib.redirect_stdout(io.StringIO()):
        err, campo = s.cargar(band)
    assert not err, f"PLC rechazo la config ({err}, {campo})"
    return s


def ciclos_ok(resp, n):
    s = sim(resp)
    marcha = lambda: s.motor() in ("dir1", "dir2")  # noqa: E731
    s.correr(0.5)
    s.poner(I1=True); s.correr(0.3); s.poner(I1=False); s.correr(2.5)
    if not marcha():
        return "no arranca con I1"
    for k in range(n):
        s.poner(S2=True); s.correr(0.3); s.poner(S2=False); s.correr(1.0)
        if marcha():
            return f"ciclo {k + 1}: no se detiene con S2"
        if "Q8" not in s.salidas():
            return f"ciclo {k + 1}: la pluma 1 no sube"
        s.correr(4.0)
        if k < n - 1 and not marcha():
            return f"ciclo {k + 1}: no vuelve a avanzar"
    s.correr(8)
    return "sigue avanzando al final" if marcha() else ""


escenario("1. el caso del banco: pasos contados desde 0")
malo = c9()
check(ciclos_ok(malo, 3) != "", "sin reparar la banda no hace los 3 ciclos (falla de r7a)")
r, av = rb.aplicar(malo, "")
pasos = r["secuencias"][0]["pasos"]
check([p.get("siguiente") for p in pasos] == [None, None], "destinos corregidos: el que sigue y fin del ciclo")
check(any("contados desde 0" in a for a in av), "avisa del ajuste")
check(ciclos_ok(r, 3) == "", "reparada: 3 ciclos (para, sube pluma 1, vuelve a avanzar) y se detiene")
r2, _ = rb.aplicar(c9(repeticiones=2), "")
check(ciclos_ok(r2, 2) == "", "con 2 repeticiones (turno 2): 2 ciclos y se detiene")

escenario("2. no toca secuencias bien numeradas")
for sig1, sig2 in ((None, None), (0, None), (None, "fin"), (2, None)):
    bien = c9(sig1=sig1, sig2=sig2)
    r, av = rb.aplicar(bien, "")
    check(not any("contados desde 0" in a for a in av), f"siguiente {sig1}/{sig2}: sin cambio de indices")
salto = {"motor": {}, "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
    {"salidas": ["Q3"], "avanza": "tiempo", "segundos": 2, "siguiente": 3},
    {"salidas": ["Q4"], "avanza": "tiempo", "segundos": 2},
    {"salidas": ["Q5"], "avanza": "tiempo", "segundos": 2, "siguiente": 1}]}]}
r, av = rb.aplicar(copy.deepcopy(salto), "")
check([p.get("siguiente") for p in r["secuencias"][0]["pasos"]] == [3, None, 1], "saltos base 1 validos se respetan")

escenario("3. decision con alternativa contada desde 0 (banco C1 r6a)")
dec = {"motor": {}, "secuencias": [{"id": "SQ1", "inicio": "I1", "modo": "una_vez", "pasos": [
    {"motor": ["marcha"], "avanza": "flanco", "senal": "S1"},
    {"motor": [], "avanza": "tiempo", "segundos": 0, "alternativa": {"senal": "CT1", "ir_a": 3}, "siguiente": 2},
    {"salidas": ["Q8"], "avanza": "tiempo", "segundos": 3, "siguiente": 0},
    {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5, "siguiente": "fin"}]}]}
r, av = rb.aplicar(dec, "")
p = r["secuencias"][0]["pasos"]
check(p[1]["siguiente"] is None and p[1]["alternativa"]["ir_a"] == 4, "decision: sigue al 3, con CT1 salta al 4")
check(p[2]["siguiente"] == 1 and p[3]["siguiente"] == "fin", "regreso al paso 1 y 'fin' intactos")

# ── C9 t3 (r8a, r6b): "la pluma 1 debe bajar cuando la banda vuelve a avanzar" -> el modelo
# cambio la subida (Q8) por la bajada (Q9) en vez de agregarla. Ahora se prueba simulando.
H9 = ["Al presionar I1 la banda avanza a 30 Hz hasta que el sensor 2 detecta una caja; entonces se detiene 4 "
      "segundos, la pluma 1 sube y la banda vuelve a avanzar. Esto se repite 3 veces y después la banda se detiene.",
      "En vez de 3 veces que sean 2.",
      "La pluma 1 debe bajar cuando la banda vuelve a avanzar."]


def cfg_banda(engine):
    return {"band": {"enable": True, "direction": "derecha", "freq_hz": 30, "stop_mode": 0,
                     "engine": {"version": 7, **engine}}}


_MOTOR9 = {"arranque": "I1", "paro": "C2", "modo": "enclavado", "pausa": "T1"}
_COND9 = [{"id": "C1", "op": "AND", "entradas": ["I5"], "flanco": "subida"},
          {"id": "C2", "op": "OR", "entradas": ["I3", "T2"], "flanco": "nivel"}]
# r8a t3 (fallo): la pluma solo baja; ya no sube en la pausa
MAL9 = cfg_banda({"motor": _MOTOR9, "condiciones": _COND9,
                  "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "C1", "segundos": 4.0},
                             {"id": "T2", "tipo": "RETARDO", "entrada": "CT1", "segundos": 6.0},
                             {"id": "T3", "tipo": "RETARDO", "entrada": "C1", "segundos": 4.0},
                             {"id": "T4", "tipo": "PULSO", "entrada": "T3", "segundos": 2.0}],
                  "contadores": [{"id": "CT1", "entrada": "C1", "preset": 2, "modo": "una_vez"}],
                  "salidas": {"Q9": "T4"}})
# r8b t3 (bien): SQ1 [Q8 4 s][Q9 3 s] por cada deteccion
BIEN9 = cfg_banda({"motor": _MOTOR9, "condiciones": _COND9,
                   "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "C1", "segundos": 4.0},
                              {"id": "T2", "tipo": "RETARDO", "entrada": "CT1", "segundos": 4.0}],
                   "contadores": [{"id": "CT1", "entrada": "C1", "reset": "I1", "preset": 2, "modo": "una_vez"}],
                   "secuencias": [{"id": "SQ1", "inicio": "C1", "modo": "una_vez", "exclusiva": True,
                                   "pausa_apaga": True, "pasos": [
                                       {"salidas": ["Q8"], "avanza": "tiempo", "segundos": 4, "motor": []},
                                       {"salidas": ["Q9"], "avanza": "tiempo", "segundos": 3, "motor": []}]}]})

escenario("4. plumas en la pausa por deteccion (C9 t3)")
reqs = co.requisitos(H9)
pl = [(r["pluma"], r["accion"], r["momento"], r["q"]) for r in reqs if r["tipo"] == "pluma_pausa"]
check(pl == [(1, "sube", "pausa", "Q8"), (1, "baja", "reanuda", "Q9")],
      "se leen las dos: sube en la pausa (turno 1) y baja al reanudar (turno 3)")
check(all(r.get("senal") == "S2" for r in reqs if r["tipo"] == "pluma_pausa"), "el sensor sale de la pausa (S2)")
check("VUELVE A AVANZAR" in co.bloque_modelo(reqs), "el modelo recibe que la bajada es ADEMAS de la subida")
f = co.verificar(MAL9, [r for r in reqs if r["tipo"] == "pluma_pausa"])
check(len(f) == 1 and "sube (Q8)" in f[0], "r8a t3: se detecta que la pluma ya no sube en la pausa")
check(co.verificar(BIEN9, [r for r in reqs if r["tipo"] == "pluma_pausa"]) == [], "r8b t3: cumple")
solo_t1 = [r for r in co.requisitos(H9[:1]) if r["tipo"] == "pluma_pausa"]
check(len(solo_t1) == 1 and solo_t1[0]["momento"] == "pausa", "turno 1 solo: solo la subida en la pausa")
check(not [r for r in co.requisitos(H9 + ["Ya no uses la pluma 1."]) if r["tipo"] == "pluma_pausa"],
      "'ya no uses la pluma 1' quita sus requisitos")
check(not [r for r in co.requisitos(["Cuando S1 detecte, la pluma 1 sube 3 s."]) if r["tipo"] == "pluma_pausa"],
      "sin pausa por deteccion no se agrega (no se adivina)")

# ── Parche sin el modelo: la vigente del turno 2 ya cumple todo menos la bajada.
escenario("5. parche de plumas sobre la vigente (sin el modelo)")
app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_r7.json")
app.BANDA_MOTOR_V7 = True
llamadas = []


def _llm(msgs):
    llamadas.append(msgs)
    raise AssertionError("no se debia llamar al modelo")


app.llamar_modelo_json = _llm
app.llamar_extractor_json = _llm

# r9a t2 (secuencia) y r9b t2 (timers + salidas): las dos formas que armo el modelo
VIG_SEQ = {"name": "C9 t2", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
           "motor": {"modo": "enclavado", "paro": "I3"},
           "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I3", "modo": "repetir", "repeticiones": 2,
                           "exclusiva": True, "pausa_apaga": True, "pasos": [
                               {"motor": ["marcha"], "avanza": "senal", "senal": "I5", "salidas": []},
                               {"motor": ["paro"], "salidas": ["Q8"], "avanza": "tiempo", "segundos": 4},
                               {"motor": ["marcha"], "avanza": "tiempo", "segundos": 0, "salidas": []}]}]}
VIG_TIM = {"name": "C9 t2", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
           "motor": {"arranque": "I1", "modo": "enclavado", "paro": "C3", "pausa": "T1"},
           "condiciones": [{"id": "C1", "op": "AND", "entradas": ["I5", "NOT CT1"], "flanco": "subida"},
                           {"id": "C2", "op": "AND", "entradas": ["I5", "CT1"], "flanco": "subida"},
                           {"id": "C3", "op": "OR", "entradas": ["I3", "C2"], "flanco": "nivel"},
                           {"id": "C4", "op": "OR", "entradas": ["T2", "T3"], "flanco": "nivel"}],
           "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "C1", "segundos": 4.0},
                      {"id": "T2", "tipo": "PULSO", "entrada": "C1", "segundos": 4.0},
                      {"id": "T3", "tipo": "PULSO", "entrada": "C2", "segundos": 4.0}],
           "contadores": [{"id": "CT1", "entrada": "I5", "reset": "I1", "preset": 2, "modo": "una_vez"}],
           "salidas": {"Q8": "C4"}}


def logic_de(resp):
    cfg = lm.cfg_desde_respuesta(resp)
    cfg["respuesta_llm"] = resp
    cfg["name"] = resp["name"]
    return cfg


def ciclos_c9(cfg, n=2, baja=False):
    """Como el evaluador del banco: n ciclos (para con S2, sube pluma 1, vuelve a avanzar
    y, si baja, la pluma 1 baja al reanudar) y al final queda detenida."""
    s = ls.SimV7()
    with contextlib.redirect_stdout(io.StringIO()):
        err, _ = s.cargar({"band": cfg["band"]})
    if err:
        return False
    corre = lambda: s.motor() in ("dir1", "dir2")  # noqa: E731
    s.correr(0.5)
    s.pulsar("I1")
    s.correr(2.5)
    if not corre():
        return False
    for k in range(n):
        s.pulsar("I5")
        vistos = []
        for _ in range(70):
            s.correr(0.1)
            vistos.append((corre(), "Q8" in s.salidas(), "Q9" in s.salidas()))
        if not any(not c for c, _, _ in vistos[:15]) or not any(q8 for _, q8, _ in vistos[:55]):
            return False
        if k < n - 1:
            if not any(c for c, _, _ in vistos[35:]):
                return False
            if baja and not any(q9 for _, _, q9 in vistos[35:]):
                return False
    s.correr(8)
    return not corre()


def generar(texto, vig):
    ctx = {"programa_anterior": {"metadata": {"name": vig["name"], "engine_config": logic_de(vig)}},
           "historial": H9[:2]}
    return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", contexto=ctx)))


for nombre, vig in (("secuencia", VIG_SEQ), ("timers", VIG_TIM)):
    check(ciclos_c9(logic_de(vig)), f"{nombre}: la vigente cumple el turno 2 (2 ciclos, pluma sube, para)")
    llamadas.clear()
    r = generar(H9[2], vig)
    check(r.status == "ok" and r.analysis.get("fuente") == "parche" and not llamadas,
          f"{nombre}: /generar-logica lo agrega sin el modelo ({r.status}, {r.analysis.get('fuente')})")
    check(ciclos_c9(r.logic, baja=True), f"{nombre}: 2 ciclos, la pluma 1 sube en la pausa, baja al reanudar y para")
    check(any("Agregado sin cambiar" in a for a in r.warnings or []), f"{nombre}: avisa lo agregado")

reqs = co.requisitos(H9)
cfg_s = lm.cfg_desde_respuesta(VIG_SEQ)
check(app._respuesta_parche_plumas("La pluma 1 debe bajar cuando la banda vuelve a avanzar y que vaya a 40 Hz.",
                                   VIG_SEQ, cfg_s, reqs, {"aplica": False}, {}) is None,
      "si la instruccion pide algo mas (40 Hz) no se parcha: lo resuelve el modelo")
check(pp.parchear(VIG_SEQ, [r for r in reqs if r["tipo"] == "pluma_pausa" and r["momento"] == "pausa"], cfg_s)
      is None, "si la vigente ya cumple lo de la pluma no hay nada que agregar")

print(f"\n{OK} comprobaciones OK")
