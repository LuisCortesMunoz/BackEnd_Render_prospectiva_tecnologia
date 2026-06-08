# app.py — Backend LadderVoice
# Servidor FastAPI que combina:
#   - Groq Vision para leer PDFs de la carpeta codigos/
#   - Modelo generador para crear programas Ladder desde texto
# Despliegue: Render.com

import os
import sys
import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s — %(levelname)s — %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Importar funciones del módulo principal
# ─────────────────────────────────────────────────────────────────
from test_con_contexto import (
    cargar_carpeta_vision,
    construir_system_prompt,
    construir_mensaje_usuario,
    validar_enclavamiento,
    a_schema,
    guardar_js,
    groq_client,
    MODELO,
    es_enclavamiento,
)
import json as _json

# ─────────────────────────────────────────────────────────────────
# Estado global — se carga UNA vez al iniciar
# ─────────────────────────────────────────────────────────────────
STATE = {"system_prompt": "", "contexto_chars": 0}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Carga los PDFs con Vision al arrancar el servidor."""
    carpeta = os.environ.get("CODIGOS_PATH", "codigos")
    log.info(f"Cargando PDFs desde '{carpeta}/' con Groq Vision...")
    try:
        contexto = cargar_carpeta_vision(carpeta)
        if not contexto:
            log.warning("No se encontraron PDFs — el contexto estará vacío.")
            contexto = "(sin programas de referencia)"
        STATE["system_prompt"]   = construir_system_prompt(contexto)
        STATE["contexto_chars"]  = len(contexto)
        log.info(f"Contexto listo — {STATE['contexto_chars']} chars")
    except Exception as e:
        log.error(f"Error cargando contexto: {e}")
        STATE["system_prompt"]  = construir_system_prompt("(sin programas de referencia)")
        STATE["contexto_chars"] = 0
    yield  # servidor corriendo
    log.info("Servidor detenido.")

# ─────────────────────────────────────────────────────────────────
# App FastAPI
# ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="LadderVoice Backend",
    description="Genera programas Ladder para PLC Horner desde lenguaje natural",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — permite peticiones desde GitHub Pages y localhost
ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:5500,https://sebas30073007.github.io"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────
# Modelos de request / response
# ─────────────────────────────────────────────────────────────────
class PromptRequest(BaseModel):
    prompt: str

class LadderResponse(BaseModel):
    program: dict
    nombre:  str
    rungs:   int
    ramas_paralelas: int
    variables: int
    es_enclavamiento: bool

# ─────────────────────────────────────────────────────────────────
# Función principal — consulta al modelo y retorna el schema
# ─────────────────────────────────────────────────────────────────
def consultar_retorna_schema(pregunta: str) -> tuple[dict, dict]:
    """
    Llama al modelo Groq con el system prompt cargado,
    convierte la respuesta al schema del editor Ladder
    y retorna (datos_raw, schema).
    """
    system_prompt   = STATE["system_prompt"]
    mensaje_usuario = construir_mensaje_usuario(pregunta)

    log.info(f"Consultando modelo: {MODELO}")
    respuesta = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": mensaje_usuario},
        ],
        model=MODELO,
        temperature=1,
        max_tokens=2048,
        response_format={"type": "json_object"},
    )

    texto_raw = respuesta.choices[0].message.content
    ti = respuesta.usage.prompt_tokens
    ts = respuesta.usage.completion_tokens
    log.info(f"Tokens — entrada: {ti}  salida: {ts}  total: {ti+ts}")

    try:
        datos = _json.loads(texto_raw)
    except _json.JSONDecodeError as e:
        raise ValueError(f"JSON inválido del modelo: {e}\n{texto_raw[:300]}")

    # Validar enclavamiento
    ok, msg = validar_enclavamiento(datos, pregunta)
    if not ok:
        log.warning(f"Enclavamiento sin rama paralela: {msg}")
    else:
        log.info(f"Validación: {msg}")

    schema = a_schema(datos)

    # Guardar copia local en respuestas/ (opcional en producción)
    try:
        guardar_js(datos, pregunta)
    except Exception as e:
        log.warning(f"No se pudo guardar .js local: {e}")

    return datos, schema


# ─────────────────────────────────────────────────────────────────
# ENDPOINTS
# ─────────────────────────────────────────────────────────────────
@app.get("/")
def root():
    return {
        "service":        "LadderVoice Backend",
        "version":        "1.0.0",
        "status":         "ok",
        "contexto_chars": STATE["contexto_chars"],
        "docs":           "/docs",
    }

@app.get("/health")
def health():
    return {
        "status":         "ok",
        "contexto_chars": STATE["contexto_chars"],
        "modelo":         MODELO,
    }

@app.post("/generar-ladder", response_model=LadderResponse)
async def generar_ladder(req: PromptRequest):
    """
    Recibe un prompt en lenguaje natural y retorna
    el schema JSON listo para cargar en el editor Ladder.
    """
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="El prompt no puede estar vacío.")

    if len(req.prompt) > 2000:
        raise HTTPException(status_code=400, detail="El prompt es demasiado largo (máx 2000 chars).")

    try:
        datos, schema = consultar_retorna_schema(req.prompt.strip())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        log.error(f"Error generando Ladder: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

    rungs    = schema.get("rungs", [])
    n_rungs  = len(rungs)
    n_ramas  = sum(len(r["network"]) - 1 for r in rungs if len(r["network"]) > 1)
    n_vars   = len(schema.get("symbol_table", {}))
    nombre   = schema.get("metadata", {}).get("name", "Programa")

    return LadderResponse(
        program          = schema,
        nombre           = nombre,
        rungs            = n_rungs,
        ramas_paralelas  = n_ramas,
        variables        = n_vars,
        es_enclavamiento = es_enclavamiento(req.prompt),
    )
