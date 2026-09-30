"""
============================================================================
CONTEXTO INDUSTRIAL DE LA BANDA  (CAPA B — extension, no sustitucion)
============================================================================

El interprete de la banda trabaja con DOS fuentes de conocimiento que se
SUMAN; ninguna reemplaza a la otra:

    CAPA A  SYSTEM_PROMPT_BANDA (app.py) + banda_intent.py + plc_banda.py
            Lenguaje, reglas, ejemplos y mapeos que YA funcionan. Dice COMO
            se ejecuta fisicamente una instruccion en el ST maestro.

    CAPA B  context/Contexto_Industrial_para_Modelo_Banda.txt (este modulo)
            Procesos industriales, arquetipos, industrias, referencias
            conceptuales y peticiones vagas. Dice QUE significa un proceso.

Este modulo SOLO carga la capa B y la ofrece como un mensaje de sistema
ADICIONAL que se envia DESPUES del prompt de siempre. No modifica, recorta
ni reescribe SYSTEM_PROMPT_BANDA: si este archivo no existe o falla, la
banda sigue funcionando exactamente como antes (se registra un aviso y se
usa un resumen minimo incorporado).

La capa B se envia SOLO cuando la peticion tiene algo conceptual que
traducir (requiere_contexto_industrial). Una instruccion especifica
("cuando S1 detecte sube la pluma 1") la resuelve entera la capa A, y
mandar tambien la capa B solo gastaba tokens del limite por minuto del
plan gratuito de Groq, que rechazaba la peticion completa con 429.
Por lo mismo, del archivo de contexto viajan solo las secciones que la
capa A no cubre (SECCIONES_PROMPT); el archivo en disco no se toca.

Limites que la capa B NUNCA puede cruzar (orden de prioridad del §22):
    1. capacidades fisicas reales del ST
    2. reglas de hardware ya implementadas
    3. instruccion explicita del usuario
    4. contexto industrial (esta capa)
    5. defaults
La capa B no decide registros: sigue siendo banda_intent.normalizar_intencion
quien traduce la intencion a los campos del ST.

Solo aplica a la BANDA. El maletin no ve nada de este modulo.
============================================================================
"""

import logging
import os
import re

log = logging.getLogger("uvicorn.error")

# Ruta del archivo de contexto industrial. Configurable por entorno para que
# Render pueda apuntar a otra copia sin tocar el codigo.
CONTEXTO_BANDA_PATH = os.environ.get(
    "CONTEXTO_BANDA_INDUSTRIAL", "context/Contexto_Industrial_para_Modelo_Banda.txt")

# Tope de caracteres que se inyectan en el prompt (el archivo completo cabe
# de sobra; el tope evita que una edicion enorme dispare el costo del modelo).
MAX_CARACTERES_CONTEXTO = 20000

# Secciones del archivo que SI viajan en el prompt. El archivo completo sigue
# siendo la referencia (no se toca en disco y cargar_contexto_industrial lo
# devuelve entero); aqui solo se elige lo que el modelo necesita LEER.
#
# Por que: el plan gratuito de Groq da 8000 tokens/minuto y cuenta
# prompt + max_tokens reservados. Con la capa B completa (~4900 tokens) mas la
# capa A (~3475) ninguna peticion de banda cabia y Groq respondia 429 SIEMPRE.
# Las secciones excluidas no aportan nada nuevo al modelo:
#   - USO, NORMALIZADOR, VALIDACION DE COBERTURA: describen el backend, no la
#     lectura de la instruccion.
#   - HARDWARE DISPONIBLE, CAPACIDADES DE SENSORES, PRINCIPIO DE EVENTOS,
#     ACCION INMEDIATA VS CONTADOR, SIMULTANEIDAD, SECUENCIA, CONDICIONES,
#     CONFLICTOS, NO INVENTAR HARDWARE, DEFAULTS, REGLAS GENERALES: ya estan
#     en SYSTEM_PROMPT_BANDA (capa A), palabra por palabra en varios casos.
#   - SALIDA INTERMEDIA RECOMENDADA DEL LLM: propone un esquema DISTINTO
#     ("movement", "sensor1", "on_detection") que contradice el de la capa A.
#   - OBJETIVO FINAL: prosa sin reglas.
# Lo que queda es justo el conocimiento que la capa A no tiene: que significa
# cada proceso industrial, que inferir de cada industria y como aterrizar una
# peticion vaga.
SECCIONES_PROMPT = (
    "ARQUETIPOS DE PROCESO",
    "REFERENCIAS POR INDUSTRIA",
    "PETICIONES VAGAS",
    "COMPOSICION",
)

# Titulo de seccion del archivo: una linea en MAYUSCULAS seguida de una linea
# de guiones. Se usa para partir el archivo sin reescribirlo.
_TITULO_SECCION_RE = re.compile(r"^([A-Z][A-Z0-9 _/-]*)\n-{3,}$", re.M)


def _secciones(texto: str) -> list:
    """[(titulo, cuerpo)] del archivo de contexto, en orden."""
    marcas = list(_TITULO_SECCION_RE.finditer(texto))
    out = []
    for i, m in enumerate(marcas):
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        out.append((m.group(1).strip(), texto[m.end():fin].strip()))
    return out


def compactar_contexto(texto: str) -> str:
    """Solo las secciones de SECCIONES_PROMPT, con su titulo.

    Si el archivo cambia de formato y no se reconoce ninguna seccion, devuelve
    el texto tal cual: nunca se queda sin contexto industrial."""
    secs = [(t, c) for t, c in _secciones(texto) if t in SECCIONES_PROMPT]
    if not secs:
        return texto
    return "\n\n".join(f"{t}\n{'-' * len(t)}\n{c}" for t, c in secs)


# Arquetipos de proceso reconocidos (§5). El LLM puede devolver varios; un
# valor fuera de esta lista se reporta como error de forma, no se inventa.
ARQUETIPOS = (
    "continuous_transport", "indexed_transport", "counting", "batching",
    "sorting", "rejection", "inspection", "positioning", "buffering",
    "congestion_control", "signaling", "timed_process", "count_based_sorting",
    "direction_change", "filling_simulation", "packaging", "boxing",
    "pallet_feed", "two_route_split", "alternating_routes",
    "two_station_process", "rework", "flow_control", "end_of_line",
)

# Industrias reconocidas para process_reference.industry (§12). "otra" deja
# pasar cualquier caso que no encaje sin inventar una categoria nueva.
INDUSTRIAS = (
    "food_beverage", "parcel_distribution", "automotive", "electronics",
    "pharma", "warehouse_logistics", "otra",
)

# Resumen minimo de emergencia: solo se usa si el archivo de contexto no se
# puede leer, para que el sistema no pierda del todo la capa B.
_RESUMEN_MINIMO = """CONTEXTO INDUSTRIAL (resumen de emergencia: no se pudo leer el archivo completo).
Arquetipos de proceso disponibles: """ + ", ".join(ARQUETIPOS) + """.
Industrias: """ + ", ".join(INDUSTRIAS) + """.
Una peticion vaga ("banda de produccion", "empaquetado", "clasificacion") se resuelve con
una propuesta base: I1 arranca, verde durante RUN, S1 detecta/cuenta, S2 confirma salida, y
los parametros que no se dijeron quedan en null y se piden antes de ejecutar.
El nombre de una empresa solo sirve para inferir industria y arquetipos; nunca se afirma que
esa empresa use exactamente la secuencia propuesta.
Nunca se inventa hardware: solo banda, S1, S2, pluma 1, pluma 2, torreta, timers y contadores."""


# ---------------------------------------------------------------------------
# CARGA DEL ARCHIVO (capa B)
# ---------------------------------------------------------------------------
_cache = {"ruta": None, "texto": None}


def cargar_contexto_industrial(ruta: str = None, recargar: bool = False) -> str:
    """Texto del archivo de contexto industrial, cacheado.

    Devuelve el resumen minimo si el archivo no existe o no se puede leer:
    la capa A nunca depende de esto."""
    ruta = ruta or CONTEXTO_BANDA_PATH
    if not recargar and _cache["ruta"] == ruta and _cache["texto"] is not None:
        return _cache["texto"]
    texto = _RESUMEN_MINIMO
    try:
        if os.path.exists(ruta):
            with open(ruta, encoding="utf-8") as f:
                contenido = f.read().strip()
            if contenido:
                texto = contenido[:MAX_CARACTERES_CONTEXTO]
                if len(contenido) > MAX_CARACTERES_CONTEXTO:
                    log.warning(f"Contexto industrial recortado a {MAX_CARACTERES_CONTEXTO} caracteres.")
            else:
                log.warning(f"'{ruta}' esta vacio: se usa el resumen minimo de contexto industrial.")
        else:
            log.warning(f"No se encontro '{ruta}': se usa el resumen minimo de contexto industrial.")
    except Exception as e:                                  # pragma: no cover
        log.warning(f"No se pudo leer '{ruta}' ({e}): se usa el resumen minimo.")
    _cache["ruta"], _cache["texto"] = ruta, texto
    return texto


def contexto_disponible(ruta: str = None) -> bool:
    """¿Se esta usando el archivo real (True) o el resumen minimo (False)?"""
    return cargar_contexto_industrial(ruta) is not _RESUMEN_MINIMO


# ---------------------------------------------------------------------------
# EXTENSION DEL ESQUEMA (capa semantica de la intencion)
# ---------------------------------------------------------------------------
# Campos NUEVOS y OPCIONALES de la intencion. No sustituyen ningun campo del
# esquema anterior: "movimiento", "paros", "eventos", "luces", "luz_temporizada",
# "plumas_manual" y "no_soportado" siguen siendo exactamente los mismos y
# siguen siendo los unicos que producen registros.
EXTENSION_ESQUEMA = """CAPA SEMANTICA (campos NUEVOS y OPCIONALES de "intencion"):
Los campos de siempre ("movimiento", "paros", "eventos", "luces", "luz_temporizada",
"plumas_manual", "no_soportado") NO cambian y siguen siendo los unicos que llegan al PLC.
Ademas puedes agregar estos campos descriptivos:

  "process_reference": {"industry": null|"food_beverage"|"parcel_distribution"|"automotive"|
                        "electronics"|"pharma"|"warehouse_logistics"|"otra",
                        "reference": null|"texto corto", "exact_company_process_claim": false}
  "archetypes": [] uno o VARIOS de: """ + ", ".join(ARQUETIPOS) + """
  "supuestos": ["lo que asumiste porque el usuario no lo dijo"]
  "parametros_faltantes": [{"campo": "frecuencia_hz"|"conteo"|"duracion_s"|...,
                            "pregunta": "pregunta corta", "opciones": ["sug 1", "sug 2"]}]
  "conflictos": ["ordenes que se contradicen en el MISMO evento"]
  "unsupported_capability": ["parte CONCEPTUAL del proceso que este hardware no tiene"]

REGLAS:
- "archetypes" describe QUE proceso es; no ejecuta nada. Si la instruccion combina procesos
  ("transporta, cuenta 5 y clasifica"), pon TODOS, no solo uno.
- "exact_company_process_claim" es SIEMPRE false: el nombre de una empresa solo infiere industria
  y arquetipos; nunca afirmes que esa empresa usa esta secuencia.
- Un arquetipo se ejecuta SOLO con el hardware real (banda, S1, S2, pluma 1, pluma 2, torreta,
  timers, contadores): "sorting" con una pluma como desviador, "inspection" con una pausa y una
  luz, "filling_simulation" con una pausa temporizada. Camaras, robots, valvulas, cilindros y
  lectores NO existen.
- DOS listas distintas, no las confundas:
  * "no_soportado": algo que el USUARIO PIDIO y el PLC no puede hacer. BLOQUEA, como siempre.
  * "unsupported_capability": parte conceptual del proceso sin hardware aqui, que el usuario no
    pidio expresamente. NO bloquea: se avisa y el proceso se aterriza con lo que si existe
    (p. ej. "no hay camara: la ruta la elige la pluma 1, no el tipo de producto").
- Un parametro esencial que no se dijo NO se inventa: null + "parametros_faltantes".
- Si dos ordenes se contradicen en el mismo evento, NO elijas: "conflictos" y el campo en null.
- Una instruccion especifica se responde igual que siempre; "archetypes" a lo sumo describe."""


CABECERA_CAPA_B = """CONOCIMIENTO ADICIONAL — CONTEXTO INDUSTRIAL DE LA BANDA (CAPA B).

SE SUMA a las instrucciones anteriores; no las reemplaza. Las reglas, ejemplos y esquema del
mensaje anterior siguen vigentes tal cual. Sirve para entender tambien instrucciones GENERALES
o CONCEPTUALES ("una banda como PepsiCo", "un proceso de empaquetado", "clasificacion", "un buffer").

Prioridad cuando algo se contradiga: 1) capacidades fisicas reales del ST (banda, S1, S2, pluma 1,
pluma 2, torreta, timers, contadores); 2) reglas de hardware del mensaje anterior; 3) lo que el
usuario pidio explicitamente; 4) este contexto industrial; 5) valores por defecto.
Este contexto NUNCA sobrescribe una limitacion fisica ni inventa hardware.

Si la instruccion nombra S1, S2, plumas, luces, Hz, segundos o conteos, interpretala EXACTAMENTE
con las reglas del mensaje anterior: este contexto solo agrega la lectura conceptual."""


def bloque_industrial_prompt(ruta: str = None) -> str:
    """Mensaje de sistema ADICIONAL con la capa B completa.

    Se envia DESPUES de SYSTEM_PROMPT_BANDA. Estructura:
        cabecera (como se combinan las dos capas y que manda)
        + secciones utiles del contexto industrial (arquetipos, industrias,
          peticiones vagas, composicion), no el archivo entero
        + extension del esquema JSON (campos semanticos nuevos y opcionales)
    """
    return (CABECERA_CAPA_B
            + "\n" + "=" * 70 + "\n"
            + compactar_contexto(cargar_contexto_industrial(ruta))
            + "\n" + "=" * 70 + "\n"
            + EXTENSION_ESQUEMA)


# Ejemplo de la capa B. Solo UNO a proposito: con el limite de 8000 tokens/minuto
# del plan gratuito, cada ejemplo extra (~300 tokens) se come el presupuesto de la
# respuesta. Este es el caso canonico (peticion vaga + referencia a una empresa) y
# muestra TODOS los campos nuevos; el resto de arquetipos ya viene descrito en el
# contexto industrial de arriba. Los ejemplos de instrucciones ESPECIFICAS estan en
# la capa A, que siempre viaja.
EJEMPLOS_CAPA_B = """EJEMPLO DE INSTRUCCION CONCEPTUAL (capa B). El hardware es el de siempre:

Peticion: "quiero una banda como PepsiCo"
JSON: {"name":"Linea continua con conteo y señalizacion","intencion":{
 "process_reference":{"industry":"food_beverage","reference":"linea de bebidas estilo PepsiCo","exact_company_process_claim":false},
 "archetypes":["continuous_transport","counting","flow_control","signaling"],
 "movimiento":{"mover":true,"direccion":"derecha","frecuencia_hz":null,"boton_inicio":"I1","paro_automatico":null},
 "paros":{"i2":false,"software":false},
 "eventos":[
  {"sensor":1,"conteo":null,"banda":"no_afecta","duracion_s":null,"luces":[],"pluma1":null,"pluma2":null,"al_contar":null},
  {"sensor":2,"conteo":null,"banda":"pausa_mientras_detecta","duracion_s":null,"luces":["amarilla"],"pluma1":null,"pluma2":null,"al_contar":null}],
 "luces":{"corriendo":["verde"],"detenida":[],"mientras_i1":[]},"luz_temporizada":null,
 "plumas_manual":{"pluma1":null,"pluma2":null},"no_soportado":[],
 "supuestos":["S1 observa el flujo y S2 pausa la banda mientras la salida siga ocupada","verde mientras la banda corre"],
 "parametros_faltantes":[{"campo":"frecuencia_hz","pregunta":"¿A que frecuencia debe avanzar la banda (1 a 327 Hz)?","opciones":["20 Hz","30 Hz","40 Hz"]},
                         {"campo":"conteo","pregunta":"¿Cuantas piezas forman un lote?","opciones":["5","10","20"]}],
 "conflictos":[]}}"""


def mensaje_sistema_industrial(ruta: str = None) -> dict:
    """Mensaje de sistema listo para la lista `messages` del modelo."""
    return {"role": "system",
            "content": bloque_industrial_prompt(ruta) + "\n" + "=" * 70 + "\n" + EJEMPLOS_CAPA_B}


# ---------------------------------------------------------------------------
# ¿ESTA PETICION NECESITA LA CAPA B?
# ---------------------------------------------------------------------------
# La capa B solo aporta cuando hay algo CONCEPTUAL que traducir. Una instruccion
# especifica ("cuando S1 detecte sube la pluma 1", "avanza a 20 Hz") se resuelve
# entera con la capa A, que es la que manda; mandar ademas la capa B solo gasta
# tokens del limite por minuto y, cuando no cabe, Groq rechaza la peticion entera.
#
# Regla, en este orden:
#   1. vocabulario de proceso industrial  -> SI (es lo que la capa B sabe leer)
#   2. hardware/parametros concretos      -> NO (la capa A basta)
#   3. nada de lo anterior (peticion vaga)-> SI (la capa B propone la base)
# Ante la duda se manda: perder la capa B en una peticion conceptual cambiaria la
# respuesta; mandarla de mas solo cuesta tokens.

# Hardware, parametros y verbos que la capa A ya interpreta sola.
_ESPECIFICO_TERMS = [
    r"\bs\s?[12]\b", r"\bsensor(?:es)?\s*(?:1|2|uno|dos)\b",
    r"\bplumas?\b", r"\btorreta\b",
    r"\b\d+(?:[.,]\d+)?\s*hz\b", r"\bhertz\b", r"\bfrecuencia",
    r"\b\d+\s*(?:s|seg|segs|segundos?|minutos?)\b",
    r"\bverde\b", r"\bamarilla\b", r"\broj[ao]\b",
    r"\blampara", r"\bluces?\b", r"\bluz\b",
    r"\bi\s?[1234]\b",
    r"\bderecha\b", r"\bizquierda\b", r"\bhorario\b", r"\bantihorario\b",
    r"\bavanz", r"\bmuev", r"\bmover\b", r"\barranc", r"\bcorre\b", r"\bgir[ae]",
    r"\bdeten", r"\bpaus", r"\bparo\b", r"\bpara\b",
    r"\bsub[ei]", r"\bbaj[ae]",
    r"\bcuent[ae]", r"\bcontar\b", r"\bconteo\b", r"\bcontador",
    r"\bdetect", r"\bdeteccion",
    r"\bprend[ae]", r"\bencend", r"\benciend", r"\bapag",
    r"\btemporizador", r"\btimer\b",
]

# Vocabulario conceptual: se reutiliza el del router (unica fuente de verdad) y se
# suman las formas vagas que no nombran ningun proceso concreto.
_VAGO_TERMS = [
    r"\bproceso\b", r"\bprocesos\b", r"\blinea\b", r"\bplanta\b",
    r"\bindustrial\b", r"\bfabrica", r"\bsimula", r"\bejemplo\b",
    r"\balgo\b", r"\bcomo (?:la|el|una?|en)\b", r"\btipo\b", r"\bestilo\b",
]

_ESPECIFICO_RE = [re.compile(x) for x in _ESPECIFICO_TERMS]
_VAGO_RE = [re.compile(x) for x in _VAGO_TERMS]


def _normalizar(texto: str) -> str:
    """Minusculas y sin acentos. Espejo de device_router.normalizar; se define
    aqui para que este modulo no dependa de que el router exista."""
    import unicodedata
    t = str(texto or "").lower()
    t = unicodedata.normalize("NFD", t)
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _terminos_proceso():
    """Patrones de proceso industrial del router. Si el router no esta
    disponible, se queda solo con las formas vagas: nunca revienta."""
    try:
        import device_router
        return [re.compile(x) for x in device_router.PROCESO_TERMS]
    except Exception as e:                                  # pragma: no cover
        log.warning(f"device_router no disponible para la capa B ({e}).")
        return []


_PROCESO_RE = None


def requiere_contexto_industrial(texto: str) -> bool:
    """¿Hay que mandarle la capa B al modelo para esta instruccion?"""
    global _PROCESO_RE
    if _PROCESO_RE is None:
        _PROCESO_RE = _terminos_proceso()
    t = _normalizar(texto)
    if not t.strip():
        return True
    if any(r.search(t) for r in _PROCESO_RE) or any(r.search(t) for r in _VAGO_RE):
        return True
    return not any(r.search(t) for r in _ESPECIFICO_RE)


def motivo_contexto_industrial(texto: str) -> str:
    """Explicacion corta de la decision, para el log."""
    global _PROCESO_RE
    if _PROCESO_RE is None:
        _PROCESO_RE = _terminos_proceso()
    t = _normalizar(texto)
    if not t.strip():
        return "peticion vacia"
    if any(r.search(t) for r in _PROCESO_RE):
        return "nombra un proceso industrial"
    if any(r.search(t) for r in _VAGO_RE):
        return "peticion conceptual o vaga"
    if any(r.search(t) for r in _ESPECIFICO_RE):
        return "instruccion especifica: la capa A basta"
    return "sin senales concretas: peticion vaga"
