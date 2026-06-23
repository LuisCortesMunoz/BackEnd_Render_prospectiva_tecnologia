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
| `GROQ_CHAT_MODEL` | Modelo del chat (default `llama-3.3-70b-versatile`) | No |
| `GROQ_STT_MODEL` | Modelo de voz (default `whisper-large-v3`) | No |
| `ADMIN_TOKEN` | Protege los endpoints `/admin/*` | Recomendada |
| `ALLOWED_ORIGINS` | Dominios del frontend (CORS) | No |

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

## Instalación local

```powershell
pip install -r requirements.txt
```
