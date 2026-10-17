"""
============================================================================
CONTEXTO POR RAMIFICACIONES (que recibe el modelo en cada peticion)
============================================================================
Tres niveles de conocimiento, separados y nunca mezclados:

  Nivel 1  memoria validada (memoria_validada.py): lo que el usuario aprobo.
  Nivel 2  contexto tecnico LOCAL (fuente de verdad del hardware):
             maletin -> codigos/ (via context_json/contexto.json)
             banda   -> cscape/LAVO_Referencia_v7.txt + la guia y los ejemplos
                        del prompt v7 (que ya describen el ST v7)
  Nivel 3  catalogo de procesos industriales GENERICOS
           (procesos_industriales/): referencias investigadas, no ejecutables.

Ramificacion de la BANDA (motor v7):
  siempre       nucleo: hardware, seguridad, senales, bloques y esquema JSON
  hay memoria   + memorias validadas relevantes (sin guia ni ejemplos)
  no hay        + guia rapida + ejemplos + limites del programa maestro
  general/vaga  + proceso del catalogo y reglas para adaptarlo al hardware real
                  (solo si la instruccion NO describe ya la secuencia)
Ramificacion del MALETIN: su prompt de siempre + memorias relevantes y, si no
hay memoria adecuada, los programas de codigos/ relacionados con la peticion.
============================================================================
"""

import json
import logging
import os
import re

import memoria_validada as mv
import validador_esquema

log = logging.getLogger(__name__)
AQUI = os.path.dirname(os.path.abspath(__file__))

CARPETA_PROCESOS = os.path.join(AQUI, "procesos_industriales")
REFERENCIA_V7 = os.path.join(AQUI, "cscape", "LAVO_Referencia_v7.txt")
CONTEXTO_MALETIN = os.path.join(AQUI, "context_json", "contexto.json")
MAX_JSON_MEMORIA = 1500
MAX_PROCESOS = 2


# ---------------------------------------------------------------------------
# NIVEL 3: catalogo de procesos industriales
# ---------------------------------------------------------------------------
_catalogo = {"procesos": None}


def catalogo() -> list:
    """Procesos del catalogo (validados contra esquemas/proceso_schema.json)."""
    if _catalogo["procesos"] is None:
        procesos = []
        try:
            with open(os.path.join(CARPETA_PROCESOS, "indice.json"), encoding="utf-8") as f:
                indice = json.load(f)
            esquema = validador_esquema.cargar("proceso_schema.json")
            for e in indice.get("procesos", []):
                with open(os.path.join(CARPETA_PROCESOS, e["archivo"]), encoding="utf-8") as f:
                    p = json.load(f)
                fallas = validador_esquema.validar(p, esquema)
                if fallas:
                    log.warning(f"Proceso {e['archivo']} ignorado: {fallas[:3]}")
                    continue
                procesos.append(p)
        except Exception as e:
            log.warning(f"Catalogo de procesos no disponible: {e}")
        _catalogo["procesos"] = procesos
    return _catalogo["procesos"]


def procesos_mencionados(texto: str) -> list:
    """Procesos del catalogo cuyo nombre o sinonimo aparece en el texto."""
    t = mv.normalizar_texto(texto)
    hallados = []
    for p in catalogo():
        mejor = 0
        for s in p.get("sinonimos", []) + [p["id"].replace("_", " ")]:
            sn = mv.normalizar_texto(s)
            if sn and re.search(rf"\b{re.escape(sn)}", t):
                mejor = max(mejor, len(sn))
        if mejor:
            hallados.append((mejor, p))
    hallados.sort(key=lambda x: -x[0])
    return [p for _, p in hallados[:MAX_PROCESOS]]


_GENERICAS = {"produccion", "trabajo", "proceso", "control", "la", "una", "un", "mi", "tu", "banda",
              "transporte", "transportadora"}


def proceso_desconocido(texto: str):
    """Nombre de un proceso que la instruccion pide y el catalogo no tiene."""
    t = mv.normalizar_texto(texto)
    m = re.search(r"\b(?:linea|proceso|estacion|planta|sistema|maquina) de ([a-z]+)", t)
    if not m or m.group(1) in _GENERICAS:
        return None
    if procesos_mencionados(texto):
        return None
    return m.group(1)


def es_peticion_general(texto: str) -> bool:
    """La instruccion describe un proceso o es vaga (no dice la secuencia)."""
    try:
        import banda_contexto
        return banda_contexto.requiere_contexto_industrial(texto)
    except Exception as e:                     # pragma: no cover
        log.warning(f"banda_contexto no disponible ({e}); se trata como especifica.")
        return False


def _proceso_compacto(p: dict) -> dict:
    return {
        "proceso": p["nombre"],
        "etapas": [{"n": e["orden"], "etapa": e["nombre"], "acciones": e["acciones"], "pasa_cuando": e["transicion"]}
                   for e in p["etapas"]],
        "entradas_funcionales": [x["nombre"] for x in p["entradas_funcionales"]],
        "salidas_funcionales": [x["nombre"] for x in p["salidas_funcionales"]],
        "temporizadores": [{k: t.get(k) for k in ("nombre", "valor_tipico_s")} for t in p["temporizadores"]],
        "contadores": [{k: c.get(k) for k in ("nombre", "valor_tipico")} for c in p["contadores"]],
        "repeticiones": p["repeticiones"].get("modo"),
        "arranque": p["arranque"], "paro": p["paro"], "resultado_esperado": p["resultado_esperado"],
        "componentes_tipicos_que_quiza_no_existan": p.get("requiere_componentes_tipicos", []),
    }


REGLAS_ADAPTACION_BANDA = """PROCESO INDUSTRIAL DE REFERENCIA (nivel 3: catalogo GENERICO investigado).
NO es una logica validada ni describe este hardware: es solo la secuencia tipica del proceso.
La instruccion del usuario es general; construye una propuesta adaptando esa secuencia a los
recursos REALES de esta banda:
- sensores de presencia o conteo -> S1 (I4) y S2 (I5); solo existen esos dos.
- desviador, compuerta, tope, empujador o actuador de carga -> pluma 1 (Q8 sube / Q9 baja)
  o pluma 2 (Q6 sube / Q7 baja).
- operacion de una estacion (llenar, etiquetar, taponar, ensamblar, sellar) -> pausa
  temporizada de la banda + una luz de la torreta mientras dura (es una SIMULACION).
- indicadores -> torreta Q3 verde, Q4 amarilla, Q5 roja.
- valvulas, robots, camaras, etiquetadoras, sensores de nivel, peso, tamano o tipo de producto
  NO EXISTEN: nunca asumas que existen; reportalos en analisis.simplificado (si se simulo) o en
  analisis.no_soportado (si no hay forma).
- arranque I1, paro I2 salvo que el usuario diga otra cosa; I3 siempre es el paro de emergencia.
- valores que el usuario no dio: usa los tipicos del proceso y reportalos en analisis.modificado;
  si la banda se mueve y no dio los Hz, frecuencia_hz = null.
- analisis.requiere_confirmacion = true: es una propuesta adaptada, no lo que el usuario dicto."""


def bloque_procesos_banda(procesos: list) -> str:
    cuerpo = [REGLAS_ADAPTACION_BANDA]
    for p in procesos:
        cuerpo.append("PROCESO: " + json.dumps(_proceso_compacto(p), ensure_ascii=False, separators=(",", ":")))
    return "\n".join(cuerpo)


# ---------------------------------------------------------------------------
# NIVEL 2: contexto tecnico local
# ---------------------------------------------------------------------------
_cache = {}


def partir_prompt_v7(prompt: str) -> dict:
    """SYSTEM_PROMPT_BANDA_V7 -> nucleo / guia / ejemplos.

    El nucleo conserva TODO lo critico (hardware, seguridad, senales, bloques,
    las reglas de Hz/arranque/Q10-Q12 y el esquema). Si el texto cambia de
    forma y no se encuentran las marcas, todo queda en el nucleo."""
    if prompt in _cache:
        return _cache[prompt]
    try:
        i_guia = prompt.index("\nGUIA RAPIDA\n")
        i_esq = prompt.index("\nESQUEMA\n")
        i_ej = prompt.index("\nEJEMPLOS\n")
        guia = prompt[i_guia:i_esq]
        i_reglas = guia.index("Si la banda se mueve y no dicen los Hz")
        partes = {
            "nucleo": (prompt[:i_guia] + "\n\nREGLAS CRITICAS\n" + guia[i_reglas:].strip()
                       + "\n" + prompt[i_esq:i_ej]).strip(),
            "guia": guia[:i_reglas].strip(),
            "ejemplos": prompt[i_ej:].strip(),
        }
    except ValueError:
        log.warning("SYSTEM_PROMPT_BANDA_V7 sin las marcas esperadas: se manda completo.")
        partes = {"nucleo": prompt, "guia": "", "ejemplos": ""}
    _cache[prompt] = partes
    return partes


def limites_banda_v7() -> str:
    """Seccion '5. LIMITES' de cscape/LAVO_Referencia_v7.txt (fuente local)."""
    if "limites" not in _cache:
        texto = ""
        try:
            with open(REFERENCIA_V7, "rb") as f:
                crudo = f.read()
            try:
                ref = crudo.decode("utf-8")
            except UnicodeDecodeError:
                ref = crudo.decode("cp1252")          # el archivo lo genera Cscape/Windows
            m = re.search(r"^5\. LIMITES.*?$(.*?)(?=^\d+\. |\Z)", ref, re.S | re.M)
            if m:
                texto = "LIMITES DEL PROGRAMA MAESTRO (cscape/LAVO_Referencia_v7.txt)\n" + \
                        re.sub(r"^-+\s*$", "", m.group(1), flags=re.M).strip()
        except Exception as e:
            log.warning(f"No se pudo leer {REFERENCIA_V7}: {e}")
        _cache["limites"] = texto
    return _cache["limites"]


# Programas de codigos/ que explican cada funcion del maletin.
_PROGRAMAS_MALETIN = [
    ("enclavamiento", "01_"), ("contador", "02_"), ("contador+temporizador", "05_"),
    ("temporizador_off", "03_"), ("temporizador", "04_"), ("inversion", "07_"),
]


def _programas_maletin():
    if "maletin" not in _cache:
        try:
            with open(CONTEXTO_MALETIN, encoding="utf-8") as f:
                _cache["maletin"] = json.load(f).get("programas", [])
        except Exception as e:
            log.warning(f"No se pudo leer {CONTEXTO_MALETIN}: {e}")
            _cache["maletin"] = []
    return _cache["maletin"]


def contexto_tecnico_maletin(texto: str, maximo: int = 2) -> tuple:
    """(bloque, nombres) con los programas de codigos/ relacionados."""
    t = mv.normalizar_texto(texto)
    tags = mv.etiquetas_texto(texto)
    claves = []
    if "enclavamiento" in tags:
        claves.append("01_")
    if "contador" in tags:
        claves.append("05_" if "temporizador" in tags else "02_")
    if "temporizador" in tags or "intermitente" in tags:
        off = re.search(r"al soltar|despues de soltar|se apaga despues|off delay|\btof\b|desconexion", t)
        claves.append("03_" if off else "04_")
    if re.search(r"invier|inversion|selector|altern|cambia de color", t):
        claves.append("07_")
    elegidos = []
    for p in _programas_maletin():
        if any(p.get("nombre", "").startswith(c) for c in claves) and p not in elegidos:
            elegidos.append(p)
    elegidos = elegidos[:maximo]
    if not elegidos:
        return "", []
    partes = ["REFERENCIA TECNICA DEL MALETIN (programas reales de codigos/, nivel 2).",
              "Explican como se comporta cada funcion en el Ladder fisico. Sus direcciones (%Q1, %M1, ...)",
              "son internas de esos programas: tu respuesta usa SOLO el HARDWARE FIJO y el esquema indicado."]
    for p in elegidos:
        partes.append(f"\n- {p.get('nombre')}: {p.get('descripcion_general', '')}")
        for r in (p.get("renglones") or [])[:4]:
            partes.append(f"  renglon {r.get('numero')}: {str(r.get('descripcion', ''))[:220]}")
    return "\n".join(partes), [p.get("nombre") for p in elegidos]


# ---------------------------------------------------------------------------
# NIVEL 1: memorias en el prompt
# ---------------------------------------------------------------------------
def bloque_memorias(dispositivo: str, memorias: list) -> str:
    """memorias: [(puntaje, registro completo)]."""
    if not memorias:
        return ""
    if dispositivo == "banda":
        cab = ["MEMORIAS VALIDADAS POR EL USUARIO (nivel 1: configuraciones que ya aprobo en ESTA banda).",
               "Si la instruccion es igual o parecida, reutiliza la MISMA estructura de bloques y cambia SOLO lo",
               "que la instruccion pida distinto (tiempos, conteos, Hz, entradas, salidas). No agregues lo que no se",
               "pidio. Las reglas de hardware y seguridad de arriba siguen mandando."]
    else:
        cab = ["MEMORIAS VALIDADAS POR EL USUARIO (nivel 1: configuraciones del maletin que ya aprobo).",
               "Si la peticion es igual o parecida, imita la solucion y ajusta solo los parametros distintos;",
               "respeta el mismo esquema."]
    partes = list(cab)
    for i, (p, r) in enumerate(memorias, 1):
        partes.append(f"\n--- Memoria {i} (similitud {p:.2f}; prueba fisica: "
                      f"{r['validacion']['fisica']['estado']}) ---")
        partes.append(f"Peticion: {r['instruccion']}")
        partes.append("JSON: " + json.dumps(r["json_modelo"], ensure_ascii=False,
                                            separators=(",", ":"))[:MAX_JSON_MEMORIA])
    return "\n".join(partes)


# ---------------------------------------------------------------------------
# CONSTRUCCION DEL PROMPT DE LA BANDA (v7)
# ---------------------------------------------------------------------------
def mensajes_sistema_banda_v7(prompt_v7: str, texto: str, memorias: list) -> tuple:
    """(mensajes de sistema, plan) segun la ramificacion de la banda.

    plan = {"contexto": [...partes usadas...], "procesos": [ids], "proceso_desconocido": str|None}"""
    partes = partir_prompt_v7(prompt_v7)
    msgs = [{"role": "system", "content": partes["nucleo"]}]
    plan = {"contexto": ["nucleo_v7"], "procesos": [], "proceso_desconocido": None}

    if memorias:
        msgs.append({"role": "system", "content": bloque_memorias("banda", memorias)})
        plan["contexto"].append(f"memorias_validadas({len(memorias)})")
    else:
        extra = [x for x in (partes["guia"], partes["ejemplos"], limites_banda_v7()) if x]
        if extra:
            msgs.append({"role": "system", "content": "\n\n".join(extra)})
            plan["contexto"].append("contexto_tecnico_local(guia+ejemplos+limites)")

    if es_peticion_general(texto):
        procesos = procesos_mencionados(texto)
        if procesos:
            msgs.append({"role": "system", "content": bloque_procesos_banda(procesos)})
            plan["procesos"] = [p["id"] for p in procesos]
            plan["contexto"].append("catalogo_procesos(" + ",".join(plan["procesos"]) + ")")
        else:
            plan["proceso_desconocido"] = proceso_desconocido(texto)
    return msgs, plan


def pregunta_proceso_desconocido(nombre: str) -> dict:
    return {"id": "proceso_no_catalogado", "campo": "proceso",
            "pregunta": (f"El catalogo de procesos de LAVO no tiene \"{nombre}\", asi que no voy a inventar "
                         "como funciona. ¿Como seguimos?"),
            "opciones": ["Describir la secuencia paso a paso", "Usar transporte basico con I1 y I2",
                         "Autorizar ampliar el catalogo con ese proceso"]}
