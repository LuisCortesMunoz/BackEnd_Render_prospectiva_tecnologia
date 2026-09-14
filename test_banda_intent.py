# test_banda_intent.py — prueba OFFLINE del flujo LLM -> intencion -> normalizador
# -> cobertura -> validacion -> plan Modbus de la BANDA.
#
# Verifica que:
#  - Las secuencias combinadas conservan TODAS sus relaciones (sensor -> plumas,
#    luces, pausa) y no intercambian pluma 1 / pluma 2.
#  - Una accion que desaparece al normalizar bloquea la escritura.
#  - Cada configuracion dispara ResetCmd y NewCfgFlag (NewCfgFlag al final) y
#    espera CfgReady; sin movimiento deja DirCmd = FreqRequest = 0.
#  - START solo con movimiento y solo con I1 (unico boton del ST vigente).
#  - /generar-logica reintenta con el LLM cuando su intencion omite acciones.
#
# Uso:  python test_banda_intent.py   (no necesita red, PLC ni GROQ_API_KEY real)

import copy
import os
import asyncio

os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import app
import banda_intent as bi
import plc_banda as pb


def intencion(mover=False, direccion=None, hz=None, eventos=(), luces=None, manual=None,
              paros=None, auto=None, boton=None, no_soportado=()):
    return {
        "movimiento": {"mover": mover, "direccion": direccion, "frecuencia_hz": hz,
                       "boton_inicio": boton, "paro_automatico": auto},
        "paros": paros or {"i2": False, "software": False},
        "eventos": list(eventos),
        "luces": luces or {"corriendo": [], "detenida": [], "mientras_i1": []},
        "plumas_manual": manual or {"pluma1": None, "pluma2": None},
        "no_soportado": list(no_soportado),
    }


def evento(sensor, banda="no_afecta", duracion=None, luces=(), p1=None, p2=None, conteo=None):
    return {"sensor": sensor, "conteo": conteo, "banda": banda, "duracion_s": duracion,
            "luces": list(luces), "pluma1": p1, "pluma2": p2}


def cfg_de(intent):
    band, errores = bi.normalizar_intencion(intent)
    return {"device": "banda", "intent": intent, "band": band, "outputs": []}, errores


# ── 1. S1 sube las dos plumas / S2 baja las dos (sin movimiento) ──────────
texto1 = ("Cuando el sensor 1 detecte, sube las dos plumas y cuando el sensor 2 "
          "detecte, baja las dos plumas.")
i1 = intencion(eventos=[evento(1, p1="subir", p2="subir"), evento(2, p1="bajar", p2="bajar")])
cfg1, err = cfg_de(i1)
b = cfg1["band"]
assert not err and not bi.errores_cobertura(cfg1) and not pb.validar_config(cfg1), err
assert (b["s1_pluma1"], b["s1_pluma2"], b["s2_pluma1"], b["s2_pluma2"]) == (1, 1, 2, 2)
assert b["s1_band_mode"] == b["s2_band_mode"] == 1 and b["s1_action"] == b["s2_action"] == 0
assert b["enable"] is False and not pb.requiere_start(cfg1)
assert bi.revisar_contra_texto(texto1, i1) == []
print("1. S1 -> P1/P2 subir, S2 -> P1/P2 bajar: 6 relaciones conservadas, sin START (OK)")

# ── 2. Secuencia compleja con movimiento ─────────────────────────────────
texto2 = ("Avanza la banda a la derecha a 30 Hz, cuando S1 detecte detente 5 segundos, "
          "prende la roja y sube las dos plumas; despues continua y cuando S2 detecte "
          "baja las dos plumas.")
i2 = intencion(mover=True, direccion="derecha", hz=30, eventos=[
    evento(1, "pausa_temporizada", 5, ["roja"], "subir", "subir"),
    evento(2, p1="bajar", p2="bajar")])
cfg2, err = cfg_de(i2)
b = cfg2["band"]
assert not err and not bi.errores_cobertura(cfg2) and not pb.validar_config(cfg2), err
assert (b["s1_action"], b["wait_s1_s"], b["torreta_s1"], b["s1_band_mode"]) == (4, 5, 4, 0)
assert (b["s2_action"], b["s2_band_mode"], b["s2_pluma1"], b["s2_pluma2"]) == (0, 1, 2, 2)
assert b["enable"] and b["freq_hz"] == 30 and pb.requiere_start(cfg2) and pb.boton_start(cfg2) == "I1"
assert bi.revisar_contra_texto(texto2, i2) == []
print("2. Movimiento + S1 (pausa 5 s, roja, dos plumas) + S2 (baja dos plumas) (OK)")

# ── 3. Una accion perdida al normalizar BLOQUEA la escritura ─────────────
roto = copy.deepcopy(cfg2)
roto["band"]["s2_pluma2"] = None
errores = bi.errores_cobertura(roto)
assert errores and "S2: pluma 2 bajar" in errores[0], errores
cambiado = copy.deepcopy(cfg1)
cambiado["band"]["s1_pluma1"], cambiado["band"]["s1_pluma2"] = 2, 1
assert len(bi.errores_cobertura(cambiado)) == 2      # falta lo pedido y sobra lo inventado
print("3. Cobertura: accion perdida o intercambiada -> error antes de escribir (OK)")

# ── 4. La revision contra el texto detecta una lectura incompleta del LLM ─
incompleta = intencion(eventos=[evento(1, p1="subir", p2="subir"), evento(2, p1="bajar")])
avisos = bi.revisar_contra_texto(texto1, incompleta)
assert any("dos plumas" in a for a in avisos), avisos
assert bi.revisar_contra_texto(texto1, intencion(eventos=[evento(1, p1="subir", p2="subir")]))
print("4. Texto vs intencion: falta S2 o una pluma -> se pide revision al LLM (OK)")

# ── 5. Limites del ST: START distinto de I1, conflictos, frecuencia ───────
_, err = cfg_de(intencion(mover=True, hz=20, boton="I2"))
assert err and "I1" in err[0], err
_, err = cfg_de(intencion(eventos=[evento(1, p1="subir"), evento(1, p1="bajar")]))
assert err and "pluma 1" in err[0], err
_, err = cfg_de(intencion(auto={"segundos": 10, "cuenta": "movimiento"}))
assert err and "se mueva" in err[0], err
assert bi.pregunta_pendiente(intencion(mover=True))["slot"] == "frecuencia"
_, err = cfg_de(intencion(no_soportado=["encender Q10"]))
assert err, err
print("5. START con I2, pluma con dos comandos, paro automatico sin marcha y dato faltante (OK)")

# ── 6. Plan: reset + NewCfgFlag SIEMPRE, NewCfgFlag al final ─────────────
class _R:
    registers = [1]
    def isError(self):
        return False


class FakeClient:
    def __init__(self):
        self.escrituras = []
        self.mem = {}

    def write_register(self, addr, val, *, device_id=1):
        self.escrituras.append((addr - 2999, val))
        self.mem[addr] = val
        return _R()

    def read_holding_registers(self, addr, *, count=1, device_id=1):
        r = _R()
        r.registers = [0 if addr == pb.ADDR_CFG_READY_REG and len(self.escrituras) < 3
                       else self.mem.get(addr, 1 if addr == pb.ADDR_CFG_READY_REG else 0)
                       for _ in range(count)]
        return r


def ejecutar(cfg):
    plc = pb.BandaPLC.__new__(pb.BandaPLC)
    plc.client, plc.unit, plc.ip, plc.port = FakeClient(), 1, "fake", 502
    pb.aplicar_config(plc, cfg)
    return [r for r, _ in plc.client.escrituras], plc.client.escrituras


regs, escr = ejecutar(cfg1)
assert (2, 0) in escr and (4, 0) in escr, escr           # sin movimiento: R2 = R4 = 0
assert regs[-2:] == [6, 5], regs                          # ResetCmd y luego NewCfgFlag
assert all(r not in regs for r in (500, 502, 504, 506)) and not any(100 <= r <= 127 for r in regs)
regs, escr = ejecutar(cfg2)
assert regs[-2:] == [6, 5] and (4, 30) in escr and (2, 1) in escr, escr
assert (16, 0) in escr and (17, 1) in escr and (38, 2) in escr and (39, 2) in escr, escr
print("6. Plan Modbus: R6 -> R5 al final, R2/R4 = 0 sin marcha, sin registros de feedback (OK)")

# ── 7. /generar-logica: el LLM omite una pluma, se le devuelve y corrige ──
respuestas = [
    {"name": "incompleta", "intencion": incompleta},
    {"name": "S1 sube plumas, S2 las baja", "intencion": i1},
]
llamadas = []


def llm_falso(messages):
    llamadas.append(messages)
    return copy.deepcopy(respuestas[len(llamadas) - 1])


app.llamar_modelo_json = llm_falso
resp = asyncio.run(app.generar_logica(app.LogicaRequest(texto=texto1, device="banda")))
assert resp.status == "ok" and len(llamadas) == 2, (resp.status, len(llamadas))
assert "dos plumas" in llamadas[1][-1]["content"]
band = resp.logic["band"]
assert (band["s1_pluma1"], band["s1_pluma2"], band["s2_pluma1"], band["s2_pluma2"]) == (1, 1, 2, 2)
assert resp.logic["intent"]["eventos"] and not bi.errores_cobertura(resp.logic)
print("7. /generar-logica: auto-revision recupera la pluma omitida por el LLM (OK)")

# ── 8. /generar-logica pregunta la frecuencia en vez de inventarla ────────
respuestas[:] = [{"name": "sin hz", "intencion": intencion(mover=True, direccion="derecha")}]
llamadas.clear()
resp = asyncio.run(app.generar_logica(app.LogicaRequest(texto="avanza la banda a la derecha", device="banda")))
assert resp.status == "needs_clarification" and resp.questions[0]["slot"] == "frecuencia"
print("8. Movimiento sin frecuencia -> needs_clarification (OK)")

# ── 9. /aplicar-plc rechaza una configuracion que perdio acciones ─────────
from fastapi.testclient import TestClient
cliente = TestClient(app.app)
r = cliente.post("/aplicar-plc", json={"logic": roto, "device": "banda", "dry_run": True})
assert r.status_code == 422 and "perdio acciones" in r.json()["detail"], r.json()
r = cliente.post("/aplicar-plc", json={"logic": cfg2, "device": "banda", "dry_run": True})
assert r.status_code == 200 and r.json()["requiere_start"] and r.json()["boton_start"] == "I1"
assert r.json()["plan"][-3:] == ["banda.trigger_vfd_reset()", "banda.trigger_new_config()",
                                 "banda.esperar_config_lista(esperar_caida=True)"], r.json()["plan"]
r = cliente.post("/aplicar-plc", json={"logic": cfg1, "device": "banda", "dry_run": True})
assert r.status_code == 200 and not r.json()["requiere_start"]
print("9. /aplicar-plc: cobertura final + requiere_start/boton_start (OK)")

# ── 10. Estado: configuracion sin movimiento no pide I1 ───────────────────
reg = {n: 0 for s, c in pb.LECTURA_BLOQUES for n in range(s, s + c)}
reg.update({7: 1, 120: 1, 121: 1, 18: 1, 28: 1})
e = pb.estado_desde_registros(reg)
assert e["fase"] == "sin_marcha" and e["sensores"]["s1"]["event_active"]
reg.update({2: 1, 4: 30})
assert pb.estado_desde_registros(reg)["fase"] == "lista"
print("10. Fase: sin movimiento = 'sin_marcha' (sin I1); con movimiento = 'lista' (OK)")

print("\nTODAS LAS PRUEBAS DE LA BANDA PASARON")
