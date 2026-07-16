# app.py — LadderVoice Backend v2.0  (archivo unificado)
# Generador : Groq (openai/gpt-oss-120b)
# STT       : Groq Whisper
# Contexto  : context_json/contexto.json  (generado por preparar_contexto.py)
# Historial : respuestas/historial.json   (persistente entre reinicios)
# Memoria   : memoria/ejemplos.json       (feedback del usuario → mejores respuestas)

import os, re, json, time, logging, datetime, threading
import socket, ipaddress, subprocess, platform
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq, APIStatusError

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
log = logging.getLogger(__name__)

# ─── Modelos y clientes Groq ─────────────────────────────────────

MODELO        = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
MODELO_STT    = os.environ.get("GROQ_STT_MODEL",    "whisper-large-v3")

# gpt-oss-120b es un modelo razonador: sus tokens de razonamiento interno
# cuentan dentro de max_tokens. Con 1300 el razonamiento agotaba el
# presupuesto y el JSON salia vacio -> Groq respondia 400 json_validate_failed
# con failed_generation: ''. 4096 es el valor probado en plc-llm-assistant
# (test_con_contexto.py v10) que funciona con este mismo modelo.
MAX_COMPLETION_TOKENS = int(os.environ.get("MAX_COMPLETION_TOKENS", "4096"))
IDIOMA_STT    = os.environ.get("GROQ_STT_LANGUAGE", "es")
MAX_AUDIO_MB  = int(os.environ.get("MAX_AUDIO_MB",  "25"))

GROQ_API_KEY     = os.environ.get("GROQ_API_KEY")
GROQ_API_KEY_STT = os.environ.get("GROQ_API_KEY_stt")
ADMIN_TOKEN      = os.environ.get("ADMIN_TOKEN", "")

# El puente local al PLC (Modbus) no necesita Groq. Si la llave no esta
# presente (p. ej. corriendo en la red del PLC, con las llaves solo en
# Render), el servidor debe arrancar igual: la generacion se hace en Render
# y aqui solo se usan los endpoints /plc/*. Por eso NO se crea el cliente sin
# llave (Groq() lanzaria al iniciar y tumbaria el puente).
groq_client     = Groq(api_key=GROQ_API_KEY) if GROQ_API_KEY else None
groq_client_stt = Groq(api_key=GROQ_API_KEY_STT) if GROQ_API_KEY_STT else None
if groq_client is None:
    log.warning("GROQ_API_KEY no configurada: la generacion (Groq/STT) estara "
                "deshabilitada. Los endpoints del puente al PLC (/plc/*) si funcionan.")

# ─── Rutas de archivos ────────────────────────────────────────────

CONTEXTO_JSON_PATH = os.environ.get("CONTEXTO_JSON", "context_json/contexto.json")
HISTORIAL_PATH     = "respuestas/historial.json"
MEMORIA_PATH       = os.environ.get("MEMORIA_JSON", "memoria/ejemplos.json")

# Cuantos pares Q&A mantener en RAM (contexto activo del modelo).
# Limitado a 2: el plan gratuito de Groq permite 8000 tokens/minuto y el
# historial con JSONs completos desbordaba ese limite (error 413).
MAX_HISTORY   = 2
# Cuantas entradas guardar en historial.json (memoria a largo plazo)
MAX_HISTORIAL = 50
# Maximo de ejemplos en memoria/ejemplos.json y cuantos se inyectan por peticion
MAX_EJEMPLOS        = int(os.environ.get("MAX_EJEMPLOS", "100"))
MAX_EJEMPLOS_PROMPT = 3
# Auto-revision en /generar-logica: cuantas veces el agente vuelve a generar
# corrigiendo sus PROPIOS errores de validacion antes de rendirse. Cada intento
# extra es otra llamada a Groq, por eso se mantiene bajo (3).
MAX_AUTOREVISIONES  = int(os.environ.get("MAX_AUTOREVISIONES", "3"))

# ─── Variables PLC ────────────────────────────────────────────────

VARIABLES_PLC = """
PLC     : Horner XL4 / XC1E5 | Software: Cscape 10.2 | 24VDC
Red     : IP 192.168.3.12 | Puerto Modbus TCP 502

ENTRADAS (%I):
  %I1: Boton NA arranque    %I2: Boton NC paro
  %I3: Selector/reset       %I4: Selector
  %I8: Paro emergencia NC

SALIDAS (%Q):
  %Q10: Lampara verde   %Q11: Lampara amarilla   %Q12: Lampara roja

MARCAS (%M): bits internos    REGISTROS (%R): palabras 16 bits
"""

SYSTEM_PROMPT_BASE = """Eres un experto en PLCs Horner XL4/XC1E5 programados con Cscape en lenguaje Ladder.

VARIABLES DEL SISTEMA:
{variables}

PROGRAMAS DE REFERENCIA:
{contexto}

REGLAS DE RESPUESTA:
- Responde SOLO con JSON valido, sin texto adicional.
- Usa siempre el campo "filas" dentro de cada renglon.
- "fila 0" es la logica serie principal.
- "fila 1, 2..." son ramas paralelas.
- Tipos: XIC, XIO, OTE, OTL, OTU, TON, TOF, CTU, CTD, CMP, MOV, ADD.
- TON/TOF llevan "parametros": {{"PT_ms": <milisegundos>}}.
- CTU/CTD llevan "parametros": {{"PV": <cuentas>}}.
- Usa el valor que pida el usuario (ej. 5 segundos -> PT_ms 5000).
- Operandos con %: %I1, %Q10, %M1, %R1.
- Paros NC (%I2, %I8) siempre van como XIO en fila 0."""

ESQUEMA = """\

Responde con este esquema JSON exacto:
{
  "programa_nombre": "string",
  "logica_ladder": [
    {
      "renglon": 1,
      "descripcion": "string",
      "filas": [
        {
          "fila": 0,
          "descripcion": "string",
          "elementos": [
            {"tipo": "XIC", "operando": "%I1", "descripcion": "string"},
            {"tipo": "TON", "operando": "%R1", "descripcion": "string",
             "parametros": {"PT_ms": 5000}},
            {"tipo": "CTU", "operando": "%R2", "descripcion": "string",
             "parametros": {"PV": 10}}
          ]
        }
      ]
    }
  ],
  "explicacion_simple": "string",
  "implementacion_cscape": ["paso1", "paso2"],
  "codigo_python_modbus": "string o null",
  "variables_usadas": {
    "entradas": ["%I1"], "salidas": ["%Q10"], "marcas": [], "registros": []
  }
}"""

PALABRAS_ENCLAVAMIENTO = [
    "enclav", "latch", "retenc", "arranque", "paro", "marcha",
    "mantenga", "mantenerse", "soltar", "auto", "memoria",
]

VERIFICACION_ENCLAVAMIENTO = """
VERIFICACION OBLIGATORIA PARA ENCLAVAMIENTO:
Un enclavamiento real SIEMPRE necesita una rama paralela de auto-retencion.
  fila 0: [XIC arranque] [XIO paro] [XIO emergencia] -> (OTE bobina)
  fila 1: [XIC bobina]   <- contacto de memoria, IGUAL operando que la bobina
"""

# ─── Contrato "JSON logico simple" (engine-config dual) ───────────
# Este es el flujo NUEVO: /generar-logica. La IA NO genera geometria ladder
# ni registros; solo describe la configuracion del motor logico de funcion
# fija del PLC (maletin), que luego ejecuta el codigo Python (clase XL4) y
# que el frontend dibuja con su compilador determinista.

ENGINE_INPUTS  = {"NINGUNA", "I1", "I2", "I3", "I4", "I7"}
ENGINE_OUTPUTS = {"Q10", "Q11", "Q12", "VERDE", "AMARILLA", "ROJA"}
ENGINE_MODES   = {"off", "directo", "enclavado", "combinacional"}
TIMER_TYPES    = {"on_delay", "pulse"}
COUNTER_TYPES  = {"up", "up_held"}
# Secuenciador de pasos (capa opcional para semaforos / secuencias temporizadas)
SEQ_MODES      = {"once", "loop"}
SEQ_MAX_STEPS  = 8

SYSTEM_PROMPT_LOGICA = """Eres el motor de interpretacion de un PLC Horner XL4 de un maletin de laboratorio.
Traduces una instruccion en lenguaje natural a un JSON de CONFIGURACION (no generas geometria ladder ni codigo).

HARDWARE FIJO (no inventes nada fuera de esto):
- Salidas (lamparas): Q10 (verde), Q11 (amarilla), Q12 (roja). Solo estas 3.
- Entradas (botones): I1, I2, I3, I4, I7. Solo estas 5. NINGUNA = sin entrada.
  Tipo fisico (no lo decides tu): I1,I3,I4 = NA ; I2,I7 = NC.
- No existen %M, %R, %Q, I5, I6, I8 ni expresiones booleanas arbitrarias.

CADA salida tiene UNA logica base (elige una):
  - "off"            -> apagada.
  - "directo"        -> source (entrada), enable opcional. La salida sigue a source
                        SOLO mientras la entrada este activa (al soltar, se apaga).
  - "enclavado"      -> start (arranque), stop opcional (paro), enable opcional.
                        AUTO-RETENCION: la salida se queda ENCENDIDA al soltar el
                        arranque, y solo se apaga con el paro (stop).
  - "combinacional"  -> a, b (dos entradas), op "OR"|"AND", latched opcional, stop opcional.
                        Usa latched:true cuando la combinacion debe QUEDARSE encendida.
                        En "AND" NO se permite enable (el 2do operando ocupa ese lugar).
Solo se pueden combinar 2 entradas como maximo por salida.

CUANDO USAR "enclavado" (MUY IMPORTANTE):
Si el usuario usa palabras como enclavar, enclavamiento, se queda/quede encendida,
se mantiene/mantenga, permanece, sigue prendida, retencion, sello, latch, o describe
un arranque-paro (un boton enciende y OTRO apaga), entonces DEBES usar mode "enclavado"
(no "directo"). "directo" es solo para "mientras presione". Si hay dos entradas que
encienden y debe quedar enclavado, usa "combinacional" con latched:true.
El paro va en "stop" (no inventes paro si el usuario no lo menciona).

EXCEPCION IMPORTANTE (contadores): si el "se enclava / se queda encendida / se mantiene"
ocurre AL LLEGAR A UN CONTEO (ej. "cuenta 5 pulsos de I1 y enciende la verde", "al pulsar
I1 cinco veces se enclava la lampara"), NO uses base "enclavado". Usa logica base "directo"
con source = la entrada que se cuenta, MAS counter {"type":"up_held","preset":N}. El
enclavamiento lo hace el contador (up_held), no la logica base. Si pusieras "enclavado",
la salida se encenderia desde el PRIMER pulso en vez de al llegar al conteo.

CAPAS OPCIONALES por salida (independientes):
  - timer:   {"type":"on_delay"|"pulse","preset_s":N}  (segundos enteros; on_delay 0..32767, pulse 1..32767)
  - counter: {"type":"up"|"up_held","preset":N,"reset_input":"I4"|null}  (up 0..32767, up_held 1..32767)
    El contador cuenta los PULSOS de la entrada de la logica base (el "source" si la base
    es "directo", el "start" si es "enclavado"). Esa entrada base es la que se cuenta, por
    eso una salida con counter SIEMPRE necesita una logica base con una entrada: NUNCA "off"
    (con "off" no hay nada que contar y la salida jamas prende).
      - "up"      -> la salida prende SOLO mientras la entrada base siga activa Y conteo >= preset.
      - "up_held" -> la salida se ENCIENDE y SE QUEDA encendida al llegar el conteo al preset,
                     aunque sueltes la entrada; solo se apaga con el reset_input.
    reset_input = entrada fisica que regresa el conteo a cero (opcional).

GLOBAL:
  - system.enable (bool, normalmente true) y system.global_stop (entrada de paro general o null).

SECUENCIAS TEMPORIZADAS (semaforo / pasos uno tras otro) — MUY IMPORTANTE:
La logica por salida de arriba NO puede encadenar etapas (una salida no puede
disparar a otra, y solo tiene UN temporizador). Para peticiones donde las salidas
se encienden EN ORDEN, una tras otra, con transiciones por TIEMPO automaticas
(ej. semaforo: verde 5 s, luego amarilla 5 s, luego roja 5 s), NO uses timers por
salida: usa el bloque de nivel superior "sequence" y deja "outputs": [].

Reconoce una secuencia cuando el usuario diga: secuencia, semaforo, "uno tras otro",
"luego/despues se enciende otra", "primero ... despues ... despues ...", etapas con
tiempos, "nunca al mismo tiempo", "se ejecuta una sola vez al presionar".

Esquema de "sequence":
  {
    "start": "I1",          // entrada que ARRANCA la secuencia (obligatoria)
    "mode": "once",         // "once" = una vez por pulsacion ; "loop" = ciclico/se repite
    "reset": null,          // entrada que aborta/reinicia (opcional, normalmente null)
    "steps": [              // 1..8 pasos EN ORDEN; cada paso dura sus segundos y avanza solo
      {"outputs": ["Q10"], "duration_s": 5},
      {"outputs": ["Q11"], "duration_s": 5},
      {"outputs": ["Q12"], "duration_s": 5}
    ]
  }
Reglas de la secuencia:
- "outputs" de cada paso = salidas ENCENDIDAS durante ese paso (normalmente una sola,
  para que "nunca al mismo tiempo"). duration_s = segundos enteros (1..32767).
- Al terminar el ultimo paso: con "once" todo se apaga (y vuelve a correr en la
  siguiente pulsacion); con "loop" regresa al paso 1.
- Cuando uses "sequence", el arreglo "outputs" del nivel superior va VACIO: [].
- Mapea colores igual: verde->Q10, amarilla->Q11, roja->Q12.

REGLAS DE RESPUESTA:
- Responde SOLO con JSON valido, sin texto extra ni ```.
- Usa unicamente las entradas/salidas que el usuario menciona. No agregues paros ni entradas extra.
- Mapea colores: verde->Q10, amarilla->Q11, roja->Q12.
- Cada salida incluye un campo "expr" legible (gramatica: * AND, + OR, ! NOT, operandos I1.. y la propia salida para sello) y un "comment" breve. Estos son solo para mostrar; la verdad es la config.

ESQUEMA EXACTO:
{
  "name": "string",
  "device_profile": "maletin_basico",
  "reset_before": true,
  "system": { "enable": true, "global_stop": null },
  "outputs": [
    {
      "output": "Q10",
      "logic": { "mode": "enclavado", "start": "I1", "stop": "I2" },
      "timer": null,
      "counter": null,
      "expr": "(I1 + Q10) * !I2",
      "comment": "I1 enciende y enclava la verde; I2 la apaga"
    },
    {
      "output": "Q11",
      "logic": { "mode": "combinacional", "a": "I1", "b": "I3", "op": "OR" },
      "timer": { "type": "pulse", "preset_s": 5 },
      "counter": null,
      "expr": "I1 + I3",
      "comment": "I1 o I3 encienden la amarilla 5 s"
    }
  ]
}

EJEMPLO de enclavamiento (peticion -> JSON):
Peticion: "El boton verde enciende la lampara verde y se queda prendida; el rojo la apaga."
JSON: {"name":"Enclavamiento verde","device_profile":"maletin_basico","reset_before":true,
 "system":{"enable":true,"global_stop":null},
 "outputs":[{"output":"Q10","logic":{"mode":"enclavado","start":"I1","stop":"I2"},
 "timer":null,"counter":null,"expr":"(I1 + Q10) * !I2","comment":"enclavamiento verde"}]}

EJEMPLO de contador (peticion -> JSON):
Peticion: "Cuenta los pulsos de I1; al llegar a 5 la lampara verde se enciende y se queda enclavada."
JSON: {"name":"Contador 5 verde","device_profile":"maletin_basico","reset_before":true,
 "system":{"enable":true,"global_stop":null},
 "outputs":[{"output":"Q10","logic":{"mode":"directo","source":"I1"},
 "timer":null,"counter":{"type":"up_held","preset":5,"reset_input":null},
 "expr":"I1","comment":"cuenta 5 pulsos de I1 y enclava la verde"}]}
Nota: la base es "directo" con source I1 (la entrada que se cuenta) y el enclavamiento lo da
"up_held". NO se usa logica "enclavado" aqui.

EJEMPLO de secuencia / semaforo (peticion -> JSON):
Peticion: "Al presionar I1 enciende la verde 5 s, luego la amarilla 5 s, luego la roja 5 s,
una sola vez y nunca al mismo tiempo."
JSON: {"name":"Semaforo I1","device_profile":"maletin_basico","reset_before":true,
 "system":{"enable":true,"global_stop":null},
 "sequence":{"start":"I1","mode":"once","reset":null,
   "steps":[{"outputs":["Q10"],"duration_s":5},
            {"outputs":["Q11"],"duration_s":5},
            {"outputs":["Q12"],"duration_s":5}]},
 "outputs":[]}
Nota: se usa "sequence" (no timers por salida) y "outputs" va vacio. Cada paso enciende UNA
salida por su duracion y avanza solo; "once" = se ejecuta una vez por cada pulsacion de I1."""


def _expr_de_logica(lg: dict, salida: str) -> str:
    """Deriva un 'expr' legible a partir de la logica del motor (para el
    frontend). El motor sigue siendo la fuente de verdad."""
    mode = str(lg.get("mode", "off")).lower()
    if mode == "off":
        return "0"
    if mode == "directo":
        e = str(lg.get("source") or "")
        if lg.get("enable"):
            e = f"{e} * {lg['enable']}"
        return e or "0"
    if mode == "enclavado":
        start = str(lg.get("start") or "")
        e = f"({start} + {salida})"
        if lg.get("stop"):
            e += f" * !{lg['stop']}"
        if lg.get("enable"):
            e += f" * {lg['enable']}"
        return e
    if mode == "combinacional":
        a, b = str(lg.get("a") or ""), str(lg.get("b") or "")
        op = "+" if str(lg.get("op", "OR")).upper() == "OR" else "*"
        base = f"{a} {op} {b}"
        if lg.get("latched"):
            base = f"({base} + {salida})"
        if lg.get("stop"):
            base = f"({base}) * !{lg['stop']}"
        return base
    return "0"


def _entrada_valida(nombre) -> bool:
    return nombre is None or str(nombre).upper() in ENGINE_INPUTS


def validar_logica_config(cfg: dict) -> list:
    """Valida el JSON dual contra el hardware fijo del maletin. Devuelve la
    lista de errores (vacia = ok). Espejo del validador de Python (XL4)."""
    errores = []
    if not isinstance(cfg, dict):
        return ["El JSON raiz no es un objeto."]
    outputs = cfg.get("outputs")
    seq = cfg.get("sequence")
    if not isinstance(outputs, list):
        outputs = []
    # Una config valida necesita al menos salidas O una secuencia.
    if not outputs and not seq:
        return ["Falta 'outputs' o esta vacio: debe haber al menos una salida (o una 'sequence')."]

    vistos = set()
    for i, o in enumerate(outputs):
        tag = f"salida {i+1}"
        if not isinstance(o, dict):
            errores.append(f"{tag}: no es un objeto."); continue
        sal = str(o.get("output", "")).upper()
        tag = f"salida {i+1} ({sal})"
        if sal not in ENGINE_OUTPUTS:
            errores.append(f"{tag}: salida invalida. Usa Q10, Q11 o Q12.")
        else:
            canon = "Q10" if sal in ("Q10", "VERDE") else "Q11" if sal in ("Q11", "AMARILLA") else "Q12"
            if canon in vistos:
                errores.append(f"{tag}: salida repetida.")
            vistos.add(canon)

        lg = o.get("logic") or {"mode": "off"}
        mode = str(lg.get("mode", "off")).lower()
        if mode not in ENGINE_MODES:
            errores.append(f"{tag}: mode '{mode}' invalido.")
        if mode == "directo":
            if not lg.get("source"):
                errores.append(f"{tag}: 'directo' requiere 'source'.")
            for c in ("source", "enable"):
                if not _entrada_valida(lg.get(c)):
                    errores.append(f"{tag}: '{c}'='{lg.get(c)}' no es entrada valida.")
        elif mode == "enclavado":
            if not lg.get("start"):
                errores.append(f"{tag}: 'enclavado' requiere 'start'.")
            for c in ("start", "stop", "enable"):
                if not _entrada_valida(lg.get(c)):
                    errores.append(f"{tag}: '{c}'='{lg.get(c)}' no es entrada valida.")
        elif mode == "combinacional":
            if not lg.get("a") or not lg.get("b"):
                errores.append(f"{tag}: 'combinacional' requiere 'a' y 'b'.")
            for c in ("a", "b", "stop"):
                if not _entrada_valida(lg.get(c)):
                    errores.append(f"{tag}: '{c}'='{lg.get(c)}' no es entrada valida.")
            if str(lg.get("op", "OR")).upper() not in ("OR", "AND"):
                errores.append(f"{tag}: 'op' debe ser OR o AND.")
            if str(lg.get("op", "OR")).upper() == "AND" and lg.get("enable"):
                errores.append(f"{tag}: en AND no se permite 'enable'.")

        tm = o.get("timer")
        if tm:
            if str(tm.get("type")).lower() not in TIMER_TYPES:
                errores.append(f"{tag}: timer.type debe ser on_delay o pulse.")
            else:
                low = 0 if str(tm["type"]).lower() == "on_delay" else 1
                try:
                    v = int(tm.get("preset_s"))
                    if v < low or v > 32767:
                        errores.append(f"{tag}: timer.preset_s {v} fuera de [{low},32767].")
                except (TypeError, ValueError):
                    errores.append(f"{tag}: timer.preset_s no es entero.")
        ct = o.get("counter")
        if ct:
            if str(ct.get("type")).lower() not in COUNTER_TYPES:
                errores.append(f"{tag}: counter.type debe ser up o up_held.")
            else:
                low = 0 if str(ct["type"]).lower() == "up" else 1
                try:
                    v = int(ct.get("preset"))
                    if v < low or v > 32767:
                        errores.append(f"{tag}: counter.preset {v} fuera de [{low},32767].")
                except (TypeError, ValueError):
                    errores.append(f"{tag}: counter.preset no es entero.")
                if not _entrada_valida(ct.get("reset_input")):
                    errores.append(f"{tag}: counter.reset_input invalido.")

    if seq is not None:
        errores.extend(_validar_secuencia_cfg(seq))

    sysc = cfg.get("system") or {}
    if not _entrada_valida(sysc.get("global_stop")):
        errores.append(f"system.global_stop='{sysc.get('global_stop')}' invalido.")
    return errores


def _validar_secuencia_cfg(seq) -> list:
    """Valida el bloque 'sequence' (secuenciador de pasos). Espejo del
    validador de plc_maestro y del secuenciador en Texto Estructurado."""
    errores = []
    if not isinstance(seq, dict):
        return ["'sequence' no es un objeto."]
    if not seq.get("start") or not _entrada_valida(seq.get("start")):
        errores.append(f"sequence.start='{seq.get('start')}' debe ser una entrada valida (I1..I7).")
    if str(seq.get("mode", "once")).lower() not in SEQ_MODES:
        errores.append(f"sequence.mode='{seq.get('mode')}' debe ser 'once' o 'loop'.")
    if seq.get("reset") is not None and not _entrada_valida(seq.get("reset")):
        errores.append(f"sequence.reset='{seq.get('reset')}' no es una entrada valida.")
    steps = seq.get("steps")
    if not isinstance(steps, list) or not steps:
        errores.append("sequence.steps debe ser una lista con al menos un paso.")
    elif len(steps) > SEQ_MAX_STEPS:
        errores.append(f"sequence.steps no puede tener mas de {SEQ_MAX_STEPS} pasos.")
    else:
        for i, st in enumerate(steps):
            etq = f"sequence paso {i+1}"
            if not isinstance(st, dict):
                errores.append(f"{etq}: no es un objeto."); continue
            outs = st.get("outputs")
            if not isinstance(outs, list) or not outs:
                errores.append(f"{etq}: 'outputs' debe listar al menos una salida.")
            else:
                for o in outs:
                    if str(o).upper() not in ENGINE_OUTPUTS:
                        errores.append(f"{etq}: salida '{o}' invalida. Usa Q10, Q11 o Q12.")
            try:
                v = int(st.get("duration_s"))
                if v < 1 or v > 32767:
                    errores.append(f"{etq}: duration_s {v} fuera de [1,32767].")
            except (TypeError, ValueError):
                errores.append(f"{etq}: duration_s no es entero.")
    return errores


def normalizar_logica_config(cfg: dict) -> dict:
    """Completa campos por defecto y garantiza 'expr'/'comment' por salida
    para que el JSON dual viaje completo al frontend."""
    cfg.setdefault("name", "Programa maletin")
    cfg.setdefault("device_profile", "maletin_basico")
    cfg.setdefault("reset_before", True)
    sysc = cfg.setdefault("system", {})
    sysc.setdefault("enable", True)
    sysc.setdefault("global_stop", None)
    cfg.setdefault("outputs", [])
    for o in cfg.get("outputs", []):
        o["output"] = str(o.get("output", "")).upper()
        lg = o.setdefault("logic", {"mode": "off"})
        lg["mode"] = str(lg.get("mode", "off")).lower()
        o.setdefault("timer", None)
        o.setdefault("counter", None)
        if not o.get("expr"):
            o["expr"] = _expr_de_logica(lg, o["output"])
        o.setdefault("comment", "")
    seq = cfg.get("sequence")
    if isinstance(seq, dict):
        seq["mode"] = str(seq.get("mode", "once")).lower()
        seq.setdefault("reset", None)
        for st in seq.get("steps", []):
            if isinstance(st, dict) and isinstance(st.get("outputs"), list):
                st["outputs"] = [str(o).upper() for o in st["outputs"]]
    return cfg

# ─── Carga de contexto JSON ──────────────────────────────────────

def cargar_contexto_json(ruta: str = CONTEXTO_JSON_PATH) -> dict:
    if not os.path.exists(ruta):
        log.warning(
            f"No se encontro '{ruta}'. "
            "Ejecuta  python preparar_contexto.py  y sube el JSON a git."
        )
        return {}
    with open(ruta, encoding="utf-8") as f:
        return json.load(f)


def construir_system_prompt(contexto_json: dict) -> str:
    """Convierte el JSON de programas en el texto del system prompt."""
    programas_texto = ""
    for prog in contexto_json.get("programas", []):
        nombre = prog.get("nombre", "")
        desc   = prog.get("descripcion", "")
        programas_texto += f"\n{'='*50}\nPROGRAMA: {nombre}\n{'='*50}\n"
        if desc:
            programas_texto += f"[DESCRIPCION]\n{desc.strip()[:800]}\n\n"
        programas_texto += "[LOGICA LADDER]\n"
        for r in prog.get("renglones", []):
            programas_texto += f"  Renglon {r.get('numero','?')}: {r.get('descripcion','')}\n"
            for el in r.get("fila_0", []):
                programas_texto += f"    fila0 -> {el['tipo']} {el['operando']}"
                if el.get("parametros"):
                    programas_texto += f" {el['parametros']}"
                programas_texto += "\n"
            for j, fp in enumerate(r.get("filas_paralelas", []), 1):
                for el in fp:
                    programas_texto += f"    fila{j}(par) -> {el['tipo']} {el['operando']}\n"

    if not programas_texto.strip():
        programas_texto = "(sin programas de referencia — ejecuta preparar_contexto.py)"

    return SYSTEM_PROMPT_BASE.format(
        variables=VARIABLES_PLC.strip(),
        contexto=programas_texto.strip(),
    )

# ─── Historial persistente ────────────────────────────────────────

def cargar_historial(max_pares: int = MAX_HISTORY) -> list:
    """
    Carga los ultimos max_pares pares Q&A del archivo historial.json
    y los devuelve como lista de mensajes {role, content}.
    """
    try:
        if not os.path.exists(HISTORIAL_PATH):
            return []
        with open(HISTORIAL_PATH, encoding="utf-8") as f:
            datos = json.load(f)
        mensajes = []
        for entry in datos[-max_pares:]:
            mensajes.append({"role": "user",      "content": entry["pregunta"]})
            mensajes.append({"role": "assistant",  "content": entry["respuesta"]})
        log.info(f"Historial cargado — {len(mensajes)//2} pares previos")
        return mensajes
    except Exception as e:
        log.warning(f"No se pudo cargar historial: {e}")
        return []


def resumen_para_historial(datos: dict) -> str:
    """Version compacta de la respuesta del modelo para el historial.
    El JSON completo pesa 1000-1500 tokens por respuesta y desbordaba el
    limite de 8000 tokens/minuto de Groq; este resumen pesa ~10x menos y
    conserva lo necesario para dar continuidad a la conversacion."""
    lineas = [f"Programa: {datos.get('programa_nombre', '')}"]
    for r in datos.get("logica_ladder", []):
        filas = []
        for fila in r.get("filas", []):
            els = " ".join(f"{e.get('tipo', '')} {e.get('operando', '')}"
                           for e in fila.get("elementos", []))
            filas.append(f"fila{fila.get('fila', 0)}: {els}")
        lineas.append(f"Renglon {r.get('renglon', '?')}: " + " | ".join(filas))
    exp = datos.get("explicacion_simple", "")
    if exp:
        lineas.append(exp[:200])
    return "\n".join(lineas)


def guardar_historial(pregunta: str, texto_raw: str):
    """Agrega un par Q&A al historial.json (maximo MAX_HISTORIAL entradas)."""
    try:
        os.makedirs("respuestas", exist_ok=True)
        datos = []
        if os.path.exists(HISTORIAL_PATH):
            with open(HISTORIAL_PATH, encoding="utf-8") as f:
                datos = json.load(f)
        datos.append({
            "ts":        datetime.datetime.now().isoformat(),
            "pregunta":  pregunta,
            "respuesta": texto_raw,
        })
        if len(datos) > MAX_HISTORIAL:
            datos = datos[-MAX_HISTORIAL:]
        with open(HISTORIAL_PATH, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"No se pudo guardar historial: {e}")

# ─── Memoria de aprendizaje por feedback ─────────────────────────
# Cada generacion se guarda como ejemplo "pending" en memoria/ejemplos.json.
# Con POST /feedback el usuario lo marca accepted / corrected / rejected.
# Solo los ejemplos accepted/corrected se inyectan como contexto en
# peticiones futuras: el modelo mejora sin reentrenamiento.

STOPWORDS_ES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
    "y", "o", "u", "que", "se", "en", "con", "por", "para", "cuando", "como",
    "donde", "es", "su", "sus", "lo", "le", "les", "mi", "tu", "si", "no",
    "me", "te", "ya", "hay", "este", "esta", "esto", "ese", "esa", "eso",
    "crea", "crear", "genera", "generar", "haz", "hacer", "quiero",
    "necesito", "favor", "pon", "poner", "programa", "ladder", "donde",
}

ACENTOS = str.maketrans("áéíóúüñ", "aeiouun")


def _tokens(texto: str) -> set:
    """Palabras significativas de un texto (sin acentos ni stopwords)."""
    t = str(texto or "").lower().translate(ACENTOS)
    return {p for p in re.findall(r"[a-z0-9%\.]+", t)
            if len(p) > 2 and p not in STOPWORDS_ES}


TAGS_KEYWORDS = {
    "button":    ["boton", "pulsador", "button"],
    "output":    ["salida", "lampara", "output", "luz", "foco", "led"],
    "seal-in":   ["enclav", "seal", "retenc", "memoria", "latch",
                  "arranque", "marcha", "mantenga"],
    "stop":      ["paro", "stop", "detener", "apagar"],
    "emergency": ["emergencia", "emergency"],
    "timer":     ["timer", "temporiz", "retardo", "segundo", "delay"],
    "counter":   ["contador", "counter", "pulso", "conteo", "contar", "cuenta"],
    "compare":   ["compar", "mayor", "menor", "igual"],
    "sequence":  ["secuencia", "ciclo", "intermitente", "alternar", "semaforo"],
}


def _tag_de_tipo(tipo: str):
    t = str(tipo or "").strip().upper()
    if t in ("TON", "TOF", "BLOCK_TON", "BLOCK_TOF"):
        return "timer"
    if t in ("CTU", "CTD", "BLOCK_CTU", "BLOCK_CTD"):
        return "counter"
    if t in ("OTL", "OTU", "COIL_S", "COIL_R"):
        return "seal-in"
    if t in ("CMP", "BLOCK_CMP"):
        return "compare"
    return None


def extraer_tags(pregunta: str, datos: Optional[dict] = None) -> list:
    """Tags automaticos: palabras clave del prompt + tipos usados en la logica."""
    texto = str(pregunta or "").lower().translate(ACENTOS)
    tags  = {t for t, kws in TAGS_KEYWORDS.items() if any(k in texto for k in kws)}
    for r in (datos or {}).get("logica_ladder", []):
        if len(r.get("filas", [])) > 1:
            tags.add("seal-in")
        for fila in r.get("filas", []):
            for el in fila.get("elementos", []):
                tag = _tag_de_tipo(el.get("tipo"))
                if tag:
                    tags.add(tag)
    tags.add("Horner_XL4")
    return sorted(tags)


def cargar_memoria() -> list:
    try:
        if not os.path.exists(MEMORIA_PATH):
            return []
        with open(MEMORIA_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"No se pudo cargar memoria de feedback: {e}")
        return []


def guardar_memoria(ejemplos: list):
    try:
        os.makedirs(os.path.dirname(MEMORIA_PATH), exist_ok=True)
        with open(MEMORIA_PATH, "w", encoding="utf-8") as f:
            json.dump(ejemplos, f, ensure_ascii=False, indent=1)
    except Exception as e:
        log.warning(f"No se pudo guardar memoria de feedback: {e}")


PRIORIDAD_STATUS = {"corrected": 3, "accepted": 2, "pending": 1, "rejected": 0}


def _podar_memoria(ejemplos: list) -> list:
    """Mantiene la memoria bajo MAX_EJEMPLOS. Salen primero los rejected y
    pending mas viejos y menos usados; corrected/accepted son los ultimos."""
    if len(ejemplos) <= MAX_EJEMPLOS:
        return ejemplos
    orden = sorted(ejemplos, key=lambda e: (
        PRIORIDAD_STATUS.get(e.get("status"), 0),
        e.get("uses", 0),
        e.get("date", ""),
    ))
    quitar = {e.get("id") for e in orden[:len(ejemplos) - MAX_EJEMPLOS]}
    return [e for e in ejemplos if e.get("id") not in quitar]


def _compactar_datos_modelo(datos: dict) -> dict:
    """Solo los campos del JSON del modelo que aportan al aprendizaje."""
    return {
        "programa_nombre":  datos.get("programa_nombre", ""),
        "logica_ladder":    datos.get("logica_ladder", []),
        "variables_usadas": datos.get("variables_usadas", {}),
    }


def agregar_ejemplo(pregunta: str, datos: dict) -> str:
    """Guarda la interaccion como ejemplo 'pending' y devuelve su id."""
    ejemplos    = cargar_memoria()
    norm_prompt = " ".join(sorted(_tokens(pregunta)))

    # Dedupe: una peticion identica que sigue 'pending' se reemplaza
    ejemplos = [e for e in ejemplos
                if not (e.get("status") == "pending"
                        and " ".join(sorted(_tokens(e.get("user_prompt", "")))) == norm_prompt)]

    nuevo_id = f"ej_{datetime.datetime.now():%Y%m%d_%H%M%S_%f}"
    ejemplos.append({
        "id":                    nuevo_id,
        "date":                  datetime.datetime.now().isoformat(timespec="seconds"),
        "user_prompt":           pregunta,
        "model_response":        datos.get("explicacion_simple", ""),
        "generated_ladder_json": _compactar_datos_modelo(datos),
        "user_correction":       None,
        "error_explanation":     None,
        "final_ladder_json":     None,
        "status":                "pending",
        "tags":                  extraer_tags(pregunta, datos),
        "uses":                  0,
    })
    guardar_memoria(_podar_memoria(ejemplos))
    return nuevo_id


def agregar_ejemplo_logica(texto: str, cfg: dict) -> str:
    """Guarda el engine_config como ejemplo de feedback (formato nuevo).
    No usa el esquema viejo (logica_ladder) para no contaminar el prompt de
    /generar-logica si en el futuro se inyectan estos ejemplos."""
    ejemplos    = cargar_memoria()
    norm_prompt = " ".join(sorted(_tokens(texto)))
    ejemplos = [e for e in ejemplos
                if not (e.get("status") == "pending"
                        and " ".join(sorted(_tokens(e.get("user_prompt", "")))) == norm_prompt)]
    nuevo_id = f"lej_{datetime.datetime.now():%Y%m%d_%H%M%S_%f}"
    ejemplos.append({
        "id":          nuevo_id,
        "date":        datetime.datetime.now().isoformat(timespec="seconds"),
        "user_prompt": texto,
        "datos":       {"engine_config": cfg},
        "status":      "pending",
        "tags":        extraer_tags(texto),
        "uses":        0,
    })
    guardar_memoria(_podar_memoria(ejemplos))
    return nuevo_id


def aplicar_feedback(ejemplo_id: str, status: str,
                     user_correction: Optional[str] = None,
                     error_explanation: Optional[str] = None,
                     final_ladder_json: Optional[dict] = None,
                     tags_extra: Optional[List[str]] = None) -> dict:
    ejemplos = cargar_memoria()
    for e in ejemplos:
        if e.get("id") == ejemplo_id:
            e["status"] = status
            if user_correction:
                e["user_correction"] = user_correction
            if error_explanation:
                e["error_explanation"] = error_explanation
            if final_ladder_json:
                e["final_ladder_json"] = final_ladder_json
            if tags_extra:
                e["tags"] = sorted(set(e.get("tags", [])) | set(tags_extra))
            guardar_memoria(ejemplos)
            return e
    raise KeyError(ejemplo_id)


def ejemplos_relevantes(pregunta: str, k: int = MAX_EJEMPLOS_PROMPT) -> list:
    """Top-k ejemplos validados (accepted/corrected) mas parecidos a la
    peticion actual, por coincidencia de tags y de palabras clave."""
    ejemplos = cargar_memoria()
    tokens_p = _tokens(pregunta)
    tags_p   = set(extraer_tags(pregunta)) - {"Horner_XL4"}

    candidatos = []
    for e in ejemplos:
        if e.get("status") not in ("accepted", "corrected"):
            continue
        tags_e = set(e.get("tags", [])) - {"Horner_XL4"}
        score  = 3 * len(tags_p & tags_e) + len(tokens_p & _tokens(e.get("user_prompt", "")))
        score += 2 if e.get("status") == "corrected" else 1
        if score >= 3:   # exige al menos un tag o varias palabras en comun
            candidatos.append((score, e))

    candidatos.sort(key=lambda c: (-c[0], c[1].get("date", "")))
    elegidos = [e for _, e in candidatos[:k]]

    if elegidos:
        ids = {e["id"] for e in elegidos}
        for e in ejemplos:
            if e.get("id") in ids:
                e["uses"] = e.get("uses", 0) + 1
        guardar_memoria(ejemplos)
    return elegidos


def _ladder_a_texto(e: dict) -> str:
    """Representacion compacta de la solucion final de un ejemplo."""
    final = e.get("final_ladder_json")
    if isinstance(final, dict) and final.get("rungs"):
        return describir_programa_editor(final)   # vino en esquema del editor
    fuente = final if isinstance(final, dict) and final.get("logica_ladder") \
        else e.get("generated_ladder_json") or {}
    return json.dumps(_compactar_datos_modelo(fuente),
                      ensure_ascii=False, separators=(",", ":"))


def bloque_ejemplos_prompt(ejemplos: list) -> str:
    partes = [
        "EJEMPLOS VALIDADOS POR EL USUARIO (interacciones previas):",
        "Imita estas soluciones cuando la peticion sea similar y evita los errores senalados.",
    ]
    for i, e in enumerate(ejemplos, 1):
        partes.append(f"\n--- Ejemplo validado {i} [{e.get('status')}] ---")
        partes.append(f"Peticion: {e.get('user_prompt', '')}")
        if e.get("user_correction"):
            partes.append(f"Correccion del usuario: {e['user_correction']}")
        if e.get("error_explanation"):
            partes.append(f"Error a evitar: {e['error_explanation']}")
        partes.append(f"Solucion final: {_ladder_a_texto(e)[:1500]}")
    return "\n".join(partes)

# ─── Conversion JSON del modelo → schema del editor ──────────────

TIPO_MAP = {
    "XIC": "contact_no",  "XIO": "contact_nc",
    "OSR": "contact_pos_edge", "OSF": "contact_neg_edge",
    "OTE": "coil",   "OTL": "coil_s",   "OTU": "coil_r",
    "TON": "block_ton", "TOF": "block_tof",
    "CTU": "block_ctu", "CTD": "block_ctd",
    "CMP": "block_cmp", "MOV": "block_mov", "ADD": "block_add",
    # pass-through si el modelo ya devuelve el tipo correcto
    "contact_no": "contact_no", "contact_nc": "contact_nc",
    "contact_pos_edge": "contact_pos_edge", "contact_neg_edge": "contact_neg_edge",
    "coil": "coil", "coil_s": "coil_s", "coil_r": "coil_r",
    "block_ton": "block_ton", "block_tof": "block_tof",
    "block_ctu": "block_ctu", "block_ctd": "block_ctd",
    "block_cmp": "block_cmp", "block_mov": "block_mov", "block_add": "block_add",
}


def norm(op: str) -> str:
    if not op:
        return ""
    s = str(op).strip().upper()
    m = re.match(r"^%([IQMR])0*(\d+)$", s)
    if m:
        l, n = m.group(1), int(m.group(2))
        return f"I0.{n}" if l == "I" else f"Q0.{n}" if l == "Q" else f"M0.{n}" if l == "M" else f"MW{n}"
    m = re.match(r"^([IQMR])(\d+)$", s)
    if m:
        l, n = m.group(1), int(m.group(2))
        return f"I0.{n}" if l == "I" else f"Q0.{n}" if l == "Q" else f"M0.{n}" if l == "M" else f"MW{n}"
    return op


def modbus(addr: str) -> dict:
    a = str(addr).upper()
    if a.startswith("I"):   return {"fn": "read_coil",   "address": None}
    if a.startswith("Q"):   return {"fn": "write_coil",  "address": None}
    if a.startswith("MW"):  return {"fn": "holding_reg", "address": None}
    return {"fn": "internal", "address": None}


def _num(v, default):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def mk_el(tipo_raw, operando, col, uid, params=None):
    t = TIPO_MAP.get(str(tipo_raw).strip().upper(),
        TIPO_MAP.get(str(tipo_raw).strip(), "contact_no"))
    a = norm(operando)
    p = params if isinstance(params, dict) else {}
    e = {"id": uid, "type": t, "address": a, "pos": {"col": col}}
    if t == "coil":    e["coil_type"] = "output"
    if t == "coil_s":  e["coil_type"] = "set"
    if t == "coil_r":  e["coil_type"] = "reset"
    if t in ("block_ton", "block_tof"):
        e["params"] = {"preset_ms": _num(p.get("PT_ms", p.get("preset_ms")), 1000)}
    if t in ("block_ctu", "block_ctd"):
        e["params"] = {"preset": _num(p.get("PV", p.get("preset")), 10)}
    if t == "block_cmp":
        e["params"] = {"op": p.get("op", "EQ"), "value": _num(p.get("value"), 0)}
    return e


def renglon_a_rung(renglon, idx, tid):
    num  = renglon.get("renglon", idx + 1)
    desc = renglon.get("descripcion", f"Rung {idx+1}")
    pfx  = f"e{tid}r{idx}"
    net  = []

    # Formato nuevo con "filas"
    if "filas" in renglon and isinstance(renglon["filas"], list):
        for fila in renglon["filas"]:
            fn  = fila.get("fila", len(net))
            els = [mk_el(e.get("tipo", ""), e.get("operando", ""),
                         c, f"{pfx}f{fn}c{c}", e.get("parametros"))
                   for c, e in enumerate(fila.get("elementos", []))]
            net.append({"row": fn, "elements": els})
        if not net:
            net = [{"row": 0, "elements": []}]
        return {"id": num, "enabled": True, "comment": desc, "network": net}

    # Formato legado con "elementos" plano
    raw   = renglon.get("elementos", [])
    todos = [{"tipo":   str(e.get("tipo", "")).strip().upper(),
               "op":     e.get("operando", ""),
               "params": e.get("parametros")} for e in raw]

    if not todos:
        return {"id": num, "enabled": True, "comment": desc,
                "network": [{"row": 0, "elements": []}]}

    bobina_addr = None
    for e in reversed(todos):
        if e["tipo"] in ("OTE", "OTL", "OTU", "coil", "coil_s", "coil_r"):
            bobina_addr = norm(e["op"])
            break

    paralelos = set()
    if bobina_addr:
        for i, e in enumerate(todos):
            if (e["tipo"] in ("XIC", "contact_no")
                    and norm(e["op"]) == bobina_addr
                    and 0 < i < len(todos) - 1):
                paralelos.add(i)

    f0, col = [], 0
    for i, e in enumerate(todos):
        if i not in paralelos:
            f0.append(mk_el(e["tipo"], e["op"], col, f"{pfx}f0c{col}",
                            e.get("params")))
            col += 1
    net = [{"row": 0, "elements": f0}]

    if paralelos:
        f1 = [mk_el(todos[i]["tipo"], todos[i]["op"], c, f"{pfx}f1c{c}",
                    todos[i].get("params"))
              for c, i in enumerate(sorted(paralelos))]
        net.append({"row": 1, "elements": f1})

    return {"id": num, "enabled": True, "comment": desc, "network": net}


def build_symbol_table(rungs, vars_usadas):
    tbl = {}

    def reg(lista, cmt):
        for a in (lista or []):
            n = norm(a)
            if n and n not in tbl:
                tbl[n] = {"symbol": n.replace(".", "_"), "type": "BOOL",
                           "modbus": modbus(n), "comment": f"{cmt} — {a}"}

    if vars_usadas:
        reg(vars_usadas.get("entradas",  []), "Entrada")
        reg(vars_usadas.get("salidas",   []), "Salida")
        reg(vars_usadas.get("marcas",    []), "Marca")
        reg(vars_usadas.get("registros", []), "Registro")

    for rung in rungs:
        for row in rung["network"]:
            for el in row["elements"]:
                a = el["address"]
                if a and a not in tbl:
                    tbl[a] = {"symbol": a.replace(".", "_"),
                               "type": "INT" if a.startswith("MW") else "BOOL",
                               "modbus": modbus(a), "comment": ""}
    return tbl


def a_schema(datos: dict) -> dict:
    tid   = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    rungs = [renglon_a_rung(r, i, tid)
             for i, r in enumerate(datos.get("logica_ladder", []))]
    return {
        "metadata": {
            "project_id":     f"import_{tid}",
            "name":            datos.get("programa_nombre", "Programa importado"),
            "version":         "1.0.0",
            "plc_target":      {"ip": "192.168.1.100", "port": 502, "unit_id": 1},
            "scan_time_ms":    100,
            "_explicacion":    datos.get("explicacion_simple", ""),
            "_implementacion": " -> ".join(datos.get("implementacion_cscape", [])),
            "_python_modbus":  datos.get("codigo_python_modbus", None),
        },
        "symbol_table":    build_symbol_table(rungs, datos.get("variables_usadas", {})),
        "rungs":           rungs,
        "execution_state": {"mode": "run", "rung_states": {}, "forced_outputs": {}},
    }


def schema_a_js_string(schema: dict, pregunta: str) -> str:
    ramas = sum(len(r["network"]) - 1 for r in schema["rungs"] if len(r["network"]) > 1)
    lineas = [
        f"// Generado : {MODELO} | {datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        f"// Consulta : {pregunta}",
        f"// Rungs: {len(schema['rungs'])} | Ramas paralelas: {ramas} | Variables: {len(schema['symbol_table'])}",
        "",
        f"export const program = {json.dumps(schema, indent=2, ensure_ascii=False)};",
        "",
        "export default program;",
    ]
    return "\n".join(lineas)


def guardar_js(datos: dict, pregunta: str, carpeta: str = "respuestas"):
    os.makedirs(carpeta, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^\w\s-]", "", datos.get("programa_nombre", "prog")).strip().replace(" ", "_")
    ruta = os.path.join(carpeta, f"{ts}_{slug}.js")
    schema    = a_schema(datos)
    contenido = schema_a_js_string(schema, pregunta)
    ramas = sum(len(r["network"]) - 1 for r in schema["rungs"] if len(r["network"]) > 1)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    log.info(f"JS guardado: {ruta} | Rungs: {len(schema['rungs'])} | Ramas: {ramas}")
    return ruta, schema

# ─── Contexto conversacional (modo Diseñador) ────────────────────

def denorm(addr: str) -> str:
    """Direccion del editor (I0.1, Q0.10, M0.2, MW5) → formato del
    modelo (%I1, %Q10, %M2, %R5). Si no coincide, se deja tal cual."""
    s = str(addr or "").strip().upper()
    m = re.match(r"^([IQM])0\.(\d+)$", s)
    if m:
        return f"%{m.group(1)}{m.group(2)}"
    m = re.match(r"^MW(\d+)$", s)
    if m:
        return f"%R{m.group(1)}"
    return addr or ""


def describir_programa_editor(prog: dict) -> str:
    """Convierte el JSON del editor (rungs/network/elements) a la misma
    descripcion textual que usan los programas de referencia del system
    prompt, para que el modelo lo lea en un formato que ya conoce y sin
    gastar tokens en metadata, symbol_table ni execution_state."""
    lineas = [f"Nombre: {prog.get('metadata', {}).get('name', 'Programa')}"]
    for r in prog.get("rungs", []):
        lineas.append(f"Renglon {r.get('id', '?')}: {r.get('comment', '')}")
        for row in sorted(r.get("network", []), key=lambda x: x.get("row", 0)):
            els = []
            for el in row.get("elements", []):
                txt = f"{el.get('type', '?')} {denorm(el.get('address', ''))}"
                if el.get("params"):
                    txt += f" {el['params']}"
                els.append(txt)
            par = "(paralela)" if row.get("row", 0) > 0 else ""
            lineas.append(f"  fila {row.get('row', 0)}{par}: " + " | ".join(els))
    return "\n".join(lineas)

# ─── Helpers de prompt ────────────────────────────────────────────

def es_enclavamiento(pregunta: str) -> bool:
    p = pregunta.lower()
    return any(w in p for w in PALABRAS_ENCLAVAMIENTO)


def _tiene_latch(cfg: dict) -> bool:
    """True si al menos una salida del engine-config tiene enclavamiento real."""
    return any(
        (o.get("logic", {}).get("mode") == "enclavado")
        or (o.get("logic", {}).get("mode") == "combinacional"
            and o.get("logic", {}).get("latched"))
        for o in cfg.get("outputs", [])
    )


def construir_mensaje_usuario(pregunta: str) -> str:
    verificacion = VERIFICACION_ENCLAVAMIENTO if es_enclavamiento(pregunta) else ""
    return f"{pregunta}{verificacion}{ESQUEMA}"


def validar_enclavamiento(datos: dict, pregunta: str) -> tuple:
    if not es_enclavamiento(pregunta):
        return True, "No es enclavamiento."
    for r in datos.get("logica_ladder", []):
        if len(r.get("filas", [])) > 1:
            return True, f"Renglon {r.get('renglon')} tiene rama paralela. OK."
    return False, "ADVERTENCIA: enclavamiento sin rama paralela."

# ─── Llamada a Groq y validacion del JSON ────────────────────────

def extraer_json_de_texto(texto: str) -> dict:
    """Extrae el primer objeto JSON valido aunque el modelo agregue texto
    extra o lo envuelva en bloques ```json ... ```."""
    t = (texto or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"```\s*$", "", t.strip())
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    ini, fin = t.find("{"), t.rfind("}")
    if ini != -1 and fin > ini:
        return json.loads(t[ini:fin + 1])
    raise json.JSONDecodeError("no se encontro un objeto JSON", t, 0)


def validar_estructura_datos(datos: dict) -> dict:
    """Verifica que el JSON del modelo tenga la estructura minima esperada
    antes de convertirlo al esquema del editor. Normaliza los campos
    opcionales y descarta renglones malformados."""
    if not isinstance(datos, dict):
        raise ValueError("El modelo no devolvio un objeto JSON.")
    logica = datos.get("logica_ladder")
    if not isinstance(logica, list) or not logica:
        raise ValueError(
            "El modelo no devolvio renglones en 'logica_ladder'. "
            "Vuelve a intentar o reformula la peticion."
        )
    renglones_ok = []
    for r in logica:
        if not isinstance(r, dict):
            continue
        filas = r.get("filas")
        if isinstance(filas, list):
            r["filas"] = [f for f in filas
                          if isinstance(f, dict) and isinstance(f.get("elementos"), list)]
            if r["filas"]:
                renglones_ok.append(r)
        elif isinstance(r.get("elementos"), list):
            renglones_ok.append(r)   # formato legado, lo resuelve renglon_a_rung
    if not renglones_ok:
        raise ValueError(
            "Ningun renglon del modelo tiene 'filas' o 'elementos' validos. "
            "Vuelve a intentar la peticion."
        )
    datos["logica_ladder"] = renglones_ok
    datos.setdefault("programa_nombre", "Programa")
    datos.setdefault("explicacion_simple", "")
    datos.setdefault("implementacion_cscape", [])
    datos.setdefault("variables_usadas", {})
    return datos


def _crear_completion(messages: list, max_tokens: int, con_formato: bool = True):
    if groq_client is None:
        raise HTTPException(
            status_code=503,
            detail="Generacion deshabilitada: este servidor no tiene GROQ_API_KEY. "
                   "Usa el backend de Render para generar; este puente local solo "
                   "habla con el PLC.",
        )
    kwargs = dict(messages=messages, model=MODELO,
                  temperature=1, max_tokens=max_tokens)
    if con_formato:
        kwargs["response_format"] = {"type": "json_object"}
    return groq_client.chat.completions.create(**kwargs)


def llamar_modelo_json(messages: list) -> dict:
    """Llama a Groq pidiendo JSON con dos reintentos automaticos:
    - json_validate_failed (400): el JSON salio truncado/vacio con el modo
      json_object; se reintenta SIN response_format y se extrae el JSON
      del texto a mano.
    - 413/429 (limite de tokens del plan gratuito): se reintenta con un
      max_tokens reducido."""
    try:
        resp = _crear_completion(messages, MAX_COMPLETION_TOKENS)
    except APIStatusError as e:
        cuerpo = str(getattr(e, "body", "") or e)
        if e.status_code == 400 and "json_validate_failed" in cuerpo:
            log.warning("Groq devolvio json_validate_failed; "
                        "reintentando sin response_format...")
            try:
                resp = _crear_completion(messages, MAX_COMPLETION_TOKENS,
                                         con_formato=False)
            except APIStatusError as e2:
                raise ValueError(
                    f"Groq rechazo la peticion tambien sin modo JSON: {e2}")
        elif e.status_code in (413, 429):
            log.warning(f"Groq {e.status_code} (limite de tokens); "
                        "reintentando con max_tokens=2000...")
            try:
                resp = _crear_completion(messages, 2000)
            except APIStatusError:
                raise ValueError(
                    "Groq rechazo la peticion por el limite de tokens por "
                    "minuto del plan gratuito. Espera un minuto y vuelve a intentar."
                )
        else:
            raise

    texto_raw = resp.choices[0].message.content or ""
    ti = resp.usage.prompt_tokens
    ts = resp.usage.completion_tokens
    log.info(f"Tokens — entrada: {ti}  salida: {ts}  total: {ti+ts}")

    try:
        return extraer_json_de_texto(texto_raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            "El modelo no devolvio un JSON valido. Vuelve a intentar. "
            f"Detalle: {e} | Inicio de la respuesta: {texto_raw[:300]}"
        )

# ─── Estado global ────────────────────────────────────────────────

STATE = {
    "system_prompt":      "",
    "contexto_chars":     0,
    "contexto_programas": 0,
    "history":            [],   # pares {role, content} activos en RAM
}

# ─── Lifespan: carga al arrancar ─────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Cargar contexto desde JSON pre-procesado (rapido, sin Vision API)
    log.info(f"Cargando contexto desde '{CONTEXTO_JSON_PATH}'...")
    contexto_json = cargar_contexto_json()
    n_programas   = len(contexto_json.get("programas", []))

    STATE["system_prompt"]      = construir_system_prompt(contexto_json)
    STATE["contexto_chars"]     = len(STATE["system_prompt"])
    STATE["contexto_programas"] = n_programas
    log.info(f"Contexto listo — {n_programas} programas — {STATE['contexto_chars']} chars")

    # 2. Cargar historial persistente de respuestas anteriores
    STATE["history"] = cargar_historial()

    yield
    log.info("Servidor detenido.")

# ─── FastAPI ──────────────────────────────────────────────────────

app = FastAPI(
    title="LadderVoice Backend",
    version="2.0.0",
    description=(
        "Genera programas Ladder desde lenguaje natural. "
        "Contexto desde JSON pre-procesado, historial persistente."
    ),
    lifespan=lifespan,
)

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173,"
    "http://127.0.0.1:5500,http://127.0.0.1:5173,"
    "https://sebas30073007.github.io,"
    "https://luiscortesmunoz.github.io"
).split(",")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ─── Modelos Pydantic ─────────────────────────────────────────────

class ContextoLadder(BaseModel):
    """Contexto conversacional que manda el Copiloto (modo Diseñador):
    el ultimo programa generado (esquema del editor) y los prompts previos,
    para que instrucciones como "cambialo" o "agregale" tengan referencia."""
    programa_anterior: Optional[dict] = None
    historial: Optional[List[str]] = None


class PromptRequest(BaseModel):
    prompt: str
    # Opcional: clientes viejos que solo mandan {prompt} siguen funcionando.
    contexto: Optional[ContextoLadder] = None


class LadderResponse(BaseModel):
    program: dict
    nombre: str
    rungs: int
    ramas_paralelas: int
    variables: int
    es_enclavamiento: bool
    js_content: str      # JS completo listo para importar en el frontend
    historia_pares: int  # pares Q&A que el modelo tiene como contexto
    ejemplo_id: str = "" # id en la memoria de feedback (para POST /feedback)


class STTResponse(BaseModel):
    texto: str
    modelo: str
    idioma: str
    archivo: str


class VozLadderResponse(BaseModel):
    texto: str
    stt: STTResponse
    ladder: LadderResponse


class FeedbackRequest(BaseModel):
    """Evaluacion del usuario sobre un programa generado.
    status: accepted (quedo bien) | corrected (lo arregle) | rejected (mal)."""
    ejemplo_id: str
    status: str
    user_correction: Optional[str] = None     # ej: "usa %Q10 en vez de %Q1"
    error_explanation: Optional[str] = None   # que estaba mal y por que
    final_ladder_json: Optional[dict] = None  # programa correcto (esquema del editor o del modelo)
    tags_extra: Optional[List[str]] = None


class LogicaRequest(BaseModel):
    """Peticion del flujo NUEVO (arquitectura unica del frontend).
    Devuelve el JSON dual engine-config que consume Python (XL4) y dibuja
    el frontend. El front manda {texto, device_profile, contexto}."""
    texto: str
    device_profile: Optional[str] = None
    contexto: Optional[ContextoLadder] = None


class LogicaResponse(BaseModel):
    logic: dict          # JSON dual engine-config (fuente de verdad para Python)
    name: str
    outputs: int
    warnings: List[str] = []
    ejemplo_id: str = ""  # id en memoria de feedback (para POST /feedback)
    # Mismo engine_config envuelto como lo espera el editor y /aplicar-plc:
    # program.metadata.engine_config. Es ADITIVO (no reemplaza a 'logic'); el
    # front puede cargar el programa al PLC sin transformar la respuesta.
    program: dict = {}


class AplicarPLCRequest(BaseModel):
    """Envia un programa al PLC fisico por Modbus TCP (boton 'Cargar' del editor).
    Manda 'logic' (engine_config) o un 'program' completo (se extrae de
    metadata.engine_config). 'ip'/'port' opcionales sobreescriben el PLC por
    defecto del modulo plc_maestro. dry_run=true valida e imprime sin escribir."""
    logic: Optional[dict] = None
    program: Optional[dict] = None
    ip: Optional[str] = None
    port: Optional[int] = None
    dry_run: bool = False

# ─── Logica principal Ladder ──────────────────────────────────────

def consultar_retorna_schema(pregunta: str, contexto: Optional[ContextoLadder] = None) -> tuple:
    es_modificacion = bool(contexto and contexto.programa_anterior)

    if es_modificacion:
        # OJO: la regla de "usa unicamente lo pedido" no aplica aqui — el
        # usuario pide un cambio puntual y el resto del programa debe sobrevivir.
        pregunta_reforzada = f"""{pregunta}

REGLAS IMPORTANTES:
- La instruccion de arriba MODIFICA el PROGRAMA ACTUAL de esta conversacion.
- Genera el programa COMPLETO actualizado: conserva todos los renglones y
  elementos del programa actual, salvo lo que la instruccion pida cambiar,
  quitar o agregar.
- No agregues entradas, salidas ni marcas nuevas que no se pidan.
"""
    else:
        pregunta_reforzada = f"""{pregunta}

REGLAS IMPORTANTES:
- Usa unicamente las entradas, salidas, marcas, temporizadores o contadores que el usuario pidio explicitamente.
- No agregues paro de emergencia, sensores, salidas ni marcas extra si el usuario no los menciono.
"""
    mensaje_usuario = construir_mensaje_usuario(pregunta_reforzada)

    # Memoria de feedback: ejemplos validados parecidos a esta peticion
    ejemplos = ejemplos_relevantes(pregunta)
    system_prompt = STATE["system_prompt"]
    if ejemplos:
        system_prompt += "\n\n" + bloque_ejemplos_prompt(ejemplos)
        log.info(f"Memoria de feedback: {len(ejemplos)} ejemplo(s) inyectado(s)")

    messages = [{"role": "system", "content": system_prompt}]
    if es_modificacion:
        # Contexto por peticion enviado por el cliente: es la fuente de
        # verdad de SU conversacion (el historial global en RAM mezcla a
        # todos los usuarios y se pierde cuando Render duerme).
        previas = "\n".join(f"- {p}" for p in (contexto.historial or [])[-4:])
        messages.append({"role": "user", "content": (
            "CONTEXTO DEL MODO DISENADOR — conversacion previa con este usuario.\n"
            f"Peticiones anteriores:\n{previas or '- (sin registro)'}\n\n"
            "PROGRAMA ACTUAL (resultado de la peticion anterior):\n"
            f"{describir_programa_editor(contexto.programa_anterior)}"
        )})
        messages.append({"role": "assistant", "content": (
            "Entendido, ese es el programa actual. Aplicare la siguiente "
            "instruccion como modificacion y devolvere el programa completo "
            "actualizado en el esquema JSON indicado."
        )})
    else:
        # Sin contexto del cliente: historial acumulado del servidor
        # (comportamiento original, usado tambien por /voz-a-ladder).
        messages.extend(STATE["history"])
    messages.append({"role": "user", "content": mensaje_usuario})

    log.info(
        f"Modelo: {MODELO} | historial en RAM: {len(STATE['history'])//2} pares"
        f" | modificacion con contexto del cliente: {es_modificacion}"
    )

    datos = validar_estructura_datos(llamar_modelo_json(messages))

    ok, msg = validar_enclavamiento(datos, pregunta)
    if not ok:
        log.warning(msg)
    else:
        log.info(msg)

    schema    = a_schema(datos)
    js_string = schema_a_js_string(schema, pregunta)

    # Actualizar historial en RAM (resumen compacto, no el JSON completo,
    # para no desbordar el limite de tokens/minuto de Groq)
    resumen = resumen_para_historial(datos)
    STATE["history"].append({"role": "user",     "content": pregunta})
    STATE["history"].append({"role": "assistant", "content": resumen})
    if len(STATE["history"]) > MAX_HISTORY * 2:
        STATE["history"] = STATE["history"][-(MAX_HISTORY * 2):]

    # Persistir en disco (mismo resumen; el JSON completo ya queda en
    # respuestas/*.js y en la memoria de feedback)
    guardar_historial(pregunta, resumen)
    try:
        guardar_js(datos, pregunta)
    except Exception as e:
        log.warning(f"No se pudo guardar .js local: {e}")

    # Registrar en la memoria de feedback (queda 'pending' hasta /feedback)
    ejemplo_id = ""
    try:
        ejemplo_id = agregar_ejemplo(pregunta, datos)
    except Exception as e:
        log.warning(f"No se pudo guardar ejemplo en memoria: {e}")

    return datos, schema, js_string, ejemplo_id


def crear_ladder_response(prompt: str, schema: dict, js_string: str,
                          ejemplo_id: str = "") -> LadderResponse:
    rungs   = schema.get("rungs", [])
    n_ramas = sum(len(r["network"]) - 1 for r in rungs if len(r.get("network", [])) > 1)
    return LadderResponse(
        program=schema,
        nombre=schema.get("metadata", {}).get("name", "Programa"),
        rungs=len(rungs),
        ramas_paralelas=n_ramas,
        variables=len(schema.get("symbol_table", {})),
        es_enclavamiento=es_enclavamiento(prompt),
        js_content=js_string,
        historia_pares=len(STATE["history"]) // 2,
        ejemplo_id=ejemplo_id,
    )

# ─── Utilidades STT ───────────────────────────────────────────────

def _nombre_audio(content_type: Optional[str], filename: Optional[str]) -> str:
    if filename and "." in filename:
        return filename
    mapa = {
        "audio/webm": "audio.webm", "audio/wav": "audio.wav",
        "audio/x-wav": "audio.wav", "audio/mpeg": "audio.mp3",
        "audio/mp3":  "audio.mp3",  "audio/mp4": "audio.m4a",
        "audio/ogg":  "audio.ogg",
    }
    return mapa.get(content_type or "", "audio.webm")


async def _transcribir(archivo: UploadFile) -> STTResponse:
    if groq_client_stt is None:
        raise HTTPException(500, "No se encontro GROQ_API_KEY_stt en las variables de entorno.")
    audio = await archivo.read()
    if not audio:
        raise HTTPException(400, "El archivo de audio esta vacio.")
    if len(audio) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(413, f"El audio supera {MAX_AUDIO_MB} MB.")
    nombre = _nombre_audio(archivo.content_type, archivo.filename)
    log.info(f"Transcribiendo '{nombre}' ({len(audio)} bytes) con {MODELO_STT}")
    t = groq_client_stt.audio.transcriptions.create(
        file=(nombre, audio), model=MODELO_STT,
        language=IDIOMA_STT, response_format="json",
    )
    return STTResponse(
        texto=(getattr(t, "text", "") or "").strip(),
        modelo=MODELO_STT, idioma=IDIOMA_STT, archivo=nombre,
    )


def _seleccionar_audio(audio: Optional[UploadFile], file: Optional[UploadFile]) -> UploadFile:
    a = audio or file
    if a is None:
        raise HTTPException(400, "No se recibio archivo de audio. Usa FormData con campo 'audio'.")
    return a

# ─── Endpoints ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    with open("templates/index.html", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/ladder", response_class=HTMLResponse)
def ladder_editor():
    with open("templates/ladder.html", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/api")
def api_info():
    return {
        "service":            "LadderVoice Backend",
        "version":            "2.0.0",
        "status":             "ok",
        "contexto_programas": STATE["contexto_programas"],
        "contexto_chars":     STATE["contexto_chars"],
        "historia_pares":     len(STATE["history"]) // 2,
        "docs":               "/docs",
    }


@app.get("/health")
def health():
    return {
        "status":             "ok",
        "modelo_ladder":      MODELO,
        "stt_configurado":    groq_client_stt is not None,
        "modelo_stt":         MODELO_STT,
        "contexto_programas": STATE["contexto_programas"],
        "historia_pares":     len(STATE["history"]) // 2,
        "ejemplos_memoria":   len(cargar_memoria()),
    }


@app.post("/transcribir", response_model=STTResponse)
async def transcribir_audio(
    audio: Optional[UploadFile] = File(None),
    file:  Optional[UploadFile] = File(None),
):
    """Recibe audio y lo transcribe con Groq Whisper. Campo 'audio' o 'file'."""
    try:
        return await _transcribir(_seleccionar_audio(audio, file))
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error STT: {e}")
        raise HTTPException(500, f"Error transcribiendo: {e}")


@app.post("/voz-a-ladder", response_model=VozLadderResponse)
async def voz_a_ladder(
    audio: Optional[UploadFile] = File(None),
    file:  Optional[UploadFile] = File(None),
):
    """Flujo completo: audio → STT → Ladder JSON → respuesta."""
    try:
        stt    = await _transcribir(_seleccionar_audio(audio, file))
        prompt = stt.texto.strip()
        if not prompt:
            raise HTTPException(422, "Transcripcion vacia. Habla mas claro o graba de nuevo.")
        if len(prompt) > 2000:
            raise HTTPException(400, "Transcripcion demasiado larga, maximo 2000 caracteres.")
        _, schema, js_string, ejemplo_id = consultar_retorna_schema(prompt)
        return VozLadderResponse(
            texto=prompt, stt=stt,
            ladder=crear_ladder_response(prompt, schema, js_string, ejemplo_id),
        )
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        log.error(f"Error voz-a-ladder: {e}")
        raise HTTPException(500, str(e))


@app.post("/generar-ladder", response_model=LadderResponse)
async def generar_ladder(req: PromptRequest):
    """Recibe texto y genera el programa Ladder correspondiente."""
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(400, "El prompt no puede estar vacio.")
    if len(req.prompt) > 2000:
        raise HTTPException(400, "Prompt demasiado largo, maximo 2000 caracteres.")
    try:
        _, schema, js_string, ejemplo_id = consultar_retorna_schema(req.prompt.strip(), req.contexto)
        return crear_ladder_response(req.prompt.strip(), schema, js_string, ejemplo_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        log.error(f"Error generar-ladder: {e}")
        raise HTTPException(500, str(e))


@app.post("/generar-logica", response_model=LogicaResponse)
async def generar_logica(req: LogicaRequest):
    """Flujo NUEVO (ver CONTRACT del frontend): la IA interpreta la intencion
    y devuelve el JSON dual engine-config del maletin. NO genera geometria ni
    registros: eso lo hacen el front (dibujo) y Python (XL4 -> PLC)."""
    texto = (req.texto or "").strip()
    if not texto:
        raise HTTPException(400, "El campo 'texto' no puede estar vacio.")
    if len(texto) > 2000:
        raise HTTPException(400, "Texto demasiado largo, maximo 2000 caracteres.")

    # OJO: aqui NO se inyecta la memoria de feedback (memoria/ejemplos.json):
    # esos ejemplos estan en el esquema VIEJO (logica_ladder/filas/XIC/OTE) y
    # contaminan el contrato engine-config (el modelo intenta imitarlos y rompe
    # el enclavamiento). El contexto va solo en SYSTEM_PROMPT_LOGICA.
    messages = [{"role": "system", "content": SYSTEM_PROMPT_LOGICA}]
    if req.contexto:
        previas = "\n".join(f"- {p}" for p in (req.contexto.historial or [])[-4:])
        # En modificaciones, el programa anterior trae su engine_config en la
        # metadata: se lo damos al modelo para que "agregue/cambie" sobre el.
        prev_cfg = None
        if req.contexto.programa_anterior:
            prev_cfg = (req.contexto.programa_anterior.get("metadata", {})
                        .get("engine_config"))
        if previas or prev_cfg:
            partes = []
            if previas:
                partes.append(f"Peticiones anteriores del usuario:\n{previas}")
            if prev_cfg:
                partes.append("PROGRAMA ACTUAL (modifica este JSON, conserva lo no pedido):\n"
                              + json.dumps(prev_cfg, ensure_ascii=False))
            messages.append({"role": "user", "content": "\n\n".join(partes)})
            messages.append({"role": "assistant", "content":
                             "Entendido. Aplicare la siguiente instruccion sobre ese "
                             "programa y devolvere el JSON completo actualizado."})
    messages.append({"role": "user", "content":
                     f"{texto}\n\nResponde SOLO con el JSON del esquema indicado."})

    # Auto-revision iterativa: el agente genera, se valida a si mismo y, si el
    # JSON sale invalido, se le devuelven SUS errores para que se corrija, hasta
    # MAX_AUTOREVISIONES intentos. Solo se rinde (422) si tras todos los intentos
    # sigue invalido. A la primera buena, sale sin gastar llamadas de mas.
    cfg = None
    errores = []
    intentos = max(1, MAX_AUTOREVISIONES)
    msgs_iter = list(messages)
    for intento in range(1, intentos + 1):
        try:
            candidato = llamar_modelo_json(msgs_iter)
        except ValueError as e:
            # JSON ilegible (no parseable). Reintenta si quedan vueltas.
            if intento < intentos:
                log.warning(f"Auto-revision {intento}/{intentos}: JSON ilegible, reintentando — {e}")
                continue
            raise HTTPException(422, str(e))
        except Exception as e:
            log.error(f"Error generar-logica (modelo): {e}")
            raise HTTPException(500, str(e))

        errores = validar_logica_config(candidato)
        if not errores:
            cfg = candidato
            if intento > 1:
                log.info(f"Auto-revision exitosa en el intento {intento}/{intentos}.")
            break

        log.warning(f"Auto-revision {intento}/{intentos}: JSON invalido — {errores}")
        if intento < intentos:
            # Realimenta los errores concretos para que el modelo se corrija.
            msgs_iter = list(messages)
            msgs_iter.append({"role": "assistant",
                              "content": json.dumps(candidato, ensure_ascii=False)})
            msgs_iter.append({"role": "user", "content": (
                "El JSON anterior es INVALIDO por estos motivos:\n- "
                + "\n- ".join(errores)
                + "\nCorrige SOLO esos errores y devuelve el JSON COMPLETO del "
                  "esquema indicado, sin texto extra.")})

    if cfg is None:
        raise HTTPException(
            422, "El JSON generado no es valido tras varios intentos:\n- "
            + "\n- ".join(errores))

    cfg = normalizar_logica_config(cfg)
    warnings = list(cfg.get("system", {}).get("warnings", []) or [])

    # Red de seguridad + reintento: si la peticion pide enclavamiento pero el
    # modelo no lo genero, reintentar UNA vez antes de emitir el aviso.
    if es_enclavamiento(texto) and not cfg.get("sequence") and not _tiene_latch(cfg):
        log.warning("Enclavamiento sin latch en 1ª respuesta — reintentando...")
        retry_msgs = list(messages)
        retry_msgs.append({
            "role": "assistant",
            "content": json.dumps(cfg, ensure_ascii=False),
        })
        retry_msgs.append({
            "role": "user",
            "content": (
                "El JSON anterior es INCORRECTO: la peticion pide enclavamiento "
                "pero ninguna salida usa mode 'enclavado'. "
                "Corrige SOLO eso: usa mode 'enclavado' con 'start' (arranque) y "
                "'stop' (paro) correctos. "
                "Devuelve el JSON completo corregido, sin texto extra."
            ),
        })
        try:
            cfg2 = llamar_modelo_json(retry_msgs)
            errs2 = validar_logica_config(cfg2)
            if not errs2 and _tiene_latch(cfg2):
                cfg = normalizar_logica_config(cfg2)
                warnings = list(cfg.get("system", {}).get("warnings", []) or [])
                log.info("Retry de enclavamiento exitoso.")
            else:
                warnings.append(
                    "La peticion parece pedir enclavamiento, pero ninguna salida quedo "
                    "en modo 'enclavado'. Reformula mencionando 'arranque y paro'."
                )
        except Exception as e_retry:
            log.warning(f"Retry enclavamiento fallo: {e_retry}")
            warnings.append(
                "La peticion parece pedir enclavamiento, pero ninguna salida quedo "
                "en modo 'enclavado'. Reformula mencionando 'arranque y paro'."
            )

    # Persistencia: historial + memoria de feedback
    try:
        guardar_historial(texto, json.dumps(cfg, ensure_ascii=False)[:1500])
    except Exception as e:
        log.warning(f"No se pudo guardar historial (logica): {e}")

    ejemplo_id = ""
    try:
        ejemplo_id = agregar_ejemplo_logica(texto, cfg)
    except Exception as e:
        log.warning(f"No se pudo guardar ejemplo logica: {e}")

    log.info(f"/generar-logica OK — {len(cfg.get('outputs', []))} salida(s)")
    nombre_prog = cfg.get("name", "Programa maletin")
    return LogicaResponse(
        logic=cfg,
        name=nombre_prog,
        outputs=len(cfg.get("outputs", [])),
        warnings=warnings,
        ejemplo_id=ejemplo_id,
        # Forma lista para el editor / POST /aplicar-plc (program.metadata.engine_config).
        program={"metadata": {"name": nombre_prog, "engine_config": cfg}},
    )


# ─── Copiloto de chat (modo Aprendizaje / Practico) ──────────────
# Portado del backend local de Ollama (Practica 4) a Groq, para que el
# chat del frontend funcione EN LA NUBE sin Ollama ni nada local.
# Es codigo NUEVO e independiente: no toca la generacion Ladder/PLC.
# Contrato identico al backend viejo (main.py): GET /profiles y POST /chat
# con los mismos campos que ya envia/espera copilot.js.

# Modelo de chat en Groq. gpt-oss-120b es razonador y consume max_tokens con
# su razonamiento interno (mal para respuestas cortas), por eso el chat usa
# por defecto un modelo de produccion no-razonador. Configurable por env.
MODELO_CHAT = os.environ.get("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
DEFAULT_PROFILE = "media"

# Perfiles copiados TAL CUAL del backend de Ollama (mismos system prompts y
# parametros) para no cambiar el comportamiento que el usuario ya conocia.
# 'num_ctx' y 'repeat_penalty' no existen en Groq: el front los usa solo para
# rellenar campos de la UI; aqui se conservan en el perfil pero se ignoran al
# llamar a Groq.
COPILOT_PROFILES = {
    "generico": {
        "label": "Genérico",
        "description": "Asistente sin especializar. Úsalo como línea base para comparar contra los perfiles especializados.",
        "system_prompt": (
            "Eres un asistente académico claro, preciso y útil para estudiantes "
            "universitarios. Respondes siempre en español."
        ),
        "params": {"temperature": 0.7, "top_p": 0.9, "num_predict": 300,
                   "num_ctx": 4096, "repeat_penalty": 1.1},
    },
    "instantanea": {
        "label": "Instantánea",
        "description": "La opción más rápida. Para dudas sencillas, traducciones, correcciones y listas rápidas.",
        "system_prompt": (
            "Eres LadderVoice Copilot en modo Instantánea: un copiloto de automatización "
            "industrial y programación de PLCs en lenguaje Ladder para estudiantes "
            "universitarios. Tu prioridad es la rapidez.\n\n"
            "Reglas:\n"
            "- Responde en español, de forma breve y directa (idealmente 5 líneas o menos).\n"
            "- Usa listas cortas cuando mejoren la claridad.\n"
            "- Si la tarea es compleja (diseñar lógica Ladder completa, depurar código "
            "extenso), da solo la respuesta esencial y sugiere cambiar al modo Media o Alta.\n"
            "- Si falta información, haz una sola pregunta puntual en lugar de asumir.\n"
            "- No inventes referencias, datos técnicos ni normas.\n"
            "- Si no sabes algo, dilo explícitamente."
        ),
        "params": {"temperature": 0.4, "top_p": 0.9, "num_predict": 180,
                   "num_ctx": 2048, "repeat_penalty": 1.1},
    },
    "media": {
        "label": "Media",
        "description": "Punto intermedio. Explicaciones paso a paso, comparar opciones, errores sencillos de código.",
        "system_prompt": (
            "Eres LadderVoice Copilot en modo Media: un copiloto de automatización "
            "industrial y programación de PLCs en lenguaje Ladder para estudiantes "
            "universitarios. Buscas el equilibrio entre rapidez y profundidad.\n\n"
            "Tu tarea principal es ayudar con: explicaciones paso a paso, comparación de "
            "opciones, resumen de documentos, mejora de prompts, errores sencillos de "
            "código (Python, Flask, JavaScript) y lógica Ladder básica.\n\n"
            "Formato:\n"
            "- Responde en español con pasos numerados o secciones cortas.\n"
            "- Al explicar Ladder usa ejemplos con entradas (I0.x), salidas (Q0.x), "
            "marcas (M0.x) y timers (T0).\n\n"
            "Reglas:\n"
            "- Si falta información, pregunta antes de asumir.\n"
            "- No inventes referencias, datos técnicos ni normas.\n"
            "- Advierte riesgos eléctricos básicos cuando la pregunta involucre "
            "conexiones, motores o baterías.\n"
            "- Si no sabes algo, dilo explícitamente."
        ),
        "params": {"temperature": 0.7, "top_p": 0.9, "num_predict": 450,
                   "num_ctx": 4096, "repeat_penalty": 1.1},
    },
    "alta": {
        "label": "Alta",
        "description": "Máximo razonamiento. Problemas largos, programación y depuración, análisis de PLC/Ladder, decisiones con muchos pasos.",
        "system_prompt": (
            "Eres LadderVoice Copilot en modo Alta: un copiloto experto en automatización "
            "industrial, PLCs y lógica Ladder (contactos NO/NC, bobinas, set/reset, "
            "timers TON/TOF, contadores CTU/CTD, comparadores), comunicación Modbus TCP "
            "y desarrollo en Python (FastAPI, Flask) y JavaScript. Tu prioridad es la "
            "calidad del razonamiento sobre la velocidad.\n\n"
            "Método de trabajo:\n"
            "- Analiza el problema antes de responder y muestra tu razonamiento en pasos.\n"
            "- Separa explícitamente hechos, inferencias y recomendaciones.\n"
            "- Al depurar código: identifica la causa probable, la evidencia que la "
            "sustenta y la corrección propuesta con código comentado.\n\n"
            "Formato:\n"
            "- Responde en español. Para tareas complejas usa secciones tituladas: "
            "Análisis, Propuesta, Pasos, Riesgos.\n"
            "- Para lógica Ladder describe cada rung con sus elementos y direcciones "
            "(I0.x, Q0.x, M0.x, T0, C0).\n\n"
            "Reglas:\n"
            "- Si falta información crítica (modelo de PLC, voltaje, corriente, diagrama "
            "de conexión), pregunta primero antes de dar instrucciones específicas.\n"
            "- Advierte riesgos de seguridad eléctrica cuando aplique.\n"
            "- No inventes referencias, registros Modbus, datos de hardware ni normas.\n"
            "- Si no puedes verificar algo, dilo explícitamente."
        ),
        "params": {"temperature": 0.7, "top_p": 0.9, "num_predict": 900,
                   "num_ctx": 8192, "repeat_penalty": 1.1},
    },
}


class ChatRequest(BaseModel):
    message: str
    # El front manda un nombre de modelo de Ollama (ej. 'llama3.2:3b'); aqui se
    # ignora si trae ':' y se usa MODELO_CHAT (ver _modelo_chat_groq).
    model: Optional[str] = None
    copilot_profile: str = DEFAULT_PROFILE
    system_prompt: Optional[str] = None
    temperature: float = 0.7
    top_p: float = 0.9
    num_predict: int = 450
    # Aceptados por compatibilidad con el front; Groq no los usa.
    num_ctx: Optional[int] = None
    repeat_penalty: Optional[float] = None
    keep_alive: Optional[str] = None


def _perfil_chat(profile_id: str) -> dict:
    p = COPILOT_PROFILES.get(profile_id)
    if p is None:
        raise HTTPException(
            400, f"Perfil no válido: '{profile_id}'. "
                 f"Opciones: {', '.join(COPILOT_PROFILES)}.")
    return p


def _modelo_chat_groq(model: Optional[str]) -> str:
    """Los nombres de Ollama llevan ':' (llama3.2:3b) y no sirven en Groq.
    Si el cliente manda uno asi (o nada), se usa MODELO_CHAT."""
    m = (model or "").strip()
    return MODELO_CHAT if (not m or ":" in m) else m


@app.get("/profiles")
def chat_profiles():
    """Perfiles del copiloto de chat (mismos que el backend viejo de Ollama)."""
    return {"default": DEFAULT_PROFILE, "profiles": COPILOT_PROFILES}


@app.post("/chat")
def chat(req: ChatRequest):
    """Copiloto de chat del modo Aprendizaje/Practico, ahora sobre Groq.
    Mantiene el mismo contrato de entrada/salida que el backend de Ollama."""
    if groq_client is None:
        raise HTTPException(
            503, "Chat deshabilitado: este servidor no tiene GROQ_API_KEY. "
                 "Usa el backend de Render para el copiloto.")
    mensaje = (req.message or "").strip()
    if not mensaje:
        raise HTTPException(400, "El mensaje no puede estar vacío.")
    if len(mensaje) > 4000:
        raise HTTPException(400, "Mensaje demasiado largo, máximo 4000 caracteres.")

    perfil        = _perfil_chat(req.copilot_profile)
    system_prompt = (req.system_prompt or "").strip() or perfil["system_prompt"]
    modelo        = _modelo_chat_groq(req.model)

    start = time.perf_counter()
    try:
        resp = groq_client.chat.completions.create(
            model=modelo,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": mensaje},
            ],
            temperature=req.temperature,
            top_p=req.top_p,
            max_tokens=req.num_predict,
        )
    except APIStatusError as e:
        if e.status_code in (413, 429):
            raise HTTPException(
                429, "Límite de tokens por minuto del plan gratuito de Groq. "
                     "Espera un minuto y vuelve a intentar.")
        raise HTTPException(502, f"Error de Groq: {e}")
    except Exception as e:
        log.error(f"Error /chat: {e}")
        raise HTTPException(500, f"Error generando respuesta: {e}")

    backend_ms = round((time.perf_counter() - start) * 1000, 1)
    reply = (resp.choices[0].message.content or "").strip()
    if not reply:
        raise HTTPException(500, "El modelo respondió sin contenido. Reintenta.")

    pin = getattr(resp.usage, "prompt_tokens", None)
    pout = getattr(resp.usage, "completion_tokens", None)
    tps = round(pout / (backend_ms / 1000), 1) if (pout and backend_ms > 0) else None
    log.info(f"/chat OK — perfil {req.copilot_profile} | modelo {modelo} | "
             f"tokens entrada {pin} salida {pout} | {backend_ms} ms")

    return {
        "model":              modelo,
        "copilot_profile":    req.copilot_profile,
        "copilot_label":      perfil["label"],
        "system_prompt_used": system_prompt,
        "reply":              reply,
        "metrics": {
            "backend_ms":        backend_ms,
            "prompt_eval_count": pin,
            "eval_count":        pout,
            "tokens_per_second": tps,
        },
    }


# ─── Deteccion / configuracion de la IP del PLC ──────────────────
# El PLC vive en una LAN privada (Modbus TCP, puerto 502). Estos helpers
# y endpoints SOLO sirven si el backend corre LOCALMENTE en esa red (no en
# Render). Permiten al frontend descubrir la IP del PLC conectado en vez de
# pedirsela al usuario de memoria.

# Override en runtime de la IP/puerto por defecto del PLC (se fija con
# POST /plc/config; si es None se usa el valor de plc_maestro.PLC_IP).
PLC_STATE = {"ip": None, "port": None}


def plc_default_ip() -> str:
    if PLC_STATE["ip"]:
        return PLC_STATE["ip"]
    try:
        import plc_maestro
        return plc_maestro.PLC_IP
    except Exception:
        return "192.168.3.12"


def plc_default_port() -> int:
    if PLC_STATE["port"]:
        return PLC_STATE["port"]
    try:
        import plc_maestro
        return plc_maestro.PLC_PORT
    except Exception:
        return 502


def _ips_locales() -> set:
    """IPv4 locales del host (para deducir la subred /24 del PLC)."""
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except Exception:
        pass
    # IP de salida real (no envia nada; solo elige la interfaz de ruta)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return {ip for ip in ips if not ip.startswith("127.")}


def _puerto_abierto(ip: str, port: int = 502, timeout: float = 0.3) -> bool:
    """True si hay un servicio TCP escuchando en ip:port (Modbus = 502)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _ping(ip: str, timeout_ms: int = 1000) -> bool:
    """Ping ICMP a una IP (host vivo). Util cuando el PLC esta conectado
    directo a la PC: responde al ping aunque el puerto Modbus aun no escuche.
    Multiplataforma (Windows usa -n/-w ms; Unix usa -c/-W s)."""
    es_windows = platform.system().lower().startswith("win")
    if es_windows:
        cmd = ["ping", "-n", "1", "-w", str(int(timeout_ms)), ip]
    else:
        secs = max(1, (int(timeout_ms) + 999) // 1000)   # -W en segundos
        cmd = ["ping", "-c", "1", "-W", str(secs), ip]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=max(2.0, timeout_ms / 1000 + 2))
    except Exception:
        return False
    if res.returncode != 0:
        return False
    # En Windows el "host inaccesible" a veces sale con returncode 0; exige TTL
    # para confirmar que hubo respuesta real del dispositivo.
    salida = (res.stdout or b"").decode("utf-8", "ignore").lower()
    if es_windows and "ttl=" not in salida:
        return False
    return True


def _subredes_a_escanear(ip_base: str = "") -> list:
    """Subredes /24 candidatas: la del parametro, las de cada NIC local y la
    del PLC por defecto (por si el host tiene varias interfaces)."""
    bases = set()
    if ip_base:
        bases.add(".".join(ip_base.split(".")[:3]) + ".0")
    for ip in _ips_locales():
        bases.add(".".join(ip.split(".")[:3]) + ".0")
    bases.add(".".join(plc_default_ip().split(".")[:3]) + ".0")
    return sorted(bases)


@app.get("/plc/config")
def plc_config_ver():
    """IP/puerto del PLC que usara /aplicar-plc por defecto."""
    return {"ip": plc_default_ip(), "port": plc_default_port(),
            "override": dict(PLC_STATE)}


class PLCConfigRequest(BaseModel):
    ip: Optional[str] = None
    port: Optional[int] = None


@app.post("/plc/config")
def plc_config_set(req: PLCConfigRequest):
    """Fija la IP/puerto por defecto del PLC en este backend (en memoria)."""
    if req.ip is not None:
        PLC_STATE["ip"] = req.ip.strip() or None
    if req.port is not None:
        PLC_STATE["port"] = int(req.port) or None
    log.info(f"PLC por defecto -> {plc_default_ip()}:{plc_default_port()}")
    return {"status": "ok", "ip": plc_default_ip(), "port": plc_default_port()}


@app.get("/plc/probar")
def plc_probar(ip: str, port: int = 502, timeout_ms: int = 1000):
    """Prueba de comunicacion con un PLC concreto:
      - ping ICMP  -> el dispositivo esta vivo y en la red (host alcanzable).
      - TCP 502    -> el servidor Modbus del PLC esta escuchando (listo para
                      recibir las reglas).
    'alcanzable' = responde al ping o tiene el puerto abierto."""
    ping_ok = _ping(ip, timeout_ms)
    tcp_ok  = _puerto_abierto(ip, port, max(0.1, timeout_ms / 1000))
    alcanzable = ping_ok or tcp_ok
    return {"status": "ok" if alcanzable else "sin_respuesta",
            "ip": ip, "port": port,
            "ping": ping_ok, "tcp": tcp_ok, "alcanzable": alcanzable}


@app.get("/plc/escanear")
def plc_escanear(ip_base: str = "", port: int = 502, timeout_ms: int = 300):
    """Escanea la(s) subred(es) /24 locales buscando dispositivos Modbus TCP
    (puerto 502). Devuelve las IPs que responden para que el frontend deje
    elegir el PLC. SOLO funciona si el backend corre en la LAN del PLC."""
    subredes = _subredes_a_escanear(ip_base)
    candidatos = []
    for base in subredes:
        try:
            red = ipaddress.ip_network(base + "/24", strict=False)
        except ValueError:
            continue
        candidatos.extend(str(h) for h in red.hosts())
    candidatos = list(dict.fromkeys(candidatos))   # dedupe conservando orden

    timeout = max(0.05, timeout_ms / 1000)
    encontrados = []
    with ThreadPoolExecutor(max_workers=128) as ex:
        abiertos = ex.map(lambda x: _puerto_abierto(x, port, timeout), candidatos)
        for ip, ok in zip(candidatos, abiertos):
            if ok:
                encontrados.append(ip)

    # El PLC por defecto primero si respondio (comodo para el usuario)
    por_defecto = plc_default_ip()
    encontrados.sort(key=lambda ip: (ip != por_defecto,
                                     tuple(int(o) for o in ip.split("."))))

    log.info(f"/plc/escanear — subredes {subredes} -> {len(encontrados)} PLC(s): {encontrados}")
    return {
        "status":       "ok",
        "puerto":       port,
        "subredes":     subredes,
        "ips_locales":  sorted(_ips_locales()),
        "encontrados":  encontrados,
        "default":      por_defecto,
        "total_revisadas": len(candidatos),
    }


@app.post("/aplicar-plc")
def aplicar_plc(req: AplicarPLCRequest):
    """Envia el programa al PLC fisico por Modbus TCP (boton 'Cargar' del editor).
    Usa la clase XL4 de plc_maestro SIN modificar sus reglas.

    IMPORTANTE: el PLC esta en una LAN privada (p. ej. 192.168.3.12). Este
    endpoint SOLO alcanza el PLC si el backend corre LOCALMENTE en esa red; en
    Render (internet) devolvera error de conexion. dry_run=true valida y
    devuelve el plan sin tocar el PLC (sirve en cualquier lado)."""
    # 1) Obtener el engine_config (directo o desde program.metadata)
    cfg = req.logic
    if cfg is None and isinstance(req.program, dict):
        cfg = (req.program.get("metadata", {}) or {}).get("engine_config")
    if not isinstance(cfg, dict):
        raise HTTPException(400, "Falta el engine_config. Manda 'logic' o un 'program' "
                                 "que tenga metadata.engine_config (generado por la IA).")

    # 2) Import perezoso: solo quien use el PLC necesita pymodbus instalado
    try:
        import plc_maestro
    except Exception as e:
        raise HTTPException(500, f"No se pudo cargar plc_maestro (¿falta pymodbus?): {e}")

    # 3) Validar con las MISMAS reglas del motor antes de tocar el PLC
    errores = plc_maestro.validar_config(cfg)
    if errores:
        raise HTTPException(422, "El programa no es valido para el PLC:\n- " + "\n- ".join(errores))

    plan = plc_maestro.plan_config(cfg)
    plan_legible = [
        f"plc.{m}(" + ", ".join([repr(a) for a in args]
                                + [f"{k}={v!r}" for k, v in kw.items()]) + ")"
        for m, args, kw in plan
    ]

    # 4) Dry-run: no toca el PLC (validacion/preview en cualquier entorno)
    if req.dry_run:
        return {"status": "dry-run", "enviado": False,
                "salidas": len(cfg.get("outputs", [])), "plan": plan_legible}

    # 5) Conectar y escribir al PLC real (Modbus TCP)
    #    Prioridad: lo que mande el front (req.ip) > override del backend
    #    (POST /plc/config) > valor por defecto de plc_maestro.
    ip   = req.ip   or plc_default_ip()
    port = req.port or plc_default_port()
    plc  = plc_maestro.XL4(ip=ip, port=port)
    try:
        plc.connect()
    except Exception as e:
        raise HTTPException(503, f"No se pudo conectar al PLC {ip}:{port}. "
                                 f"¿Esta el backend en la misma red del PLC y encendido? Detalle: {e}")
    try:
        plc_maestro.aplicar_config(plc, cfg, dry_run=False)
    except Exception as e:
        raise HTTPException(500, f"Error escribiendo al PLC: {e}")
    finally:
        plc.close()

    log.info(f"/aplicar-plc OK — {len(cfg.get('outputs', []))} salida(s) -> {ip}:{port}")
    return {"status": "ok", "enviado": True, "plc": f"{ip}:{port}",
            "salidas": len(cfg.get("outputs", [])), "plan": plan_legible}


@app.post("/feedback")
def registrar_feedback(req: FeedbackRequest):
    """Marca un ejemplo de la memoria como accepted / corrected / rejected.
    Los accepted/corrected se inyectan como contexto en futuras peticiones."""
    if req.status not in ("accepted", "corrected", "rejected"):
        raise HTTPException(400, "status debe ser: accepted, corrected o rejected.")
    try:
        e = aplicar_feedback(
            req.ejemplo_id, req.status, req.user_correction,
            req.error_explanation, req.final_ladder_json, req.tags_extra,
        )
    except KeyError:
        raise HTTPException(404, f"No existe el ejemplo '{req.ejemplo_id}'.")
    log.info(f"Feedback registrado: {req.ejemplo_id} -> {req.status}")
    return {"status": "ok",
            "ejemplo": {k: e.get(k) for k in ("id", "status", "tags", "date")}}


@app.get("/memoria")
def ver_memoria():
    """Resumen de la memoria de aprendizaje por feedback."""
    ejemplos   = cargar_memoria()
    por_status = {}
    for e in ejemplos:
        s = e.get("status", "?")
        por_status[s] = por_status.get(s, 0) + 1
    return {
        "total":         len(ejemplos),
        "max_ejemplos":  MAX_EJEMPLOS,
        "por_status":    por_status,
        "ejemplos": [
            {"id": e.get("id"), "status": e.get("status"),
             "tags": e.get("tags", []), "uses": e.get("uses", 0),
             "user_prompt": (e.get("user_prompt") or "")[:120]}
            for e in ejemplos
        ],
    }


@app.delete("/memoria/{ejemplo_id}")
def borrar_ejemplo(ejemplo_id: str):
    """Elimina un ejemplo concreto de la memoria de feedback."""
    ejemplos  = cargar_memoria()
    filtrados = [e for e in ejemplos if e.get("id") != ejemplo_id]
    if len(filtrados) == len(ejemplos):
        raise HTTPException(404, f"No existe el ejemplo '{ejemplo_id}'.")
    guardar_memoria(filtrados)
    return {"status": "ok", "mensaje": f"Ejemplo {ejemplo_id} eliminado."}


# ─── Admin: generar contexto EN Render (usa la GROQ_API_KEY de Render) ──
# Flujo: 1) GET /admin/generar-contexto?token=...  (inicia, tarda minutos)
#        2) GET /admin/contexto-estado?token=...   (ver progreso)
#        3) GET /admin/contexto-json?token=...     (descargar y subir a git,
#           porque el disco de Render se borra en cada deploy)

ESTADO_GENERACION = {"estado": "inactivo", "detalle": [], "error": ""}


def _verificar_admin(token: str):
    if not ADMIN_TOKEN:
        raise HTTPException(403, (
            "Endpoint deshabilitado: define la variable de entorno ADMIN_TOKEN "
            "en Render (Environment) con una contraseña que tu elijas."
        ))
    if token != ADMIN_TOKEN:
        raise HTTPException(401, "Token incorrecto. Usa ?token=TU_ADMIN_TOKEN")


def _generar_contexto_en_servidor():
    try:
        import preparar_contexto
        datos = preparar_contexto.generar_datos(
            "codigos",
            progreso=lambda m: ESTADO_GENERACION["detalle"].append(str(m)),
        )
        os.makedirs(os.path.dirname(CONTEXTO_JSON_PATH), exist_ok=True)
        with open(CONTEXTO_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(datos, f, indent=2, ensure_ascii=False)

        # Aplicar de inmediato sin reiniciar el servidor
        STATE["system_prompt"]      = construir_system_prompt(datos)
        STATE["contexto_chars"]     = len(STATE["system_prompt"])
        STATE["contexto_programas"] = len(datos.get("programas", []))

        ESTADO_GENERACION["estado"] = "completado"
        ESTADO_GENERACION["detalle"].append(
            f"LISTO: {datos['total_programas']} programas, "
            f"{datos['total_renglones']} renglones. Descarga el JSON en "
            "/admin/contexto-json y subelo a git para que sobreviva los deploys."
        )
        log.info("Contexto generado en servidor y aplicado en caliente.")
    except Exception as e:
        ESTADO_GENERACION["estado"] = "error"
        ESTADO_GENERACION["error"]  = str(e)
        log.error(f"Error generando contexto en servidor: {e}")


@app.get("/admin/generar-contexto")
def admin_generar_contexto(token: str = ""):
    """Procesa los PDF de codigos/ con Groq Vision usando la clave de Render."""
    _verificar_admin(token)
    if ESTADO_GENERACION["estado"] == "procesando":
        return {"status": "ya_en_curso", "detalle": ESTADO_GENERACION["detalle"][-3:]}
    ESTADO_GENERACION.update({"estado": "procesando", "detalle": [], "error": ""})
    threading.Thread(target=_generar_contexto_en_servidor, daemon=True).start()
    return {
        "status":  "iniciado",
        "mensaje": "Procesando PDFs con Vision (tarda unos minutos). "
                   "Consulta el avance en /admin/contexto-estado?token=...",
    }


@app.get("/admin/contexto-estado")
def admin_contexto_estado(token: str = ""):
    _verificar_admin(token)
    return {
        "estado":          ESTADO_GENERACION["estado"],
        "error":           ESTADO_GENERACION["error"],
        "progreso":        ESTADO_GENERACION["detalle"][-10:],
        "archivo_existe":  os.path.exists(CONTEXTO_JSON_PATH),
        "programas_activos": STATE["contexto_programas"],
    }


@app.get("/admin/contexto-json")
def admin_contexto_json(token: str = ""):
    """Descarga el contexto.json generado (para subirlo a git)."""
    _verificar_admin(token)
    if not os.path.exists(CONTEXTO_JSON_PATH):
        raise HTTPException(404, "Aun no existe contexto.json. Genera primero con /admin/generar-contexto")
    return FileResponse(CONTEXTO_JSON_PATH, media_type="application/json",
                        filename="contexto.json")


@app.get("/admin/memoria-json")
def admin_memoria_json(token: str = ""):
    """Descarga la memoria de feedback completa (para subirla a git antes
    de que un deploy o el sueño de Render borren el disco)."""
    _verificar_admin(token)
    if not os.path.exists(MEMORIA_PATH):
        raise HTTPException(404, "Aun no existe la memoria de feedback en este servidor.")
    return FileResponse(MEMORIA_PATH, media_type="application/json",
                        filename="ejemplos.json")


@app.get("/historial")
def ver_historial():
    """Muestra las preguntas que el modelo tiene como contexto activo."""
    preguntas = [
        STATE["history"][i]["content"][:120]
        for i in range(0, len(STATE["history"]), 2)
    ]
    return {
        "pares_en_ram":  len(STATE["history"]) // 2,
        "max_pares":     MAX_HISTORY,
        "max_historial": MAX_HISTORIAL,
        "preguntas":     preguntas,
    }


@app.delete("/historial")
def limpiar_historial():
    """Resetea el historial en RAM y borra historial.json del disco."""
    STATE["history"] = []
    if os.path.exists(HISTORIAL_PATH):
        os.remove(HISTORIAL_PATH)
    log.info("Historial limpiado.")
    return {"status": "ok", "mensaje": "Historial limpiado."}
