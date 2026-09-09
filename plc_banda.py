"""
============================================================================
CAPA FISICA DE LA BANDA TRANSPORTADORA  (PLC INDEPENDIENTE)
============================================================================

Espejo EXACTO de "Programa_Banda.txt" (PROGRAMA MAESTRO - BANDA
TRANSPORTADORA STANDALONE, Horner XL4 / XC1E5, Cscape 10.2 IEC ST).

Este modulo es GEMELO e INDEPENDIENTE de plc_maestro.py:

  plc_maestro.py  -> PLC del MALETIN  (Q10/Q11/Q12, I1..I7, secuenciador)
  plc_banda.py    -> PLC de la BANDA  (VFD, sensores S1/S2, torreta)

No comparten ni una sola direccion de registro ni se importan entre si. La
banda tiene su propio PLC, su propia IP y su propio Ladder maestro; una
instruccion de un equipo NUNCA puede acabar escrita en el otro.

Convencion de direcciones del XL4 (identica en los dos PLC, pero aplicada a
mapas distintos):  %Rnnnnn de Cscape  ->  registro Modbus  n + 2999
    %R00001 -> 3000     %R00500 -> 3499
============================================================================
"""

import os

from pymodbus.client import ModbusTcpClient


# ---------------------------------------------------------------------------
# CONEXION
# ---------------------------------------------------------------------------
# La banda es un PLC DISTINTO al del maletin: su IP se configura por variable
# de entorno BANDA_PLC_IP (o desde el frontend / POST /plc/config?device=banda).
# A proposito NO se hereda la IP del maletin: sin IP explicita se falla con un
# mensaje claro, en vez de arriesgarse a escribir la banda en el maletin.
PLC_IP = os.environ.get("BANDA_PLC_IP", "").strip()
PLC_PORT = int(os.environ.get("BANDA_PLC_PORT", "502"))
UNIT_ID = 1


def R(n: int) -> int:
    """%Rn de Cscape -> direccion Modbus (holding register)."""
    return n + 2999


# ---------------------------------------------------------------------------
# MAPA DE REGISTROS  (seccion "MAPA DE REGISTROS" del Ladder maestro)
# ---------------------------------------------------------------------------
# CONTROL GENERAL
ADDR_BAND_ENABLE   = R(1)    # BandEnable    RW  0=disable, 1=enable
ADDR_DIR_CMD       = R(2)    # DirCmd        RW  0=derecha, 1=izquierda
ADDR_NEW_CFG_FLAG  = R(3)    # NewCfgFlag    RW  flanco 0->1 dispara init VFD
ADDR_FREQ_REQUEST  = R(4)    # FreqRequest   RW  Hz (el PLC lo multiplica x100)
ADDR_BAND_STATUS   = R(5)    # BandStatus    RO  bitfield de estado
ADDR_RESET_CMD     = R(6)    # ResetCmd      RW  flanco 0->1 = reset VFD manual

# SENSOR S1 / S2  (misma estructura, bloques %R10.. y %R20..)
ADDR_S1_ENABLE        = R(10)
ADDR_S1_ACTION        = R(11)
ADDR_S1_TIMER_PRESET  = R(12)
ADDR_S1_COUNT_PRESET  = R(13)
ADDR_S1_COUNT_ACCUM   = R(14)   # RO
ADDR_S1_TORRETA_MASK  = R(15)
ADDR_S1_TIMER_ACCUM   = R(16)   # RO
ADDR_S1_COUNT_RESET   = R(17)

ADDR_S2_ENABLE        = R(20)
ADDR_S2_ACTION        = R(21)
ADDR_S2_TIMER_PRESET  = R(22)
ADDR_S2_COUNT_PRESET  = R(23)
ADDR_S2_COUNT_ACCUM   = R(24)   # RO
ADDR_S2_TORRETA_MASK  = R(25)
ADDR_S2_TIMER_ACCUM   = R(26)   # RO
ADDR_S2_COUNT_RESET   = R(27)

# Acceso por numero de sensor (1 / 2)
ADDR_SENSOR = {
    1: {"enable": ADDR_S1_ENABLE, "action": ADDR_S1_ACTION,
        "timer_preset": ADDR_S1_TIMER_PRESET, "count_preset": ADDR_S1_COUNT_PRESET,
        "count_accum": ADDR_S1_COUNT_ACCUM, "torreta_mask": ADDR_S1_TORRETA_MASK,
        "timer_accum": ADDR_S1_TIMER_ACCUM, "count_reset": ADDR_S1_COUNT_RESET},
    2: {"enable": ADDR_S2_ENABLE, "action": ADDR_S2_ACTION,
        "timer_preset": ADDR_S2_TIMER_PRESET, "count_preset": ADDR_S2_COUNT_PRESET,
        "count_accum": ADDR_S2_COUNT_ACCUM, "torreta_mask": ADDR_S2_TORRETA_MASK,
        "timer_accum": ADDR_S2_TIMER_ACCUM, "count_reset": ADDR_S2_COUNT_RESET},
}

# TORRETA  (bitmask b0=Verde, b1=Amarilla, b2=Roja; valores 0..7)
ADDR_TORRETA_RUN   = R(30)
ADDR_TORRETA_IDLE  = R(31)

# SECUENCIA INIT VFD  (solo lectura)
ADDR_INIT_SEQ_STATE   = R(50)
ADDR_INIT_TIMER_ACCUM = R(51)

# VFD (FIJOS, no modificar). Desde Python solo se LEEN: los gobierna el ladder.
ADDR_VFD_CONTROL   = R(500)   # 18=derecha, 34=izquierda, 1=stop
ADDR_VFD_SPEED_RAW = R(502)   # velocidad actual x100
ADDR_VFD_FREQ_SEND = R(504)   # FreqRequest x100
ADDR_VFD_RESET     = R(506)
ADDR_VFD_STATUS    = R(508)


# ---------------------------------------------------------------------------
# VOCABULARIO DEL LADDER
# ---------------------------------------------------------------------------
# DirCmd (%R2) -> comando que el ladder envia al VFD (%R500)
DIR_DERECHA = 0
DIR_IZQUIERDA = 1

BAND_DIR = {
    "derecha": DIR_DERECHA, "right": DIR_DERECHA, "der": DIR_DERECHA,
    "cw": DIR_DERECHA, "horario": DIR_DERECHA, "0": DIR_DERECHA,
    "izquierda": DIR_IZQUIERDA, "left": DIR_IZQUIERDA, "izq": DIR_IZQUIERDA,
    "ccw": DIR_IZQUIERDA, "antihorario": DIR_IZQUIERDA, "1": DIR_IZQUIERDA,
}

VFD_CMD_DERECHA   = 18
VFD_CMD_IZQUIERDA = 34
VFD_CMD_PARO      = 1

# S1_Action / S2_Action (%R11 / %R21), tal como los interpreta el CASE del ladder
ACTION_NADA            = 0   # sin accion configurada
ACTION_PARO_TEMPORIZADO = 1  # stop + timer + continua sola
ACTION_PARO_ENCLAVADO   = 2  # stop + timer + LATCH (queda parada)
ACTION_CONTAR           = 3   # solo cuenta los flancos del sensor
ACTION_CONTAR_Y_PARAR   = 4   # cuenta y al llegar al preset detiene la banda

SENSOR_ACTIONS = {
    "nada": ACTION_NADA,
    "paro_temporizado": ACTION_PARO_TEMPORIZADO,
    "paro_enclavado": ACTION_PARO_ENCLAVADO,
    "contar": ACTION_CONTAR,
    "contar_y_parar": ACTION_CONTAR_Y_PARAR,
}

# Sensores fisicos: S1 = %I0004 -> I[3] ; S2 = %I0005 -> I[4]. Ambos NC (el
# ladder los invierte con NOT).
SENSORES = {1: "S1", 2: "S2"}

# Botonera fisica del tablero (Ladder v2.0, seccion 1b). NO se controla por
# Modbus: el ladder la lee directo y el backend solo puede OBSERVAR su efecto.
#   %I0001 -> I[0]  I1  NA  arranque: flanco de subida pone BandEnable = 1
#   %I0002 -> I[1]  I2  NC  RESERVADO, sin funcion asignada en el ladder
#   %I0003 -> I[2]  I3  NC  paro: fuerza GenStop y pone BandEnable = 0
# I3 tiene prioridad sobre todo lo demas, incluido este backend: mientras
# este presionado el ladder borra BandEnable en cada scan, asi que un
# habilitar() por Modbus no surte efecto (ver el aviso en habilitar()).
BOTONES = {
    "I1": {"addr": "%I0001", "tipo": "NA", "funcion": "arranque"},
    "I2": {"addr": "%I0002", "tipo": "NC", "funcion": None},
    "I3": {"addr": "%I0003", "tipo": "NC", "funcion": "paro"},
}

# Bit de cada lampara dentro de una mascara de torreta (0..7)
TORRETA_BIT = {"verde": 1, "amarilla": 2, "roja": 4}

# Bits de BandStatus (%R5), para leer_estado()
STATUS_BITS = [
    (1,   "running"),        # b0 banda en movimiento
    (2,   "resetting"),      # b1 secuencia VFD reset activa
    (4,   "s1_wait"),        # b2 timer S1 corriendo
    (8,   "s2_wait"),        # b3 timer S2 corriendo
    (16,  "s1_count_done"),  # b4 contador S1 alcanzo preset
    (32,  "s2_count_done"),  # b5 contador S2 alcanzo preset
    (64,  "init_armed"),     # b6 secuencia init completa
    (128, "gen_stop"),       # b7 parada general activa
    (256, "stop_button"),    # b8 boton fisico de paro I3 presionado
]

INIT_SEQ_ESTADOS = {
    0: "idle", 1: "stopping", 2: "resetting",
    3: "waiting", 4: "loading", 5: "armed",
}

# Rango entero admitido por los registros del ladder (INT de Cscape)
INT_MAX = 32767
MASK_MAX = 7


# ---------------------------------------------------------------------------
# DRIVER MODBUS
# ---------------------------------------------------------------------------
class BandaPLC:
    """Cliente Modbus TCP del PLC de la banda transportadora.

    Cada metodo corresponde a un bloque del Ladder maestro de la banda. No
    escribe NINGUN registro fuera del mapa documentado arriba."""

    def __init__(self, ip=None, port=None, unit=UNIT_ID):
        ip = (ip or PLC_IP or "").strip()
        if not ip:
            raise ValueError(
                "No hay IP para el PLC de la banda. Configura BANDA_PLC_IP, "
                "manda 'ip' en la peticion o usa POST /plc/config con "
                "device='banda'."
            )
        self.ip = ip
        self.port = int(port or PLC_PORT)
        self.unit = unit
        self.client = ModbusTcpClient(self.ip, port=self.port)

    # -- conexion ----------------------------------------------------------
    def connect(self):
        if not self.client.connect():
            raise ConnectionError(
                f"No se pudo conectar al PLC de la banda en {self.ip}:{self.port}")
        print(f"Conectado al PLC de la BANDA {self.ip}:{self.port}")
        return self

    def close(self):
        self.client.close()

    # -- primitivas --------------------------------------------------------
    def _w(self, addr: int, valor: int):
        r = self.client.write_register(addr, int(valor), slave=self.unit)
        if r.isError():
            raise IOError(f"Error escribiendo %R{addr - 2999} (Modbus {addr}) = {valor}")

    def _r(self, addr: int) -> int:
        r = self.client.read_holding_registers(addr, count=1, slave=self.unit)
        if r.isError():
            raise IOError(f"Error leyendo %R{addr - 2999} (Modbus {addr})")
        return r.registers[0]

    # -- helpers de traduccion --------------------------------------------
    @staticmethod
    def _direccion(valor):
        if valor is None:
            return None
        if isinstance(valor, int):
            if valor not in (DIR_DERECHA, DIR_IZQUIERDA):
                raise ValueError("La direccion debe ser 0 (derecha) o 1 (izquierda).")
            return valor
        clave = str(valor).lower()
        if clave not in BAND_DIR:
            raise ValueError(f"Direccion no valida: {valor}. Usa 'derecha' o 'izquierda'.")
        return BAND_DIR[clave]

    @staticmethod
    def _accion(valor):
        if valor is None:
            return None
        if isinstance(valor, int):
            if valor not in SENSOR_ACTIONS.values():
                raise ValueError(f"Accion de sensor no valida: {valor} (0..4).")
            return valor
        clave = str(valor).lower()
        if clave not in SENSOR_ACTIONS:
            raise ValueError(
                f"Accion de sensor no valida: {valor}. Usa {list(SENSOR_ACTIONS)}.")
        return SENSOR_ACTIONS[clave]

    @staticmethod
    def _entero(valor, low, high, etiqueta):
        v = int(valor)
        if v < low or v > high:
            raise ValueError(f"{etiqueta} debe estar entre {low} y {high} (recibido {v}).")
        return v

    # -- §0/§12  configuracion general ------------------------------------
    def configurar_banda(self, frecuencia_hz=None, direccion=None):
        """Escribe FreqRequest (%R4) y DirCmd (%R2). NO arranca la banda."""
        if frecuencia_hz is not None:
            self._w(ADDR_FREQ_REQUEST,
                    self._entero(frecuencia_hz, 0, INT_MAX, "La frecuencia"))
        d = self._direccion(direccion)
        if d is not None:
            self._w(ADDR_DIR_CMD, d)
        print("BANDA configurada"
              + (f" | frecuencia={frecuencia_hz} Hz" if frecuencia_hz is not None else "")
              + (f" | direccion={'izquierda' if d == 1 else 'derecha'}" if d is not None else ""))

    # -- §6/§7  sensores S1 y S2 ------------------------------------------
    def configurar_sensor(self, n, accion=None, timer_preset=None,
                          count_preset=None, torreta_mask=None, habilitar=True):
        """Configura el bloque completo de S1 (%R10..%R15) o S2 (%R20..%R25).

        accion       : 'nada' | 'paro_temporizado' | 'paro_enclavado' |
                       'contar' | 'contar_y_parar'  (o su codigo 0..4)
        timer_preset : segundos que la banda se detiene (acciones 1 y 2)
        count_preset : preset del contador (acciones 3 y 4)
        torreta_mask : bitmask 0..7 que se superpone mientras dura el evento
        """
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}. Solo existen S1 y S2.")
        a = ADDR_SENSOR[n]

        self._w(a["enable"], 1 if habilitar else 0)

        acc = self._accion(accion)
        if acc is not None:
            self._w(a["action"], acc)
        if timer_preset is not None:
            self._w(a["timer_preset"],
                    self._entero(timer_preset, 0, INT_MAX, f"La espera de S{n}"))
        if count_preset is not None:
            self._w(a["count_preset"],
                    self._entero(count_preset, 0, INT_MAX, f"El preset del contador de S{n}"))
        if torreta_mask is not None:
            self._w(a["torreta_mask"],
                    self._entero(torreta_mask, 0, MASK_MAX, f"La mascara de torreta de S{n}"))

        print(f"S{n}: {'habilitado' if habilitar else 'deshabilitado'}"
              + (f" | accion={accion}" if accion is not None else "")
              + (f" | timer={timer_preset}s" if timer_preset is not None else "")
              + (f" | conteo={count_preset}" if count_preset is not None else "")
              + (f" | torreta={torreta_mask}" if torreta_mask is not None else ""))

    def apagar_sensor(self, n):
        """Deja el sensor sin efecto (Enable=0, Action=0)."""
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}.")
        self._w(ADDR_SENSOR[n]["enable"], 0)
        self._w(ADDR_SENSOR[n]["action"], ACTION_NADA)
        print(f"S{n}: deshabilitado")

    def reset_contador(self, n):
        """Pone a 0 el acumulado del sensor (%R17 / %R27; el ladder auto-limpia)."""
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}.")
        self._w(ADDR_SENSOR[n]["count_reset"], 1)
        print(f"S{n}: contador reseteado")

    # -- §10  torreta ------------------------------------------------------
    def configurar_torreta(self, mask_run=None, mask_idle=None):
        """TorretaRun (%R30) y TorretaIdle (%R31). Bitmask 0..7 (b0=V,b1=A,b2=R).

        La torreta la gobierna el ladder; aqui solo se declara que se enciende
        con la banda corriendo y que se enciende con la banda detenida."""
        if mask_run is not None:
            self._w(ADDR_TORRETA_RUN,
                    self._entero(mask_run, 0, MASK_MAX, "La mascara de torreta en marcha"))
        if mask_idle is not None:
            self._w(ADDR_TORRETA_IDLE,
                    self._entero(mask_idle, 0, MASK_MAX, "La mascara de torreta detenida"))
        print("Torreta configurada"
              + (f" | run={mask_run}" if mask_run is not None else "")
              + (f" | idle={mask_idle}" if mask_idle is not None else ""))

    # -- §2/§3  secuencia de inicializacion del VFD ------------------------
    def aplicar_nueva_config(self):
        """Flanco 0->1 en NewCfgFlag (%R3): dispara la secuencia init del VFD
        (stop -> reset -> espera 3 s -> carga frecuencia -> armed).

        SIEMPRE debe llamarse DESPUES de escribir la configuracion y ANTES de
        habilitar: hasta que InitArmed sea TRUE el ladder mantiene GenStop."""
        self._w(ADDR_NEW_CFG_FLAG, 0)
        self._w(ADDR_NEW_CFG_FLAG, 1)
        print("Nueva configuracion aplicada (secuencia init del VFD lanzada)")

    def reset_vfd(self):
        """Flanco 0->1 en ResetCmd (%R6): reset manual del VFD."""
        self._w(ADDR_RESET_CMD, 0)
        self._w(ADDR_RESET_CMD, 1)
        print("Reset del VFD solicitado")

    # -- §8/§9  marcha y paro ---------------------------------------------
    def habilitar(self, on=True):
        """BandEnable (%R1). Con 0 el ladder fuerza GenStop y VFD en paro.

        El boton fisico de paro I3 tiene prioridad sobre este registro: si
        esta presionado, el ladder vuelve a poner BandEnable en 0 en el
        siguiente scan y la banda no arranca. Se avisa en vez de dejar que
        el comando se pierda en silencio."""
        self._w(ADDR_BAND_ENABLE, 1 if on else 0)
        print(f"BANDA {'HABILITADA' if on else 'DETENIDA'}")
        if on and self.paro_por_boton():
            print("AVISO: el boton fisico de paro (I3) esta presionado; "
                  "la banda NO arrancara hasta que se libere y se pulse I1.")

    def paro_por_boton(self) -> bool:
        """True si el boton fisico de paro I3 esta presionado (BandStatus b8).

        Es la unica via para distinguir un paro por botonera de los demas
        motivos de gen_stop (sensor, secuencia init, BandEnable=0)."""
        return bool(self._r(ADDR_BAND_STATUS) & 256)

    def parar(self):
        self.habilitar(False)

    # -- lectura de estado -------------------------------------------------
    def leer_estado(self) -> dict:
        """Lee los registros RO del ladder (BandStatus, acumulados, VFD)."""
        status = self._r(ADDR_BAND_STATUS)
        estado = {nombre: bool(status & bit) for bit, nombre in STATUS_BITS}
        seq = self._r(ADDR_INIT_SEQ_STATE)
        estado.update({
            "band_status": status,
            "init_seq_state": seq,
            "init_seq": INIT_SEQ_ESTADOS.get(seq, str(seq)),
            "init_timer_s": self._r(ADDR_INIT_TIMER_ACCUM),
            "s1_count": self._r(ADDR_S1_COUNT_ACCUM),
            "s1_timer_s": self._r(ADDR_S1_TIMER_ACCUM),
            "s2_count": self._r(ADDR_S2_COUNT_ACCUM),
            "s2_timer_s": self._r(ADDR_S2_TIMER_ACCUM),
            "vfd_control": self._r(ADDR_VFD_CONTROL),
            "vfd_speed_hz": self._r(ADDR_VFD_SPEED_RAW) / 100.0,
        })
        return estado


# ---------------------------------------------------------------------------
# VALIDADOR DEL BLOQUE "band"
# ---------------------------------------------------------------------------
# Contrato del JSON (identico al que ya dibuja el frontend, mas campos
# ADITIVOS opcionales que el nuevo Ladder maestro hizo posibles):
#
#   {
#     "device": "banda",
#     "name": "...",
#     "band": {
#       "enable": true,
#       "direction": "derecha" | "izquierda",
#       "freq_hz": 0..32767 | null,
#       "wait_s1_s": 0..32767 | null,      # atajo: accion 'paro_temporizado'
#       "wait_s2_s": 0..32767 | null,
#       "s1_action": "nada"|"paro_temporizado"|"paro_enclavado"|"contar"|"contar_y_parar",
#       "s2_action": ...,
#       "count_s1": 0..32767 | null,       # preset del contador de S1
#       "count_s2": 0..32767 | null,
#       "torreta_s1": 0..7 | null,         # mascara durante el evento de S1
#       "torreta_s2": 0..7 | null,
#       "torreta_run": 0..7 | null,        # mascara con la banda en marcha
#       "torreta_idle": 0..7 | null        # mascara con la banda detenida
#     }
#   }
#
# retrigger_s1_s / retrigger_s2_s se ACEPTAN por compatibilidad con el
# frontend actual, pero NO se escriben: el Ladder maestro de la banda ya no
# tiene registro de anti-retrigger (lo resuelve con flancos S1_Rising).

BAND_DIRS = set(BAND_DIR.keys())
CAMPOS_IGNORADOS = ("retrigger_s1_s", "retrigger_s2_s")


def _entero_en_rango(valor, low, high, etiqueta, errores):
    try:
        v = int(valor)
    except (TypeError, ValueError):
        errores.append(f"{etiqueta}: '{valor}' no es un entero.")
        return None
    if v < low or v > high:
        errores.append(f"{etiqueta}: {v} fuera de [{low}, {high}].")
        return None
    return v


def validar_config(cfg) -> list:
    """Valida un engine_config de BANDA contra su Ladder maestro.

    Rechaza expresamente cualquier bloque del maletin (outputs / sequence):
    esos registros pertenecen al otro PLC y no existen aqui."""
    errores = []
    if not isinstance(cfg, dict):
        return ["El JSON raiz no es un objeto."]

    band = cfg.get("band")
    if not isinstance(band, dict):
        return ["Falta el bloque 'band': un programa de la banda debe traerlo."]

    if cfg.get("outputs"):
        errores.append("El PLC de la banda no tiene Q10/Q11/Q12 configurables: "
                       "'outputs' debe ir vacio (la torreta la maneja su ladder).")
    if cfg.get("sequence"):
        errores.append("El PLC de la banda no tiene secuenciador de pasos: "
                       "'sequence' no aplica a este equipo.")

    for campo in ("freq_hz", "wait_s1_s", "wait_s2_s", "count_s1", "count_s2"):
        if band.get(campo) is not None:
            _entero_en_rango(band[campo], 0, INT_MAX, f"band.{campo}", errores)

    for campo in ("torreta_s1", "torreta_s2", "torreta_run", "torreta_idle"):
        if band.get(campo) is not None:
            _entero_en_rango(band[campo], 0, MASK_MAX, f"band.{campo}", errores)

    d = band.get("direction")
    if d is not None and str(d).lower() not in BAND_DIRS:
        errores.append(f"band.direction='{d}' debe ser 'derecha' o 'izquierda'.")

    for n in (1, 2):
        acc = band.get(f"s{n}_action")
        if acc is None:
            continue
        if isinstance(acc, int):
            if acc not in SENSOR_ACTIONS.values():
                errores.append(f"band.s{n}_action={acc} debe estar entre 0 y 4.")
        elif str(acc).lower() not in SENSOR_ACTIONS:
            errores.append(f"band.s{n}_action='{acc}' debe ser uno de "
                           f"{sorted(SENSOR_ACTIONS)}.")

    return errores


# Valores historicos del anti-retrigger. El editor los usa para DIBUJAR sus
# rungs (presentacion); el PLC ya no los tiene. Solo se avisa cuando el usuario
# pidio un tiempo distinto del historico, para no llenar de ruido cada programa.
RETRIGGER_HISTORICO = {"retrigger_s1_s": 8, "retrigger_s2_s": 12}


def avisos_config(cfg) -> list:
    """Avisos no bloqueantes (campos que el Ladder maestro nuevo ya no usa)."""
    avisos = []
    band = (cfg or {}).get("band") or {}
    pedidos = [c for c in CAMPOS_IGNORADOS
               if band.get(c) is not None and band.get(c) != RETRIGGER_HISTORICO[c]]
    if pedidos:
        avisos.append(
            "El Ladder maestro de la banda ya no tiene registro de "
            "anti-retrigger (lo resuelve por flanco de los sensores): se "
            f"ignora {', '.join('band.' + c for c in pedidos)} al cargar al PLC.")
    return avisos


# ---------------------------------------------------------------------------
# PLAN Y APLICACION
# ---------------------------------------------------------------------------
def _accion_de_sensor(band, n):
    """Deduce (accion, timer_preset, count_preset) para S1 o S2 desde el JSON.

    'wait_sN_s' es el atajo historico del frontend: significa 'paro
    temporizado de N segundos' (Action=1 del ladder)."""
    accion = band.get(f"s{n}_action")
    espera = band.get(f"wait_s{n}_s")
    conteo = band.get(f"count_s{n}")

    if accion is None:
        if espera is not None:
            accion = "paro_temporizado"
        elif conteo is not None:
            accion = "contar"
        else:
            return None, None, None      # sensor no mencionado: no se toca

    return accion, espera, conteo


def plan_config(cfg) -> list:
    """Traduce el engine_config de banda a (metodo, args, kwargs) SIN Modbus.

    Orden impuesto por el Ladder maestro:
      1) parar (BandEnable=0)  -> estado seguro antes de reconfigurar
      2) configuracion (frecuencia, direccion, sensores, torreta)
      3) NewCfgFlag              -> secuencia init del VFD (deja InitArmed)
      4) BandEnable=1            -> la banda arranca
    """
    plan = []
    band = cfg.get("band") or {}
    arrancar = band.get("enable", True) is not False

    # 1) Estado seguro: nada se reconfigura con la banda en marcha.
    plan.append(("parar", (), {}))

    # 2) Configuracion
    plan.append(("configurar_banda", (), {
        "frecuencia_hz": band.get("freq_hz"),
        "direccion": band.get("direction"),
    }))

    for n in (1, 2):
        accion, espera, conteo = _accion_de_sensor(band, n)
        if accion is None:
            plan.append(("apagar_sensor", (n,), {}))
            continue
        plan.append(("configurar_sensor", (n,), {
            "accion": accion,
            "timer_preset": espera,
            "count_preset": conteo,
            "torreta_mask": band.get(f"torreta_s{n}"),
            "habilitar": True,
        }))
        # Un programa nuevo arranca con el conteo en cero.
        if str(accion).lower() in ("contar", "contar_y_parar") or accion in (3, 4):
            plan.append(("reset_contador", (n,), {}))

    if band.get("torreta_run") is not None or band.get("torreta_idle") is not None:
        plan.append(("configurar_torreta", (), {
            "mask_run": band.get("torreta_run"),
            "mask_idle": band.get("torreta_idle"),
        }))

    # 3) Secuencia de inicializacion del VFD (obligatoria tras reconfigurar)
    plan.append(("aplicar_nueva_config", (), {}))

    # 4) Marcha
    plan.append(("habilitar", (bool(arrancar),), {}))

    return plan


def aplicar_config(plc: "BandaPLC", cfg, dry_run=False) -> list:
    """Valida y aplica el engine_config de banda sobre su PLC."""
    errores = validar_config(cfg)
    if errores:
        raise ValueError("JSON invalido para la banda:\n  - " + "\n  - ".join(errores))

    plan = plan_config(cfg)
    for metodo, args, kwargs in plan:
        if dry_run:
            firma = ", ".join([repr(a) for a in args]
                              + [f"{k}={v!r}" for k, v in kwargs.items()])
            print(f"[dry-run] banda.{metodo}({firma})")
        else:
            getattr(plc, metodo)(*args, **kwargs)
    return plan


# JSON de referencia del equipo BANDA.
EJEMPLO_CONFIG = {
    "name": "Banda con paro por S1",
    "device": "banda",
    "band": {
        "enable": True,
        "direction": "derecha",
        "freq_hz": 30,
        "wait_s1_s": 5,
        "wait_s2_s": None,
    },
    "outputs": [],
}


if __name__ == "__main__":
    print("Plan de ejemplo para el PLC de la banda:")
    aplicar_config(None, EJEMPLO_CONFIG, dry_run=True)
