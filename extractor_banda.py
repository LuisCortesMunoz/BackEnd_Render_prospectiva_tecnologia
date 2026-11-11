"""
extractor_banda.py — la IA ENTIENDE la instruccion; no escribe el JSON del PLC.

Cuando el lector de patrones (constructores_banda) no entiende toda la
instruccion, una llamada CORTA al modelo la traduce al formato comun de reglas
(reglas_banda). Despues los constructores arman el programa con sus recetas
probadas y se simula; el modelo nunca escribe los bloques del motor en ese caso.

  mensajes(texto, acl)        -> prompt (corto: hardware + formato + ejemplos)
  extraer(texto, acl, llamar) -> {"reglas": dict validado | None,
                                  "dudas": [preguntas con opciones],
                                  "secuencia": bool  (acciones en orden: va al flujo de secuencias),
                                  "problemas": [por que no se usan las reglas]}

`llamar(messages) -> dict` la pone app.py (la llamada a Groq). Cualquier error
devuelve "problemas" y el flujo sigue como siempre (no se rompe nada).

Defensas contra reglas inventadas: cada regla cita el fragmento del texto de
donde sale (reglas_banda.validar lo comprueba), las senales y rangos se
validan, lo ambiguo vuelve como PREGUNTA con opciones (nunca un valor supuesto)
y lo armado se simula regla por regla.
"""

import json
import re

import comportamiento as co
import reglas_banda

PROMPT = """Traduces instrucciones en espanol para una BANDA TRANSPORTADORA a REGLAS en JSON. No programas el PLC:
solo dices QUE pasa CUANDO. Responde SOLO con JSON.

HARDWARE: botones I1 (verde) e I2 (rojo); I3 es paro de emergencia (nunca se usa como disparo).
Sensores S1 y S2 (detectan cajas/piezas/objetos). Luces de la torreta: verde, amarilla, roja.
Plumas: pluma1 y pluma2 (subir / bajar). Banda: avanzar, detener, pausar (detenerse N s y seguir).
Motor: 1..327 Hz, direccion derecha o izquierda.

FORMATO
{"secuencia": false,
 "reglas": [{"cuando": {"boton": "I1"} | {"presionado": "I1"} | {"sensor": "S1"} | {"conteo": true} |
                       {"banda": "en_marcha"|"detenida"},
             "hacer": [{"recurso": "banda|verde|amarilla|roja|pluma1|pluma2",
                        "accion": "avanzar|detener|pausar|encender|apagar|subir|bajar",
                        "duracion_s": N|null, "espera_s": N|null, "parpadeo": false, "queda": false}],
             "fragmento": "<copia LITERAL del pedazo de la instruccion de donde sale la regla>"}],
 "motor": {"hz": N|null, "direccion": "derecha"|"izquierda"|null},
 "alternancia": "I1"|"I2"|null,
 "paro_solo_s": N|null,
 "conteo": {"senal": "S1"|"S2", "n": N, "reinicia": true|false, "boton": "I1"|"I2"|null} | null,
 "dudas": [{"campo": "<nombre corto>", "pregunta": "...", "opciones": ["...", "..."]}]}

REGLAS
- NO inventes: si algo no lo dice el texto, no lo pongas. Cada regla lleva su "fragmento" copiado del texto.
- Si un dato imprescindible falta o es ambiguo, NO lo supongas: ponlo en "dudas" con 2 a 5 opciones concretas
  (p. ej. "prende la verde con I1": ¿solo mientras se presiona, se queda encendida o N segundos?).
  Los Hz que falten NO van en dudas (el sistema los pregunta).
- "cuando la banda avanza / esta en marcha" -> {"banda": "en_marcha"}; "con la banda detenida" -> {"banda": "detenida"}.
- "solo mientras se presiona / mantiene presionado I1" (luz o pluma) -> {"presionado": "I1"} (se apaga al soltar).
- "el mismo boton la arranca y la detiene" / "al presionarlo otra vez se detiene" -> "alternancia": ese boton
  (y una regla con ese boton que hace avanzar la banda).
- "se detiene sola a los N s" -> "paro_solo_s": N. "se detiene N s y sigue" -> accion "pausar" con duracion_s.
- Contar: "al llegar a N cajas en S2 ..." -> "conteo" {"senal","n"} y las reglas con {"cuando": {"conteo": true}};
  "reinicia": true si vuelve a contar solo ("cada N", "se reinicia solo"); "boton" si se reinicia con un boton.
- "queda": true si la luz se queda encendida; "parpadeo": true si parpadea / es intermitente.
- Si la instruccion pide acciones EN ORDEN (primero..., luego..., al terminar..., en ese orden) responde solo
  {"secuencia": true}: eso lo arma otro modulo.

EJEMPLOS
"Con el verde arranca la banda a 25 Hz y con el rojo se para" ->
{"secuencia":false,"reglas":[{"cuando":{"boton":"I1"},"hacer":[{"recurso":"banda","accion":"avanzar"}],"fragmento":"con el verde arranca la banda"},{"cuando":{"boton":"I2"},"hacer":[{"recurso":"banda","accion":"detener"}],"fragmento":"con el rojo se para"}],"motor":{"hz":25,"direccion":null},"alternancia":null,"paro_solo_s":null,"conteo":null,"dudas":[]}
"que la roja avise tres segundos cada vez que el primer sensor vea algo" ->
{"secuencia":false,"reglas":[{"cuando":{"sensor":"S1"},"hacer":[{"recurso":"roja","accion":"encender","duracion_s":3}],"fragmento":"que la roja avise tres segundos cada vez que el primer sensor vea algo"}],"motor":{"hz":null,"direccion":null},"alternancia":null,"paro_solo_s":null,"conteo":null,"dudas":[]}
"cuando pasen 4 piezas por S2 levanta la pluma 2 dos segundos y vuelve a contar" ->
{"secuencia":false,"reglas":[{"cuando":{"conteo":true},"hacer":[{"recurso":"pluma2","accion":"subir","duracion_s":2}],"fragmento":"levanta la pluma 2 dos segundos"}],"motor":{"hz":null,"direccion":null},"alternancia":null,"paro_solo_s":null,"conteo":{"senal":"S2","n":4,"reinicia":true,"boton":null},"dudas":[]}
"prende la amarilla con I1" ->
{"secuencia":false,"reglas":[],"motor":{"hz":null,"direccion":null},"alternancia":null,"paro_solo_s":null,"conteo":null,"dudas":[{"campo":"amarilla_i1","pregunta":"¿Cómo debe quedar la amarilla al presionar I1?","opciones":["Solo mientras se presiona I1","Se queda encendida","5 segundos","10 segundos"]}]}
"""

_CAMPO = re.compile(r"[^a-z0-9_]+")


def mensajes(texto: str, acl: dict = None) -> list:
    usuario = texto
    if acl:
        respuestas = "; ".join(f"{k}: {(v or {}).get('respuesta')}" for k, v in acl.items()
                               if isinstance(v, dict) and v.get("respuesta"))
        if respuestas:
            usuario += f"\n\nRESPUESTAS DEL USUARIO A TUS DUDAS (ya no las preguntes): {respuestas}"
    return [{"role": "system", "content": PROMPT}, {"role": "user", "content": usuario}]


def _dudas(data: dict, acl: dict) -> list:
    out = []
    dudas = data.get("dudas")
    if not isinstance(dudas, list):
        return out                                    # otra forma: se ignora (no es una pregunta usable)
    for i, d in enumerate(dudas, 1):
        if not isinstance(d, dict):
            continue
        campo = _CAMPO.sub("_", co._fold(d.get("campo") or f"duda_{i}")).strip("_")[:30] or f"duda_{i}"
        campo = "extr_" + campo
        ops = [str(o)[:60] for o in (d.get("opciones") or []) if str(o).strip()][:5]
        if campo in (acl or {}) or len(ops) < 2 or not str(d.get("pregunta") or "").strip():
            continue                                  # ya respondida, o sin opciones: no sirve como pregunta
        out.append({"id": campo, "campo": campo, "pregunta": str(d["pregunta"])[:200], "opciones": ops})
    return out


def extraer(texto: str, acl: dict, llamar) -> dict:
    out = {"reglas": None, "dudas": [], "secuencia": False, "problemas": []}
    try:
        data = llamar(mensajes(texto, acl))
    except Exception as e:                            # Groq caido, 429, JSON invalido...
        out["problemas"].append(f"el extractor no respondio: {e}")
        return out
    if not isinstance(data, dict):
        out["problemas"].append("el extractor no devolvio un objeto")
        return out
    if data.get("secuencia"):
        out["secuencia"] = True
        return out
    try:
        out["dudas"] = _dudas(data, acl)
    except Exception as e:                            # dudas con otra forma: no se preguntan
        out["problemas"].append(f"dudas del extractor ilegibles ({type(e).__name__})")
    if out["dudas"]:
        return out
    try:
        out["reglas"] = reglas_banda.validar(data, texto)
    except ValueError as e:
        out["problemas"] += list(e.args[0]) if e.args and isinstance(e.args[0], list) else [str(e)]
    except Exception as e:                            # reglas con otra forma: se sigue sin ellas
        out["reglas"] = None
        out["problemas"].append(f"el extractor devolvio reglas con otra forma ({type(e).__name__}: {e})")
    return out


def bloque_reglas(reglas: dict) -> str:
    """Para el modelo de generacion cuando las reglas no se pudieron armar solas."""
    return ("REGLAS ENTENDIDAS DE LA INSTRUCCION (cumplelas todas; despues se prueba cada una simulando el PLC):\n"
            + json.dumps({k: v for k, v in reglas.items() if v}, ensure_ascii=False, separators=(",", ":")))
