"""
test_edicion_incremental.py — cada instruccion edita la logica activa.

Corre sin Groq (modelo simulado) y sin PLC:
    python test_edicion_incremental.py

Escenarios: generar desde cero, agregar, cambiar un tiempo, sumar un segundo
sensor, eliminar una pieza, corregir, varias ediciones seguidas, la red que
detecta piezas perdidas sin pedirlo y que una memoria identica no reemplace
la logica activa.
"""

import asyncio
import copy
import json
import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_edicion_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_edicion.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import app
import edicion_incremental as ei

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_edicion.json")
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


class Sesion:
    """Lo que hace el front: una logica activa + las instrucciones aplicadas."""

    def __init__(self, device):
        self.device, self.logic, self.instrucciones = device, None, []

    def contexto(self):
        if not self.logic:
            return None
        return app.ContextoLadder(programa_anterior={"metadata": {"engine_config": self.logic}},
                                  historial=list(self.instrucciones))

    def enviar(self, texto, *resp):
        respuestas[:] = list(resp)
        llamadas.clear()
        req = app.LogicaRequest(texto=texto, device=self.device, device_profile="maletin_basico",
                                contexto=self.contexto())
        r = asyncio.run(app.generar_logica(req))
        if r.status == "ok":              # el front solo aplica versiones validas y confirmadas
            self.logic = r.logic
            self.instrucciones.append(texto)
        return r


def usuario(i=-1):
    return "\n".join(m["content"] for m in llamadas[i] if m["role"] == "user")


def motor_v7(r):
    return r.logic["respuesta_llm"]


BANDA = {"mover": True, "direccion": "derecha", "frecuencia_hz": 30}
S1 = {"cuando": "S1", "duracion_s": 3, "acciones": {"banda": "pausar"}}
S2 = {"cuando": "S2", "duracion_s": 4, "acciones": {"banda": "pausar"}}


def j(eventos, segundos=None, salidas=None, name="Banda S1"):
    d = {"name": name, "banda": dict(BANDA), "motor": {"arranque": "I1"}, "eventos": eventos}
    if segundos is not None:
        d["timers"] = [{"id": "T1", "tipo": "TON", "entrada": "BANDA_CORRIENDO", "segundos": segundos}]
        d["motor"]["paro"] = "T1"
    if salidas:
        d["salidas"] = salidas
    d["analisis"] = {"intencion": "x", "compatibilidad": "total", "conservado": [], "modificado": [],
                     "simplificado": [], "no_soportado": [], "requiere_confirmacion": False}
    return d


# ---------------------------------------------------------------------------
escenario("1. Generar desde cero (sin logica activa)")
s = Sesion("banda")
r = s.enviar("Cuando el sensor 1 detecte una pieza, detén la banda durante 3 segundos y después vuelve a arrancarla",
             j([S1]))
check(r.status == "ok", "logica nueva aceptada")
check("LOGICA ACTIVA" not in usuario(), "sin logica activa no se manda JSON previo")
check(motor_v7(r)["eventos"][0]["cuando"] == "S1", "evento de S1 generado")

# ---------------------------------------------------------------------------
escenario("2. Agregar una condicion: el modelo recibe la logica activa")
r = s.enviar("Ahora agrega que, después de arrancar, avance 5 segundos y vuelva a detenerse",
             j([S1], segundos=5))
u = usuario()
check("LOGICA ACTIVA DE LA BANDA" in u, "el prompt lleva la logica activa")
check('"cuando": "S1"' in u, "el JSON vigente va completo")
check("Cuando el sensor 1 detecte" in u, "y las instrucciones ya aplicadas")
check("Conserva TODO" in u, "con las reglas de edicion")
check(r.status == "ok" and len(llamadas) == 1, "una sola llamada: nada se perdio")
check(motor_v7(r)["timers"][0]["segundos"] == 5 and motor_v7(r)["eventos"][0]["cuando"] == "S1",
      "conserva S1 y agrega el avance de 5 s")

# ---------------------------------------------------------------------------
escenario("3. Cambiar un temporizador sin perder lo demas")
r = s.enviar("Cambia el último tiempo de 5 a 8 segundos", j([S1], segundos=8))
check(r.status == "ok", "cambio aceptado")
check(motor_v7(r)["timers"][0]["segundos"] == 8, "el timer quedo en 8 s")
check(motor_v7(r)["eventos"] == [S1], "la pausa de S1 sigue igual")
check("Cambia el último tiempo" in usuario() or "Ahora agrega" in usuario(), "historial en el prompt")

# ---------------------------------------------------------------------------
escenario("4. Incorporar un segundo sensor a la logica existente")
r = s.enviar("Agrega que el sensor 2 detenga la banda durante 4 segundos y después continúe avanzando",
             j([S1, S2], segundos=8))
check(r.status == "ok", "segundo sensor aceptado")
ev = [e["cuando"] for e in motor_v7(r)["eventos"]]
check(ev == ["S1", "S2"] and motor_v7(r)["timers"][0]["segundos"] == 8, "S1, S2 y el timer de 8 s conviven")
check(len(s.instrucciones) == 4, "cuatro instrucciones acumuladas en la sesion")

# ---------------------------------------------------------------------------
escenario("5. Red de seguridad: el modelo pierde piezas que no se pidio quitar")
r = s.enviar("Agrega que la torreta verde encienda mientras la banda corre",
             j([S1], salidas={"Q3": "BANDA_CORRIENDO"}),                    # pierde S2 y T1
             j([S1, S2], segundos=8, salidas={"Q3": "BANDA_CORRIENDO"}))   # restaurado
check(len(llamadas) == 2, "se pidio una revision")
check("PERDIO" in usuario() and "evento cuando S2" in usuario() and "timer T1" in usuario(),
      "la revision nombra las piezas perdidas")
check(r.status == "ok" and [e["cuando"] for e in motor_v7(r)["eventos"]] == ["S1", "S2"],
      "la version final conserva todo y agrega la verde")
check(motor_v7(r)["salidas"] == {"Q3": "BANDA_CORRIENDO"}, "con la verde nueva")
activa = copy.deepcopy(s.logic)

# ---------------------------------------------------------------------------
escenario("6. Si el modelo insiste en perder piezas, NO reemplaza la logica activa")
r = s.enviar("Agrega que la roja encienda cuando la banda este detenida",
             j([S1], salidas={"Q5": "BANDA_DETENIDA"}),
             j([S1], salidas={"Q5": "BANDA_DETENIDA"}))
check(r.status == "needs_confirmation", "queda como alternativa que el usuario debe confirmar")
check(any("no pedia quitar" in x for x in r.analysis["compatibilidad"]["modificado"]),
      "explica que se quitaron piezas")
check(s.logic == activa, "la logica activa no cambio")

# ---------------------------------------------------------------------------
escenario("7. Eliminar una pieza especifica")
r = s.enviar("Elimina la pausa del sensor 2", j([S1], segundos=8, salidas={"Q3": "BANDA_CORRIENDO"}))
check(len(llamadas) == 1 and r.status == "ok", "pedir quitar no dispara la red de perdidas")
check([e["cuando"] for e in motor_v7(r)["eventos"]] == ["S1"], "solo se quito S2")
check(motor_v7(r)["timers"][0]["segundos"] == 8 and motor_v7(r)["salidas"], "lo demas se conserva")

# ---------------------------------------------------------------------------
escenario("8. Corregir sin reemplazar toda la logica")
r = s.enviar("Corrige el temporizador, debe esperar 6 segundos",
             j([S1], segundos=6, salidas={"Q3": "BANDA_CORRIENDO"}))
check(r.status == "ok" and motor_v7(r)["timers"][0]["segundos"] == 6, "timer corregido")
check([e["cuando"] for e in motor_v7(r)["eventos"]] == ["S1"] and motor_v7(r)["salidas"], "resto intacto")
check(len(s.instrucciones) == 7, "seis ediciones consecutivas acumuladas sobre la primera")

# ---------------------------------------------------------------------------
escenario("9. Una memoria identica no reemplaza la logica activa")
texto0 = s.instrucciones[0]
logica0 = Sesion("banda").enviar(texto0, j([S1])).logic
guardada = app.guardar_memoria_validada(app.GuardarMemoriaRequest(
    dispositivo="banda", instruccion=texto0, logic=logica0))
check(guardada["resultado"] in ("guardada", "duplicada", "nueva_version"), "memoria validada guardada")
nueva = Sesion("banda")
r = nueva.enviar(texto0)                                  # sin respuestas: no debe llamar al modelo
check(r.analysis.get("fuente") == "memoria" and not llamadas, "sin logica activa se reutiliza la memoria")
r = s.enviar(texto0, j([S1], segundos=6, salidas={"Q3": "BANDA_CORRIENDO"}))
check(len(llamadas) == 1 and r.analysis.get("fuente") == "modelo", "con logica activa se edita con el modelo")
check(motor_v7(r)["timers"][0]["segundos"] == 6, "y conserva la logica activa")

# ---------------------------------------------------------------------------
escenario("10. Maletin: la misma red de perdidas")


def mal(*outs):
    return {"name": "m", "device": "maletin", "outputs": list(outs), "system": {}}


Q10 = {"output": "Q10", "logic": {"mode": "enclavado", "start": "I1", "stop": "I2"}}
Q11 = {"output": "Q11", "logic": {"mode": "directo", "source": "I3"}}
m = Sesion("maletin")
r = m.enviar("Con I1 arranca la verde y se queda; con I2 se apaga", mal(Q10))
check(r.status == "ok", "maletin desde cero")
r = m.enviar("Agrega que la amarilla prenda con I3", mal(Q11), mal(Q10, Q11))
check(len(llamadas) == 2 and "salida Q10" in usuario(), "detecta que se perdio Q10 y pide restaurarla")
check([o["output"] for o in r.logic["outputs"] if o.get("logic", {}).get("mode") != "off"] == ["Q10", "Q11"],
      "Q10 y Q11 conviven")
check("LOGICA ACTIVA DEL MALETIN" in usuario(0), "el prompt del maletin lleva la logica activa")

# ---------------------------------------------------------------------------
escenario("11. Deteccion de instrucciones que quitan piezas")
for t in ("Elimina el último paro", "Cambia el sensor 1 por el sensor 2", "ya no uses la roja",
          "quita la pausa"):
    check(ei.pide_quitar(t), f"'{t}' puede quitar piezas")
for t in ("Agrega un contador", "Cambia el último tiempo de 5 a 8 segundos", "Corrige el temporizador"):
    check(not ei.pide_quitar(t), f"'{t}' no quita piezas")

print(f"\nTODAS LAS PRUEBAS DE EDICION INCREMENTAL PASARON ({OK} comprobaciones).")
