# test_agente_fase4.py — prueba OFFLINE de la Fase 4 (perfiles parametricos).
#
# 1) EQUIVALENCIA (regresion critica): validar_logica_config(cfg) [historico] ==
#    validar_logica_config(cfg, perfil_maletin) para una bateria de configs.
# 2) ESCALADO: con el perfil plc_extendido se aceptan I/O que el maletin rechaza,
#    y viceversa; mas pasos de secuencia; descripcion de hardware.
# 3) INTEGRACION: generar_logica con device_profile=plc_extendido valida contra
#    ese perfil e inyecta su hardware en el prompt (llamar_modelo_json simulado).
#
# Uso:  python test_agente_fase4.py

import os, json, asyncio, tempfile
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_fase4.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import app
from profile_registry import cargar_perfil

maletin = cargar_perfil("maletin_basico")
ext     = cargar_perfil("plc_extendido")

def out(o, **extra):
    base = {"output": o, "logic": {"mode": "directo", "source": "I1"},
            "timer": None, "counter": None, "expr": "I1", "comment": ""}
    base.update(extra); return base

def cfg(outputs=None, sequence=None):
    c = {"name": "t", "system": {"enable": True, "global_stop": None}}
    c["outputs"] = outputs if outputs is not None else []
    if sequence is not None:
        c["sequence"] = sequence
    return c

# ── 1. EQUIVALENCIA maletin: None (historico) == perfil maletin ──
bateria = [
    cfg([out("Q10")]),
    cfg([out("Q10", logic={"mode": "enclavado", "start": "I1", "stop": "I2"})]),
    cfg([out("Q11", logic={"mode": "combinacional", "a": "I1", "b": "I3", "op": "OR"})]),
    cfg([out("Q10", timer={"type": "pulse", "preset_s": 5})]),
    cfg([out("Q10", counter={"type": "up_held", "preset": 5, "reset_input": None})]),
    cfg([out("VERDE")]),                                  # alias
    cfg(sequence={"start": "I1", "mode": "once",
                  "steps": [{"outputs": ["Q10"], "duration_s": 5}]}),
    cfg([out("Q99")]),                                    # salida invalida
    cfg([out("Q10", logic={"mode": "directo", "source": "I9"})]),  # entrada invalida
    cfg([out("Q10", logic={"mode": "raro"})]),            # modo invalido
    cfg([out("Q10", timer={"type": "foo", "preset_s": 5})]),       # timer invalido
    cfg([out("Q10", timer={"type": "on_delay", "preset_s": -3})]), # preset fuera de rango
    cfg([out("Q10"), out("Q10")]),                        # repetida
    cfg(),                                                # vacia
]
for i, c in enumerate(bateria):
    a = app.validar_logica_config(c)              # historico (None)
    b = app.validar_logica_config(c, maletin)     # via perfil maletin
    assert (len(a) == len(b)) and (bool(a) == bool(b)), \
        f"config {i}: historico={a}  perfil={b}"
print(f"1. Equivalencia maletin en {len(bateria)} configs (None == perfil): OK")

# ── 2. ESCALADO con plc_extendido ──
# Q1/I5 no existen en el maletin pero si en el extendido
c_q1 = cfg([out("Q1", logic={"mode": "directo", "source": "I5"})])
assert app.validar_logica_config(c_q1) != []               # rechazado por maletin
assert app.validar_logica_config(c_q1, ext) == []          # aceptado por extendido
print("2. Q1/I5: rechazado por maletin, aceptado por plc_extendido (OK)")

# Q10 existe en maletin pero NO en el extendido
c_q10 = cfg([out("Q10")])
assert app.validar_logica_config(c_q10) == []              # ok en maletin
assert app.validar_logica_config(c_q10, ext) != []         # invalido en extendido
print("3. Q10: valido en maletin, invalido en plc_extendido (OK)")

# Secuencia de 10 pasos: excede el maletin (8) pero cabe en el extendido (12)
pasos = [{"outputs": ["Q1"], "duration_s": 2} for _ in range(10)]
c_seq = cfg(sequence={"start": "I1", "mode": "once", "steps": pasos})
assert any("pasos" in e for e in app.validar_logica_config(c_seq, maletin))
assert app.validar_logica_config(c_seq, ext) == []
print("4. Secuencia de 10 pasos: rechazada por maletin (max 8), aceptada por extendido (max 12) (OK)")

# Descripcion de hardware
import engine_spec
hw = engine_spec.descripcion_hardware(ext)
assert "plc_extendido" in hw and "Q1" in hw and "I8" in hw
print("5. descripcion_hardware del perfil extendido (OK)")

# ── 3. INTEGRACION: generar_logica con plc_extendido ──
app.guardar_historial = lambda *a, **k: None

capt = {}
def fake(msgs):
    capt["msgs"] = msgs
    return cfg([out("Q1", logic={"mode": "directo", "source": "I5"})])
app.llamar_modelo_json = fake

r = asyncio.run(app.generar_logica(app.LogicaRequest(
    texto="I5 enciende Q1", device_profile="plc_extendido")))
assert r.status == "ok", r.status
assert r.logic["outputs"][0]["output"] == "Q1"
sys_txt = " \n ".join(m["content"] for m in capt["msgs"] if m["role"] == "system")
assert "plc_extendido" in sys_txt, "no se inyecto el hardware del perfil"
print("6. generar_logica con plc_extendido: valida y el prompt trae su hardware (OK)")

if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
print("\nTODAS LAS PRUEBAS DE FASE 4 PASARON")
