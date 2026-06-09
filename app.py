# app.py — Backend LadderVoice
# Generador : Groq  (openai/gpt-oss-120b)
# STT       : Groq Whisper
# Vision    : Groq Vision desde test_con_contexto.py

import os
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
# Configuración STT
# ─────────────────────────────────────────────────────────────────

# En Render crea esta variable exactamente así:
# GROQ_API_KEY_stt
GROQ_API_KEY_STT = os.environ.get("GROQ_API_KEY_stt")

# Opcionales en Render
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
            log.warning("No se encontraron PDFs — contexto vacío.")
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
    description="Genera programas Ladder desde lenguaje natural y transcribe voz con STT",
    version="1.2.0",
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


class VozLadderResponse(BaseModel):
    texto: str
    stt: STTResponse
    ladder: LadderResponse


# ─────────────────────────────────────────────────────────────────
# Función principal Ladder
# ─────────────────────────────────────────────────────────────────

def consultar_retorna_schema(pregunta: str) -> tuple:
    system_prompt = STATE["system_prompt"]

    # Regla extra para evitar que el modelo agregue entradas no pedidas,
    # por ejemplo paro de emergencia I8 si el usuario no lo mencionó.
    pregunta_reforzada = f"""
{pregunta}

REGLAS IMPORTANTES:
- Usa únicamente las entradas, salidas, marcas, temporizadores o contadores que el usuario pidió explícitamente.
- No agregues paro de emergencia, sensores, salidas ni marcas extra si el usuario no los mencionó.
- Si el usuario pide botón de inicio I1, botón de paro I2 y salida Q10, usa solo I1, I2, Q10 y una marca interna si es necesaria para enclavamiento.
"""

    mensaje_usuario = construir_mensaje_usuario(pregunta_reforzada)

    log.info(f"Consultando modelo Ladder: {MODELO}")

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
        raise ValueError(f"JSON inválido del modelo: {e}\n{texto_raw[:300]}")

    ok, msg = validar_enclavamiento(datos, pregunta)

    if not ok:
        log.warning(f"Enclavamiento sin rama paralela: {msg}")
    else:
        log.info(f"Validación: {msg}")

    schema = a_schema(datos)

    try:
        guardar_js(datos, pregunta)
    except Exception as e:
        log.warning(f"No se pudo guardar .js local: {e}")

    return datos, schema


def crear_ladder_response(prompt: str, schema: dict) -> LadderResponse:
    rungs = schema.get("rungs", [])
    n_rungs = len(rungs)
    n_ramas = sum(len(r["network"]) - 1 for r in rungs if len(r.get("network", [])) > 1)
    n_vars = len(schema.get("symbol_table", {}))
    nombre = schema.get("metadata", {}).get("name", "Programa")

    return LadderResponse(
        program=schema,
        nombre=nombre,
        rungs=n_rungs,
        ramas_paralelas=n_ramas,
        variables=n_vars,
        es_enclavamiento=es_enclavamiento(prompt),
    )


# ─────────────────────────────────────────────────────────────────
# Utilidades STT
# ─────────────────────────────────────────────────────────────────

def obtener_nombre_audio(content_type: Optional[str], filename: Optional[str]) -> str:
    """
    Ayuda a darle extensión correcta al archivo cuando el navegador
    manda algo como 'blob' sin extensión.
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


async def transcribir_uploadfile(archivo_audio: UploadFile) -> STTResponse:
    if groq_client_stt is None:
        raise HTTPException(
            status_code=500,
            detail="No se encontró GROQ_API_KEY_stt en las variables de entorno de Render.",
        )

    audio_bytes = await archivo_audio.read()

    if not audio_bytes:
        raise HTTPException(
            status_code=400,
            detail="El archivo de audio está vacío.",
        )

    max_bytes = MAX_AUDIO_MB * 1024 * 1024

    if len(audio_bytes) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"El audio supera el límite de {MAX_AUDIO_MB} MB.",
        )

    nombre_archivo = obtener_nombre_audio(
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

    texto = getattr(transcripcion, "text", "") or ""

    return STTResponse(
        texto=texto.strip(),
        modelo=GROQ_STT_MODEL,
        idioma=GROQ_STT_LANGUAGE,
        archivo=nombre_archivo,
    )


def seleccionar_archivo_audio(audio: Optional[UploadFile], file: Optional[UploadFile]) -> UploadFile:
    archivo_audio = audio or file

    if archivo_audio is None:
        raise HTTPException(
            status_code=400,
            detail="No se recibió archivo de audio. Usa FormData con campo 'audio'.",
        )

    return archivo_audio


# ─────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service": "LadderVoice Backend",
        "version": "1.2.0",
        "status": "ok",
        "contexto_chars": STATE["contexto_chars"],
        "docs": "/docs",
        "endpoints": {
            "health": "/health",
            "stt": "/transcribir",
            "voz_a_ladder": "/voz-a-ladder",
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
    Acepta FormData con campo 'audio' o 'file'.
    """

    try:
        archivo_audio = seleccionar_archivo_audio(audio, file)
        return await transcribir_uploadfile(archivo_audio)

    except HTTPException:
        raise

    except Exception as e:
        log.error(f"Error en STT: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error transcribiendo audio: {str(e)}",
        )


@app.post("/voz-a-ladder", response_model=VozLadderResponse)
async def voz_a_ladder(
    audio: Optional[UploadFile] = File(None),
    file: Optional[UploadFile] = File(None),
):
    """
    Flujo completo:
    1. Recibe audio.
    2. Transcribe con STT.
    3. Manda el texto transcrito al modelo generador de Ladder.
    4. Regresa texto + programa ladder.
    """

    try:
        archivo_audio = seleccionar_archivo_audio(audio, file)
        stt = await transcribir_uploadfile(archivo_audio)

        prompt = stt.texto.strip()

        if not prompt:
            raise HTTPException(
                status_code=422,
                detail="La transcripción salió vacía. Intenta hablar más claro o grabar de nuevo.",
            )

        if len(prompt) > 2000:
            raise HTTPException(
                status_code=400,
                detail="La transcripción es demasiado larga, máximo 2000 caracteres.",
            )

        datos, schema = consultar_retorna_schema(prompt)
        ladder_response = crear_ladder_response(prompt, schema)

        return VozLadderResponse(
            texto=prompt,
            stt=stt,
            ladder=ladder_response,
        )

    except HTTPException:
        raise

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        log.error(f"Error en voz-a-ladder: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error generando Ladder desde voz: {str(e)}",
        )


@app.post("/generar-ladder", response_model=LadderResponse)
async def generar_ladder(req: PromptRequest):
    if not req.prompt or not req.prompt.strip():
        raise HTTPException(status_code=400, detail="El prompt no puede estar vacío.")

    if len(req.prompt) > 2000:
        raise HTTPException(status_code=400, detail="Prompt demasiado largo, máximo 2000 caracteres.")

    try:
        datos, schema = consultar_retorna_schema(req.prompt.strip())
        return crear_ladder_response(req.prompt.strip(), schema)

    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        log.error(f"Error generando Ladder: {e}")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")