# test_memoria_feedback.py — prueba local de la memoria de feedback (sin Groq).
# Uso:  python test_memoria_feedback.py
import os, json, tempfile

os.environ.setdefault("GROQ_API_KEY", "test-key-local")
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_memoria.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])

import app

DATOS_MODELO = {
    "programa_nombre": "Boton enciende lampara",
    "logica_ladder": [{
        "renglon": 1,
        "descripcion": "Boton %I1 enciende %Q1",
        "filas": [{
            "fila": 0,
            "descripcion": "serie principal",
            "elementos": [
                {"tipo": "XIC", "operando": "%I1", "descripcion": "boton"},
                {"tipo": "OTE", "operando": "%Q1", "descripcion": "lampara"},
            ],
        }],
    }],
    "explicacion_simple": "Al presionar el boton se enciende la lampara.",
    "variables_usadas": {"entradas": ["%I1"], "salidas": ["%Q1"],
                         "marcas": [], "registros": []},
}

# 1. Guardar interaccion como pending
eid = app.agregar_ejemplo("Crea un programa donde un boton encienda una lampara", DATOS_MODELO)
assert eid, "agregar_ejemplo no devolvio id"
print(f"1. Ejemplo guardado: {eid}")

# 2. Pending NO debe usarse como contexto todavia
assert app.ejemplos_relevantes("boton que enciende lampara") == []
print("2. Pending no se inyecta (correcto)")

# 3. El usuario corrige
e = app.aplicar_feedback(
    eid, "corrected",
    user_correction="Usa %Q10 en vez de %Q1 y no agregues marcas",
    error_explanation="La salida correcta del tablero es %Q10 (lampara verde)",
    final_ladder_json={
        "programa_nombre": "Boton enciende lampara verde",
        "logica_ladder": [{
            "renglon": 1, "descripcion": "Boton %I1 enciende %Q10",
            "filas": [{"fila": 0, "descripcion": "serie", "elementos": [
                {"tipo": "XIC", "operando": "%I1"},
                {"tipo": "OTE", "operando": "%Q10"},
            ]}],
        }],
        "variables_usadas": {"entradas": ["%I1"], "salidas": ["%Q10"]},
    },
)
assert e["status"] == "corrected"
print(f"3. Feedback aplicado: {e['status']} | tags: {e['tags']}")

# 4. Ahora una peticion similar SI debe recuperarlo
rel = app.ejemplos_relevantes("Haz que un pulsador encienda la lampara verde")
assert len(rel) == 1 and rel[0]["id"] == eid, f"esperaba 1 ejemplo, hubo {len(rel)}"
print(f"4. Recuperado por similitud (uses={app.cargar_memoria()[0]['uses']})")

# 5. Una peticion sin relacion NO debe recuperarlo
assert app.ejemplos_relevantes("suma dos registros con ADD") == []
print("5. Peticion sin relacion no recupera nada (correcto)")

# 6. Bloque para el system prompt
bloque = app.bloque_ejemplos_prompt(rel)
assert "%Q10" in bloque and "Correccion del usuario" in bloque
print("6. Bloque de prompt OK:")
print("-" * 50)
print(bloque[:600])
print("-" * 50)

# 7. Tags automaticos
tags = app.extraer_tags("enclavamiento con paro de emergencia y temporizador de 5 segundos")
assert {"seal-in", "emergency", "timer", "stop"} <= set(tags), tags
print(f"7. Tags automaticos OK: {tags}")

# 8. Poda: llenar mas alla de MAX_EJEMPLOS (prompts distintos para evitar dedupe)
for i in range(app.MAX_EJEMPLOS + 10):
    app.agregar_ejemplo(f"contador de pulsos para la linea{i} de produccion", DATOS_MODELO)
mem = app.cargar_memoria()
assert len(mem) <= app.MAX_EJEMPLOS, f"memoria con {len(mem)} > {app.MAX_EJEMPLOS}"
assert any(m["id"] == eid for m in mem), "la poda elimino un ejemplo corrected (mal)"
print(f"8. Poda OK: {len(mem)} ejemplos (max {app.MAX_EJEMPLOS}), el corrected sobrevivio")

os.remove(os.environ["MEMORIA_JSON"])
print("\nTODAS LAS PRUEBAS PASARON")
