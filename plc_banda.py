"""
============================================================================
CAPA FISICA DE LA BANDA TRANSPORTADORA  (PLC INDEPENDIENTE)
============================================================================

Espejo EXACTO del Ladder maestro NUEVO de la banda:
    Archivos Cscape/Programa_Banda.txt        (texto estructurado)
    Archivos Cscape/ladder_maestro_banda.csp  (tabla de tags = direcciones)

Las direcciones de este modulo NO se deducen del texto del programa (el ST
usa solo nombres simbolicos): salen de la tabla de tags vigente del .csp,
que es la que el compilador de Cscape descargo al PLC.

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
# MAPA DE REGISTROS  (tabla de tags vigente de ladder_maestro_banda.csp)
# ---------------------------------------------------------------------------
# CONTROL GENERAL
# OJO: BandEnable ya NO es un registro. En el Ladder maestro nuevo es un BOOL
# global sin direccion, que SOLO enciende el boton fisico I1 (§4) y borran el
# boton I3 / GenStop (§3). %R1 es su ESPEJO de lectura (§16), no su mando.
ADDR_BAND_ENABLE_REG = R(1)    # BandEnable_Reg  RO  1 = habilitacion latcheada
ADDR_DIR_CMD         = R(2)    # DirCmd          RW  0=paro, 1=derecha, 2=izquierda
ADDR_BAND_STATUS     = R(3)    # BandStatus      RO  0=parada, 1=derecha, 2=izquierda
ADDR_FREQ_REQUEST    = R(4)    # FreqRequest     RW  Hz (sin escalar)
ADDR_NEW_CFG_FLAG    = R(5)    # NewCfgFlag      RW  cambio de valor <>0 = nueva config
ADDR_RESET_CMD       = R(6)    # ResetCmd        RW  cambio de valor <>0 = reset VFD
ADDR_CFG_READY_REG   = R(7)    # CfgReady_Reg    RO  1 = configuracion armada
ADDR_VFD_SPEED_DISP  = R(8)    # VFD_SpeedDisp   RO  velocidad actual (§9)

# SENSOR S1 (%R20..%R26) y S2 (%R30..%R36)
ADDR_S1_ENABLE        = R(20)
ADDR_S1_ACTION        = R(21)
ADDR_S1_TIMER_PRESET  = R(22)
ADDR_S1_COUNT_PRESET  = R(23)
ADDR_S1_TORRETA_MASK  = R(24)
ADDR_S1_COUNT_ACCUM   = R(25)   # RO salvo para ponerlo a 0 (no hay CountReset)
ADDR_S1_TIMER_ACCUM   = R(26)   # RO

ADDR_S2_ENABLE        = R(30)
ADDR_S2_ACTION        = R(31)
ADDR_S2_TIMER_PRESET  = R(32)
ADDR_S2_COUNT_PRESET  = R(33)
ADDR_S2_TORRETA_MASK  = R(34)
ADDR_S2_COUNT_ACCUM   = R(35)   # RO salvo para ponerlo a 0
ADDR_S2_TIMER_ACCUM   = R(36)   # RO

# Acceso por numero de sensor (1 / 2)
ADDR_SENSOR = {
    1: {"enable": ADDR_S1_ENABLE, "action": ADDR_S1_ACTION,
        "timer_preset": ADDR_S1_TIMER_PRESET, "count_preset": ADDR_S1_COUNT_PRESET,
        "torreta_mask": ADDR_S1_TORRETA_MASK, "count_accum": ADDR_S1_COUNT_ACCUM,
        "timer_accum": ADDR_S1_TIMER_ACCUM},
    2: {"enable": ADDR_S2_ENABLE, "action": ADDR_S2_ACTION,
        "timer_preset": ADDR_S2_TIMER_PRESET, "count_preset": ADDR_S2_COUNT_PRESET,
        "torreta_mask": ADDR_S2_TORRETA_MASK, "count_accum": ADDR_S2_COUNT_ACCUM,
        "timer_accum": ADDR_S2_TIMER_ACCUM},
}

# TORRETA  (bitmask b0=Verde, b1=Amarilla, b2=Roja; valores 0..7)
ADDR_TORRETA_RUN   = R(40)
ADDR_TORRETA_IDLE  = R(41)

# SECUENCIA INIT VFD  (solo lectura; los mueve la maquina de estados §7)
# OJO: InitSeqState e InitTimerAccum NO tienen $tag en el Ladder maestro, asi
# que el compilador de Cscape les asigna la direccion automaticamente y puede
# MOVERLAS en cualquier recompilacion. Estas dos direcciones se verificaron
# contra el PLC, pero son solo diagnostico: si dejan de cuadrar, pideles un
# $tag fijo en Cscape. Nada del flujo de carga depende de ellas.
ADDR_INIT_SEQ_STATE   = R(39)
ADDR_INIT_TIMER_ACCUM = R(42)

# VFD (FIJOS, no modificar). Desde Python solo se LEEN: los gobierna el ladder.
ADDR_VFD_CONTROL   = R(500)   # 18=derecha, 34=izquierda, 1=stop  (§15)
ADDR_VFD_SPEED_RAW = R(502)   # velocidad actual leida del variador (escala x100)
ADDR_VFD_FREQ_CALC = R(504)   # consigna de frecuencia enviada al variador (x100)
ADDR_VFD_RESET     = R(506)   # 0 / 2 durante la secuencia de reset (§7)


# ---------------------------------------------------------------------------
# VOCABULARIO DEL LADDER
# ---------------------------------------------------------------------------
# DirCmd (%R2) -> §15 lo traduce a VFD_Control (%R500):
#   DirCmd = 1 -> VFD_Control = 18 (derecha)
#   DirCmd = 2 -> VFD_Control = 34 (izquierda)
#   cualquier otro valor (0) -> VFD_Control = 1 (paro)
DIR_PARO      = 0
DIR_DERECHA   = 1
DIR_IZQUIERDA = 2

BAND_DIR = {
    "paro": DIR_PARO, "parar": DIR_PARO, "stop": DIR_PARO, "0": DIR_PARO,
    "derecha": DIR_DERECHA, "right": DIR_DERECHA, "der": DIR_DERECHA,
    "cw": DIR_DERECHA, "horario": DIR_DERECHA, "1": DIR_DERECHA,
    "izquierda": DIR_IZQUIERDA, "left": DIR_IZQUIERDA, "izq": DIR_IZQUIERDA,
    "ccw": DIR_IZQUIERDA, "antihorario": DIR_IZQUIERDA, "2": DIR_IZQUIERDA,
}

VFD_CMD_DERECHA   = 18
VFD_CMD_IZQUIERDA = 34
VFD_CMD_PARO      = 1

# S1_Action / S2_Action (%R21 / %R31), tal como los interpretan §10..§14:
#   >0  -> al flanco de subida del sensor se enclava el paro (SN_StopLatch)
#   2/4 -> ademas arranca el timer: la banda sigue sola al vencer TimerPreset
#   1/3 -> el paro dura mientras el sensor siga detectando la pieza
#   3/4 -> ademas superpone la mascara de torreta mientras dura el evento
ACTION_NADA                     = 0
ACTION_PARO_PRESENCIA           = 1
ACTION_PARO_TEMPORIZADO         = 2
ACTION_PARO_PRESENCIA_TORRETA   = 3
ACTION_PARO_TEMPORIZADO_TORRETA = 4

SENSOR_ACTIONS = {
    "nada": ACTION_NADA,
    "paro_presencia": ACTION_PARO_PRESENCIA,
    "paro_mientras_detecta": ACTION_PARO_PRESENCIA,
    "paro_temporizado": ACTION_PARO_TEMPORIZADO,
    "paro_presencia_torreta": ACTION_PARO_PRESENCIA_TORRETA,
    "paro_mientras_detecta_torreta": ACTION_PARO_PRESENCIA_TORRETA,
    "paro_temporizado_torreta": ACTION_PARO_TEMPORIZADO_TORRETA,
}

# Acciones del Ladder ANTERIOR que ya no existen. Se siguen ACEPTANDO para no
# romper programas guardados, pero se traducen a lo que el ladder nuevo si
# puede hacer, y avisos_config() lo explica:
#   'paro_enclavado' -> el ladder nuevo no tiene enclavamiento permanente; lo
#                       mas parecido es el paro temporizado (§11 lo libera).
#   'contar' / 'contar_y_parar' -> el conteo ya NO es una accion: cualquier
#                       sensor habilitado cuenta sus flancos en SN_CountAccum
#                       (§10), y SN_CountDone no detiene la banda.
SENSOR_ACTIONS_OBSOLETAS = {
    "paro_enclavado": ACTION_PARO_TEMPORIZADO,
    "contar": ACTION_NADA,
    "contar_y_parar": ACTION_NADA,
}

# Sensores fisicos: S1 = %I0004 -> PhIn4 ; S2 = %I0005 -> PhIn5. Ambos NC (el
# ladder los invierte con NOT en §2).
SENSORES = {1: "S1", 2: "S2"}

# Botonera fisica del tablero (§2/§3/§4). NO se controla por Modbus: el ladder
# la lee directo y el backend solo puede OBSERVAR su efecto en %R1/%R3.
#   %I0001 -> PhIn1  I1  NA  arranque: flanco de subida pone BandEnable = TRUE
#   %I0002 -> (sin uso en el Ladder maestro nuevo)
#   %I0003 -> PhIn3  I3  NC  paro: fuerza GenStop y borra BandEnable
# I3 tiene prioridad sobre todo. Y desde el Ladder maestro nuevo, I1 es la
# UNICA forma de habilitar la banda: no hay registro BandEnable escribible.
BOTONES = {
    "I1": {"addr": "%I0001", "tipo": "NA", "funcion": "arranque"},
    "I2": {"addr": "%I0002", "tipo": "NC", "funcion": None},
    "I3": {"addr": "%I0003", "tipo": "NC", "funcion": "paro"},
}

# Bit de cada lampara dentro de una mascara de torreta (0..7)
TORRETA_BIT = {"verde": 1, "amarilla": 2, "roja": 4}

# BandStatus (%R3) segun §16: ya NO es un bitfield, es un enumerado.
BAND_STATUS = {
    0: "parada",
    1: "corriendo_derecha",
    2: "corriendo_izquierda",
}

# InitSeqState (%R39) segun la maquina de estados de §7.
INIT_SEQ_ESTADOS = {
    0: "idle",              # sin secuencia en curso
    1: "arranque",          # arma la secuencia, limpia VFD_ResetReg
    2: "pulso_reset",       # VFD_ResetReg := 2
    3: "espera_reset",      # mantiene el reset 2 s (InitTimerAccum)
    4: "carga_config",      # carga FreqRequest, guarda RETAIN, CfgReady := 1
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
    # pymodbus renombro el argumento que identifica al servidor Modbus: hasta
    # 3.8 era slave=, desde 3.9 es device_id= (y en 3.13, que es la version
    # instalada, slave= ya ni existe y la llamada revienta con TypeError). Se
    # intenta el nombre moderno y se cae al antiguo, exactamente igual que
    # plc_maestro.XL4, para que los dos PLC funcionen con cualquiera de las
    # dos versiones de la libreria.
    def _w(self, addr: int, valor: int):
        valor = int(valor) & 0xFFFF
        try:
            r = self.client.write_register(addr, valor, device_id=self.unit)
        except TypeError:
            r = self.client.write_register(addr, valor, slave=self.unit)
        if r is None:
            raise IOError(f"Sin respuesta escribiendo %R{addr - 2999} (Modbus {addr})")
        if hasattr(r, "isError") and r.isError():
            raise IOError(f"Error escribiendo %R{addr - 2999} (Modbus {addr}) = {valor}")

    def _r(self, addr: int) -> int:
        try:
            r = self.client.read_holding_registers(addr, count=1, device_id=self.unit)
        except TypeError:
            r = self.client.read_holding_registers(addr, count=1, slave=self.unit)
        if r is None:
            raise IOError(f"Sin respuesta leyendo %R{addr - 2999} (Modbus {addr})")
        if hasattr(r, "isError") and r.isError():
            raise IOError(f"Error leyendo %R{addr - 2999} (Modbus {addr})")
        return r.registers[0]

    def _pulso_valor_nuevo(self, addr: int) -> int:
        """Escribe en 'addr' un valor <>0 DISTINTO del que ya tenia.

        §5 y §6 no detectan un flanco 0->1, sino un CAMBIO DE VALOR:
            NewCfgTrigger := (NewCfgFlag <> 0) AND (NewCfgFlag <> NewCfgPrev)
        Escribir 0 y luego 1 solo dispara la primera vez, porque NewCfgPrev
        se queda en 1 y el segundo 1 ya no es un valor distinto."""
        try:
            actual = self._r(addr)
        except IOError:
            actual = 0
        nuevo = (int(actual) % INT_MAX) + 1     # 1..32767, siempre distinto
        self._w(addr, nuevo)
        return nuevo

    # -- helpers de traduccion --------------------------------------------
    @staticmethod
    def _direccion(valor):
        if valor is None:
            return None
        if isinstance(valor, int):
            if valor not in (DIR_PARO, DIR_DERECHA, DIR_IZQUIERDA):
                raise ValueError(
                    "DirCmd debe ser 0 (paro), 1 (derecha) o 2 (izquierda).")
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
            if valor not in range(0, 5):
                raise ValueError(f"Accion de sensor no valida: {valor} (0..4).")
            return valor
        clave = str(valor).lower()
        if clave in SENSOR_ACTIONS:
            return SENSOR_ACTIONS[clave]
        if clave in SENSOR_ACTIONS_OBSOLETAS:
            return SENSOR_ACTIONS_OBSOLETAS[clave]
        raise ValueError(
            f"Accion de sensor no valida: {valor}. Usa {sorted(SENSOR_ACTIONS)}.")

    @staticmethod
    def _entero(valor, low, high, etiqueta):
        v = int(valor)
        if v < low or v > high:
            raise ValueError(f"{etiqueta} debe estar entre {low} y {high} (recibido {v}).")
        return v

    # -- §8/§15  configuracion general ------------------------------------
    def configurar_banda(self, frecuencia_hz=None, direccion=None):
        """Escribe FreqRequest (%R4) y DirCmd (%R2). NO habilita la banda.

        La frecuencia va en Hz tal cual: §7 (estado 4) y §8 hacen
        VFD_FreqCalc := FreqRequest, sin escalado."""
        if frecuencia_hz is not None:
            self._w(ADDR_FREQ_REQUEST,
                    self._entero(frecuencia_hz, 0, INT_MAX, "La frecuencia"))
        d = self._direccion(direccion)
        if d is not None:
            self._w(ADDR_DIR_CMD, d)
        nombres = {DIR_PARO: "paro", DIR_DERECHA: "derecha", DIR_IZQUIERDA: "izquierda"}
        print("BANDA configurada"
              + (f" | frecuencia={frecuencia_hz} Hz" if frecuencia_hz is not None else "")
              + (f" | DirCmd={d} ({nombres[d]})" if d is not None else ""))

    # -- §10..§14  sensores S1 y S2 ---------------------------------------
    def configurar_sensor(self, n, accion=None, timer_preset=None,
                          count_preset=None, torreta_mask=None, habilitar=True):
        """Configura el bloque completo de S1 (%R20..%R24) o S2 (%R30..%R34).

        accion       : 'nada' | 'paro_presencia' | 'paro_temporizado' |
                       'paro_presencia_torreta' | 'paro_temporizado_torreta'
                       (o su codigo 0..4)
        timer_preset : segundos que la banda se detiene (acciones 2 y 4)
        count_preset : preset del contador; el conteo corre siempre que el
                       sensor este habilitado, sea cual sea la accion (§10)
        torreta_mask : bitmask 0..7 que se superpone durante el evento
                       (acciones 3 y 4)
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
              + (f" | accion={accion} (SN_Action={acc})" if acc is not None else "")
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
        """Pone a 0 el acumulado del sensor (%R25 / %R35).

        El Ladder maestro nuevo NO tiene registro CountReset: el acumulado es
        RETAIN y solo lo borra el paro fisico I3 (§3). Se escribe directamente
        el acumulador, que §10 unicamente incrementa en flanco de subida."""
        if n not in ADDR_SENSOR:
            raise ValueError(f"Sensor no valido: {n}.")
        self._w(ADDR_SENSOR[n]["count_accum"], 0)
        print(f"S{n}: contador reseteado")

    # -- §14  torreta ------------------------------------------------------
    def configurar_torreta(self, mask_run=None, mask_idle=None):
        """TorretaRun (%R40) y TorretaIdle (%R41). Bitmask 0..7 (b0=V,b1=A,b2=R).

        La torreta la gobierna el ladder; aqui solo se declara que se enciende
        con la banda corriendo y que se enciende con la banda detenida. Las
        mascaras de los sensores (acciones 3 y 4) tienen prioridad sobre estas."""
        if mask_run is not None:
            self._w(ADDR_TORRETA_RUN,
                    self._entero(mask_run, 0, MASK_MAX, "La mascara de torreta en marcha"))
        if mask_idle is not None:
            self._w(ADDR_TORRETA_IDLE,
                    self._entero(mask_idle, 0, MASK_MAX, "La mascara de torreta detenida"))
        print("Torreta configurada"
              + (f" | run={mask_run}" if mask_run is not None else "")
              + (f" | idle={mask_idle}" if mask_idle is not None else ""))

    # -- §5/§6/§7  secuencia de inicializacion del VFD ---------------------
    def aplicar_nueva_config(self):
        """Cambia NewCfgFlag (%R5): dispara la secuencia init del VFD
        (§7: VFD_ResetReg 0 -> 2 -> 2 s -> 0 -> carga FreqRequest -> CfgReady=1).

        SIEMPRE debe llamarse DESPUES de escribir la configuracion: hasta que
        CfgReady sea TRUE, §15 mantiene VFD_Control en paro."""
        v = self._pulso_valor_nuevo(ADDR_NEW_CFG_FLAG)
        print(f"Nueva configuracion aplicada (NewCfgFlag={v}; secuencia init lanzada)")

    def reset_vfd(self):
        """Cambia ResetCmd (%R6): reset manual del VFD (§6 -> InitSeqState=1)."""
        v = self._pulso_valor_nuevo(ADDR_RESET_CMD)
        print(f"Reset del VFD solicitado (ResetCmd={v})")

    # -- §15/§16  marcha y paro -------------------------------------------
    def parar(self):
        """Paro por Modbus: DirCmd (%R2) = 0.

        Es la UNICA via de paro que le queda al backend en el Ladder maestro
        nuevo: con DirCmd = 0, §15 deja tmpDir = 1 y BandRunning = FALSE, o
        sea VFD_Control = 1 (paro del variador)."""
        self._w(ADDR_DIR_CMD, DIR_PARO)
        print("BANDA DETENIDA (DirCmd=0 -> VFD_Control=1)")

    def habilitar(self, on=True, direccion=None):
        """Arranca (on=True) o detiene (on=False) el movimiento.

        AVISO IMPORTANTE del Ladder maestro nuevo: BandEnable ya NO es un
        registro Modbus. Es un BOOL que solo enciende el boton fisico I1 (§4)
        y borran I3 / GenStop (§3). Desde aqui lo unico que se puede hacer es
        dar el sentido de giro (DirCmd) y comprobar en %R1 si el operador ya
        habilito la banda; si no lo ha hecho, se avisa en vez de dejar que el
        comando se pierda en silencio."""
        if not on:
            self.parar()
            return
        d = self._direccion(direccion) if direccion is not None else None
        if d is not None:
            self._w(ADDR_DIR_CMD, d)
        print("BANDA lista para marcha" + (f" (DirCmd={d})" if d is not None else ""))
        if not self.band_enable():
            print("AVISO: BandEnable esta en 0 (%R1). El Ladder maestro solo lo "
                  "enciende con el boton fisico I1: pulsalo (y suelta I3) para "
                  "que la banda arranque con esta configuracion.")

    def band_enable(self) -> bool:
        """True si BandEnable esta latcheado (%R1 = BandEnable_Reg, §16)."""
        return self._r(ADDR_BAND_ENABLE_REG) == 1

    def config_lista(self) -> bool:
        """True si la secuencia init termino y CfgReady esta activo (%R7)."""
        return self._r(ADDR_CFG_READY_REG) == 1

    # -- lectura de estado -------------------------------------------------
    def leer_estado(self) -> dict:
        """Lee los registros RO del ladder (§16 y acumulados de §10..§13)."""
        status = self._r(ADDR_BAND_STATUS)
        seq = self._r(ADDR_INIT_SEQ_STATE)
        return {
            "band_status": status,
            "estado": BAND_STATUS.get(status, str(status)),
            "running": status in (1, 2),
            "direccion": {1: "derecha", 2: "izquierda"}.get(status),
            "band_enable": self._r(ADDR_BAND_ENABLE_REG) == 1,
            "cfg_ready": self._r(ADDR_CFG_READY_REG) == 1,
            "dir_cmd": self._r(ADDR_DIR_CMD),
            "freq_request_hz": self._r(ADDR_FREQ_REQUEST),
            "init_seq_state": seq,
            "init_seq": INIT_SEQ_ESTADOS.get(seq, str(seq)),
            "init_timer_s": self._r(ADDR_INIT_TIMER_ACCUM),
            "s1_count": self._r(ADDR_S1_COUNT_ACCUM),
            "s1_timer_s": self._r(ADDR_S1_TIMER_ACCUM),
            "s2_count": self._r(ADDR_S2_COUNT_ACCUM),
            "s2_timer_s": self._r(ADDR_S2_TIMER_ACCUM),
            "vfd_control": self._r(ADDR_VFD_CONTROL),
            # Consigna cruda tal como la ve el variador (escala x100) y su
            # equivalente en Hz, para no depender de como escale el ladder.
            "vfd_freq_raw": self._r(ADDR_VFD_FREQ_CALC),
            "vfd_freq_hz": self._r(ADDR_VFD_FREQ_CALC) / 100.0,
            "vfd_reset": self._r(ADDR_VFD_RESET),
            # %R502 SIEMPRE viene x100 del variador; %R8 es lo que el ladder
            # decida mostrar en §9.
            "vfd_speed_hz": self._r(ADDR_VFD_SPEED_RAW) / 100.0,
            "vfd_speed_disp": self._r(ADDR_VFD_SPEED_DISP),
        }

    # -- verificacion post-carga ------------------------------------------
    def verificar_vfd(self) -> list:
        """Comprueba que la consigna de frecuencia llego ESCALADA al variador.

        El variador lee %R504 en centesimas de Hz, asi que el ladder debe
        entregar FreqRequest * 100. Si el ST hace la asignacion sin escalar,
        el variador recibe una frecuencia 100 veces menor (30 Hz -> 0.30 Hz)
        y la banda no llega a moverse, sin que nada falle de forma visible.

        Esto NO se puede corregir desde aqui: §8 reescribe %R504 en cada scan,
        asi que cualquier valor que mande el backend dura menos de un scan. Se
        detecta y se avisa para que el arreglo se haga en el ST."""
        avisos = []
        hz = self._r(ADDR_FREQ_REQUEST)
        enviado = self._r(ADDR_VFD_FREQ_CALC)
        if hz <= 0:
            return avisos
        if enviado == hz * 100:
            return avisos                      # escalado correcto
        if enviado == hz:
            avisos.append(
                f"El Ladder maestro esta entregando la frecuencia SIN escalar: "
                f"%R504 = {enviado} en vez de {hz * 100}. El variador lo lee "
                f"como {enviado / 100:.2f} Hz y la banda no se movera. "
                f"Corrige en el ST (§7 estado 4 y §8): "
                f"VFD_FreqCalc := FreqRequest * 100 ;")
        else:
            avisos.append(
                f"La consigna del variador no cuadra con la frecuencia pedida: "
                f"%R4 = {hz} Hz pero %R504 = {enviado} (se esperaba {hz * 100}). "
                f"El variador la leera como {enviado / 100:.2f} Hz.")
        return avisos


# ---------------------------------------------------------------------------
# VALIDADOR DEL BLOQUE "band"
# ---------------------------------------------------------------------------
# Contrato del JSON (identico al que ya dibuja el frontend, mas campos
# ADITIVOS opcionales que el Ladder maestro nuevo hizo posibles):
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
#       "s1_action": "nada"|"paro_presencia"|"paro_temporizado"|
#                    "paro_presencia_torreta"|"paro_temporizado_torreta",
#       "s2_action": ...,
#       "count_s1": 0..32767 | null,       # preset del contador de S1 (%R23)
#       "count_s2": 0..32767 | null,
#       "torreta_s1": 0..7 | null,         # mascara durante el evento de S1
#       "torreta_s2": 0..7 | null,
#       "torreta_run": 0..7 | null,        # mascara con la banda en marcha
#       "torreta_idle": 0..7 | null        # mascara con la banda detenida
#     }
#   }
#
# retrigger_s1_s / retrigger_s2_s se ACEPTAN por compatibilidad con el
# frontend actual, pero NO se escriben: el Ladder maestro de la banda no
# tiene registro de anti-retrigger (lo resuelve con flancos SN_Rising).

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
            if acc not in range(0, 5):
                errores.append(f"band.s{n}_action={acc} debe estar entre 0 y 4.")
        elif (str(acc).lower() not in SENSOR_ACTIONS
              and str(acc).lower() not in SENSOR_ACTIONS_OBSOLETAS):
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
            "El Ladder maestro de la banda no tiene registro de anti-retrigger "
            "(lo resuelve por flanco de los sensores): se ignora "
            f"{', '.join('band.' + c for c in pedidos)} al cargar al PLC.")

    for n in (1, 2):
        acc = band.get(f"s{n}_action")
        clave = str(acc).lower() if acc is not None else ""
        if clave == "paro_enclavado":
            avisos.append(
                f"band.s{n}_action='paro_enclavado' ya no existe en el Ladder "
                f"maestro nuevo: se carga como 'paro_temporizado' (S{n}_Action=2), "
                f"asi que la banda continua sola al vencer el tiempo.")
        elif clave in ("contar", "contar_y_parar"):
            avisos.append(
                f"band.s{n}_action='{clave}' ya no es una accion del ladder: "
                f"cualquier sensor habilitado cuenta sus flancos en "
                f"S{n}_CountAccum. Se carga como S{n}_Action=0 (el conteo si "
                f"funciona, pero el ladder nuevo no detiene la banda al "
                f"alcanzar el preset).")

    if band.get("enable", True) is not False:
        avisos.append(
            "El Ladder maestro nuevo no tiene registro BandEnable: la banda "
            "solo queda habilitada con el boton fisico I1. El backend deja la "
            "configuracion cargada y el sentido de giro escrito; el arranque "
            "final lo da I1.")

    return avisos


# ---------------------------------------------------------------------------
# PLAN Y APLICACION
# ---------------------------------------------------------------------------
def _accion_de_sensor(band, n):
    """Deduce (accion, timer_preset, count_preset) para S1 o S2 desde el JSON.

    'wait_sN_s' es el atajo historico del frontend: significa 'paro
    temporizado de N segundos', que en el ladder NUEVO es Action = 2."""
    accion = band.get(f"s{n}_action")
    espera = band.get(f"wait_s{n}_s")
    conteo = band.get(f"count_s{n}")

    if accion is None:
        if espera is not None:
            accion = "paro_temporizado"
        elif conteo is not None:
            # El conteo ya no necesita accion propia: basta habilitar el
            # sensor con su preset (§10 cuenta siempre que Enable <> 0).
            accion = "nada"
        else:
            return None, None, None      # sensor no mencionado: no se toca

    return accion, espera, conteo


def plan_config(cfg) -> list:
    """Traduce el engine_config de banda a (metodo, args, kwargs) SIN Modbus.

    Orden impuesto por el Ladder maestro nuevo:
      1) parar (DirCmd = 0)  -> §15 deja VFD_Control = 1 antes de reconfigurar
      2) configuracion (frecuencia, sensores, torreta)
      3) NewCfgFlag          -> §7: reset del VFD y carga de FreqRequest; deja
                                CfgReady = 1
      4) DirCmd = 1 / 2      -> sentido de giro; §15 arranca en cuanto CfgReady
                                y BandEnable (boton I1) esten activos

    DirCmd se escribe DESPUES de NewCfgFlag a proposito: mientras corre la
    secuencia init, CfgReady esta en 0 y la banda no se mueve, asi que no hay
    ningun instante en que gire con la configuracion vieja.
    """
    plan = []
    band = cfg.get("band") or {}
    arrancar = band.get("enable", True) is not False

    # 1) Estado seguro: nada se reconfigura con la banda en marcha.
    plan.append(("parar", (), {}))

    # 2) Configuracion (la direccion va en el paso 4)
    if band.get("freq_hz") is not None:
        plan.append(("configurar_banda", (), {"frecuencia_hz": band.get("freq_hz")}))

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
        if conteo is not None:
            plan.append(("reset_contador", (n,), {}))

    if band.get("torreta_run") is not None or band.get("torreta_idle") is not None:
        plan.append(("configurar_torreta", (), {
            "mask_run": band.get("torreta_run"),
            "mask_idle": band.get("torreta_idle"),
        }))

    # 3) Secuencia de inicializacion del VFD (obligatoria tras reconfigurar)
    plan.append(("aplicar_nueva_config", (), {}))

    # 4) Sentido de giro (o paro si el programa pide dejarla parada)
    if arrancar:
        plan.append(("habilitar", (True,), {
            "direccion": band.get("direction") or "derecha"}))
    else:
        plan.append(("parar", (), {}))

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
