# test_agente_validators.py — prueba OFFLINE de la Fase 2 (reflexion + bucle).
#
# 1) Unidad: reglas de agents/validators.validar_semantica.
# 2) Integracion: el bucle de generar_logica con llamar_modelo_json SIMULADO
#    (sin Groq, sin disco): auto-correccion y fallback lenient.
#
# Uso:  python test_agente_validators.py

import os, json, asyncio, tempfile
# Aislar la memoria: en Fase 3 generar_logica consulta ejemplos_relevantes, que
# escribe 'uses'. Usamos un archivo temporal para no tocar memoria/ejemplos.json.
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_validators.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import app
from agents import validators
from profile_registry import perfil_por_defecto

perfil = perfil_por_defecto()

def cfg_directo_contador():
    return {"name": "Contador 5", "device_profile": "maletin_basico", "reset_before": True,
            "system": {"enable": True, "global_stop": None},
            "outputs": [{"output": "Q10", "logic": {"mode": "directo", "source": "I1"},
                         "timer": None, "counter": {"type": "up_held", "preset": 5, "reset_input": None},
                         "expr": "I1", "comment": "cuenta 5"}]}

def cfg_contador_sin_base():
    c = cfg_directo_contador()
    c["outputs"][0]["logic"] = {"mode": "off"}   # contador sin entrada base -> incoherente
    return c

# ── 1. Unidad del validador semantico ──
sem = validators.validar_semantica(cfg_contador_sin_base(), perfil)
assert any("contador necesita" in e for e in sem["errors"]), sem
print("1. counter sobre base 'off' -> error semantico (OK)")

sem = validators.validar_semantica(cfg_directo_contador(), perfil)
assert sem["errors"] == [], sem
print("2. counter con base 'directo' -> sin errores (OK)")

seq_cfg = {"outputs": [{"output": "Q10", "logic": {"mode": "off"}, "timer": {"type": "pulse", "preset_s": 5}}],
           "sequence": {"start": "I1", "mode": "once", "steps": [{"outputs": ["Q10"], "duration_s": 5}]}}
sem = validators.validar_semantica(seq_cfg, perfil)
assert any("secuencia gobierna" in w for w in sem["warnings"]), sem
print("3. sequence + timer por salida -> aviso (OK)")

# ── 1b. Reglas alineadas con el Ladder maestro (ST "TON y enable") ──
def cfg_una_salida(logic, timer=None, counter=None):
    return {"outputs": [{"output": "Q10", "logic": logic, "timer": timer, "counter": counter}]}

def errores(cfg):
    return validators.validar_semantica(cfg, perfil)["errors"]

UP_HELD = {"type": "up_held", "preset": 3, "reset_input": "I4"}
UP      = {"type": "up", "preset": 3, "reset_input": None}
TON     = {"type": "on_delay", "preset_s": 5}

# Contador: cuenta activaciones de la base; una base enclavada solo se activa una vez.
e = errores(cfg_una_salida({"mode": "enclavado", "start": "I1", "stop": "I2"}, counter=UP_HELD))
assert any("cuenta las activaciones" in x for x in e), e
e = errores(cfg_una_salida({"mode": "combinacional", "a": "I1", "b": "I3", "op": "OR",
                            "latched": True}, counter=UP))
assert any("cuenta las activaciones" in x for x in e), e
assert errores(cfg_una_salida({"mode": "combinacional", "a": "I1", "b": "I3", "op": "OR"},
                              counter=UP)) == []
print("3a. counter sobre base enclavada -> error; sobre OR sin latched -> ok (OK)")

# up_held sobrescribe el resultado del timer en el PLC.
e = errores(cfg_una_salida({"mode": "directo", "source": "I1"}, timer=TON, counter=UP_HELD))
assert any("ignora el timer" in x for x in e), e
assert errores(cfg_una_salida({"mode": "directo", "source": "I1"}, timer=TON, counter=UP)) == []
print("3b. up_held + timer -> error; up + timer -> ok (OK)")

# stop: el PLC solo lo lee en logicas enclavadas.
e = errores(cfg_una_salida({"mode": "combinacional", "a": "I1", "b": "I3", "op": "OR", "stop": "I2"}))
assert any("solo usa 'stop'" in x for x in e), e
e = errores(cfg_una_salida({"mode": "directo", "source": "I1", "stop": "I2"}))
assert any("solo usa 'stop'" in x for x in e), e
assert errores(cfg_una_salida({"mode": "enclavado", "start": "I1", "stop": "I2"})) == []
assert errores(cfg_una_salida({"mode": "combinacional", "a": "I1", "b": "I3", "op": "OR",
                               "latched": True, "stop": "I2"})) == []
print("3c. stop sin enclavar -> error; con enclavado/latched -> ok (OK)")

# ── 2. Integracion del bucle (llamar_modelo_json simulado, sin Groq/disco) ──
_orig = (app.llamar_modelo_json, app.guardar_historial, app.agregar_ejemplo_logica)
app.guardar_historial     = lambda *a, **k: None
app.agregar_ejemplo_logica = lambda *a, **k: ""

def make_fake(respuestas):
    estado = {"n": 0}
    def fake(msgs):
        i = min(estado["n"], len(respuestas) - 1)
        estado["n"] += 1
        return json.loads(json.dumps(respuestas[i]))   # copia fresca
    return fake, estado

TXT = "I1 cuenta pulsos y enciende Q10"   # no ambiguo, sin palabras de enclavamiento

# 2a. Config limpia a la primera -> 1 llamada, status ok, sin aviso "Revisa"
app.llamar_modelo_json, st = make_fake([cfg_directo_contador()])
r = asyncio.run(app.generar_logica(app.LogicaRequest(texto=TXT, device_profile="maletin_basico")))
assert r.status == "ok" and st["n"] == 1, (r.status, st["n"])
assert not any("Revisa la logica" in w for w in r.warnings)
print("4. Config limpia -> 1 llamada, status ok (OK)")

# 2b. Auto-correccion: 1ra mala (semantica), 2da buena -> 2 llamadas, corregida
app.llamar_modelo_json, st = make_fake([cfg_contador_sin_base(), cfg_directo_contador()])
r = asyncio.run(app.generar_logica(app.LogicaRequest(texto=TXT, device_profile="maletin_basico")))
assert st["n"] == 2, st["n"]
assert r.status == "ok"
assert r.logic["outputs"][0]["logic"]["mode"] == "directo"   # quedo la corregida
print("5. Reflexion: 1ra incoherente -> se auto-corrige en la 2da (OK)")

# 2c. Fallback lenient: siempre incoherente -> no rechaza, acepta con aviso
app.llamar_modelo_json, st = make_fake([cfg_contador_sin_base()])
r = asyncio.run(app.generar_logica(app.LogicaRequest(texto=TXT, device_profile="maletin_basico")))
assert r.status == "ok", r.status                      # NO 422: nunca peor que antes
assert st["n"] == app.MAX_AUTOREVISIONES               # reintento hasta agotar
assert any("Revisa la logica" in w for w in r.warnings), r.warnings
print(f"6. Fallback lenient: {st['n']} intentos, aceptado con aviso (no 422) (OK)")

# 2d. Red de seguridad de enclavamiento: "se enclava al llegar al conteo" es un
#     contador up_held sobre base 'directo'. No debe reintentar pidiendo 'enclavado'
#     (contaria ciclos de arranque-paro en vez de pulsos).
TXT_CONTEO = "Cuenta 5 pulsos de I1 y al llegar se enclava la lampara verde Q10"
app.llamar_modelo_json, st = make_fake([cfg_directo_contador()])
r = asyncio.run(app.generar_logica(app.LogicaRequest(texto=TXT_CONTEO, device_profile="maletin_basico")))
assert r.status == "ok", r.status
assert st["n"] == 1, st["n"]                                  # sin reintento
assert r.logic["outputs"][0]["logic"]["mode"] == "directo"
assert not any("enclavamiento" in w for w in r.warnings), r.warnings
print("7. Conteo que 'se enclava' -> up_held aceptado sin reintento (OK)")

app.llamar_modelo_json, app.guardar_historial, app.agregar_ejemplo_logica = _orig
print("\nTODAS LAS PRUEBAS DE FASE 2 PASARON")
