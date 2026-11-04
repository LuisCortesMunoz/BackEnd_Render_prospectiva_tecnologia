"""
test_no_bloquea.py — una generacion lenta ya no congela el servidor.

Corre sin Groq y sin PLC:
    python test_no_bloquea.py

La llamada a Groq es sincrona (y si Groq responde 429 se espera con
time.sleep). Antes corria dentro del event loop y, mientras tanto, ninguna
otra peticion avanzaba: /banda/estado, paros, resets, long-poll de lentes.
Aqui la "llamada a Groq" se simula con time.sleep y se mide que el event loop
siga atendiendo otras tareas mientras dura.
"""

import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_nb_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ.pop("APP_PASSWORD", None)
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import warnings
warnings.filterwarnings("ignore")

import asyncio
import time

from fastapi import HTTPException

import app
import metricas_lavo

OK = 0
ESPERA = 1.0     # lo que "tarda Groq" en cada generacion simulada
TICK = 0.05      # cada cuanto avanza la otra tarea


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1


async def con_ticker(coro):
    """Corre coro y cuenta cuantas veces avanzo, mientras, otra tarea."""
    ticks = 0
    fin = False

    async def ticker():
        nonlocal ticks
        while not fin:
            await asyncio.sleep(TICK)
            ticks += 1

    t = asyncio.create_task(ticker())
    t0 = time.perf_counter()
    try:
        resultado = await coro
    except HTTPException as e:
        resultado = e
    dur = time.perf_counter() - t0
    fin = True
    await t
    return resultado, ticks, dur


# ── 1. /generar-logica: la generacion lenta no bloquea el event loop ──
original = app._generar_logica


def generacion_lenta(req):
    time.sleep(ESPERA)                          # como la llamada a Groq
    metricas_lavo.sumar("llamadas_modelo", 1)   # las metricas cruzan al hilo
    raise HTTPException(400, "simulado")


app._generar_logica = generacion_lenta
try:
    r, ticks, dur = asyncio.run(con_ticker(app.generar_logica(app.LogicaRequest(texto="x"))))
finally:
    app._generar_logica = original
check(isinstance(r, HTTPException) and r.status_code == 400, "el error de la generacion llega igual")
check(dur >= ESPERA, "la generacion simulada si tardo")
check(ticks >= int(ESPERA / TICK) // 2, f"el event loop siguio atendiendo durante la generacion ({ticks} ticks)")
check(metricas_lavo.HISTORIAL[-1]["llamadas_modelo"] == 1, "metricas_lavo ve lo que sumo el hilo")
check(metricas_lavo.HISTORIAL[-1]["status"] == "error_400", "la medicion se cierra con el error")
print(f"1. /generar-logica no bloquea ({ticks} ticks en {dur:.2f} s) (OK)")

# ── 2. /generar-ladder: igual ──
original_c = app.consultar_retorna_schema


def consulta_lenta(*args):
    time.sleep(ESPERA)
    raise ValueError("simulado")


app.consultar_retorna_schema = consulta_lenta
try:
    r, ticks, dur = asyncio.run(con_ticker(app.generar_ladder(app.PromptRequest(prompt="x"))))
finally:
    app.consultar_retorna_schema = original_c
check(isinstance(r, HTTPException) and r.status_code == 422, "ValueError sigue saliendo como 422")
check(ticks >= int(ESPERA / TICK) // 2, f"generar-ladder no bloquea ({ticks} ticks)")
print(f"2. /generar-ladder no bloquea ({ticks} ticks en {dur:.2f} s) (OK)")

# ── 3. Dos generaciones a la vez van en fila (limite de tokens, memoria) ──
en_curso = 0
max_en_curso = 0


def generacion_contada(req):
    global en_curso, max_en_curso
    en_curso += 1
    max_en_curso = max(max_en_curso, en_curso)
    time.sleep(ESPERA / 2)
    en_curso -= 1
    raise HTTPException(400, "simulado")


async def dos_a_la_vez():
    return await asyncio.gather(
        app.generar_logica(app.LogicaRequest(texto="a")),
        app.generar_logica(app.LogicaRequest(texto="b")),
        return_exceptions=True)


app._generar_logica = generacion_contada
try:
    r, ticks, dur = asyncio.run(con_ticker(dos_a_la_vez()))
finally:
    app._generar_logica = original
check(max_en_curso == 1, "nunca corren dos generaciones al mismo tiempo")
check(dur >= ESPERA * 0.95, "la segunda espero a la primera")
check(ticks >= int(ESPERA / TICK) // 2, f"y mientras esperan el event loop sigue libre ({ticks} ticks)")
print(f"3. dos generaciones van en fila sin bloquear ({ticks} ticks en {dur:.2f} s) (OK)")

print(f"\nTODAS LAS PRUEBAS DE NO BLOQUEO PASARON ({OK} comprobaciones).")
