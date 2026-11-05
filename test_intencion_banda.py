"""
test_intencion_banda.py — la intencion comun de la banda (etapa 2).

Rapida, sin Groq ni simulador:
    python test_intencion_banda.py

intencion_banda.analizar junta lo que entienden preguntas_banda, etapas_orden y
comportamiento. No debe cambiar lo que se le pide al modelo: bloques_modelo
devuelve exactamente los mismos mensajes que antes armaba app.py.
"""

import logging
logging.disable(logging.WARNING)

import comportamiento as co
import etapas_orden as eo
import intencion_banda as ib

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1
    print(f"  ok  {msg}")


def escenario(nombre):
    print(f"\n== {nombre}")


TOGGLE = ("Al presionar el botón verde, la banda comienza a avanzar y, al presionarlo por segunda vez, se "
          "detiene")
CONTEO = ("Con I1 la banda avanza a 20 Hz. Cuando S2 cuente 3 cajas prende la roja 5 segundos; el contador se "
          "reinicia solo y tambien con I2")
LARGA = ("Al presionar el pulsador I1, la banda transportadora se pone en marcha a una frecuencia de 20 Hz. "
         "Cuando el Sensor 1 detecta un objeto, la pluma 1 se eleva y la pluma 2 desciende. Posteriormente, al "
         "activarse el Sensor 2, la pluma 2 se eleva y la pluma 1 desciende, registrando un conteo. Una vez que "
         "el Sensor 2 acumula 3 detecciones, la banda continúa avanzando durante 5 segundos y luego se detiene "
         "automáticamente; a continuación, la pluma 1 se eleva, la pluma 2 desciende y el contador se reinicia "
         "a cero en ese orden.")

# ---------------------------------------------------------------------------
escenario("1. Lo que se entiende de cada instruccion, en un solo formato")
i = ib.analizar(TOGGLE)
check(i["modulos"] == ["motor"] and i["motor"]["arranque"] == "I1" and i["motor"]["alternancia"] == "I1",
      "boton verde: motor con arranque y alternancia en I1")
i = ib.analizar(CONTEO)
check(i["modulos"] == ["motor", "conteo"] and i["motor"]["hz"] == 20, "conteo suelto: motor (20 Hz) y conteo")
check(i["conteos"] == [{"senal": "S2", "n": 3, "reinicia": True, "boton": "I2", "en_secuencia": False}],
      "el conteo: S2, 3, se reinicia solo y con I2")
check("I2 detiene la banda y tambien borra la cuenta." not in i["conflictos"], "I2 no es paro aqui: sin conflicto")
i = ib.analizar(LARGA)
check(i["modulos"] == ["motor", "conteo", "secuencias"], "secuencia larga: motor, conteo y secuencias")
check(i["motor"]["arranque"] == "I1", "el arranque sale de la primera etapa")
check(len(i["conteos"]) == 1 and i["conteos"][0]["en_secuencia"] and i["conteos"][0]["n"] == 3,
      "el conteo de la secuencia una sola vez (no repetido como suelto)")
check(i["secuencias"] == [{"n": 1, "etapas": [1, 2, 3, 4, 5, 6], "repite": False, "veces": None, "conteo": True}],
      "una secuencia de 6 etapas con conteo")
check(not any(r["tipo"] in ("para_sola", "conteo") for r in i["requisitos"]),
      "en una secuencia el paro solo y el conteo los prueba la simulacion de etapas")
i = ib.analizar("Quiero mover la banda")
check([q["campo"] for q in i["faltan"]] == ["arranque", "direccion", "freq_hz", "paro"], "movimiento: lo que falta")

# ---------------------------------------------------------------------------
escenario("2. Conflictos que se ven antes de generar (solo se registran)")
check(ib.analizar("Con I1 la banda arranca y con I1 se detiene")["conflictos"]
      == ["I1 arranca y detiene la banda sin pedir alternancia."], "mismo boton arranca y para sin alternancia")
check(ib.analizar(TOGGLE)["conflictos"] == [], "con alternancia no es conflicto")
c = ib.analizar("Con I1 la banda avanza y con I2 se detiene. Cuando S2 cuente 3 cajas prende la roja y el "
                "contador se reinicia con I2")["conflictos"]
check(c == ["I2 detiene la banda y tambien borra la cuenta."], f"reset del contador = paro de la banda ({c})")
c = ib.analizar("Con I1 la banda avanza. Cuando S2 cuente 3 cajas prende la roja y se reinicia con I3")["conflictos"]
check(any("I3" in x for x in c), "reset con I3 (paro de emergencia)")

# ---------------------------------------------------------------------------
escenario("3. Al modelo le llegan los mismos bloques que antes")
for t in (TOGGLE, CONTEO, LARGA, "Con I1 la banda avanza y se detiene sola a los 10 s"):
    i = ib.analizar(t)
    antes = []
    if i["etapas"]["aplica"]:
        antes.append({"role": "system", "content": eo.bloque_modelo(i["etapas"], "banda")})
    if i["requisitos"]:
        antes.append({"role": "system", "content": co.bloque_modelo(i["requisitos"])})
    check(ib.bloques_modelo(i) == antes, f"mismos bloques: '{t[:40]}...'")
check(ib.resumen(ib.analizar(LARGA))["pruebas"] == ["arranca_con I1"],
      "resumen corto para analysis ('pulsador I1' ya se prueba como arranque)")

print(f"\nTODAS LAS PRUEBAS DE INTENCION DE LA BANDA PASARON ({OK} comprobaciones).")
