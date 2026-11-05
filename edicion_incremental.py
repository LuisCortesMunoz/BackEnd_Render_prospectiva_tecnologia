"""
edicion_incremental.py — la siguiente instruccion EDITA la logica activa.

El front guarda una sola logica activa por sesion y, en cada instruccion de
modificacion, manda su JSON completo (programa_anterior.metadata.engine_config)
y la lista de instrucciones ya aplicadas (historial). Aqui se arma lo que
recibe el modelo y se comprueba, de forma determinista, que la version nueva
no perdio piezas que el usuario no pidio quitar.

No toca el PLC, el mapeo Modbus ni la memoria validada.
"""

import collections
import json
import re
import unicodedata

# Instrucciones previas que se le muestran al modelo (el JSON va completo).
MAX_CAMBIOS_PROMPT = 8


def _norm(texto: str) -> str:
    t = unicodedata.normalize("NFD", str(texto or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", t)).strip()


# "quita", "elimina", "ya no", "cambia S1 por S2", "en vez de"... Con estas
# palabras desaparecer una pieza puede ser justo lo pedido.
_RX_QUITAR = re.compile(
    r"\b(quita\w*|quitar|elimina\w*|borra\w*|remueve\w*|retira\w*|suprime\w*|ya no|"
    r"reemplaza\w*|sustituye\w*|en vez de|en lugar de|cambia\w* .+ por|solo deja|deja solo|"
    r"unicamente|nada mas|desde cero|de nuevo todo|nueva logica|logica nueva|otra logica)\b")


def pide_quitar(texto: str) -> bool:
    """True si la instruccion puede quitar o sustituir piezas de la logica."""
    return bool(_RX_QUITAR.search(_norm(texto)))


def _lista_cambios(historial) -> str:
    previas = [str(p).strip() for p in (historial or []) if str(p).strip()][-MAX_CAMBIOS_PROMPT:]
    return "\n".join(f"{i}. {p}" for i, p in enumerate(previas, 1))


REGLAS_EDICION = (
    "La nueva instruccion EDITA esta logica; no es una logica independiente:\n"
    "- Conserva TODO lo que la instruccion no pide cambiar: entradas, salidas, timers, contadores, "
    "condiciones, memorias, eventos, secuencias y sus pasos, con sus ids y valores.\n"
    "- Agrega solo lo nuevo, cambia solo lo pedido y quita solo lo que se pide quitar (y lo que "
    "dependa unicamente de eso).\n"
    "- Referencias como \"el ultimo tiempo\", \"ese timer\" o \"el segundo paso\": resuelvelas con las "
    "instrucciones anteriores (la mas reciente manda). Si hay varias piezas posibles y el contexto no "
    "decide, pregunta en \"preguntas\" en vez de adivinar.\n"
    "- Devuelve el JSON COMPLETO actualizado (lo anterior vigente + lo nuevo), nunca solo el cambio."
)


def mensajes_edicion(prev: dict, historial, titulo: str, respuesta: str,
                     correccion: bool = False, diagnostico=None) -> list:
    """Par user/assistant que antecede a la instruccion nueva cuando hay
    logica activa. `prev` es el JSON vigente en el formato que el modelo
    devuelve (para que lo edite en su propio esquema). Con `correccion` el
    usuario dice que esa logica NO funciona: las reglas cambian (se permite
    rehacer la parte que falla) y va el diagnostico de la simulacion."""
    partes = []
    cambios = _lista_cambios(historial)
    if cambios:
        partes.append("Instrucciones ya aplicadas en esta sesion (de la mas antigua a la mas reciente):\n"
                      + cambios)
    reglas = REGLAS_CORRECCION if correccion else REGLAS_EDICION
    partes.append(f"{titulo}\n{reglas}\nJSON vigente:\n" + json.dumps(prev, ensure_ascii=False))
    if correccion and diagnostico is not None:      # None = no hay simulador para este equipo
        partes.append(bloque_diagnostico(diagnostico))
    return [{"role": "user", "content": "\n\n".join(partes)},
            {"role": "assistant", "content": respuesta}]


# ---------------------------------------------------------------------------
# Correcciones: "revisa", "checa", "no esta funcionando", "corrige"
# ---------------------------------------------------------------------------
# El usuario dice que la logica activa NO hace lo que pidio. Antes la regla
# era "ajusta la parte indicada; no la rehagas" y, con la restauracion de
# piezas, el modelo devolvia casi la misma solucion. Ahora: diagnostico,
# rediseño de la parte que falla y prueba de que la version nueva cambio.
_RX_CORRECCION = re.compile(
    r"\b(revis\w*|chec\w*|cheque\w*|verifica\w*|corrig\w*|corrige\w*|arregl\w*|repar\w*|"
    r"no (?:esta |estan )?(?:funcionando|funciona|funcionan|sirve|sirven|jala|jalan|hace|hizo|cumple|quedo|"
    r"respeta|obedece|detiene|detuvo|para|paro|arranca|arranco|mueve|movio|avanza|enciende|prende|apaga|"
    r"sube|baja|cuenta|cambia|cambio|se (?:detiene|detuvo|para|paro|mueve|movio|enciende|prende|apaga|"
    r"apago|sube|baja|activa|repite|reinicia))|"
    r"esta mal|estan mal|salio mal|quedo mal|mal hecho|falla\w*|incorrect\w*|"
    r"sigue (?:igual|sin|mal|sin funcionar)|todavia no|aun no|tampoco|otra vez (?:no|mal)|"
    r"no es (?:lo que|asi)|eso no)\b")


def es_correccion(texto: str) -> bool:
    """True si el usuario reporta que la logica activa no funciona como pidio."""
    return bool(_RX_CORRECCION.search(_norm(texto)))


REGLAS_CORRECCION = (
    "El usuario dice que la logica activa NO funciona como pidio. Antes de responder:\n"
    "1. DIAGNOSTICO: compara el JSON vigente con TODAS las instrucciones de la sesion (y con el diagnostico "
    "de la simulacion, si lo hay) y encuentra que parte no cumple y por que.\n"
    "2. REDISEÑA esa parte desde cero si hace falta: puedes cambiar el tipo de bloque, usar condiciones, "
    "memorias, timers, contadores, eventos o secuencias distintos, cambiar campos del motor e ids. No "
    "repitas la misma estructura con otro valor si la estructura es la que falla.\n"
    "3. Conserva TAL CUAL lo que si funciona y no tiene que ver con la falla.\n"
    "4. En \"analisis\" agrega \"diagnostico\": que estaba mal y que cambiaste para corregirlo (una o dos "
    "frases). \"modificado\" sigue siendo SOLO para lo que no se pudo hacer como el usuario pidio.\n"
    "5. Devolver el mismo JSON NO es una respuesta valida. Si de verdad no encuentras ninguna falla, pregunta "
    "en \"preguntas\" que es lo que no funciona, con opciones concretas.\n"
    "Devuelve el JSON COMPLETO corregido, nunca solo el cambio."
)


def bloque_diagnostico(diagnostico) -> str:
    if diagnostico:
        return ("DIAGNOSTICO AUTOMATICO (se simulo el programa del PLC con la logica vigente):\n- "
                + "\n- ".join(diagnostico))
    return ("DIAGNOSTICO AUTOMATICO: la simulacion no pudo probar la falla que reporta el usuario; "
            "revisa la logica contra sus instrucciones.")


def misma_logica(dispositivo: str, prev: dict, nuevo: dict) -> bool:
    """La version nueva hace EXACTAMENTE lo mismo que la vigente (mismos
    registros en el PLC para la banda; mismo JSON sin textos en el maletin)."""
    if not isinstance(prev, dict) or not isinstance(nuevo, dict):
        return False
    if dispositivo == "banda":
        try:
            import lavo_motor as lm
            a = lm.compilar_motor(lm.cfg_desde_respuesta(prev)["band"])
            b = lm.compilar_motor(lm.cfg_desde_respuesta(nuevo)["band"])
            return list(a) == list(b)
        except Exception:
            pass
    quitar = ("analisis", "preguntas", "no_soportado", "name", "comment", "compatibilidad", "analysis")

    def limpio(x):
        if isinstance(x, dict):
            return {k: limpio(v) for k, v in x.items() if k not in quitar}
        if isinstance(x, list):
            return [limpio(v) for v in x]
        return x
    return json.dumps(limpio(prev), sort_keys=True) == json.dumps(limpio(nuevo), sort_keys=True)


def mensaje_misma_logica(diagnostico) -> str:
    return ("Devolviste la MISMA logica que el usuario dice que no funciona (los registros del PLC son "
            "identicos). Vuelve al diagnostico, cambia la parte que falla (otra estructura, no solo otro "
            "valor) y conserva lo demas. " + (("Fallas encontradas al simular:\n- " + "\n- ".join(diagnostico))
                                             if diagnostico else "")
            + "\nDevuelve el JSON COMPLETO, sin texto extra.")


def pregunta_que_falla(dispositivo: str = "banda") -> dict:
    ops = (["No arranca", "No se detiene con el botón", "No se detiene sola", "Las luces no hacen lo pedido",
            "Las plumas no se mueven bien", "El orden de las acciones no es el correcto"]
           if dispositivo == "banda" else
           ["La lámpara no enciende", "La lámpara no se apaga", "El tiempo no es el correcto",
            "El contador no cuenta bien", "El orden de la secuencia no es el correcto"])
    return {"id": "falla", "campo": "falla",
            "pregunta": "No encontré qué cambiar en la lógica. ¿Qué es lo que no funciona?", "opciones": ops}


# ---------------------------------------------------------------------------
# Localizar QUE corregir (zona de cambio)
# ---------------------------------------------------------------------------
_MODULOS = {
    "conteo": r"\b(contador\w*|conteo\w*|cuent\w*|reinici\w*|detecciones|cajas|piezas)\b",
    "plumas": r"\bpluma\w*\b",
    "torreta": r"\b(luz|luces|lampara\w*|torreta|verde|amarill\w*|roj[ao]s?)\b",
    "motor": r"\b(banda|motor|deten\w*|detien\w*|(?:se |no )para|paro|arranc\w*|avanz\w*|frecuencia|hz|velocidad|direccion|"
             r"sentido|pausa\w*|continu\w*|reanud\w*)\b",
    "tiempos": r"\b(\d+(?:[.,]\d+)?\s*(?:s|seg|segundos?)|tiempo\w*|temporizador\w*)\b",
}


def _desc_etapa(e: dict) -> str:
    return f"etapa {e['n']} «{e['texto'][:70]}»"


def localizar(texto: str, det: dict = None, diagnostico=None, acl: dict = None) -> dict:
    """Que parte de la logica revisar, segun lo que dice el usuario (y lo que
    la simulacion de la version vigente encontro). {"modulos", "etapas", "vago"}"""
    falla = ((acl or {}).get("falla") or {}).get("respuesta") or ""
    t = _norm(f"{texto} {falla}")
    modulos = [m for m, rx in _MODULOS.items() if re.search(rx, t)]
    etapas = set(int(n) for n in re.findall(r"\b(?:etapa|paso)\s+(\d+)", t))
    for d in diagnostico or []:
        etapas |= {int(n) for n in re.findall(r"\b[Ee]tapa (\d+)", d)}
    det = det or {}
    for e in det.get("etapas") or []:
        reqs = e.get("requisitos") or []
        qs = set().union(*[r["q"] for r in reqs if r["tipo"] == "salida"]) if any(
            r["tipo"] == "salida" for r in reqs) else set()
        if ("plumas" in modulos and qs & {"Q6", "Q7", "Q8", "Q9"}) or \
           ("torreta" in modulos and qs & {"Q3", "Q4", "Q5"}) or \
           ("conteo" in modulos and (e.get("conteo") or e.get("reinicia_contador") or e.get("incrementa"))):
            etapas.add(e["n"])
        for s in re.findall(r"\b(?:s|sensor)\s?([12])\b", t):
            if e.get("entra_con") == f"S{s}" or e.get("hasta") == f"S{s}":
                etapas.add(e["n"])
        for n in re.findall(r"\b(\d+(?:[.,]\d+)?)\s*(?:s|seg|segundos?)\b", t):
            if any(abs(float(n.replace(",", ".")) - x) < 0.05 for x in e.get("tiempos") or []):
                etapas.add(e["n"])
    vago = not modulos and not etapas
    return {"modulos": modulos, "etapas": sorted(etapas), "vago": vago}


def bloque_zona(loc: dict, det: dict = None) -> str:
    """Para el modelo: que parte tocar y que se comprueba despues."""
    por_n = {e["n"]: e for e in (det or {}).get("etapas") or []}
    partes = []
    if loc.get("etapas"):
        partes.append("etapas: " + "; ".join(_desc_etapa(por_n[n]) if n in por_n else f"etapa {n}"
                                              for n in loc["etapas"]))
    if loc.get("modulos"):
        partes.append("bloques: " + ", ".join(loc["modulos"]))
    if not partes:
        return ""
    return ("ZONA A CORREGIR (lo que el usuario reporta): " + " | ".join(partes) + ".\n"
            "Cambia SOLO lo necesario en esa zona y deja identico lo demas (ids, pasos, tiempos y senales). "
            "Despues se simula el programa ANTERIOR y el NUEVO con todas las pruebas de la sesion: si algo que "
            "funcionaba deja de funcionar, la correccion se rechaza.")


MENSAJE_ESTRUCTURAL = (
    "Con cambios dentro de la zona no se logro. Ahora puedes REHACER la secuencia completa (otra estructura de "
    "pasos, ramas, contador o timers) mientras cumpla TODAS las instrucciones de la sesion; se vuelve a comprobar "
    "contra todas las pruebas.")


def pregunta_falla_etapas(det: dict, dispositivo: str = "banda") -> dict:
    """'Revisa' sin decir que: se pregunta con las etapas REALES de la sesion."""
    etapas = (det or {}).get("etapas") or []
    if not etapas:
        q = pregunta_que_falla(dispositivo)
        q["pregunta"] = "¿Qué es lo que no funciona?"
        return q
    ops = [f"Etapa {e['n']}: {e['texto'][:55]}" for e in etapas[:5]] + ["Otra cosa"]
    return {"id": "falla", "campo": "falla", "pregunta": "¿Qué parte no funciona como pediste?", "opciones": ops}


# ---------------------------------------------------------------------------
# Piezas de una logica (para detectar perdidas no pedidas)
# ---------------------------------------------------------------------------
def _piezas_v7(resp: dict) -> collections.Counter:
    """Piezas de un JSON del motor v7 en el formato del modelo (bloques)."""
    c = collections.Counter()
    if not isinstance(resp, dict):
        return c
    for clave, nombre in (("condiciones", "condicion"), ("memorias", "memoria"),
                          ("timers", "timer"), ("contadores", "contador")):
        for b in resp.get(clave) or []:
            if isinstance(b, dict) and b.get("id"):
                c[f"{nombre} {b['id']}"] += 1
    for q, senal in (resp.get("salidas") or {}).items():
        if senal:
            c[f"salida {q}"] += 1
    for ev in resp.get("eventos") or []:
        if isinstance(ev, dict) and ev.get("cuando"):
            c[f"evento cuando {ev['cuando']}"] += 1
    for sq in resp.get("secuencias") or []:
        if not isinstance(sq, dict):
            continue
        sid = sq.get("id") or "SQ"
        c[f"secuencia {sid}"] += 1
        for i, _ in enumerate(sq.get("pasos") or [], 1):
            c[f"paso {i} de {sid}"] += 1
    for k, v in (resp.get("motor") or {}).items():
        if v not in (None, "", [], False):
            c[f"motor.{k}"] += 1
    return c


def _piezas_maletin(cfg: dict) -> collections.Counter:
    c = collections.Counter()
    if not isinstance(cfg, dict):
        return c
    for o in cfg.get("outputs") or []:
        if not isinstance(o, dict):
            continue
        q = str(o.get("output") or "").upper()
        if (o.get("logic") or {}).get("mode", "off") != "off":
            c[f"salida {q}"] += 1
        if o.get("timer"):
            c[f"timer de {q}"] += 1
        if o.get("counter"):
            c[f"contador de {q}"] += 1
    for i, _ in enumerate((cfg.get("sequence") or {}).get("steps") or [], 1):
        c[f"paso {i} de la secuencia"] += 1
    return c


def piezas_perdidas(dispositivo: str, prev: dict, nuevo: dict) -> list:
    """Piezas que estaban en la logica activa y ya no estan en la nueva."""
    f = _piezas_v7 if dispositivo == "banda" else _piezas_maletin
    falta = f(prev) - f(nuevo)
    return sorted(falta.elements())


def mensaje_restaurar(perdidas: list) -> str:
    return ("Esa version PERDIO piezas de la logica activa que el usuario no pidio quitar:\n- "
            + "\n- ".join(perdidas)
            + "\nRestauralas tal como estaban en el JSON vigente y aplica SOLO el cambio pedido. "
              "Devuelve el JSON COMPLETO, sin texto extra.")


def aviso_perdidas(perdidas: list) -> str:
    # Sin mencionar la "logica activa": el sistema parte de ella por dentro.
    return ("Se quitaron piezas que la instruccion no pedia quitar: "
            + ", ".join(perdidas) + ". Si no es lo que pediste, pide corregirlo.")
