"""
============================================================================
SIMULADOR DEL PROGRAMA MAESTRO v7 (ejecuta el TEXTO ST real, sin PLC)
============================================================================

SimV7 corre cscape/ST_banda_v7_lavo.txt scan a scan con banda_st_interprete
(el mismo traductor ST -> Python que ya probaba el v6). No reimplementa la
logica: si el ST cambia, el simulador cambia con el.

ModbusSimulado es un cliente pymodbus FALSO conectado a los registros del
simulador: plc_banda.BandaPLC lo usa exactamente como al PLC real (mismas
direcciones Modbus = %R + 2999). Asi la carga transaccional (BEGIN ->
escritura -> COMMIT -> confirmacion) se prueba de punta a punta contra el ST.

Limites (no sustituye a Cscape ni al PLC):
  - no comprueba tipos, tabla de tags ni el limite de memoria de Cscape;
  - el tiempo es simulado (scans de 10 ms, %S4/%S5 como ondas cuadradas);
  - el VFD es un modelo: rampa de 20 Hz/s que sigue a VFD_Control.
============================================================================
"""

import os

import banda_st_interprete as si
import lavo_motor as lm
import plc_banda as pb

SCAN_S = 0.01
ST_V7 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cscape", "ST_banda_v7_lavo.txt")

# Entradas en logica de USUARIO -> variable fisica del ST y si se invierte
# (NA: directo; NC e I4/I5 activos en bajo: invertidos), como en el tablero.
_FISICA = {"I1": ("PHIN1", False), "I2": ("PHIN2", True), "I3": ("PHIN3", True),
           "I4": ("PHIN4", True), "I5": ("PHIN5", True)}
_ALIAS = {"S1": "I4", "S2": "I5"}


class SimV7:
    def __init__(self, ruta=ST_V7, rampa_hz_s=20.0):
        self._scan, arreglos, _ = si._programa(ruta)
        V = si._Vars()
        for a in set(arreglos) | {"LCMD", "LSTG", "LSTA", "LACT", "LPEN", "LSIG", "LI", "LB", "LD"}:
            V[a] = si._Arr()
        self.V = V
        self.u = {k: False for k in _FISICA}
        self._ley = si.leyenda(ruta)
        self.rampa = rampa_hz_s
        self.vfd_hz = 0.0
        self.n = 0
        self.fst = True
        self.modbus = ModbusSimulado(self)

    # ------------------------------------------------------------- entradas
    def poner(self, **entradas):
        for k, v in entradas.items():
            self.u[_ALIAS.get(k, k)] = bool(v)

    def correr(self, segundos):
        for _ in range(max(1, int(round(segundos / SCAN_S)))):
            self.scan()

    def pulsar(self, nombre, segundos=0.3):
        self.poner(**{nombre: True})
        self.correr(segundos)
        self.poner(**{nombre: False})

    # ------------------------------------------------------------- lectura
    def q(self, nombre) -> bool:
        return bool(self.V[f"PHQ{int(lm.salida_canonica(nombre)[1:])}"])

    def salidas(self) -> set:
        return {f"Q{i}" for i in range(3, 10) if self.V[f"PHQ{i}"]}

    def motor(self) -> str:
        return {1: "paro", 18: "dir1", 34: "dir2"}.get(self.V["VFD_CONTROL"], str(self.V["VFD_CONTROL"]))

    def var(self, nombre, i=0):
        n = nombre.upper()
        if n in self._ley:
            arr, base = self._ley[n]
            return self.V[arr][base + i]
        v = self.V[n]
        return v[i] if isinstance(v, dict) else v

    def estado(self) -> dict:
        return lm.estado_desde_status([self.V["LSTA"][i] for i in range(lm.STATUS_LEN)])

    # ------------------------------------------------------------- un scan
    def scan(self):
        V = self.V
        V["FST_SCN"] = self.fst
        V["SYS100MS"] = (self.n * SCAN_S) % 0.1 >= 0.05
        V["SYS1SEC"] = (self.n * SCAN_S) % 1.0 >= 0.5
        for k, (var, inv) in _FISICA.items():
            V[var] = (not self.u[k]) if inv else self.u[k]
        V["VFD_SPEEDRAW"] = int(abs(self.vfd_hz) * 100)
        self._scan(V)
        self.fst = False
        self.n += 1
        # El VFD sigue a VFD_Control (18 dir 1, 34 dir 2, otro = paro) con la
        # consigna VFD_FreqCalc (Hz x 100) y una rampa constante.
        objetivo = 0.0
        if V["VFD_CONTROL"] in (18, 34) and not V["VFD_RESETREG"]:
            objetivo = V["VFD_FREQCALC"] / 100 * (1 if V["VFD_CONTROL"] == 18 else -1)
        paso = self.rampa * SCAN_S
        if self.vfd_hz < objetivo:
            self.vfd_hz = min(objetivo, self.vfd_hz + paso)
        elif self.vfd_hz > objetivo:
            self.vfd_hz = max(objetivo, self.vfd_hz - paso)

    # ------------------------------------------- carga directa (sin Modbus)
    def cargar(self, cfg_o_band, esperar=3.0):
        """Carga como lo hace el backend, pero escribiendo las variables.
        Devuelve (codigo de error, campo) del COMMIT."""
        band = cfg_o_band.get("band", cfg_o_band)
        bloque = lm.compilar_motor(band)
        plc = pb.BandaPLC(ip="127.0.0.1")
        plc.client = self.modbus
        plc._esperar = self.correr
        plc.cargar_motor_v7(bloque)
        if esperar:
            self.correr(esperar)
        return self.V["LSTA"][lm.S_ERR], self.V["LSTA"][lm.S_ERRFIELD]


class _Resp:
    def __init__(self, registers=None, error=False):
        self.registers = registers or []
        self._error = error

    def isError(self):
        return self._error


class ModbusSimulado:
    """Cliente pymodbus falso: direccion Modbus -> variable del ST.

    %R1000..%R1799 -> LCMD/LSTG/LSTA; %R1..%R127 -> las variables v5 de
    plc_banda.BAND_REGISTERS por su simbolo. Cada peticion avanza el PLC
    'scans_por_peticion' scans (el PLC sigue corriendo mientras se escribe).
    'fallar_en' simula una interrupcion: la peticion numero N lanza un error."""

    def __init__(self, sim, scans_por_peticion=1):
        self.sim = sim
        self.scans = scans_por_peticion
        self.peticiones = 0
        self.fallar_en = None
        self.log = []
        self._simbolos = {pb.R(n): sym.upper() for n, _acc, sym in pb.BAND_REGISTERS.values()}

    def _ubicar(self, addr):
        r = addr - 2999
        for base, n, arr in ((lm.CMD_BASE, lm.CMD_LEN, "LCMD"), (lm.STAGE_BASE, lm.CFG_LEN, "LSTG"),
                             (lm.STATUS_BASE, lm.STATUS_LEN, "LSTA"), (lm.ACT_BASE, lm.CFG_LEN, "LACT"),
                             (lm.PEN_BASE, lm.CFG_LEN, "LPEN"), (lm.LI_BASE, lm.LI_LEN, "LI")):
            if base <= r < base + n:
                return arr, r - base
        if addr in self._simbolos:
            return self._simbolos[addr], None
        return f"_R{r}", None

    def _leer(self, addr):
        arr, i = self._ubicar(addr)
        v = self.sim.V[arr][i] if i is not None else self.sim.V[arr]
        return int(v) & 0xFFFF

    def _escribir(self, addr, valor):
        arr, i = self._ubicar(addr)
        v = valor - 0x10000 if valor >= 0x8000 else valor
        if i is not None:
            self.sim.V[arr][i] = v
        else:
            self.sim.V[arr] = v

    def _tick(self):
        self.peticiones += 1
        if self.fallar_en is not None and self.peticiones >= self.fallar_en:
            raise ConnectionError("Modbus: conexion interrumpida (simulada)")
        for _ in range(self.scans):
            self.sim.scan()

    def write_register(self, addr, valor, device_id=None):
        self._tick()
        self._escribir(addr, int(valor) & 0xFFFF)
        self.log.append(("w", addr - 2999, valor))
        return _Resp()

    def write_registers(self, addr, valores, device_id=None):
        self._tick()
        for i, v in enumerate(valores):
            self._escribir(addr + i, int(v) & 0xFFFF)
        self.log.append(("ws", addr - 2999, len(valores)))
        return _Resp()

    def read_holding_registers(self, addr, count=1, device_id=None):
        self._tick()
        return _Resp([self._leer(addr + i) for i in range(count)])

    def close(self):
        pass
