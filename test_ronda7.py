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

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda7.py
"""

import contextlib
import copy
import io
import logging

logging.disable(logging.WARNING)

import lavo_sim as ls
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

print(f"\n{OK} comprobaciones OK")
