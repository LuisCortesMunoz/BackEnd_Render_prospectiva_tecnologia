# test_banda_motor.py — prueba OFFLINE del motor configurable de la banda (ST v6).
#
# 1) Validador y compilador (banda_motor.py).
# 2) Simulador scan a scan (banda_motor_sim.py, espejo del ST v6) con las
#    secuencias que pidio el usuario: sensor -> timer -> invertir, AND/OR de
#    botones y sensores, contadores que se rearman, torreta en secuencia,
#    plumas, paro de emergencia.
# 3) plc_banda: plan de carga, orden de escritura Modbus, estado y diagnostico.
# 4) /generar-logica con el LLM SIMULADO (sin Groq).
#
# Uso:  python test_banda_motor.py

import os, json, asyncio, tempfile
os.environ["MEMORIA_JSON"] = os.path.join(tempfile.gettempdir(), "test_mem_banda_motor.json")
if os.path.exists(os.environ["MEMORIA_JSON"]):
    os.remove(os.environ["MEMORIA_JSON"])
os.environ.setdefault("GROQ_API_KEY", "test-key-local")

import banda_motor as bm
import banda_motor_sim as ms
import plc_banda as pb

OK = 0


def check(cond, msg):
    global OK
    assert cond, msg
    OK += 1


def banda(engine, mover=True, freq=30, direccion="derecha"):
    b = {"enable": mover, "engine": engine}
    if mover:
        b.update(direction=direccion, freq_hz=freq)
    return b


# ---------------------------------------------------------------------------
print("1. Validador y compilador")
# ---------------------------------------------------------------------------
check(bm.codigo_senal("S1") == 4 and bm.codigo_senal("NOT S1") == 104
      and bm.codigo_senal("!I2") == 102 and bm.codigo_senal("T3") == 26
      and bm.codigo_senal("ct2") == 33 and bm.codigo_senal("PASO16") == 63
      and bm.codigo_senal(None) == 0 and bm.codigo_senal("SENSOR 1") == 4,
      "codigos de senal")
check(bm.nombre_senal(104) == "NOT S1" and bm.nombre_senal(16) == "C1", "nombres de senal")

def errores(engine, **kw):
    return bm.validar_motor(banda(engine, **kw))[0]

check(any("T1" in e and "no esta definido" in e for e in errores({"motor": {"invertir": "T1"}})),
      "referencia a un timer no definido")
check(any("no existe" in e for e in errores({"salidas": {"verde": "S7"}})), "senal inexistente")
check(any("I3" in e for e in errores({"motor": {"arranque": "I3"}})), "I3 no puede arrancar")
check(any("ciclica sin ninguna espera" in e for e in errores({"secuencia": {
    "inicio": "I1", "modo": "ciclica", "pasos": [{"acciones": ["verde"], "avanza": "tiempo", "segundos": 0}]}})),
    "secuencia ciclica sin espera")
check(any("mueve la banda" in e for e in errores({"secuencia": {
    "inicio": "S1", "pasos": [{"acciones": ["marcha"], "avanza": "tiempo", "segundos": 1}]}}, mover=False)),
    "paso con marcha sin movimiento")
check(any("subir y bajar" in e for e in errores({"secuencia": {
    "inicio": "S1", "pasos": [{"acciones": ["pluma1_sube", "pluma1_baja"], "segundos": 1}]}})),
    "pluma sube y baja en el mismo paso")
check(any("paro_inversion_s" in e for e in errores({"motor": {"arranque": "I1", "paro_inversion_s": 0.5}})),
      "paro de inversion menor a 1 s")
check(any("entre 1 y 4" in e for e in errores({"condiciones": [{"id": "C1", "op": "AND",
      "entradas": ["I1", "I2", "S1", "S2", "SIEMPRE"]}]})), "condicion con 5 entradas")
av = bm.validar_motor(banda({"contadores": [{"id": "CT1", "entrada": "S1", "preset": 2, "modo": "ciclico"}],
                             "salidas": {"roja": "CT1"}}, mover=False))[1]
check(any("pulso de un scan" in a for a in av), "aviso: contador ciclico directo a una luz")
check(any("I1" in a for a in bm.validar_motor(banda({}))[1]), "aviso: arranque por defecto I1")

ec = bm.compilar_motor(banda({"motor": {"arranque": "I1", "invertir": "T1"},
                              "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 3}]}))
check(len(ec) == bm.ENGINE_LEN and ec[0] == 1 and ec[bm.O_RUN] == 1 and ec[bm.O_TOGGLE] == 24
      and ec[bm.O_TIMER] == 4 and ec[bm.O_TIMER + 1] == 4 and ec[bm.O_TIMER + 3] == 30
      and ec[bm.O_REVTIME] == 20, "compilacion de S1 -> 3 s -> invertir")
check(bm.error_registros(ec) == 0, "EC compilado pasa la validacion del ST (§3b)")
malo = list(ec); malo[bm.O_TIMER] = 9
check(bm.error_registros(malo) == bm.O_TIMER + 1 and bm.campo_de_offset(bm.O_TIMER) == "T1.tipo",
      "§3b reporta el primer campo invalido")
malo = list(ec); malo[bm.O_OUT] = 70
check(bm.error_registros(malo) == bm.O_OUT + 1, "§3b rechaza codigos de senal fuera de rango")
check(bm.error_registros(bm.registros_apagado()) == 0, "EngineMode = 0 siempre es valido")
d = bm.describir_motor(banda({"motor": {"arranque": "I1", "invertir": "T1"},
                              "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 3}]}))
check(any("invierte con T1" in x for x in d) and any("3 s despues" in x for x in d), "descripcion legible")
print(f"   OK ({OK})")


# ---------------------------------------------------------------------------
print("2. Simulador (espejo del ST v6) con las secuencias pedidas")
# ---------------------------------------------------------------------------
import banda_st_interprete as si
SIMULAR = ms.simular


def sim(engine, **kw):
    return SIMULAR(banda(engine, **{k: v for k, v in kw.items() if k in ("mover", "freq", "direccion")}),
                   **{k: v for k, v in kw.items() if k in ("stop_mode",)})


def escenarios():

    # 2.1 "Cuando S1 detecte una caja, espera 3 s y cambia el sentido" — en CADA caja.
    s = sim({"motor": {"arranque": "I1", "invertir": "T1"},
             "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 3}]})
    check(s.CfgReady and s.motor() == "paro", "lista y detenida tras cargar")
    s.pulsar("I1"); s.correr(1)
    check(s.motor() == "dir1", "I1 arranca hacia dir 1")
    s.pulsar("S1", 0.5); s.correr(2.2)
    check(s.motor() == "dir1", "durante el retardo la banda SIGUE avanzando (no se pausa)")
    s.correr(0.6)
    check(s.motor() == "paro" and s.MotorState == 2, "a los 3 s frena antes de invertir")
    s.correr(1.0)
    check(s.motor() == "paro", "no invierte con el motor girando (espera velocidad cero y >= 2 s)")
    s.correr(2.5)
    check(s.motor() == "dir2" and s.DirActual == 2, "invierte a dir 2")
    s.pulsar("S1", 0.5); s.correr(7)
    check(s.motor() == "dir1", "la SEGUNDA caja vuelve a invertir (no se agota como en v5)")

    # 2.2 "S2 detecta -> detener -> esperar 2 s -> arrancar en sentido contrario"
    s = sim({"motor": {"arranque": "I1"},
             "secuencia": {"inicio": "S2", "modo": "una_vez", "exclusiva": False, "pasos": [
                 {"acciones": ["paro"], "avanza": "tiempo", "segundos": 2},
                 {"acciones": ["invertir"], "avanza": "tiempo", "segundos": 0}]}})
    s.pulsar("I1"); s.correr(2)
    s.pulsar("S2", 0.3); s.correr(1)
    check(s.motor() == "paro", "S2 detiene la banda")
    s.correr(1.5)
    check(s.motor() == "dir2", "a los 2 s arranca en sentido contrario")
    s.correr(2); s.pulsar("S2", 0.3); s.correr(4)
    check(s.motor() == "dir1", "otra caja en S2 lo repite")

    # 2.3 "Boton -> banda adelante -> sensor -> timer -> banda atras"
    s = sim({"motor": {"arranque": "I1", "dir_2": "T1"},
             "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 1.5}]})
    s.pulsar("I1"); s.correr(1)
    check(s.motor() == "dir1", "adelante")
    s.pulsar("S1", 0.2); s.correr(6)
    check(s.motor() == "dir2", "tras S1 + 1.5 s, atras")

    # 2.4 "Boton -> esperar tiempo -> cambiar direccion" (I2 dispara)
    s = sim({"motor": {"arranque": "I1", "invertir": "T1"},
             "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "I2", "segundos": 2}]})
    s.pulsar("I1"); s.correr(1); s.pulsar("I2", 0.2); s.correr(1)
    check(s.motor() == "dir1", "I2 -> aun no invierte")
    s.correr(5)
    check(s.motor() == "dir2", "I2 -> 2 s -> invierte")

    # 2.5 "Sensor + boton -> esperar tiempo -> detener motor"
    s = sim({"motor": {"arranque": "I1", "paro": "T1"},
             "condiciones": [{"id": "C1", "op": "AND", "entradas": ["S1", "I2"], "flanco": "subida"}],
             "timers": [{"id": "T1", "tipo": "PULSO", "entrada": "C1", "segundos": 1}]})
    s.pulsar("I1"); s.correr(1)
    s.poner(S1=True); s.correr(0.5)
    check(s.motor() == "dir1", "S1 solo no detiene")
    s.pulsar("I2", 0.2); s.correr(0.3)
    check(s.motor() == "paro", "S1 Y I2 -> detiene")
    s.poner(S1=False); s.correr(2)
    check(s.motor() == "paro" and not s.RunLatch, "se queda detenida (paro enclavado)")
    s.pulsar("I1"); s.correr(1)
    check(s.motor() == "dir1", "I1 la vuelve a arrancar")

    # 2.6 "Cuando presione el boton verde y S2 este activo, enciende la luz amarilla"
    s = sim({"condiciones": [{"id": "C1", "op": "AND", "entradas": ["I1", "S2"]}],
             "salidas": {"amarilla": "C1"}}, mover=False)
    s.poner(I1=True); s.correr(0.3)
    check(s.luces() == set(), "solo I1: apagada")
    s.poner(S2=True); s.correr(0.3)
    check(s.luces() == {"amarilla"}, "I1 y S2: amarilla")
    s.poner(I1=False); s.correr(0.3)
    check(s.luces() == set(), "suelta I1: apagada")

    # 2.7 "Al presionar un boton arranca; cuando S1 detecte una caja, detenla"
    s = sim({"motor": {"arranque": "I1", "paro": "S1"}})
    s.pulsar("I1"); s.correr(1)
    s.pulsar("S1", 0.3); s.correr(1)
    check(s.motor() == "paro", "S1 la detiene")
    s.correr(2)
    check(s.motor() == "paro", "y NO reanuda sola al irse la caja (v5 si reanudaba)")

    # 2.8 "Cuando S1 detecte tres cajas, enciende la luz roja"
    s = sim({"contadores": [{"id": "CT1", "entrada": "S1", "reset": "I2", "preset": 3}],
             "salidas": {"roja": "CT1"}}, mover=False)
    for _ in range(2):
        s.pulsar("S1", 0.3); s.correr(0.3)
    check("roja" not in s.luces(), "2 cajas: apagada")
    s.pulsar("S1", 0.3); s.correr(0.3)
    check("roja" in s.luces(), "3 cajas: roja")
    s.pulsar("I2", 0.3); s.correr(0.3)
    check("roja" not in s.luces(), "I2 resetea el contador")

    # 2.9 "Cuando se presione el boton rojo o S2 detecte una caja, deten el motor"
    s = sim({"motor": {"arranque": "I1", "paro": "C1"},
             "condiciones": [{"id": "C1", "op": "OR", "entradas": ["I2", "S2"]}]})
    s.pulsar("I1"); s.correr(1); s.pulsar("I2", 0.3); s.correr(0.5)
    check(s.motor() == "paro", "I2 detiene")
    s.pulsar("I1"); s.correr(1); s.pulsar("S2", 0.3); s.correr(0.5)
    check(s.motor() == "paro", "S2 detiene")

    # 2.10 Torreta: "S1 -> amarilla -> 3 s -> verde -> 5 s -> apagar"
    s = sim({"secuencia": {"inicio": "S1", "modo": "una_vez", "exclusiva": True, "pasos": [
        {"acciones": ["amarilla"], "avanza": "tiempo", "segundos": 3},
        {"acciones": ["verde"], "avanza": "tiempo", "segundos": 5}]}}, mover=False)
    s.pulsar("S1", 0.3); s.correr(0.2)
    check(s.luces() == {"amarilla"}, "amarilla")
    s.correr(3)
    check(s.luces() == {"verde"}, "a los 3 s, verde")
    s.correr(5.2)
    check(s.luces() == set(), "a los 5 s, apagada")

    # 2.11 Secuencia de 7 pasos: adelante -> S1 -> 2 s -> amarilla + invertir -> S2 -> detener + verde
    s = sim({"secuencia": {"inicio": "I1", "modo": "una_vez", "exclusiva": True, "pasos": [
        {"acciones": ["marcha", "dir_1"], "avanza": "flanco", "senal": "S1"},
        {"acciones": ["marcha"], "avanza": "tiempo", "segundos": 2},
        {"acciones": ["marcha", "amarilla", "invertir"], "avanza": "flanco", "senal": "S2"},
        {"acciones": ["paro", "verde"], "avanza": "flanco", "senal": "I1"}]}})
    s.pulsar("I1"); s.correr(1)
    check(s.motor() == "dir1" and s.SeqStep == 1, "paso 1: adelante esperando S1")
    s.pulsar("S1", 0.3); s.correr(1)
    check(s.motor() == "dir1" and s.SeqStep == 2, "paso 2: sigue 2 s")
    s.correr(1.5)
    check(s.SeqStep == 3 and "amarilla" in s.luces(), "paso 3: amarilla")
    s.correr(4.5)
    check(s.motor() == "dir2", "paso 3: invirtio")
    s.pulsar("S2", 0.3); s.correr(0.5)
    check(s.motor() == "paro" and s.luces() == {"verde"}, "paso 4: detenida y verde")
    s.pulsar("I1"); s.correr(0.5)
    check(not s.SeqActive and s.luces() == set(), "I1 termina la secuencia")

    # 2.12 Un sensor que YA estaba activo no salta el paso (flanco desde la entrada al paso)
    s = sim({"secuencia": {"inicio": "S1", "pasos": [
        {"acciones": ["amarilla"], "avanza": "flanco", "senal": "S1"},
        {"acciones": ["roja"], "avanza": "tiempo", "segundos": 1}]}}, mover=False)
    s.poner(S1=True); s.correr(1)
    check(s.SeqStep == 1 and s.luces() == {"amarilla"}, "S1 sostenido no avanza el paso")
    s.poner(S1=False); s.correr(0.3); s.poner(S1=True); s.correr(0.3)
    check(s.SeqStep == 2 and s.luces() == {"roja"}, "una NUEVA deteccion si avanza")

    # 2.13 "S1 -> contador -> boton rojo -> reset -> arrancar banda"; cada 2 piezas invierte
    s = sim({"motor": {"arranque": "I2", "invertir": "CT2"},
             "contadores": [{"id": "CT1", "entrada": "S1", "reset": "I2", "preset": 3},
                            {"id": "CT2", "entrada": "S2", "preset": 2, "modo": "ciclico"}],
             "salidas": {"roja": "CT1"}})
    for _ in range(3):
        s.pulsar("S1", 0.2); s.correr(0.2)
    check("roja" in s.luces() and s.motor() == "paro", "3 piezas: roja, banda detenida")
    s.pulsar("I2"); s.correr(1)
    check("roja" not in s.luces() and s.motor() == "dir1", "I2 resetea y arranca")
    s.pulsar("S2", 0.2); s.correr(0.3)
    check(s.DirTarget == 1, "1 pieza en S2: sin invertir")
    s.pulsar("S2", 0.2); s.correr(5)
    check(s.motor() == "dir2", "2 piezas en S2: invierte (contador ciclico)")
    s.pulsar("S2", 0.2); s.correr(0.3); s.pulsar("S2", 0.2); s.correr(5)
    check(s.motor() == "dir1", "otras 2 piezas: vuelve a invertir")

    # 2.14 Memoria: "mantener la roja hasta que S2 detecte"
    s = sim({"memorias": [{"id": "M1", "set": "S1", "reset": "S2"}], "salidas": {"roja": "M1"}}, mover=False)
    s.pulsar("S1", 0.2); s.correr(2)
    check(s.luces() == {"roja"}, "S1 enciende y la roja se mantiene")
    s.pulsar("S2", 0.2); s.correr(0.2)
    check(s.luces() == set(), "S2 la apaga")

    # 2.15 Pluma: "S1 -> sube pluma 1 por 2 s" + tiempo maximo
    s = sim({"timers": [{"id": "T1", "tipo": "PULSO", "entrada": "S1", "segundos": 2}],
             "salidas": {"pluma1_sube": "T1", "pluma2_baja": "S2"},
             "opciones": {"pluma_max_s": 3}}, mover=False)
    s.pulsar("S1", 0.2); s.correr(0.5)
    check(s.pluma(1) == "sube", "pluma 1 sube")
    s.correr(2)
    check(s.pluma(1) == "stop", "a los 2 s se detiene")
    s.poner(S2=True); s.correr(2)
    check(s.pluma(2) == "baja", "pluma 2 baja mientras S2")
    s.correr(1.5)
    check(s.pluma(2) == "stop" and s.PlumaFault & 8, "pluma 2 cortada por tiempo maximo")

    # 2.16 Paro de emergencia I3: todo detenido, luces de paro, sin rearranque solo
    s = sim({"motor": {"arranque": "I1"}, "salidas": {"verde": "BANDA_CORRIENDO", "pluma1_sube": "SIEMPRE"},
             "opciones": {"luces_paro": ["roja"]}})
    s.pulsar("I1"); s.correr(1)
    check(s.motor() == "dir1" and s.luces() == {"verde"} and s.pluma(1) == "sube", "corriendo")
    s.poner(I3=True); s.correr(0.3)
    check(s.motor() == "paro" and s.luces() == {"roja"} and s.pluma(1) == "stop", "I3: todo parado, roja")
    s.poner(I3=False); s.correr(1)
    check(s.motor() == "paro", "al soltar I3 no arranca sola")

    # 2.17 Una secuencia se aborta con cualquier paro y no rearranca el motor sola
    s = sim({"secuencia": {"inicio": "I1", "pasos": [{"acciones": ["marcha"], "avanza": "flanco", "senal": "S1"}]}},
            stop_mode=1)
    s.pulsar("I1"); s.correr(1)
    check(s.motor() == "dir1", "secuencia corriendo")
    s.pulsar("I2", 0.3); s.correr(1)
    check(s.motor() == "paro" and not s.SeqActive, "I2 (StopMode 1) aborta la secuencia")

    # 2.18 Filtro antirrebote: un rebote de 50 ms no cuenta
    s = sim({"contadores": [{"id": "CT1", "entrada": "S1", "preset": 2}], "salidas": {"roja": "CT1"},
             "opciones": {"filtro_sensores_s": 0.2}}, mover=False)
    s.poner(S1=True); s.correr(0.05); s.poner(S1=False); s.correr(0.3)
    check(s.CAcc[0] == 0, "rebote de 50 ms ignorado")
    s.poner(S1=True); s.correr(0.5)
    check(s.CAcc[0] == 1, "deteccion real contada")


for _nombre, _fn in (("simulador Python", ms.simular), ("ST v6 real (interpretado)", si.simular)):
    SIMULAR = _fn
    _antes = OK
    escenarios()
    print(f"   {_nombre}: {OK - _antes} comprobaciones")
print(f"   OK ({OK})")


# ---------------------------------------------------------------------------
print("3. plc_banda: validacion, plan y escritura Modbus")
# ---------------------------------------------------------------------------
eng_ok = {"motor": {"arranque": "I1", "invertir": "T1"},
          "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 3}]}
cfg = {"device": "banda", "band": banda(eng_ok), "outputs": []}
check(pb.validar_config(cfg) == [], "config del motor valida")
check(any("frecuencia" in e for e in pb.validar_config({"band": {"enable": True, "engine": eng_ok}})),
      "sin frecuencia: error")
check(any("timer" in e for e in pb.validar_config({"band": dict(banda(eng_ok), auto_stop_s=10,
                                                                 auto_stop_mode=1)})),
      "auto_stop no se usa con el motor configurable")
plan = pb.plan_config(cfg)
metodos = [m for m, _, _ in plan]
check(metodos[0] == "verificar_st_v6" and "configurar_motor" in metodos
      and metodos.index("configurar_motor") < metodos.index("trigger_new_config")
      and metodos[-1] == "esperar_config_lista", "orden del plan del motor")
check(metodos.count("apagar_sensor") == 2 and "configurar_torreta" in metodos,
      "los registros v5 se dejan en 0")
v5 = {"device": "banda", "band": {"enable": True, "direction": "derecha", "freq_hz": 30,
                                   "s1_action": "paro_temporizado", "wait_s1_s": 5}, "outputs": []}
check("apagar_motores" in [m for m, _, _ in pb.plan_config(v5)], "un programa v5 apaga el motor del PLC (v6: EngineMode=0, v7: UNLOAD)")
check(pb.boton_start(cfg) == "I1", "boton de arranque del motor")


class FakeResp:
    def __init__(self, registers=None):
        self.registers = registers or []

    def isError(self):
        return False


class FakeClient:
    def __init__(self):
        self.mem = {}
        self.log = []

    def write_register(self, addr, valor, device_id=None):
        self.mem[addr] = valor; self.log.append(("w", addr, valor)); return FakeResp()

    def write_registers(self, addr, valores, device_id=None):
        for i, v in enumerate(valores):
            self.mem[addr + i] = v
        self.log.append(("ws", addr, len(valores))); return FakeResp()

    def read_holding_registers(self, addr, count=1, device_id=None):
        return FakeResp([self.mem.get(addr + i, 0) for i in range(count)])


plc = pb.BandaPLC(ip="127.0.0.1")
plc.client = FakeClient()
try:
    plc.verificar_st_v6()
    check(False, "con ST v5 (%R460 = 0) debe rechazar la carga")
except ValueError as e:
    check("v6" in str(e), "rechaza cargar el motor en un PLC con ST v5")
plc.client.mem[pb.R(bm.STATUS_BASE)] = 6
plc.verificar_st_v6()
ec = bm.compilar_motor(cfg["band"])
plc.configurar_motor(ec)
log = plc.client.log
check(log[0] == ("w", pb.R(200), 0) and log[-1] == ("w", pb.R(200), 1)
      and all(x[0] == "ws" for x in log[1:-1]), "EngineMode=0 -> bloque -> EngineMode=1")
check(all(plc.client.mem[pb.R(200) + i] == v for i, v in enumerate(ec)), "bloque EC escrito completo")
try:
    plc.write_band_block(pb.R(455), [0] * 10)
    check(False, "un bloque fuera de la lista blanca debe fallar")
except ValueError:
    check(True, "lista blanca: no escribe %R460+ (estado del ST)")
plc.boton_app(1, True)
check(plc.client.mem[pb.R(200 + bm.O_APP1)] == 1, "boton APP1")

reg = {200: 1, 460: 6, 461: 0, 462: bm.O_TIMER + 1, 463: 1, 464: 2, 474: 0b0000001, 475: 0b01001}
est = pb.estado_desde_registros(reg)
m = est["motor_configurable"]
check(est["st_version"] == 6 and est["engine_mode"] == 1 and m["motor"] == "en marcha"
      and m["direccion"] == 2 and m["salidas"]["verde"] and m["entradas"]["S1"],
      "estado del motor configurable para el panel")
check(any("T1.tipo" in x for x in pb.motivos_config_invalida(reg)), "diagnostico: campo invalido del motor")
check(pb.estado_desde_registros({})["st_version"] == 5, "PLC con ST v5: st_version 5")
print(f"   OK ({OK})")


# ---------------------------------------------------------------------------
print("4. /generar-logica con el LLM simulado (BANDA_MOTOR_CONFIGURABLE)")
# ---------------------------------------------------------------------------
import app
app.HISTORIAL_PATH = os.path.join(tempfile.gettempdir(), "test_hist_banda_motor.json")
app.BANDA_MOTOR_CONFIGURABLE = True
respuestas = []
app.llamar_modelo_json = lambda msgs: respuestas.pop(0)


def generar(texto, contexto=None):
    req = app.LogicaRequest(texto=texto, device="banda", contexto=contexto)
    return asyncio.run(app.generar_logica(req))


respuestas[:] = [{"name": "S1 3 s invierte",
                  "banda": {"mover": True, "direccion": "derecha", "frecuencia_hz": 30},
                  "motor": {"arranque": "I1", "invertir": "T1"},
                  "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 3}],
                  "no_soportado": []}]
r = generar("Arranca con I1 a 30 Hz; cuando S1 detecte espera 3 s y cambia el sentido")
check(r.status == "ok" and r.device == "banda" and bm.es_motor(r.logic)
      and r.program["metadata"]["engine_config"]["band"]["engine"]["timers"][0]["id"] == "T1",
      "genera el motor configurable")
check(r.analysis.get("motor_configurable") and any("invierte" in x for x in r.analysis["acciones"]),
      "resumen legible en analysis")

respuestas[:] = [
    {"name": "x", "banda": {"mover": True, "frecuencia_hz": 30}, "motor": {"invertir": "T1"}},
    {"name": "x", "banda": {"mover": True, "frecuencia_hz": 30}, "motor": {"arranque": "I1", "invertir": "T1"},
     "timers": [{"id": "T1", "tipo": "RETARDO", "entrada": "S1", "segundos": 3}]}]
r = generar("cuando S1 detecte espera 3 s e invierte")
check(r.status == "ok" and not respuestas, "auto-revision: corrige un timer no definido")

respuestas[:] = [{"name": "x", "banda": {"mover": True, "frecuencia_hz": None},
                  "motor": {"arranque": "I1"}}]
r = generar("arranca la banda con I1")
check(r.status == "needs_clarification" and r.questions[0]["campo"] == "freq_hz",
      "sin frecuencia: pregunta en vez de inventar")

respuestas[:] = [{"name": "x", "banda": {"mover": False}, "salidas": {"roja": "S1"},
                  "no_soportado": ["un tercer sensor"]}]
r = generar("S1 prende la roja y un tercer sensor la apaga")
check(r.status == "ok" and any("tercer sensor" in w for w in r.warnings), "no_soportado como aviso")

ctx = app.ContextoLadder(programa_anterior=r.program, historial=["S1 prende la roja"])
respuestas[:] = [{"name": "x", "banda": {"mover": False}, "salidas": {"roja": "S1", "verde": "S2"}}]
capturado = {}
def _llm(msgs):
    capturado["msgs"] = msgs
    return respuestas.pop(0)
app.llamar_modelo_json = _llm
r = generar("y que S2 prenda la verde", contexto=ctx)
check(r.status == "ok" and any("PROGRAMA ACTUAL" in m["content"] for m in capturado["msgs"]),
      "modificaciones: el programa anterior viaja al LLM")

app.BANDA_MOTOR_CONFIGURABLE = False
print(f"   OK ({OK})")


# ---------------------------------------------------------------------------
print("5. Regresion: ST v6 con EngineMode = 0 == ST v5 (scan a scan)")
# ---------------------------------------------------------------------------
V5 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cscape", "ST_banda_v5.txt")
SALIDAS_FIS = ("PHQ3", "PHQ4", "PHQ5", "PHQ6", "PHQ7", "PHQ8", "PHQ9", "VFD_CONTROL",
               "VFD_FREQCALC", "VFD_RESETREG", "BANDSTATUS", "STOPREASON", "CFGREADY_REG",
               "BANDENABLE_REG", "S1_COUNTACCUM", "S2_COUNTACCUM", "DIRCMD")


def programa_v5(regs, ruta):
    sim = si.SimST([0] * bm.ENGINE_LEN, freq_hz=regs.get("FREQREQUEST", 30),
                   dir_cmd=regs.get("DIRCMD", 1), stop_mode=regs.get("STOPMODE", 0), ruta=ruta)
    for k, v in regs.items():
        sim.V[k] = v
        sim.V["R_" + k] = v
    return sim


CASOS_V5 = [
    {"S1_ENABLE": 1, "S1_ACTION": 2, "S1_TIMERPRESET": 3, "S1_TORRETAMASK": 2, "TORRETARUN": 1},
    {"S1_ENABLE": 1, "S1_ACTION": 1, "S2_ENABLE": 1, "S2_ACTION": 0, "S2_PLUMA1CMD": 1,
     "S1_COUNTPRESET": 2, "S1_COUNTACTIONMASK": 8 + 4, "S1_COUNTDIRTARGET": 3, "S1_COUNTLAMPMASK": 4,
     "TORRETAIDLE": 4, "STOPMODE": 1},
    {"DIRCMD": 0, "FREQREQUEST": 0, "S2_ENABLE": 1, "S2_ACTION": 0, "S2_TORRETAMASK": 7,
     "I1_LAMPMASK": 1, "TIMEDLAMPMASK": 2, "TIMEDLAMPPRESET": 2},
]
GUION = [("I1", 0.3), (None, 2), ("S1", 0.6), (None, 4), ("S2", 0.4), (None, 2), ("S1", 0.3),
         (None, 1), ("I2", 0.3), (None, 1), ("I1", 0.3), (None, 1), ("I3", 0.5), (None, 1),
         ("I1", 0.3), ("S1", 0.3), (None, 5)]
for n, regs in enumerate(CASOS_V5, 1):
    a, b = programa_v5(regs, V5), programa_v5(regs, si.ST_V6)
    difs, vistos = 0, set()
    for entrada, dur in GUION:
        if entrada:
            a.poner(**{entrada: True}); b.poner(**{entrada: True})
        for _ in range(int(round(dur / si.SCAN_S))):
            a.scan(); b.scan()
            vistos.add((a.V["VFD_CONTROL"], a.V["PHQ3"], a.V["PHQ4"], a.V["PHQ5"]))
            if any(a.V[x] != b.V[x] for x in SALIDAS_FIS):
                difs += 1
        if entrada:
            a.poner(**{entrada: False}); b.poner(**{entrada: False})
    check(difs == 0, f"caso v5 {n}: {difs} scans con salidas distintas entre v5 y v6")
    check(len(vistos) > 1, f"caso v5 {n}: el guion ejercita la logica (salidas cambian)")
    check(b.V["ES"][0] == 6 and a.V["ES"][0] == 0, "solo el v6 se identifica en %R460")
print(f"   OK ({OK})")

print(f"\nTODAS LAS PRUEBAS DEL MOTOR CONFIGURABLE PASARON ({OK} comprobaciones).")
