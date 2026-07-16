# test_agente_experts.py — prueba OFFLINE de la Fase 3 (expertos + memoria).
#
# 1) Unidad: seleccion/composicion de expertos y filtrado/render de memoria.
# 2) Integracion: generar_logica inyecta la guia de expertos y los ejemplos
#    validados en el prompt (se captura llamar_modelo_json; sin Groq).
#
# Uso:  python test_agente_experts.py

import os, json, asyncio, tempfile

MEM = os.path.join(tempfile.gettempdir(), "test_mem_fase3.json")
os.environ["MEMORIA_JSON"] = MEM
os.environ.setdefault("GROQ_API_KEY", "test-key-local")
if os.path.exists(MEM):
    os.remove(MEM)

import app
from agents import experts, memory
from profile_registry import perfil_por_defecto

perfil = perfil_por_defecto()

# ── 1. Expertos: la guia contiene el experto correcto segun la intencion ──
casos = [
    ("enciende Q10 durante 5 segundos con I1",              "TEMPORIZADOR"),
    ("cuenta los pulsos de I1 y enciende Q10",              "CONTADOR"),
    ("I1 arranca Q10 y se queda enclavada, I2 la apaga",    "ENCLAVAMIENTO"),
    ("haz un semaforo con I1: verde, amarilla y roja",      "SECUENCIA"),
    ("I1 o I3 encienden Q11",                               "COMBINACIONAL"),
]
for txt, marca in casos:
    guia = experts.componer_guia(txt, perfil)
    assert marca in guia, f"{txt!r} -> falta {marca} en la guia: {guia[:80]}"
print(f"1. {len(casos)} intenciones -> guia de experto correcta (OK)")

# Sin intencion clara de capa -> sin guia
assert experts.componer_guia("con I1 prende Q10", perfil) == ""
print("2. Peticion sin capa especial -> sin guia (OK)")

# ── 2. Memoria: filtra solo esquema nuevo y lo renderiza ──
viejo = {"id": "ej_1", "status": "accepted", "user_prompt": "x",
         "generated_ladder_json": {"logica_ladder": []}}
nuevo = {"id": "lej_1", "status": "accepted", "user_prompt": "enclava Q10 con I1",
         "datos": {"engine_config": {"outputs": [{"output": "Q10",
                    "logic": {"mode": "enclavado", "start": "I1", "stop": "I2"}}]}}}
assert memory.filtrar_logica([viejo, nuevo]) == [nuevo]
blo = memory.bloque_logica_prompt([viejo, nuevo])
assert "engine_config:" in blo and "Q10" in blo and "ej_1" not in blo
print("3. Memoria: filtra esquema nuevo (lej_) y descarta el viejo (ej_) (OK)")

# ── 3. Integracion: el prompt final incluye guia de expertos y ejemplos ──
app.guardar_historial = lambda *a, **k: None   # aislar disco (historial)

# Semilla: un ejemplo validado (accepted) del esquema nuevo
eid = app.agregar_ejemplo_logica(
    "arranca la lampara verde Q10 con I1 y se enclava, se apaga con I2",
    {"name": "Enclave", "device_profile": "maletin_basico",
     "system": {"enable": True, "global_stop": None},
     "outputs": [{"output": "Q10", "logic": {"mode": "enclavado", "start": "I1", "stop": "I2"},
                  "timer": None, "counter": None, "expr": "(I1 + Q10) * !I2", "comment": "x"}]})
app.aplicar_feedback(eid, "accepted")

capt = {}
def fake_llamar(msgs):
    capt["msgs"] = msgs
    return {"name": "Enclave", "device_profile": "maletin_basico",
            "system": {"enable": True, "global_stop": None},
            "outputs": [{"output": "Q10", "logic": {"mode": "enclavado", "start": "I1", "stop": "I2"},
                         "timer": None, "counter": None, "expr": "(I1 + Q10) * !I2", "comment": "x"}]}
app.llamar_modelo_json = fake_llamar

r = asyncio.run(app.generar_logica(app.LogicaRequest(
    texto="arranca la lampara verde Q10 con I1 y se enclava, se apaga con I2",
    device_profile="maletin_basico")))
assert r.status == "ok"
sys_texts = " \n ".join(m["content"] for m in capt["msgs"] if m["role"] == "system")
assert "ENCLAVAMIENTO" in sys_texts, "falta guia de experto en el prompt"
assert "engine_config:" in sys_texts, "falta la memoria de ejemplos en el prompt"
print("4. Integracion: el prompt inyecta guia de expertos + ejemplo validado (OK)")

if os.path.exists(MEM):
    os.remove(MEM)
print("\nTODAS LAS PRUEBAS DE FASE 3 PASARON")
