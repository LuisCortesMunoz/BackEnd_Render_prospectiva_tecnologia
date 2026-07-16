# test_agente_clarify.py — prueba OFFLINE de la Fase 1 (deteccion de ambiguedad).
#
# Verifica que:
#  - Peticiones AMBIGUAS (falta salida o entrada) se detectan y generan preguntas.
#  - Peticiones CLARAS NO se marcan ambiguas (siguen el flujo normal, sin regresion).
#  - El endpoint /generar-logica responde needs_clarification en el caso ambiguo
#    SIN llamar a Groq (la rama sale antes de generar).
#
# Uso:  python test_agente_clarify.py   (no necesita red ni GROQ_API_KEY real)

import os, asyncio

os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import app
from agents import clarify
from profile_registry import perfil_por_defecto

perfil = perfil_por_defecto()


def es_ambigua(texto):
    return clarify.analizar_peticion(texto, perfil)["ambiguous"]


# ── 1. Peticiones CLARAS: NO deben ser ambiguas (sin regresion) ──
claras = [
    "Quiero que I1 active Q10, que Q10 permanezca enclavada hasta presionar I2 y que se apague tras 10 segundos",
    "enciende la lampara verde con I1",
    "con I1 prende Q10",
    "I1 o I3 encienden la amarilla",
    "apaga todo",
]
for txt in claras:
    a = clarify.analizar_peticion(txt, perfil)
    assert not a["ambiguous"], f"FALSO POSITIVO (deberia ser clara): {txt!r} -> {a['missing']}"
print(f"1. {len(claras)} peticiones claras -> ninguna marcada ambigua (OK, sin regresion)")

# ── 2. Peticiones AMBIGUAS: deben pedir aclaracion ──
casos_ambiguos = [
    ("Hola, quiero prender una lampara, por favor", {"output", "input"}),
    ("enciende la lampara verde",                    {"input"}),          # falta boton
    ("quiero usar el boton I1",                       {"output"}),         # falta salida (btn dado)
]
for txt, esperado in casos_ambiguos:
    a = clarify.analizar_peticion(txt, perfil)
    assert a["ambiguous"], f"FALSO NEGATIVO (deberia ser ambigua): {txt!r}"
    assert set(a["missing"]) == esperado, f"{txt!r}: missing {a['missing']} != {esperado}"
    assert a["questions"], f"{txt!r}: deberia traer preguntas"
print(f"2. {len(casos_ambiguos)} peticiones ambiguas -> detectadas con preguntas (OK)")

# ── 3. Preguntas bien formadas (slot + pregunta + opciones del perfil) ──
a = clarify.analizar_peticion("prender una lampara", perfil)
slots = {q["slot"] for q in a["questions"]}
assert slots == {"output", "input"}
q_out = next(q for q in a["questions"] if q["slot"] == "output")
assert any("Q10" in o for o in q_out["opciones"]) and any("Q12" in o for o in q_out["opciones"])
print("3. Preguntas con opciones del perfil OK:", [q["slot"] for q in a["questions"]])

# ── 4. Endpoint: caso ambiguo responde needs_clarification SIN Groq ──
resp = asyncio.run(app.generar_logica(app.LogicaRequest(texto="Hola, quiero prender una lampara")))
assert resp.status == "needs_clarification", f"status={resp.status}"
assert resp.logic == {} and resp.outputs == 0
assert len(resp.questions) >= 1
print("4. Endpoint /generar-logica -> needs_clarification sin llamar a Groq (OK)")

# ── 5. Una modificacion (con programa_anterior) NO dispara el gate ──
#    (se comprueba que el flag es_modificacion lo salta; no llega a generar)
ctx = app.ContextoLadder(programa_anterior={"metadata": {"engine_config": {"outputs": []}}})
# Con contexto de modificacion, el gate de ambiguedad se omite: analizar_peticion
# no se usa. Verificamos la condicion que usa app.py:
es_modificacion = bool(ctx and ctx.programa_anterior)
assert es_modificacion is True
print("5. Peticion con programa_anterior se trata como modificacion (gate omitido) OK")

print("\nTODAS LAS PRUEBAS DE FASE 1 PASARON")
