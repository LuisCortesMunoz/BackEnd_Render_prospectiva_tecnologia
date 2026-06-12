# leer_pdfs.py  v2
# Usa Groq Vision (gratis) en lugar de Anthropic para leer los PDFs
# Modelo de vision: meta-llama/llama-4-scout-17b-16e-instruct
#
# Instalacion:
#   pip install pymupdf groq python-dotenv
#
# Uso:
#   python leer_pdfs.py                         <- toda la carpeta codigos/
#   python leer_pdfs.py codigos/02_Contador.pdf <- un solo PDF

import os
import re
import sys
import json
import base64
import fitz  # pymupdf
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

vision_client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
MODELO_VISION = "meta-llama/llama-4-scout-17b-16e-instruct"

print(f"Modelo Vision : {MODELO_VISION}")
print("=" * 60)

PROMPT_VISION = """Eres un experto en PLCs Horner programados en Ladder con Cscape.
Analiza esta imagen de un diagrama Ladder y extrae la logica exacta.

Responde SOLO con JSON valido, sin texto adicional ni bloques de codigo:
{
  "renglones": [
    {
      "numero": 1,
      "descripcion": "que hace este renglon en palabras simples",
      "fila_0": [
        {"tipo": "XIC", "operando": "%I1", "parametros": {}}
      ],
      "filas_paralelas": [
        [{"tipo": "XIC", "operando": "%M1"}]
      ]
    }
  ],
  "descripcion_general": "que hace el programa completo en una oracion"
}

Reglas:
- XIC = contacto NA (--|  |--)
- XIO = contacto NC (--|/|--)
- OTE = bobina salida (--( )--)
- OTL = bobina set --(S)--
- OTU = bobina reset --(R)--
- CTU = contador ascendente: incluye R y PV en parametros
- TON/TOF = temporizadores: incluye PT_ms en parametros
- Si hay rama debajo en paralelo, ponla en filas_paralelas
- Normaliza: %I0001->%I1, %M00001->%M1, %Q0010->%Q10, %R00001->%R1
- Si la pagina esta vacia responde: {"renglones":[],"descripcion_general":"pagina vacia"}
"""

# ─────────────────────────────────────────────────────────────────
def pagina_a_base64(pdf_path: str, numero_pagina: int) -> str:
    doc = fitz.open(pdf_path)
    page = doc[numero_pagina]
    mat = fitz.Matrix(2.0, 2.0)  # zoom x2 mejor resolucion
    pix = page.get_pixmap(matrix=mat)
    png_bytes = pix.tobytes("png")
    doc.close()
    return base64.standard_b64encode(png_bytes).decode("utf-8")

def analizar_pagina_vision(pdf_path: str, numero_pagina: int) -> dict:
    b64 = pagina_a_base64(pdf_path, numero_pagina)
    resp = vision_client.chat.completions.create(
        model=MODELO_VISION,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": PROMPT_VISION}
            ]
        }]
    )
    texto = resp.choices[0].message.content.strip()
    texto = re.sub(r"^```json\s*", "", texto)
    texto = re.sub(r"```$", "", texto.strip())
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        return {"renglones": [], "descripcion_general": "error al parsear",
                "raw": texto}

# ─────────────────────────────────────────────────────────────────
def leer_pdf(ruta_pdf: str, descripcion_txt: str = "") -> None:
    nombre = os.path.splitext(os.path.basename(ruta_pdf))[0]

    print(f"\n{'='*60}")
    print(f"PROGRAMA : {nombre}")
    print(f"ARCHIVO  : {ruta_pdf}")
    if descripcion_txt:
        print(f"DESCRIPCION TXT:\n  {descripcion_txt[:300].strip()}")
    print(f"{'='*60}")

    doc = fitz.open(ruta_pdf)
    total_paginas = len(doc)
    doc.close()
    print(f"Total de páginas: {total_paginas}\n")

    for i in range(total_paginas):
        if i == 0:
            print(f"  Página 1 — portada, omitida")
            continue

        print(f"  Página {i+1} — analizando con Vision...", end=" ", flush=True)
        datos = analizar_pagina_vision(ruta_pdf, i)
        renglones = datos.get("renglones", [])

        if not renglones:
            print("vacía / sin lógica Ladder")
            continue

        print(f"{len(renglones)} renglon(es) detectados")

        desc_general = datos.get("descripcion_general", "")
        if desc_general:
            print(f"\n  Función general: {desc_general}")

        print()
        for r in renglones:
            print(f"  ── Renglon {r.get('numero','?')}: {r.get('descripcion','')}")
            print(f"     fila 0 (serie):")
            for el in r.get("fila_0", []):
                params = f" {el['parametros']}" if el.get("parametros") else ""
                print(f"       {el['tipo']:8} {el['operando']}{params}")
            for j, fp in enumerate(r.get("filas_paralelas", []), 1):
                print(f"     fila {j} (paralela):")
                for el in fp:
                    print(f"       {el['tipo']:8} {el['operando']}")
            print()

        print(f"  JSON completo página {i+1}:")
        print(json.dumps(datos, indent=4, ensure_ascii=False))
        print()

# ─────────────────────────────────────────────────────────────────
def leer_carpeta(carpeta: str = "codigos") -> None:
    if not os.path.isdir(carpeta):
        print(f"ERROR: carpeta '{carpeta}/' no encontrada.")
        sys.exit(1)

    pdfs = sorted([f for f in os.listdir(carpeta) if f.lower().endswith(".pdf")])
    if not pdfs:
        print(f"No hay PDFs en '{carpeta}/'")
        sys.exit(1)

    print(f"\nEncontrados {len(pdfs)} PDF(s) en '{carpeta}/':")
    for f in pdfs:
        print(f"  - {f}")

    for nombre_pdf in pdfs:
        base     = re.sub(r"\.pdf$", "", nombre_pdf, flags=re.IGNORECASE)
        ruta_pdf = os.path.join(carpeta, nombre_pdf)
        ruta_txt = os.path.join(carpeta, base + ".txt")

        descripcion = ""
        if os.path.exists(ruta_txt):
            with open(ruta_txt, "r", encoding="utf-8", errors="ignore") as f:
                descripcion = f.read()[:1200]
            print(f"\n  TXT asociado: {base}.txt ✓")
        else:
            print(f"\n  Sin TXT asociado para: {nombre_pdf}")

        leer_pdf(ruta_pdf, descripcion)

    print("\n" + "="*60)
    print("Lectura completada.")

# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else "codigos"

    if arg.lower().endswith(".pdf"):
        base_txt = re.sub(r"\.pdf$", ".txt", arg, flags=re.IGNORECASE)
        desc = ""
        if os.path.exists(base_txt):
            with open(base_txt, "r", encoding="utf-8", errors="ignore") as f:
                desc = f.read()[:1200]
        leer_pdf(arg, desc)
    else:
        leer_carpeta(arg)