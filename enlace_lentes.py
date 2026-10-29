"""
enlace_lentes.py — relevo entre los lentes (Unity) y la PC del PLC.

La PC conectada al maletin o a la banda pulsa "Simular en lentes", elige el
PLC destino y queda esperando. Desde los lentes se simula como siempre y, al
pulsar "Enviar al sistema fisico", la ultima configuracion viaja por aqui a la
PC, que la carga AUTOMATICAMENTE en el PLC que eligio (sin volver a preguntar).

    PC     POST /enlace/abrir   {device, ip, port}      -> canal
    PC     GET  /enlace/esperar ?canal_id               (long-poll, se repite)
    Lentes GET  /enlace/estado                          -> que PC espera
    Lentes POST /enlace/enviar  {device, logic, ...}    -> envio_id
    PC     POST /enlace/resultado {envio_id, ok, mensaje}
    Lentes GET  /enlace/envio/{envio_id}                -> cargado / error

Render no alcanza la red del PLC: el backend solo hace de relevo. Quien
escribe Modbus es la pagina local de la PC, por el puente localhost:8000.

Un canal por equipo ("maletin", "banda"): abrir otro canal del mismo equipo
reemplaza al anterior. Todo vive en memoria del proceso (una sesion de
laboratorio); si Render reinicia, la PC vuelve a abrir el canal sola.

La validacion de la configuracion la inyecta app.py (las mismas reglas que
/aplicar-plc), para que a la PC solo llegue algo que se puede cargar.
"""

import ipaddress
import json
import threading
import time
import uuid

DISPOSITIVOS = ("maletin", "banda")

# La PC hace long-poll de ~25 s; si deja de preguntar mas de esto, se da por
# desconectada y los lentes ya no le pueden mandar nada.
LATIDO_MAX_S = 75
# Canales cerrados o reemplazados se olvidan pasado este tiempo.
OLVIDAR_CERRADO_S = 600
MAX_ENVIOS_GUARDADOS = 30
MAX_BYTES_LOGIC = 200_000
MAX_FRASES = 30


class ErrorEnlace(Exception):
    """Error con codigo HTTP sugerido (app.py lo convierte en HTTPException)."""

    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


def normalizar_device(device) -> str:
    d = str(device or "").strip().lower()
    d = {"maletín": "maletin", "banda transportadora": "banda"}.get(d, d)
    return d if d in DISPOSITIVOS else ""


def _id() -> str:
    return uuid.uuid4().hex[:12]


def _plc(ip, port, nombre) -> dict:
    """PLC elegido en la PC. La IP es opcional (el puente usa la configurada)."""
    ip = str(ip or "").strip()
    if ip:
        try:
            ipaddress.IPv4Address(ip)
        except ValueError:
            raise ErrorEnlace(400, f"IP del PLC no valida: '{ip}'.")
    try:
        port = int(port) if port not in (None, "") else 502
    except (TypeError, ValueError):
        raise ErrorEnlace(400, f"Puerto del PLC no valido: '{port}'.")
    if not 1 <= port <= 65535:
        raise ErrorEnlace(400, f"Puerto del PLC fuera de rango: {port}.")
    nombre = str(nombre or "").strip()[:80]
    return {"ip": ip, "port": port, "nombre": nombre}


def _etiqueta_plc(device: str, plc: dict) -> str:
    base = plc.get("nombre") or ("PLC de la banda" if device == "banda" else "PLC del maletin")
    return f"{base} ({plc['ip']}:{plc['port']})" if plc.get("ip") else base


class Enlace:
    """Estado del relevo. Seguro entre hilos: los endpoints sync de FastAPI
    corren en un pool y el long-poll en el event loop."""

    def __init__(self, reloj=time.time):
        self._reloj = reloj
        self._lock = threading.Lock()
        self._canales = {}      # canal_id -> canal
        self._activo = {}       # device -> canal_id abierto
        self._envios = {}       # envio_id -> envio (los ultimos MAX_ENVIOS_GUARDADOS)

    # ── PC ────────────────────────────────────────────────────────────

    def abrir(self, device, ip=None, port=None, nombre_plc=None) -> dict:
        dev = normalizar_device(device)
        if not dev:
            raise ErrorEnlace(400, "Elige el equipo: 'maletin' o 'banda'.")
        plc = _plc(ip, port, nombre_plc)
        ahora = self._reloj()
        with self._lock:
            self._limpiar(ahora)
            anterior = self._activo.get(dev)
            if anterior in self._canales:
                self._cerrar(self._canales[anterior], "reemplazado", ahora)
            canal = {"canal_id": _id(), "device": dev, "plc": plc,
                     "abierto_en": ahora, "latido": ahora,
                     "estado": "esperando", "pendiente": None, "cerrado_en": None}
            self._canales[canal["canal_id"]] = canal
            self._activo[dev] = canal["canal_id"]
            return self._publico(canal, ahora)

    def cerrar(self, canal_id) -> dict:
        ahora = self._reloj()
        with self._lock:
            canal = self._canales.get(str(canal_id or ""))
            if canal and canal["estado"] != "cerrado":
                self._cerrar(canal, "cerrado", ahora)
            return {"status": "cerrado"}

    def recoger(self, canal_id) -> dict:
        """Una vuelta del long-poll de la PC: marca el latido y entrega el
        envio pendiente, si lo hay. app.py lo llama en bucle hasta el timeout."""
        ahora = self._reloj()
        with self._lock:
            canal = self._canales.get(str(canal_id or ""))
            if not canal or canal["estado"] == "cerrado":
                motivo = (canal or {}).get("motivo") or "no_existe"
                return {"status": "cerrado", "motivo": motivo}
            canal["latido"] = ahora
            envio_id = canal["pendiente"]
            if not envio_id:
                return {"status": "sin_envio"}
            canal["pendiente"] = None
            envio = self._envios.get(envio_id)
            if not envio:
                return {"status": "sin_envio"}
            envio["estado"] = "entregado"
            envio["entregado_en"] = ahora
            return {"status": "envio", "envio": self._envio_para_pc(envio)}

    def resultado(self, canal_id, envio_id, ok, mensaje="") -> dict:
        """La PC informa como fue la carga en el PLC (los lentes lo muestran)."""
        ahora = self._reloj()
        with self._lock:
            envio = self._envios.get(str(envio_id or ""))
            if not envio or envio["canal_id"] != str(canal_id or ""):
                raise ErrorEnlace(404, "No existe ese envio en este canal.")
            envio["estado"] = "cargado" if ok else "error"
            envio["mensaje"] = str(mensaje or "")[:2000]
            envio["resuelto_en"] = ahora
            canal = self._canales.get(envio["canal_id"])
            if canal:
                canal["latido"] = ahora
            return {"status": "ok", "envio_id": envio["envio_id"], "estado": envio["estado"]}

    # ── Lentes ────────────────────────────────────────────────────────

    def estado(self, device=None) -> dict:
        """Que PC esta esperando (para mostrar o no el boton en los lentes)."""
        dev = normalizar_device(device) if device else ""
        ahora = self._reloj()
        with self._lock:
            self._limpiar(ahora)
            canales = []
            for d in DISPOSITIVOS:
                if dev and d != dev:
                    continue
                canal = self._canales.get(self._activo.get(d))
                if canal and canal["estado"] != "cerrado":
                    canales.append(self._publico(canal, ahora))
            return {"canales": canales}

    def enviar(self, device, logic, validar, nombre="", frases=None, origen="lentes") -> dict:
        """Deja la configuracion para la PC que espera ese equipo.

        validar(cfg, device) -> lista de errores (vacia = se puede cargar).
        Si ya habia un envio sin recoger, el nuevo lo sustituye: siempre
        viaja la ULTIMA configuracion."""
        dev = normalizar_device(device)
        if not dev:
            raise ErrorEnlace(400, "Falta el equipo: 'maletin' o 'banda'.")
        if not isinstance(logic, dict) or not logic:
            raise ErrorEnlace(400, "Falta la configuracion ('logic') que se simulo en los lentes.")
        try:
            tam = len(json.dumps(logic, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError):
            raise ErrorEnlace(400, "La configuracion no es JSON valido.")
        if tam > MAX_BYTES_LOGIC:
            raise ErrorEnlace(413, f"La configuracion es demasiado grande ({tam} bytes).")

        ahora = self._reloj()
        with self._lock:
            canal = self._canal_vivo(dev, ahora)
        if canal is None:
            otro = "banda" if dev == "maletin" else "maletin"
            with self._lock:
                espera_otro = self._canal_vivo(otro, ahora) is not None
            pista = (" La PC esta esperando un programa para "
                     + ("el maletin." if otro == "maletin" else "la banda.")
                     if espera_otro else "")
            raise ErrorEnlace(409, "Ninguna PC esta esperando un programa para "
                              + ("el maletin" if dev == "maletin" else "la banda")
                              + ". En la PC pulsa 'Simular en lentes' y elige el PLC." + pista)

        # La validacion puede tardar (importa modulos): fuera del lock.
        errores = list(validar(logic, dev) or [])
        if errores:
            raise ErrorEnlace(422, "La configuracion no se puede cargar en el PLC:\n- "
                              + "\n- ".join(str(e) for e in errores))

        if isinstance(frases, str):
            frases = [frases]
        frases = [str(f).strip()[:1000] for f in (frases or []) if str(f or "").strip()][:MAX_FRASES]
        nombre = str(nombre or logic.get("name") or "").strip()[:120]

        with self._lock:
            # El canal pudo cerrarse o reemplazarse mientras se validaba.
            vivo = self._canal_vivo(dev, ahora)
            if vivo is None or vivo["canal_id"] != canal["canal_id"]:
                raise ErrorEnlace(409, "La PC dejo de esperar mientras se validaba. Vuelve a enviar.")
            previo = self._envios.get(vivo["pendiente"] or "")
            if previo and previo["estado"] == "pendiente":
                previo["estado"] = "sustituido"
            envio = {"envio_id": _id(), "canal_id": vivo["canal_id"], "device": dev,
                     "plc": dict(vivo["plc"]), "logic": logic, "nombre": nombre,
                     "frases": frases, "origen": str(origen or "lentes")[:40],
                     "estado": "pendiente", "mensaje": "", "creado_en": ahora,
                     "entregado_en": None, "resuelto_en": None}
            self._envios[envio["envio_id"]] = envio
            vivo["pendiente"] = envio["envio_id"]
            self._recortar_envios()
            return {"status": "ok", "envio_id": envio["envio_id"], "device": dev,
                    "plc": _etiqueta_plc(dev, vivo["plc"]),
                    "mensaje": "Enviado. La PC lo cargara en " + _etiqueta_plc(dev, vivo["plc"]) + "."}

    def consultar_envio(self, envio_id) -> dict:
        with self._lock:
            envio = self._envios.get(str(envio_id or ""))
            if not envio:
                raise ErrorEnlace(404, "No existe ese envio (o ya se olvido).")
            return {"envio_id": envio["envio_id"], "device": envio["device"],
                    "nombre": envio["nombre"], "estado": envio["estado"],
                    "mensaje": envio["mensaje"],
                    "plc": _etiqueta_plc(envio["device"], envio["plc"])}

    # ── internos (con el lock tomado) ─────────────────────────────────

    def _canal_vivo(self, dev, ahora):
        canal = self._canales.get(self._activo.get(dev))
        if not canal or canal["estado"] == "cerrado":
            return None
        if ahora - canal["latido"] > LATIDO_MAX_S:
            return None
        return canal

    def _cerrar(self, canal, motivo, ahora):
        canal["estado"] = "cerrado"
        canal["motivo"] = motivo
        canal["cerrado_en"] = ahora
        if self._activo.get(canal["device"]) == canal["canal_id"]:
            del self._activo[canal["device"]]
        envio = self._envios.get(canal["pendiente"] or "")
        if envio and envio["estado"] == "pendiente":
            envio["estado"] = "error"
            envio["mensaje"] = "La PC dejo de esperar antes de recibirlo."
        canal["pendiente"] = None

    def _limpiar(self, ahora):
        for cid in [c for c, v in self._canales.items()
                    if v["estado"] == "cerrado" and ahora - v["cerrado_en"] > OLVIDAR_CERRADO_S]:
            del self._canales[cid]

    def _recortar_envios(self):
        sobran = len(self._envios) - MAX_ENVIOS_GUARDADOS
        if sobran > 0:
            for eid in sorted(self._envios, key=lambda e: self._envios[e]["creado_en"])[:sobran]:
                del self._envios[eid]

    def _publico(self, canal, ahora) -> dict:
        return {"canal_id": canal["canal_id"], "device": canal["device"],
                "plc": dict(canal["plc"]), "plc_texto": _etiqueta_plc(canal["device"], canal["plc"]),
                "pc_conectada": ahora - canal["latido"] <= LATIDO_MAX_S,
                "envio_pendiente": bool(canal["pendiente"])}

    @staticmethod
    def _envio_para_pc(envio) -> dict:
        return {"envio_id": envio["envio_id"], "device": envio["device"],
                "plc": dict(envio["plc"]), "logic": envio["logic"],
                "nombre": envio["nombre"], "frases": list(envio["frases"]),
                "origen": envio["origen"]}
