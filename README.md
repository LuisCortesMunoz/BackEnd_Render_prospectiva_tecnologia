# BackEnd_Render_prospectiva_tecnologia

Backend de **LadderVoice**: genera lógica Ladder para PLCs Horner XL4 a partir de
lenguaje natural (texto o voz) usando **Groq**, y la carga al PLC físico por
**Modbus TCP**.

## Arquitectura

El backend cumple **dos roles** según dónde corra:

| Rol | Dónde corre | Qué hace | Necesita |
|---|---|---|---|
| **Nube (Groq)** | Render | Genera lógica/Ladder, chat copiloto y voz a texto | `GROQ_API_KEY` |
| **Puente PLC** | PC local en la red del PLC | Carga programas al PLC por Modbus TCP (`/aplicar-plc`, `/plc/*`) | Estar en la LAN del PLC |

> El PLC vive en una red privada (ej. `192.168.3.12:502`). **Render no puede
> alcanzar esa red** (es internet), por eso la carga al PLC se hace desde un
> backend local. La generación de lógica sí se hace en la nube.

## Endpoints principales

- `POST /generar-logica` — texto → JSON lógico (engine-config) del maletín.
- `POST /generar-ladder` — texto → programa Ladder (esquema del editor).
- `POST /transcribir`, `POST /voz-a-ladder` — voz a texto (Groq Whisper).
- `POST /chat`, `GET /profiles` — copiloto de chat (modo Aprendizaje), 4 perfiles.
- `POST /aplicar-plc` — carga un programa al PLC físico (solo puente local).
- `GET /plc/probar`, `GET /plc/escanear`, `GET/POST /plc/config` — utilidades del PLC.
- `GET /health`, `GET /docs` — estado y documentación interactiva.

## Variables de entorno

| Variable | Para qué | ¿Obligatoria? |
|---|---|---|
| `GROQ_API_KEY` | Generar lógica/Ladder y chat | Sí en Render |
| `GROQ_API_KEY_stt` | Voz a texto (Whisper) | Sí, si usas voz |
| `GROQ_MODEL` | Modelo de generación (default `openai/gpt-oss-120b`) | No |
| `GROQ_CHAT_MODEL` | Modelo del chat (default `llama-3.3-70b-versatile`). Usa solo modelos de **producción** de Groq: los de preview se retiran sin aviso. Si el configurado no existe, el chat responde con el primero disponible de `llama-3.3-70b-versatile`, `openai/gpt-oss-20b`, `llama-3.1-8b-instant` y lo avisa en el log. | No |
| `GROQ_CHAT_MODEL_APRENDIZAJE` | Modelo **solo** del modo Aprendizaje (default `llama-3.3-70b-versatile`). Los demás modos del copiloto siguen con `GROQ_CHAT_MODEL`. | No |
| `GROQ_STT_MODEL` | Modelo de voz (default `whisper-large-v3`) | No |
| `ADMIN_TOKEN` | Protege los endpoints `/admin/*` | Recomendada |
| `BANDA_MOTOR_CONFIGURABLE` | `1` = la banda usa el motor configurable (requiere el ST v6 en el PLC) | No (default `0`) |
| `ALLOWED_ORIGINS` | Dominios del frontend (CORS) | No |
| `LAVO_MEMORIA_REPO` / `LAVO_MEMORIA_TOKEN` | Repo privado de GitHub donde vive la memoria validada (ver «Memoria validada») | Sí, para que la memoria sea persistente |

## Despliegue en la nube (Render)

Comando de arranque:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

Configura al menos `GROQ_API_KEY` (y `GROQ_API_KEY_stt` si usas voz) en
**Render → Environment**. El push a `main` redespliega automáticamente si el
servicio tiene auto-deploy.

## Cargar un programa al PLC físico (puente local)

La carga al PLC se hace **localmente**, en la PC conectada a la red del PLC.
Una página publicada en HTTPS (GitHub Pages) **no puede** contactar un servidor
local por seguridad del navegador, así que el editor también se sirve en local.

En la PC de la red del PLC, abre **dos** ventanas:

1. **Puente al PLC** — doble clic en `iniciar_puente_PLC.bat` (esta carpeta).
   Levanta el backend en `http://localhost:8000`.
   - No necesita `GROQ_API_KEY`: verás un WARNING amarillo de eso, es **normal**.
     El puente queda listo cuando aparece `Uvicorn running on http://0.0.0.0:8000`.

2. **Página local del editor** — doble clic en `iniciar_pagina_local.bat`
   (en la carpeta del frontend `Proyecto_Final_Prospectiva_Tecnologia`).
   Sirve la web y abre el navegador en `http://127.0.0.1:5500/ladder.html`.

Desde esa pestaña local usa el botón **"Cargar al PLC"**. Funciona porque es
`http://127.0.0.1` (origen permitido en CORS) hablando con el puente local, sin
HTTPS de por medio.

> El **chat** y **generar lógica** puedes seguir usándolos desde la página
> publicada (van a Render); el servir local es **solo** para cargar al PLC.

Para detener cualquiera de los dos: `Ctrl + C` en su ventana (se cierra sola).

### Ejecución manual (sin los .bat)

```powershell
# Puente al PLC (carpeta del backend)
uvicorn app:app --host 0.0.0.0 --port 8000

# Página local (carpeta del frontend)
python -m http.server 5500 --bind 127.0.0.1
# luego abre http://127.0.0.1:5500/ladder.html
```

## Banda: motor configurable (ST v6)

El programa maestro v6 de la banda (`cscape/ST_banda_v6_motor_configurable.txt`,
guía en `cscape/GUIA_banda_v6.md`) agrega un **motor configurable**: cualquier
señal (I1, I2, I3, S1, S2, estados de la banda, botones de la app) se conecta con
condiciones AND/OR, timers (TON, TOF, PULSO, RETARDO), contadores (una vez o
cíclicos), memorias set/reset y secuencias de hasta 16 pasos, que manejan el
motor (marcha, paro, pausa, dirección e inversión segura), la torreta y las plumas.

| Pieza | Archivo |
|---|---|
| Contrato, validador y compilador JSON → %R200..%R459 | `banda_motor.py` |
| Simulador scan a scan (espejo del ST) | `banda_motor_sim.py` |
| Intérprete del ST real, para probarlo sin PLC | `banda_st_interprete.py` |
| Carga Modbus (bloque EC, estado ES, verificación v6) | `plc_banda.py` |
| Generación con IA (`SYSTEM_PROMPT_BANDA_MOTOR`) | `app.py` |
| Pruebas: escenarios en simulador y ST real, regresión v5 = v6 | `test_banda_motor.py` |

- `BANDA_MOTOR_CONFIGURABLE=1` en Render: las instrucciones de la banda se generan
  para el motor configurable. Con `0` (por defecto) se mantiene el esquema v5.
- La carga verifica `%R460 = 6`: si el PLC aún tiene el ST v5, se rechaza con un mensaje claro.
- `POST /banda/boton-app` `{boton: 1|2, activo}` → botones virtuales APP1/APP2.
- `GET /banda/estado` incluye `motor_configurable` (motor, dirección, paso, timers, contadores).

## Banda: programa maestro general (ST v7, motor LAVO)

El v7 (`cscape/ST_banda_v7_lavo.txt`, que en Cscape va completo en un solo bloque, compactado para quedar debajo del límite de 64 KB: `LAVO_Main_ST.txt` → STBLOCK1, en *Archivos Cscape*)
conserva el v5 intacto y sustituye el motor v6 por un **motor general**: las
entradas no tienen papel fijo (I1 puede ser arranque, paro, reset o condición),
hay 16 condiciones AND/OR/XOR/NAND/NOR con flancos, 16 memorias, 16 timers (TON,
TOF, PULSO, RETARDO, TONR, INTERMITENTE), 16 contadores (sube/baja, una vez,
cíclico, nivel) y **2 secuencias** independientes de 16 pasos (una vez, infinita
o N repeticiones; inicio, paro, reset y pausa por señal; transiciones por
tiempo, nivel o flanco y una rama alterna por paso). I3 (paro de emergencia), NA/NC
y el enclavamiento de las plumas son fijos en el ST (`§L0`) y no se configuran por Modbus.

La configuración se carga **por transacción**: BEGIN → 640 palabras en el área de
espera (%R1010..%R1649) → relectura → COMMIT (el PLC valida checksum, rangos y
reglas de seguridad y aplica en un solo scan) → confirmación (id, checksum y
relectura de la config activa en %R2000..). Un corte de comunicación nunca deja
una configuración a medias.

| Pieza | Archivo |
|---|---|
| Contrato, validador, compilador determinista, compatibilidad | `lavo_motor.py` |
| Generador del ST y de `LAVO_Variables_Cscape.txt` (mismas constantes) | `cscape/generar_st_v7.py` |
| Simulador que ejecuta el ST real + Modbus simulado | `lavo_sim.py` |
| Carga transaccional, mandos en vivo, detección de versión | `plc_banda.py` |
| Generación con IA y alternativas con confirmación (`SYSTEM_PROMPT_BANDA_V7`) | `app.py` |
| Pruebas (16 escenarios + regresión v5 + equivalencia ST/Python) | `test_lavo_v7.py` |

- `BANDA_MOTOR_V7=1` activa el v7 (tiene prioridad sobre `BANDA_MOTOR_CONFIGURABLE`).
  Con `0` el backend se comporta exactamente como antes.
- Si la instrucción no se puede cumplir tal cual, `/generar-logica` responde
  `needs_confirmation` con lo conservado / modificado / simplificado; `/aplicar-plc`
  y `/banda/config` responden **409** hasta que el usuario acepta la alternativa.
  Sin alternativa segura responde `not_supported`.
- `GET /banda/capacidades`, `POST /banda/v7/registros` (registros exactos sin tocar el PLC),
  `POST /banda/v7/paro`, `/banda/v7/reset`, `/banda/v7/descargar`; `/banda/boton-app` acepta APP1..APP4.
- Si cambias `lavo_motor.py`: `python cscape/generar_st_v7.py --destino "<Archivos Cscape>"`
  y vuelve a descargar el ST.
- Pruebas: `python test_lavo_v7.py` (backend) y, en el frontend, `node tests/test_motor_v7.mjs`.

## Memoria validada y contexto por ramificaciones

El modelo ya no recibe siempre lo mismo. El conocimiento está en tres niveles separados:

| Nivel | Qué es | Dónde vive | Cuándo se usa |
|---|---|---|---|
| 1. Memoria validada | Lógicas que el usuario aprobó con **Guardar lógica validada** (el JSON es lo principal) | Repo privado de GitHub (`LAVO_MEMORIA_REPO`); en local, `memoria_validada/` | Siempre se consulta primero; una idéntica se reutiliza **sin llamar al modelo** |
| 2. Contexto técnico local | La fuente de verdad del hardware | Maletín: `codigos/` (vía `context_json/contexto.json`). Banda: `cscape/LAVO_Referencia_v7.txt` + guía y ejemplos del prompt v7 | Solo si no hay una memoria adecuada |
| 3. Procesos industriales | Referencias genéricas investigadas, **no ejecutables** | `procesos_industriales/*.json` | Solo si la petición es general ("línea de empaquetado") |

Flujo de `/generar-logica`: equipo → memoria idéntica (reuso) → memorias similares → contexto
técnico si no hay memoria → catálogo si la petición es general → prompt → validación de siempre.
Un proceso que no está en el catálogo no se inventa: se pregunta (y la solicitud se registra en
`procesos_solicitados.json` del almacén).

- Código: `memoria_validada.py` (guardar, deduplicar, versionar, buscar), `almacen_memoria.py`
  (GitHub / local), `contexto_ramificado.py` (qué recibe el modelo), `metricas_lavo.py`,
  `validador_esquema.py`. Esquemas en `esquemas/`.
- Endpoints: `POST /memoria/validadas`, `GET /memoria/validadas?dispositivo=banda|maletin`,
  `GET /memoria/validadas/{dispositivo}/{id}`, `POST /memoria/validadas/{dispositivo}/{id}/prueba-fisica`,
  `GET /memoria/metricas`, `GET /procesos-industriales`. Cada respuesta de `/generar-logica`
  trae `analysis.metricas` (tiempos, tokens, llamadas, fuente, contexto usado).
- Antes de guardar, la lógica pasa la misma validación que `/aplicar-plc` (y en v7 la compilación
  a registros). Se registran por separado: aprobación del usuario, pruebas de software y prueba
  física (`no_realizada` salvo que el usuario la declare).

Variables de entorno (Render):

| Variable | Valor |
|---|---|
| `LAVO_MEMORIA_REPO` | `usuario/LAVO-memoria` (repo **privado**, aparte del backend) |
| `LAVO_MEMORIA_TOKEN` | Token *fine-grained* con acceso solo a ese repo, permiso **Contents: Read and write** |
| `LAVO_MEMORIA_RAMA` | Opcional, por defecto `main` |
| `LAVO_CONTEXTO_RAMIFICADO` | `0` vuelve al prompt completo de siempre (sin redeploy) |
| `LAVO_REUSO_MEMORIA` | `0` desactiva la reutilización directa sin modelo |

Sin repo y token la memoria cae a una carpeta local, que en Render **se borra en cada despliegue**:
`/health` lo muestra en `memoria_validada.persistente`.

Pruebas: `python test_memoria_validada.py` (14 escenarios: guardar, reutilizar, similares, catálogo,
separación banda/maletín, persistencia con GitHub simulado y comparación de rendimiento).

## Edición incremental (lógica activa de la sesión)

El front guarda una sola lógica activa por sesión (`assets/js/lavo-sesion.js`) y, en cada
modificación, manda su JSON completo (`contexto.programa_anterior.metadata.engine_config`) y las
instrucciones ya aplicadas (`contexto.historial`). Con lógica activa:

- El modelo recibe ese JSON con reglas de edición: conservar lo no pedido y devolver el JSON completo
  (`edicion_incremental.py`, maletín y banda v7).
- Una memoria validada idéntica no reemplaza la lógica activa: solo se reutiliza en lógicas nuevas.
- Si la versión nueva pierde piezas que la instrucción no pidió quitar (timers, eventos, pasos,
  salidas...), se pide una revisión; si insiste, en la banda queda como alternativa por confirmar
  (`needs_confirmation`) y en el maletín como aviso.

Pruebas: `python test_edicion_incremental.py` (y en el front `node tests/test_sesion_logica.mjs`).

## Aclaraciones (respuestas a `needs_clarification`)

La instrucción llega siempre tal cual la escribió el usuario. Las opciones que elige en las
preguntas viajan aparte, en `aclaraciones` de `POST /generar-logica`:

```json
{"texto": "Mueve la banda hacia la derecha",
 "aclaraciones": {"freq_hz": {"pregunta": "¿A qué frecuencia...?", "respuesta": "20 Hz"}}}
```

`aclaraciones.py` las normaliza (`frecuencia_hz` = `freq_hz`, `salida` = `output`...), se las da al
modelo como datos ya decididos, quita las preguntas ya respondidas (equipo, entrada/salida del
maletín, frecuencia, proceso) y en la banda v7 fija la frecuencia y la dirección elegidas en el JSON.
Con aclaraciones no se reutiliza una memoria idéntica por texto (podría tener otros valores).

## Instalación local

```powershell
pip install -r requirements.txt
```
