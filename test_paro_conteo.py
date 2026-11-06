"""
test_paro_conteo.py — "cuando el contador llegue a 3, la banda avanza 5 s y luego se detiene".

Corre sin Groq y sin PLC (ST v7 real en lavo_sim):
    python test_paro_conteo.py

Antes los 5 s se probaban desde el ARRANQUE: un programa correcto (cuenta 3,
sigue 5 s, se detiene) se rechazaba y uno incorrecto (para a los 5 s sin
contar) pasaba. Ahora:
  1. lectura: el paro va ligado al conteo con "se detiene sola", "se detiene",
     "se para" o "para" (verbo); no con un boton ni con "para que"
  2. simulacion: corre con N-1 objetos, sigue los X s desde el objeto N, se
     detiene y ya no arranca sola
  3. el requisito "conteo" acepta una accion que llega unos segundos despues
"""

import logging

logging.disable(logging.WARNING)

import comportamiento as co
import lavo_motor as lm

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


def paro(texto):
    return next((r for r in co.requisitos([texto]) if r["tipo"] == "para_sola"), None)


INICIO = "Al presionar I1 la banda avanza a 30 Hz. Cada vez que el sensor 2 detecta una caja se cuenta. "

# ---------------------------------------------------------------------------
escenario("1. Lectura: paro ligado al conteo")
casos = [
    ("Cuando el contador llegue a 3, la banda avanza 5 segundos y luego se detiene sola.", 3, 5.0),
    ("Cuando el contador llegue a 3, la banda avanza 5 segundos y luego se detiene.", 3, 5.0),
    ("Cuando el contador llegue a 3, la banda avanza 5 segundos y luego se para.", 3, 5.0),
    ("Cuando el contador llegue a 3, la banda sigue 4 segundos y para.", 3, 4.0),
    ("Al llegar a 3 cajas la banda se detiene.", 3, 0.0),
    ("Al contar 4 cajas la banda se para sola.", 4, 0.0),
    ("Al contar 3 cajas, la banda avanza 2 segundos más. Luego se detiene.", 3, 2.0),
    ("Una vez que acumule 3 detecciones, la banda continúa durante 6 segundos y luego para.", 3, 6.0),
]
for frase, n, seg in casos:
    r = paro(INICIO + frase)
    check(r is not None and r.get("conteo") == n and r.get("senal") == "S2" and r.get("segundos") == seg,
          f"«{frase}» -> conteo {n}, {seg:g} s ({r and {k: r.get(k) for k in ('conteo', 'segundos')}})")

for frase in ["Al contar 3 en S2 enciende la roja. La banda se detiene con I2.",
              "Al contar 3 cajas en S2 la pluma 1 se detiene.",
              "Cuenta 3 cajas en S2 para encender la roja.",
              "Al llegar a 3 cajas en S2 la banda sigue para que pasen las cajas.",
              "Al contar 3 cajas en S2 enciende la verde y con I2 se para la banda."]:
    r = paro(INICIO + frase)
    check(r is None or not r.get("conteo"), f"sin paro por conteo: «{frase}»")

check(paro(INICIO + "Al contar 3 cajas se detenga sola.")["conteo"] == 3, "lo de antes sigue: 'al contar 3 cajas se detenga sola'")
r = co.requisitos([INICIO + "Cuando el contador llegue a 3, la banda avanza 5 segundos y luego se detiene sola.",
                   "no se detiene sola, revisa"])
p = next(x for x in r if x["tipo"] == "para_sola")
check(p.get("conteo") == 3 and p.get("segundos") == 5.0, "una correccion sin condicion conserva el paro por conteo")
r = co.requisitos([INICIO + "Al contar 3 cajas la banda se detiene.", "ya no quiero que se detenga sola"])
check(not any(x["tipo"] == "para_sola" for x in r), "'ya no se detenga sola' lo quita")

# ---------------------------------------------------------------------------
escenario("2. Simulacion: los segundos se cuentan desde el objeto N")
A = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [], "simplificado": [],
     "no_soportado": [], "requiere_confirmacion": False}


def prog(pasos):
    return lm.cfg_desde_respuesta({
        "name": "t", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
        "contadores": [{"id": "CT1", "entrada": "S2", "preset": 3, "modo": "una_vez", "reset": "SQ1_FIN"}],
        "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I2", "modo": "una_vez", "pasos": pasos}],
        "analisis": A, "preguntas": []})


CORRECTO = prog([{"motor": ["marcha"], "avanza": "flanco", "senal": "CT1"},
                 {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5},
                 {"motor": [], "avanza": "tiempo", "segundos": 0.5}])
SIN_CONTAR = prog([{"motor": ["marcha"], "avanza": "tiempo", "segundos": 5},
                   {"motor": [], "avanza": "tiempo", "segundos": 0.5}])
DE_GOLPE = prog([{"motor": ["marcha"], "avanza": "flanco", "senal": "CT1"},
                 {"motor": [], "avanza": "tiempo", "segundos": 0.5}])

T5 = INICIO + "Cuando el contador llegue a 3, la banda avanza 5 segundos y luego se detiene sola."
reqs = co.requisitos([T5])
check([x["tipo"] for x in reqs] == ["arranca_con", "para_sola"],
      f"requisitos: arranque + paro por conteo (el conteo lo prueba el paro) {[x['tipo'] for x in reqs]}")
f = co.verificar(CORRECTO, reqs)
check(f == [], f"el programa CORRECTO (cuenta 3, sigue 5 s, se detiene) cumple ({f})")
f = co.verificar(SIN_CONTAR, reqs)
check(f and ("antes de contar 3" in f[0] or "enseguida" in f[0]),
      f"el que para a los 5 s de arrancar SIN contar falla ({f[:1]})")
f = co.verificar(DE_GOLPE, reqs)
check(f and "enseguida" in f[0], f"el que para al instante en la caja 3 falla ({f[:1]})")
POR_TIEMPO = prog([{"motor": ["marcha"], "avanza": "tiempo", "segundos": 9},
                   {"motor": [], "avanza": "tiempo", "segundos": 0.5}])
f = co.verificar(POR_TIEMPO, reqs)
check(f and "por tiempo" in f[0], f"el que para a los 9 s de arrancar (cae en la ventana) falla por el control ({f[:1]})")

T0 = INICIO + "Al llegar a 3 cajas la banda se detiene."
f = co.verificar(DE_GOLPE, co.requisitos([T0]))
check(f == [], f"'al llegar a 3 se detiene' (sin tiempo): parar en la caja 3 cumple ({f})")
f = co.verificar(CORRECTO, co.requisitos([T0]))
check(f and "NO se detiene sola" in f[0], f"... y seguir 5 s mas no ({f[:1]})")

# ---------------------------------------------------------------------------
escenario("3. Conteo: la accion puede llegar unos segundos despues")
r = {"tipo": "conteo", "conteo": 3, "senal": "S2", "reinicia": False, "boton": None, "texto": ""}
f = co.verificar(CORRECTO, [{"tipo": "arranca_con", "senal": "I1", "texto": ""}, r])
check(f == [], f"al contar 3 la banda se detiene 5 s despues: el conteo SI hace algo ({f})")
f = co.verificar(SIN_CONTAR, [r])
check(f and "no pasa nada" in f[0], f"si nunca pasa nada sigue fallando ({f[:1]})")

print(f"\nTODAS LAS PRUEBAS DE PARO POR CONTEO PASARON ({OK} comprobaciones).")
