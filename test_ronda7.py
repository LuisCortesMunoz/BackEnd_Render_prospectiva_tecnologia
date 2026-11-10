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

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda7.py
"""

import contextlib
import copy
import io
import logging

logging.disable(logging.WARNING)

import lavo_sim as ls
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

print(f"\n{OK} comprobaciones OK")
