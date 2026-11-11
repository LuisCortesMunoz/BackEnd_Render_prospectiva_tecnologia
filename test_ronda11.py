"""
test_ronda11.py — cambios de valor DICHOS en los lentes (11 nov).

Caso real: "al llegar a 3 la banda avanza 5 s y se detiene" y luego, por voz, "cambia
los 5 segundos a 6". En la web se escribe "por 6 segundos"; hablado la unidad no se
repite y la transcripcion puede traer los numeros con palabras ("cinco", "seis"):
  1. cambios_texto no veia el cambio -> no habia edicion directa y el requisito seguia
     en 5 s
  2. cobertura tomaba el "5 segundos" del mensaje como algo PEDIDO: a la logica de 6 s
     le "faltaba 5 s" y se le pedia al modelo agregarlo (la secuencia de mas)
  3. lo mismo con Hz ("cambia los 30 Hz a 45" tomaba 30) y con el conteo ("cambia el
     contador de 3 a 5" tomaba 3)

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda11.py
"""

import asyncio
import contextlib
import copy
import io
import logging
import os
import re
import tempfile
import unicodedata

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_r11_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_r11.json")
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

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_r11.json")
app.BANDA_MOTOR_V7 = True
OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


def voz(t: str) -> str:
    """Como puede llegar de la transcripcion: sin acentos ni puntuacion."""
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[.,;:!?¿¡]", "", t)


def cfg(resp):
    with contextlib.redirect_stdout(io.StringIO()):
        c = lm.cfg_desde_respuesta(copy.deepcopy(resp))
    c["respuesta_llm"] = copy.deepcopy(resp)
    return c


def tiempos(c):
    return sorted(round(x[2], 2) for x in ediciones._tiempos(c["respuesta_llm"]))


# Banco C3 t1 (r13a): al contar 3 cajas en S2, la banda avanza 5 s y se detiene.
T_C3 = ("Que al presionar I1 la banda avance a 30 Hz, que el sensor 2 cuente las cajas y al llegar a un contador "
        "de 3 la banda avance 5 segundos y luego se detenga.")
C3 = {"name": "Conteo 3 en S2 y paro", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
      "motor": {"arranque": "I1", "modo": "enclavado", "paro": "SQ1_CICLO"}, "condiciones": [], "timers": [],
      "memorias": [], "eventos": [],
      "contadores": [{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez", "reset": "SQ1_CICLO"}],
      "salidas": {},
      "secuencias": [{"id": "SQ1", "inicio": "CT1", "modo": "una_vez", "exclusiva": True,
                      "pasos": [{"motor": ["marcha"], "avanza": "tiempo", "segundos": 5.0},
                                {"motor": ["paro"], "avanza": "tiempo", "segundos": 0.5}]}]}
C3_6 = copy.deepcopy(C3)
C3_6["secuencias"][0]["pasos"][0]["segundos"] = 6.0

# Como se dice el cambio de 5 a 6 s (escrito y hablado).
CAMBIOS_5_6 = [
    "Cambia los 5 segundos por 6 segundos.",
    "Cambia los 5 segundos a 6.",
    "Cambia los cinco segundos a seis.",
    "Cambia los cinco segundos a seis segundos.",
    "Cambia los 5 segundos que avanza a 6 segundos.",
    "Que en lugar de 5 segundos avance 6 segundos.",
    "Que avance 6 segundos en vez de 5.",
    "En vez de 5 segundos, que sean 6.",
    "Cambia el tiempo de cinco a seis segundos.",
    "Que avance 6 segundos, no 5.",
    "No 5 segundos sino 6.",
    "Cambia los 5 segundos a 6, por favor.",
    voz("Cambia los cinco segundos a seis."),
    voz("Cambia los 5 segundos que avanza la banda a 6."),
]

# ── 1. numeros dichos con palabras ─────────────────────────────────────────
escenario("1. corregir pasa a digitos los numeros que son un dato (y nada mas)")
for dicho, esperado in [
    ("Cambia los cinco segundos a seis.", "Cambia los 5 segundos a 6."),
    ("Cambia los treinta hertz a cuarenta y cinco.", "Cambia los 30 hertz a 45."),
    ("Cuando el sensor dos detecte, espera tres segundos.", "Cuando el sensor 2 detecte, espera 3 segundos."),
    ("Cambia el contador de tres a cinco.", "Cambia el contador de 3 a 5."),
    ("Que la pluma uno suba un segundo.", "Que la pluma 1 suba 1 segundo."),
    ("En la secuencia dos, la roja dura dieciséis segundos.", "En la secuencia 2, la roja dura 16 segundos."),
    ("Que cuente cinco cajas en vez de tres.", "Que cuente 5 cajas en vez de 3."),
    # lo que NO es un dato se queda igual
    ("Sube las dos plumas.", "Sube las dos plumas."),
    ("Una vez que el sensor detecte, baja las dos plumas a la vez.",
     "Una vez que el sensor detecte, baja las dos plumas a la vez."),
    ("Al llegar a un contador de tres la banda se detiene.", "Al llegar a un contador de tres la banda se detiene."),
    ("Cambia los 5 segundos por 6 segundos.", "Cambia los 5 segundos por 6 segundos."),
]:
    check(ct.corregir(dicho) == esperado, f"«{dicho}» -> «{ct.corregir(dicho)}»")

# ── 2. sustituciones de tiempo, Hz y conteo ───────────────────────────────
escenario("2. el cambio de 5 a 6 s se reconoce como se diga")
for f in CAMBIOS_5_6:
    check(ct.sustituciones(f)["t"] == [(5.0, 6.0)], f"«{f}» -> 5 s -> 6 s")
for f in ["Cambia la velocidad a 45 Hz y la espera a 5 segundos.", "Cambia los 5 segundos a 6 veces.",
          "Cuando el sensor 1 detecte, en lugar de 5 segundos que la pluma 2 suba.", "Que avance 6 segundos."]:
    check(ct.sustituciones(f)["t"] == [], f"«{f}» no es un cambio de 5 a 6 s")
for f in ["Cambia los 30 Hz a 45.", "Cambia los treinta hertz a cuarenta y cinco.",
          "Cambia la velocidad de 30 a 45 Hz.", "Que vaya a 45 Hz en lugar de 30."]:
    check(ct.sustituciones(f)["hz"] == [(30.0, 45.0)], f"«{f}» -> 30 Hz -> 45 Hz")
for f, n in [("Cambia el contador de 3 a 5.", 5), ("Cambia el contador de tres a cinco.", 5),
             ("Cambia las 3 cajas a 5.", 5), ("Cambia el conteo a 5 cajas en lugar de 3.", 5),
             ("Cambia el conteo a cinco.", 5)]:
    check(co.cambio_conteo(f) == n, f"«{f}» -> conteo {n}")
for f in ["Cambia el tiempo a 6 segundos.", "Cámbialo a seis segundos.", "Mejor que sean seis segundos.",
          "Ahora que avance 6 segundos y luego se detenga."]:
    tn = ct.tiempo_nuevo(f)
    check(tn is not None and tn[1] == 6.0, f"«{f}» -> tiempo nuevo 6 s ({tn})")

# ── 3. el requisito de la sesion pasa a 6 s ───────────────────────────────
escenario("3. el requisito 'avanza N s y se detiene' sigue al cambio")
for f in CAMBIOS_5_6 + ["Cambia el tiempo a 6 segundos.", "Mejor que sean seis segundos.",
                        "Ahora que avance 6 segundos y luego se detenga."]:
    r = co.requisitos([ct.corregir(T_C3), ct.corregir(f)])
    s = [x.get("segundos") for x in r if x["tipo"] == "para_sola"]
    check(s == [6.0], f"«{f}» -> para_sola {s}")

# ── 4. cobertura: no exige el valor viejo y si el nuevo ───────────────────
escenario("4. cobertura: el 5 de 'cambia los 5 segundos a 6' es el viejo")
for f in CAMBIOS_5_6:
    check(cobertura.faltantes(f, cfg(C3_6)) == [], f"«{f}»: la logica de 6 s tiene todo")
    check(cobertura.faltantes(f, cfg(C3)) == ["6 s"], f"«{f}»: a la de 5 s le falta 6 s")
check("5 s" not in cobertura.faltantes("Cambia los 5 segundos de la pluma 1 a 6 segundos.", cfg(C3_6)),
      "forma que cambios_texto no lee ('de la pluma 1'): el 5 tampoco se exige")
C3_45 = copy.deepcopy(C3)
C3_45["banda"]["frecuencia_hz"] = 45
for f in ["Cambia los 30 Hz a 45.", "Cambia los treinta hertz a cuarenta y cinco."]:
    check(cobertura.faltantes(f, cfg(C3_45)) == [] and cobertura.faltantes(f, cfg(C3)) == ["45 Hz"],
          f"«{f}»: exige 45 Hz, no 30 Hz")
check(cobertura.faltantes("Cambia la velocidad a 45 Hz y la espera a 5 segundos.", cfg(C3_45)) == [],
      "dos datos nuevos en la misma frase siguen exigiendose (45 Hz y 5 s estan)")

# ── 5. de punta a punta: edicion directa, sin el modelo ───────────────────
escenario("5. de punta a punta (como lo manda Unity): el cambio se aplica sin llamar al modelo")
llamadas = []
respuesta = {"r": None}


def _llm(messages, *a, **k):
    llamadas.append(messages)
    if respuesta["r"] is None:
        raise AssertionError("no se debia llamar al modelo")
    return copy.deepcopy(respuesta["r"])


app.llamar_modelo_json = _llm
app.llamar_extractor_json = lambda m: (_ for _ in ()).throw(ValueError("sin extractor"))


def pedir(texto, vigente=C3, hist=(T_C3,), resp_modelo=None):
    llamadas.clear()
    respuesta["r"] = resp_modelo
    # Lo que manda Unity: el "program" de la respuesta anterior tal cual.
    ctx = {"programa_anterior": {"metadata": {"name": "C3", "engine_config": cfg(vigente)}},
           "historial": list(hist)}
    with contextlib.redirect_stdout(io.StringIO()):
        return asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto, device="banda", contexto=ctx)))


# "Con 6 segundos." = lo que Unity manda de "regresala como estaba pero con 6 segundos".
for f in CAMBIOS_5_6 + ["Que sean 6 segundos.", "Cambia el tiempo a 6 segundos.", "Cámbialo a seis segundos.",
                        "Con 6 segundos."]:
    res = pedir(f)
    t = tiempos(res.logic)
    check(res.status == "ok" and res.analysis.get("fuente") == "edicion" and not llamadas and 6.0 in t
          and 5.0 not in t, f"«{f}»: edicion directa, 0 llamadas, tiempos {t}")

# El modelo lo hace bien a la primera: ya no se le pide "agregar 5 s".
res = pedir("Ahora que avance 6 segundos y luego se detenga.", resp_modelo=C3_6)
check(res.status == "ok" and len(llamadas) == 1 and 6.0 in tiempos(res.logic)
      and not any("No encontré" in w for w in res.warnings),
      f"'Ahora que avance 6 segundos...': el modelo a la primera, sin pedirle el 5 ({len(llamadas)} llamadas)")
llamadas_msgs = " ".join(str(m) for m in llamadas)
check("NO incluye" not in llamadas_msgs, "no se le dijo al modelo que faltaba algo")

# Hz y conteo hablados
res = pedir("Cambia los treinta hertz a cuarenta y cinco.")
check(res.status == "ok" and res.analysis.get("fuente") == "edicion" and not llamadas
      and res.logic["band"]["freq_hz"] == 45, f"'Cambia los treinta hertz a cuarenta y cinco' -> 45 Hz sin el modelo")
res = pedir("Cambia el contador de tres a cinco.")
pre = [c.get("preset") for c in res.logic["band"]["engine"].get("contadores") or []]
check(res.status == "ok" and not llamadas and pre == [5], f"'Cambia el contador de tres a cinco' -> preset {pre}")

# Despues del cambio, la siguiente instruccion parte de 6 s (Unity manda la respuesta como vigente).
res = pedir("Cambia los 6 segundos a 4.", vigente=C3_6, hist=(T_C3, "Cambia los 5 segundos a 6."))
t = tiempos(res.logic)
check(res.status == "ok" and not llamadas and 4.0 in t and 6.0 not in t, f"segundo cambio hablado (6 -> 4): {t}")

print(f"\nTODAS LAS PRUEBAS DE LA RONDA 11 PASARON ({OK} comprobaciones).")
