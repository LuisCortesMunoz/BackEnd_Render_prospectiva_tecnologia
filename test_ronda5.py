"""
test_ronda5.py — prueba final del usuario (banco C12), tal cual la escribio:

  "Quiero avanzar la banda con i1 a 40 hz a la derecha. Cada que el sensor uno
   detecte se espere 3 segundos antes de avanzar. Aparte quiero lograr que cuando
   el temporizador uno llegue a un contador de 3, se haga la secuencia de que
   avance la banda durante 5 segundos, se detenga por sis ola y reinicie el
   contador para reiniciar el ciclo esperando a que el susaurio presione el botn i1"

Render devolvio una logica que nunca detiene la banda y el sistema no vio NINGUN
requisito. Ahora:
  1. se leen: arranque con I1, pausa de 3 s por S1 ("se espere ... antes de
     avanzar"), conteo de 3 ("llegue a un contador de 3"), paro tras 5 s ("por
     sis ola" = por si sola) y nuevo ciclo con I1
  2. la logica de Render falla la simulacion (y una correcta la pasa)
  3. la secuencia de un solo paso de marcha que debia terminar detenida se
     repara (paso de paro + paro con SQ1_CICLO)
  4. la receta fija de conteo no se usa con pausas por deteccion

Corre sin Groq y sin PLC (lavo_sim):  python test_ronda5.py
"""

import logging
logging.disable(logging.WARNING)

import comportamiento as co
import lavo_motor as lm
import reparaciones_banda as rb
import receta_conteo as rc

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
A = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [], "simplificado": [],
     "no_soportado": [], "requiere_confirmacion": False}
# Lo que devolvio Render (6 nov, 88e2055)
RENDER = {"name": "c12", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 40},
          "motor": {"arranque": "I1", "modo": "enclavado"},
          "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "I4", "segundos": 3.0}],
          "contadores": [{"id": "CT1", "entrada": "T1", "reset": "SQ1_CICLO", "preset": 3, "modo": "una_vez"}],
          "secuencias": [{"id": "SQ1", "inicio": "CT1", "reset": "I1", "modo": "una_vez", "exclusiva": True,
                          "pasos": [{"salidas": [], "motor": ["marcha"], "avanza": "tiempo", "segundos": 5,
                                     "siguiente": "fin"}]}],
          "analisis": A, "preguntas": []}
BIEN = {"name": "c12", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 40},
        "motor": {"arranque": "I1", "modo": "enclavado", "paro": "SQ1_CICLO"},
        "eventos": [{"cuando": "S1", "duracion_s": 3, "acciones": {"banda": "pausar"}}],
        "contadores": [{"id": "CT1", "entrada": "S1", "preset": 3, "modo": "una_vez", "reset": "SQ1_CICLO"}],
        "secuencias": [{"id": "SQ1", "inicio": "CT1", "modo": "una_vez", "exclusiva": True, "pasos": [
            {"motor": ["marcha"], "avanza": "tiempo", "segundos": 5},
            {"motor": ["paro"], "avanza": "tiempo", "segundos": 0.5}]}],
        "analisis": A, "preguntas": []}

# ---------------------------------------------------------------------------
escenario("1. Lectura del texto tal cual")
check(co._fold("se detenga por sis ola") == "se detenga por si sola", "'por sis ola' -> 'por si sola'")
check(co.pausa_por_deteccion(T) == {"senal": "S1", "segundos": 3.0},
      "'cada que el sensor uno detecte se espere 3 s antes de avanzar' -> pausa de 3 s por S1")
check(co.pausa_por_deteccion("Cuando el sensor 2 detecte, espera 2 segundos y luego continua.") ==
      {"senal": "S2", "segundos": 2.0}, "'espera 2 s y luego continua' tambien")
check(co.conteo_en(T)["conteo"] == 3, "'llegue a un contador de 3' -> 3 (el 'un' no es el numero)")
check(co.conteo_en("cuando el sensor 2 llegue a un conteo de 4 cajas enciende la roja")["conteo"] == 4,
      "'llegue a un conteo de 4' -> 4")
pc = co.paro_por_conteo(T)
check(pc == {"conteo": 3, "senal": "S1", "segundos": 5.0, "rearranque": True},
      f"paro por conteo: 3 en S1, sigue 5 s, nuevo ciclo ({pc})")
reqs = co.requisitos([T])
tipos = {r["tipo"]: r for r in reqs}
check(tipos.get("arranca_con", {}).get("senal") == "I1", "'avanzar la banda con i1' -> arranca con I1")
check("pausa_sensor" in tipos and tipos["para_sola"].get("pausa_s") == 3.0,
      "la prueba del paro por conteo sabe que S1 tambien pausa 3 s")
check("conteo" not in tipos, "sin la prueba suelta del conteo (veria la banda parada en cada caja)")

# ---------------------------------------------------------------------------
escenario("2. Simulacion: la de Render falla, una correcta pasa")
f = co.verificar(lm.cfg_desde_respuesta(RENDER), reqs)
check(any("NO se detiene sola" in x for x in f) and any("NO se detiene 3 s" in x for x in f),
      f"la de Render: no se detiene tras 5 s y no pausa en S1 ({len(f)} fallas)")
check(co.verificar(lm.cfg_desde_respuesta(BIEN), reqs) == [], "la correcta (pausa por evento + SQ1 + SQ1_CICLO): cumple")

# ---------------------------------------------------------------------------
escenario("3. Reparacion: secuencia de un paso de marcha que debia terminar detenida")
r2, av = rb.aplicar(RENDER, T, [T])
pasos = r2["secuencias"][0]["pasos"]
check(len(pasos) == 2 and pasos[1]["motor"] == ["paro"] and "siguiente" not in pasos[0],
      "se agrega el paso de paro al final")
check(r2["motor"].get("paro") == "SQ1_CICLO", "y el paro del motor con SQ1_CICLO (no vuelve a avanzar sola)")
check(any("se agregó el paso de paro" in a for a in av), "con su aviso")
NO_PIDE = {"name": "x", "banda": {"mover": True}, "motor": {"arranque": "I1", "paro": "I2"},
           "secuencias": [{"id": "SQ1", "inicio": "S1", "pasos": [
               {"salidas": ["Q8"], "motor": ["marcha"], "avanza": "tiempo", "segundos": 3}]}]}
texto2 = "Con I1 arranca y con I2 se detiene. Cuando el sensor 1 detecta la pluma 1 sube 3 segundos."
check(rb.aplicar(NO_PIDE, texto2, [texto2])[0]["secuencias"][0]["pasos"] == NO_PIDE["secuencias"][0]["pasos"],
      "si el texto no pide que se detenga sola (solo 'con I2 se detiene'), no se toca")

# ---------------------------------------------------------------------------
escenario("4. La receta fija de conteo no se usa con pausas")
check(rc.detectar([T], {}) is None, "con 'se espere 3 s' por S1: no aplica la receta (la arma el modelo)")

print(f"\nTODAS LAS PRUEBAS DE LA RONDA 5 PASARON ({OK} comprobaciones).")
