import os
from flask import Flask, request, jsonify, render_template
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Carga el contexto Ladder una sola vez al iniciar
with open("context/ladder_context.txt", "r", encoding="utf-8") as f:
    LADDER_CONTEXT = f.read()

SYSTEM_PROMPT = f"""
Eres un experto en programación de PLCs con lenguaje Ladder, 
específicamente para el PLC Horner XL4/XC1E5 programado con Cscape.

Aquí está el contexto del sistema:
{LADDER_CONTEXT}

Cuando el usuario te pida algo, responde SIEMPRE con estas secciones:
1. **Lógica Ladder propuesta** (describe los renglones con sus elementos)
2. **Explicación simple** (qué hace el programa paso a paso)
3. **Instrucciones de implementación** (cómo ingresar esto en Cscape)
4. **Código Python** (si se necesita comunicación con el PLC por Modbus TCP)
5. **Recomendaciones TCP/IP** (dirección IP, registros a leer/escribir)

Sé claro, técnico y práctico.
"""

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/query", methods=["POST"])
def query():
    data = request.get_json()
    user_message = data.get("message", "")

    if not user_message:
        return jsonify({"error": "Mensaje vacío"}), 400

    chat_completion = client.chat.completions.create(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ],
        model="llama-3.3-70b-versatile",
        temperature=0.3,
        max_tokens=2048
    )

    response_text = chat_completion.choices[0].message.content
    return jsonify({"response": response_text})

if __name__ == "__main__":
    app.run(debug=True, port=5000)