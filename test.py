# test_con_contexto.py  v8
# Modelo : openai/gpt-oss-120b
# FIX    : verificacion de enclavamiento en el mensaje de usuario
#          para garantizar que el modelo genere la rama paralela de auto-retencion

import os
import re
import json
import datetime
import pdfplumber
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

MODELO = "openai/gpt-oss-120b"
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

print(f"Modelo activo : {MODELO}")
print("=" * 60)

VARIABLES_PLC = """
PLC     : Horner XL4 / XC1E5 | Software: Cscape 10.2 | 24VDC
Red     : IP 192.168.1.100 | Puerto Modbus TCP 502

ENTRADAS (%I):
  %I1: Boton NA arranque    %I2: Boton NC paro
  %I3: Selector/reset       %I4: Selector
  %I8: Paro emergencia NC

SALIDAS (%Q):
  %Q10: Lampara verde   %Q11: Lampara amarilla   %Q12: Lampara roja

MARCAS (%M): bits internos    REGISTROS (%R): palabras 16 bits
"""

# ─────────────────────────────────────────────────────────────────
# LECTURA DE PDF
# ─────────────────────────────────────────────────────────────────
PARAMS_RE = re.compile(r"^\d+\s*(PV|PT|R|CU|CD|PRE|ACC)$", re.IGNORECASE)
FOOTER_RE = re.compile(r"(Main Loop Logic Block:|Wed |Tue |Mon |Thu |Fri |Sat |Sun )", re.IGNORECASE)

def leer_pdf(ruta_pdf):
    paginas = []
    with pdfplumber.open(ruta_pdf) as pdf:
        for page in pdf.pages:
            paginas.append((page.extract_text() or "").strip())
    return paginas

def parsear_renglones(paginas):
    texto = "\n".join(paginas[1:])
    renglones = []
    actual = None
    for linea in texto.split("\n"):
        linea = linea.strip()
        if not linea or FOOTER_RE.search(linea):
            continue
        if PARAMS_RE.match(linea):
            if actual:
                actual["raw"].append(linea)
            continue
        m = re.match(r"^(\d{1,3})\s+(.+)", linea)
        if m and not re.match(r"^\d+\.\d+", linea):
            if actual:
                renglones.append(actual)
            actual = {"numero": int(m.group(1)), "raw": [m.group(2).strip()]}
        elif actual:
            actual["raw"].append(linea)
    if actual:
        renglones.append(actual)
    return renglones

def pdf_a_texto(nombre, paginas, descripcion=""):
    renglones = parsear_renglones(paginas)
    bloque = f"\n{'='*50}\nPROGRAMA: {nombre}\n{'='*50}\n"
    for linea in paginas[0].split("\n"):
        if any(k in linea for k in ["Version:", "Created:", "Last Modified:"]):
            bloque += linea + "\n"
    bloque += f"\n[LOGICA LADDER]\n"
    for r in renglones:
        bloque += f"  Renglon {r['numero']}: {' | '.join(r['raw'])}\n"
    if descripcion:
        bloque += f"\n[DESCRIPCION]\n{descripcion.strip()}\n"
    return bloque

def cargar_carpeta(carpeta="codigos", max_chars_por_programa=3500):
    if not os.path.isdir(carpeta):
        print(f"AVISO: carpeta '{carpeta}/' no encontrada.")
        return ""
    pdfs = sorted([f for f in os.listdir(carpeta) if f.lower().endswith(".pdf")])
    txts_sin_pdf = sorted([
        f for f in os.listdir(carpeta)
        if f.lower().endswith(".txt")
        and f.replace(".txt", ".pdf") not in pdfs
    ])
    contexto = ""
    cargados = 0
    for nombre_pdf in pdfs:
        base     = re.sub(r"\.pdf$", "", nombre_pdf, flags=re.IGNORECASE)
        ruta_pdf = os.path.join(carpeta, nombre_pdf)
        ruta_txt = os.path.join(carpeta, base + ".txt")
        descripcion = ""
        if os.path.exists(ruta_txt):
            with open(ruta_txt, "r", encoding="utf-8", errors="ignore") as f:
                descripcion = f.read()[:1200]
        try:
            paginas = leer_pdf(ruta_pdf)
            bloque  = pdf_a_texto(base, paginas, descripcion)
            if len(bloque) > max_chars_por_programa:
                bloque = bloque[:max_chars_por_programa] + "\n[truncado]\n"
            contexto += bloque
            cargados += 1
            print(f"  PDF OK : {nombre_pdf} ({len(bloque)} chars)")
        except Exception as e:
            print(f"  PDF ERR: {nombre_pdf} — {e}")
    for nombre_txt in txts_sin_pdf:
        base     = nombre_txt.replace(".txt", "")
        ruta_txt = os.path.join(carpeta, nombre_txt)
        with open(ruta_txt, "r", encoding="utf-8", errors="ignore") as f:
            contenido = f.read()[:2000]
        contexto += f"\n{'='*50}\nPROGRAMA: {base}\n{'='*50}\n{contenido}\n"
        cargados += 1
        print(f"  TXT OK : {nombre_txt}")
    print(f"\n  Total: {cargados} archivos — {len(contexto)} chars")
    return contexto


# ─────────────────────────────────────────────────────────────────
# CONVERSION JSON DEL MODELO → SCHEMA DEL EDITOR
# ─────────────────────────────────────────────────────────────────
TIPO_MAP = {
    "XIC":"contact_no","XIO":"contact_nc",
    "OSR":"contact_pos_edge","OSF":"contact_neg_edge",
    "OTE":"coil","OTL":"coil_s","OTU":"coil_r",
    "TON":"block_ton","TOF":"block_tof",
    "CTU":"block_ctu","CTD":"block_ctd",
    "CMP":"block_cmp","MOV":"block_mov","ADD":"block_add",
    "contact_no":"contact_no","contact_nc":"contact_nc",
    "contact_pos_edge":"contact_pos_edge","contact_neg_edge":"contact_neg_edge",
    "coil":"coil","coil_s":"coil_s","coil_r":"coil_r",
    "block_ton":"block_ton","block_tof":"block_tof",
    "block_ctu":"block_ctu","block_ctd":"block_ctd",
    "block_cmp":"block_cmp","block_mov":"block_mov","block_add":"block_add",
}

def norm(op):
    if not op: return ""
    s = str(op).strip().upper()
    m = re.match(r"^%([IQMR])0*(\d+)$", s)
    if m:
        l, n = m.group(1), int(m.group(2))
        return f"I0.{n}" if l=="I" else f"Q0.{n}" if l=="Q" else f"M0.{n}" if l=="M" else f"MW{n}"
    m = re.match(r"^([IQMR])(\d+)$", s)
    if m:
        l, n = m.group(1), int(m.group(2))
        return f"I0.{n}" if l=="I" else f"Q0.{n}" if l=="Q" else f"M0.{n}" if l=="M" else f"MW{n}"
    return op

def modbus(addr):
    a = str(addr).upper()
    if a.startswith("I"):  return {"fn":"read_coil",  "address":None}
    if a.startswith("Q"):  return {"fn":"write_coil", "address":None}
    if a.startswith("MW"): return {"fn":"holding_reg","address":None}
    return {"fn":"internal","address":None}

def mk_el(tipo_raw, operando, col, uid):
    t = TIPO_MAP.get(str(tipo_raw).strip().upper(),
        TIPO_MAP.get(str(tipo_raw).strip(), "contact_no"))
    a = norm(operando)
    e = {"id": uid, "type": t, "address": a, "pos": {"col": col}}
    if t == "coil":   e["coil_type"] = "output"
    if t == "coil_s": e["coil_type"] = "set"
    if t == "coil_r": e["coil_type"] = "reset"
    if t in ("block_ton","block_tof"): e["params"] = {"preset_ms":1000}
    if t in ("block_ctu","block_ctd"): e["params"] = {"preset":10}
    if t == "block_cmp": e["params"] = {"op":"EQ","value":0}
    return e

def renglon_a_rung(renglon, idx, tid):
    num  = renglon.get("renglon", idx + 1)
    desc = renglon.get("descripcion", f"Rung {idx+1}")
    pfx  = f"e{tid}r{idx}"
    net  = []

    # ── Formato A: filas declaradas explicitamente ──────────
    if "filas" in renglon and isinstance(renglon["filas"], list):
        for fila in renglon["filas"]:
            fn  = fila.get("fila", len(net))
            els = [mk_el(e.get("tipo",""), e.get("operando",""),
                         c, f"{pfx}f{fn}c{c}")
                   for c, e in enumerate(fila.get("elementos", []))]
            net.append({"row": fn, "elements": els})
        if not net:
            net = [{"row":0,"elements":[]}]
        return {"id":num,"enabled":True,"comment":desc,"network":net}

    # ── Formato B: lista plana con auto-deteccion ────────────
    raw   = renglon.get("elementos", [])
    todos = [{"tipo": str(e.get("tipo","")).strip().upper(),
              "op":   e.get("operando","")} for e in raw]

    if not todos:
        return {"id":num,"enabled":True,"comment":desc,
                "network":[{"row":0,"elements":[]}]}

    # Buscar bobina de salida
    bobina_addr = None
    for e in reversed(todos):
        if e["tipo"] in ("OTE","OTL","OTU","coil","coil_s","coil_r"):
            bobina_addr = norm(e["op"])
            break

    # Detectar contacto de auto-retencion
    paralelos = set()
    if bobina_addr:
        for i, e in enumerate(todos):
            if (e["tipo"] in ("XIC","contact_no")
                    and norm(e["op"]) == bobina_addr
                    and 0 < i < len(todos)-1):
                paralelos.add(i)

    # Fila 0: todos excepto los paralelos
    f0, col = [], 0
    for i, e in enumerate(todos):
        if i not in paralelos:
            f0.append(mk_el(e["tipo"], e["op"], col, f"{pfx}f0c{col}"))
            col += 1
    net = [{"row":0,"elements":f0}]

    # Fila 1: rama paralela si existe
    if paralelos:
        f1 = [mk_el(todos[i]["tipo"], todos[i]["op"], c, f"{pfx}f1c{c}")
              for c, i in enumerate(sorted(paralelos))]
        net.append({"row":1,"elements":f1})

    return {"id":num,"enabled":True,"comment":desc,"network":net}

def build_symbol_table(rungs, vars_usadas):
    tbl = {}
    def reg(lista, cmt):
        for a in (lista or []):
            n = norm(a)
            if n and n not in tbl:
                tbl[n] = {"symbol":n.replace(".","_"),"type":"BOOL",
                          "modbus":modbus(n),"comment":f"{cmt} — {a}"}
    if vars_usadas:
        reg(vars_usadas.get("entradas",[]), "Entrada")
        reg(vars_usadas.get("salidas", []), "Salida")
        reg(vars_usadas.get("marcas",  []), "Marca")
        reg(vars_usadas.get("registros",[]),"Registro")
    for rung in rungs:
        for row in rung["network"]:
            for el in row["elements"]:
                a = el["address"]
                if a and a not in tbl:
                    tbl[a] = {"symbol":a.replace(".","_"),
                              "type":"INT" if a.startswith("MW") else "BOOL",
                              "modbus":modbus(a),"comment":""}
    return tbl

def a_schema(datos):
    tid   = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    rungs = [renglon_a_rung(r, i, tid)
             for i, r in enumerate(datos.get("logica_ladder", []))]
    return {
        "metadata": {
            "project_id":    f"import_{tid}",
            "name":           datos.get("programa_nombre","Programa importado"),
            "version":        "1.0.0",
            "plc_target":     {"ip":"192.168.1.100","port":502,"unit_id":1},
            "scan_time_ms":   100,
            "_explicacion":   datos.get("explicacion_simple",""),
            "_implementacion":" → ".join(datos.get("implementacion_cscape",[])),
            "_python_modbus":  datos.get("codigo_python_modbus",None),
        },
        "symbol_table": build_symbol_table(rungs, datos.get("variables_usadas",{})),
        "rungs":  rungs,
        "execution_state":{"mode":"run","rung_states":{},"forced_outputs":{}},
    }

def guardar_js(datos, pregunta, carpeta="respuestas"):
    os.makedirs(carpeta, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^\w\s-]","",datos.get("programa_nombre","prog")).strip().replace(" ","_")
    ruta = os.path.join(carpeta, f"{ts}_{slug}.js")
    schema = a_schema(datos)
    ramas  = sum(len(r["network"])-1 for r in schema["rungs"] if len(r["network"])>1)
    with open(ruta,"w",encoding="utf-8") as f:
        f.write(f"// Generado : {MODELO} | {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        f.write(f"// Consulta : {pregunta}\n")
        f.write(f"// Rungs: {len(schema['rungs'])} | Ramas paralelas: {ramas} | Variables: {len(schema['symbol_table'])}\n\n")
        f.write(f"export const program = {json.dumps(schema,indent=2,ensure_ascii=False)};\n\n")
        f.write("export default program;\n")
    print(f"\n  JS guardado : {ruta}")
    print(f"  Rungs       : {len(schema['rungs'])}")
    print(f"  Ramas par.  : {ramas}  {'✓ OK' if ramas > 0 else '⚠ ADVERTENCIA: sin ramas paralelas'}")
    print(f"  Variables   : {len(schema['symbol_table'])}")
    return ruta, schema


# ─────────────────────────────────────────────────────────────────
# PROMPTS
# ─────────────────────────────────────────────────────────────────
SYSTEM_PROMPT_BASE = """Eres un experto en PLCs Horner XL4/XC1E5 programados con Cscape en lenguaje Ladder.

VARIABLES DEL SISTEMA:
{variables}

PROGRAMAS DE REFERENCIA:
{contexto}

REGLAS DE RESPUESTA:
- Responde SOLO con JSON valido, sin texto adicional.
- Usa siempre el campo "filas" dentro de cada renglon.
- "fila 0" es la logica serie principal.
- "fila 1, 2..." son ramas paralelas.
- Tipos: XIC, XIO, OTE, OTL, OTU, TON, TOF, CTU, CTD, CMP, MOV, ADD.
- Operandos con %: %I1, %Q10, %M1, %R1.
- Paros NC (%I2, %I8) siempre van como XIO en fila 0."""

# Esquema de respuesta que va en el mensaje del usuario (no en el system)
ESQUEMA = """\

Responde con este esquema JSON exacto:
{
  "programa_nombre": "string",
  "logica_ladder": [
    {
      "renglon": 1,
      "descripcion": "string",
      "filas": [
        {
          "fila": 0,
          "descripcion": "string",
          "elementos": [
            {"tipo": "XIC", "operando": "%I1", "descripcion": "string"}
          ]
        }
      ]
    }
  ],
  "explicacion_simple": "string",
  "implementacion_cscape": ["paso1", "paso2"],
  "codigo_python_modbus": "string o null",
  "variables_usadas": {
    "entradas": ["%I1"], "salidas": ["%Q10"], "marcas": [], "registros": []
  }
}"""

# Verificacion de enclavamiento que se inyecta SOLO cuando la pregunta
# contiene palabras clave relacionadas con enclavamiento o auto-retencion
PALABRAS_ENCLAVAMIENTO = [
    "enclav", "latch", "retenc", "arranque", "paro", "marcha",
    "mantenga", "mantenerse", "soltar", "auto", "memoria"
]

VERIFICACION_ENCLAVAMIENTO = """
VERIFICACION OBLIGATORIA PARA ENCLAVAMIENTO:
Un enclavamiento real SIEMPRE necesita una rama paralela de auto-retencion.
Antes de responder verifica que en el renglon principal exista:

  fila 0: [XIC arranque] [XIO paro] [XIO emergencia] → (OTE bobina)
  fila 1: [XIC bobina]   ← contacto de memoria, IGUAL operando que la bobina

Sin fila 1 el enclavamiento NO funciona al soltar el boton.
Si usas una marca interna (%M), el renglon con esa marca tambien
necesita fila 1 con XIC de esa misma marca.

Ejemplo correcto de enclavamiento con %M1, %I1, %I2, %I8:
  Renglon 1:
    fila 0: XIC %I1, XIO %I2, XIO %I8, OTE %M1
    fila 1: XIC %M1   (auto-retencion de la marca)
  Renglon 2:
    fila 0: XIC %M1, OTE %Q10  (salida verde)
  Renglon 3:
    fila 0: XIO %M1, OTE %Q12  (salida roja cuando apagado)
"""

def es_enclavamiento(pregunta):
    """Detecta si la pregunta involucra logica de enclavamiento."""
    p = pregunta.lower()
    return any(palabra in p for palabra in PALABRAS_ENCLAVAMIENTO)

def construir_system_prompt(contexto_programas):
    return SYSTEM_PROMPT_BASE.format(
        variables=VARIABLES_PLC.strip(),
        contexto=contexto_programas.strip()
    )

def construir_mensaje_usuario(pregunta):
    """
    Construye el mensaje del usuario.
    Si detecta una pregunta de enclavamiento, agrega la verificacion
    especifica para que el modelo no olvide la rama paralela.
    """
    verificacion = VERIFICACION_ENCLAVAMIENTO if es_enclavamiento(pregunta) else ""
    return f"{pregunta}{verificacion}{ESQUEMA}"


# ─────────────────────────────────────────────────────────────────
# VALIDACION POST-GENERACION
# Verifica que el JSON del modelo tiene ramas paralelas
# en renglones que lo requieren
# ─────────────────────────────────────────────────────────────────
def validar_enclavamiento(datos, pregunta):
    """
    Si la pregunta es de enclavamiento, verifica que al menos un renglon
    tenga fila 1 (rama paralela de auto-retencion).
    Retorna (ok, mensaje).
    """
    if not es_enclavamiento(pregunta):
        return True, "No es enclavamiento, sin validacion especial."

    logica = datos.get("logica_ladder", [])
    for r in logica:
        filas = r.get("filas", [])
        if len(filas) > 1:
            return True, f"Renglon {r.get('renglon')} tiene rama paralela. OK."

    return False, (
        "ADVERTENCIA: La pregunta es de enclavamiento pero el modelo "
        "no genero ninguna rama paralela (fila 1). "
        "El programa no funcionara correctamente. "
        "Considera regenerar la consulta."
    )


# ─────────────────────────────────────────────────────────────────
# CONSULTA AL MODELO
# ─────────────────────────────────────────────────────────────────
def consultar(pregunta, system_prompt):
    print(f"\nConsulta: {pregunta}")
    if es_enclavamiento(pregunta):
        print("  [Enclavamiento detectado — verificacion activada]")
    print("-" * 60)

    mensaje_usuario = construir_mensaje_usuario(pregunta)

    respuesta = client.chat.completions.create(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": mensaje_usuario}
        ],
        model=MODELO,
        temperature=1,
        max_tokens=2048,
        response_format={"type": "json_object"}
    )

    texto_raw = respuesta.choices[0].message.content
    ti = respuesta.usage.prompt_tokens
    ts = respuesta.usage.completion_tokens
    tt = respuesta.usage.total_tokens

    try:
        datos = json.loads(texto_raw)
        print(json.dumps(datos, indent=2, ensure_ascii=False))
    except json.JSONDecodeError:
        print("AVISO: JSON invalido.")
        print(texto_raw)
        datos = {"programa_nombre": "error", "raw": texto_raw}

    print(f"\n[Modelo: {MODELO} | entrada={ti} salida={ts} total={tt}]")

    # Validar enclavamiento
    ok, msg = validar_enclavamiento(datos, pregunta)
    if not ok:
        print(f"\n  ⚠  {msg}")
    else:
        print(f"\n  ✓  {msg}")

    ruta_js, schema = guardar_js(datos, pregunta)
    return datos


# ─────────────────────────────────────────────────────────────────
# EJECUCION
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":

    print("\nCargando codigos/...")
    contexto = cargar_carpeta("codigos")

    if not contexto:
        print("No hay contexto.")
        exit(1)

    system_prompt = construir_system_prompt(contexto)
    print(f"\nSystem prompt: {len(system_prompt)} chars")
    print("=" * 60)

    consultar(
    "Crea un renglon de enclavamiento con esta estructura exacta: "
    "fila 0 en serie: primero XIO %I2 (boton paro NC), luego XIC %I1 (boton arranque NA), "
    "luego la bobina OTE %M1 como salida. "
    "fila 1 paralela UNICAMENTE con XIC %M1 en paralelo con XIC %I1, "
    "para que M1 se auto-retenga al soltar I1. "
    "I2 NO va en la rama paralela, solo en la fila 0 para cortar el circuito al parar.",
    system_prompt
)