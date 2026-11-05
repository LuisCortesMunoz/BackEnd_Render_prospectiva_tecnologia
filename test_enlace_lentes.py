"""
test_enlace_lentes.py — relevo lentes -> PC (simular en lentes, cargar en el PLC).

Corre sin Groq y sin PLC:
    python test_enlace_lentes.py

La PC elige equipo y PLC y espera; los lentes mandan la ultima configuracion
simulada; el backend la valida y se la entrega a la PC, que la carga sola e
informa el resultado.
"""

import os
import tempfile

os.environ["LAVO_MEMORIA_DIR"] = tempfile.mkdtemp(prefix="lavo_mem_enl_")
os.environ.pop("LAVO_MEMORIA_REPO", None)
os.environ.pop("LAVO_MEMORIA_TOKEN", None)
os.environ.pop("APP_PASSWORD", None)
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import logging
logging.disable(logging.WARNING)

import warnings
warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient

import enlace_lentes as el
import app

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1


BANDA = {"device": "banda", "name": "Banda con I1",
         "band": {"enable": True, "direction": "derecha", "freq_hz": 30,
                  "engine": {"version": 7, "motor": {"arranque": "I1", "paro": "I2"}}}}
MALETIN = {"device": "maletin", "name": "Enclavado",
           "outputs": [{"output": "Q10", "logic": {"mode": "enclavado", "start": "I1", "stop": "I2"}}]}


def sin_errores(cfg, dev):
    return []


class Reloj:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------------------
print("1. Modulo: abrir, enviar, recoger, resultado")
# ---------------------------------------------------------------------------
r = Reloj()
e = el.Enlace(reloj=r)
canal = e.abrir("banda", "192.168.3.12", 502, "Banda del lab")
check(canal["device"] == "banda" and canal["plc"]["ip"] == "192.168.3.12", "abre el canal con el PLC elegido")
check("192.168.3.12:502" in canal["plc_texto"], "texto del PLC para los lentes")
check(e.recoger(canal["canal_id"]) == {"status": "sin_envio"}, "sin envio todavia")
est = e.estado()
check(len(est["canales"]) == 1 and est["canales"][0]["pc_conectada"], "los lentes ven la PC esperando")
check(e.estado("maletin")["canales"] == [], "filtrar por equipo")

env = e.enviar("banda", BANDA, sin_errores, frases=["Haz que avance", " ", "Cambia a 30 Hz"])
check(env["status"] == "ok" and env["envio_id"], "los lentes envian")
check(e.consultar_envio(env["envio_id"])["estado"] == "pendiente", "pendiente hasta que la PC lo recoja")
got = e.recoger(canal["canal_id"])
check(got["status"] == "envio" and got["envio"]["logic"] == BANDA, "la PC recibe la configuracion intacta")
check(got["envio"]["plc"]["ip"] == "192.168.3.12", "el envio trae el PLC que eligio la PC")
check(got["envio"]["frases"] == ["Haz que avance", "Cambia a 30 Hz"], "frases limpias")
check(got["envio"]["nombre"] == "Banda con I1", "nombre tomado del programa")
check(e.recoger(canal["canal_id"])["status"] == "sin_envio", "se entrega una sola vez")
check(e.consultar_envio(env["envio_id"])["estado"] == "entregado", "estado entregado")

e.resultado(canal["canal_id"], env["envio_id"], True, "Cargado en 192.168.3.12")
c = e.consultar_envio(env["envio_id"])
check(c["estado"] == "cargado" and "192.168.3.12" in c["mensaje"], "los lentes ven que se cargo")
env2 = e.enviar("banda", BANDA, sin_errores)
e.recoger(canal["canal_id"])
e.resultado(canal["canal_id"], env2["envio_id"], False, "No se pudo conectar")
check(e.consultar_envio(env2["envio_id"])["estado"] == "error", "y tambien si fallo")
try:
    e.resultado("otro", env2["envio_id"], True)
    check(False, "resultado de otro canal")
except el.ErrorEnlace as x:
    check(x.codigo == 404, "no se acepta el resultado desde otro canal")
print(f"   OK ({OK})")

# ---------------------------------------------------------------------------
print("2. Siempre viaja la ULTIMA configuracion")
# ---------------------------------------------------------------------------
a = e.enviar("banda", dict(BANDA, name="Primera"), sin_errores)
b = e.enviar("banda", dict(BANDA, name="Segunda"), sin_errores)
check(e.consultar_envio(a["envio_id"])["estado"] == "sustituido", "la anterior queda sustituida")
got = e.recoger(canal["canal_id"])
check(got["envio"]["nombre"] == "Segunda" and got["envio"]["envio_id"] == b["envio_id"], "la PC recibe la ultima")
print(f"   OK ({OK})")

# ---------------------------------------------------------------------------
print("3. Errores para los lentes")
# ---------------------------------------------------------------------------


def codigo(fn, *a, **k):
    try:
        fn(*a, **k)
    except el.ErrorEnlace as x:
        return x.codigo, x.mensaje
    return None, ""


cod, msg = codigo(e.enviar, "maletin", MALETIN, sin_errores)
check(cod == 409 and "maletin" in msg and "banda" in msg, "nadie espera el maletin (y dice que la PC espera la banda)")
cod, msg = codigo(e.enviar, "banda", BANDA, lambda c, d: ["frecuencia fuera de rango"])
check(cod == 422 and "frecuencia" in msg, "configuracion invalida: no llega a la PC")
check(e.recoger(canal["canal_id"])["status"] == "sin_envio", "nada pendiente tras el rechazo")
check(codigo(e.enviar, "tostadora", BANDA, sin_errores)[0] == 400, "equipo desconocido")
check(codigo(e.enviar, "banda", {}, sin_errores)[0] == 400, "sin configuracion")
grande = dict(BANDA, relleno="x" * (el.MAX_BYTES_LOGIC + 1))
check(codigo(e.enviar, "banda", grande, sin_errores)[0] == 413, "configuracion demasiado grande")
check(codigo(e.abrir, "banda", "999.1.1.1")[0] == 400, "IP invalida al abrir")
check(codigo(e.abrir, "banda", "192.168.3.12", 70000)[0] == 400, "puerto invalido al abrir")
check(codigo(e.abrir, "")[0] == 400, "abrir sin equipo")
print(f"   OK ({OK})")

# ---------------------------------------------------------------------------
print("4. Reemplazo, cierre y PC desconectada")
# ---------------------------------------------------------------------------
pend = e.enviar("banda", BANDA, sin_errores)
nuevo = e.abrir("banda", "192.168.3.20")
check(e.recoger(canal["canal_id"]) == {"status": "cerrado", "motivo": "reemplazado"}, "el canal viejo queda cerrado")
check(e.consultar_envio(pend["envio_id"])["estado"] == "error", "su envio pendiente se marca como no entregado")
check(e.recoger(nuevo["canal_id"])["status"] == "sin_envio", "el canal nuevo no hereda envios")
check(e.estado("banda")["canales"][0]["plc"]["ip"] == "192.168.3.20", "los lentes ven el PLC nuevo")

r.t += el.LATIDO_MAX_S + 1
check(not e.estado("banda")["canales"][0]["pc_conectada"], "sin latido la PC aparece desconectada")
check(codigo(e.enviar, "banda", BANDA, sin_errores)[0] == 409, "y no se le puede enviar")
e.recoger(nuevo["canal_id"])
check(e.enviar("banda", BANDA, sin_errores)["status"] == "ok", "al volver a preguntar se reconecta")

e.cerrar(nuevo["canal_id"])
check(e.estado()["canales"] == [], "cerrar quita la espera")
check(e.recoger(nuevo["canal_id"])["status"] == "cerrado", "recoger tras cerrar")
check(e.recoger("no-existe") == {"status": "cerrado", "motivo": "no_existe"}, "canal desconocido")
r.t += el.OLVIDAR_CERRADO_S + 1
e.estado()
check(nuevo["canal_id"] not in e._canales, "los canales cerrados se olvidan")

m = e.abrir("maletin")
check(m["plc"]["ip"] == "" and m["plc"]["port"] == 502, "maletin sin IP: el puente usa la configurada")
check(e.estado()["canales"][0]["plc_texto"] == "PLC del maletin", "texto sin IP")
for _ in range(el.MAX_ENVIOS_GUARDADOS + 5):
    e.enviar("maletin", MALETIN, sin_errores)
check(len(e._envios) <= el.MAX_ENVIOS_GUARDADOS, "se guardan solo los ultimos envios")
print(f"   OK ({OK})")

# ---------------------------------------------------------------------------
print("5. Validacion real (_errores_enlace = mismas reglas que /aplicar-plc)")
# ---------------------------------------------------------------------------
check(app._errores_enlace(BANDA, "banda") == [], "banda v7 valida")
check(app._errores_enlace(MALETIN, "maletin") == [], "maletin valido")
err = app._errores_enlace(BANDA, "maletin")
check(len(err) == 1 and "para la banda" in err[0], "un programa de banda no va a la PC del maletin")
check("para el maletin" in app._errores_enlace(MALETIN, "banda")[0], "ni al reves")
malo = {"device": "banda", "name": "x",
        "band": {"enable": True, "direction": "derecha", "freq_hz": 999,
                 "engine": {"version": 7, "motor": {"arranque": "I1", "paro": "I2"}}}}
check(app._errores_enlace(malo, "banda") != [], "frecuencia imposible: rechazada")
alt = {**BANDA, "requiere_confirmacion": True, "confirmado": False}
check(app._errores_enlace(alt, "banda") == [], "ya no hay alternativas por confirmar: pasa")
print(f"   OK ({OK})")

# ---------------------------------------------------------------------------
print("6. Endpoints de punta a punta")
# ---------------------------------------------------------------------------
app.ENLACE = el.Enlace()
cli = TestClient(app.app)
res = cli.post("/enlace/abrir", json={"device": "banda", "ip": "192.168.3.12", "port": 502})
check(res.status_code == 200, f"abrir: {res.text}")
cid = res.json()["canal"]["canal_id"]
check(cli.get("/enlace/esperar", params={"canal_id": cid, "timeout_s": 0}).json()["status"] == "sin_envio",
      "esperar sin envio")
check(cli.get("/enlace/estado", params={"device": "banda"}).json()["canales"][0]["canal_id"] == cid,
      "estado para los lentes")

res = cli.post("/enlace/enviar", json={"device": "banda", "logic": malo})
check(res.status_code == 422, "enviar invalido -> 422")
res = cli.post("/enlace/enviar", json={"device": "maletin", "logic": MALETIN})
check(res.status_code == 409, "maletin sin PC -> 409")
res = cli.post("/enlace/enviar", json={"device": "banda", "logic": BANDA, "frases": ["Avanza con I1"]})
check(res.status_code == 200, f"enviar: {res.text}")
eid = res.json()["envio_id"]

got = cli.get("/enlace/esperar", params={"canal_id": cid, "timeout_s": 5}).json()
check(got["status"] == "envio" and got["envio"]["envio_id"] == eid, "la PC recibe el envio")
check(got["envio"]["logic"]["band"]["freq_hz"] == 30, "con la configuracion completa")
res = cli.post("/enlace/resultado", json={"canal_id": cid, "envio_id": eid, "ok": True, "mensaje": "Cargado"})
check(res.status_code == 200, "la PC informa el resultado")
check(cli.get(f"/enlace/envio/{eid}").json()["estado"] == "cargado", "los lentes lo ven cargado")
check(cli.get("/enlace/envio/nada").status_code == 404, "envio desconocido -> 404")
check(cli.post("/enlace/cerrar", json={"canal_id": cid}).json()["status"] == "cerrado", "cerrar")
check(cli.get("/enlace/esperar", params={"canal_id": cid, "timeout_s": 0}).json()["status"] == "cerrado",
      "esperar tras cerrar")

# Con contrasena, el enlace queda protegido como el resto del backend.
app.APP_PASSWORD = "clave-prueba"
try:
    check(cli.get("/enlace/estado").status_code == 401, "sin token -> 401")
    tok = cli.post("/login", json={"password": "clave-prueba"}).json()["token"]
    check(cli.get("/enlace/estado", headers={"Authorization": "Bearer " + tok}).status_code == 200,
          "con token -> 200")
finally:
    app.APP_PASSWORD = ""
print(f"   OK ({OK})")

print(f"\nTODAS LAS PRUEBAS DEL ENLACE LENTES -> PC PASARON ({OK} comprobaciones).")
