"""
forma_v7.py — revisa la FORMA del JSON del motor v7 antes de que lo toque nada.

El modelo a veces devuelve un bloque con otro tipo ("motor": "I1", pasos de
secuencia como texto, "analisis": "ok"...). Todo el codigo que sigue (reparaciones,
validacion, simulacion, huella, cobertura) supone la forma del prompt y con otra
forma lanzaba excepciones que llegaban a los lentes como "HTTP 500".

  normalizar(resp) -> (resp_normalizada, problemas)

- Lo que no admite otra lectura se corrige solo: [] / "" / null en lugar de {}
  (o al reves), un texto suelto donde va una lista de textos, "analisis" o
  "preguntas" con otra forma (son informativos).
- Lo demas vuelve en `problemas` (frases para el modelo): con problemas el
  candidato no se usa y el modelo lo corrige en el siguiente intento.
Nunca lanza excepciones.
"""

# Bloques del motor y su forma.
LISTAS_DE_OBJETOS = ("condiciones", "memorias", "timers", "contadores", "eventos", "secuencias")
OBJETOS = ("banda", "motor", "salidas", "opciones")

# Campos escalares conocidos dentro de los objetos de cada bloque.
NUMEROS = {"segundos", "segundos_off", "preset", "repeticiones", "veces", "espera_s", "duracion_s",
           "frecuencia_hz", "hz", "n", "siguiente_si", "pulso_s"}
TEXTOS = {"id", "op", "flanco", "entrada", "reset", "tipo", "modo", "inicio", "paro", "cuando",
          "avanza", "senal", "set", "arranque", "direccion", "nombre", "name"}
# Campos que son listas de textos (un texto suelto se envuelve en lista).
LISTAS_DE_TEXTOS = {"entradas", "salidas", "motor", "no_soportado", "conservado", "modificado", "simplificado"}


def _vacio(v) -> bool:
    return v is None or v == "" or v == [] or v == {}


def _num_ok(v) -> bool:
    if v is None or isinstance(v, bool):
        return v is None
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v.strip().replace(",", ".").split()[0])
            return True
        except (ValueError, IndexError):
            return False
    return False


def _escalares(obj: dict, donde: str, problemas: list):
    """Revisa los campos escalares conocidos de un objeto (in situ)."""
    for k in list(obj.keys()):
        v = obj[k]
        if k in NUMEROS and not _num_ok(v) and k != "siguiente":
            problemas.append(f"{donde}.{k} debe ser un numero (llego {type(v).__name__}).")
        elif k in TEXTOS and isinstance(v, (list, dict)):
            problemas.append(f"{donde}.{k} debe ser un texto (llego {type(v).__name__}).")
        elif k in LISTAS_DE_TEXTOS and k not in ("salidas", "motor"):
            if isinstance(v, str):
                obj[k] = [v]
            elif v is None:
                obj[k] = []
            elif not isinstance(v, list) or any(isinstance(x, (list, dict)) for x in v):
                problemas.append(f"{donde}.{k} debe ser una lista de textos.")
        elif k == "siguiente" and isinstance(v, (list, dict)):
            problemas.append(f"{donde}.siguiente debe ser null, un numero de paso o \"fin\".")


def _lista_de_objetos(resp: dict, clave: str, problemas: list):
    v = resp.get(clave)
    if _vacio(v):
        if clave in resp:
            resp[clave] = []
        return
    if isinstance(v, dict):
        # Un solo objeto en lugar de la lista.
        v = resp[clave] = [v]
    if not isinstance(v, list):
        problemas.append(f"\"{clave}\" debe ser una lista de objetos (llego {type(v).__name__}).")
        return
    for i, x in enumerate(v):
        if not isinstance(x, dict):
            problemas.append(f"{clave}[{i}] debe ser un objeto {{...}} (llego {type(x).__name__}: {str(x)[:40]}).")
            continue
        _escalares(x, f"{clave}[{i}]", problemas)
        if clave == "condiciones" and "entradas" in x:
            e = x["entradas"]
            if isinstance(e, str):
                x["entradas"] = [e]
            elif not isinstance(e, list) or any(isinstance(y, (list, dict)) for y in e):
                problemas.append(f"condiciones[{i}].entradas debe ser una lista de senales.")
        if clave == "eventos" and "acciones" in x and not isinstance(x["acciones"], dict):
            if _vacio(x["acciones"]):
                x["acciones"] = {}
            else:
                problemas.append(f"eventos[{i}].acciones debe ser un objeto {{recurso: accion}}.")
        if clave == "secuencias":
            _pasos(x, i, problemas)


def _pasos(sq: dict, i: int, problemas: list):
    pasos = sq.get("pasos")
    if pasos is None:
        return
    if isinstance(pasos, dict):
        pasos = sq["pasos"] = [pasos]
    if not isinstance(pasos, list):
        problemas.append(f"secuencias[{i}].pasos debe ser una lista de objetos.")
        return
    for j, p in enumerate(pasos):
        donde = f"secuencias[{i}].pasos[{j}]"
        if not isinstance(p, dict):
            problemas.append(f"{donde} debe ser un objeto {{\"salidas\":[...],\"avanza\":...}} "
                             f"(llego {type(p).__name__}: {str(p)[:40]}).")
            continue
        for k in ("salidas", "motor"):
            if k in p:
                v = p[k]
                if v is None or v == "":
                    p[k] = []
                elif isinstance(v, str):
                    p[k] = [v]
                elif not isinstance(v, list) or any(isinstance(y, (list, dict)) for y in v):
                    problemas.append(f"{donde}.{k} debe ser una lista de textos.")
        _escalares(p, donde, problemas)


def _objeto(resp: dict, clave: str, problemas: list):
    v = resp.get(clave)
    if _vacio(v):
        if clave in resp:
            resp[clave] = {}
        return
    if not isinstance(v, dict):
        problemas.append(f"\"{clave}\" debe ser un objeto {{...}} (llego {type(v).__name__}: {str(v)[:40]}).")
        return
    if clave == "salidas":
        for q, x in list(v.items()):
            if isinstance(x, (list, dict)) and not _vacio(x):
                problemas.append(f"salidas.{q} debe ser el nombre de una senal o condicion (texto).")
    elif clave == "opciones":
        return
    else:
        _escalares(v, clave, problemas)


def normalizar(resp):
    """(resp normalizada, problemas). Nunca lanza."""
    try:
        return _normalizar(resp)
    except Exception as e:                         # red de seguridad: forma imposible de leer
        return resp if isinstance(resp, dict) else {}, [f"El JSON no se pudo leer ({type(e).__name__}: {e})."]


def _normalizar(resp):
    if not isinstance(resp, dict):
        return {}, [f"La respuesta debe ser UN objeto JSON {{...}} (llego {type(resp).__name__})."]
    problemas = []
    if "name" in resp and not isinstance(resp["name"], str):
        resp["name"] = "" if resp["name"] is None else str(resp["name"])
    # Informativos: con otra forma se descartan (no impiden cargar el programa).
    if "analisis" in resp and not isinstance(resp["analisis"], dict):
        resp["analisis"] = {}
    if isinstance(resp.get("analisis"), dict):
        for k in ("conservado", "modificado", "simplificado", "no_soportado"):
            v = resp["analisis"].get(k)
            if v is not None and not isinstance(v, list):
                resp["analisis"][k] = [str(v)] if v != "" else []
    if "preguntas" in resp:
        p = resp["preguntas"]
        resp["preguntas"] = [q for q in p if isinstance(q, dict)] if isinstance(p, list) else []
    if "no_soportado" in resp and not isinstance(resp["no_soportado"], list):
        v = resp["no_soportado"]
        resp["no_soportado"] = [str(v)] if not _vacio(v) else []
    for k in OBJETOS:
        _objeto(resp, k, problemas)
    for k in LISTAS_DE_OBJETOS:
        _lista_de_objetos(resp, k, problemas)
    if "proceso" in resp and not _vacio(resp["proceso"]) and not isinstance(resp["proceso"], dict):
        problemas.append("\"proceso\" debe ser un objeto o null.")
    return resp, problemas


def mensaje(problemas: list) -> str:
    """Texto para que el modelo corrija la forma."""
    return ("El JSON anterior no tiene la forma del motor v7:\n- " + "\n- ".join(problemas[:12])
            + "\nRespeta exactamente el FORMATO indicado (objetos y listas de objetos) sin perder nada de lo "
              "pedido. Devuelve el JSON COMPLETO, sin texto extra.")
