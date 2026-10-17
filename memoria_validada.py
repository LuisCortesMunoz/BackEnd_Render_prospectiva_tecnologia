"""
============================================================================
MEMORIA VALIDADA DE LAVO (nivel 1 de conocimiento)
============================================================================
Configuraciones que el USUARIO aprobo con "Guardar logica validada". Es la
primera referencia para una peticion nueva:

    identica  -> se reutiliza el JSON SIN llamar al modelo (si vuelve a pasar
                 la validacion del equipo y la instruccion es autonoma).
    similar   -> se le muestra al modelo como referencia (solo las relevantes).
    nada      -> el modelo genera con el contexto tecnico local.

Reglas:
  - banda y maletin NUNCA se mezclan (indices y carpetas separados, y la
    busqueda filtra por dispositivo y por version de esquema compatible);
  - el elemento principal es el JSON estructurado ('json'), no el texto;
  - antes de guardar se valida contra las capacidades reales del equipo (la
    misma validacion que precede a /aplicar-plc): lo incorrecto no entra;
  - aprobacion del usuario, pruebas de software y prueba fisica se registran
    por separado; la fisica es "no_realizada" salvo que el usuario la declare;
  - duplicados (mismo contenido ejecutable) no se repiten; la misma
    instruccion con otro JSON crea una version nueva y la anterior queda
    "reemplazada" (no se borra).
Ver esquemas/memoria_schema.json y almacen_memoria.py.
============================================================================
"""

import copy
import datetime
import hashlib
import json
import logging
import re
import secrets
import time
import unicodedata

import almacen_memoria
import validador_esquema

log = logging.getLogger(__name__)

DISPOSITIVOS = ("banda", "maletin")
PROGRAMA_MAESTRO = {
    "banda-motor-v7": "LAVO_Main_ST v7 (cscape/ST_banda_v7_lavo.txt)",
    "banda-motor-v6": "ST banda v6, motor configurable (cscape/ST_banda_v6_motor_configurable.txt)",
    "banda-v5": "ST maestro de la banda v5 (cscape/ST_banda_v5.txt)",
    "maletin-engine-config-1": "Ladder maestro del maletin (plc_maestro.py)",
}
ESTADOS_FISICA = ("no_realizada", "exitosa", "fallida")

# Similitud minima para mostrarle una memoria al modelo como referencia.
UMBRAL_SIMILAR = 0.40
TTL_INDICE_S = 300


class ErrorMemoria(Exception):
    """La configuracion no se puede guardar (invalida, mezclada, incompatible)."""

    def __init__(self, errores):
        self.errores = errores if isinstance(errores, list) else [str(errores)]
        super().__init__("; ".join(self.errores))


# ---------------------------------------------------------------------------
# TEXTO: normalizacion, tokens, etiquetas, entradas/salidas
# ---------------------------------------------------------------------------
_NUMEROS = {"un": "1", "uno": "1", "una": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5",
            "seis": "6", "siete": "7", "ocho": "8", "nueve": "9", "diez": "10", "quince": "15",
            "veinte": "20", "treinta": "30"}
_STOP = set("""a al ante con de del desde el en entre hacia hasta la las lo los para por que se sin sobre
su sus un una unos unas y o e u mi me le les quiero quisiera favor porfavor haz has hace
programa programala configura configurala la banda maletin cuando luego""".split()) - {"banda", "cuando", "luego"}


def _sin_acentos(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")


def normalizar_texto(texto: str) -> str:
    """Forma canonica para comparar instrucciones (identica = misma forma).

    minusculas, sin acentos ni signos, numeros escritos -> digitos,
    "segundos" -> "s", voz "11/12/13" -> i1/i2/i3, espacios simples."""
    t = _sin_acentos(str(texto or "").lower())
    t = re.sub(r"(\d)[.,](\d)", r"\1_\2", t)          # conserva decimales
    t = re.sub(r"[^a-z0-9_% ]+", " ", t)
    t = t.replace("_", ".")
    t = re.sub(r"%\s*", "", t)
    palabras = []
    for p in t.split():
        p = _NUMEROS.get(p, p)
        if p in ("segundo", "segundos", "seg", "segs", "sg"):
            p = "s"
        if p in ("11", "12", "13"):                    # la voz dice "once" -> I1
            p = "i" + p[1]
        palabras.append(p)
    t = " ".join(palabras)
    t = re.sub(r"\b([iqs])\s+(\d+)\b", r"\1\2", t)     # "i 1" -> "i1", "s 2" -> "s2"
    t = re.sub(r"\b(\d+)\s*s\b", r"\1 s", t)
    return t.strip()


def tokens(texto: str) -> set:
    return {p for p in normalizar_texto(texto).split() if p not in _STOP and (len(p) > 1 or p.isdigit())}


_ETIQUETAS = {
    "temporizador": r"\b\d+(\.\d+)? s\b|durante|despues de|tras \d|retardo|temporiz|timer|esper",
    "intermitente": r"parpade|intermit|destell",
    "contador": r"cuent|contar|conteo|contador|\bcajas?\b|\bpiezas?\b|\bpulsos?\b",
    "secuencia": r"luego|despues|secuencia|tras otr|ciclo|repit|repet|en orden|paso",
    "enclavamiento": r"enclav|se queda|se mantiene|mantener|retenc|latch|hasta que",
    "motor": r"\bbanda\b|avanz|muev|mover|motor|\bhz\b|hertz|arranc",
    "direccion": r"derecha|izquierda|invier|revers|sentido|direccion",
    "plumas": r"pluma",
    "sensores": r"\bs[12]\b|sensor|detect",
    "luces": r"verde|amarill|\broj|lampara|\bluz\b|luces|torreta|\bq\d+",
    "condicion": r"al mismo tiempo|ambos|ambas|cualquiera|\bsi\b|mientras",
    "paro": r"\bparo\b|\bpare\b|\bparar\b|deten|stop|apag",
    "reset": r"reinici|reset",
}


def etiquetas_texto(texto: str) -> set:
    t = normalizar_texto(texto)
    return {k for k, rx in _ETIQUETAS.items() if re.search(rx, t)}


def io_texto(texto: str, dispositivo: str) -> set:
    """Entradas/salidas que la instruccion nombra (con nombre fisico o color)."""
    t = normalizar_texto(texto)
    io = {m.upper() for m in re.findall(r"\b[iq]\d{1,2}\b", t)}
    if dispositivo == "banda":
        io |= {"I4" for _ in re.findall(r"\bs1\b|sensor 1\b", t)}
        io |= {"I5" for _ in re.findall(r"\bs2\b|sensor 2\b", t)}
        alias = {"Q10": "Q3", "Q11": "Q4", "Q12": "Q5"}
        io = {alias.get(x, x) for x in io}
        colores = {"verde": "Q3", "amarill": "Q4", "roj": "Q5"}
        for c, q in colores.items():
            if re.search(rf"(?<!boton )\b{c}", t):      # "boton verde" es I1, no la luz
                io.add(q)
        if re.search(r"boton verde", t):
            io.add("I1")
        if re.search(r"boton roj", t):
            io.add("I2")
        if re.search(r"emergencia", t):
            io.add("I3")
        if re.search(r"pluma 1|pluma uno|primera pluma", t):
            io.update({"Q8", "Q9"})
        if re.search(r"pluma 2|pluma dos|segunda pluma", t):
            io.update({"Q6", "Q7"})
    else:
        colores = {"verde": "Q10", "amarill": "Q11", "roj": "Q12"}
        for c, q in colores.items():
            if re.search(rf"(?<!boton )\b{c}", t):
                io.add(q)
        if re.search(r"boton verde", t):
            io.add("I1")
        if re.search(r"boton roj", t):
            io.add("I2")
        if re.search(r"emergencia", t):
            io.add("I7")
    return io


_RX_MODIFICACION = re.compile(
    r"\b(agrega\w*|agregar|anade\w*|quita\w*|elimina\w*|cambia\w*|modifica\w*|tambien|ademas|"
    r"en vez de|en lugar de|ya no|lo mismo pero|igual pero|el mismo pero|ahora que)\b")


def es_modificacion(texto: str) -> bool:
    """True si la instruccion edita un programa anterior ("agrega...", "cambia...")."""
    return bool(_RX_MODIFICACION.search(normalizar_texto(texto)))


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 0.0


# ---------------------------------------------------------------------------
# JSON: version de esquema, huella y caracteristicas
# ---------------------------------------------------------------------------
def version_esquema(dispositivo: str, cfg: dict) -> str:
    if dispositivo == "maletin":
        return "maletin-engine-config-1"
    import lavo_motor
    if lavo_motor.es_motor_v7(cfg):
        return "banda-motor-v7"
    try:
        import banda_motor
        if banda_motor.es_motor(cfg):
            return "banda-motor-v6"
    except Exception:
        pass
    return "banda-v5"


def _parte_ejecutable(dispositivo: str, cfg: dict) -> dict:
    """Lo que de verdad llega al PLC (sin nombres, analisis ni marcas de UI)."""
    if dispositivo == "banda":
        band = copy.deepcopy(cfg.get("band") or {})
        eng = band.get("engine")
        if isinstance(eng, dict):
            for k in ("requiere_confirmacion", "confirmado"):
                eng.pop(k, None)
        return band
    return {"outputs": cfg.get("outputs") or [], "sequence": cfg.get("sequence") or None,
            "system": cfg.get("system") or {}}


def huella(dispositivo: str, cfg: dict) -> str:
    canon = json.dumps(_parte_ejecutable(dispositivo, cfg), sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _senales(valor) -> set:
    return {m.upper() for m in re.findall(r"\b[IQ]\d{1,2}\b", json.dumps(valor, ensure_ascii=False).upper())}


def caracteristicas(dispositivo: str, cfg: dict) -> dict:
    """Funciones, E/S, timers, contadores, condiciones y secuencias del JSON."""
    c = {"funciones": set(), "entradas": set(), "salidas": set(), "temporizadores": [],
         "contadores": [], "condiciones": [], "secuencias": [], "memorias": [], "motor": {}}
    if dispositivo == "banda":
        import lavo_motor
        band = cfg.get("band") or {}
        if lavo_motor.es_motor_v7(band):
            eng = lavo_motor.normalizar_motor(band.get("engine") or {})
            usados = lavo_motor.recursos_usados(eng)
            c["entradas"] |= set(usados["entradas"])
            c["salidas"] |= set(usados["salidas"])
            for t in eng.get("timers") or []:
                c["temporizadores"].append({"id": t.get("id"), "tipo": t.get("tipo"), "segundos": t.get("segundos")})
                c["funciones"].add("intermitente" if str(t.get("tipo")).upper() == "INTERMITENTE" else "temporizador")
            for k in eng.get("contadores") or []:
                c["contadores"].append({"id": k.get("id"), "preset": k.get("preset"), "modo": k.get("modo")})
                c["funciones"].add("contador")
            for k in eng.get("condiciones") or []:
                c["condiciones"].append({"id": k.get("id"), "op": k.get("op")})
                c["funciones"].add("condicion")
            for k in eng.get("memorias") or []:
                c["memorias"].append({"id": k.get("id")})
                c["funciones"].add("enclavamiento")
            for s in eng.get("secuencias") or []:
                pasos = s.get("pasos") or []
                c["secuencias"].append({"id": s.get("id"), "modo": s.get("modo"), "pasos": len(pasos)})
                c["funciones"].add("secuencia")
                if any(p.get("segundos") for p in pasos):
                    c["funciones"].add("temporizador")
            motor = eng.get("motor") or {}
            c["motor"] = {k: motor.get(k) for k in ("arranque", "paro", "modo") if motor.get(k)}
        else:
            c["entradas"] |= {x for x in _senales(band) if x.startswith("I")}
            c["salidas"] |= {x for x in _senales(band) if x.startswith("Q")}
        if band.get("enable") is not False:
            c["funciones"].add("motor")
            c["motor"].update({"mover": True, "direccion": band.get("direction"), "frecuencia_hz": band.get("freq_hz")})
        if c["salidas"] & {"Q6", "Q7", "Q8", "Q9"}:
            c["funciones"].add("plumas")
        if c["salidas"] & {"Q3", "Q4", "Q5"}:
            c["funciones"].add("luces")
        if c["entradas"] & {"I4", "I5"}:
            c["funciones"].add("sensores")
    else:
        for o in cfg.get("outputs") or []:
            q = str(o.get("output") or "").upper()
            if q:
                c["salidas"].add(q)
            lg = o.get("logic") or {}
            modo = str(lg.get("mode") or "").lower()
            if modo == "enclavado" or lg.get("latched"):
                c["funciones"].add("enclavamiento")
            if modo == "combinacional":
                c["funciones"].add("condicion")
                c["condiciones"].append({"salida": q, "op": lg.get("op")})
            for campo in ("source", "start", "stop", "a", "b", "enable"):
                if lg.get(campo):
                    c["entradas"].add(str(lg[campo]).upper())
            if o.get("timer"):
                c["funciones"].add("temporizador")
                c["temporizadores"].append({"salida": q, **{k: o["timer"].get(k) for k in ("type", "preset_s")}})
            if o.get("counter"):
                c["funciones"].add("contador")
                c["contadores"].append({"salida": q, **{k: o["counter"].get(k) for k in ("type", "preset")}})
                if o["counter"].get("reset_input"):
                    c["entradas"].add(str(o["counter"]["reset_input"]).upper())
        seq = cfg.get("sequence")
        if isinstance(seq, dict) and seq.get("steps"):
            c["funciones"].add("secuencia")
            c["secuencias"].append({"pasos": len(seq.get("steps") or []), "modo": seq.get("mode")})
            c["entradas"] |= {x for x in _senales(seq) if x.startswith("I")}
            c["salidas"] |= {x for x in _senales(seq) if x.startswith("Q")}
        gs = (cfg.get("system") or {}).get("global_stop")
        if gs:
            c["entradas"].add(str(gs).upper())
            c["funciones"].add("paro")
        if c["salidas"]:
            c["funciones"].add("luces")
    for k in ("funciones", "entradas", "salidas"):
        c[k] = sorted(c[k])
    return c


def json_modelo(dispositivo: str, cfg: dict) -> dict:
    """El programa en el esquema que PRODUCE el modelo (para usarlo de ejemplo)."""
    if dispositivo == "banda":
        if isinstance(cfg.get("respuesta_llm"), dict):
            return copy.deepcopy(cfg["respuesta_llm"])
        band = cfg.get("band") or {}
        eng = {k: v for k, v in (band.get("engine") or {}).items()
               if k not in ("version", "requiere_confirmacion", "confirmado")}
        return {"name": cfg.get("name"), "banda": {"mover": band.get("enable") is not False,
                                                   "direccion": band.get("direction"),
                                                   "frecuencia_hz": band.get("freq_hz")}, **eng}
    return {k: v for k, v in cfg.items() if k not in ("device",)}


def _ahora() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# MEMORIA
# ---------------------------------------------------------------------------
class MemoriaValidada:
    def __init__(self, almacen=None, ttl_s: float = TTL_INDICE_S):
        self.almacen = almacen or almacen_memoria.crear_almacen()
        self.ttl_s = ttl_s
        self._indices = {}        # dispositivo -> (t, indice)
        self._registros = {}      # (dispositivo, id) -> registro

    # ---- indice y registros -------------------------------------------------
    def _rel_indice(self, d):
        return f"{d}/indice.json"

    def _rel_registro(self, d, id_):
        return f"{d}/validadas/{id_}.json"

    def indice(self, dispositivo: str, refrescar: bool = False) -> dict:
        _validar_dispositivo(dispositivo)
        t, idx = self._indices.get(dispositivo, (0, None))
        if refrescar or idx is None or time.monotonic() - t > self.ttl_s:
            idx = self.almacen.leer(self._rel_indice(dispositivo)) or {"version": 1, "dispositivo": dispositivo,
                                                                       "memorias": []}
            self._indices[dispositivo] = (time.monotonic(), idx)
        return idx

    def _guardar_indice(self, dispositivo, idx, mensaje):
        self.almacen.escribir(self._rel_indice(dispositivo), idx, mensaje)
        self._indices[dispositivo] = (time.monotonic(), idx)

    def obtener(self, dispositivo: str, id_: str):
        _validar_dispositivo(dispositivo)
        clave = (dispositivo, id_)
        if clave not in self._registros:
            reg = self.almacen.leer(self._rel_registro(dispositivo, id_))
            if reg is None:
                return None
            self._registros[clave] = reg
        return copy.deepcopy(self._registros[clave])

    def _escribir_registro(self, reg, mensaje):
        self.almacen.escribir(self._rel_registro(reg["dispositivo"], reg["id"]), reg, mensaje)
        self._registros[(reg["dispositivo"], reg["id"])] = copy.deepcopy(reg)

    def listar(self, dispositivo: str) -> list:
        return copy.deepcopy(self.indice(dispositivo)["memorias"])

    # ---- guardar ------------------------------------------------------------
    def guardar(self, dispositivo: str, instruccion: str, cfg: dict, validar, esquema_activo: str,
                prueba_fisica: str = "no_realizada", nota: str = "", ejemplo_id: str = "") -> dict:
        """Valida y guarda. validar(cfg) -> (pruebas_ok: list, errores: list).

        Devuelve {"resultado": "guardada"|"nueva_version"|"duplicada", "memoria": entrada}."""
        _validar_dispositivo(dispositivo)
        instruccion = str(instruccion or "").strip()
        errores = []
        if not instruccion:
            errores.append("Falta la instruccion original del usuario.")
        if not isinstance(cfg, dict) or not cfg:
            raise ErrorMemoria(errores + ["Falta el JSON de la logica."])
        if prueba_fisica not in ESTADOS_FISICA:
            errores.append(f"prueba_fisica debe ser uno de {ESTADOS_FISICA}.")
        es_banda = isinstance(cfg.get("band"), dict)
        if dispositivo == "banda" and not es_banda:
            errores.append("El JSON no es de la banda (falta el bloque 'band'): no se mezclan dispositivos.")
        if dispositivo == "maletin" and (es_banda or cfg.get("device") == "banda"):
            errores.append("El JSON es de la banda y se pidio guardarlo como maletin: no se mezclan dispositivos.")
        if errores:
            raise ErrorMemoria(errores)

        esquema = version_esquema(dispositivo, cfg)
        if esquema != esquema_activo:
            raise ErrorMemoria([f"El JSON es del esquema '{esquema}', pero el programa maestro activo es "
                                f"'{esquema_activo}': no se guarda una configuracion incompatible."])
        if cfg.get("requiere_confirmacion") and not cfg.get("confirmado"):
            raise ErrorMemoria(["Es una alternativa sin confirmar: primero pulsa 'Usar la alternativa'."])

        pruebas, errores = validar(cfg)
        if errores:
            raise ErrorMemoria(["La configuracion no pasa la validacion del equipo:"] + list(errores))

        h = huella(dispositivo, cfg)
        norm = normalizar_texto(instruccion)
        idx = self.indice(dispositivo, refrescar=True)
        activas = [m for m in idx["memorias"] if m.get("estado") == "activa"]

        dup = next((m for m in activas if m["huella"] == h and m["version_esquema"] == esquema), None)
        if dup is not None:
            if prueba_fisica != "no_realizada":
                self.actualizar_prueba_fisica(dispositivo, dup["id"], prueba_fisica, nota)
                dup = next(m for m in self.indice(dispositivo)["memorias"] if m["id"] == dup["id"])
            return {"resultado": "duplicada", "memoria": copy.deepcopy(dup)}

        previa = next((m for m in activas if m["instruccion_normalizada"] == norm
                       and m["version_esquema"] == esquema), None)
        ahora = _ahora()
        reg = {
            "id": f"{dispositivo}-{datetime.datetime.now().strftime('%Y%m%d')}-{secrets.token_hex(4)}",
            "dispositivo": dispositivo,
            "instruccion": instruccion,
            "instruccion_normalizada": norm,
            "autonoma": not es_modificacion(instruccion),
            "json": copy.deepcopy(cfg),
            "json_modelo": json_modelo(dispositivo, cfg),
            "huella": h,
            "version_esquema": esquema,
            "programa_maestro": PROGRAMA_MAESTRO.get(esquema, esquema),
            "caracteristicas": caracteristicas(dispositivo, cfg),
            "etiquetas": [],
            "validacion": {
                "usuario": {"aprobada": True, "fecha": ahora},
                "software": {"estado": "aprobada", "pruebas": list(pruebas)},
                "fisica": {"estado": prueba_fisica,
                           "fecha": ahora if prueba_fisica != "no_realizada" else None,
                           "nota": str(nota or "")},
            },
            "version": (previa["version"] + 1) if previa else 1,
            "reemplaza": previa["id"] if previa else None,
            "estado": "activa",
            "fecha_validacion": ahora,
            "origen": {"ejemplo_id": ejemplo_id or ""},
        }
        reg["etiquetas"] = sorted(etiquetas_texto(instruccion) | set(reg["caracteristicas"]["funciones"]))
        fallas = validador_esquema.validar(reg, validador_esquema.cargar("memoria_schema.json"))
        if fallas:
            raise ErrorMemoria(["El registro no cumple esquemas/memoria_schema.json:"] + fallas)

        self._escribir_registro(reg, f"LAVO: memoria validada {reg['id']} ({dispositivo})")
        if previa:
            vieja = self.obtener(dispositivo, previa["id"])
            if vieja:
                vieja["estado"] = "reemplazada"
                self._escribir_registro(vieja, f"LAVO: {previa['id']} reemplazada por {reg['id']}")
            for m in idx["memorias"]:
                if m["id"] == previa["id"]:
                    m["estado"] = "reemplazada"
        entrada = _entrada_indice(reg)
        idx["memorias"].append(entrada)
        self._guardar_indice(dispositivo, idx, f"LAVO: indice {dispositivo} (+{reg['id']})")
        return {"resultado": "nueva_version" if previa else "guardada", "memoria": copy.deepcopy(entrada)}

    def actualizar_prueba_fisica(self, dispositivo: str, id_: str, estado: str, nota: str = "") -> dict:
        if estado not in ESTADOS_FISICA:
            raise ErrorMemoria([f"estado debe ser uno de {ESTADOS_FISICA}."])
        reg = self.obtener(dispositivo, id_)
        if reg is None:
            raise KeyError(id_)
        reg["validacion"]["fisica"] = {"estado": estado,
                                       "fecha": _ahora() if estado != "no_realizada" else None,
                                       "nota": str(nota or "")}
        self._escribir_registro(reg, f"LAVO: prueba fisica de {id_} = {estado}")
        idx = self.indice(dispositivo, refrescar=True)
        for m in idx["memorias"]:
            if m["id"] == id_:
                m["prueba_fisica"] = estado
        self._guardar_indice(dispositivo, idx, f"LAVO: indice {dispositivo} (prueba fisica {id_})")
        return _entrada_indice(reg)

    # ---- buscar -------------------------------------------------------------
    def _candidatas(self, dispositivo, esquema_activo):
        return [m for m in self.indice(dispositivo)["memorias"]
                if m.get("estado") == "activa" and m.get("version_esquema") == esquema_activo
                and m.get("dispositivo") == dispositivo]

    def identica(self, dispositivo: str, texto: str, esquema_activo: str):
        """Entrada con la MISMA instruccion normalizada, autonoma y compatible."""
        norm = normalizar_texto(texto)
        for m in reversed(self._candidatas(dispositivo, esquema_activo)):
            if m["instruccion_normalizada"] == norm and m.get("autonoma"):
                return copy.deepcopy(m)
        return None

    def buscar(self, dispositivo: str, texto: str, esquema_activo: str, k: int = 2,
               umbral: float = UMBRAL_SIMILAR) -> list:
        """[(puntaje, entrada)] de las memorias mas parecidas (solo del dispositivo)."""
        tq, gq, iq = tokens(texto), etiquetas_texto(texto), io_texto(texto, dispositivo)
        out = []
        for m in self._candidatas(dispositivo, esquema_activo):
            p = puntaje(tq, gq, iq, m)
            if p >= umbral:
                out.append((round(p, 3), copy.deepcopy(m)))
        out.sort(key=lambda x: (-x[0], x[1].get("fecha_validacion", "")))
        return out[:k]


def puntaje(tq: set, gq: set, iq: set, m: dict) -> float:
    """Similitud 0..1: palabras (45 %), funciones (35 %) y entradas/salidas (20 %)."""
    te = tokens(m.get("instruccion_normalizada", ""))
    ge = set(m.get("etiquetas") or [])
    ie = set(m.get("entradas") or []) | set(m.get("salidas") or [])
    p = 0.45 * _jaccard(tq, te) + 0.35 * _jaccard(gq, ge)
    p += 0.20 * (_jaccard(iq, ie) if iq else 0.0)
    return p


def _entrada_indice(reg: dict) -> dict:
    c = reg["caracteristicas"]
    return {"id": reg["id"], "dispositivo": reg["dispositivo"], "instruccion": reg["instruccion"],
            "instruccion_normalizada": reg["instruccion_normalizada"], "autonoma": reg["autonoma"],
            "huella": reg["huella"], "version_esquema": reg["version_esquema"],
            "etiquetas": reg["etiquetas"], "funciones": c["funciones"],
            "entradas": c["entradas"], "salidas": c["salidas"],
            "version": reg["version"], "reemplaza": reg["reemplaza"], "estado": reg["estado"],
            "prueba_fisica": reg["validacion"]["fisica"]["estado"],
            "fecha_validacion": reg["fecha_validacion"], "nombre": (reg["json"] or {}).get("name", "")}


def _validar_dispositivo(d):
    if d not in DISPOSITIVOS:
        raise ErrorMemoria([f"dispositivo debe ser 'banda' o 'maletin' (llego {d!r})."])
