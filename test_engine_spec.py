# test_engine_spec.py — prueba OFFLINE de la Fase 0 (perfiles de dispositivo).
#
# Garantiza que el vocabulario DERIVADO del perfil maletin_basico.json
# (via engine_spec) es EXACTAMENTE igual a las constantes que hoy tiene app.py.
# Si esto pasa, integrar el perfil no cambia el comportamiento del validador.
#
# Uso:  python test_engine_spec.py   (no necesita red ni GROQ_API_KEY)

import os

os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import app
import engine_spec
from profile_registry import perfil_por_defecto, cargar_perfil, listar_perfiles

perfil = perfil_por_defecto()

# 1. Conjuntos de vocabulario == constantes actuales de app.py
assert engine_spec.entradas_validas(perfil) == app.ENGINE_INPUTS, \
    f"entradas: {engine_spec.entradas_validas(perfil)} != {app.ENGINE_INPUTS}"
print("1. ENGINE_INPUTS  OK:", sorted(app.ENGINE_INPUTS))

assert engine_spec.salidas_validas(perfil) == app.ENGINE_OUTPUTS, \
    f"salidas: {engine_spec.salidas_validas(perfil)} != {app.ENGINE_OUTPUTS}"
print("2. ENGINE_OUTPUTS OK:", sorted(app.ENGINE_OUTPUTS))

assert engine_spec.modos(perfil) == app.ENGINE_MODES
print("3. ENGINE_MODES   OK:", sorted(app.ENGINE_MODES))

assert engine_spec.tipos_timer(perfil) == app.TIMER_TYPES
print("4. TIMER_TYPES    OK:", sorted(app.TIMER_TYPES))

assert engine_spec.tipos_counter(perfil) == app.COUNTER_TYPES
print("5. COUNTER_TYPES  OK:", sorted(app.COUNTER_TYPES))

assert engine_spec.modos_secuencia(perfil) == app.SEQ_MODES
print("6. SEQ_MODES      OK:", sorted(app.SEQ_MODES))

assert engine_spec.max_pasos_secuencia(perfil) == app.SEQ_MAX_STEPS
print("7. SEQ_MAX_STEPS  OK:", app.SEQ_MAX_STEPS)

# 2. Canonicalizacion de salidas identica a la de validar_logica_config
for nombre, esperado in [("Q10", "Q10"), ("VERDE", "Q10"),
                         ("Q11", "Q11"), ("AMARILLA", "Q11"),
                         ("Q12", "Q12"), ("ROJA", "Q12")]:
    got = engine_spec.canon_salida(perfil, nombre)
    assert got == esperado, f"canon {nombre}: {got} != {esperado}"
print("8. Canonicalizacion de salidas OK (Q10/Q11/Q12 y alias)")

# 3. Rangos de preset coinciden con los del validador actual
assert engine_spec.rango_timer(perfil, "on_delay") == [0, 32767]
assert engine_spec.rango_timer(perfil, "pulse")    == [1, 32767]
assert engine_spec.rango_counter(perfil, "up")       == [0, 32767]
assert engine_spec.rango_counter(perfil, "up_held")  == [1, 32767]
assert engine_spec.rango_duracion_secuencia(perfil)  == [1, 32767]
print("9. Rangos de preset (timer/counter/duracion) OK")

# 4. El registro descubre el perfil
assert "maletin_basico" in listar_perfiles()
assert cargar_perfil("maletin_basico")["id"] == "maletin_basico"
print("10. Registro de perfiles OK:", listar_perfiles())

print("\nTODAS LAS PRUEBAS DE FASE 0 PASARON")
