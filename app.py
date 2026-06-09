# app.py — LadderVoice Backend v2.0  (archivo unificado)
# Generador : Groq (openai/gpt-oss-120b)
# STT       : Groq Whisper
# Contexto  : context_json/contexto.json  (generado por preparar_contexto.py)
# Historial : respuestas/historial.json   (persistente entre reinicios)

import os, re, json, logging, datetime
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
log = logging.getLogger(__name__)

# ─── Modelos y clientes Groq ─────────────────────────────────────

MODELO        = "openai/gpt-oss-120b"
MODELO_STT    = os.environ.get("GROQ_STT_MODEL",    "whisper-large-v3")
IDIOMA_STT    = os.environ.get("GROQ_STT_LANGUAGE", "es")
MAX_AUDIO_MB  = int(os.environ.get("MAX_AUDIO_MB",  "25"))

GROQ_API_KEY     = os.environ.get("GROQ_API_KEY")
GROQ_API_KEY_STT = os.environ.get("GROQ_API_KEY_stt")

groq_client     = Groq(api_key=GROQ_API_KEY)
groq_client_stt = Groq(api_key=GROQ_API_KEY_STT) if GROQ_API_KEY_STT else None

# ─── Rutas de archivos ────────────────────────────────────────────

CONTEXTO_JSON_PATH = os.environ.get("CONTEXTO_JSON", "context_json/contexto.json")
HISTORIAL_PATH     = "respuestas/historial.json"

# Cuantos pares Q&A mantener en RAM (contexto activo del modelo)
MAX_HISTORY   = 5
# Cuantas entradas guardar en historial.json (memoria a largo plazo)
MAX_HISTORIAL = 50

# ─── Variables PLC ────────────────────────────────────────────────

VARIABLES_PLC = """
PLC     : Horner XL4 / XC1E5 | Software: Cscape 10.2 | 24VDC
Red     : IP 192.168.1.100 | Puerto Modbus TCP 502

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
            {"tipo": "XIC", "operando": "%I1", "descripcion": "string"}
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


def mk_el(tipo_raw, operando, col, uid):
    t = TIPO_MAP.get(str(tipo_raw).strip().upper(),
        TIPO_MAP.get(str(tipo_raw).strip(), "contact_no"))
    a = norm(operando)
    e = {"id": uid, "type": t, "address": a, "pos": {"col": col}}
    if t == "coil":    e["coil_type"] = "output"
    if t == "coil_s":  e["coil_type"] = "set"
    if t == "coil_r":  e["coil_type"] = "reset"
    if t in ("block_ton", "block_tof"): e["params"] = {"preset_ms": 1000}
    if t in ("block_ctu", "block_ctd"): e["params"] = {"preset": 10}
    if t == "block_cmp": e["params"] = {"op": "EQ", "value": 0}
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
                         c, f"{pfx}f{fn}c{c}")
                   for c, e in enumerate(fila.get("elementos", []))]
            net.append({"row": fn, "elements": els})
        if not net:
            net = [{"row": 0, "elements": []}]
        return {"id": num, "enabled": True, "comment": desc, "network": net}

    # Formato legado con "elementos" plano
    raw   = renglon.get("elementos", [])
    todos = [{"tipo": str(e.get("tipo", "")).strip().upper(),
               "op":  e.get("operando", "")} for e in raw]

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
            f0.append(mk_el(e["tipo"], e["op"], col, f"{pfx}f0c{col}"))
            col += 1
    net = [{"row": 0, "elements": f0}]

    if paralelos:
        f1 = [mk_el(todos[i]["tipo"], todos[i]["op"], c, f"{pfx}f1c{c}")
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

# ─── Helpers de prompt ────────────────────────────────────────────

def es_enclavamiento(pregunta: str) -> bool:
    p = pregunta.lower()
    return any(w in p for w in PALABRAS_ENCLAVAMIENTO)


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
    "https://sebas30073007.github.io"
).split(",")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGINS if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# ─── Modelos Pydantic ─────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str


class LadderResponse(BaseModel):
    program: dict
    nombre: str
    rungs: int
    ramas_paralelas: int
    variables: int
    es_enclavamiento: bool
    js_content: str      # JS completo listo para importar en el frontend
    historia_pares: int  # pares Q&A que el modelo tiene como contexto


class STTResponse(BaseModel):
    texto: str
    modelo: str
    idioma: str
    archivo: str


class VozLadderResponse(BaseModel):
    texto: str
    stt: STTResponse
    ladder: LadderResponse

# ─── Logica principal Ladder ──────────────────────────────────────

def consultar_retorna_schema(pregunta: str) -> tuple:
    pregunta_reforzada = f"""{pregunta}

REGLAS IMPORTANTES:
- Usa unicamente las entradas, salidas, marcas, temporizadores o contadores que el usuario pidio explicitamente.
- No agregues paro de emergencia, sensores, salidas ni marcas extra si el usuario no los menciono.
"""
    mensaje_usuario = construir_mensaje_usuario(pregunta_reforzada)

    # Sistema + historial acumulado + pregunta nueva
    messages = [{"role": "system", "content": STATE["system_prompt"]}]
    messages.extend(STATE["history"])
    messages.append({"role": "user", "content": mensaje_usuario})

    log.info(f"Modelo: {MODELO} | historial en RAM: {len(STATE['history'])//2} pares")

    resp = groq_client.chat.completions.create(
        messages=messages,
        model=MODELO,
        temperature=1,
        max_tokens=2048,
        response_format={"type": "json_object"},
    )
    texto_raw = resp.choices[0].message.content
    ti = resp.usage.prompt_tokens
    ts = resp.usage.completion_tokens
    log.info(f"Tokens — entrada: {ti}  salida: {ts}  total: {ti+ts}")

    try:
        datos = json.loads(texto_raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON invalido del modelo: {e}\n{texto_raw[:300]}")

    ok, msg = validar_enclavamiento(datos, pregunta)
    if not ok:
        log.warning(msg)
    else:
        log.info(msg)

    schema    = a_schema(datos)
    js_string = schema_a_js_string(schema, pregunta)

    # Actualizar historial en RAM
    STATE["history"].append({"role": "user",     "content": pregunta})
    STATE["history"].append({"role": "assistant", "content": texto_raw})
    if len(STATE["history"]) > MAX_HISTORY * 2:
        STATE["history"] = STATE["history"][-(MAX_HISTORY * 2):]

    # Persistir en disco
    guardar_historial(pregunta, texto_raw)
    try:
        guardar_js(datos, pregunta)
    except Exception as e:
        log.warning(f"No se pudo guardar .js local: {e}")

    return datos, schema, js_string


def crear_ladder_response(prompt: str, schema: dict, js_string: str) -> LadderResponse:
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

@app.get("/")
def root():
    return {
        "service":            "LadderVoice Backend",
        "version":            "2.0.0",
        "status":             "ok",
        "contexto_programas": STATE["contexto_programas"],
        "contexto_chars":     STATE["contexto_chars"],
        "historia_pares":     len(STATE["history"]) // 2,
        "docs":               "/docs",
        "endpoints": {
            "health":             "/health",
            "stt":                "POST /transcribir",
            "voz_a_ladder":       "POST /voz-a-ladder",
            "ladder":             "POST /generar-ladder",
            "historial_ver":      "GET  /historial",
            "historial_limpiar":  "DELETE /historial",
        },
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
        datos, schema, js_string = consultar_retorna_schema(prompt)
        return VozLadderResponse(
            texto=prompt, stt=stt,
            ladder=crear_ladder_response(prompt, schema, js_string),
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
        datos, schema, js_string = consultar_retorna_schema(req.prompt.strip())
        return crear_ladder_response(req.prompt.strip(), schema, js_string)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:
        log.error(f"Error generar-ladder: {e}")
        raise HTTPException(500, str(e))


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
