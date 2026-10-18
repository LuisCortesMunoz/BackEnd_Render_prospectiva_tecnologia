# test_memoria_validada.py — pruebas OFFLINE de la memoria validada (nivel 1),
# el contexto por ramificaciones (nivel 2) y el catalogo de procesos (nivel 3).
#
# Que se prueba y COMO (nada toca un PLC fisico ni Groq):
#   - /generar-logica con el LLM SIMULADO (respuestas fijas): se prueba el
#     flujo del backend y QUE CONTEXTO recibe el modelo, no la calidad del
#     modelo real.
#   - La memoria se guarda en un almacen local temporal y, aparte, en un
#     almacen GitHub con la API SIMULADA (no se contacta GitHub).
#   - El rendimiento se mide con tokens ESTIMADOS (chars/3.2, el mismo
#     estimador del backend) y llamadas al modelo; la latencia del modelo real
#     no se puede medir offline.
#
# Uso:  python test_memoria_validada.py

import asyncio
import base64
import copy
import json
import os
import tempfile
import time

DIR_MEM = tempfile.mkdtemp(prefix="lavo_mem_")
os.environ["LAVO_MEMORIA_DIR"] = DIR_MEM
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_feedback_mv.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import almacen_memoria
import app
import contexto_ramificado as cr
import memoria_validada as mv

app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_mv.json")
app.BANDA_MOTOR_V7 = True
app.BANDA_MOTOR_CONFIGURABLE = False

OK = 0
TABLA = []


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1


def escenario(nombre):
    TABLA.append([nombre, OK, None])
    print(f"\n== {nombre}")


def cerrar():
    TABLA[-1][2] = OK - TABLA[-1][1]
    print(f"   OK ({TABLA[-1][2]} comprobaciones)")


# ---------------------------------------------------------------------------
# LLM simulado: registra cada llamada y lo que recibio
# ---------------------------------------------------------------------------
respuestas, llamadas = [], []


def _llm(msgs):
    llamadas.append(copy.deepcopy(msgs))
    if not respuestas:
        raise AssertionError("el modelo fue llamado sin respuesta preparada")
    return copy.deepcopy(respuestas.pop(0))


app.llamar_modelo_json = _llm


def generar(texto, device="banda", contexto=None):
    llamadas.clear()
    req = app.LogicaRequest(texto=texto, device=device, device_profile="maletin_basico", contexto=contexto)
    return asyncio.run(app.generar_logica(req))


def sistema(i=-1):
    """Texto de TODOS los mensajes de sistema de la llamada i al modelo."""
    return "\n".join(m["content"] for m in llamadas[i] if m["role"] == "system")


def guardar(dispositivo, instruccion, logic, fisica="no_realizada"):
    req = app.GuardarMemoriaRequest(dispositivo=dispositivo, instruccion=instruccion, logic=logic,
                                    prueba_fisica=fisica)
    return app.guardar_memoria_validada(req)


def error_al_guardar(dispositivo, instruccion, logic):
    try:
        guardar(dispositivo, instruccion, logic)
    except app.HTTPException as e:
        return e.detail
    return None


def ciclo(marcha, paro, hz=30, nombre="Banda ciclica"):
    return {"name": nombre, "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": hz},
            "secuencias": [{"id": "SQ1", "inicio": "I1", "paro": "I2", "modo": "infinita", "exclusiva": True,
                            "pasos": [{"motor": ["marcha"], "avanza": "tiempo", "segundos": marcha},
                                      {"motor": ["paro"], "avanza": "tiempo", "segundos": paro}]}],
            "salidas": {"Q3": "BANDA_CORRIENDO"},
            "analisis": {"intencion": "ciclo marcha/paro", "compatibilidad": "total",
                         "conservado": [f"{marcha} s / {paro} s"], "modificado": [], "simplificado": [],
                         "no_soportado": [], "requiere_confirmacion": False}, "preguntas": []}


MAL_ENCLAVADO = {"name": "Verde enclavada", "device_profile": "maletin_basico", "reset_before": True,
                 "system": {"enable": True, "global_stop": None},
                 "outputs": [{"output": "Q10", "logic": {"mode": "enclavado", "start": "I1", "stop": "I2"}}]}

T_CICLO = "A 30 Hz con I1 la banda avanza 5 segundos, se detiene 3 segundos y repite hasta I2; verde mientras corre"
T_CICLO_SIM = "A 30 Hz con I1 la banda avanza 8 segundos, se detiene 2 segundos y repite hasta I2; verde mientras corre"
T_MAL = "El verde se enclava con I1 y se apaga con I2"


# ---------------------------------------------------------------------------
escenario("1. Guardar una logica correcta de la banda")
# ---------------------------------------------------------------------------
respuestas[:] = [ciclo(5, 3)]
r = generar(T_CICLO)
check(r.status == "ok" and len(llamadas) == 1, "sin memoria: genera con el modelo (1 llamada)")
check(r.analysis["fuente"] == "modelo" and r.analysis["metricas"]["llamadas_modelo"] == 1, "metricas: fuente y llamadas")
LOGIC_CICLO = r.program["metadata"]["engine_config"]
g = guardar("banda", T_CICLO, LOGIC_CICLO)
check(g["resultado"] == "guardada" and g["memoria"]["dispositivo"] == "banda", "se guarda en la memoria de la banda")
reg = app.MEMORIA.obtener("banda", g["memoria"]["id"])
check(reg["json"]["band"] == LOGIC_CICLO["band"], "el elemento principal es el JSON (engine_config)")
check(reg["version_esquema"] == "banda-motor-v7" and "v7" in reg["programa_maestro"], "version de esquema y programa maestro")
check(reg["validacion"]["usuario"]["aprobada"] and reg["validacion"]["software"]["estado"] == "aprobada",
      "aprobacion del usuario y pruebas de software registradas por separado")
check(any("compilacion" in p for p in reg["validacion"]["software"]["pruebas"]), "incluye la compilacion a registros")
check(reg["validacion"]["fisica"]["estado"] == "no_realizada" and reg["validacion"]["fisica"]["fecha"] is None,
      "la prueba fisica NO se marca si no se declaro")
c = reg["caracteristicas"]
check({"secuencia", "motor", "luces"} <= set(c["funciones"]) and "I1" in c["entradas"] and "Q3" in c["salidas"],
      "funciones y entradas/salidas extraidas del JSON")
check(c["secuencias"][0]["pasos"] == 2 and reg["autonoma"] and reg["id"].startswith("banda-"), "secuencia, autonomia e id")
check(not mv.validador_esquema.validar(reg, mv.validador_esquema.cargar("memoria_schema.json")),
      "el registro cumple esquemas/memoria_schema.json")
# Lo incorrecto no entra
malo = copy.deepcopy(LOGIC_CICLO)
malo["band"]["engine"]["salidas"] = {"Q12": "SIEMPRE"}
check(error_al_guardar("banda", "x", malo) is not None, "un JSON que el PLC rechaza (Q12) no se guarda")
alt = copy.deepcopy(LOGIC_CICLO)
alt.update(requiere_confirmacion=True, confirmado=False)
check(any("alternativa sin confirmar" in e for e in error_al_guardar("banda", "y", alt)),
      "una alternativa sin confirmar no se guarda")
check(guardar("banda", T_CICLO, LOGIC_CICLO)["resultado"] == "duplicada", "el mismo JSON no se duplica")
cerrar()


# ---------------------------------------------------------------------------
escenario("2. Guardar una logica correcta del maletin")
# ---------------------------------------------------------------------------
respuestas[:] = [copy.deepcopy(MAL_ENCLAVADO)]
r = generar(T_MAL, device="maletin")
check(r.status == "ok" and r.device == "maletin" and len(llamadas) == 1, "genera con el modelo")
check(any("REFERENCIA TECNICA DEL MALETIN" in m["content"] for m in llamadas[0]),
      "sin memoria: recibe el contexto tecnico de codigos/")
check("01_Enclavar" in sistema(0), "el programa local relacionado (enclavar) y no todos")
check("02_Contador" not in sistema(0), "no envia programas que no tienen relacion")
LOGIC_MAL = r.program["metadata"]["engine_config"]
g = guardar("maletin", T_MAL, LOGIC_MAL)
check(g["resultado"] == "guardada", "guardada en la memoria del maletin")
regm = app.MEMORIA.obtener("maletin", g["memoria"]["id"])
check(regm["version_esquema"] == "maletin-engine-config-1" and "enclavamiento" in regm["caracteristicas"]["funciones"],
      "esquema y funciones del maletin")
check(error_al_guardar("maletin", "z", LOGIC_CICLO) is not None, "un JSON de la banda no se guarda como maletin")
check(error_al_guardar("banda", "z", LOGIC_MAL) is not None, "un JSON del maletin no se guarda como banda")
cerrar()


# ---------------------------------------------------------------------------
escenario("3. Recuperar una configuracion identica (sin llamar al modelo)")
# ---------------------------------------------------------------------------
respuestas[:] = []
r = generar(T_CICLO)
check(len(llamadas) == 0, "el modelo NO se llama")
check(r.status == "ok" and r.analysis["fuente"] == "memoria", "fuente = memoria")
check(r.logic["band"] == LOGIC_CICLO["band"], "devuelve el JSON aprobado")
check(r.analysis["metricas"]["tokens_prompt_estimados"] == 0, "0 tokens enviados")
check(any("Reutilizada de la memoria validada" in w for w in r.warnings), "se avisa que viene de la memoria")
r = generar("  a 30 hz con i1 la banda avanza cinco segundos, se detiene tres segundos y repite hasta i2; VERDE mientras corre ")
check(len(llamadas) == 0 and r.analysis["fuente"] == "memoria", "misma instruccion escrita distinto (numeros, mayusculas)")
respuestas[:] = [ciclo(5, 3)]
r = generar("Agrega a la banda: " + T_CICLO)
check(len(llamadas) == 1 and r.analysis["fuente"] == "modelo", "una MODIFICACION ('agrega') no se reutiliza a ciegas")
respuestas[:] = [copy.deepcopy(MAL_ENCLAVADO)]
r = generar(T_MAL, device="maletin")
check(len(llamadas) == 0 and r.analysis["fuente"] == "memoria" and r.device == "maletin", "identica del maletin")
cerrar()


# ---------------------------------------------------------------------------
escenario("4. Recuperar una configuracion similar (referencia para el modelo)")
# ---------------------------------------------------------------------------
respuestas[:] = [ciclo(8, 2)]
r = generar(T_CICLO_SIM)
s = sistema(0)
check(len(llamadas) == 1, "una similar SI llama al modelo (cambian parametros)")
check("MEMORIAS VALIDADAS" in s and T_CICLO in s, "recibe la memoria parecida como referencia")
check("EJEMPLOS" not in s and "GUIA RAPIDA" not in s, "con memoria no recibe la guia ni los ejemplos genericos")
check(r.analysis["memorias_referencia"] and r.analysis["memorias_referencia"][0]["similitud"] >= mv.UMBRAL_SIMILAR,
      "la respuesta informa que memoria uso y su similitud")
check(r.logic["band"]["engine"]["secuencias"][0]["pasos"][0]["segundos"] == 8, "el resultado conserva los parametros nuevos")
cerrar()


# ---------------------------------------------------------------------------
escenario("5. Generar sin memoria previa (contexto tecnico local)")
# ---------------------------------------------------------------------------
resp_plumas = {"name": "S1 sube pluma 1", "banda": {"mover": False},
               "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "S1", "segundos": 3}], "salidas": {"Q8": "T1"},
               "analisis": {"intencion": "pluma con S1", "compatibilidad": "total", "conservado": ["3 s"],
                            "modificado": [], "simplificado": [], "no_soportado": [], "requiere_confirmacion": False},
               "preguntas": []}
respuestas[:] = [resp_plumas]
r = generar("Cuando el sensor 1 detecte una caja, sube la pluma 1 durante 3 segundos")
s = sistema(0)
check(r.status == "ok" and "MEMORIAS VALIDADAS" not in s, "sin memoria adecuada no se inyecta ninguna")
check("GUIA RAPIDA" in s and "EJEMPLOS" in s and "LIMITES DEL PROGRAMA MAESTRO" in s,
      "recibe guia + ejemplos + limites de cscape/LAVO_Referencia_v7.txt")
check("HARDWARE (fijo" in s and "REGLAS CRITICAS" in s and "ESQUEMA" in s, "el nucleo (hardware, reglas, esquema) siempre va")
cerrar()


# ---------------------------------------------------------------------------
escenario("6. Secuencia especifica: NO consulta procesos industriales")
# ---------------------------------------------------------------------------
respuestas[:] = [ciclo(5, 3, nombre="5/3")]
r = generar("Enciende la banda cinco segundos, detenla tres segundos y repite el ciclo a 30 Hz con I1")
check("REFERENCIA DEL CATALOGO" not in sistema(0) and "PROCESO GENERAL" not in sistema(0), "no recibe el catalogo")
check(not any("catalogo" in x for x in r.analysis["contexto"]["contexto"]), "el plan de contexto no lo incluye")
cerrar()


# ---------------------------------------------------------------------------
escenario("7. 'Linea de empaquetado' usa el catalogo")
# ---------------------------------------------------------------------------
resp_emp = {"name": "Empaquetado (propuesta)", "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 25},
            "motor": {"arranque": "I1", "paro": "I2", "pausa": "T1"},
            "contadores": [{"id": "CT1", "entrada": "S1", "preset": 6, "modo": "ciclico"}],
            "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "CT1", "segundos": 3}],
            "salidas": {"Q3": "BANDA_CORRIENDO", "Q4": "T1"},
            "analisis": {"intencion": "empaquetado por lotes de 6", "compatibilidad": "parcial",
                         "conservado": ["transporte, conteo y lote"], "modificado": ["lote de 6 y 3 s (valores tipicos)"],
                         "simplificado": ["el deposito en caja se simula con una pausa de 3 s y la luz amarilla"],
                         "no_soportado": ["no hay actuador de carga ni sensor de caja"],
                         "requiere_confirmacion": True}, "preguntas": []}
respuestas[:] = [copy.deepcopy(resp_emp)]
r = generar("Quiero que mi banda funcione como una linea de empaquetado")
s = sistema(0)
check("REFERENCIA DEL CATALOGO" in s and "encajado" in s.lower(), "recibe el proceso de empaquetado")
check("Línea de embotellado" not in s, "no recibe la referencia de procesos que no se pidieron")
check(r.analysis["contexto"]["procesos"] == ["empaquetado"], "el plan registra el proceso usado")
check(r.status == "needs_confirmation", "una propuesta adaptada requiere confirmacion")
cerrar()


# ---------------------------------------------------------------------------
escenario("8. 'Linea de embotellado' usa el catalogo")
# ---------------------------------------------------------------------------
resp_bot = copy.deepcopy(resp_emp)
resp_bot["name"] = "Embotellado (propuesta)"
respuestas[:] = [resp_bot]
r = generar("Quiero que funcione como una linea de embotellado")
check("Línea de embotellado" in sistema(0) and r.analysis["contexto"]["procesos"] == ["embotellado"],
      "recibe el proceso de embotellado")
cerrar()


# ---------------------------------------------------------------------------
escenario("9. Adaptar un proceso generico a los recursos reales de la banda")
# ---------------------------------------------------------------------------
s = sistema(0)
check("S1, S2, pluma1, pluma2" in s and "SIMULAN" in s and "estacion_trabajo" in s,
      "las reglas de adaptacion mapean a sensores, plumas y pausas reales")
inventado = copy.deepcopy(resp_emp)
inventado["salidas"] = {"Q10": "T1"}          # salida que no existe en la banda
respuestas[:] = [inventado, copy.deepcopy(resp_emp)]
r = generar("Quiero automatizar una estacion de empaquetado")
check(len(llamadas) == 2, "si el modelo usa hardware inexistente, la validacion lo devuelve (auto-revision)")
hw = set(app.SYSTEM_PROMPT_BANDA_V7 and ["Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9"])
check(set(r.analysis["recursos"]["salidas"]) <= hw, "la propuesta final usa solo salidas reales")
cerrar()


# ---------------------------------------------------------------------------
escenario("10. Detectar componentes requeridos que no existen")
# ---------------------------------------------------------------------------
check("componentes_tipicos_que_quiza_no_existan" in sistema(0) and "NO EXISTEN" in sistema(0),
      "el modelo recibe que componentes tipicos pueden no existir")
check(any("actuador de carga" in x for x in r.analysis["compatibilidad"]["no_soportado"]),
      "los componentes faltantes quedan en no_soportado")
check(any(w.startswith("No soportado:") for w in r.warnings), "y se avisan al usuario")
respuestas[:] = []
r = generar("Quiero que la banda sea una linea de mineria")
check(len(llamadas) == 0 and r.status == "needs_clarification", "proceso fuera del catalogo: no se inventa")
check(r.questions[0]["id"] == "proceso_no_catalogado" and "mineria" in r.questions[0]["pregunta"],
      "explica la limitacion y ofrece opciones")
r = generar("Quiero que la banda sea una linea de mineria Autorizar ampliar el catalogo con ese proceso")
check(r.status == "needs_clarification" and r.analysis["proceso_solicitado"] == "mineria", "registra la solicitud")
check(app.MEMORIA.almacen.leer("procesos_solicitados.json")["solicitudes"][-1]["proceso"] == "mineria",
      "la solicitud queda guardada para ampliar el catalogo despues")
cerrar()


# ---------------------------------------------------------------------------
escenario("11. La banda nunca recibe memorias del maletin")
# ---------------------------------------------------------------------------
T_MIX = "Con I1 enciende la verde 5 segundos"
mal_pulso = {"name": "Verde 5 s", "device_profile": "maletin_basico", "reset_before": True,
             "system": {"enable": True, "global_stop": None},
             "outputs": [{"output": "Q10", "logic": {"mode": "directo", "source": "I1"},
                          "timer": {"type": "pulse", "preset_s": 5}}]}
g = guardar("maletin", T_MIX, mal_pulso)
check(app.MEMORIA.buscar("maletin", T_MIX, "maletin-engine-config-1"), "la memoria del maletin existe y se encuentra")
check(not any(m["id"] == g["memoria"]["id"] for _, m in app.MEMORIA.buscar("banda", T_MIX, "banda-motor-v7", umbral=0)),
      "la busqueda de la banda no la ve (ni con umbral 0)")
respuestas[:] = [{"name": "Verde 5 s", "banda": {"mover": False},
                  "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "I1", "segundos": 5}], "salidas": {"Q3": "T1"},
                  "analisis": {"intencion": "verde 5 s", "compatibilidad": "total", "conservado": ["5 s"],
                               "modificado": [], "simplificado": [], "no_soportado": [],
                               "requiere_confirmacion": False}, "preguntas": []}]
r = generar(T_MIX, device="banda")
check(len(llamadas) == 1 and r.analysis["fuente"] == "modelo", "la identica del maletin NO se reutiliza en la banda")
check(g["memoria"]["id"] not in sistema(0) and "Q10" not in json.dumps(r.logic["band"]), "ni se inyecta en su prompt")
cerrar()


# ---------------------------------------------------------------------------
escenario("12. Persistencia de la memoria")
# ---------------------------------------------------------------------------
nueva = mv.MemoriaValidada(almacen_memoria.AlmacenLocal(DIR_MEM))
check(nueva.identica("banda", T_CICLO, "banda-motor-v7") is not None, "otra instancia (reinicio) la recupera del almacen")
check(os.path.exists(os.path.join(DIR_MEM, "banda", "indice.json")), "indice por dispositivo en disco")


class GitHubFalso:
    """API de contenidos de GitHub en memoria (incluye conflictos de sha)."""
    def __init__(self):
        self.archivos, self.puts, self.forzar_conflicto = {}, 0, 0

    def __call__(self, metodo, url, cuerpo, cab):
        assert cab["Authorization"].startswith("Bearer ")
        ruta = url.split("/contents/")[1].split("?")[0]
        if metodo == "GET":
            if ruta not in self.archivos:
                return 404, {"message": "Not Found"}
            contenido, sha = self.archivos[ruta]
            return 200, {"content": base64.b64encode(contenido).decode(), "sha": sha}
        self.puts += 1
        if self.forzar_conflicto:
            self.forzar_conflicto -= 1
            return 409, {"message": "sha mismatch"}
        actual = self.archivos.get(ruta)
        if actual and cuerpo.get("sha") != actual[1]:
            return 409, {"message": "sha mismatch"}
        sha = f"sha{self.puts}"
        self.archivos[ruta] = (base64.b64decode(cuerpo["content"]), sha)
        return (200 if actual else 201), {"content": {"sha": sha}}


gh = GitHubFalso()
mem_gh = mv.MemoriaValidada(almacen_memoria.AlmacenGitHub("usuario/LAVO-memoria", "tok", transporte=gh))
val = app._validador_memoria("banda")
r1 = mem_gh.guardar("banda", T_CICLO, LOGIC_CICLO, val, "banda-motor-v7")
check(r1["resultado"] == "guardada" and "banda/indice.json" in gh.archivos, "se guarda en el repo de GitHub (API simulada)")
check(any(k.startswith("banda/validadas/") for k in gh.archivos), "registro en banda/validadas/<id>.json")
gh.forzar_conflicto = 1
otro = copy.deepcopy(LOGIC_CICLO)
otro["band"]["freq_hz"] = 40
r2 = mem_gh.guardar("banda", "Otra banda a 40 Hz", otro, val, "banda-motor-v7")
check(r2["resultado"] == "guardada", "un conflicto de sha (409) se resuelve releyendo y reintentando")
reinicio = mv.MemoriaValidada(almacen_memoria.AlmacenGitHub("usuario/LAVO-memoria", "tok", transporte=gh))
check(len(reinicio.listar("banda")) == 2 and reinicio.identica("banda", T_CICLO, "banda-motor-v7"),
      "tras un 'reinicio' la memoria sigue en GitHub")
check(isinstance(almacen_memoria.crear_almacen(), almacen_memoria.AlmacenLocal), "sin token se usa el almacen local")
os.environ.update(LAVO_MEMORIA_REPO="u/r", LAVO_MEMORIA_TOKEN="t")
check(isinstance(almacen_memoria.crear_almacen(), almacen_memoria.AlmacenGitHub), "con repo + token se usa GitHub")
del os.environ["LAVO_MEMORIA_REPO"], os.environ["LAVO_MEMORIA_TOKEN"]
# Versiones: misma instruccion con otro JSON -> nueva version, la anterior queda reemplazada
v2 = copy.deepcopy(LOGIC_CICLO)
v2["band"]["freq_hz"] = 35
g2 = guardar("banda", T_CICLO, v2)
check(g2["resultado"] == "nueva_version" and g2["memoria"]["version"] == 2, "misma instruccion, otro JSON -> version 2")
anterior = [m for m in app.MEMORIA.listar("banda") if m["id"] == g2["memoria"]["reemplaza"]][0]
check(anterior["estado"] == "reemplazada", "la version anterior queda reemplazada (no se borra)")
respuestas[:] = []
r = generar(T_CICLO)
check(r.analysis["fuente"] == "memoria" and r.logic["band"]["freq_hz"] == 35, "la identica usa la version vigente")
pf = app.marcar_prueba_fisica("banda", g2["memoria"]["id"], app.PruebaFisicaRequest(estado="exitosa", nota="banda real"))
check(pf["memoria"]["prueba_fisica"] == "exitosa", "la prueba fisica se registra solo cuando el usuario la declara")
cerrar()


# ---------------------------------------------------------------------------
escenario("13. Los procesos investigados no se tratan como logicas validadas")
# ---------------------------------------------------------------------------
check(all(p["ejecutable"] is False and p["tipo_conocimiento"] == "proceso_industrial_generico" for p in cr.catalogo()),
      "todo el catalogo esta marcado como no ejecutable")
check(len(cr.catalogo()) == 10 and all(p["fuentes"] for p in cr.catalogo()), "10 procesos, todos con fuente")
ids_memoria = {m["id"] for d in mv.DISPOSITIVOS for m in app.MEMORIA.listar(d)}
check(not ids_memoria & {p["id"] for p in cr.catalogo()}, "ningun proceso aparece en la memoria validada")
respuestas[:] = [copy.deepcopy(resp_emp)]
r = generar("Quiero que mi banda funcione como una linea de empaquetado")
check(len(llamadas) == 1 and r.analysis["fuente"] == "modelo", "pedir un proceso nunca devuelve el catalogo como logica")
check("NO es una logica validada" in sistema(0), "el modelo recibe el proceso marcado como referencia")
cerrar()


# ---------------------------------------------------------------------------
escenario("14. Rendimiento: antes (prompt completo) vs despues (ramificado + memoria)")
# ---------------------------------------------------------------------------
CASOS = [  # (texto, respuesta simulada)
    (T_CICLO, ciclo(5, 3)),                                    # identica en memoria
    (T_CICLO_SIM, ciclo(8, 2)),                                # similar
    ("Cuando el sensor 1 detecte una caja, sube la pluma 1 durante 3 segundos", resp_plumas),  # sin memoria
    ("Quiero que mi banda funcione como una linea de empaquetado", resp_emp),                 # proceso
]


mal_conteo = {"name": "Amarilla al 5", "device_profile": "maletin_basico", "reset_before": True,
              "system": {"enable": True, "global_stop": None},
              "outputs": [{"output": "Q11", "logic": {"mode": "directo", "source": "I1"},
                           "counter": {"type": "up_held", "preset": 5, "reset_input": "I4"}}]}
CASOS_MAL = [
    (T_MAL, MAL_ENCLAVADO),                                                    # identica en memoria
    ("Con I1 enciende la verde 5 segundos solamente", mal_pulso),               # similar
    ("Cuenta 5 pulsos de I1 y enciende la amarilla; I4 reinicia", mal_conteo),  # sin memoria
]


def correr(ramificado, casos=CASOS, device="banda"):
    app.CONTEXTO_RAMIFICADO = app.REUSO_MEMORIA = ramificado
    filas = []
    for texto, resp in casos:
        respuestas[:] = [copy.deepcopy(resp)]
        t0 = time.perf_counter()
        r = generar(texto, device=device)
        m = r.analysis["metricas"]
        filas.append({"texto": texto[:38], "llamadas": m["llamadas_modelo"], "tokens": m["tokens_prompt_estimados"],
                      "fuente": m["fuente"], "t_local_ms": round((time.perf_counter() - t0) * 1000, 1),
                      "t_memoria_ms": m["t_memoria_ms"]})
    return filas


antes, despues = correr(False), correr(True)
print(f"   {'caso':40s} {'ANTES tokens/llam':>18s} {'DESPUES tokens/llam':>20s}  fuente   t_mem")
for a, d in zip(antes, despues):
    print(f"   {a['texto']:40s} {a['tokens']:>12d} / {a['llamadas']}  {d['tokens']:>13d} / {d['llamadas']}"
          f"   {d['fuente']:7s} {d['t_memoria_ms']:5.1f} ms")
ta, td = sum(x["tokens"] for x in antes), sum(x["tokens"] for x in despues)
la, ld = sum(x["llamadas"] for x in antes), sum(x["llamadas"] for x in despues)
reuso = sum(1 for x in despues if x["fuente"] == "memoria") / len(despues)
print(f"   TOTAL tokens estimados: {ta} -> {td} ({100 * (td - ta) / ta:+.0f} %) · llamadas: {la} -> {ld} · "
      f"reutilizacion {100 * reuso:.0f} %")
check(despues[0]["llamadas"] == 0 and antes[0]["llamadas"] == 1, "la identica ahorra la llamada al modelo")
check(despues[1]["tokens"] < antes[1]["tokens"], "con memoria similar se envian menos tokens que el prompt completo")
check(td < ta and ld < la, "en conjunto: menos tokens y menos llamadas")
check(all(x["t_memoria_ms"] < 200 for x in despues), "la busqueda en memoria local tarda < 200 ms")
antes_m, despues_m = correr(False, CASOS_MAL, "maletin"), correr(True, CASOS_MAL, "maletin")
print("   MALETIN")
for a, d in zip(antes_m, despues_m):
    print(f"   {a['texto']:40s} {a['tokens']:>12d} / {a['llamadas']}  {d['tokens']:>13d} / {d['llamadas']}"
          f"   {d['fuente']:7s} {d['t_memoria_ms']:5.1f} ms")
tam, tdm = sum(x["tokens"] for x in antes_m), sum(x["tokens"] for x in despues_m)
print(f"   TOTAL maletin tokens estimados: {tam} -> {tdm} ({100 * (tdm - tam) / tam:+.0f} %) · llamadas: "
      f"{sum(x['llamadas'] for x in antes_m)} -> {sum(x['llamadas'] for x in despues_m)}")
check(despues_m[0]["llamadas"] == 0, "maletin: la identica no llama al modelo")
resumen = app.metricas_generacion()
check(resumen["banda"]["peticiones"] > 0 and "porcentaje_reutilizacion" in resumen["banda"], "GET /memoria/metricas agrega")
RENDIMIENTO = {"antes": antes, "despues": despues}
cerrar()

print("\n" + "=" * 72)
for nombre, _, n in TABLA:
    print(f"  {n:3d}  {nombre}")
print(f"TODAS LAS PRUEBAS DE MEMORIA VALIDADA PASARON ({OK} comprobaciones).")
