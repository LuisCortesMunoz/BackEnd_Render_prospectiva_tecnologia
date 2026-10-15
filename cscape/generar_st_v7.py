"""
Genera el programa maestro ST v7 (motor LAVO general) para Cscape.

    python cscape/generar_st_v7.py            -> cscape/ST_banda_v7_lavo.txt
    python cscape/generar_st_v7.py --destino "C:/.../Archivos Cscape"
                                               -> ademas LAVO_Main_ST.txt ahi

Parte del ST v6 vigente (el que esta cargado en ladder_maestro_banda.csp):

  - las secciones v5 (§0..§20) quedan INTACTAS salvo los puntos marcados
    "v7:" (direccion/frecuencia efectivas, paro por software, selector de
    motor). La prueba de regresion compara el v7 con el v5 scan a scan;
  - el motor configurable v6 (§3b y §E1..§E10) se ELIMINA;
  - se agregan §L0 (recursos fisicos), §L1 (transaccion Modbus), §L2
    (motor general) y §L3 (estado).

Todas las direcciones, offsets y codigos salen de lavo_motor.py: el ST y el
backend no pueden divergir. Las variables internas viven en arreglos
empaquetados (LI = enteros, LB = booleanos), igual que el v6, para que en
Cscape solo haya que declarar unas pocas; la leyenda va al inicio del ST.
"""

import argparse
import os
import sys

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(AQUI))
import lavo_motor as lm  # noqa: E402

V6 = os.path.join(AQUI, "ST_banda_v6_motor_configurable.txt")
SALIDA = os.path.join(AQUI, "ST_banda_v7_lavo.txt")

# ---------------------------------------------------------------------------
# Posiciones de las variables internas (leyenda del ST)
# ---------------------------------------------------------------------------
LI_MAP = {
    "TxState": 0, "CmdPrev": 1, "ErrCode": 2, "ErrField": 3, "ActiveId": 4, "ActiveChk": 5,
    "ValPtr": 6, "RxTicks": 7, "Heartbeat": 8, "ScanCnt": 9, "ScansPerSec": 10,
    "EffDir": 11, "EffFreq": 12, "MotorState": 13, "DirActual": 14, "DirTarget": 15,
    "BrakeAcc": 16, "RevT": 17, "StopReason7": 18, "PlumaFault": 19, "LastResult": 20,
    "StagedId": 21, "SpeedAbs": 22, "ValEnd": 23, "RngField": 24,
}
LI_RANGOS = {"DebAcc": (25, 12), "TAcc": (37, 16), "CAcc": (53, 16), "PlAcc": (69, 2),
             "PlPrev": (71, 2), "PlCmd": (73, 2), "SeqI": (75, 16)}
for _i, _n in enumerate(("a", "val", "base", "op", "lo", "hi", "kind", "r", "msk", "cnt", "ntrue",
                         "nused", "deb", "sb", "st", "ib", "bb", "gb", "n", "tgt", "pre", "pre2",
                         "typ", "mode", "q", "tacc")):
    LI_MAP[_n] = 91 + _i
LI_LEN = lm.LI_LEN

LB_MAP = {
    "Pulse100ms": 0, "Prev100ms": 1, "EngResetReq": 2, "EngFirst": 3, "V7Active": 4,
    "RunLatch": 5, "RunArmed": 6, "HardStop7": 7, "SoftStop7": 8, "GenStop7": 9,
    "RunPrev": 10, "FwdPrev": 11, "RevPrev": 12, "TogPrev": 13, "BrakeOk": 14, "MotorOK": 15,
    "WantRun": 16, "ApplyPulse": 17, "sv": 18, "neg": 19, "res": 20, "used": 21, "edge": 22,
    "vin": 23, "vrst": 24, "vdn": 25, "ok": 26, "runRise": 27, "fwdRise": 28, "revRise": 29,
    "togRise": 30, "seqRun": 31, "seqStop": 32, "seqDir1": 33, "seqDir2": 34, "seqTog": 35,
    "seqOwnMot": 36, "sStart": 37, "sStop": 38, "sRst": 39, "sPause": 40, "fire": 41,
    "timeOk": 42, "tsig": 43, "owned": 44, "seqv": 45, "pUp": 46, "pDn": 47, "sRise": 48,
    "contrib": 49, "sigRun": 50, "sigStop": 51, "sigPause": 52, "sigD1": 53, "sigD2": 54,
    "sigTog": 55, "FirstCycle": 56, "tdn": 57, "trun": 58,
}
LB_RANGOS = {"RawIn": (60, 12), "DebIn": (72, 12), "CPrev": (84, 16), "TDn": (100, 16),
             "TRun": (116, 16), "TPrevIn": (132, 16), "CtDn": (148, 16), "CtUpPrev": (164, 16),
             "CtDnPrev": (180, 16), "MBit": (196, 16), "OutReq": (212, 12), "OutFinal": (224, 12),
             "PlTimeout": (236, 2), "HwQ": (240, 12), "HwLamp": (252, 12), "EstopQ": (264, 12),
             "HwSens": (276, 12), "SeqB": (290, 20), "SeqUsesQ": (310, 24), "SeqCurQ": (334, 24),
             "SeqUsesMot": (358, 2), "ValBits": (380, 12)}
LB_LEN = 400
SEQI_W, SEQB_W = 8, 10      # LI[SeqI + 8k + campo] · LB[SeqB + 10k + campo]
SQI = {"Step": 0, "Acc": 1, "Cycles": 2, "CurOut": 3, "CurMot": 4}
SQB = {"Active": 0, "Paused": 1, "Done": 2, "CyclePulse": 3, "StartPrev": 4, "TransPrev": 5,
       "StepEntry": 6, "Excl": 7, "PauseOff": 8}


def I(n):
    return f"LI[{LI_MAP[n]}]"


def B(n):
    return f"LB[{LB_MAP[n]}]"


def Ir(rango, expr="0"):
    base = LI_RANGOS[rango][0]
    return f"LI[{base + int(expr)}]" if str(expr).isdigit() else f"LI[ANY_TO_DINT({base} + {expr})]"


def Br(rango, expr="0"):
    base = LB_RANGOS[rango][0]
    return f"LB[{base + int(expr)}]" if str(expr).isdigit() else f"LB[ANY_TO_DINT({base} + {expr})]"


def ACT(expr):
    return f"LACT[{expr}]" if str(expr).isdigit() else f"LACT[ANY_TO_DINT({expr})]"


def PEN(expr):
    return f"LPEN[{expr}]" if str(expr).isdigit() else f"LPEN[ANY_TO_DINT({expr})]"


def ind(txt, n):
    pre = " " * n
    return "\n".join((pre + l) if l.strip() else l for l in txt.strip("\n").split("\n"))


def rd(dst, code_expr, n=0):
    """Lee la senal cuyo codigo es code_expr (0 = nada, +1000 = NOT) en dst.
    El codigo ya fue validado al aplicar la config (0..143 o 1001..1143),
    asi que MOD 1000 siempre cae dentro de LSIG."""
    a = I("a")
    return ind(f"""
{a} := {code_expr} ;
{dst} := LSIG[ANY_TO_DINT({a} MOD {lm.NOT_OFFSET})] XOR ({a} >= {lm.NOT_OFFSET}) ;""", n)


def bits(src_expr, dst_rango, dst_off_expr, nbits, var, n=0):
    """Descompone src (0..4095) en nbits booleanos LB[dst + off + var]."""
    m = I("msk")
    dst = Br(dst_rango, f"{dst_off_expr} + {var}" if dst_off_expr != "0" else var)
    return ind(f"""
{m} := {src_expr} ;
FOR {var} := 0 TO {nbits - 1} DO
    {dst} := ({m} MOD 2) = 1 ;
    {m} := {m} / 2 ;
END_FOR ;""", n)


# ---------------------------------------------------------------------------
# Perfil de hardware (sale de lm.HW_BANDA)
# ---------------------------------------------------------------------------
def seccion_L0():
    hw = lm.HW_BANDA
    lineas = []
    for j in range(lm.N_OUT):
        q = f"Q{j + 1}"
        d = hw["salidas"].get(q)
        lineas.append(f"{Br('HwQ', j)} := {'TRUE' if d else 'FALSE'} ;    "
                      f"(* {q}: {d['desc'] if d else 'no cableada en la banda'} *)")
    for j in range(lm.N_OUT):
        q = f"Q{j + 1}"
        d = hw["salidas"].get(q) or {}
        lineas.append(f"{Br('HwLamp', j)} := {'TRUE' if d.get('lampara') else 'FALSE'} ;    "
                      f"(* {q} {'es lampara: puede quedar encendida con I3' if d.get('lampara') else 'no es lampara'} *)")
    fuentes = {"I1": "BtnStart", "I2": "BtnAux", "I3": "BtnStop", "I4": "S1_Det", "I5": "S2_Det"}
    entradas = []
    for j in range(lm.N_IN):
        i = f"I{j + 1}"
        d = hw["entradas"].get(i)
        if d:
            entradas.append(f"{Br('RawIn', j)} := {fuentes[i]} ;    (* {i}: {d['desc']} ({d['tipo']}) *)")
        else:
            entradas.append(f"{Br('RawIn', j)} := FALSE ;    (* {i}: no cableada *)")
        entradas.append(f"{Br('HwSens', j)} := {'TRUE' if (d or {}).get('sensor') else 'FALSE'} ;    "
                        f"(* {i}: {'filtro de sensores' if (d or {}).get('sensor') else 'filtro de botones'} *)")
    return f"""
(* ============================================================ *)
(* §L0 - RECURSOS FISICOS DE ESTA BANDA (perfil de hardware)   *)
(*                                                            *)
(* UNICA seccion que depende del cableado. Ningun registro     *)
(* Modbus la cambia: NA/NC, el paro de emergencia I3 y el      *)
(* enclavamiento de las plumas son fijos aqui.                 *)
(* Entradas: valor NORMALIZADO por §2 (TRUE = presionado /     *)
(* detecta). Salidas: TRUE = el motor puede manejarla.         *)
(*                                                            *)
(* Banda: lamparas Q3 verde, Q4 amarilla, Q5 roja; plumas      *)
(* Q6..Q9; botones I1, I2 e I3 (paro). Q1, Q2 y Q10..Q12 NO    *)
(* estan cableadas: el motor nunca las escribe.               *)
(* ============================================================ *)

{chr(10).join(lineas)}

{chr(10).join(entradas)}

"""


# ---------------------------------------------------------------------------
# §L1 - Transaccion
# ---------------------------------------------------------------------------
def clasificacion():
    """Deja en kind/lo/hi el tipo de la palabra ek (espejo de lm._es_senal y
    lm.rango_campo). El PLC solo vigila lo que puede romperlo: codigos de
    senal (indices de LSIG), indices de pasos y valores negativos. La
    coherencia completa la revisa el backend antes de enviar."""
    k, lo, hi, r, q = I("kind"), I("lo"), I("hi"), I("r"), I("q")
    S = lm
    return f"""
{k} := 2 ; {lo} := 0 ; {hi} := 32767 ;
IF ek <= {S.H_CHK} THEN
    {k} := 0 ;
ELSIF ek = {S.H_FREQ} THEN
    {hi} := {S.FREQ_MAX} ;
ELSIF ek = {S.H_DIR} THEN
    {hi} := 2 ;
ELSIF (ek = {S.M_RUN}) OR (ek = {S.M_STOP}) OR ((ek >= {S.M_PAUSE}) AND (ek <= {S.M_TOGGLE})) OR
      ((ek >= {S.O_OUT}) AND (ek <= {S.O_OUT + S.N_OUT - 1})) THEN
    {k} := 1 ;
ELSIF (ek >= {S.O_COND}) AND (ek < {S.O_MEM}) THEN
    {r} := (ek - {S.O_COND}) MOD {S.COND_W} ;
    IF ({r} >= 1) AND ({r} <= 4) THEN {k} := 1 ; END_IF ;
ELSIF (ek >= {S.O_MEM}) AND (ek < {S.O_TIMER}) THEN
    IF ((ek - {S.O_MEM}) MOD {S.MEM_W}) <= 1 THEN {k} := 1 ; END_IF ;
ELSIF (ek >= {S.O_TIMER}) AND (ek < {S.O_COUNT}) THEN
    {r} := (ek - {S.O_TIMER}) MOD {S.TIMER_W} ;
    IF ({r} = 1) OR ({r} = 2) THEN {k} := 1 ; END_IF ;
ELSIF (ek >= {S.O_COUNT}) AND (ek < {S.O_SEQ}) THEN
    IF ((ek - {S.O_COUNT}) MOD {S.COUNT_W}) <= 2 THEN {k} := 1 ; END_IF ;
ELSIF (ek >= {S.O_SEQ}) AND (ek < {S.O_SEQ + S.N_SEQ * S.SEQ_W}) THEN
    {r} := (ek - {S.O_SEQ}) MOD {S.SEQ_W} ;
    IF {r} <= {S.SQ_PAUSE} THEN
        {k} := 1 ;
    ELSIF {r} = {S.SQ_NSTEPS} THEN
        {hi} := {S.N_STEPS} ;
    ELSIF {r} >= {S.SEQ_HDR} THEN
        {q} := ({r} - {S.SEQ_HDR}) MOD {S.STEP_W} ;
        IF ({q} = {S.ST_TSIG}) OR ({q} = {S.ST_ALTSIG}) THEN
            {k} := 1 ;
        ELSIF ({q} = {S.ST_NEXT}) OR ({q} = {S.ST_ALTNEXT}) THEN
            {hi} := {S.NEXT_FIN} ;
        END_IF ;
    END_IF ;
END_IF ;"""


def chequeo(cond, codigo, campo):
    return f"IF ({I('ErrCode')} = 0) AND ({cond}) THEN {I('ErrCode')} := {codigo} ; {I('ErrField')} := {campo} ; END_IF ;"


def validacion_final():
    S = lm
    E, F = I("ErrCode"), I("ErrField")
    emerg = (S.SENALES[S.EMERGENCIA], S.SENALES[S.EMERGENCIA] + S.NOT_OFFSET)
    L = []
    L.append(chequeo(f"{PEN(S.H_FORMAT)} <> {S.ENGINE_VERSION}", S.E_FORMAT, S.H_FORMAT + 1))
    L.append(chequeo(f"{PEN(S.H_LEN)} <> {S.CFG_LEN}", S.E_LEN, S.H_LEN + 1))
    L.append(chequeo(f"{PEN(S.H_ID)} < 1", S.E_ID, S.H_ID + 1))
    L.append(chequeo(f"{PEN(S.H_ID)} <> {I('StagedId')}", S.E_IDMISMATCH, S.H_ID + 1))
    L.append(chequeo(f"ANY_TO_DINT({PEN(S.H_CHK)}) <> LCK[0]", S.E_CHK, S.H_CHK + 1))
    L.append(f"IF ({E} = 0) AND ({I('RngField')} <> 0) THEN {E} := {S.E_RANGE} ; {F} := {I('RngField')} ; END_IF ;")
    L.append(f"IF {PEN(S.H_MOVE)} = 1 THEN")
    L.append("    " + chequeo(f"({PEN(S.H_FREQ)} < {S.FREQ_MIN}) OR ({PEN(S.H_FREQ)} > {S.FREQ_MAX})", S.E_SEM, S.H_FREQ + 1))
    L.append("    " + chequeo(f"({PEN(S.H_DIR)} <> 1) AND ({PEN(S.H_DIR)} <> 2)", S.E_SEM, S.H_DIR + 1))
    L.append("END_IF ;")
    L.append(chequeo(f"({PEN(S.M_RUN)} = {emerg[0]}) OR ({PEN(S.M_RUN)} = {emerg[1]})", S.E_SAFETY, S.M_RUN + 1))
    L.append(bits(PEN(S.H_ESTOP_LAMPS), "ValBits", "0", S.N_OUT, "ek"))
    L.append(f"FOR ek := 0 TO {S.N_OUT - 1} DO")
    L.append("    " + chequeo(f"({PEN(f'{S.O_OUT} + ek')} <> 0) AND NOT {Br('HwQ', 'ek')}", S.E_HW, f"{S.O_OUT + 1} + ek"))
    L.append("END_FOR ;")
    L.append(f"FOR ek := 0 TO {S.N_OUT - 1} DO")
    L.append("    " + chequeo(f"{Br('ValBits', 'ek')} AND NOT {Br('HwLamp', 'ek')}", S.E_HW, S.H_ESTOP_LAMPS + 1))
    L.append("END_FOR ;")
    sb, n, st = I("sb"), I("n"), I("st")
    L.append(f"FOR ej := 0 TO {S.N_SEQ - 1} DO")
    L.append(f"    {sb} := {S.O_SEQ} + ej * {S.SEQ_W} ;")
    L.append(f"    {n} := {PEN(f'{sb} + {S.SQ_NSTEPS}')} ;")
    L.append("    " + chequeo(f"({n} > 0) AND (({PEN(sb)} = {emerg[0]}) OR ({PEN(sb)} = {emerg[1]}))", S.E_SAFETY, f"{sb} + 1"))
    L.append(f"    FOR ek := 1 TO {n} DO")
    L.append(f"        {st} := {sb} + {S.SEQ_HDR} + (ek - 1) * {S.STEP_W} ;")
    for dst in (S.ST_NEXT, S.ST_ALTNEXT):
        L.append("        " + chequeo(f"({PEN(f'{st} + {dst}')} > {n}) AND ({PEN(f'{st} + {dst}')} <> {S.NEXT_FIN})",
                                       S.E_SEM, f"{st} + {dst + 1}"))
    L.append(ind(bits(PEN(f"{st} + {S.ST_OUT}"), "ValBits", "0", S.N_OUT, "em"), 8))
    L.append(f"        FOR em := 0 TO {S.N_OUT - 1} DO")
    L.append("            " + chequeo(f"{Br('ValBits', 'em')} AND NOT {Br('HwQ', 'em')}", S.E_HW, f"{st} + {S.ST_OUT + 1}"))
    L.append("        END_FOR ;")
    for a_, c_, _nom in S.HW_BANDA["pares_excluyentes"]:
        ia, ic = int(a_[1:]) - 1, int(c_[1:]) - 1
        L.append("        " + chequeo(f"{Br('ValBits', ia)} AND {Br('ValBits', ic)}", S.E_SAFETY, f"{st} + {S.ST_OUT + 1}")
                 + f"    (* {_nom}: {a_} y {c_} juntas *)")
    L.append("    END_FOR ;")
    L.append("END_FOR ;")
    return "\n".join(L)


def seccion_L1():
    S = lm
    T = I("TxState")
    return f"""
(* ============================================================ *)
(* §L1 - CONFIGURACION TRANSACCIONAL POR MODBUS (motor v7)      *)
(*                                                            *)
(* LCMD (%R{S.CMD_BASE}..%R{S.CMD_BASE + S.CMD_LEN - 1}) : comandos que escribe el backend.     *)
(*   [0] CmdSeq: cada cambio de valor ejecuta [1] CmdCode       *)
(*       1 BEGIN  2 COMMIT (arg [2] = id)  3 ABORT              *)
(*       4 UNLOAD (vuelve a v5)  5 RESET (reinicia el motor)    *)
(*   [3..6] APP1..APP4 (botones virtuales)  [7] paro software  *)
(* LSTG (%R{S.STAGE_BASE}..%R{S.STAGE_BASE + S.CFG_LEN - 1}) : area de ESPERA. El motor nunca la   *)
(*   lee directamente: COMMIT la copia a LPEN en trozos de     *)
(*   {S.VAL_CHUNK} palabras por scan mientras calcula el checksum y    *)
(*   valida; si todo pasa, LPEN -> LACT en UN solo scan.        *)
(* La config ACTIVA (LACT) no cambia hasta un COMMIT valido.   *)
(* Transaccion abierta {S.RX_TIMEOUT_DECIMAS // 10} s sin COMMIT -> TIMEOUT (se descarta). *)
(* ============================================================ *)

IF {I('ScanCnt')} < 32000 THEN {I('ScanCnt')} := {I('ScanCnt')} + 1 ; END_IF ;
IF Pulse1s THEN
    {I('ScansPerSec')} := {I('ScanCnt')} ;
    {I('ScanCnt')} := 0 ;
END_IF ;
IF {B('Pulse100ms')} THEN
    {I('Heartbeat')} := {I('Heartbeat')} + 1 ;
    IF {I('Heartbeat')} > 32000 THEN {I('Heartbeat')} := 0 ; END_IF ;
END_IF ;

{B('ApplyPulse')} := FALSE ;

IF LCMD[{S.C_SEQ}] <> {I('CmdPrev')} THEN

    {I('CmdPrev')} := LCMD[{S.C_SEQ}] ;
    {I('LastResult')} := 0 ;

    CASE LCMD[{S.C_CODE}] OF
        {S.CMD_BEGIN}:
            {T} := {S.TX_RX} ;
            {I('RxTicks')} := 0 ;
            {I('ErrCode')} := 0 ;
            {I('ErrField')} := 0 ;
            {I('ValPtr')} := 0 ;
        {S.CMD_COMMIT}:
            IF {T} = {S.TX_RX} THEN
                {T} := {S.TX_VAL} ;
                {I('ValPtr')} := 0 ;
                {I('RngField')} := 0 ;
                {I('ErrCode')} := 0 ;
                {I('ErrField')} := 0 ;
                {I('StagedId')} := LCMD[{S.C_ARG}] ;
                LCK[0] := DINT#0 ;
            ELSE
                {I('LastResult')} := {S.E_NOTX} ;
            END_IF ;
        {S.CMD_ABORT}:
            IF ({T} = {S.TX_RX}) OR ({T} = {S.TX_VAL}) THEN
                {T} := {S.TX_ABORT} ;
            ELSE
                {I('LastResult')} := {S.E_NOTX} ;
            END_IF ;
        {S.CMD_UNLOAD}:
            IF {I('ActiveId')} <> 0 THEN
                {B('ApplyPulse')} := TRUE ;
            END_IF ;
            {I('ActiveId')} := 0 ;
            {I('ActiveChk')} := 0 ;
            {T} := {S.TX_IDLE} ;
        {S.CMD_RESET}:
            IF {I('ActiveId')} <> 0 THEN
                {B('EngResetReq')} := TRUE ;
            ELSE
                {I('LastResult')} := {S.E_NOTX} ;
            END_IF ;
    ELSE
        {I('LastResult')} := {S.E_CMD} ;
    END_CASE ;

END_IF ;


(* Transaccion abierta sin COMMIT: se descarta por tiempo. *)
IF {T} = {S.TX_RX} THEN
    IF {B('Pulse100ms')} THEN
        {I('RxTicks')} := {I('RxTicks')} + 1 ;
    END_IF ;
    IF {I('RxTicks')} >= {S.RX_TIMEOUT_DECIMAS} THEN
        {T} := {S.TX_TIMEOUT} ;
        {I('ErrCode')} := {S.E_TIMEOUT} ;
        {I('ErrField')} := 0 ;
    END_IF ;
END_IF ;


(* COMMIT: copia + checksum + rangos, {S.VAL_CHUNK} palabras por scan. *)
IF {T} = {S.TX_VAL} THEN

    {I('ValEnd')} := {I('ValPtr')} + {S.VAL_CHUNK - 1} ;
    IF {I('ValEnd')} > {S.CFG_LEN - 1} THEN
        {I('ValEnd')} := {S.CFG_LEN - 1} ;
    END_IF ;

    FOR ek := {I('ValPtr')} TO {I('ValEnd')} DO

        {I('val')} := LSTG[ANY_TO_DINT(ek)] ;
        LPEN[ANY_TO_DINT(ek)] := {I('val')} ;

        IF ek <> {S.H_CHK} THEN
            LCK[0] := (LCK[0] * DINT#31 + ANY_TO_DINT({I('val')}) + DINT#32769) MOD DINT#32749 ;
        END_IF ;
{ind(clasificacion(), 8)}

        IF ({I('RngField')} = 0) AND ({I('kind')} = 1) THEN
            IF NOT ((({I('val')} >= 0) AND ({I('val')} <= {S.SIG_MAX})) OR
                    (({I('val')} >= {S.NOT_OFFSET + 1}) AND ({I('val')} <= {S.NOT_OFFSET + S.SIG_MAX}))) THEN
                {I('RngField')} := ek + 1 ;
            END_IF ;
        ELSIF ({I('RngField')} = 0) AND ({I('kind')} = 2) THEN
            IF ({I('val')} < {I('lo')}) OR ({I('val')} > {I('hi')}) THEN
                {I('RngField')} := ek + 1 ;
            END_IF ;
        END_IF ;

    END_FOR ;

    {I('ValPtr')} := {I('ValEnd')} + 1 ;

    IF {I('ValPtr')} > {S.CFG_LEN - 1} THEN

        (* Reglas en el MISMO orden que lavo_motor.error_plc(). *)
{ind(validacion_final(), 8)}

        IF {I('ErrCode')} = 0 THEN
            (* Aplicar: copia atomica (un scan) y reinicio seguro. *)
            FOR ek := 0 TO {S.CFG_LEN - 1} DO
                LACT[ANY_TO_DINT(ek)] := LPEN[ANY_TO_DINT(ek)] ;
            END_FOR ;
            {I('ActiveId')} := LPEN[{S.H_ID}] ;
            {I('ActiveChk')} := LPEN[{S.H_CHK}] ;
            {T} := {S.TX_OK} ;
            {B('ApplyPulse')} := TRUE ;
            {B('EngResetReq')} := TRUE ;
        ELSE
            {T} := {S.TX_REJ} ;
        END_IF ;

    END_IF ;

END_IF ;


(* Motor v7 activo = hay una configuracion aplicada. *)
{B('V7Active')} := ({I('ActiveId')} <> 0) ;

(* Direccion y frecuencia EFECTIVAS que usan §3, §8 y §9:       *)
(* con v7 salen de la config activa; sin v7, de DirCmd/FreqReq. *)
IF {B('V7Active')} THEN
    {I('EffDir')}  := {ACT(S.H_DIR)} ;
    {I('EffFreq')} := {ACT(S.H_FREQ)} ;
ELSE
    {I('EffDir')}  := DirCmd ;
    {I('EffFreq')} := FreqRequest ;
END_IF ;

(* Paros del motor v7: I3 (fisico, siempre) y paro por software. *)
{B('HardStop7')} := BtnStop ;
{B('SoftStop7')} := {B('V7Active')} AND (LCMD[{S.C_SOFTSTOP}] <> 0) ;
{B('GenStop7')}  := {B('HardStop7')} OR {B('SoftStop7')} ;

"""


# ---------------------------------------------------------------------------
# §L2 - Motor general
# ---------------------------------------------------------------------------
def seccion_L2():
    S = lm
    ef = B("EngFirst")
    p100 = B("Pulse100ms")
    # ---- reinicio
    reinicio = f"""
(* ============================================================ *)
(* §L2a - REINICIO DEL MOTOR (config nueva, RESET, R6, 1er scan)*)
(* En el scan de reinicio ningun flanco cuenta (EngFirst).      *)
(* ============================================================ *)

{ef} := {B('EngResetReq')} ;

IF {ef} THEN

    FOR ek := 0 TO 15 DO
        {Ir('TAcc', 'ek')} := 0 ;
        {Br('TDn', 'ek')} := FALSE ;
        {Br('TRun', 'ek')} := FALSE ;
        {Ir('CAcc', 'ek')} := 0 ;
        {Br('CtDn', 'ek')} := FALSE ;
        {Br('MBit', 'ek')} := FALSE ;
        {Br('CPrev', 'ek')} := FALSE ;
    END_FOR ;

    FOR ek := {S.SIG_COND} TO {S.SIG_TABLE - 1} DO
        LSIG[ANY_TO_DINT(ek)] := FALSE ;
    END_FOR ;

    FOR ek := 0 TO {S.N_OUT - 1} DO
        {Br('OutReq', 'ek')} := FALSE ;
        {Br('OutFinal', 'ek')} := FALSE ;
        {Ir('DebAcc', 'ek')} := 0 ;
        {Br('DebIn', 'ek')} := {Br('RawIn', 'ek')} ;
    END_FOR ;

    (* Secuencias: estado en cero y que salidas/motor usa cada una. *)
    FOR ej := 0 TO {S.N_SEQ - 1} DO
        {I('sb')} := {S.O_SEQ} + ej * {S.SEQ_W} ;
        FOR ek := 0 TO {SEQI_W - 1} DO
            {Ir('SeqI', f'ej * {SEQI_W} + ek')} := 0 ;
        END_FOR ;
        FOR ek := 0 TO {SEQB_W - 1} DO
            {Br('SeqB', f'ej * {SEQB_W} + ek')} := FALSE ;
        END_FOR ;
        {Br('SeqB', f"ej * {SEQB_W} + {SQB['Excl']}")} := ({ACT(f"{I('sb')} + {S.SQ_EXCL}")} <> 0) ;
        {Br('SeqB', f"ej * {SEQB_W} + {SQB['PauseOff']}")} := ({ACT(f"{I('sb')} + {S.SQ_PAUSEOFF}")} <> 0) ;
        FOR em := 0 TO {S.N_OUT - 1} DO
            {Br('SeqUsesQ', f'ej * {S.N_OUT} + em')} := FALSE ;
        END_FOR ;
        {Br('SeqUsesMot', 'ej')} := FALSE ;
        FOR ek := 1 TO {ACT(f"{I('sb')} + {S.SQ_NSTEPS}")} DO
            {I('st')} := {I('sb')} + {S.SEQ_HDR} + (ek - 1) * {S.STEP_W} ;
            {I('msk')} := {ACT(f"{I('st')} + {S.ST_OUT}")} ;
            FOR em := 0 TO {S.N_OUT - 1} DO
                IF ({I('msk')} MOD 2) = 1 THEN
                    {Br('SeqUsesQ', f'ej * {S.N_OUT} + em')} := TRUE ;
                END_IF ;
                {I('msk')} := {I('msk')} / 2 ;
            END_FOR ;
            IF {ACT(f"{I('st')} + {S.ST_MOTOR}")} <> 0 THEN
                {Br('SeqUsesMot', 'ej')} := TRUE ;
            END_IF ;
        END_FOR ;
    END_FOR ;

    (* Motor detenido, direccion inicial, plumas quietas. *)
    {B('RunLatch')} := FALSE ;
    {B('RunArmed')} := FALSE ;
    {I('MotorState')} := 0 ;
    IF ({I('EffDir')} = 1) OR ({I('EffDir')} = 2) THEN
        {I('DirActual')} := {I('EffDir')} ;
    ELSE
        {I('DirActual')} := 1 ;
    END_IF ;
    {I('DirTarget')} := {I('DirActual')} ;
    {I('BrakeAcc')} := 32767 ;
    FOR ek := 0 TO 1 DO
        {Ir('PlAcc', 'ek')} := 0 ;
        {Ir('PlPrev', 'ek')} := 0 ;
        {Ir('PlCmd', 'ek')} := 0 ;
        {Br('PlTimeout', 'ek')} := FALSE ;
    END_FOR ;

    (* Lamparas que quedan encendidas con el paro de emergencia. *)
{ind(bits(ACT(S.H_ESTOP_LAMPS), 'EstopQ', '0', S.N_OUT, 'ek'), 4)}

END_IF ;
"""
    # ---- entradas y senales
    entradas = f"""
(* ============================================================ *)
(* §L2b - ENTRADAS FILTRADAS Y TABLA DE SENALES                 *)
(* LSIG[codigo] = valor de la senal en este scan:               *)
(*   0 nada | 1..12 I1..I12 | 13..24 Q1..Q12 (scan anterior)    *)
(*   25 SIEMPRE | 26 corriendo 27 detenida 28 dir1 29 dir2      *)
(*   30 invirtiendo 31 VFD en cero 32 CfgReady 33 primer ciclo  *)
(*   34..37 APP1..APP4 | 38 paro software                       *)
(*   40..55 C1..C16 | 56..71 M1..M16 | 72..87 T1..T16           *)
(*   88..103 CT1..CT16 | 104.. SQ1 (+0 activa +1 pausada +2 fin *)
(*   +3 ciclo +4..+19 paso 1..16) | 124.. SQ2 (igual)           *)
(* Un codigo +1000 lee la senal negada (NOT).                   *)
(* I3 se filtra SOLO para usarlo como condicion: el paro de     *)
(* emergencia (§4 y GenStop7) lee la entrada directa.           *)
(* ============================================================ *)

FOR ek := 0 TO {S.N_IN - 1} DO

    IF {Br('HwSens', 'ek')} THEN
        {I('deb')} := {ACT(S.H_DEB_SENS)} ;
    ELSE
        {I('deb')} := {ACT(S.H_DEB_BTN)} ;
    END_IF ;

    IF ({I('deb')} <= 0) OR {ef} THEN
        {Br('DebIn', 'ek')} := {Br('RawIn', 'ek')} ;
        {Ir('DebAcc', 'ek')} := 0 ;
    ELSIF {Br('RawIn', 'ek')} = {Br('DebIn', 'ek')} THEN
        {Ir('DebAcc', 'ek')} := 0 ;
    ELSE
        IF {p100} THEN
            {Ir('DebAcc', 'ek')} := {Ir('DebAcc', 'ek')} + 1 ;
        END_IF ;
        IF {Ir('DebAcc', 'ek')} >= {I('deb')} THEN
            {Br('DebIn', 'ek')} := {Br('RawIn', 'ek')} ;
            {Ir('DebAcc', 'ek')} := 0 ;
        END_IF ;
    END_IF ;

    LSIG[ANY_TO_DINT({S.SIG_I} + ek)] := {Br('DebIn', 'ek')} ;
    LSIG[ANY_TO_DINT({S.SIG_Q} + ek)] := {Br('OutFinal', 'ek')} ;

END_FOR ;

{I('SpeedAbs')} := VFD_SpeedRaw ;
IF {I('SpeedAbs')} < 0 THEN
    {I('SpeedAbs')} := 0 - {I('SpeedAbs')} ;
END_IF ;

LSIG[0]  := FALSE ;
LSIG[{S.SIG_TRUE}] := TRUE ;
LSIG[{S.SIG_SYS['BANDA_CORRIENDO']}] := ({I('MotorState')} = 1) ;
LSIG[{S.SIG_SYS['BANDA_DETENIDA']}] := ({I('MotorState')} <> 1) ;
LSIG[{S.SIG_SYS['DIR_1']}] := ({I('DirActual')} = 1) ;
LSIG[{S.SIG_SYS['DIR_2']}] := ({I('DirActual')} = 2) ;
LSIG[{S.SIG_SYS['INVIRTIENDO']}] := ({I('MotorState')} = 2) ;
LSIG[{S.SIG_SYS['VFD_CERO']}] := ({I('SpeedAbs')} < 50) ;
LSIG[{S.SIG_SYS['CFG_LISTA']}] := CfgReady ;
LSIG[{S.SIG_SYS['PRIMER_CICLO']}] := {B('FirstCycle')} ;
FOR ek := 0 TO {S.N_APP - 1} DO
    LSIG[ANY_TO_DINT({S.SIG_SYS['APP1']} + ek)] := (LCMD[ANY_TO_DINT({S.C_APP} + ek)] <> 0) ;
END_FOR ;
LSIG[{S.SIG_SYS['PARO_SOFTWARE']}] := {B('SoftStop7')} ;
LSIG[39] := FALSE ;
"""
    # ---- condiciones
    a = I("a")
    condiciones = f"""
(* ============================================================ *)
(* §L2c - CONDICIONES C1..C16                                   *)
(* CFG[{S.O_COND} + 6k]: Op (1 AND 2 OR 3 XOR 4 NAND 5 NOR),            *)
(*   Entrada1..4 (codigo de senal), Flanco (0 nivel 1 subida    *)
(*   2 bajada). Una condicion puede usar otra: las anteriores   *)
(*   dan su valor de este scan, las posteriores el del anterior.*)
(* ============================================================ *)

FOR ek := 0 TO {S.N_COND - 1} DO

    {I('base')} := {S.O_COND} + ek * {S.COND_W} ;
    {I('op')} := {ACT(I('base'))} ;
    {I('ntrue')} := 0 ;
    {I('nused')} := 0 ;

    IF {I('op')} <> 0 THEN
        FOR ej := 1 TO 4 DO
            {a} := {ACT(f"{I('base')} + ej")} ;
            IF {a} <> 0 THEN
                {I('nused')} := {I('nused')} + 1 ;
{rd(B('sv'), a, 16)}
                IF {B('sv')} THEN
                    {I('ntrue')} := {I('ntrue')} + 1 ;
                END_IF ;
            END_IF ;
        END_FOR ;
    END_IF ;

    CASE {I('op')} OF
        1: {B('res')} := ({I('nused')} > 0) AND ({I('ntrue')} = {I('nused')}) ;
        2: {B('res')} := ({I('ntrue')} > 0) ;
        3: {B('res')} := (({I('ntrue')} MOD 2) = 1) ;
        4: {B('res')} := ({I('nused')} > 0) AND ({I('ntrue')} < {I('nused')}) ;
        5: {B('res')} := ({I('nused')} > 0) AND ({I('ntrue')} = 0) ;
    ELSE
        {B('res')} := FALSE ;
    END_CASE ;

    CASE {ACT(f"{I('base')} + 5")} OF
        1: {B('edge')} := {B('res')} AND NOT {Br('CPrev', 'ek')} AND NOT {ef} ;
        2: {B('edge')} := (NOT {B('res')}) AND {Br('CPrev', 'ek')} AND NOT {ef} ;
    ELSE
        {B('edge')} := {B('res')} ;
    END_CASE ;

    {Br('CPrev', 'ek')} := {B('res')} ;
    LSIG[ANY_TO_DINT({S.SIG_COND} + ek)] := {B('edge')} ;

END_FOR ;
"""
    # ---- memorias
    memorias = f"""
(* ============================================================ *)
(* §L2d - MEMORIAS M1..M16 (enclavamientos set / reset)         *)
(* CFG[{S.O_MEM} + 3k]: Set, Reset, Prioridad (0 gana reset, 1 gana set) *)
(* Cualquier paro general (I3 o software) las borra: un          *)
(* enclavamiento nunca re-energiza nada al soltar el paro.      *)
(* ============================================================ *)

FOR ek := 0 TO {S.N_MEM - 1} DO

    {I('base')} := {S.O_MEM} + ek * {S.MEM_W} ;

    IF {B('GenStop7')} OR (({ACT(I('base'))} = 0) AND ({ACT(I('base') + ' + 1')} = 0)) THEN
        {Br('MBit', 'ek')} := FALSE ;
    ELSE
{rd(B('vin'), ACT(I('base')), 8)}
{rd(B('vrst'), ACT(I('base') + ' + 1'), 8)}
        IF {ACT(I('base') + ' + 2')} = 1 THEN
            IF {B('vin')} THEN
                {Br('MBit', 'ek')} := TRUE ;
            ELSIF {B('vrst')} THEN
                {Br('MBit', 'ek')} := FALSE ;
            END_IF ;
        ELSE
            IF {B('vrst')} THEN
                {Br('MBit', 'ek')} := FALSE ;
            ELSIF {B('vin')} THEN
                {Br('MBit', 'ek')} := TRUE ;
            END_IF ;
        END_IF ;
    END_IF ;

    LSIG[ANY_TO_DINT({S.SIG_MEM} + ek)] := {Br('MBit', 'ek')} ;

END_FOR ;
"""
    # ---- timers
    acc, dn, run = I("tacc"), B("tdn"), B("trun")
    pre, pre2 = I("pre"), I("pre2")
    timers = f"""
(* ============================================================ *)
(* §L2e - TIMERS T1..T16 (base 100 ms, presets en decimas)      *)
(* CFG[{S.O_TIMER} + 5k]: Tipo, Entrada, Reset, Preset, Preset2         *)
(*   1 TON     : activo si la entrada sigue activa Preset        *)
(*   2 TOF     : activo con la entrada y Preset despues de caer  *)
(*   3 PULSO   : flanco de la entrada -> activo durante Preset   *)
(*   4 RETARDO : flanco -> activo al cumplir Preset y se queda   *)
(*               hasta Reset o el siguiente flanco               *)
(*   5 TONR    : acumula mientras la entrada esta activa (no se  *)
(*               borra al soltar); activo al llegar a Preset     *)
(*   6 INTERMITENTE: mientras la entrada, Preset encendido /     *)
(*               Preset2 apagado                                 *)
(* Reset, tipo 0 o cualquier paro general -> acumulado en 0.     *)
(* ============================================================ *)

FOR ek := 0 TO {S.N_TIMER - 1} DO

    {I('base')} := {S.O_TIMER} + ek * {S.TIMER_W} ;
    {I('typ')} := {ACT(I('base'))} ;
    {pre} := {ACT(I('base') + ' + 3')} ;
    {pre2} := {ACT(I('base') + ' + 4')} ;
{rd(B('vin'), ACT(I('base') + ' + 1'), 4)}
{rd(B('vrst'), ACT(I('base') + ' + 2'), 4)}

    {B('edge')} := {B('vin')} AND NOT {Br('TPrevIn', 'ek')} AND NOT {ef} ;
    {Br('TPrevIn', 'ek')} := {B('vin')} ;
    {acc} := {Ir('TAcc', 'ek')} ;
    {dn} := {Br('TDn', 'ek')} ;
    {run} := {Br('TRun', 'ek')} ;

    IF {B('vrst')} OR ({I('typ')} = 0) OR {B('GenStop7')} THEN

        {acc} := 0 ;
        {dn} := FALSE ;
        {run} := FALSE ;

    ELSIF {I('typ')} = 1 THEN

        IF {B('vin')} THEN
            IF {p100} AND ({acc} < {pre}) THEN
                {acc} := {acc} + 1 ;
            END_IF ;
            {dn} := ({acc} >= {pre}) ;
        ELSE
            {acc} := 0 ;
            {dn} := FALSE ;
        END_IF ;

    ELSIF {I('typ')} = 2 THEN

        IF {B('vin')} THEN
            {acc} := 0 ;
            {dn} := TRUE ;
            {run} := TRUE ;
        ELSIF {run} THEN
            IF {p100} AND ({acc} < {pre}) THEN
                {acc} := {acc} + 1 ;
            END_IF ;
            IF {acc} >= {pre} THEN
                {run} := FALSE ;
                {dn} := FALSE ;
            END_IF ;
        ELSE
            {dn} := FALSE ;
        END_IF ;

    ELSIF {I('typ')} = 3 THEN

        IF {B('edge')} AND NOT {run} THEN
            {run} := TRUE ;
            {acc} := 0 ;
        END_IF ;
        IF {run} THEN
            IF {p100} AND ({acc} < {pre}) THEN
                {acc} := {acc} + 1 ;
            END_IF ;
            IF {acc} >= {pre} THEN
                {run} := FALSE ;
                {dn} := FALSE ;
            ELSE
                {dn} := TRUE ;
            END_IF ;
        ELSE
            {dn} := FALSE ;
        END_IF ;

    ELSIF {I('typ')} = 4 THEN

        IF {B('edge')} THEN
            {run} := TRUE ;
            {acc} := 0 ;
            {dn} := FALSE ;
        END_IF ;
        IF {run} THEN
            IF {p100} AND ({acc} < {pre}) THEN
                {acc} := {acc} + 1 ;
            END_IF ;
            IF {acc} >= {pre} THEN
                {run} := FALSE ;
                {dn} := TRUE ;
            END_IF ;
        END_IF ;

    ELSIF {I('typ')} = 5 THEN

        IF {B('vin')} AND {p100} AND ({acc} < {pre}) THEN
            {acc} := {acc} + 1 ;
        END_IF ;
        {dn} := ({acc} >= {pre}) ;

    ELSIF {I('typ')} = 6 THEN

        IF {B('vin')} THEN
            IF {B('edge')} THEN
                {run} := TRUE ;
                {acc} := 0 ;
            END_IF ;
            IF {p100} THEN
                {acc} := {acc} + 1 ;
            END_IF ;
            IF {run} THEN
                IF {acc} >= {pre} THEN
                    {run} := FALSE ;
                    {acc} := 0 ;
                END_IF ;
            ELSIF {acc} >= {pre2} THEN
                {run} := TRUE ;
                {acc} := 0 ;
            END_IF ;
            {dn} := {run} ;
        ELSE
            {acc} := 0 ;
            {run} := FALSE ;
            {dn} := FALSE ;
        END_IF ;

    END_IF ;

    {Ir('TAcc', 'ek')} := {acc} ;
    {Br('TDn', 'ek')} := {dn} ;
    {Br('TRun', 'ek')} := {run} ;
    LSIG[ANY_TO_DINT({S.SIG_TIMER} + ek)] := {dn} ;

END_FOR ;
"""
    # ---- contadores
    cacc, cdn = Ir("CAcc", "ek"), Br("CtDn", "ek")
    contadores = f"""
(* ============================================================ *)
(* §L2f - CONTADORES CT1..CT16                                  *)
(* CFG[{S.O_COUNT} + 5k]: Sube, Baja, Reset, Preset, Modo                *)
(*   Cuenta FLANCOS de subida de cualquier senal.               *)
(*   Modo 0 una vez : activo al llegar a Preset, hasta Reset    *)
(*   Modo 1 ciclico : pulso de un scan cada Preset y vuelve a 0 *)
(*   Modo 2 nivel   : activo mientras la cuenta >= Preset       *)
(* Los paros generales NO borran la cuenta (igual que en v5).   *)
(* ============================================================ *)

FOR ek := 0 TO {S.N_COUNT - 1} DO

    {I('base')} := {S.O_COUNT} + ek * {S.COUNT_W} ;
    {pre} := {ACT(I('base') + ' + 3')} ;
    {I('mode')} := {ACT(I('base') + ' + 4')} ;
{rd(B('vin'), ACT(I('base')), 4)}
{rd(B('vdn'), ACT(I('base') + ' + 1'), 4)}
{rd(B('vrst'), ACT(I('base') + ' + 2'), 4)}

    {B('edge')} := {B('vin')} AND NOT {Br('CtUpPrev', 'ek')} AND NOT {ef} ;
    {B('res')} := {B('vdn')} AND NOT {Br('CtDnPrev', 'ek')} AND NOT {ef} ;
    {Br('CtUpPrev', 'ek')} := {B('vin')} ;
    {Br('CtDnPrev', 'ek')} := {B('vdn')} ;

    IF {B('vrst')} OR (({ACT(I('base'))} = 0) AND ({ACT(I('base') + ' + 1')} = 0)) THEN

        {cacc} := 0 ;
        {cdn} := FALSE ;

    ELSE

        IF {I('mode')} = 1 THEN
            {cdn} := FALSE ;
        END_IF ;
        IF {B('edge')} AND ({cacc} < 32767) THEN
            {cacc} := {cacc} + 1 ;
        END_IF ;
        IF {B('res')} AND ({cacc} > 0) THEN
            {cacc} := {cacc} - 1 ;
        END_IF ;

        IF {I('mode')} = 0 THEN
            IF ({pre} > 0) AND ({cacc} >= {pre}) THEN
                {cdn} := TRUE ;
            END_IF ;
        ELSIF {I('mode')} = 1 THEN
            IF ({pre} > 0) AND ({cacc} >= {pre}) THEN
                {cdn} := TRUE ;
                {cacc} := 0 ;
            END_IF ;
        ELSE
            {cdn} := ({pre} > 0) AND ({cacc} >= {pre}) ;
        END_IF ;

    END_IF ;

    LSIG[ANY_TO_DINT({S.SIG_COUNT} + ek)] := {cdn} ;

END_FOR ;
"""
    # ---- secuencias
    sb, ib, bb, gb, n, st, tgt = I("sb"), I("ib"), I("bb"), I("gb"), I("n"), I("st"), I("tgt")
    LIb = LI_RANGOS["SeqI"][0]
    LBb = LB_RANGOS["SeqB"][0]

    def SI(campo):
        return f"LI[ANY_TO_DINT({ib} + {SQI[campo]})]"

    def SB(campo):
        return f"LB[ANY_TO_DINT({bb} + {SQB[campo]})]"
    secuencias = f"""
(* ============================================================ *)
(* §L2g - SECUENCIAS SQ1 y SQ2 (maquinas de etapas)             *)
(* Cabecera CFG[{S.O_SEQ} + {S.SEQ_W}k]: Inicio, Paro, Reset, Pausa, Modo      *)
(*   (0 una vez, 1 infinita, 2 repetir), Repeticiones, Pasos,   *)
(*   Exclusiva, PausaApaga.                                     *)
(* Paso i (CFG[cabecera + {S.SEQ_HDR} + {S.STEP_W}(i-1)]): Salidas (bits Q1..Q12),*)
(*   Motor (b0 marcha b1 paro b2 dir1 b3 dir2 b4 invertir al    *)
(*   entrar), Avanza (0 tiempo 1 senal 2 tiempo Y senal 3 senal *)
(*   O tiempo 4 flanco), Senal, Tiempo, Siguiente (0 = el que   *)
(*   sigue, 1..16, {S.NEXT_FIN} = fin), SenalAlterna, IrA (rama: si la     *)
(*   senal alterna se activa, salta a IrA antes que nada).      *)
(* Inicio por FLANCO; Paro y Reset por nivel; un paro general   *)
(* aborta la secuencia y no vuelve sola al soltarlo.            *)
(* ============================================================ *)

{B('seqRun')} := FALSE ;
{B('seqStop')} := FALSE ;
{B('seqDir1')} := FALSE ;
{B('seqDir2')} := FALSE ;
{B('seqTog')} := FALSE ;
{B('seqOwnMot')} := FALSE ;

FOR ej := 0 TO {S.N_SEQ - 1} DO

    {sb} := {S.O_SEQ} + ej * {S.SEQ_W} ;
    {ib} := {LIb} + ej * {SEQI_W} ;
    {bb} := {LBb} + ej * {SEQB_W} ;
    {gb} := {S.SIG_SEQ} + ej * {S.SEQ_SIG_W} ;
    {n} := {ACT(f'{sb} + {S.SQ_NSTEPS}')} ;

{rd(B('sStart'), ACT(sb), 4)}
{rd(B('sStop'), ACT(f'{sb} + {S.SQ_STOP}'), 4)}
{rd(B('sRst'), ACT(f'{sb} + {S.SQ_RESET}'), 4)}
{rd(B('sPause'), ACT(f'{sb} + {S.SQ_PAUSE}'), 4)}

    {B('sRise')} := {B('sStart')} AND NOT {SB('StartPrev')} AND NOT {ef} ;
    {SB('StartPrev')} := {B('sStart')} ;
    {SB('CyclePulse')} := FALSE ;
    {SB('StepEntry')} := FALSE ;

    IF ({n} = 0) OR {ef} OR {B('GenStop7')} OR {B('sStop')} OR {B('sRst')} THEN

        {SB('Active')} := FALSE ;
        {SB('Paused')} := FALSE ;
        {SB('Done')} := FALSE ;
        {SI('Step')} := 0 ;
        {SI('Acc')} := 0 ;
        IF NOT {B('sStop')} OR {B('sRst')} THEN
            {SI('Cycles')} := 0 ;
        END_IF ;

    ELSIF NOT {SB('Active')} THEN

        IF {B('sRise')} THEN
            {SB('Active')} := TRUE ;
            {SB('Done')} := FALSE ;
            {SI('Step')} := 1 ;
            {SI('Acc')} := 0 ;
            {SI('Cycles')} := 0 ;
            {SB('StepEntry')} := TRUE ;
        END_IF ;

    ELSE

        {SB('Paused')} := {B('sPause')} ;

        IF NOT {B('sPause')} THEN

            IF {p100} AND ({SI('Acc')} < 32767) THEN
                {SI('Acc')} := {SI('Acc')} + 1 ;
            END_IF ;

            {st} := {sb} + {S.SEQ_HDR} + ({SI('Step')} - 1) * {S.STEP_W} ;
            {pre} := {ACT(f'{st} + {S.ST_TIME}')} ;
{rd(B('tsig'), ACT(f'{st} + {S.ST_TSIG}'), 12)}

            {B('fire')} := FALSE ;
            {tgt} := 0 ;

            IF {ACT(f'{st} + {S.ST_ALTSIG}')} <> 0 THEN
{rd(B('res'), ACT(f'{st} + {S.ST_ALTSIG}'), 16)}
                IF {B('res')} THEN
                    {B('fire')} := TRUE ;
                    {tgt} := {ACT(f'{st} + {S.ST_ALTNEXT}')} ;
                END_IF ;
            END_IF ;

            IF NOT {B('fire')} THEN
                {B('timeOk')} := ({SI('Acc')} >= {pre}) ;
                {B('edge')} := {B('tsig')} AND NOT {SB('TransPrev')} ;
                CASE {ACT(f'{st} + {S.ST_TMODE}')} OF
                    0: {B('fire')} := {B('timeOk')} ;
                    1: {B('fire')} := {B('tsig')} ;
                    2: {B('fire')} := {B('timeOk')} AND {B('tsig')} ;
                    3: {B('fire')} := {B('tsig')} OR (({pre} > 0) AND {B('timeOk')}) ;
                    4: {B('fire')} := {B('edge')} ;
                ELSE
                    {B('fire')} := FALSE ;
                END_CASE ;
                {tgt} := {ACT(f'{st} + {S.ST_NEXT}')} ;
            END_IF ;

            {SB('TransPrev')} := {B('tsig')} ;

            IF {B('fire')} THEN

                IF {tgt} <= 0 THEN
                    {tgt} := {SI('Step')} + 1 ;
                END_IF ;

                IF ({tgt} = {S.NEXT_FIN}) OR ({tgt} > {n}) THEN
                    (* fin de una vuelta completa *)
                    IF {SI('Cycles')} < 32767 THEN
                        {SI('Cycles')} := {SI('Cycles')} + 1 ;
                    END_IF ;
                    {SB('CyclePulse')} := TRUE ;
                    IF {tgt} = {S.NEXT_FIN} THEN
                        {tgt} := 0 ;
                    ELSIF {ACT(f'{sb} + {S.SQ_MODE}')} = 1 THEN
                        {tgt} := 1 ;
                    ELSIF ({ACT(f'{sb} + {S.SQ_MODE}')} = 2) AND ({SI('Cycles')} < {ACT(f'{sb} + {S.SQ_REPEAT}')}) THEN
                        {tgt} := 1 ;
                    ELSE
                        {tgt} := 0 ;
                    END_IF ;
                END_IF ;

                IF {tgt} = 0 THEN
                    {SB('Active')} := FALSE ;
                    {SB('Done')} := TRUE ;
                    {SI('Step')} := 0 ;
                ELSE
                    {SI('Step')} := {tgt} ;
                    {SB('StepEntry')} := TRUE ;
                END_IF ;
                {SI('Acc')} := 0 ;

            END_IF ;

        END_IF ;

    END_IF ;

    (* Al entrar a un paso, una senal que YA estaba activa no      *)
    (* cuenta como flanco: "flanco" espera un evento NUEVO.        *)
    IF {SB('StepEntry')} THEN
        {st} := {sb} + {S.SEQ_HDR} + ({SI('Step')} - 1) * {S.STEP_W} ;
{rd(B('tsig'), ACT(f'{st} + {S.ST_TSIG}'), 8)}
        {SB('TransPrev')} := {B('tsig')} ;
    END_IF ;

    (* Lo que pide el paso actual (salidas y motor). *)
    {B('contrib')} := {SB('Active')} AND NOT ({SB('Paused')} AND {SB('PauseOff')}) ;
    IF {B('contrib')} THEN
        {st} := {sb} + {S.SEQ_HDR} + ({SI('Step')} - 1) * {S.STEP_W} ;
        {SI('CurOut')} := {ACT(f'{st} + {S.ST_OUT}')} ;
        {SI('CurMot')} := {ACT(f'{st} + {S.ST_MOTOR}')} ;
    ELSE
        {SI('CurOut')} := 0 ;
        {SI('CurMot')} := 0 ;
    END_IF ;

{bits(SI('CurOut'), 'SeqCurQ', f'ej * {S.N_OUT}', S.N_OUT, 'em', 4)}

    {I('msk')} := {SI('CurMot')} ;
    IF ({I('msk')} MOD 2) = 1 THEN {B('seqRun')} := TRUE ; END_IF ;
    {I('msk')} := {I('msk')} / 2 ;
    IF ({I('msk')} MOD 2) = 1 THEN {B('seqStop')} := TRUE ; END_IF ;
    {I('msk')} := {I('msk')} / 2 ;
    IF ({I('msk')} MOD 2) = 1 THEN {B('seqDir1')} := TRUE ; END_IF ;
    {I('msk')} := {I('msk')} / 2 ;
    IF ({I('msk')} MOD 2) = 1 THEN {B('seqDir2')} := TRUE ; END_IF ;
    {I('msk')} := {I('msk')} / 2 ;
    IF (({I('msk')} MOD 2) = 1) AND {SB('StepEntry')} THEN {B('seqTog')} := TRUE ; END_IF ;

    IF {SB('Active')} AND {SB('Excl')} AND {Br('SeqUsesMot', 'ej')} THEN
        {B('seqOwnMot')} := TRUE ;
    END_IF ;

    LSIG[ANY_TO_DINT({gb})] := {SB('Active')} ;
    LSIG[ANY_TO_DINT({gb} + 1)] := {SB('Active')} AND {SB('Paused')} ;
    LSIG[ANY_TO_DINT({gb} + 2)] := {SB('Done')} ;
    LSIG[ANY_TO_DINT({gb} + 3)] := {SB('CyclePulse')} ;
    FOR em := 0 TO {S.N_STEPS - 1} DO
        LSIG[ANY_TO_DINT({gb} + 4 + em)] := {SB('Active')} AND ({SI('Step')} = em + 1) ;
    END_FOR ;

END_FOR ;
"""
    # ---- motor
    ms = I("MotorState")
    motor = f"""
(* ============================================================ *)
(* §L2h - MOTOR (VFD): UNA SOLA MAQUINA DE ESTADOS              *)
(*  0 PARADO ---(quiere marcha)---------------> 1 MARCHA        *)
(*  1 MARCHA ---(cambia la direccion pedida)--> 2 FRENANDO      *)
(*  2 FRENANDO -(t >= paro_inversion Y VFD en cero)-> 1         *)
(*  cualquier paro / config no lista ---------> 0               *)
(* CFG[{S.M_RUN}..{S.M_REVTIME}]: Arranque, Paro, Modo (0 enclavado 1 mientras),*)
(*   Pausa, Dir1, Dir2, Invertir, ParoInversion (0.1 s, >= 1 s) *)
(* "mientras": tras un paro hay que soltar el arranque antes de *)
(* volver a moverse (RunArmed): nunca arranca solo.             *)
(* ============================================================ *)

{rd(B('sigRun'), ACT(S.M_RUN))}
{rd(B('sigStop'), ACT(S.M_STOP))}
{rd(B('sigPause'), ACT(S.M_PAUSE))}
{rd(B('sigD1'), ACT(S.M_DIR1))}
{rd(B('sigD2'), ACT(S.M_DIR2))}
{rd(B('sigTog'), ACT(S.M_TOGGLE))}

{B('runRise')} := {B('sigRun')} AND NOT {B('RunPrev')} AND NOT {ef} ;
{B('fwdRise')} := {B('sigD1')} AND NOT {B('FwdPrev')} AND NOT {ef} ;
{B('revRise')} := {B('sigD2')} AND NOT {B('RevPrev')} AND NOT {ef} ;
{B('togRise')} := {B('sigTog')} AND NOT {B('TogPrev')} AND NOT {ef} ;
{B('RunPrev')} := {B('sigRun')} ;
{B('FwdPrev')} := {B('sigD1')} ;
{B('RevPrev')} := {B('sigD2')} ;
{B('TogPrev')} := {B('sigTog')} ;

{B('MotorOK')} := MotionCfgPresent AND CfgReady AND CfgValid AND
                (InitSeqState = 0) AND NOT GenStop ;

IF (NOT {B('MotorOK')}) OR {ef} THEN
    {B('RunArmed')} := FALSE ;
ELSIF NOT {B('sigRun')} THEN
    {B('RunArmed')} := TRUE ;
END_IF ;

IF NOT {B('MotorOK')} THEN
    {B('RunLatch')} := FALSE ;
ELSIF {ACT(S.M_MODE)} = 1 THEN
    {B('RunLatch')} := {B('sigRun')} AND {B('RunArmed')} AND NOT {B('sigStop')} ;
ELSIF {B('sigStop')} THEN
    {B('RunLatch')} := FALSE ;
ELSIF {B('runRise')} THEN
    {B('RunLatch')} := TRUE ;
END_IF ;

IF {B('fwdRise')} OR {B('seqDir1')} THEN
    {I('DirTarget')} := 1 ;
END_IF ;
IF {B('revRise')} OR {B('seqDir2')} THEN
    {I('DirTarget')} := 2 ;
END_IF ;
IF {B('togRise')} OR {B('seqTog')} THEN
    IF {I('DirTarget')} = 1 THEN {I('DirTarget')} := 2 ; ELSE {I('DirTarget')} := 1 ; END_IF ;
END_IF ;

IF {B('seqOwnMot')} THEN
    {B('WantRun')} := {B('seqRun')} AND NOT {B('seqStop')} ;
ELSE
    {B('WantRun')} := ({B('RunLatch')} OR {B('seqRun')}) AND NOT {B('seqStop')} ;
END_IF ;

IF {B('sigPause')} OR {B('sigStop')} OR NOT {B('MotorOK')} THEN
    {B('WantRun')} := FALSE ;
END_IF ;

IF {B('HardStop7')} THEN
    {I('StopReason7')} := 1 ;
ELSIF {B('SoftStop7')} THEN
    {I('StopReason7')} := 2 ;
ELSIF NOT {B('MotorOK')} THEN
    {I('StopReason7')} := 5 ;
ELSIF {B('sigStop')} THEN
    {I('StopReason7')} := 3 ;
ELSIF {B('sigPause')} THEN
    {I('StopReason7')} := 4 ;
ELSIF {B('seqStop')} THEN
    {I('StopReason7')} := 6 ;
ELSE
    {I('StopReason7')} := 0 ;
END_IF ;

{I('RevT')} := {ACT(S.M_REVTIME)} ;
IF {I('RevT')} < {S.REV_TIME_MIN} THEN
    {I('RevT')} := {S.REV_TIME_MIN} ;
END_IF ;

IF (NOT {B('MotorOK')}) AND ({ms} <> 0) THEN
    {ms} := 0 ;
    {I('BrakeAcc')} := 0 ;
END_IF ;

IF {p100} AND ({ms} <> 1) AND ({I('BrakeAcc')} < 32767) THEN
    {I('BrakeAcc')} := {I('BrakeAcc')} + 1 ;
END_IF ;

{B('BrakeOk')} := ({I('BrakeAcc')} >= {I('RevT')}) AND
               (({I('SpeedAbs')} < 50) OR ({I('BrakeAcc')} >= {I('RevT')} + 50)) ;

IF {ms} = 0 THEN

    IF ({I('DirTarget')} <> {I('DirActual')}) AND {B('BrakeOk')} THEN
        {I('DirActual')} := {I('DirTarget')} ;
    END_IF ;
    IF {B('WantRun')} AND ({I('DirTarget')} = {I('DirActual')}) THEN
        {ms} := 1 ;
    END_IF ;

ELSIF {ms} = 1 THEN

    IF NOT {B('WantRun')} THEN
        {ms} := 0 ;
        {I('BrakeAcc')} := 0 ;
    ELSIF {I('DirTarget')} <> {I('DirActual')} THEN
        {ms} := 2 ;
        {I('BrakeAcc')} := 0 ;
    END_IF ;

ELSIF {ms} = 2 THEN

    IF {B('BrakeOk')} THEN
        {I('DirActual')} := {I('DirTarget')} ;
        IF {B('WantRun')} THEN
            {ms} := 1 ;
        ELSE
            {ms} := 0 ;
        END_IF ;
    ELSIF NOT {B('WantRun')} THEN
        {ms} := 0 ;
    END_IF ;

ELSE

    {ms} := 0 ;

END_IF ;

IF {ms} = 1 THEN
    IF {I('DirActual')} = 2 THEN
        VFD_Control := 34 ;
    ELSE
        VFD_Control := 18 ;
    END_IF ;
    BandRunning := TRUE ;
ELSE
    VFD_Control := 1 ;
    BandRunning := FALSE ;
END_IF ;

BandEnable := {B('RunLatch')} ;

IF NOT GenStop THEN
    StopReason := 0 ;
END_IF ;
"""
    # ---- salidas
    pares = S.HW_BANDA["pares_excluyentes"]
    (a1, b1, _), (a2, b2, _) = pares
    pl_lines = []
    for k, (up, dn, nombre) in enumerate(pares):
        iu, idn = int(up[1:]) - 1, int(dn[1:]) - 1
        pl_lines.append((k, iu, idn, nombre, up, dn))
    pl_code = []
    for k, iu, idn, nombre, up, dn in pl_lines:
        man = "Pluma1Cmd" if k == 0 else "Pluma2Cmd"
        f_both, f_to = (1, 2) if k == 0 else (4, 8)
        cmd, prev, accp, to = Ir("PlCmd", k), Ir("PlPrev", k), Ir("PlAcc", k), Br("PlTimeout", k)
        pl_code.append(f"""
(* ---------- {nombre.upper()}: {up} sube / {dn} baja ---------- *)
{B('pUp')} := {Br('OutReq', iu)} ;
{B('pDn')} := {Br('OutReq', idn)} ;
IF {B('pUp')} AND {B('pDn')} THEN
    {I('q')} := 0 ;
    {I('PlumaFault')} := {I('PlumaFault')} + {f_both} ;
ELSIF {B('pUp')} THEN
    {I('q')} := 1 ;
ELSIF {B('pDn')} THEN
    {I('q')} := 2 ;
ELSE
    {I('q')} := 0 ;
    (* comando manual del panel (R60/R61) si la logica no pide nada *)
    IF NOT {B('GenStop7')} AND (({man} = 1) OR ({man} = 2)) THEN
        {I('q')} := {man} ;
    END_IF ;
END_IF ;
IF {I('q')} <> {prev} THEN
    {accp} := 0 ;
    {to} := FALSE ;
END_IF ;
{prev} := {I('q')} ;
IF {I('q')} <> 0 THEN
    IF {p100} AND ({accp} < 32767) THEN
        {accp} := {accp} + 1 ;
    END_IF ;
    IF ({ACT(S.H_PLUMA_MAX)} > 0) AND ({accp} >= {ACT(S.H_PLUMA_MAX)}) THEN
        {to} := TRUE ;
    END_IF ;
END_IF ;
IF {to} THEN
    {I('q')} := 0 ;
    {I('PlumaFault')} := {I('PlumaFault')} + {f_to} ;
END_IF ;
{cmd} := {I('q')} ;
{Br('OutFinal', iu)} := ({I('q')} = 1) ;
{Br('OutFinal', idn)} := ({I('q')} = 2) ;""")
    salidas = f"""
(* ============================================================ *)
(* §L2i - SALIDAS                                               *)
(* CFG[{S.O_OUT}..{S.O_OUT + S.N_OUT - 1}]: senal que enciende Q1..Q12.                  *)
(* Se suman (OR) las salidas del paso activo de cada secuencia; *)
(* una secuencia EXCLUSIVA activa manda sola sobre las salidas  *)
(* que usa. Solo se escriben las salidas cableadas (§L0).       *)
(* Paro de emergencia I3: solo las lamparas de luces_paro.      *)
(* Paro por software: se apagan las salidas que no son lampara. *)
(* Plumas: subir Y bajar a la vez -> ambas apagadas (falla).    *)
(* ============================================================ *)

FOR ek := 0 TO {S.N_OUT - 1} DO

{rd(B('vin'), ACT(f'{S.O_OUT} + ek'), 4)}
    {B('seqv')} := FALSE ;
    {B('owned')} := FALSE ;
    FOR ej := 0 TO {S.N_SEQ - 1} DO
        IF {Br('SeqCurQ', f'ej * {S.N_OUT} + ek')} THEN
            {B('seqv')} := TRUE ;
        END_IF ;
        IF {Br('SeqB', f"ej * {SEQB_W} + {SQB['Active']}")} AND {Br('SeqB', f"ej * {SEQB_W} + {SQB['Excl']}")}
           AND {Br('SeqUsesQ', f'ej * {S.N_OUT} + ek')} THEN
            {B('owned')} := TRUE ;
        END_IF ;
    END_FOR ;

    IF {B('owned')} THEN
        {Br('OutReq', 'ek')} := {B('seqv')} ;
    ELSE
        {Br('OutReq', 'ek')} := {B('vin')} OR {B('seqv')} ;
    END_IF ;
    {Br('OutReq', 'ek')} := {Br('OutReq', 'ek')} AND {Br('HwQ', 'ek')} ;

    IF {B('HardStop7')} THEN
        {Br('OutReq', 'ek')} := {Br('EstopQ', 'ek')} AND {Br('HwLamp', 'ek')} AND {Br('HwQ', 'ek')} ;
    ELSIF {B('SoftStop7')} THEN
        {Br('OutReq', 'ek')} := {Br('OutReq', 'ek')} AND {Br('HwLamp', 'ek')} ;
    END_IF ;

    {Br('OutFinal', 'ek')} := {Br('OutReq', 'ek')} ;

END_FOR ;

{I('PlumaFault')} := 0 ;
{chr(10).join(pl_code)}

(* ---------- salidas fisicas de esta banda ---------- *)
PhQ3 := {Br('OutFinal', 2)} ;
PhQ4 := {Br('OutFinal', 3)} ;
PhQ5 := {Br('OutFinal', 4)} ;
PhQ6 := {Br('OutFinal', 5)} ;
PhQ7 := {Br('OutFinal', 6)} ;
PhQ8 := {Br('OutFinal', 7)} ;
PhQ9 := {Br('OutFinal', 8)} ;
(* Q1, Q2 y Q10..Q12 no existen en la banda: no se escriben.  *)

Pluma1Status := {Ir('PlCmd', 0)} ;
Pluma2Status := {Ir('PlCmd', 1)} ;

tVerde    := PhQ3 ;
tAmarilla := PhQ4 ;
tRoja     := PhQ5 ;

{B('FirstCycle')} := {ef} ;
{B('EngResetReq')} := FALSE ;
"""
    return reinicio + entradas + condiciones + memorias + timers + contadores + secuencias + motor + salidas


def seccion_L3():
    S = lm

    assert [S.S_COND, S.S_MEM, S.S_TIMERS, S.S_COUNTS] == [20, 22, 24, 26]
    assert [S.SIG_COND, S.SIG_MEM, S.SIG_TIMER, S.SIG_COUNT] == [40, 56, 72, 88]
    masks = f"""
(* C1..C16, M1..M16, T1..T16 y CT1..CT16: 8 bits por palabra. *)
FOR ej := 0 TO 7 DO
    {I('cnt')} := 0 ;
    {I('msk')} := 1 ;
    FOR ek := 0 TO 7 DO
        IF LSIG[ANY_TO_DINT({S.SIG_COND} + ej * 8 + ek)] THEN {I('cnt')} := {I('cnt')} + {I('msk')} ; END_IF ;
        {I('msk')} := {I('msk')} * 2 ;
    END_FOR ;
    LSTA[ANY_TO_DINT({S.S_COND} + ej)] := {I('cnt')} ;
END_FOR ;"""
    LIb, LBb = LI_RANGOS["SeqI"][0], LB_RANGOS["SeqB"][0]
    return f"""
(* ============================================================ *)
(* §L3 - ESTADO DEL MOTOR v7 -> LSTA (%R{S.STATUS_BASE}..%R{S.STATUS_BASE + S.STATUS_LEN - 1}, solo lectura)  *)
(* Se escribe SIEMPRE (tambien en modo v5): LSTA[0] = {S.ENGINE_VERSION} y       *)
(* LSTA[1] = {S.MAGIC} identifican este programa ante el backend.   *)
(* ============================================================ *)

LSTA[{S.S_VERSION}] := {S.ENGINE_VERSION} ;
LSTA[{S.S_MAGIC}] := {S.MAGIC} ;
LSTA[{S.S_HEARTBEAT}] := {I('Heartbeat')} ;
LSTA[{S.S_SCANS}] := {I('ScansPerSec')} ;
LSTA[{S.S_TX}] := {I('TxState')} ;
LSTA[{S.S_ACK}] := {I('CmdPrev')} ;
LSTA[{S.S_RESULT}] := {I('LastResult')} ;
LSTA[{S.S_ERR}] := {I('ErrCode')} ;
LSTA[{S.S_ERRFIELD}] := {I('ErrField')} ;
LSTA[{S.S_ACTIVE_ID}] := {I('ActiveId')} ;
LSTA[{S.S_ACTIVE_CHK}] := {I('ActiveChk')} ;
IF {B('V7Active')} THEN LSTA[{S.S_ENGINE}] := 1 ; ELSE LSTA[{S.S_ENGINE}] := 0 ; END_IF ;
IF CfgReady THEN LSTA[{S.S_CFGREADY}] := 1 ; ELSE LSTA[{S.S_CFGREADY}] := 0 ; END_IF ;
LSTA[{S.S_MOTOR}] := {I('MotorState')} ;
LSTA[{S.S_DIR}] := {I('DirActual')} ;
LSTA[{S.S_DIR_TARGET}] := {I('DirTarget')} ;
IF {B('RunLatch')} THEN LSTA[{S.S_RUNLATCH}] := 1 ; ELSE LSTA[{S.S_RUNLATCH}] := 0 ; END_IF ;
LSTA[{S.S_STOPREASON}] := {I('StopReason7')} ;

{I('cnt')} := 0 ;
{I('msk')} := 1 ;
FOR ek := 0 TO {S.N_IN - 1} DO
    IF {B('V7Active')} THEN
        {B('vin')} := {Br('DebIn', 'ek')} ;
    ELSE
        {B('vin')} := {Br('RawIn', 'ek')} ;
    END_IF ;
    IF {B('vin')} THEN {I('cnt')} := {I('cnt')} + {I('msk')} ; END_IF ;
    {I('msk')} := {I('msk')} * 2 ;
END_FOR ;
LSTA[{S.S_IN}] := {I('cnt')} ;

{I('cnt')} := 0 ;
IF PhQ3 THEN {I('cnt')} := {I('cnt')} + 4 ; END_IF ;
IF PhQ4 THEN {I('cnt')} := {I('cnt')} + 8 ; END_IF ;
IF PhQ5 THEN {I('cnt')} := {I('cnt')} + 16 ; END_IF ;
IF PhQ6 THEN {I('cnt')} := {I('cnt')} + 32 ; END_IF ;
IF PhQ7 THEN {I('cnt')} := {I('cnt')} + 64 ; END_IF ;
IF PhQ8 THEN {I('cnt')} := {I('cnt')} + 128 ; END_IF ;
IF PhQ9 THEN {I('cnt')} := {I('cnt')} + 256 ; END_IF ;
LSTA[{S.S_OUT}] := {I('cnt')} ;
{masks}

FOR ej := 0 TO {S.N_SEQ - 1} DO
    {I('ib')} := {LIb} + ej * {SEQI_W} ;
    {I('bb')} := {LBb} + ej * {SEQB_W} ;
    {I('base')} := {S.S_SEQ} + ej * {S.S_SEQ_W} ;
    IF LB[ANY_TO_DINT({I('bb')} + {SQB['Active']})] AND LB[ANY_TO_DINT({I('bb')} + {SQB['Paused']})] THEN
        LSTA[ANY_TO_DINT({I('base')})] := 2 ;
    ELSIF LB[ANY_TO_DINT({I('bb')} + {SQB['Active']})] THEN
        LSTA[ANY_TO_DINT({I('base')})] := 1 ;
    ELSIF LB[ANY_TO_DINT({I('bb')} + {SQB['Done']})] THEN
        LSTA[ANY_TO_DINT({I('base')})] := 3 ;
    ELSE
        LSTA[ANY_TO_DINT({I('base')})] := 0 ;
    END_IF ;
    LSTA[ANY_TO_DINT({I('base')} + 1)] := LI[ANY_TO_DINT({I('ib')} + {SQI['Step']})] ;
    LSTA[ANY_TO_DINT({I('base')} + 2)] := LI[ANY_TO_DINT({I('ib')} + {SQI['Acc']})] ;
    LSTA[ANY_TO_DINT({I('base')} + 3)] := LI[ANY_TO_DINT({I('ib')} + {SQI['Cycles']})] ;
END_FOR ;

FOR ek := 0 TO 15 DO
    LSTA[ANY_TO_DINT({S.S_TACC} + ek)] := {Ir('TAcc', 'ek')} ;
    LSTA[ANY_TO_DINT({S.S_CACC} + ek)] := {Ir('CAcc', 'ek')} ;
END_FOR ;

LSTA[{S.S_PLFAULT}] := {I('PlumaFault')} ;
LSTA[{S.S_BRAKE}] := {I('BrakeAcc')} ;

{I('cnt')} := 0 ;
{I('lo')} := 0 ;
{I('msk')} := 1 ;
FOR ek := 0 TO {S.N_OUT - 1} DO
    IF {Br('HwQ', 'ek')} THEN {I('cnt')} := {I('cnt')} + {I('msk')} ; END_IF ;
    IF {Br('HwLamp', 'ek')} THEN {I('lo')} := {I('lo')} + {I('msk')} ; END_IF ;
    {I('msk')} := {I('msk')} * 2 ;
END_FOR ;
LSTA[{S.S_HW_OUT}] := {I('cnt')} ;
LSTA[{S.S_HW_LAMP}] := {I('lo')} ;
LSTA[{S.S_HW_IN}] := {lm.hw_in_mask()} ;    (* I1..I5 cableadas (ver §L0) *)
LSTA[{S.S_RX}] := {I('RxTicks')} ;
LSTA[{S.S_CFGLEN}] := {S.CFG_LEN} ;
LSTA[{S.S_STAGED_ID}] := {I('StagedId')} ;
"""


# ---------------------------------------------------------------------------
# Leyenda y ensamblado
# ---------------------------------------------------------------------------
def leyenda():
    items = [f"LI[{v}]={k}" for k, v in LI_MAP.items()]
    items += [f"LI[{b}..{b + n - 1}]={k}" for k, (b, n) in LI_RANGOS.items()]
    items += [f"LB[{v}]={k}" for k, v in LB_MAP.items()]
    items += [f"LB[{b}..{b + n - 1}]={k}" for k, (b, n) in LB_RANGOS.items()]
    filas, fila = [], ""
    for it in items:
        if len(fila) + len(it) + 2 > 58:
            filas.append(fila)
            fila = ""
        fila += ("  " if fila else "") + it
    filas.append(fila)
    S = lm
    cab = [
        "(* ============================================================ *)",
        "(* PROGRAMA MAESTRO BANDA - HORNER XL4 / CSCAPE ST - v7 LAVO  *)",
        "(* BLOQUE 1 de 2 (STBLOCK1). El motor general esta en el       *)",
        "(* bloque 2 (STBLOCK2 = LAVO_Motor_ST.txt), que se ejecuta     *)",
        "(* despues de este en cada ciclo (limite de 64 KB por bloque). *)",
        "(* Generado por cscape/generar_st_v7.py (no editar a mano:    *)",
        "(* regenerar si cambia lavo_motor.py).                        *)",
        "(*                                                            *)",
        "(* v7 = v5 INTACTO + MOTOR GENERAL configurable por Modbus.   *)",
        "(*   Sin config v7 aplicada -> corre la logica v5 de siempre. *)",
        "(*   Con config v7 aplicada -> el motor general maneja VFD,   *)",
        "(*   torreta y plumas; la logica v5 queda en espera.          *)",
        f"(*   Comandos : LCMD[0..9]   = %R{S.CMD_BASE}..%R{S.CMD_BASE + 9}                 *)",
        f"(*   Espera   : LSTG[0..639] = %R{S.STAGE_BASE}..%R{S.STAGE_BASE + S.CFG_LEN - 1}                 *)",
        f"(*   Estado   : LSTA[0..99]  = %R{S.STATUS_BASE}..%R{S.STATUS_BASE + 99} (LSTA[0] = {S.ENGINE_VERSION})       *)",
        "(* Comparte con v5: §0 arranque seguro, §3 validacion, §4     *)",
        "(* paros (I3 SIEMPRE manda), §6/§7, §8/§9 VFD, §18..§20.      *)",
        "(*                                                            *)",
        "(* TAGS NUEVOS (ver LAVO_Variables_Cscape.txt):               *)",
        f"(*   LCMD INT[10] %R{S.CMD_BASE} · LSTG INT[640] %R{S.STAGE_BASE} · LSTA INT[100] %R{S.STATUS_BASE} *)",
        f"(*   LACT INT[640] %R{S.ACT_BASE} · LPEN INT[640] %R{S.PEN_BASE}                 *)",
        f"(*   LI INT[{LI_LEN}] %R{S.LI_BASE} · LCK DINT[4] %R{S.LD_BASE}                     *)",
        f"(*   LSIG BOOL[160] · LB BOOL[{LB_LEN}] · em INT  (internas, sin tag)  *)",
        "(*   (se conservan Sys100ms %S4, ek, ej)                       *)",
        "(* Posiciones de LI / LB (nombre logico de cada una):          *)",
    ]
    for f in filas:
        cab.append(f"(*   {f:<57}*)")
    cab.append("(* ============================================================ *)")
    return "\n".join(cab) + "\n\n"


def reemplazar(txt, viejo, nuevo, veces=1):
    n = txt.count(viejo)
    if n != veces:
        raise SystemExit(f"Ancla encontrada {n} veces (se esperaban {veces}):\n{viejo[:200]}")
    return txt.replace(viejo, nuevo)


def cortar(txt, desde, hasta, nuevo):
    a = txt.index(desde)
    b = txt.index(hasta, a)
    return txt[:a] + nuevo + txt[b:]


CORTE = "(* @@CORTE_STBLOCK2@@ *)\n"


def cabecera_bloque2():
    return """(* ============================================================ *)
(* PROGRAMA MAESTRO BANDA v7 LAVO - BLOQUE 2 de 2 (STBLOCK2)    *)
(* Se ejecuta en cada ciclo DESPUES de STBLOCK1 (LAVO_Main_ST). *)
(* Contiene el motor general (§L2), el estado (§L3) y §18..§20. *)
(* Usa las mismas variables globales que el bloque 1. Se separo *)
(* porque Cscape limita cada bloque ST a 64 KB de codigo.       *)
(* Generado por cscape/generar_st_v7.py (no editar a mano).     *)
(* ============================================================ *)

"""


def partes() -> tuple:
    """(bloque1, bloque2) para Cscape. Juntos == ST_banda_v7_lavo.txt."""
    t = generar()
    a = t.index(CORTE)
    return t[:a], t[a + len(CORTE):]


def generar() -> str:
    with open(V6, encoding="utf-8") as f:
        t = f.read().replace("\r\n", "\n")

    # Cabecera: leyenda v7 + texto descriptivo v5 (desde "OBJETIVO v5").
    t = leyenda() + t[t.index("(* OBJETIVO v5:"):]

    # §0: arranque seguro del motor v7.
    t = reemplazar(t, "    EB[1]       := Sys100ms ;\n\n    (* v6: el motor configurable arranca en estado seguro *)\n    EB[2]     := TRUE ;\n",
                   f"    {B('Prev100ms')} := Sys100ms ;\n\n"
                   "    (* v7: sin configuracion activa tras energizar (estado    *)\n"
                   "    (* seguro: corre v5 con los registros que dejo el backend) *)\n"
                   f"    {B('EngResetReq')} := TRUE ;\n"
                   f"    {I('CmdPrev')} := LCMD[{lm.C_SEQ}] ;\n"
                   f"    {I('TxState')} := {lm.TX_IDLE} ;\n"
                   f"    {I('ActiveId')} := 0 ;\n"
                   f"    {I('ActiveChk')} := 0 ;\n"
                   f"    {I('ErrCode')} := 0 ;\n"
                   f"    {I('ErrField')} := 0 ;\n"
                   f"    {B('V7Active')} := FALSE ;\n")

    # §1: pulso de 100 ms.
    t = reemplazar(t, "(* v6: pulso de 100 ms (T_100MS = %S4) para timers del motor. *)\nEB[0] := Sys100ms AND NOT EB[1] ;\nEB[1]  := Sys100ms ;",
                   "(* v7: pulso de 100 ms (T_100MS = %S4) para timers del motor. *)\n"
                   f"{B('Pulse100ms')} := Sys100ms AND NOT {B('Prev100ms')} ;\n{B('Prev100ms')} := Sys100ms ;")

    # §L0 + §L1 antes de §3.
    ancla3 = "(* ============================================================ *)\n(* §3 - VALIDACION DE CONFIGURACION"
    t = reemplazar(t, ancla3, seccion_L0() + seccion_L1() + "\n" + ancla3)

    # §3: direccion/frecuencia efectivas.
    a = t.index("(* §3 - VALIDACION DE CONFIGURACION")
    b = t.index("(* Fuentes de paro *)", a)
    bloque = t[a:b]
    bloque = bloque.replace("(DirCmd <> 0) OR\n    (FreqRequest <> 0) ;",
                            f"({I('EffDir')} <> 0) OR\n    ({I('EffFreq')} <> 0) ;    (* v7: valores efectivos *)")
    bloque = bloque.replace("IF NOT ((DirCmd = 1) OR (DirCmd = 2)) THEN",
                            f"IF NOT (({I('EffDir')} = 1) OR ({I('EffDir')} = 2)) THEN")
    bloque = bloque.replace("IF (FreqRequest <= 0) OR (FreqRequest > 327) THEN",
                            f"IF ({I('EffFreq')} <= 0) OR ({I('EffFreq')} > 327) THEN")
    bloque = bloque.replace("IF (DirCmd <> 0) OR (FreqRequest <> 0) THEN",
                            f"IF ({I('EffDir')} <> 0) OR ({I('EffFreq')} <> 0) THEN")
    if "DirCmd" in bloque or "FreqRequest" in bloque:
        raise SystemExit("§3: quedo una referencia a DirCmd/FreqRequest sin reemplazar")
    t = t[:a] + bloque + t[b:]

    # §3b (validacion del motor v6) -> fuera.
    t = cortar(t, "(* ============================================================ *)\n(* §3b - VALIDACION DEL MOTOR CONFIGURABLE (v6)",
               "(* §4 - PAROS", "(* ============================================================ *)\n\n\n")

    # §4: el paro por software del motor v7 es un paro general.
    t = reemplazar(t, "GenStop :=\n    HardStopActive OR\n    AuxStopActive OR\n    SoftStopActive OR\n    CountSystemStopLatch ;",
                   f"GenStop :=\n    HardStopActive OR\n    AuxStopActive OR\n    SoftStopActive OR\n    CountSystemStopLatch OR\n    {B('SoftStop7')} ;    (* v7: paro por software del motor general *)", veces=2)
    t = reemplazar(t, "ELSIF SoftStopActive THEN\n\n    StopReason := 3 ;\n\nEND_IF ;",
                   f"ELSIF SoftStopActive OR {B('SoftStop7')} THEN\n\n    StopReason := 3 ;\n\nEND_IF ;")

    # §5: I1 solo arranca la logica v5.
    t = reemplazar(t, "(* v6: con EngineMode = 1 el arranque lo decide el motor (EC[1]). *)\nIF (EI[0] = 0) AND",
                   "(* v7: con el motor v7 activo el arranque lo decide su configuracion. *)\n"
                   f"IF NOT {B('V7Active')} AND")

    # §6: aplicar/descargar una config v7 = nueva configuracion.
    t = reemplazar(t, "NewCfgTrigger :=\n    (NewCfgFlag <> 0) AND\n    (NewCfgFlag <> NewCfgPrev) ;",
                   "NewCfgTrigger :=\n    ((NewCfgFlag <> 0) AND\n    (NewCfgFlag <> NewCfgPrev)) OR\n"
                   f"    {B('ApplyPulse')} ;    (* v7: COMMIT aplicado o UNLOAD *)")
    t = reemplazar(t, "(* v6: una configuracion nueva o un reset reinician el motor configurable. *)\nIF NewCfgTrigger OR ResetCmdTrigger THEN\n    EB[2] := TRUE ;",
                   "(* v7: una configuracion nueva o un reset reinician el motor general. *)\nIF NewCfgTrigger OR ResetCmdTrigger THEN\n"
                   f"    {B('EngResetReq')} := TRUE ;")

    # §8 / §9: frecuencia efectiva.
    t = reemplazar(t, "FreqRequest * INT#100 ;", f"{I('EffFreq')} * INT#100 ;", veces=2)

    # Selector de motor.
    t = reemplazar(t, "(* v6: §11..§17 son la logica v5. Solo corren con EngineMode=0. *)\n(* Con EngineMode=1 corre el MOTOR CONFIGURABLE (§E1..§E10).   *)\n"
                      "(* ============================================================ *)\n\nIF EI[0] = 0 THEN",
                   "(* v7: §11..§17 son la logica v5. Solo corren SIN config v7.   *)\n(* Con config v7 aplicada corre el MOTOR GENERAL (§L2).         *)\n"
                   f"(* ============================================================ *)\n\nIF NOT {B('V7Active')} THEN")
    t = reemplazar(t, "    (* v5: la direccion real es la que pide DirCmd. El motor     *)\n    (* configurable queda listo para reiniciarse si se activa.   *)\n    EI[3]   := DirCmd ;\n    EB[2] := TRUE ;",
                   "    (* v5: la direccion real es la que pide DirCmd. El motor     *)\n    (* general queda listo para reiniciarse si se activa.        *)\n"
                   f"    {I('DirActual')} := DirCmd ;\n    {B('EngResetReq')} := TRUE ;")

    # Motor v6 -> motor general v7.
    a = t.index("(* ############################################################ *)\n(* ##  MOTOR CONFIGURABLE v6")
    a = t.rindex("ELSE\n", 0, a)
    b = t.index("(* v6: identificacion y validez del motor (siempre). *)")
    b_fin = t.index("ES[2] := EI[1] ;\n", b) + len("ES[2] := EI[1] ;\n")
    t = (t[:a] + "END_IF ;\n\n" + CORTE + cabecera_bloque2() + f"IF {B('V7Active')} THEN\n\n"
         + seccion_L2() + "\nEND_IF ;\n\n" + seccion_L3() + t[b_fin:])

    # §18: direccion real.
    t = reemplazar(t, "    IF EI[3] = 2 THEN\n\n        BandStatus := 2 ;", f"    IF {I('DirActual')} = 2 THEN\n\n        BandStatus := 2 ;")

    for viejo in ("EI[", "EB[", "EC[", "ES["):
        if viejo in t:
            i = t.index(viejo)
            raise SystemExit(f"Quedo una referencia v6 {viejo}: ...{t[i - 80:i + 40]}...")
    return t


def generar_variables() -> str:
    """LAVO_Variables_Cscape.txt: variables, mapa Modbus e instalacion."""
    S = lm
    R = lambda n: n + 2999          # noqa: E731  %R -> Modbus
    nuevas = [
        # nombre, tipo, dim, tag, modbus, funcion
        ("LCMD", "INT", 10, f"%R{S.CMD_BASE}", "ESCRITURA (backend)",
         "Comandos: [0] CmdSeq, [1] CmdCode, [2] arg, [3..6] APP1..APP4, [7] paro software"),
        ("LSTG", "INT", S.CFG_LEN, f"%R{S.STAGE_BASE}", "ESCRITURA (backend)",
         "Area de ESPERA de la configuracion (640 palabras). El motor nunca la lee directo."),
        ("LSTA", "INT", S.STATUS_LEN, f"%R{S.STATUS_BASE}", "SOLO LECTURA",
         "Estado del motor v7 (version, transaccion, errores, motor, secuencias, timers...)"),
        ("LACT", "INT", S.CFG_LEN, f"%R{S.ACT_BASE}", "SOLO LECTURA",
         "Configuracion ACTIVA (copia validada). El backend la relee para confirmar la carga."),
        ("LPEN", "INT", S.CFG_LEN, f"%R{S.PEN_BASE}", "SOLO LECTURA",
         "Buffer privado de validacion (COMMIT copia LSTG aqui antes de validar)."),
        ("LI", "INT", LI_LEN, f"%R{S.LI_BASE}", "SOLO LECTURA (diagnostico)",
         "Enteros internos del motor (posiciones en la leyenda del ST)."),
        ("LCK", "DINT", 4, f"%R{S.LD_BASE}", "no se usa",
         f"Acumulador DINT del checksum (ocupa %R{S.LD_BASE}..%R{S.LD_BASE + 7})."),
        ("LSIG", "BOOL", S.SIG_TABLE, "(sin tag)", "no",
         "Tabla de senales del motor: LSIG[codigo] (ver mapa de senales)."),
        ("LB", "BOOL", LB_LEN, "(sin tag)", "no", "Booleanos internos del motor (leyenda del ST)."),
        ("em", "INT", None, "(sin tag)", "no", "Contador del tercer ciclo FOR anidado."),
    ]
    L = []
    w = L.append
    w("=" * 78)
    w(" LAVO - PROGRAMA MAESTRO v7 (motor general) - VARIABLES PARA CSCAPE")
    w(" Generado por cscape/generar_st_v7.py junto con LAVO_Main_ST.txt.")
    w(" Si cambias lavo_motor.py, regenera AMBOS archivos.")
    w("=" * 78)
    w("")
    w("Donde se declaran: TODAS en el editor de variables de Cscape, seccion")
    w("Global Variables (igual que las del v5/v6). El texto ST no trae bloques")
    w("VAR ... END_VAR: el programa actual tampoco los usa y es el formato que")
    w("ya se compilo en este proyecto.")
    w("Retentivas: NINGUNA. No pongas ninguna variable v7 en Retain Variables.")
    w("Valor inicial: 0 / FALSE en todas (no hace falta escribir valores")
    w("iniciales). Tras energizar, el motor v7 arranca SIN configuracion activa")
    w("(estado seguro): el backend la vuelve a cargar.")
    w("")
    w("-" * 78)
    w("1. CAMBIOS EN LA TABLA DE VARIABLES")
    w("-" * 78)
    w("ELIMINAR (eran del motor v6, el v7 ya no las usa):")
    w("   EC  INT[260]  %R200      ES  INT[40]  %R460")
    w("   EI  INT[65]   (interna)  EB  BOOL[203] (interna)")
    w("   -> Cscape marcara 'variable no usada' si las dejas; no son error,")
    w("      pero quitarlas libera %R200..%R571 y %T71..%T273.")
    w("CONSERVAR SIN CAMBIOS:")
    w("   todas las variables del v5 (FST_SCN, PhIn1..5, PhQ3..9, DirCmd, ...,")
    w("   R_*, Mon_*, VFD_*) y Sys100ms (BOOL %S4), ek (INT), ej (INT).")
    w("AGREGAR (10):")
    w("")
    w(f"  {'Nombre':<6} {'Tipo':<5} {'Dim':>5}  {'Tag':<10} {'Modbus':<26} Funcion")
    for n, t, d, tag, mb, fn in nuevas:
        w(f"  {n:<6} {t:<5} {('-' if d is None else d):>5}  {tag:<10} {mb:<26} {fn}")
    w("")
    w("  'Dim' = tamano del arreglo (en Cscape: Dim / Array size). Arreglos")
    w("  empezando en 0: ARRAY [0..Dim-1].")
    w("  Las 7 primeras llevan direccion FIJA a proposito: si Cscape las asignara")
    w("  solo, las pondria en %R200..%R499 (el hueco que deja el v6), que el flujo")
    w("  v5 del backend aun escribe. LSIG, LB y em pueden quedar sin tag")
    w("  (Cscape las asigna en %T / %R libres).")
    w("")
    w("  Alcance: global (todas). Retentividad: ninguna. Valor inicial: 0/FALSE.")
    w("")
    w("  Relacion con el Main (LAVO_Main_ST.txt):")
    w("    LCMD  lo lee §L1 (comandos) y §L2b (APP1..APP4, paro software).")
    w("    LSTG  lo lee §L1 solo durante un COMMIT (copia a LPEN).")
    w("    LPEN  lo escribe y valida §L1; al aprobarse se copia a LACT.")
    w("    LACT  lo leen §L1 (direccion/frecuencia efectivas) y todo §L2.")
    w("    LSTA  lo escribe §L3 en CADA scan (tambien en modo v5).")
    w("    LI/LB estado interno de §L0..§L3 (leyenda al inicio del ST).")
    w("    LSIG  tabla de senales: la llenan §L2b..§L2g y la leen todos los bloques.")
    w("    LCK   acumulador del checksum en §L1.  em: ciclos FOR anidados.")
    w("  Variables v5 que usa el motor v7 (ya existen, no se tocan):")
    w("    lee     BtnStart, BtnAux, BtnStop, S1_Det, S2_Det (§2 normaliza NA/NC),")
    w("            Pulse1s, CfgReady, CfgValid, MotionCfgPresent, InitSeqState,")
    w("            GenStop, VFD_SpeedRaw, DirCmd, FreqRequest, Pluma1Cmd, Pluma2Cmd")
    w("    escribe PhQ3..PhQ9, VFD_Control, BandRunning, BandEnable, StopReason,")
    w("            Pluma1Status, Pluma2Status, tVerde, tAmarilla, tRoja")
    w("    §3, §8 y §9 del v5 usan ahora la direccion/frecuencia EFECTIVAS (LI[11],")
    w("    LI[12]): sin config v7 valen DirCmd/FreqRequest, asi que v5 no cambia.")
    w("")
    w("  Rangos verificados LIBRES en ladder_maestro_banda.csp (14 oct 2026): el")
    w("  %R mas alto usado era %R571 (EI). Si agregaste algo despues, revisa en")
    w(f"  Cscape (Program > Register Usage / Cross Reference) que %R{S.CMD_BASE}..%R{S.LD_BASE + 7}")
    w("  no se usen en otra parte (pantallas, Modbus maestro, etc.).")
    w("")
    w("-" * 78)
    w("2. BLOQUE PARA PEGAR (antes de END_VAR de las Global Variables)")
    w("-" * 78)
    w("------------------------- copiar desde aqui -------------------------")
    w("      (*$desc====== MOTOR LAVO v7 =====*)")
    for n, t, d, tag, _mb, _fn in nuevas:
        tipo = f"ARRAY [0..{d - 1}] OF {t}" if d else t
        w(f"   {n} : {tipo} ;")
        if tag.startswith("%"):
            w(f"      (*$tag={tag}*)")
    w("------------------------- copiar hasta aqui -------------------------")
    w("Si el editor no deja pegar texto, crea cada una a mano con la tabla de")
    w("arriba (Nombre, Tipo, Dim, Tag).")
    w("")
    w("-" * 78)
    w("3. MAPA MODBUS (holding registers; direccion Modbus = %R + 2999)")
    w("-" * 78)
    w(f"  {'Bloque':<8} {'%R':<16} {'Modbus':<14} Acceso del backend")
    w(f"  {'LCMD':<8} {f'{S.CMD_BASE}..{S.CMD_BASE + 9}':<16} {f'{R(S.CMD_BASE)}..{R(S.CMD_BASE + 9)}':<14} ESCRIBE (comandos en vivo)")
    w(f"  {'LSTG':<8} {f'{S.STAGE_BASE}..{S.STAGE_BASE + S.CFG_LEN - 1}':<16} {f'{R(S.STAGE_BASE)}..{R(S.STAGE_BASE + S.CFG_LEN - 1)}':<14} ESCRIBE (solo dentro de BEGIN..COMMIT)")
    w(f"  {'LSTA':<8} {f'{S.STATUS_BASE}..{S.STATUS_BASE + 99}':<16} {f'{R(S.STATUS_BASE)}..{R(S.STATUS_BASE + 99)}':<14} LEE")
    w(f"  {'LACT':<8} {f'{S.ACT_BASE}..{S.ACT_BASE + S.CFG_LEN - 1}':<16} {f'{R(S.ACT_BASE)}..{R(S.ACT_BASE + S.CFG_LEN - 1)}':<14} LEE (verificacion)")
    w(f"  {'LPEN/LI/LCK':<8} {f'{S.PEN_BASE}..{S.LD_BASE + 7}':<16} {f'{R(S.PEN_BASE)}..{R(S.LD_BASE + 7)}':<14} no se escriben nunca")
    w("  Los registros v5 (%R1..%R127) siguen igual. Con el motor v7 el backend")
    w("  los deja en estado neutro (sin movimiento, sin eventos, torreta 0).")
    w("")
    w("3.1 LCMD - comandos")
    w("  [0] CmdSeq   : cada CAMBIO de valor ejecuta el comando (1, 2, 3...).")
    w("  [1] CmdCode  : 1 BEGIN  2 COMMIT  3 ABORT  4 UNLOAD (vuelve a v5)  5 RESET")
    w("  [2] Arg      : en COMMIT, el id de la configuracion escrita (CFG[1]).")
    w("  [3..6] APP1..APP4 : botones virtuales (0/1, en vivo).")
    w("  [7] Paro software : <>0 detiene motor, secuencias y plumas.")
    w("  El PLC confirma cada comando copiando CmdSeq en LSTA[5].")
    w("")
    w("3.2 Protocolo de carga (lo hace plc_banda.cargar_motor_v7)")
    w("  1. LCMD[3..7] = 0; BEGIN; esperar LSTA[5] = seq y LSTA[4] = 1 (recibiendo).")
    w("  2. Escribir las 640 palabras en LSTG (7 peticiones de hasta 100).")
    w("  3. Releer LSTG y comparar (si difiere: ABORT y error).")
    w("  4. COMMIT con Arg = CFG[1]. El PLC copia LSTG -> LPEN en trozos de")
    w(f"     {S.VAL_CHUNK} palabras por scan, calcula el checksum y valida; si todo")
    w("     pasa, copia LPEN -> LACT en UN solo scan y reinicia el motor en")
    w("     estado seguro (motor parado, secuencias en 0, VFD reinicializado).")
    w("  5. Esperar LSTA[4] = 3 (aplicada) o 4 (rechazada: LSTA[7] codigo,")
    w("     LSTA[8] campo = offset+1). Confirmar LSTA[9] = id, LSTA[10] = checksum")
    w("     y releer LACT. Si no confirma, la carga se reporta como fallida.")
    w(f"  Fallo de comunicacion: la config activa NO cambia; a los {S.RX_TIMEOUT_DECIMAS // 10} s el PLC")
    w("  cierra la transaccion (LSTA[4] = 6) y el backend puede reintentar.")
    w("")
    w("3.3 CFG[0..639] (LSTG / LACT) - configuracion")
    w(f"  0 formato (=7)  1 id (1..32767)  2 longitud (=640)  3 checksum")
    w(f"  4 banda se mueve (0/1)  5 frecuencia Hz (1..327)  6 direccion inicial (1/2)")
    w(f"  7 filtro sensores (0.1 s, 0..50)  8 filtro botones  9 tiempo max pluma (0.1 s)")
    w(f"  10 lamparas con paro I3 (bits Q1..Q12; solo Q3..Q5)  11..19 reservado (0)")
    w(f"  20 motor.arranque  21 paro  22 modo (0 enclavado 1 mientras)  23 pausa")
    w(f"  24 dir_1  25 dir_2  26 invertir  27 paro antes de invertir (0.1 s, >=10)")
    w(f"  {S.O_OUT}..{S.O_OUT + 11} senal que enciende Q1..Q12 (solo Q3..Q9 cableadas)")
    w(f"  {S.O_COND}+6k  C(k+1): op (1 AND 2 OR 3 XOR 4 NAND 5 NOR), in1..in4, flanco (0/1/2)")
    w(f"  {S.O_MEM}+3k M(k+1): set, reset, prioridad (0 reset 1 set)")
    w(f"  {S.O_TIMER}+5k T(k+1): tipo (1 TON 2 TOF 3 PULSO 4 RETARDO 5 TONR 6 INTERMITENTE),")
    w("                entrada, reset, preset (0.1 s), preset2 (0.1 s, intermitente)")
    w(f"  {S.O_COUNT}+5k CT(k+1): sube, baja, reset, preset, modo (0 una vez 1 ciclico 2 nivel)")
    w(f"  {S.O_SEQ}+140k SQ(k+1): inicio, paro, reset, pausa, modo (0 una vez 1 infinita")
    w("                2 repetir), repeticiones, pasos (0..16), exclusiva, pausa_apaga,")
    w("                3 reservados, y 16 pasos de 8 palabras:")
    w("                salidas (bits Q1..Q12), motor (b0 marcha b1 paro b2 dir1 b3 dir2")
    w("                b4 invertir), avanza (0 tiempo 1 senal 2 tiempo Y senal 3 senal O")
    w("                tiempo 4 flanco), senal, tiempo (0.1 s), siguiente (0 = el que")
    w("                sigue, 1..16, 17 = fin), senal alterna, ir a (0..17)")
    w(f"  {S.O_SEQ + S.N_SEQ * S.SEQ_W}..639 reservado (0)")
    w("")
    w("3.4 Codigos de senal (cualquier campo 'senal'; +1000 = NOT)")
    w("  0 nada | 1..12 I1..I12 (I4 = S1, I5 = S2) | 13..24 Q1..Q12 encendida")
    w("  25 SIEMPRE | 26 banda corriendo 27 detenida 28 dir 1 29 dir 2 30 invirtiendo")
    w("  31 VFD en cero 32 CfgReady 33 primer ciclo | 34..37 APP1..APP4 | 38 paro software")
    w(f"  {S.SIG_COND}..{S.SIG_COND + 15} C1..C16 | {S.SIG_MEM}..{S.SIG_MEM + 15} M1..M16 | "
      f"{S.SIG_TIMER}..{S.SIG_TIMER + 15} T1..T16 | {S.SIG_COUNT}..{S.SIG_COUNT + 15} CT1..CT16")
    w(f"  {S.SIG_SEQ}.. SQ1: +0 activa +1 pausada +2 fin +3 ciclo +4..+19 paso 1..16 | "
      f"{S.SIG_SEQ + S.SEQ_SIG_W}.. SQ2 (igual)")
    w("  Checksum: s = 0; para i = 0..639 (i <> 3): s = (s*31 + valor + 32769) MOD 32749")
    w("            (valor como entero con signo de 16 bits).")
    w("")
    w("3.5 LSTA[0..99] - estado (solo lectura)")
    for k, txt in ((S.S_VERSION, "version del programa (= 7)"), (S.S_MAGIC, f"firma LAVO (= {S.MAGIC})"),
                   (S.S_HEARTBEAT, "latido (+1 cada 0.1 s): si no cambia, el PLC no corre"),
                   (S.S_SCANS, "scans por segundo (ciclo = 1000 / valor ms)"),
                   (S.S_TX, "transaccion: 0 sin, 1 recibiendo, 2 validando, 3 aplicada, 4 rechazada, 5 abortada, 6 timeout"),
                   (S.S_ACK, "ultimo CmdSeq procesado"), (S.S_RESULT, "resultado del ultimo comando (0 ok, 9 fuera de orden, 11 desconocido)"),
                   (S.S_ERR, "error de validacion (1 formato 2 longitud 3 id 4 checksum 5 rango 6 seguridad 7 hardware 8 incoherente 10 timeout 12 id distinto)"),
                   (S.S_ERRFIELD, "campo con error = offset + 1"), (S.S_ACTIVE_ID, "id de la config activa (0 = ninguna)"),
                   (S.S_ACTIVE_CHK, "checksum de la config activa"), (S.S_ENGINE, "1 = motor v7 activo"),
                   (S.S_CFGREADY, "1 = CfgReady (VFD listo)"), (S.S_MOTOR, "motor: 0 detenido 1 marcha 2 frenando"),
                   (S.S_DIR, "direccion real"), (S.S_DIR_TARGET, "direccion pedida"), (S.S_RUNLATCH, "marcha enclavada"),
                   (S.S_STOPREASON, "motivo de paro (1 I3 2 software 3 senal de paro 4 pausa 5 config no lista 6 secuencia)"),
                   (S.S_IN, "entradas filtradas (bits I1..I12)"), (S.S_OUT, "salidas reales (bits Q1..Q12)")):
        w(f"  [{k}] {txt}")
    w(f"  [{S.S_COND}..{S.S_COND + 1}] C1..C16  [{S.S_MEM}..{S.S_MEM + 1}] M1..M16  [{S.S_TIMERS}..{S.S_TIMERS + 1}] T1..T16 terminados")
    w(f"  [{S.S_COUNTS}..{S.S_COUNTS + 1}] CT1..CT16 (cada par: bits 0..7 y 8..15)")
    w(f"  [{S.S_SEQ}..{S.S_SEQ + 7}] SQ1 y SQ2: estado (0 inactiva 1 activa 2 pausa 3 terminada), paso, tiempo del paso (0.1 s), ciclos")
    w(f"  [{S.S_TACC}..{S.S_TACC + 15}] acumulado T1..T16 (0.1 s)  [{S.S_CACC}..{S.S_CACC + 15}] cuenta CT1..CT16")
    w(f"  [{S.S_PLFAULT}] falla de plumas  [{S.S_BRAKE}] tiempo de frenado  [{S.S_HW_OUT}] salidas cableadas (= {S.hw_out_mask()})")
    w(f"  [{S.S_HW_IN}] entradas cableadas (= {S.hw_in_mask()})  [{S.S_HW_LAMP}] lamparas (= {S.hw_lamp_mask()})  "
      f"[{S.S_RX}] tiempo de transaccion  [{S.S_CFGLEN}] longitud (= 640)  [{S.S_STAGED_ID}] id del ultimo COMMIT")
    w("")
    w("-" * 78)
    w("4. INSTALACION EN CSCAPE (paso a paso)")
    w("-" * 78)
    w("  1. Abre ladder_maestro_banda.csp y GUARDALO COMO COPIA")
    w("     (p. ej. ladder_maestro_banda_v7.csp). El original es tu respaldo.")
    w("  2. En Global Variables: elimina EC, ES, EI, EB (seccion 1) y pega el")
    w("     bloque de la seccion 2. Conserva Sys100ms, ek y ej.")
    w("  3. Abre el bloque ST STBLOCK1 y REEMPLAZA todo su texto por el")
    w("     contenido completo de LAVO_Main_ST.txt.")
    w("  3b. Agrega un SEGUNDO bloque ST (STBLOCK2) DESPUES de STBLOCK1 y pega")
    w("     LAVO_Motor_ST.txt. Cscape limita cada bloque a 64 KB; los dos se")
    w("     ejecutan en orden en cada ciclo y comparten las variables globales.")
    w("  4. Compila (Program > Compile / Verify). Los avisos W10034 ('loop")
    w("     instructions may block the target cycle') son normales.")
    w("     Si aparece un error de tipo en una linea con DINT#, cambia esa")
    w("     constante a su forma sin prefijo (p. ej. DINT#31 -> 31) y avisame.")
    w("  5. Revisa el uso de registros: nada mas debe ocupar %R1000..%R3537.")
    w("  6. Descarga al PLC de la banda (con la banda detenida y el paro I3")
    w("     a la mano). Sin configuracion v7 el PLC corre la logica v5 de siempre.")
    w("  7. Verifica en el Data Watch de Cscape:")
    w(f"       %R{S.STATUS_BASE} = 7   %R{S.STATUS_BASE + 1} = {S.MAGIC}   %R{S.STATUS_BASE + 2} cambia solo (latido)")
    w(f"       %R{S.STATUS_BASE + S.S_HW_OUT} = {S.hw_out_mask()}   %R{S.STATUS_BASE + S.S_HW_LAMP} = {S.hw_lamp_mask()}   "
      f"%R{S.STATUS_BASE + S.S_CFGLEN} = 640   %R{S.STATUS_BASE + S.S_SCANS} = scans/s")
    w("  8. En Render (o en el puente local) pon BANDA_MOTOR_V7 = 1. Con 0, el")
    w("     backend sigue generando programas v5/v6 exactamente como antes.")
    w("  9. Primera prueba fisica sugerida (en este orden):")
    w("       a) programa v5 conocido -> debe comportarse igual que antes;")
    w("       b) 'verde con I1' (sin motor) -> I1 enciende Q3;")
    w("       c) 'torreta verde, amarilla, roja 2 s cada una con I1, paro I2';")
    w("       d) 'a 20 Hz arranca con I1 y para con I2' -> presiona I3 a mitad:")
    w("          todo se detiene y al soltarlo NADA arranca solo.")
    w("")
    w("  Volver atras: abre el .csp original (v6) y descargalo. El backend")
    w("  detecta la version del PLC (%R1700 = 7 o %R460 = 6) y no carga un")
    w("  programa v7 en un PLC sin el v7.")
    w("")
    w("-" * 78)
    w("5. LIMITES DE ESTE PROGRAMA MAESTRO")
    w("-" * 78)
    w(f"  16 condiciones (4 entradas c/u, anidables) · 16 memorias · 16 timers · 16 contadores")
    w(f"  2 secuencias independientes de 16 pasos (una rama alterna por paso)")
    w(f"  Resolucion de tiempo 0.1 s (T_100MS %S4); maximo 3276.7 s por timer o paso")
    w(f"  Salidas cableadas: lamparas Q3 verde, Q4 amarilla, Q5 roja y plumas Q6..Q9.")
    w(f"  Botones: I1, I2 e I3 (paro de emergencia). Q10..Q12 NO existen en la banda.")
    w(f"  Frecuencia fija por configuracion (1..327 Hz); no hay rampas ni analogicas")
    w(f"  Contadores hasta 32767; una pluma nunca sube y baja a la vez")
    return "\r\n".join(L) + "\r\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", help="carpeta 'Archivos Cscape' donde escribir LAVO_Main_ST.txt "
                                      "y LAVO_Variables_Cscape.txt")
    args = ap.parse_args()
    b1, b2 = partes()
    st = b1 + b2
    with open(SALIDA, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(st)
    for nombre, txt in (("ST_banda_v7_bloque1.txt", b1), ("ST_banda_v7_bloque2.txt", b2)):
        with open(os.path.join(AQUI, nombre), "w", encoding="utf-8", newline="\r\n") as f:
            f.write(txt)
    print(f"{SALIDA}: {st.count(chr(10)) + 1} lineas")
    referencia = generar_variables()
    with open(os.path.join(AQUI, "LAVO_Referencia_v7.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(referencia)
    # LAVO_Variables_Cscape.txt: SOLO el bloque para pegar en Global Variables.
    a = referencia.index("      (*$desc====== MOTOR LAVO v7")
    b = referencia.index("------------------------- copiar hasta aqui")
    variables = referencia[a:b]
    with open(os.path.join(AQUI, "LAVO_Variables_Cscape.txt"), "w", encoding="utf-8", newline="") as f:
        f.write(variables)
    if args.destino:
        for nombre, txt in (("LAVO_Main_ST.txt", b1), ("LAVO_Motor_ST.txt", b2)):
            ruta = os.path.join(args.destino, nombre)
            with open(ruta, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(txt)
            print(f"{ruta}: escrito")
        ruta = os.path.join(args.destino, "LAVO_Variables_Cscape.txt")
        with open(ruta, "w", encoding="utf-8", newline="") as f:
            f.write(variables)
        print(f"{ruta}: escrito")


if __name__ == "__main__":
    main()
