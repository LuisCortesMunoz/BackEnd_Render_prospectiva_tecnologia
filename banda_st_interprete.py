"""
============================================================================
INTERPRETE DEL PROGRAMA MAESTRO ST DE LA BANDA (para pruebas sin PLC)
============================================================================

Traduce a Python el subconjunto de Texto Estructurado IEC que usa el programa
maestro (asignaciones, IF/ELSIF/ELSE, FOR, CASE, arreglos, AND/OR/NOT, MOD,
division entera, INT#n) y lo ejecuta scan a scan contra un entorno simulado.

No sustituye a Cscape (no comprueba tipos ni la tabla de tags), pero ejecuta
el TEXTO REAL de cscape/ST_banda_v6_motor_configurable.txt: si el ST y el
simulador de Python (banda_motor_sim.py) dejaran de comportarse igual, las
pruebas lo detectan.

SimST expone la misma API que banda_motor_sim.SimBanda.
============================================================================
"""

import os
import re

import banda_motor as bm

SCAN_S = 0.01
ST_V6 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cscape",
                     "ST_banda_v6_motor_configurable.txt")

_TOKEN = re.compile(r"""
    (?P<num>D?INT\#-?\d+|\d+)
  | (?P<op>:=|<>|<=|>=|[-+*/=<>();:\[\],])
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
""", re.X)
_KW_EXPR = {"AND": " and ", "OR": " or ", "NOT": " not ", "MOD": " % ",
            "TRUE": "True", "FALSE": "False",
            # conversiones de tipo de Cscape (los indices de arreglo son DINT)
            "ANY_TO_DINT": "int", "ANY_TO_INT": "int"}


class _Arr(dict):
    def __missing__(self, k):
        return 0


class _Vars(dict):
    def __missing__(self, k):
        return 0


def _tokens(texto):
    texto = re.sub(r"\(\*.*?\*\)", " ", texto, flags=re.S)
    out = []
    for m in _TOKEN.finditer(texto):
        if m.group("num"):
            out.append(("num", m.group("num").replace("DINT#", "").replace("INT#", "")))
        elif m.group("op"):
            out.append(("op", m.group("op")))
        else:
            out.append(("id", m.group("id").upper()))
    return out


class _Traductor:
    def __init__(self, toks, arreglos):
        self.t = toks
        self.i = 0
        self.arr = arreglos
        self.lineas = []

    def ver(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def tomar(self, esperado=None):
        tok = self.t[self.i]
        if esperado is not None and tok[1] != esperado:
            raise SyntaxError(f"se esperaba {esperado} y llego {tok} (token {self.i})")
        self.i += 1
        return tok

    def expr(self, fin):
        """Traduce tokens hasta uno de 'fin' (no lo consume)."""
        partes, prof = [], 0
        while True:
            k, v = self.ver()
            if v is None:
                raise SyntaxError("expresion sin terminar")
            if prof == 0 and v in fin:
                break
            self.i += 1
            if v in ("(", "["):
                prof += 1
            elif v in (")", "]"):
                prof -= 1
            if k == "id":
                if v in _KW_EXPR:
                    partes.append(_KW_EXPR[v])
                else:
                    partes.append(f"V['{v}']")
            elif k == "num":
                partes.append(v)
            elif v == "=":
                partes.append(" == ")
            elif v == "<>":
                partes.append(" != ")
            elif v == "/":
                partes.append(" // ")
            else:
                partes.append(f" {v} ")
        return "".join(partes)

    def emitir(self, nivel, txt):
        self.lineas.append("    " * nivel + txt)

    def bloque(self, nivel, fin):
        antes = len(self.lineas)
        while self.ver()[1] not in fin:
            self.sentencia(nivel)
        if len(self.lineas) == antes:
            self.emitir(nivel, "pass")

    def sentencia(self, nivel):
        k, v = self.ver()
        if v == ";":
            self.tomar()
            return
        if v == "IF":
            self.tomar()
            self.emitir(nivel, f"if {self.expr({'THEN'})}:")
            self.tomar("THEN")
            self.bloque(nivel + 1, {"ELSIF", "ELSE", "END_IF"})
            while self.ver()[1] == "ELSIF":
                self.tomar()
                self.emitir(nivel, f"elif {self.expr({'THEN'})}:")
                self.tomar("THEN")
                self.bloque(nivel + 1, {"ELSIF", "ELSE", "END_IF"})
            if self.ver()[1] == "ELSE":
                self.tomar()
                self.emitir(nivel, "else:")
                self.bloque(nivel + 1, {"END_IF"})
            self.tomar("END_IF")
            return
        if v == "FOR":
            self.tomar()
            var = self.tomar()[1]
            self.tomar(":=")
            a = self.expr({"TO"})
            self.tomar("TO")
            b = self.expr({"DO"})
            self.tomar("DO")
            self.emitir(nivel, f"for _{var} in range({a}, ({b}) + 1):")
            self.emitir(nivel + 1, f"V['{var}'] = _{var}")
            self.bloque(nivel + 1, {"END_FOR"})
            self.tomar("END_FOR")
            return
        if v == "CASE":
            self.tomar()
            sel = self.expr({"OF"})
            self.tomar("OF")
            self.emitir(nivel, f"_sel = {sel}")
            primero = True
            while self.ver()[1] not in ("ELSE", "END_CASE"):
                etiquetas = [self.tomar()[1]]
                while self.ver()[1] == ",":
                    self.tomar()
                    etiquetas.append(self.tomar()[1])
                self.tomar(":")
                cond = " or ".join(f"_sel == {e}" for e in etiquetas)
                self.emitir(nivel, f"{'if' if primero else 'elif'} {cond}:")
                primero = False
                antes = len(self.lineas)
                while not (self.ver()[0] == "num" and self.ver(1)[1] in (":", ",")) \
                        and self.ver()[1] not in ("ELSE", "END_CASE"):
                    self.sentencia(nivel + 1)
                if len(self.lineas) == antes:
                    self.emitir(nivel + 1, "pass")
            if self.ver()[1] == "ELSE":
                self.tomar()
                self.emitir(nivel, "else:" if not primero else "if True:")
                self.bloque(nivel + 1, {"END_CASE"})
            self.tomar("END_CASE")
            return
        # asignacion: nombre [ '[' expr ']' ] := expr ;
        nombre = self.tomar()[1]
        if self.ver()[1] == "[":
            self.tomar("[")
            idx = self.expr({"]"})
            self.tomar("]")
            self.arr.add(nombre)
            destino = f"V['{nombre}'][{idx}]"
        else:
            destino = f"V['{nombre}']"
        self.tomar(":=")
        self.emitir(nivel, f"{destino} = {self.expr({';'})}")
        self.tomar(";")


def compilar_st(texto):
    """Texto ST -> (funcion scan(V), conjunto de arreglos)."""
    arreglos = set()
    tr = _Traductor(_tokens(texto), arreglos)
    while tr.i < len(tr.t):
        tr.sentencia(1)
    # Tambien son arreglos los que solo se leen con indice.
    toks = tr.t
    for j in range(len(toks) - 1):
        if toks[j][0] == "id" and toks[j + 1][1] == "[":
            arreglos.add(toks[j][1])
    fuente = "def scan(V):\n" + "\n".join(tr.lineas) + "\n"
    ns = {}
    exec(compile(fuente, "<st>", "exec"), ns)
    return ns["scan"], arreglos, fuente


_CACHE = {}


def leyenda(ruta=ST_V6):
    """Posiciones de EI / EB (v6) o LI / LB (v7) del ST empaquetado:
    {nombre: (arreglo, base)}.
    Las lee de la leyenda que encabeza el archivo ("EI[3]=MotorState",
    "EB[48..111]=Sig")."""
    with open(ruta, encoding="utf-8") as f:
        cab = f.read(20000)
    out = {}
    for arr, a, _b, nombre in re.findall(r"(EI|EB|LI|LB)\[(\d+)(?:\.\.(\d+))?\]=([A-Za-z_][A-Za-z0-9_]*)", cab):
        out[nombre.upper()] = (arr, int(a))
    return out


def _programa(ruta=ST_V6):
    if ruta not in _CACHE:
        with open(ruta, encoding="utf-8") as f:
            _CACHE[ruta] = compilar_st(f.read())
    return _CACHE[ruta]


class SimST:
    """Ejecuta el ST REAL de la banda con el mismo entorno que SimBanda.

    La configuracion se carga como lo hace plc_banda._plan_motor: registros
    v5 en 0, EC en %R200.., DirCmd/FreqRequest, ResetCmd y NewCfgFlag."""

    def __init__(self, ec, freq_hz=30, dir_cmd=1, stop_mode=0, soft_stop=False,
                 rampa_hz_s=20.0, ruta=ST_V6):
        self._scan, arreglos, _ = _programa(ruta)
        V = _Vars()
        for a in set(arreglos) | {"EC", "ES"}:
            V[a] = _Arr()
        self.V = V
        for i, v in enumerate(ec):
            V["EC"][i] = v
        V["DIRCMD"] = dir_cmd
        V["FREQREQUEST"] = freq_hz if dir_cmd else 0
        V["STOPMODE"] = stop_mode
        V["SOFTSTOPCMD"] = 1 if soft_stop else 0
        V["R_DIRCMD"] = V["DIRCMD"]
        V["R_FREQREQUEST"] = V["FREQREQUEST"]
        V["R_STOPMODE"] = stop_mode
        self.u = {"I1": False, "I2": False, "I3": False, "S1": False, "S2": False}
        self._ley = leyenda(ruta)
        self.rampa = rampa_hz_s
        self.vfd_hz = 0.0
        self.n = 0
        self.t = 0.0
        self.fst = True
        self._cfg_pendiente = True

    # --------------------------------------------------------------- API
    def poner(self, **entradas):
        for k, v in entradas.items():
            self.u[k] = bool(v)

    def correr(self, segundos):
        for _ in range(int(round(segundos / SCAN_S))):
            self.scan()

    def pulsar(self, nombre, segundos=0.3):
        self.poner(**{nombre: True})
        self.correr(segundos)
        self.poner(**{nombre: False})

    def luces(self):
        V = self.V
        return {c for c, q in zip(bm.COLORES, ("PHQ3", "PHQ4", "PHQ5")) if V[q]}

    def motor(self):
        return {1: "paro", 18: "dir1", 34: "dir2"}.get(self.V["VFD_CONTROL"], str(self.V["VFD_CONTROL"]))

    def pluma(self, n):
        V = self.V
        sube, baja = ("PHQ8", "PHQ9") if n == 1 else ("PHQ6", "PHQ7")
        return "sube" if V[sube] else "baja" if V[baja] else "stop"

    def var(self, nombre, i=0):
        """Variable logica del motor (aunque el ST la tenga dentro de EI/EB)."""
        n = nombre.upper()
        if n in self._ley:
            arr, base = self._ley[n]
            return self.V[arr][base + i]
        v = self.V[n]
        return v[i] if isinstance(v, dict) else v

    # atributos que leen las pruebas (mismos nombres que SimBanda)
    CfgReady = property(lambda s: bool(s.V["CFGREADY"]))
    MotorState = property(lambda s: s.var("MotorState"))
    DirActual = property(lambda s: s.var("DirActual"))
    DirTarget = property(lambda s: s.var("DirTarget"))
    RunLatch = property(lambda s: bool(s.var("RunLatch")))
    SeqActive = property(lambda s: bool(s.var("ESeqActive")))
    SeqStep = property(lambda s: s.var("ESeqStep"))
    PlumaFault = property(lambda s: s.var("PlumaFault"))
    CAcc = property(lambda s: [s.var("CAcc", i) for i in range(8)])
    ES = property(lambda s: [s.V["ES"][i] for i in range(bm.STATUS_LEN)])

    # ------------------------------------------------------------ un scan
    def scan(self):
        V = self.V
        V["FST_SCN"] = self.fst
        V["SYS100MS"] = (self.n * SCAN_S) % 0.1 >= 0.05
        V["SYS1SEC"] = (self.n * SCAN_S) % 1.0 >= 0.5
        V["PHIN1"] = self.u["I1"]
        V["PHIN2"] = not self.u["I2"]
        V["PHIN3"] = not self.u["I3"]
        V["PHIN4"] = not self.u["S1"]
        V["PHIN5"] = not self.u["S2"]
        V["VFD_SPEEDRAW"] = int(abs(self.vfd_hz) * 100)
        if self._cfg_pendiente and not self.fst:
            # trigger_vfd_reset + trigger_new_config del backend
            V["RESETCMD"] = V["RESETCMD"] + 1
            V["NEWCFGFLAG"] = V["NEWCFGFLAG"] + 1
            self._cfg_pendiente = False
        self._scan(V)
        self.fst = False
        self.n += 1
        self.t = self.n * SCAN_S
        objetivo = {18: V["FREQREQUEST"], 34: -V["FREQREQUEST"]}.get(V["VFD_CONTROL"], 0)
        paso = self.rampa * SCAN_S
        if self.vfd_hz < objetivo:
            self.vfd_hz = min(objetivo, self.vfd_hz + paso)
        elif self.vfd_hz > objetivo:
            self.vfd_hz = max(objetivo, self.vfd_hz - paso)


def simular(cfg_o_band, **kw):
    band = cfg_o_band.get("band", cfg_o_band)
    ec = bm.compilar_motor(band)
    marcha = band.get("enable") is not False
    d = 2 if str(band.get("direction")).lower() in ("izquierda", "2") else 1
    sim = SimST(ec, freq_hz=(band.get("freq_hz") or 30) if marcha else 0,
                dir_cmd=d if marcha else 0, **kw)
    sim.correr(3.0)
    return sim
