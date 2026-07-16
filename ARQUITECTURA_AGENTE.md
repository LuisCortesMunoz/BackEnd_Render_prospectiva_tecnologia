# Arquitectura propuesta — Agente virtual con enrutamiento inteligente

> Documento de diseño (NO implementado aún). Resume la conversación sobre migrar
> el proyecto LadderVoice a una arquitectura de agente autónomo acotado, escalable
> a múltiples PLC. Guardado para no perder la información entre sesiones.

## Veredicto

**Sí se puede implementar sin perder funcionalidad.** La opción recomendada es un
**agente autónomo ACOTADO (bounded agency)**:

- Autonomía plena de **razonamiento**: planea, elige herramientas, encadena expertos,
  se autocorrige en bucle.
- **Frenos deterministas** donde importa: perfiles de dispositivo + validadores +
  un *gate* de confirmación antes de escribir al PLC.
- **NO** es un "bucle libre" (sin tope de iteraciones, sin gate, escribiendo al PLC
  sin permiso). Eso queda como modo opcional bajo responsabilidad del usuario,
  nunca por defecto — porque controla hardware físico.

Incluye los dos objetivos que pidió el usuario: **expertos especializados** y
**manejo de prompts ambiguos**. No son alternativas; son la misma propuesta.

---

## Arquitectura actual (punto de partida)

**Frontend (GitHub Pages, JS vanilla):**
```
texto/voz -> generate.js -> POST /generar-logica -> data.logic (engine-config)
          -> validateLogicJson -> compileLogicToSchema -> normalizeAndValidate -> program -> render
          -> (metadata.engine_config) -> cargarAlPLC -> POST /aplicar-plc
```
- `validate.js` es espejo del validador del backend.
- `chat.js` ya envia contexto: historial[-6] + programa_anterior (con su engine_config).

**Backend (`app.py`, FastAPI + Groq):**
- `POST /generar-logica`: UNA llamada con `SYSTEM_PROMPT_LOGICA` -> engine-config JSON,
  con auto-revision (`MAX_AUTOREVISIONES=3`) y red de seguridad de enclavamiento
  (`es_enclavamiento` + reintento).
- Validadores: `validar_logica_config`, `_validar_secuencia_cfg`, `normalizar_logica_config`.
- Memoria feedback (`memoria/ejemplos.json`): `agregar_ejemplo_logica`, `ejemplos_relevantes`,
  `extraer_tags`, `aplicar_feedback`. HOY NO se inyecta en /generar-logica.
- PLC: `plc_maestro.validar_config / plan_config / aplicar_config` (tercer espejo de validacion).

**Debilidad principal:** todo el razonamiento va en un solo prompt monolitico y NO hay
manejo de ambiguedad (ante "prende una lampara" el modelo inventa I1->Q10).

**Vocabulario fijo actual:** ENGINE_INPUTS {NINGUNA,I1,I2,I3,I4,I7}, ENGINE_OUTPUTS
{Q10,Q11,Q12,VERDE,AMARILLA,ROJA}, ENGINE_MODES {off,directo,enclavado,combinacional},
TIMER_TYPES {on_delay,pulse}, COUNTER_TYPES {up,up_held}, SEQ_MODES {once,loop}, SEQ_MAX_STEPS 8.
Tipos fisicos: I1,I3,I4 = NA ; I2,I7 = NC.

---

## 1. Nucleo de escalabilidad — Perfiles de dispositivo

El cambio que habilita "otros PLC con mas I/O". Todo (I/O, rangos, modos, timers, limites,
mapeo Modbus) se describe en un **perfil**, no hardcodeado. Agregar un PLC = agregar un perfil.

```jsonc
// profiles/maletin_basico.json (uno entre muchos)
{
  "id": "maletin_basico",
  "plc": { "modelo": "Horner XL4", "modbus": { "ip": "192.168.3.12", "port": 502 } },
  "inputs":  [ {"id":"I1","tipo":"NA","clase":"digital"}, {"id":"I2","tipo":"NC","clase":"digital"} ],
  "outputs": [ {"id":"Q10","clase":"digital","label":"verde"} ],
  "registers": [],
  "capabilities": {
    "modes":   ["off","directo","enclavado","combinacional"],
    "timers":  ["on_delay","pulse"],
    "counters":["up","up_held"],
    "sequence": { "enabled": true, "max_steps": 8 },
    "analog": false, "pid": false, "math": false
  },
  "limits": { "preset_s": [0, 32767], "max_inputs_per_output": 2 },
  "modbus_map": { "...mapeo determinista %R<->registro..." }
}
```

Consecuencias:
- Validadores **parametricos por perfil** (no constantes).
- Expertos habilitados por `capabilities` (sin PID -> experto PID no se ofrece).
- Front + back + driver Modbus consumen el MISMO perfil -> mueren los 3 espejos (1 fuente).

---

## 2. El agente autonomo — bucle (ReAct / Plan-Execute)

```
AGENTE PRINCIPAL (dentro de /generar-logica v2)
  1. PERCIBIR    perfil activo + mensaje + memoria + estado
  2. PLANEAR     LLM: sub-objetivos, que expertos/tools, orden, que falta (respeta capabilities)
  3. ACTUAR      ejecuta la SIGUIENTE accion llamando una TOOL
  4. OBSERVAR    lee el resultado real de la tool (exito/errores/plan Modbus)
  5. REFLEXIONAR objetivo cumplido y valido? -> entrega
                 no -> replantea y vuelve a 3 (auto-correccion, limite N)
                 falta dato critico -> sale a preguntar (no inventa)
```

Generaliza tu auto-revision actual a cualquier tipo de logica y perfil.
Limites del bucle: `max_iteraciones` (~4), presupuesto de tokens/llamadas, fallback
(entregar mejor propuesta marcada como no validada + preguntas).

---

## 3. Herramientas deterministas (las "manos")

Regla de oro: **el LLM razona y redacta; las tools deterministas hacen todo lo que toca
hardware o inventa datos criticos.** El agente NUNCA inventa direcciones Modbus ni rangos.

| Tool | Que hace | Origen |
|---|---|---|
| `load_profile(id)` | I/O, capacidades, limites, mapeo | Nuevo |
| `search_memory(texto)` | Ejemplos/configs anteriores | Reusa `ejemplos_relevantes`, `extraer_tags` |
| `expert(tipo, slots, perfil)` | Fragmento de prompt + reglas | Nuevo (modulos) |
| `validate(config, perfil)` | Sintactico + semantico contra perfil | Reusa `validar_logica_config` + capa nueva |
| `compile_ladder(config)` | engine-config -> geometria Ladder | Reusa `compileLogicToSchema` |
| `simulate(program)` | Corre el scan y verifica | Reusa `simulator.js` |
| `plc_dry_run(config, perfil)` | Plan Modbus sin escribir | Reusa `plc_maestro.plan_config` |
| `plc_apply(config, perfil)` | ESCRIBE al PLC (critica) | Reusa `aplicar_config` — tras el gate |
| `ask_user(preguntas)` | Solicita solo lo indispensable | Nuevo (clarify) |

---

## 4. Expertos dinamicos + seleccion por capacidades

Los expertos son **modulos de conocimiento** (prompt + reglas + extraccion/merge de slots),
NO modelos separados. El planificador los selecciona y encadena dinamicamente.

| Experto | Se activa si el perfil tiene... | Aporta |
|---|---|---|
| Temporizadores | capabilities.timers | timer{type,preset_s} |
| Contadores | capabilities.counters | counter{type,preset,reset_input} |
| Enclavamientos | modes:"enclavado" | logic{mode:enclavado,start,stop} |
| Combinacional | modes:"combinacional" | logic{a,b,op} |
| Secuencias/Semaforo | sequence.enabled | bloque sequence{steps} |
| Entradas/Salidas | siempre | mapeo colores<->coil, botones<->input, NA/NC |
| Analogico/PID/Math (futuro) | analog/pid/math | bloques nuevos OPCIONALES |

Agregar experto = agregar modulo + declararlo en los perfiles que lo soportan. Sin tocar el bucle.

### Solicitudes combinadas
El esquema engine-config YA soporta combinacion en una salida:
`logic{mode:enclavado} + timer{...} + counter{...}` en el mismo output.
Se seleccionan varios modulos -> se componen en UN prompt -> un merge determinista resuelve
solapes y aplica reglas duras (sequence excluye timers por salida; counter exige entrada base;
AND sin enable; un timer y un counter por salida).

---

## 5. Memoria (corto y largo plazo)

| Tipo | Contenido | Reusa |
|---|---|---|
| Corto plazo (working) | Conversacion, scratchpad del plan, estado del bucle | historial, programa_anterior |
| Largo plazo episodica | Configs/ejemplos validados por feedback | agregar_ejemplo_logica, ejemplos_relevantes, aplicar_feedback |
| Largo plazo semantica | Que soporta cada PLC | Registro de perfiles |

En modo autonomo la memoria episodica es una TOOL que el agente decide consultar
(p. ej. ante ambiguedad, busca configs previas antes de preguntar).

---

## 6. Autonomia plena CON politica de acciones (autonomo vs confirmacion)

"Autonomia total" = el agente decide COMO llegar al objetivo. QUE acciones ejecuta lo
rige una **politica** (policy.py), no el LLM:

| Nivel | Acciones | Confirmacion |
|---|---|---|
| Verde (Auto) | Percibir, planear, buscar memoria, generar, validar, compilar, simular, mostrar "que entendio", guardar pending | No |
| Amarillo (Auto con auditoria) | Aplicar propuesta desde defaults asumidos; usar config de memoria | Muestra supuestos; reversible |
| Rojo (Confirmacion obligatoria) | plc_apply (Modbus), sobreescribir programa existente | Si — siempre |
| Auto-apply opcional | Escribir al PLC sin preguntar | Solo si el usuario lo activa (opt-in), con validate+dry_run verdes obligatorios |

Regla invariable: nada que escriba al PLC o que dependa de datos criticos inventados
se ejecuta sin pasar por la politica.

### Autonomia total vs bucle libre (aclaracion clave)
- Lo propuesto = autonomia ACOTADA: pleno razonamiento, pero con tope de iteraciones,
  presupuesto y gate en el PLC.
- Bucle libre = sin tope, sin gate, escribe al PLC solo. NO recomendado para hardware
  (riesgo de seguridad/responsabilidad). Es un dial configurable, no el default.

---

## 7. Prompts ambiguos / incompletos (slot-filling)

Campos criticos que NO se pueden inventar: salida (coil), entrada (source/start),
tipo de activacion (mode), condicion de apagado (stop).

El agente decide en el bucle entre:
1. RECUPERAR de memoria/programa_anterior si hay match confiable.
2. PROPONER un default seguro MARCADO como supuesto (no ejecuta).
3. PREGUNTAR solo lo indispensable (max 2-3).

Respuesta: `status:"needs_clarification"` con questions, assumptions, partial_config.
El front muestra chips; NO compila ni carga al PLC hasta confirmar.

---

## 8. Esquema JSON y validacion (retrocompatible + extensible)

- El engine-config actual se conserva 100% (outputs[].logic/timer/counter, sequence, system).
- PLCs futuros -> bloques OPCIONALES activados por capabilities (analog, pid, math, comms).
  Al ser opcionales, el front/validador viejos los ignoran -> cero rupturas.
- Operandos ya no limitados a {I1..I7, Q10..Q12}: se validan contra el PERFIL activo.
- Respuesta en sobre retrocompatible: `{status, logic, analysis, questions, assumptions, ejemplo_id}`.
  generate.js ya hace `logic = data?.logic || data`.
- validate(config, perfil) = validar_logica_config parametrizado + validador semantico
  (counter exige entrada base; sequence<->outputs:[]; AND sin enable; presets en rango del perfil;
  sin salidas repetidas; coherencia NA/NC) + chequeo de capabilities -> plc_dry_run antes de escribir.

---

## 9-10. Sin duplicacion + compatibilidad

- Un perfil, una validacion, un vocabulario -> se eliminan los 3 espejos; los expertos comparten nucleo.
- El bucle vive DENTRO de /generar-logica v2 (fallback al flujo actual). Como la respuesta sigue
  trayendo `logic`, NO cambian: pipeline del front, compileLogicToSchema, renderer, simulador ni
  /aplicar-plc. Aditivo en el front: manejar needs_clarification y (opcional) selector de perfil.

---

## Ejemplo 1 (autonomo) — "I1 activa Q10, enclavada hasta I2, se apaga tras 10 s"

| Iter | Fase | Que ocurre |
|---|---|---|
| - | Percibir | load_profile("maletin_basico") |
| 1 | Planear | intents [enclavamiento, timer]; slots {start:I1, coil:Q10, stop:I2, time_s:10}; sin faltantes |
| 1 | Actuar | expert(enclavamiento)+expert(timer) -> genera output{logic:enclavado(I1,I2), timer:pulse 10s} |
| 1 | Observar | validate -> OK |
| 1 | Reflexionar | simulate confirma: prende con I1, se mantiene, apaga con I2 O a los 10s -> cumplido |
| - | Gate | plc_dry_run -> ROJO: espera confirmacion para plc_apply |

JSON final: Q10 con logic{mode:enclavado,start:I1,stop:I2} + timer{type:pulse,preset_s:10}.
Interpretacion: "se apaga a los 10 s" = timer pulse de 10 s sobre el enclavado.

## Ejemplo 2 (autonomo) — "Hola, quiero prender una lampara"

| Iter | Fase | Que ocurre |
|---|---|---|
| 1 | Planear | faltantes criticos: coil?, input?, activation?, stop? |
| 1 | Actuar | search_memory("encender lampara") -> hay config previa? |
| 1 | Reflexionar | match confiable -> propone (supuesto). Si no -> preguntar |
| - | Salida | status:needs_clarification, questions:[cual lampara?, que boton?, enclavado o momentaneo?] |

No inventa salida/entrada/enclavamiento/paro. No compila ni carga hasta confirmar.

---

## Comparacion de alternativas (dominio ya escalable)

| Enfoque | Precision | Latencia | Costo | Mantenimiento | Complejidad | Integracion |
|---|---|---|---|---|---|---|
| 1. Un modelo, varios prompts | Baja al crecer | Baja | Bajo | Malo al escalar | Baja | Facil |
| 2. Varios modelos independientes | Media | Alta | Alto | Malo | Alta | Dificil |
| 3. Router + tools deterministas | Alta | Muy baja | Muy bajo | Bueno | Media | Facil |
| 4. Multiagente autonomo puro | Alta | Alta | Alto | Medio | Muy alta | Media |
| 5. Agente autonomo + perfiles + tools + memoria + guardrails (RECOMENDADO) | Muy alta y escalable | Media | Medio | Bueno | Alta | Facil (aditivo) |

**Recomendacion: Opcion 5.** La autonomia plena solo es segura y rentable si debajo hay
perfiles + validadores + gate deterministas. Multiagente puro (4) = mismo poder que 5 pero
con mas latencia, costo y no-determinismo, sin ventaja real.

---

## Archivos a crear / modificar (plan)

**Backend — crear:**
- `profiles/` (registro) + `profile_registry.py` — fuente unica de I/O y capacidades.
- `engine_spec.py` — esquema del perfil y del engine-config (reemplaza constantes sueltas).
- `agents/orchestrator.py` — el bucle autonomo (percibir/planear/actuar/observar/reflexionar).
- `agents/planner.py` — planificacion LLM.
- `agents/experts.py` — modulos por experto (prompt + slots + merge), activados por capabilities.
- `agents/tools.py` — envoltura uniforme de las herramientas deterministas.
- `agents/policy.py` — politica autonomo/confirmacion (gate del PLC, auto-apply opt-in).
- `agents/validators.py` — validador semantico parametrico por perfil.

**Backend — modificar:**
- `app.py` -> /generar-logica v2 delega en el orquestador; LogicaResponse gana campos OPCIONALES
  (status/questions/assumptions/analysis); recibe profile_id. Reusa validar_logica_config,
  es_enclavamiento, ejemplos_relevantes, extraer_tags, agregar_ejemplo_logica.
- `plc_maestro.py` -> parametrizar el mapeo por perfil (hoy fija %R de Horner); mantener API.

**Frontend — modificar (minimo):**
- `chat.js` -> manejar needs_clarification (chips + confirmar) y opcional plan/pasos del agente.
- `validate.js` / `maletin_basico.json` -> consumir el perfil como fuente unica.
- renderer.js, simulator.js, codec.js, compiler/ -> sin cambios (o extensiones opcionales).

---

## Fases (para no romper nada)

0. **Perfiles:** extraer I/O y capacidades a maletin_basico.json como fuente unica y parametrizar
   validadores. Habilita todo lo demas; riesgo bajo.
1. **Guardrails + Clarify:** politica de acciones + needs_clarification. Resuelve ambiguedad y
   asegura el PLC.
2. **Bucle autonomo:** planner + tools + reflexion/auto-correccion (generaliza la auto-revision).
3. **Expertos dinamicos + memoria reactivada** en el flujo nuevo.
4. **Nuevos PLC:** agregar perfiles y expertos (analogico/PID) sin tocar el cerebro.

Estado actual: SOLO DISENO. No se ha modificado codigo de la app para esto.
Restriccion del usuario: no implementar todavia; primero validar arquitectura.
