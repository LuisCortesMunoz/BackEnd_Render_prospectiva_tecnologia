"""
============================================================================
ALMACEN DE LA MEMORIA VALIDADA (nivel 1) — donde viven los archivos
============================================================================
El disco de Render (plan gratuito) se BORRA en cada despliegue o reinicio,
asi que la memoria validada no puede vivir ahi. Hay dos almacenes con la
misma interfaz (leer / escribir archivos JSON por ruta relativa):

  AlmacenGitHub  un repositorio PRIVADO aparte (p. ej. LAVO-memoria) via la
                 API REST de GitHub. Persistente, versionado (cada guardado
                 es un commit) y NO redespliega Render porque no es el repo
                 del backend. Se activa con:
                   LAVO_MEMORIA_REPO   = "usuario/LAVO-memoria"
                   LAVO_MEMORIA_TOKEN  = token con permiso Contents: read/write
                   LAVO_MEMORIA_RAMA   = "main" (opcional)
  AlmacenLocal   una carpeta en disco (LAVO_MEMORIA_DIR, por defecto
                 "memoria_validada/"). Para desarrollo y pruebas, o para el
                 puente local. En Render NO es persistente: /health lo avisa.

Estructura (igual en los dos):
  banda/indice.json            banda/validadas/<id>.json
  maletin/indice.json          maletin/validadas/<id>.json
============================================================================
"""

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request

log = logging.getLogger(__name__)


class ErrorAlmacen(Exception):
    """El almacen no pudo leer o escribir (red, permisos, conflicto)."""


class AlmacenLocal:
    persistente_en_render = False

    def __init__(self, carpeta: str):
        self.carpeta = carpeta

    def descripcion(self) -> str:
        return f"carpeta local '{self.carpeta}'"

    def _ruta(self, rel: str) -> str:
        return os.path.join(self.carpeta, *rel.split("/"))

    def leer(self, rel: str):
        """JSON del archivo o None si no existe."""
        ruta = self._ruta(rel)
        if not os.path.exists(ruta):
            return None
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)

    def escribir(self, rel: str, datos, mensaje: str = "") -> None:
        ruta = self._ruta(rel)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ruta)            # escritura atomica


class AlmacenGitHub:
    """Archivos JSON en un repo de GitHub (API de contenidos).

    'transporte' se puede sustituir en pruebas: recibe (metodo, url, cuerpo,
    cabeceras) y devuelve (status, dict)."""
    persistente_en_render = True
    API = "https://api.github.com"

    def __init__(self, repo: str, token: str, rama: str = "main", transporte=None, timeout: float = 10.0):
        self.repo, self.token, self.rama = repo, token, rama
        self.timeout = timeout
        self._transporte = transporte or self._http
        self._sha = {}                    # rel -> sha del ultimo contenido conocido

    def descripcion(self) -> str:
        return f"repositorio GitHub '{self.repo}' (rama {self.rama})"

    def _http(self, metodo, url, cuerpo=None, cabeceras=None):
        datos = json.dumps(cuerpo).encode("utf-8") if cuerpo is not None else None
        req = urllib.request.Request(url, data=datos, method=metodo, headers=cabeceras or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                cuerpo_err = json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                cuerpo_err = {}
            return e.code, cuerpo_err
        except Exception as e:            # red caida, DNS, timeout
            raise ErrorAlmacen(f"GitHub no respondio: {e}")

    def _cabeceras(self):
        return {"Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "LAVO-backend"}

    def _url(self, rel):
        return f"{self.API}/repos/{self.repo}/contents/{rel}"

    def leer(self, rel: str):
        status, d = self._transporte("GET", self._url(rel) + f"?ref={self.rama}", None, self._cabeceras())
        if status == 404:
            self._sha.pop(rel, None)
            return None
        if status != 200:
            raise ErrorAlmacen(f"GitHub {status} al leer {rel}: {d.get('message', '')}")
        self._sha[rel] = d.get("sha")
        contenido = base64.b64decode(d.get("content") or b"").decode("utf-8")
        return json.loads(contenido) if contenido.strip() else None

    def escribir(self, rel: str, datos, mensaje: str = "") -> None:
        texto = json.dumps(datos, ensure_ascii=False, indent=2)
        for intento in range(3):
            cuerpo = {"message": mensaje or f"LAVO: actualiza {rel}",
                      "content": base64.b64encode(texto.encode("utf-8")).decode("ascii"),
                      "branch": self.rama}
            if self._sha.get(rel):
                cuerpo["sha"] = self._sha[rel]
            status, d = self._transporte("PUT", self._url(rel), cuerpo, self._cabeceras())
            if status in (200, 201):
                self._sha[rel] = (d.get("content") or {}).get("sha")
                return
            if status in (409, 422) and intento < 2:
                # sha desactualizado (otro guardado en medio): se relee y reintenta.
                self.leer(rel)
                time.sleep(0.2)
                continue
            raise ErrorAlmacen(f"GitHub {status} al escribir {rel}: {d.get('message', '')}")


def crear_almacen():
    """Almacen segun el entorno: GitHub si hay repo y token; si no, local."""
    repo = os.environ.get("LAVO_MEMORIA_REPO", "").strip()
    token = os.environ.get("LAVO_MEMORIA_TOKEN", "").strip()
    if repo and token:
        return AlmacenGitHub(repo, token, os.environ.get("LAVO_MEMORIA_RAMA", "main").strip() or "main")
    carpeta = os.environ.get("LAVO_MEMORIA_DIR", "memoria_validada")
    if repo or token:
        log.warning("LAVO_MEMORIA_REPO y LAVO_MEMORIA_TOKEN deben ir juntos: se usa el almacen local.")
    return AlmacenLocal(carpeta)
