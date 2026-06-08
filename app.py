# app.py — Backend LadderVoice
# Generador : Groq  (openai/gpt-oss-120b)
# STT       : Groq Whisper
# Vision    : Groq Vision desde test_con_contexto.py

import os
import sys
import json
import logging
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

# ─────────────────────────────────────────────────────────────────
# Configuracion STT
# ─────────────────────────────────────────────────────────────────

# IMPORTANTE:
# En Render debes crear una variable de entorno llamada exactamente:
# GROQ_API_KEY_stt
GROQ_API_KEY_STT = os.environ.get("GROQ_API_KEY_stt")

# Opcionales en Render:
GROQ_STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")
GROQ_STT_LANGUAGE = os.environ.get("GROQ_STT_LANGUAGE", "es")
MAX_AUDIO_MB = int(os.environ.get("MAX_AUDIO_MB", "25"))

groq_client_stt = Groq(api_key=GROQ_API_KEY_STT) if GROQ_API_KEY_STT else None


# ─────────────────────────────────────────────────────────────────
# Estado global
# ─────────────────────────────────────────────────────────────────

STATE = {
    "system_prompt": "",
    "contexto_chars": 0,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    carpeta = os.environ.get("CODIGOS_PATH", "codigos")
    log.info(f"Cargando PDFs desde '{carpeta}/' con Vision...")

    try:
        contexto = cargar_carpeta_vision(carpeta)

        if not contexto:
            log.warning("No se encontraron PDFs — contexto vacio.")
            contexto = "(sin programas de referencia)"

        STATE["system_prompt"] = construir_system_prompt(contexto)
        STATE["contexto_chars"] = len(contexto)

        log.info(f"Contexto listo — {STATE['contexto_chars']} chars")

    except Exception as e:
        log.error(f"Error cargando contexto: {e}")
        STATE["system_prompt"] = construir_system_prompt("(sin programas de referencia)")
        STATE["contexto_chars"] = 0

    yield

    log.info("Servidor detenido.")


# ─────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="LadderVoice Backend",
    description="Genera programas Ladder para PLC Horner desde lenguaje natural y transcribe voz con STT",
    version="1.1.0",
    lifespan=lifespan,
)

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS",
    "http://localhost:3000,"
    "http://localhost:5173,"
    "http://127.0.0.1:5500,"
    "http://127.0.0.1:5173,"
    "https://sebas30073007.github.io"
).split(",")

ALLOWED_ORIGINS = [origin.strip() for origin in ALLOWED_ORIGINS if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────
# Modelos request/response
# ─────────────────────────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str


class LadderResponse(BaseModel):
    program: dict
    nombre: str
    rungs: int
    ramas_paralelas: int
    variables: int
    es_enclavamiento: bool


class STTResponse(BaseModel):
    texto: str
    modelo: str
    idioma: str
    archivo: str


# ─────────────────────────────────────────────────────────────────
# Funcion principal Ladder
# ─────────────────────────────────────────────────────────────────

def consultar_retorna_schema(pregunta: str) -> tuple:
    system_prompt = STATE["system_prompt"]
    mensaje_usuario = construir_mensaje_usuario(pregunta)

    log.info(f"Consultando modelo: {MODELO}")

    respuesta = groq_client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": mensaje_usuario},
        ],
        model=MODELO,
        temperature=1,
        max_tokens=2048,
        response_format={"type": "json_object"},
    )

    texto_raw = respuesta.choices[0].message.content

    ti = respuesta.usage.prompt_tokens
    ts = respuesta.usage.completion_tokens

    log.info(f"Tokens — entrada: {ti}  salida: {ts}  total: {ti + ts}")

    try:
        datos = json.loads(texto_raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON invalido del modelo: {e}\n{texto_raw[:300]}")

    ok, msg = validar_enclavamiento(datos, pregunta)

    if not ok:
        log.warning(f"Enclavamiento sin rama paralela: {msg}")
    else:
        log.info(f"Validacion: {msg}")

    schema = a_schema(datos)

    try:
        guardar_js(datos, pregunta)
    except Exception as e:
        log.warning(f"No se pudo guardar .js local: {e}")

    return datos, schema


# ─────────────────────────────────────────────────────────────────
# Utilidad STT
# ─────────────────────────────────────────────────────────────────

def obtener_extension_audio(content_type: Optional[str], filename: Optional[str]) -> str:
    """
    Ayuda a darle una extension correcta al archivo cuando el navegador
    manda algo como 'blob' sin extension.
    """

    if filename and "." in filename:
        return filename

    mapa = {
        "audio/webm": "audio.webm",
        "audio/wav": "audio.wav",
        "audio/x-wav": "audio.wav",
        "audio/mpeg": "audio.mp3",
        "audio/mp3": "audio.mp3",
        "audio/mp4": "audio.m4a",
        "audio/ogg": "audio.ogg",
    }

    return mapa.get(content_type or "", "audio.webm")


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service": "LadderVoice Backend",
        "version": "1.1.0",
        "status": "ok",
        "contexto_chars": STATE["contexto_chars"],
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "stt": "/transcribir",
            "ladder": "/generar-ladder",
        },
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "contexto_chars": STATE["contexto_chars"],
        "modelo_ladder": MODELO,
        "stt_configurado": groq_client_stt is not None,
        "modelo_stt": GROQ_STT_MODEL,
        "idioma_stt": GROQ_STT_LANGUAGE,
    }


@app.post("/transcribir", response_model=STTResponse)
async def transcribir_audio(
    audio: Optional[UploadFile] = File(None),
    file: Optional[UploadFile] = File(None),
):
    """
    Recibe audio desde el frontend y lo transcribe con Groq Whisper.

    Acepta el archivo con nombre de campo:
    - audio
    - file

    Esto ayuda por si el frontend manda FormData con cualquiera de esos nombres.
    """

    if groq_client_stt is None:
        raise HTTPException(
            status_code=500,
            detail="No se encontro GROQ_API_KEY_stt en las variables de entorno de Render.",
        )

    archivo_audio = audio or file

    if archivo_audio is None:
        raise HTTPException(
            status_code=400,
            detail="No se recibio archivo de audio. Usa FormData con campo 'audio'.",
        )

    try:
        audio_bytes = await archivo_audio.read()

        if not audio_bytes:
            raise HTTPException(
                status_code=400,
                detail="El archivo de audio esta vacio.",
            )

        max_bytes = MAX_AUDIO_MB * 1024 * 1024

        if len(audio_bytes) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"El audio supera el limite de {MAX_AUDIO_MB} MB.",
            )

        nombre_archivo = obtener_extension_audio(
            archivo_audio.content_type,
            archivo_audio.filename,
        )

        log.info(
            f"Transcribiendo audio '{nombre_archivo}' "
            f"({len(audio_bytes)} bytes, tipo={archivo_audio.content_type}) "
            f"con modelo {GROQ_STT_MODEL}"
        )

        transcripcion = groq_client_stt.audio.transcriptions.create(
            file=(nombre_archivo, audio_bytes),
            model=GROQ_STT_MODEL,
            language=GROQ_STT_LANGUAGE,
            response_format="json",
        )

        texto = getattr(transcripcion, "text", "")

        if not texto:
            texto = ""

        return STTResponse(
            texto=texto.strip(),
            modelo=GROQ_STT_MODEL,
            idioma=GROQ_STT_LANGUAGE,
            archivo=nombre_archivo,
        )

    except HTTPException:
        raise

    except Exception as e:
        log.error(f"Error en STT: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error transcribiendo audio: {str(e)}",
        )


@app.post("/generar-ladder", response_model=LadderResponse)
async def generar_ladder(req: PromptRequest):
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="El prompt no puede estar vacio.")

    if len(req.prompt) > 2000:
        raise HTTPException(status_code=400, detail="Prompt demasiado largo, maximo 2000 caracteres.")

    try:
        datos, schema = consultar_retorna_schema(req.prompt.strip())

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        log.error(f"Error generando Ladder: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")

    rungs = schema.get("rungs", [])
    n_rungs = len(rungs)
    n_ramas = sum(len(r["network"]) - 1 for r in rungs if len(r["network"]) > 1)
    n_vars = len(schema.get("symbol_table", {}))
    nombre = schema.get("metadata", {}).get("name", "Programa")

    return LadderResponse(
        program=schema,
        nombre=nombre,
        rungs=n_rungs,
        ramas_paralelas=n_ramas,
        variables=n_vars,
        es_enclavamiento=es_enclavamiento(req.prompt),
    )