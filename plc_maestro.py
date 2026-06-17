"""
============================================================================
 INTERFAZ DE CONFIGURACION DEL MOTOR LOGICO  (Horner XL4 - Modbus TCP)
 VERSION CON TIMERS, PULSO TEMPORIZADO, CONTADORES RETENTIVOS,
 RESET FISICO CONFIGURABLE Y CONTADOR MANTENIDO
============================================================================

Python NO reprograma el PLC.
Solo escribe registros %R.

MAPEO %Rn -> Modbus holding 0-based = n + 2999

SALIDAS:
  Q10 = verde
  Q11 = amarilla
  Q12 = roja

ENTRADAS:
  I1 = codigo 1
  I2 = codigo 2
  I3 = codigo 3
  I4 = codigo 4
  I7 = codigo 5

TIPO FISICO:
  I1, I3, I4 = NA
  I2, I7     = NC
============================================================================
"""

import sys
import json

from pymodbus.client import ModbusTcpClient


PLC_IP = "192.168.3.12"
PLC_PORT = 502
UNIT_ID = 1


def R(n: int) -> int:
    return n + 2999


# ---------------------------------------------------------------------------
# REGISTROS GENERALES
# ---------------------------------------------------------------------------
ADDR_CMD = R(1)
ADDR_GENSTOP = R(2)
ADDR_INDEX = R(3)
ADDR_STATUS = R(4)


# ---------------------------------------------------------------------------
# BLOQUES POR SALIDA
# Q10 usa %R10..%R19
# Q11 usa %R20..%R29
# Q12 usa %R30..%R39
# ---------------------------------------------------------------------------
OUT_BASE = {
    "Q10": 10,
    "Q11": 20,
    "Q12": 30,
    "VERDE": 10,
    "AMARILLA": 20,
    "ROJA": 30,
}


# ---------------------------------------------------------------------------
# CODIGOS DE ENTRADA
# ---------------------------------------------------------------------------
SRC = {
    "NINGUNA": 0,
    "I1": 1,
    "I2": 2,
    "I3": 3,
    "I4": 4,
    "I7": 5,
}


INPUT_NC = {
    "I1": False,
    "I2": True,
    "I3": False,
    "I4": False,
    "I7": True,
}


# ---------------------------------------------------------------------------
# MODOS
# ---------------------------------------------------------------------------
MODE_OFF = 0
MODE_DIRECTO = 1
MODE_ENCLAVADO = 2

TIMER_OFF = 0
TIMER_RET_ON_DELAY = 1
TIMER_PULSO_TEMPORIZADO = 2

COUNTER_OFF = 0
COUNTER_UP_BASE = 1
COUNTER_UP_MANTENIDO = 2


# ---------------------------------------------------------------------------
# ACUMULADOS
# ---------------------------------------------------------------------------
ADDR_TACC = {
    "Q10": R(40),
    "Q11": R(41),
    "Q12": R(42),
    "VERDE": R(40),
    "AMARILLA": R(41),
    "ROJA": R(42),
}

ADDR_CACC = {
    "Q10": R(43),
    "Q11": R(44),
    "Q12": R(45),
    "VERDE": R(43),
    "AMARILLA": R(44),
    "ROJA": R(45),
}


# ---------------------------------------------------------------------------
# RESET POR SOFTWARE
# ---------------------------------------------------------------------------
ADDR_RESET_TIMER = {
    "Q10": R(46),
    "Q11": R(47),
    "Q12": R(48),
    "VERDE": R(46),
    "AMARILLA": R(47),
    "ROJA": R(48),
}

ADDR_RESET_COUNTER = {
    "Q10": R(49),
    "Q11": R(50),
    "Q12": R(51),
    "VERDE": R(49),
    "AMARILLA": R(50),
    "ROJA": R(51),
}


# ---------------------------------------------------------------------------
# RESET FISICO CONFIGURABLE PARA CONTADOR
# ---------------------------------------------------------------------------
ADDR_COUNTER_RESET_SRC = {
    "Q10": R(52),
    "Q11": R(53),
    "Q12": R(54),
    "VERDE": R(52),
    "AMARILLA": R(53),
    "ROJA": R(54),
}


class XL4:
    def __init__(self, ip=PLC_IP, port=PLC_PORT, unit=UNIT_ID):
        self.ip = ip
        self.port = port
        self.unit = unit
        self.client = ModbusTcpClient(ip, port=port)

    # -----------------------------------------------------------------------
    # CONEXION
    # -----------------------------------------------------------------------
    def connect(self):
        if not self.client.connect():
            raise ConnectionError(f"No conecta a {self.ip}:{self.port}")
        print("Conectado al XL4.")

    def close(self):
        self.client.close()

    # -----------------------------------------------------------------------
    # MODBUS
    # -----------------------------------------------------------------------
    def _w(self, addr, value):
        value = int(value) & 0xFFFF

        try:
            rr = self.client.write_register(addr, value, device_id=self.unit)
        except TypeError:
            rr = self.client.write_register(addr, value, slave=self.unit)

        if rr is not None and hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"Error escribiendo registro Modbus {addr}: {rr}")

    def _r(self, addr):
        try:
            rr = self.client.read_holding_registers(addr, count=1, device_id=self.unit)
        except TypeError:
            rr = self.client.read_holding_registers(addr, count=1, slave=self.unit)

        if rr is None:
            raise RuntimeError(f"Sin respuesta leyendo registro Modbus {addr}")

        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"Error leyendo registro Modbus {addr}: {rr}")

        return rr.registers[0]

    # -----------------------------------------------------------------------
    # VALIDACIONES
    # -----------------------------------------------------------------------
    def _src(self, nombre):
        if nombre is None:
            return 0

        n = str(nombre).upper()

        if n not in SRC:
            raise ValueError(f"Entrada no valida: {nombre}. Usa: {list(SRC.keys())}")

        return SRC[n]

    def _out(self, salida):
        s = str(salida).upper()

        if s not in OUT_BASE:
            raise ValueError(f"Salida no valida: {salida}. Usa: {list(OUT_BASE.keys())}")

        return s

    def _es_nc(self, nombre):
        if nombre is None:
            return False
        return INPUT_NC.get(str(nombre).upper(), False)

    # -----------------------------------------------------------------------
    # ESCRITURA DE CONFIGURACION BASE
    # -----------------------------------------------------------------------
    def _escribir_salida(self, salida, mode, srcA, srcB, stop, enable, flags=0):
        s = self._out(salida)
        base = OUT_BASE[s]

        self._w(R(base + 0), mode)
        self._w(R(base + 1), srcA)
        self._w(R(base + 2), srcB)
        self._w(R(base + 3), stop)
        self._w(R(base + 4), enable)
        self._w(R(base + 5), flags)

    # -----------------------------------------------------------------------
    # COMANDOS GENERALES
    # -----------------------------------------------------------------------
    def habilitar(self, on=True):
        self._w(ADDR_CMD, 1 if on else 0)
        print(f"Sistema {'HABILITADO' if on else 'DESHABILITADO'}")

    def paro_general(self, entrada):
        self._w(ADDR_GENSTOP, self._src(entrada))
        print(f"Paro general -> {entrada}")

    def quitar_paro_general(self):
        self._w(ADDR_GENSTOP, 0)
        print("Paro general desactivado")

    def apagar(self, salida):
        self._escribir_salida(salida, MODE_OFF, 0, 0, 0, 0, 0)
        print(f"{salida}: desactivada")

    # -----------------------------------------------------------------------
    # LOGICAS BASE
    # -----------------------------------------------------------------------
    def directo(self, salida, entrada, habilitacion=None):
        self._escribir_salida(
            salida=salida,
            mode=MODE_DIRECTO,
            srcA=self._src(entrada),
            srcB=0,
            stop=0,
            enable=self._src(habilitacion),
            flags=0,
        )

        print(
            f"{salida}: DIRECTO con {entrada}"
            + (f", habilita {habilitacion}" if habilitacion else "")
        )

    def enclavar(self, salida, arranque, paro=None, habilitacion=None):
        self._escribir_salida(
            salida=salida,
            mode=MODE_ENCLAVADO,
            srcA=self._src(arranque),
            srcB=0,
            stop=self._src(paro),
            enable=self._src(habilitacion),
            flags=0,
        )

        print(
            f"{salida}: ENCLAVADO arranque={arranque}"
            + (f" paro={paro}" if paro else "")
            + (f" habilita={habilitacion}" if habilitacion else "")
        )

    def combinacional(self, salida, a, b, op="OR", enclavado=False, paro=None):
        mode = MODE_ENCLAVADO if enclavado else MODE_DIRECTO
        op = op.upper()

        if op == "OR":
            srcA = self._src(a)
            srcB = self._src(b)
            srcEn = 0

        elif op == "AND":
            srcA = self._src(a)
            srcB = 0
            srcEn = self._src(b)

        else:
            raise ValueError("op debe ser 'AND' u 'OR'.")

        self._escribir_salida(
            salida=salida,
            mode=mode,
            srcA=srcA,
            srcB=srcB,
            stop=self._src(paro),
            enable=srcEn,
            flags=0,
        )

        print(
            f"{salida}: {op}({a},{b})"
            + (" enclavado" if enclavado else "")
            + (f" paro={paro}" if paro else "")
        )

    # -----------------------------------------------------------------------
    # TIMER
    # -----------------------------------------------------------------------
    def configurar_timer(self, salida, segundos):
        s = self._out(salida)
        base = OUT_BASE[s]
        segundos = int(segundos)

        if segundos < 0 or segundos > 32767:
            raise ValueError("El preset del timer debe estar entre 0 y 32767 segundos.")

        self._w(R(base + 6), TIMER_RET_ON_DELAY)
        self._w(R(base + 7), segundos)

        print(f"{salida}: TIMER retentivo configurado a {segundos} s")

    def configurar_pulso_salida(self, salida, segundos):
        """
        Aplica TimerMode = 2 a una salida que ya tenga logica base configurada.
        """
        s = self._out(salida)
        base = OUT_BASE[s]
        segundos = int(segundos)

        if segundos < 1 or segundos > 32767:
            raise ValueError("El tiempo debe estar entre 1 y 32767 segundos.")

        self._w(R(base + 6), TIMER_PULSO_TEMPORIZADO)
        self._w(R(base + 7), segundos)

        print(f"{salida}: pulso temporizado configurado a {segundos} s")

    def pulso_temporizado(self, salida, entrada, segundos):
        """
        Configura una salida directa con una entrada y pulso temporizado.
        """
        self.directo(salida, entrada)
        self.configurar_pulso_salida(salida, segundos)

        print(f"{salida}: prende con {entrada} durante {segundos} segundos y luego se apaga")

    def quitar_timer(self, salida):
        s = self._out(salida)
        base = OUT_BASE[s]

        self._w(R(base + 6), TIMER_OFF)
        self._w(R(base + 7), 0)

        print(f"{salida}: TIMER desactivado")

    def reset_timer(self, salida):
        s = self._out(salida)
        self._w(ADDR_RESET_TIMER[s], 1)

        print(f"{salida}: reset de TIMER enviado")

    # -----------------------------------------------------------------------
    # CONTADOR
    # -----------------------------------------------------------------------
    def configurar_contador(self, salida, conteos):
        """
        CounterMode = 1.
        La salida solo prende si la entrada base está activa y el contador llegó al preset.
        """
        s = self._out(salida)
        base = OUT_BASE[s]
        conteos = int(conteos)

        if conteos < 0 or conteos > 32767:
            raise ValueError("El preset del contador debe estar entre 0 y 32767.")

        self._w(R(base + 8), COUNTER_UP_BASE)
        self._w(R(base + 9), conteos)

        print(f"{salida}: CONTADOR retentivo configurado a {conteos} conteos")

    def configurar_contador_mantenido(self, salida, conteos):
        """
        CounterMode = 2.

        La salida se mantiene encendida cuando CounterAcc >= CounterPreset,
        aunque la entrada de conteo ya no esté presionada.

        Se apaga únicamente al resetear el contador.
        """
        s = self._out(salida)
        base = OUT_BASE[s]
        conteos = int(conteos)

        if conteos < 1 or conteos > 32767:
            raise ValueError("El preset del contador debe estar entre 1 y 32767.")

        self._w(R(base + 8), COUNTER_UP_MANTENIDO)
        self._w(R(base + 9), conteos)

        print(f"{salida}: CONTADOR mantenido configurado a {conteos} conteos")

    def quitar_contador(self, salida):
        s = self._out(salida)
        base = OUT_BASE[s]

        self._w(R(base + 8), COUNTER_OFF)
        self._w(R(base + 9), 0)

        print(f"{salida}: CONTADOR desactivado")

    def reset_contador(self, salida):
        s = self._out(salida)
        self._w(ADDR_RESET_COUNTER[s], 1)

        print(f"{salida}: reset de CONTADOR enviado")

    def configurar_reset_contador(self, salida, entrada):
        """
        Define qué entrada física resetea el contador de una salida.

        entrada:
          None o "NINGUNA" = sin reset físico
          "I1", "I2", "I3", "I4", "I7"
        """
        s = self._out(salida)
        self._w(ADDR_COUNTER_RESET_SRC[s], self._src(entrada))

        if entrada is None or str(entrada).upper() == "NINGUNA":
            print(f"{salida}: reset físico de contador desactivado")
        else:
            print(f"{salida}: contador se resetea con {entrada}")

    def quitar_reset_contador(self, salida):
        s = self._out(salida)
        self._w(ADDR_COUNTER_RESET_SRC[s], 0)

        print(f"{salida}: reset físico de contador desactivado")

    # -----------------------------------------------------------------------
    # RESET GENERAL
    # -----------------------------------------------------------------------
    def reset_todo(self, borrar_acumulados=True):
        self._w(ADDR_CMD, 0)

        for s in ("Q10", "Q11", "Q12"):
            self.apagar(s)
            self.quitar_timer(s)
            self.quitar_contador(s)
            self.quitar_reset_contador(s)

            if borrar_acumulados:
                self.reset_timer(s)
                self.reset_contador(s)

        self._w(ADDR_GENSTOP, 0)

        print("Configuracion reiniciada.")

    # -----------------------------------------------------------------------
    # LECTURAS
    # -----------------------------------------------------------------------
    def leer_estado(self):
        idx = self._r(ADDR_INDEX)
        status = self._r(ADDR_STATUS)

        I1 = idx & 1
        I2 = (idx >> 1) & 1
        I3 = (idx >> 2) & 1
        I4 = (idx >> 3) & 1
        I7 = (idx >> 4) & 1

        Q10 = status & 1
        Q11 = (status >> 1) & 1
        Q12 = (status >> 2) & 1

        print(
            f"Entradas electricas: "
            f"I1={I1} I2={I2} I3={I3} I4={I4} I7={I7}  |  "
            f"Salidas: Q10={Q10} Q11={Q11} Q12={Q12}"
        )

        return idx, status

    def leer_acumulados(self):
        datos = {}

        for s in ("Q10", "Q11", "Q12"):
            timer_s = self._r(ADDR_TACC[s])
            contador = self._r(ADDR_CACC[s])

            datos[s] = {
                "timer_s": timer_s,
                "contador": contador,
            }

        print(
            "Acumulados | "
            f"Q10: T={datos['Q10']['timer_s']}s C={datos['Q10']['contador']} | "
            f"Q11: T={datos['Q11']['timer_s']}s C={datos['Q11']['contador']} | "
            f"Q12: T={datos['Q12']['timer_s']}s C={datos['Q12']['contador']}"
        )

        return datos


# ===========================================================================
# DESPACHADOR JSON  ->  LLAMADAS A XL4
# ===========================================================================
# La IA NO genera geometria ladder ni registros: devuelve este JSON dual.
# Python lee SOLO la parte de configuracion del motor (output/logic/timer/
# counter/system/reset_before) y la traduce a llamadas de la clase XL4.
# Los campos de presentacion del JSON dual ("expr", "comment", "view"...) son
# del frontend para dibujar el ladder y aqui se ignoran.
#
# Forma esperada (ver tambien CONTRACT del proyecto):
#   {
#     "name": "Demo maletin",
#     "device": "maletin_basico",
#     "reset_before": true,
#     "system": { "enable": true, "global_stop": null },
#     "outputs": [
#       { "output": "Q11",
#         "logic": { "mode": "combinacional", "a": "I1", "b": "I3", "op": "OR" },
#         "timer": { "type": "pulse", "preset_s": 5 },
#         "counter": null,
#         "expr": "I1 + I3" },                # <- solo para el render del front
#       { "output": "Q10",
#         "logic": { "mode": "directo", "source": "I2" },
#         "counter": { "type": "up_held", "preset": 3, "reset_input": "I4" } }
#     ]
#   }
# ---------------------------------------------------------------------------

ENTRADAS_VALIDAS = set(SRC.keys())                 # NINGUNA, I1, I2, I3, I4, I7
SALIDAS_VALIDAS = set(OUT_BASE.keys())             # Q10..Q12 + alias de color
MODOS_LOGICA = {"off", "directo", "enclavado", "combinacional"}
TIPOS_TIMER = {"on_delay", "pulse"}
TIPOS_COUNTER = {"up", "up_held"}


def _es_entrada_valida(nombre):
    """Una entrada del JSON es valida si es None o uno de los codigos SRC."""
    return nombre is None or str(nombre).upper() in ENTRADAS_VALIDAS


def _canon_salida(salida):
    """Numero de bloque base de una salida, para detectar duplicados aunque
    se mezclen alias ('Q10' y 'VERDE' son la misma salida fisica)."""
    return OUT_BASE.get(str(salida).upper())


def _entero_en_rango(valor, low, high, etiqueta, errores):
    try:
        v = int(valor)
    except (TypeError, ValueError):
        errores.append(f"{etiqueta}: '{valor}' no es un entero.")
        return None
    if v < low or v > high:
        errores.append(f"{etiqueta}: {v} fuera de rango [{low}, {high}].")
    return v


def validar_config(cfg) -> list:
    """Valida el JSON sin tocar Modbus. Devuelve la lista de errores
    (vacia = valido). Pensado para que el backend muestre fallos claros
    antes de enviar nada al PLC."""
    errores = []
    if not isinstance(cfg, dict):
        return ["El JSON raiz no es un objeto."]

    outputs = cfg.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        errores.append("Falta 'outputs' o esta vacio: debe haber al menos una salida.")
        outputs = []

    vistos = set()
    for i, o in enumerate(outputs):
        tag = f"salida {i + 1}"
        if not isinstance(o, dict):
            errores.append(f"{tag}: no es un objeto.")
            continue

        salida = o.get("output")
        tag = f"salida {i + 1} ({salida})"
        if str(salida).upper() not in SALIDAS_VALIDAS:
            errores.append(f"{tag}: salida invalida. Usa {sorted({'Q10', 'Q11', 'Q12'})}.")
        else:
            canon = _canon_salida(salida)
            if canon in vistos:
                errores.append(f"{tag}: salida repetida (ya configurada antes).")
            vistos.add(canon)

        lg = o.get("logic") or {"mode": "off"}
        if not isinstance(lg, dict):
            errores.append(f"{tag}: 'logic' no es un objeto.")
            lg = {"mode": "off"}
        mode = str(lg.get("mode", "off")).lower()
        if mode not in MODOS_LOGICA:
            errores.append(f"{tag}: mode '{mode}' no soportado. Usa {sorted(MODOS_LOGICA)}.")

        if mode == "directo":
            if not lg.get("source"):
                errores.append(f"{tag}: 'directo' requiere 'source'.")
            for campo in ("source", "enable"):
                if not _es_entrada_valida(lg.get(campo)):
                    errores.append(f"{tag}: '{campo}'='{lg.get(campo)}' no es una entrada valida.")
        elif mode == "enclavado":
            if not lg.get("start"):
                errores.append(f"{tag}: 'enclavado' requiere 'start'.")
            for campo in ("start", "stop", "enable"):
                if not _es_entrada_valida(lg.get(campo)):
                    errores.append(f"{tag}: '{campo}'='{lg.get(campo)}' no es una entrada valida.")
        elif mode == "combinacional":
            if not lg.get("a") or not lg.get("b"):
                errores.append(f"{tag}: 'combinacional' requiere 'a' y 'b'.")
            for campo in ("a", "b", "stop"):
                if not _es_entrada_valida(lg.get(campo)):
                    errores.append(f"{tag}: '{campo}'='{lg.get(campo)}' no es una entrada valida.")
            op = str(lg.get("op", "OR")).upper()
            if op not in ("OR", "AND"):
                errores.append(f"{tag}: 'op'='{op}' debe ser OR o AND.")
            if op == "AND" and lg.get("enable"):
                errores.append(f"{tag}: en AND el segundo operando usa el slot de enable; "
                               "no se puede pasar 'enable' aparte.")

        tm = o.get("timer")
        if tm:
            if not isinstance(tm, dict) or str(tm.get("type")).lower() not in TIPOS_TIMER:
                errores.append(f"{tag}: timer.type debe ser {sorted(TIPOS_TIMER)}.")
            else:
                low = 0 if tm["type"].lower() == "on_delay" else 1
                _entero_en_rango(tm.get("preset_s"), low, 32767, f"{tag} timer.preset_s", errores)

        ct = o.get("counter")
        if ct:
            if not isinstance(ct, dict) or str(ct.get("type")).lower() not in TIPOS_COUNTER:
                errores.append(f"{tag}: counter.type debe ser {sorted(TIPOS_COUNTER)}.")
            else:
                low = 0 if ct["type"].lower() == "up" else 1
                _entero_en_rango(ct.get("preset"), low, 32767, f"{tag} counter.preset", errores)
                if not _es_entrada_valida(ct.get("reset_input")):
                    errores.append(f"{tag}: counter.reset_input='{ct.get('reset_input')}' invalido.")

    sysc = cfg.get("system") or {}
    if not isinstance(sysc, dict):
        errores.append("'system' no es un objeto.")
    elif not _es_entrada_valida(sysc.get("global_stop")):
        errores.append(f"system.global_stop='{sysc.get('global_stop')}' no es una entrada valida.")

    return errores


def plan_config(cfg) -> list:
    """Traduce el JSON a una lista ordenada de (metodo, args, kwargs) SIN
    tocar Modbus. Permite inspeccionar/auditar el plan o ejecutarlo en seco.
    Orden: reset_todo -> por salida (base, timer, counter, reset) ->
    paro_general -> habilitar al final (igual que el ejemplo de uso)."""
    plan = []

    if cfg.get("reset_before", True):
        plan.append(("reset_todo", (), {"borrar_acumulados": True}))

    for o in cfg.get("outputs", []):
        out = o["output"]
        lg = o.get("logic") or {"mode": "off"}
        mode = str(lg.get("mode", "off")).lower()

        if mode == "off":
            plan.append(("apagar", (out,), {}))
        elif mode == "directo":
            plan.append(("directo", (out, lg.get("source")),
                         {"habilitacion": lg.get("enable")}))
        elif mode == "enclavado":
            plan.append(("enclavar", (out, lg.get("start")),
                         {"paro": lg.get("stop"), "habilitacion": lg.get("enable")}))
        elif mode == "combinacional":
            plan.append(("combinacional", (out, lg.get("a"), lg.get("b")),
                         {"op": str(lg.get("op", "OR")).upper(),
                          "enclavado": bool(lg.get("latched", False)),
                          "paro": lg.get("stop")}))

        tm = o.get("timer")
        if tm:
            metodo = ("configurar_timer" if str(tm["type"]).lower() == "on_delay"
                      else "configurar_pulso_salida")
            plan.append((metodo, (out, tm["preset_s"]), {}))

        ct = o.get("counter")
        if ct:
            metodo = ("configurar_contador" if str(ct["type"]).lower() == "up"
                      else "configurar_contador_mantenido")
            plan.append((metodo, (out, ct["preset"]), {}))
            if ct.get("reset_input"):
                plan.append(("configurar_reset_contador", (out, ct["reset_input"]), {}))

    sysc = cfg.get("system") or {}
    if sysc.get("global_stop"):
        plan.append(("paro_general", (sysc["global_stop"],), {}))
    if sysc.get("enable", True):
        plan.append(("habilitar", (True,), {}))

    return plan


def aplicar_config(plc: "XL4", cfg, dry_run=False) -> list:
    """Valida y aplica el JSON dual sobre un XL4. Lanza ValueError con todos
    los errores si el JSON no es valido. Con dry_run=True imprime el plan sin
    escribir Modbus (util para probar sin PLC). Devuelve el plan ejecutado."""
    errores = validar_config(cfg)
    if errores:
        raise ValueError("JSON invalido:\n  - " + "\n  - ".join(errores))

    plan = plan_config(cfg)
    for metodo, args, kwargs in plan:
        if dry_run:
            firma = ", ".join([repr(a) for a in args]
                              + [f"{k}={v!r}" for k, v in kwargs.items()])
            print(f"[dry-run] plc.{metodo}({firma})")
        else:
            getattr(plc, metodo)(*args, **kwargs)
    return plan


def cargar_y_aplicar(ruta_json, dry_run=False):
    """Lee un JSON de disco y lo aplica. Sin dry_run abre conexion al PLC."""
    with open(ruta_json, encoding="utf-8") as f:
        cfg = json.load(f)
    if dry_run:
        return aplicar_config(None, cfg, dry_run=True)
    plc = XL4()
    plc.connect()
    try:
        return aplicar_config(plc, cfg, dry_run=False)
    finally:
        plc.close()


# JSON dual de referencia: reproduce exactamente el ejemplo de uso de abajo.
EJEMPLO_CONFIG = {
    "name": "Demo maletin",
    "device": "maletin_basico",
    "reset_before": True,
    "system": {"enable": True, "global_stop": None},
    "outputs": [
        {
            "output": "Q11",
            "logic": {"mode": "combinacional", "a": "I1", "b": "I3", "op": "OR"},
            "timer": {"type": "pulse", "preset_s": 5},
            "expr": "I1 + I3",
            "comment": "I1 o I3 encienden la amarilla 5 s",
        },
        {
            "output": "Q10",
            "logic": {"mode": "directo", "source": "I2"},
            "counter": {"type": "up_held", "preset": 3, "reset_input": "I4"},
            "expr": "I2",
            "comment": "Cuenta a 3 y se mantiene; I4 resetea",
        },
    ],
}


# ---------------------------------------------------------------------------
# EJEMPLO DE USO
# ---------------------------------------------------------------------------
#   python plc_maestro.py            -> conecta al PLC y aplica EJEMPLO_CONFIG
#   python plc_maestro.py --dry-run  -> imprime el plan de llamadas, SIN PLC
#   python plc_maestro.py prog.json  -> aplica un JSON de disco (conecta al PLC)
#   python plc_maestro.py prog.json --dry-run -> valida e imprime el plan, SIN PLC
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    rutas = [a for a in args if a != "--dry-run"]

    if rutas:
        cargar_y_aplicar(rutas[0], dry_run=dry_run)
    elif dry_run:
        aplicar_config(None, EJEMPLO_CONFIG, dry_run=True)
    else:
        plc = XL4()
        plc.connect()
        try:
            aplicar_config(plc, EJEMPLO_CONFIG, dry_run=False)
            plc.leer_estado()
            plc.leer_acumulados()
        finally:
            plc.close()
