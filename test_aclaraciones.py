"""
test_aclaraciones.py — las respuestas a las preguntas viajan aparte del texto.

Corre sin Groq (modelo simulado) y sin PLC:
    python test_aclaraciones.py

El texto de la instruccion llega intacto; las opciones elegidas llegan en
LogicaRequest.aclaraciones, el modelo las recibe como datos ya decididos y no
se vuelve a preguntar lo que ya se respondio.
"""

import asyncio
import copy
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_acl_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_acl.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import aclaraciones as ac
import app

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_acl.json")
app.BANDA_MOTOR_V7 = True
app.BANDA_MOTOR_CONFIGURABLE = False

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1


def escenario(nombre):
    print(f"\n== {nombre}")


respuestas, llamadas = [], []


def _llm(msgs):
    llamadas.append(copy.deepcopy(msgs))
    if not respuestas:
        raise AssertionError("el modelo fue llamado sin respuesta preparada")
    return copy.deepcopy(respuestas.pop(0))


app.llamar_modelo_json = _llm


def generar(texto, *resp, device="banda", acl=None, contexto=None):
    respuestas[:] = list(resp)
    llamadas.clear()
    req = app.LogicaRequest(texto=texto, device=device, device_profile="maletin_basico",
                            aclaraciones=acl, contexto=contexto)
    return asyncio.run(app.generar_logica(req))


def ultimo_usuario(i=-1):
    return [m["content"] for m in llamadas[i] if m["role"] == "user"][-1]


def mueve(freq=None, extra=None, preguntas=None):
    d = {"name": "Mueve derecha", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": freq},
         "motor": {"arranque": "I1"}, "analisis": {"compatibilidad": "total"}, "preguntas": preguntas or []}
    d.update(extra or {})
    return d


# Trae arranque y paro: solo falta la frecuencia (el resto lo integra el modelo).
TEXTO = "Mueve la banda hacia la derecha con I1 hasta presionar I2"

# ---------------------------------------------------------------------------
escenario("1. Movimiento sin frecuencia: se pregunta y se responde 20 Hz")
r = generar(TEXTO, mueve(None))
check(r.status == "needs_clarification" and ac.clave_pregunta(r.questions[0]) == "freq_hz", "pregunta la frecuencia")
acl = {"freq_hz": {"pregunta": r.questions[0]["pregunta"], "respuesta": "20 Hz"}}
r = generar(TEXTO, mueve(None), acl=acl)                    # el modelo vuelve a dejarla vacia
u = ultimo_usuario()
check(u.startswith(TEXTO + "\n\n"), "el texto original llega intacto, sin '20 Hz' pegado")
check("DATOS QUE EL USUARIO YA ACLARO" in u and "freq_hz: 20 Hz" in u, "el modelo recibe la frecuencia aparte")
check(r.status == "ok" and r.logic["band"]["freq_hz"] == 20, "el JSON final lleva 20 Hz")
check(r.logic["respuesta_llm"]["banda"]["frecuencia_hz"] == 20, "tambien en el JSON del modelo (base de ediciones)")

# ---------------------------------------------------------------------------
escenario("2. Falta el tiempo: 5 segundos")
T2 = "Cuando el sensor 1 detecte una pieza, espera y baja la pluma 1"
preg_t = [{"campo": "espera_s", "pregunta": "¿Cuántos segundos espera?", "opciones": ["3 s", "5 s"]}]
r = generar(T2, mueve(30, preguntas=preg_t))
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "espera_s", "el modelo pregunta el tiempo")
acl_t = {"espera_s": {"pregunta": "¿Cuántos segundos espera?", "respuesta": "5 s"}}
evento = {"eventos": [{"cuando": "S1", "espera_s": 5, "acciones": {"pluma1": "bajar"}}]}
r = generar(T2, mueve(30, preguntas=preg_t), mueve(30, extra=evento), acl=acl_t)
check(len(llamadas) == 2 and "ya estan respondidas" in ultimo_usuario(),
      "si vuelve a preguntar lo respondido, se le pide generar (no se pregunta al usuario)")
check(r.status == "ok" and r.logic["respuesta_llm"]["eventos"][0]["espera_s"] == 5, "el JSON lleva la espera de 5 s")
check("espera_s: 5 s" in ultimo_usuario(0), "la espera llego como aclaracion")

# ---------------------------------------------------------------------------
escenario("3. Dos parametros faltantes (maletin: salida y entrada)")
T3 = "Prende una lampara"
r = generar(T3, device="maletin")
claves = sorted(ac.clave_pregunta(q) for q in r.questions)
check(r.status == "needs_clarification" and claves == ["input", "output"], "pregunta salida y entrada")
r = generar(T3, device="maletin", acl={"output": {"pregunta": "¿Cuál salida?", "respuesta": "Q10 (verde)"}})
check(r.status == "needs_clarification" and [ac.clave_pregunta(q) for q in r.questions] == ["input"],
      "con la salida respondida solo pregunta la entrada")
acl3 = {"output": {"respuesta": "Q10 (verde)"}, "input": {"respuesta": "I1 (boton verde)"}}
cfg_m = {"name": "Verde con I1", "device": "maletin", "system": {},
         "outputs": [{"output": "Q10", "logic": {"mode": "directo", "source": "I1"}}]}
r = generar(T3, cfg_m, device="maletin", acl=acl3)
u = ultimo_usuario()
check(r.status == "ok" and len(llamadas) == 1, "con las dos respuestas se genera")
check(u.startswith(T3 + "\n\n") and "output: Q10 (verde)" in u and "input: I1 (boton verde)" in u,
      "el modelo recibe las dos respuestas, el texto intacto")
check(r.logic["outputs"][0]["output"] == "Q10", "JSON con la salida elegida")

# ---------------------------------------------------------------------------
escenario("4. Cambiar una respuesta ya elegida (20 -> 30 Hz)")
r = generar(TEXTO, mueve(20), acl={"freq_hz": {"respuesta": "30 Hz"}})
check(r.status == "ok" and r.logic["band"]["freq_hz"] == 30, "manda la respuesta nueva, no la del modelo")

# ---------------------------------------------------------------------------
escenario("5. Aclaracion de una modificacion (aumenta la velocidad -> 35 Hz)")
S1 = {"cuando": "S1", "duracion_s": 3, "acciones": {"banda": "pausar"}}
base = generar("Con I1 la banda avanza a 20 Hz y S1 la pausa 3 s", mueve(20, extra={"eventos": [S1]}))
ctx = app.ContextoLadder(programa_anterior={"metadata": {"engine_config": base.logic}},
                         historial=["Con I1 la banda avanza a 20 Hz y S1 la pausa 3 s"])
preg_f = [{"campo": "frecuencia_hz", "pregunta": "¿A qué frecuencia deseas aumentar?", "opciones": ["30 Hz", "35 Hz"]}]
T5 = "Ahora aumenta la velocidad"
r = generar(T5, mueve(20, extra={"eventos": [S1]}, preguntas=preg_f), contexto=ctx)
check(r.status == "needs_clarification", "pregunta a que frecuencia")
r = generar(T5, mueve(20, extra={"eventos": [S1]}), contexto=ctx,
            acl={"frecuencia_hz": {"pregunta": preg_f[0]["pregunta"], "respuesta": "35 Hz"}})
u = ultimo_usuario()
todo = " ".join(m["content"] for m in llamadas[-1])
check("LOGICA ACTIVA DE LA BANDA" in todo and '"cuando": "S1"' in todo, "parte de la logica activa")
check(u.startswith(T5 + "\n\n") and "freq_hz: 35 Hz" in u, "texto intacto + 35 Hz aparte")
check(r.status == "ok" and r.logic["band"]["freq_hz"] == 35, "la logica queda a 35 Hz")
check(r.logic["respuesta_llm"]["eventos"] == [S1], "y conserva la pausa de S1")

# ---------------------------------------------------------------------------
escenario("6. Equipo y proceso elegidos con botones")
r = generar("Enciende la luz verde 3 segundos", mueve(None, extra={"banda": {"mover": False},
            "motor": {}, "salidas": {"Q3": "I1"}}), device=None, acl={"equipo": {"respuesta": "Banda transportadora"}})
check(r.device == "banda", "la respuesta 'Banda transportadora' elige el equipo sin tocar el texto")
check(ac.equipo(ac.normalizar({"equipo": "Maletín"})) == "maletin", "y 'Maletín' el maletin")
check(ac.autoriza_catalogo(ac.normalizar({"proceso": "Autorizar ampliar el catalogo con ese proceso"})),
      "'Autorizar ampliar el catalogo' se lee de la aclaracion")

# ---------------------------------------------------------------------------
escenario("7. Una memoria identica por texto no ignora las respuestas")
app.guardar_memoria_validada(app.GuardarMemoriaRequest(dispositivo="banda", instruccion=TEXTO,
                                                       logic=generar(TEXTO, mueve(40), acl={"freq_hz": {"respuesta": "40 Hz"}}).logic))
r = generar(TEXTO)
check(r.analysis.get("fuente") == "memoria" and not llamadas, "sin aclaraciones se reutiliza la memoria")
r = generar(TEXTO, mueve(None), acl={"freq_hz": {"respuesta": "20 Hz"}})
check(len(llamadas) == 1 and r.logic["band"]["freq_hz"] == 20, "con 20 Hz elegido se genera con 20 Hz, no 40")

# ---------------------------------------------------------------------------
escenario("8. Normalizacion")
n = ac.normalizar({"Frecuencia_Hz": "25 Hz", "Dirección": {"respuesta": "Izquierda"}, "vacia": ""})
check(set(n) == {"freq_hz", "direccion"}, "alias y vacios")
check(ac.frecuencia(n) == 25 and ac.direccion(n) == "izquierda", "valores")
check(ac.sin_respondidas([{"campo": "frecuencia"}, {"slot": "input"}], n) == [{"slot": "input"}],
      "quita solo las preguntas respondidas")

print(f"\nTODAS LAS PRUEBAS DE ACLARACIONES PASARON ({OK} comprobaciones).")
