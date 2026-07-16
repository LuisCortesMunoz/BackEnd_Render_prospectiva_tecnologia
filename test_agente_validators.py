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

app.llamar_modelo_json, app.guardar_historial, app.agregar_ejemplo_logica = _orig
print("\nTODAS LAS PRUEBAS DE FASE 2 PASARON")
