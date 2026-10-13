"""
============================================================================
SIMULADOR DEL MOTOR CONFIGURABLE DE LA BANDA  (espejo de ST v6)
============================================================================

Ejecuta, scan por scan, la MISMA logica que las secciones §E del programa
maestro (Archivos Cscape/ST_banda_v6_motor_configurable.txt): mismos nombres,
mismo orden, mismas prioridades. Sirve para probar configuraciones sin PLC y
para que las pruebas automaticas cubran las secuencias que pide el usuario.

Si cambias el ST, cambia aqui lo mismo (y al reves).

Modelo de entorno (lo que el PLC lee del mundo):
  - scan de 10 ms, reloj T_100MS (%S4) y T_SEC (%S5) como ondas cuadradas;
  - entradas en logica de USUARIO: I1/I2/I3 "presionado", S1/S2 "detecta"
    (la inversion NA/NC y activo-en-bajo la hace §2, igual que en el PLC);
  - VFD con rampa de 20 Hz/s que sigue a VFD_Control (1 paro, 18 dir 1, 34 dir 2)
    y reporta VFD_SpeedRaw = |Hz| x 100;
  - la preparacion del VFD (§8) tras una configuracion nueva: 2 s de reset.
============================================================================
"""

import banda_motor as bm

SCAN_S = 0.01


class SimBanda:
    def __init__(self, ec, freq_hz=30, dir_cmd=1, stop_mode=0, soft_stop=False,
                 rampa_hz_s=20.0):
        self.EC = list(ec) + [0] * (bm.ENGINE_LEN - len(ec))
        self.ES = [0] * bm.STATUS_LEN
        self.FreqRequest = freq_hz if dir_cmd else 0
        self.DirCmd = dir_cmd
        self.StopMode = stop_mode
        self.SoftStopCmd = 1 if soft_stop else 0
        self.rampa = rampa_hz_s
        self.t = 0.0
        self.n = 0
        # entradas de usuario
        self.u = {"I1": False, "I2": False, "I3": False, "S1": False, "S2": False}
        # salidas fisicas
        self.PhQ = {q: False for q in ("Q3", "Q4", "Q5", "Q6", "Q7", "Q8", "Q9")}
        self.VFD_Control = 1
        self.vfd_hz = 0.0
        # estado compartido v5
        self.Prev1s = self.Prev100ms = False
        self.CfgReady = False
        self.InitSeqState = 1 if (self.FreqRequest > 0 and dir_cmd in (1, 2)) else 0
        self.InitTimerAccum = 0
        self.BandEnable = False
        self.BandRunning = False
        # estado del motor configurable
        n8 = range(bm.N_TIMER)
        self.Sig = [False] * 64
        self.DebFilt = [False] * 5
        self.DebAcc = [0] * 5
        self.CondPrev = [False] * bm.N_COND
        self.TAcc = [0 for _ in n8]
        self.TDn = [False for _ in n8]
        self.TRun = [False for _ in n8]
        self.TPrevIn = [False for _ in n8]
        self.CAcc = [0] * bm.N_COUNT
        self.CDn = [False] * bm.N_COUNT
        self.CPrevIn = [False] * bm.N_COUNT
        self.MBit = [False] * bm.N_MEM
        self.SeqActive = False
        self.SeqStep = 0
        self.SeqAcc = 0
        self.SeqStartPrev = False
        self.SeqTransPrev = False
        self.SeqBit = [False] * 12
        self.StepEntry = False
        self.RunLatch = False
        self.RunPrev = self.FwdPrev = self.RevPrev = self.TogPrev = False
        self.MotorState = 0
        self.DirActual = dir_cmd if dir_cmd in (1, 2) else 1
        self.DirTarget = self.DirActual
        self.BrakeAcc = 32767
        self.OutReq = [False] * 7
        self.PlPrev = [0, 0]
        self.PlAcc = [0, 0]
        self.PlTimeout = [False, False]
        self.PlCmd = [0, 0]
        self.PlumaFault = 0
        self.PlumaManual = [0, 0]      # Pluma1Cmd / Pluma2Cmd (%R60 / %R61)
        self.EngValid = True
        self.EngError = 0
        self.EngResetReq = True        # FST_SCN / NewCfgFlag
        self.log = []

    # ------------------------------------------------------------------ API
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

    def luces(self) -> set:
        return {c for c, q in zip(bm.COLORES, ("Q3", "Q4", "Q5")) if self.PhQ[q]}

    def motor(self) -> str:
        return {1: "paro", 18: "dir1", 34: "dir2"}.get(self.VFD_Control, str(self.VFD_Control))

    def pluma(self, n) -> str:
        sube, baja = ("Q8", "Q9") if n == 1 else ("Q6", "Q7")
        return "sube" if self.PhQ[sube] else "baja" if self.PhQ[baja] else "stop"

    # --------------------------------------------------------- helpers ST
    def _sig(self, sc) -> bool:
        """Resolucion de un codigo de senal (bloque repetido en el ST)."""
        sn = False
        if sc >= 100:
            sn = True
            sc = sc - 100
        sv = self.Sig[sc] if 0 <= sc <= 63 else False
        return (not sv) if sn else sv

    # ------------------------------------------------------------- un scan
    def scan(self):
        EC, Sig = self.EC, self.Sig
        # relojes del sistema (%S4 100 ms, %S5 1 s)
        Sys100ms = (self.n * SCAN_S) % 0.1 >= 0.05
        Sys1Sec = (self.n * SCAN_S) % 1.0 >= 0.5
        self.n += 1
        self.t = self.n * SCAN_S

        # §1
        Pulse1s = Sys1Sec and not self.Prev1s
        self.Prev1s = Sys1Sec
        Pulse100ms = Sys100ms and not self.Prev100ms
        self.Prev100ms = Sys100ms

        # §2 (fisico -> normalizado; aqui el usuario ya da el valor normalizado)
        BtnStart = self.u["I1"]
        BtnAux = self.u["I2"]
        BtnStop = self.u["I3"]
        S1_Det = self.u["S1"]
        S2_Det = self.u["S2"]

        # §3 (solo lo que afecta al motor configurable)
        EngineMode = EC[bm.O_MODE]
        MotionCfgPresent = self.DirCmd != 0 or self.FreqRequest != 0
        CfgValid = True
        if MotionCfgPresent and (self.DirCmd not in (1, 2) or not 0 < self.FreqRequest <= 327):
            CfgValid = False
        # §3b
        self.EngError = bm.error_registros(EC) if EngineMode == 1 else (
            0 if EngineMode in (0, 1) else 1)
        self.EngValid = self.EngError == 0
        if EngineMode == 1 and not self.EngValid:
            CfgValid = False

        # §4
        HardStopActive = BtnStop
        AuxStopActive = BtnAux and self.StopMode in (1, 3)
        SoftStopActive = self.SoftStopCmd != 0 and self.StopMode in (2, 3)
        SystemStopActive = HardStopActive
        GenStop = HardStopActive or AuxStopActive or SoftStopActive
        if GenStop:
            self.BandEnable = False
            self.BandRunning = False
            self.VFD_Control = 1

        # §8 preparacion del VFD
        if not CfgValid:
            self.CfgReady = False
            self.InitSeqState = 0
        elif not MotionCfgPresent:
            self.CfgReady = True
            self.InitSeqState = 0
        elif not GenStop:
            if self.InitSeqState == 1:
                self.InitTimerAccum = 0
                self.InitSeqState = 2
            elif self.InitSeqState == 2:
                self.InitTimerAccum = 0
                self.InitSeqState = 3
            elif self.InitSeqState == 3:
                if Pulse1s:
                    self.InitTimerAccum += 1
                if self.InitTimerAccum >= 2:
                    self.InitSeqState = 4
            elif self.InitSeqState == 4:
                self.CfgReady = True
                self.InitSeqState = 0

        if EngineMode == 1:
            self._motor(Pulse100ms, BtnStart, BtnAux, BtnStop, S1_Det, S2_Det,
                        MotionCfgPresent, CfgValid, GenStop, SystemStopActive)
        else:
            self.VFD_Control = 1
            self.BandRunning = False

        self.ES[bm.S_VERSION] = bm.ENGINE_VERSION
        self.ES[bm.S_VALID] = 1 if self.EngValid else 0
        self.ES[bm.S_ERROR] = self.EngError
        self._vfd()

    # ---------------------------------------------------- motor configurable
    def _motor(self, Pulse100ms, BtnStart, BtnAux, BtnStop, S1_Det, S2_Det,
               MotionCfgPresent, CfgValid, GenStop, SystemStopActive):
        EC, Sig = self.EC, self.Sig
        EngFirst = self.EngResetReq

        # §E1 reinicio del motor (FST_SCN / nueva configuracion / reset)
        if EngFirst:
            for k in range(bm.N_TIMER):
                self.TAcc[k] = 0; self.TDn[k] = False; self.TRun[k] = False
            for k in range(bm.N_COUNT):
                self.CAcc[k] = 0; self.CDn[k] = False
            for k in range(bm.N_MEM):
                self.MBit[k] = False
            for k in range(16, 64):
                Sig[k] = False
            self.SeqActive = False; self.SeqStep = 0; self.SeqAcc = 0
            self.RunLatch = False
            self.MotorState = 0
            self.DirActual = self.DirCmd if self.DirCmd in (1, 2) else 1
            self.DirTarget = self.DirActual
            self.BrakeAcc = 32767
            self.PlAcc = [0, 0]; self.PlTimeout = [False, False]; self.PlPrev = [0, 0]

        # §E2 entradas con filtro
        RawIn = [BtnStart, BtnAux, BtnStop, S1_Det, S2_Det]
        for ei in range(5):
            DebPre = EC[bm.O_DEB_BTN] if ei <= 2 else EC[bm.O_DEB_SENS]
            if DebPre <= 0 or EngFirst:
                self.DebFilt[ei] = RawIn[ei]; self.DebAcc[ei] = 0
            elif RawIn[ei] == self.DebFilt[ei]:
                self.DebAcc[ei] = 0
            else:
                if Pulse100ms:
                    self.DebAcc[ei] += 1
                if self.DebAcc[ei] >= DebPre:
                    self.DebFilt[ei] = RawIn[ei]; self.DebAcc[ei] = 0
        Sig[0] = False
        for ei in range(5):
            Sig[1 + ei] = self.DebFilt[ei]
        Sig[6] = self.MotorState == 1
        Sig[7] = self.MotorState != 1
        Sig[8] = self.DirActual == 1
        Sig[9] = self.DirActual == 2
        Sig[10] = self.MotorState == 2
        Sig[11] = self.CfgReady
        Sig[12] = self.vfd_speed_raw() < 50
        Sig[13] = EC[bm.O_APP1] != 0
        Sig[14] = EC[bm.O_APP2] != 0
        Sig[15] = True

        # §E3 condiciones
        for k in range(bm.N_COND):
            b = bm.O_COND + k * bm.COND_W
            op = EC[b]
            if op == 0:
                res = False
            else:
                res = op == 1
                used = False
                for j in range(1, 5):
                    sc = EC[b + j]
                    if sc != 0:
                        used = True
                        sv = self._sig(sc)
                        res = (res and sv) if op == 1 else (res or sv)
                if not used:
                    res = False
            edge = EC[b + 5]
            if edge == 1:
                out = res and not self.CondPrev[k] and not EngFirst
            elif edge == 2:
                out = (not res) and self.CondPrev[k] and not EngFirst
            else:
                out = res
            self.CondPrev[k] = res
            Sig[16 + k] = out

        # §E4 timers (decimas de segundo)
        for k in range(bm.N_TIMER):
            b = bm.O_TIMER + k * bm.TIMER_W
            typ, pre = EC[b], EC[b + 3]
            vin = self._sig(EC[b + 1])
            vrst = self._sig(EC[b + 2])
            rise = vin and not self.TPrevIn[k] and not EngFirst
            self.TPrevIn[k] = vin
            if vrst or typ == 0:
                self.TAcc[k] = 0; self.TDn[k] = False; self.TRun[k] = False
            elif typ == 1:                                  # TON
                if vin:
                    if Pulse100ms and self.TAcc[k] < pre:
                        self.TAcc[k] += 1
                    self.TDn[k] = self.TAcc[k] >= pre
                else:
                    self.TAcc[k] = 0; self.TDn[k] = False
            elif typ == 2:                                  # TOF
                if vin:
                    self.TAcc[k] = 0; self.TDn[k] = True; self.TRun[k] = True
                elif self.TRun[k]:
                    if Pulse100ms and self.TAcc[k] < pre:
                        self.TAcc[k] += 1
                    if self.TAcc[k] >= pre:
                        self.TRun[k] = False; self.TDn[k] = False
                else:
                    self.TDn[k] = False
            elif typ == 3:                                  # PULSO
                if rise and not self.TRun[k]:
                    self.TRun[k] = True; self.TAcc[k] = 0
                if self.TRun[k]:
                    if Pulse100ms and self.TAcc[k] < pre:
                        self.TAcc[k] += 1
                    if self.TAcc[k] >= pre:
                        self.TRun[k] = False; self.TDn[k] = False
                    else:
                        self.TDn[k] = True
                else:
                    self.TDn[k] = False
            elif typ == 4:                                  # RETARDO
                if rise:
                    self.TRun[k] = True; self.TAcc[k] = 0; self.TDn[k] = False
                if self.TRun[k]:
                    if Pulse100ms and self.TAcc[k] < pre:
                        self.TAcc[k] += 1
                    if self.TAcc[k] >= pre:
                        self.TRun[k] = False; self.TDn[k] = True
            Sig[24 + k] = self.TDn[k]

        # §E5 contadores
        for k in range(bm.N_COUNT):
            b = bm.O_COUNT + k * bm.COUNT_W
            vin = self._sig(EC[b])
            vrst = self._sig(EC[b + 1])
            pre, mode = EC[b + 2], EC[b + 3]
            rise = vin and not self.CPrevIn[k] and not EngFirst
            self.CPrevIn[k] = vin
            if vrst or EC[b] == 0:
                self.CAcc[k] = 0; self.CDn[k] = False
            else:
                if mode == 1:
                    self.CDn[k] = False
                if rise:
                    if self.CAcc[k] < 32767:
                        self.CAcc[k] += 1
                    if pre > 0 and self.CAcc[k] >= pre:
                        self.CDn[k] = True
                        if mode == 1:
                            self.CAcc[k] = 0
            Sig[32 + k] = self.CDn[k]

        # §E6 memorias
        for k in range(bm.N_MEM):
            b = bm.O_MEM + k * bm.MEM_W
            if EC[b] == 0 and EC[b + 1] == 0:
                self.MBit[k] = False
            else:
                vs = self._sig(EC[b])
                vr = self._sig(EC[b + 1])
                if EC[b + 2] == 1:
                    if vs:
                        self.MBit[k] = True
                    elif vr:
                        self.MBit[k] = False
                else:
                    if vr:
                        self.MBit[k] = False
                    elif vs:
                        self.MBit[k] = True
            Sig[40 + k] = self.MBit[k]

        # §E7 secuencia (maquina de estados)
        cnt = EC[bm.O_SEQ + 4]
        vSeqStart = self._sig(EC[bm.O_SEQ])
        vSeqStop = self._sig(EC[bm.O_SEQ + 1])
        vSeqReset = self._sig(EC[bm.O_SEQ + 2])
        self.StepEntry = False
        if cnt < 1 or cnt > 16 or vSeqReset or vSeqStop or GenStop or EngFirst:
            self.SeqActive = False; self.SeqStep = 0; self.SeqAcc = 0
        else:
            if vSeqStart and not self.SeqStartPrev and not self.SeqActive:
                self.SeqActive = True; self.SeqStep = 1; self.SeqAcc = 0; self.StepEntry = True
            elif self.SeqActive:
                if self.SeqStep < 1 or self.SeqStep > cnt:
                    self.SeqStep = 1
                sb = bm.O_STEP + (self.SeqStep - 1) * bm.STEP_W
                tm, st = EC[sb + 1], EC[sb + 3]
                tv = self._sig(EC[sb + 2])
                if Pulse100ms and self.SeqAcc < 32767:
                    self.SeqAcc += 1
                timeOk = self.SeqAcc >= st
                tRise = tv and not self.SeqTransPrev
                self.SeqTransPrev = tv
                if tm == 0:
                    done = timeOk
                elif tm == 1:
                    done = tv
                elif tm == 2:
                    done = timeOk and tv
                elif tm == 3:
                    done = tv or (st > 0 and timeOk)
                elif tm == 4:
                    done = tRise
                else:
                    done = False
                if done:
                    nx = EC[sb + 4]
                    if nx <= 0:
                        nx = self.SeqStep + 1
                    if nx > cnt:
                        nx = 1 if EC[bm.O_SEQ + 3] == 1 else 0
                    if nx == 0:
                        self.SeqActive = False; self.SeqStep = 0
                    else:
                        self.SeqStep = nx; self.StepEntry = True
                    self.SeqAcc = 0
            if self.StepEntry:
                sb = bm.O_STEP + (self.SeqStep - 1) * bm.STEP_W
                self.SeqTransPrev = self._sig(EC[sb + 2])
        self.SeqStartPrev = vSeqStart
        for k in range(16):
            Sig[48 + k] = self.SeqActive and self.SeqStep == k + 1
        acts = EC[bm.O_STEP + (self.SeqStep - 1) * bm.STEP_W] if self.SeqActive else 0
        for j in range(12):
            self.SeqBit[j] = acts % 2 == 1
            acts = acts // 2

        # §E8 motor (una sola maquina de estados escribe VFD_Control)
        runV = self._sig(EC[bm.O_RUN]); stopV = self._sig(EC[bm.O_STOP])
        pauseV = self._sig(EC[bm.O_PAUSE])
        fwdV = self._sig(EC[bm.O_DIR1]); revV = self._sig(EC[bm.O_DIR2])
        togV = self._sig(EC[bm.O_TOGGLE])
        runRise = runV and not self.RunPrev and not EngFirst
        fwdRise = fwdV and not self.FwdPrev and not EngFirst
        revRise = revV and not self.RevPrev and not EngFirst
        togRise = togV and not self.TogPrev and not EngFirst
        self.RunPrev, self.FwdPrev, self.RevPrev, self.TogPrev = runV, fwdV, revV, togV

        motorOK = (MotionCfgPresent and self.CfgReady and CfgValid
                   and self.InitSeqState == 0 and not GenStop)
        if not motorOK:
            self.RunLatch = False
        elif EC[bm.O_RUNMODE] == 1:
            self.RunLatch = runV and not stopV
        else:
            if stopV:
                self.RunLatch = False
            elif runRise:
                self.RunLatch = True

        if fwdRise:
            self.DirTarget = 1
        if revRise:
            self.DirTarget = 2
        if togRise:
            self.DirTarget = 2 if self.DirTarget == 1 else 1
        if self.SeqActive:
            if self.SeqBit[9]:
                self.DirTarget = 1
            if self.SeqBit[10]:
                self.DirTarget = 2
            if self.SeqBit[11] and self.StepEntry:
                self.DirTarget = 2 if self.DirTarget == 1 else 1

        SeqExcl = EC[bm.O_SEQ_EXCL] != 0
        seqRun = self.SeqActive and self.SeqBit[7]
        seqStop = self.SeqActive and self.SeqBit[8]
        if self.SeqActive and SeqExcl:
            wantRun = seqRun and not seqStop
        else:
            wantRun = (self.RunLatch or seqRun) and not seqStop
        if pauseV or not motorOK:
            wantRun = False

        RevT = max(EC[bm.O_REVTIME], bm.REV_TIME_MIN)
        speedZero = self.vfd_speed_raw() < 50
        if not motorOK and self.MotorState != 0:
            self.MotorState = 0; self.BrakeAcc = 0
        if Pulse100ms and self.MotorState != 1 and self.BrakeAcc < 32767:
            self.BrakeAcc += 1
        brakeOk = self.BrakeAcc >= RevT and (speedZero or self.BrakeAcc >= RevT + 50)
        if self.MotorState == 0:
            if self.DirTarget != self.DirActual and brakeOk:
                self.DirActual = self.DirTarget
            if wantRun and self.DirTarget == self.DirActual:
                self.MotorState = 1
        elif self.MotorState == 1:
            if not wantRun:
                self.MotorState = 0; self.BrakeAcc = 0
            elif self.DirTarget != self.DirActual:
                self.MotorState = 2; self.BrakeAcc = 0
        elif self.MotorState == 2:
            if brakeOk:
                self.DirActual = self.DirTarget
                self.MotorState = 1 if wantRun else 0
            elif not wantRun:
                self.MotorState = 0
        else:
            self.MotorState = 0

        if self.MotorState == 1:
            self.VFD_Control = 34 if self.DirActual == 2 else 18
            self.BandRunning = True
        else:
            self.VFD_Control = 1
            self.BandRunning = False
        self.BandEnable = self.RunLatch

        # §E9 salidas: torreta y plumas
        for k in range(7):
            self.OutReq[k] = self._sig(EC[bm.O_OUT + k])
        if self.SeqActive:
            for k in range(7):
                self.OutReq[k] = self.SeqBit[k] if SeqExcl else (self.OutReq[k] or self.SeqBit[k])
        self.PlumaFault = 0
        for p in range(2):
            up, dn = self.OutReq[3 + 2 * p], self.OutReq[4 + 2 * p]
            if up and dn:
                cmd = 0; self.PlumaFault += 1 if p == 0 else 4
            elif up:
                cmd = 1
            elif dn:
                cmd = 2
            else:
                cmd = 0
            # Comando manual del panel (%R60/%R61): solo si el motor no pide nada.
            if cmd == 0 and not (up and dn) and self.PlumaManual[p] in (1, 2):
                cmd = self.PlumaManual[p]
            if cmd != self.PlPrev[p]:
                self.PlAcc[p] = 0; self.PlTimeout[p] = False
            self.PlPrev[p] = cmd
            if cmd != 0:
                if Pulse100ms and self.PlAcc[p] < 32767:
                    self.PlAcc[p] += 1
                if EC[bm.O_PLUMA_MAX] > 0 and self.PlAcc[p] >= EC[bm.O_PLUMA_MAX]:
                    self.PlTimeout[p] = True
            if self.PlTimeout[p]:
                cmd = 0; self.PlumaFault += 2 if p == 0 else 8
            self.PlCmd[p] = cmd
        lamps = [self.OutReq[0], self.OutReq[1], self.OutReq[2]]
        if SystemStopActive:
            hm = EC[bm.O_HARD_LAMPS]
            lamps = [hm % 2 == 1, (hm // 2) % 2 == 1, (hm // 4) % 2 == 1]
            self.PlCmd = [0, 0]
        self.PhQ["Q3"], self.PhQ["Q4"], self.PhQ["Q5"] = lamps
        self.PhQ["Q8"] = self.PlCmd[0] == 1
        self.PhQ["Q9"] = self.PlCmd[0] == 2
        self.PhQ["Q6"] = self.PlCmd[1] == 1
        self.PhQ["Q7"] = self.PlCmd[1] == 2

        self.EngResetReq = False

    # ------------------------------------------------------------- entorno
    def vfd_speed_raw(self) -> int:
        return int(abs(self.vfd_hz) * 100)

    def _vfd(self):
        objetivo = {18: self.FreqRequest, 34: -self.FreqRequest}.get(self.VFD_Control, 0)
        paso = self.rampa * SCAN_S
        if self.vfd_hz < objetivo:
            self.vfd_hz = min(objetivo, self.vfd_hz + paso)
        elif self.vfd_hz > objetivo:
            self.vfd_hz = max(objetivo, self.vfd_hz - paso)


def simular(cfg_o_band, **kw) -> SimBanda:
    """Atajo: compila band.engine y arma el simulador con su direccion/frecuencia."""
    band = cfg_o_band.get("band", cfg_o_band)
    ec = bm.compilar_motor(band)
    marcha = band.get("enable") is not False
    d = 2 if str(band.get("direction")).lower() in ("izquierda", "2") else 1
    sim = SimBanda(ec, freq_hz=(band.get("freq_hz") or 30) if marcha else 0,
                   dir_cmd=d if marcha else 0, **kw)
    sim.correr(3.0)                       # preparacion del VFD (§8)
    return sim
