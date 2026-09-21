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

log = logging.getLogger("uvicorn.error")

# Ruta del archivo de contexto industrial. Configurable por entorno para que
# Render pueda apuntar a otra copia sin tocar el codigo.
CONTEXTO_BANDA_PATH = os.environ.get(
    "CONTEXTO_BANDA_INDUSTRIAL", "context/Contexto_Industrial_para_Modelo_Banda.txt")

# Tope de caracteres que se inyectan en el prompt (el archivo completo cabe
# de sobra; el tope evita que una edicion enorme dispare el costo del modelo).
MAX_CARACTERES_CONTEXTO = 20000

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
Ademas de los campos de siempre ("movimiento", "paros", "eventos", "luces", "luz_temporizada",
"plumas_manual", "no_soportado"), que NO cambian y siguen siendo los unicos que llegan al PLC,
puedes agregar estos campos descriptivos:

  "process_reference": {"industry": null|"food_beverage"|"parcel_distribution"|"automotive"|
                                    "electronics"|"pharma"|"warehouse_logistics"|"otra",
                        "reference": null|"texto corto, p. ej. 'linea de bebidas estilo PepsiCo'",
                        "exact_company_process_claim": false}
  "archetypes": [] lista con uno o VARIOS de: """ + ", ".join(ARQUETIPOS) + """
  "supuestos": ["frases cortas con lo que asumiste porque el usuario no lo dijo"]
  "parametros_faltantes": [{"campo": "frecuencia_hz"|"conteo"|"duracion_s"|...,
                            "pregunta": "pregunta corta y concreta para el usuario",
                            "opciones": ["sugerencia 1", "sugerencia 2"]}]
  "conflictos": ["frases cortas con ordenes que se contradicen en el MISMO evento"]
  "unsupported_capability": ["parte CONCEPTUAL del proceso que este hardware no puede hacer"]

REGLAS DE LA CAPA SEMANTICA:
- "archetypes" describe QUE proceso es; NO ejecuta nada por si solo. Si una instruccion combina
  procesos ("transporta, cuenta 5 y clasifica"), pon TODOS los arquetipos, no solo uno.
- "exact_company_process_claim" es SIEMPRE false: el nombre de una empresa solo sirve para
  inferir industria y arquetipos. Nunca afirmes que esa empresa usa esta secuencia.
- Un arquetipo se ejecuta SOLO con el hardware real (banda, S1, S2, pluma 1, pluma 2, torreta,
  timers, contadores). "sorting" se hace con una pluma como desviador; "inspection" con una
  pausa y una luz; "filling_simulation" con una pausa temporizada. Camaras, robots, valvulas,
  cilindros y lectores NO existen.
- DOS listas distintas para lo que no se puede hacer, no las confundas:
  * "no_soportado": algo que el USUARIO PIDIO EXPRESAMENTE y este PLC no puede hacer. BLOQUEA la
    ejecucion (se le explica al usuario). Se usa igual que siempre.
  * "unsupported_capability": una parte CONCEPTUAL del proceso industrial que este hardware no
    tiene (la camara de una inspeccion, el robot de un paletizado) pero que el usuario no pidio
    expresamente. NO bloquea: se avisa y el proceso se aterriza con el hardware que si existe.
- Un parametro esencial que el usuario no dio NO se inventa: se deja null y se anota en
  "parametros_faltantes".
- Si dos ordenes se contradicen en el mismo evento (misma pluma subir y bajar con S1), NO
  elijas una: anotalas en "conflictos" y deja ese campo en null.
- Estos campos NO cambian nada de lo anterior: una instruccion especifica ("cuando S1 detecte
  sube la pluma 1") se responde igual que siempre, con "archetypes" a lo sumo descriptivo."""


CABECERA_CAPA_B = """CONOCIMIENTO ADICIONAL — CONTEXTO INDUSTRIAL DE LA BANDA (CAPA B).

Esto SE SUMA a las instrucciones anteriores; no las reemplaza. Las reglas, los ejemplos y el
esquema del mensaje anterior siguen vigentes tal cual.

Para que sirve: entender tambien instrucciones GENERALES o CONCEPTUALES ("quiero una banda como
PepsiCo", "haz un proceso de empaquetado", "quiero clasificacion", "quiero un buffer"), ademas de
las instrucciones especificas de siempre.

Orden de prioridad cuando algo se contradiga:
  1. capacidades fisicas reales del ST (banda, S1, S2, pluma 1, pluma 2, torreta, timers, contadores)
  2. reglas de hardware del mensaje anterior
  3. lo que el usuario pidio explicitamente
  4. este contexto industrial
  5. valores por defecto
El contexto industrial NUNCA sobrescribe una limitacion fisica ni inventa hardware.

Si la instruccion es especifica (nombra S1, S2, plumas, luces, Hz, segundos o conteos), interpretala
EXACTAMENTE con las reglas del mensaje anterior: este contexto solo agrega la lectura conceptual.
"""


def bloque_industrial_prompt(ruta: str = None) -> str:
    """Mensaje de sistema ADICIONAL con la capa B completa.

    Se envia DESPUES de SYSTEM_PROMPT_BANDA. Estructura:
        cabecera (como se combinan las dos capas y que manda)
        + archivo de contexto industrial (arquetipos, industrias, peticiones vagas)
        + extension del esquema JSON (campos semanticos nuevos y opcionales)
    """
    return (CABECERA_CAPA_B
            + "\n" + "=" * 70 + "\n"
            + cargar_contexto_industrial(ruta)
            + "\n" + "=" * 70 + "\n"
            + EXTENSION_ESQUEMA)


# Ejemplos de la capa B: instrucciones conceptuales resueltas con hardware
# real. Van en el MISMO mensaje adicional, detras del contexto, para que el
# modelo vea como se aterriza un arquetipo sin inventar nada.
EJEMPLOS_CAPA_B = """EJEMPLOS DE INSTRUCCIONES CONCEPTUALES (capa B). Fijate en que el hardware es el de siempre:

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
 "conflictos":[]}}

Peticion: "quiero clasificacion"
JSON: {"name":"Clasificacion con pluma desviadora","intencion":{
 "process_reference":{"industry":null,"reference":null,"exact_company_process_claim":false},
 "archetypes":["sorting","counting"],
 "movimiento":{"mover":true,"direccion":"derecha","frecuencia_hz":null,"boton_inicio":"I1","paro_automatico":null},
 "paros":{"i2":false,"software":false},
 "eventos":[{"sensor":1,"conteo":null,"banda":"no_afecta","duracion_s":null,"luces":[],"pluma1":"subir","pluma2":null,"al_contar":null}],
 "luces":{"corriendo":["verde"],"detenida":[],"mientras_i1":[]},"luz_temporizada":null,
 "plumas_manual":{"pluma1":null,"pluma2":null},
 "no_soportado":[],
 "unsupported_capability":["no hay camara ni lector: la ruta se elige con la pluma 1, no por el tipo de producto"],
 "supuestos":["la pluma 1 hace de desviador hacia la ruta alterna cuando S1 detecta"],
 "parametros_faltantes":[{"campo":"frecuencia_hz","pregunta":"¿A que frecuencia debe avanzar la banda (1 a 327 Hz)?","opciones":["20 Hz","30 Hz","40 Hz"]}],
 "conflictos":[]}}

Peticion: "quiero contar cajas para un pallet"
JSON: {"name":"Lote de cajas para pallet","intencion":{
 "process_reference":{"industry":"warehouse_logistics","reference":null,"exact_company_process_claim":false},
 "archetypes":["counting","batching","pallet_feed"],
 "movimiento":{"mover":true,"direccion":"derecha","frecuencia_hz":null,"boton_inicio":"I1","paro_automatico":null},
 "paros":{"i2":false,"software":false},
 "eventos":[{"sensor":1,"conteo":null,"banda":"no_afecta","duracion_s":null,"luces":[],"pluma1":null,"pluma2":null,
   "al_contar":{"pausar_banda":true,"detener_proceso":false,"luces":["amarilla"],"direccion":null,"pluma1":null,"pluma2":null,"duracion_s":null}}],
 "luces":{"corriendo":["verde"],"detenida":[],"mientras_i1":[]},"luz_temporizada":null,
 "plumas_manual":{"pluma1":null,"pluma2":null},
 "no_soportado":[],
 "unsupported_capability":["no hay robot paletizador: el pallet completo solo se señaliza con la torreta"],
 "supuestos":["al completar el lote la banda se pausa y la amarilla avisa que el pallet esta listo"],
 "parametros_faltantes":[{"campo":"conteo","pregunta":"¿Cuantas cajas forman el pallet?","opciones":["5","10","20"]},
                         {"campo":"frecuencia_hz","pregunta":"¿A que frecuencia debe avanzar la banda (1 a 327 Hz)?","opciones":["20 Hz","30 Hz","40 Hz"]}],
 "conflictos":[]}}

Peticion: "cuando S1 detecte sube la pluma 1"   (instruccion ESPECIFICA: se responde como siempre)
JSON: {"name":"S1 sube pluma 1","intencion":{
 "process_reference":{"industry":null,"reference":null,"exact_company_process_claim":false},
 "archetypes":["positioning"],
 "movimiento":{"mover":false,"direccion":null,"frecuencia_hz":null,"boton_inicio":null,"paro_automatico":null},
 "paros":{"i2":false,"software":false},
 "eventos":[{"sensor":1,"conteo":null,"banda":"no_afecta","duracion_s":null,"luces":[],"pluma1":"subir","pluma2":null,"al_contar":null}],
 "luces":{"corriendo":[],"detenida":[],"mientras_i1":[]},"luz_temporizada":null,
 "plumas_manual":{"pluma1":null,"pluma2":null},"no_soportado":[],
 "supuestos":[],"parametros_faltantes":[],"conflictos":[]}}"""


def mensaje_sistema_industrial(ruta: str = None) -> dict:
    """Mensaje de sistema listo para la lista `messages` del modelo."""
    return {"role": "system",
            "content": bloque_industrial_prompt(ruta) + "\n" + "=" * 70 + "\n" + EJEMPLOS_CAPA_B}
