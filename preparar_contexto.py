"""
preparar_contexto.py — ejecutar UNA SOLA VEZ localmente.

Lee todos los PDF de codigos/ con Groq Vision y los TXT asociados,
y guarda el resultado estructurado en context_json/contexto.json.

Ese archivo se sube a git y Render lo usa al arrancar
(sin gastar tokens de Vision en cada reinicio del servidor).

Uso:
    python preparar_contexto.py             # procesa codigos/
    python preparar_contexto.py otra_carpeta
"""

import os, re, sys, json, base64, datetime
import fitz
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

MODELO_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

PROMPT_VISION = """Eres un experto en PLCs Horner programados en Ladder con Cscape.
Analiza esta imagen y extrae la logica exacta. Responde SOLO con JSON valido:
{
  "renglones": [
    {
      "numero": 1,
      "descripcion": "que hace este renglon",
      "fila_0": [{"tipo": "XIC", "operando": "%I1", "parametros": {}}],
      "filas_paralelas": [[{"tipo": "XIC", "operando": "%M1"}]]
    }
  ],
  "descripcion_general": "que hace el programa completo"
}
Reglas:
- XIC=NA, XIO=NC, OTE=bobina, OTL=set, OTU=reset
- CTU: incluye R y PV en parametros
- TON/TOF: incluye PT_ms en parametros
- Ramas paralelas en filas_paralelas
- Normaliza: %I0001->%I1, %Q0010->%Q10, %M00001->%M1
- Pagina vacia: {"renglones":[],"descripcion_general":"pagina vacia"}"""


def pagina_a_base64(pdf_path: str, num: int) -> str:
    doc = fitz.open(pdf_path)
    pix = doc[num].get_pixmap(matrix=fitz.Matrix(2, 2))
    b64 = base64.standard_b64encode(pix.tobytes("png")).decode()
    doc.close()
    return b64


def analizar_pagina(pdf_path: str, num: int) -> dict:
    b64 = pagina_a_base64(pdf_path, num)
    resp = client.chat.completions.create(
        model=MODELO_VISION,
        max_tokens=2000,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "text", "text": PROMPT_VISION}
        ]}]
    )
    texto = resp.choices[0].message.content.strip()
    texto = re.sub(r"^```json\s*", "", texto)
    texto = re.sub(r"```$", "", texto.strip())
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return {"renglones": [], "descripcion_general": "error al parsear"}


def procesar_pdf(ruta_pdf: str, descripcion: str = "") -> dict:
    nombre = os.path.splitext(os.path.basename(ruta_pdf))[0]
    doc    = fitz.open(ruta_pdf)
    total  = len(doc)
    doc.close()

    renglones_total    = []
    descripcion_general = ""

    for i in range(1, total):  # omitir pagina 0 (portada)
        print(f"    Pagina {i+1}...", end=" ", flush=True)
        datos = analizar_pagina(ruta_pdf, i)
        rens  = datos.get("renglones", [])
        if not rens:
            print("vacia")
            continue
        print(f"{len(rens)} renglon(es)")
        if not descripcion_general:
            descripcion_general = datos.get("descripcion_general", "")
        renglones_total.extend(rens)

    return {
        "nombre":              nombre,
        "descripcion":         descripcion.strip(),
        "descripcion_general": descripcion_general,
        "renglones":           renglones_total,
    }


def preparar(carpeta: str = "codigos", salida: str = "context_json/contexto.json"):
    if not os.path.isdir(carpeta):
        print(f"ERROR: carpeta '{carpeta}/' no encontrada.")
        sys.exit(1)

    pdfs = sorted(f for f in os.listdir(carpeta) if f.lower().endswith(".pdf"))
    if not pdfs:
        print(f"No hay PDFs en '{carpeta}/'")
        sys.exit(1)

    print(f"Encontrados {len(pdfs)} PDF(s) en '{carpeta}/'")
    print("=" * 60)

    programas = []

    for nombre_pdf in pdfs:
        base    = re.sub(r"\.pdf$", "", nombre_pdf, flags=re.IGNORECASE)
        ruta_pdf = os.path.join(carpeta, nombre_pdf)
        ruta_txt = os.path.join(carpeta, base + ".txt")

        desc = ""
        if os.path.exists(ruta_txt):
            with open(ruta_txt, encoding="utf-8", errors="ignore") as f:
                desc = f.read()[:1200]
            print(f"\nPDF : {nombre_pdf}   TXT: {base}.txt")
        else:
            print(f"\nPDF : {nombre_pdf}   (sin TXT)")

        prog = procesar_pdf(ruta_pdf, desc)
        programas.append(prog)
        print(f"  -> {len(prog['renglones'])} renglones totales")

    # TXTs sin PDF asociado
    pdfs_set   = set(pdfs)
    txts_solos = sorted(
        f for f in os.listdir(carpeta)
        if f.lower().endswith(".txt")
        and f.replace(".txt", ".pdf") not in pdfs_set
        and f.replace(".txt", ".PDF") not in pdfs_set
    )
    for nombre_txt in txts_solos:
        ruta_txt = os.path.join(carpeta, nombre_txt)
        with open(ruta_txt, encoding="utf-8", errors="ignore") as f:
            contenido = f.read()[:2000]
        programas.append({
            "nombre":              nombre_txt.replace(".txt", ""),
            "descripcion":         contenido,
            "descripcion_general": "",
            "renglones":           [],
        })
        print(f"\nTXT solo: {nombre_txt}")

    datos = {
        "version":          "1.0",
        "generado":         datetime.datetime.now().isoformat(),
        "total_programas":  len(programas),
        "total_renglones":  sum(len(p["renglones"]) for p in programas),
        "programas":        programas,
    }

    os.makedirs(os.path.dirname(salida), exist_ok=True)
    with open(salida, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"Guardado  : {salida}")
    print(f"Programas : {datos['total_programas']}")
    print(f"Renglones : {datos['total_renglones']}")
    print(f"\nAhora haz:  git add {salida}  y  git push")
    print("Render lo usara en el proximo arranque (sin llamar a Vision).")


if __name__ == "__main__":
    carpeta = sys.argv[1] if len(sys.argv) > 1 else "codigos"
    preparar(carpeta)
