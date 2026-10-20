"""Bitácora de excepciones: los logs de la aplicación guardados en MongoDB (CU-000-009-C).

Un handler de logging toma los registros a partir del nivel configurado por cada tenant
(WARNING por defecto) y los encola; un hilo aparte los escribe en la colección
``bitacora_excepciones`` del tenant. Así el log nunca espera a la base y una caída de
MongoDB no frena la aplicación (los registros que no entran en la cola se descartan).
Los logs sin tenant (arranque, tareas globales) van a la base de la plataforma.
"""

import logging
import queue
import re
import threading
import time
import traceback
from datetime import UTC, datetime

from pymongo import MongoClient

from app.core import contexto
from app.core.config import obtener_configuracion
from app.core.secretos import proveedor_secretos

COLECCION = "bitacora_excepciones"
COLECCION_CONFIGURACION = "configuracion"
NIVEL_POR_DEFECTO = logging.WARNING
NIVELES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
CAPACIDAD_COLA = 10_000
REFRESCO_SEGUNDOS = 60
PATRON_CODIGO = re.compile(r"\b[A-Z]{2,4}_[A-Z]{2,4}_\d{3}\b|\b[A-Z]{3}_\d{3}\b")

# Nivel mínimo vigente por tenant (slug). Lo actualiza el hilo escritor y, al instante,
# el servicio que lo cambia.
_niveles: dict[str, int] = {}


def nivel_de(slug: str | None) -> int:
    return _niveles.get(slug, NIVEL_POR_DEFECTO) if slug else NIVEL_POR_DEFECTO


def actualizar_nivel(slug: str, nivel: str) -> None:
    _niveles[slug] = logging.getLevelName(nivel)


def documento(registro: logging.LogRecord, slug: str | None, usuario: str | None) -> dict:
    mensaje = registro.getMessage()
    traza = None
    if registro.exc_info:
        traza = "".join(traceback.format_exception(*registro.exc_info))
    codigo = PATRON_CODIGO.search(mensaje)
    return {
        "ts": datetime.fromtimestamp(registro.created, UTC),
        "nivel": registro.levelname,
        "modulo": registro.name,
        "mensaje": mensaje[:4000],
        "codigo": codigo.group(0) if codigo else None,
        "traza": traza[-8000:] if traza else None,
        "usuario": usuario,
        "ruta": contexto.ruta.get(),
        "tenant": slug,
    }


class ManejadorExcepciones(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.cola: queue.Queue = queue.Queue(CAPACIDAD_COLA)
        self.escritor: threading.Thread | None = None

    def emit(self, registro: logging.LogRecord) -> None:
        # Los logs del propio escritor (por ejemplo, MongoDB caído) no se vuelven a encolar.
        if threading.current_thread() is self.escritor:
            return
        slug = contexto.tenant_slug.get()
        if registro.levelno < nivel_de(slug):
            return
        try:
            self.cola.put_nowait(documento(registro, slug, contexto.usuario.get()))
        except queue.Full:
            pass
        except Exception:
            self.handleError(registro)


class Escritor:
    """Hilo que vacía la cola en MongoDB y mantiene al día los niveles por tenant."""

    def __init__(self, manejador: ManejadorExcepciones, tenants_activos, plataforma: str = ""):
        self.manejador = manejador
        # Base para los logs sin tenant; se resuelve al iniciar, fuera del hilo.
        self.plataforma = plataforma
        self.tenants_activos = tenants_activos  # () -> dict slug -> base MongoDB
        self.detener = threading.Event()
        self.bases: dict[str, str] = {}
        self.refrescado = 0.0

    def iniciar(self) -> None:
        hilo = threading.Thread(target=self._ejecutar, name="bitacora-excepciones", daemon=True)
        self.manejador.escritor = hilo
        hilo.start()

    def _refrescar(self, cliente: MongoClient) -> None:
        self.bases = self.tenants_activos()
        for slug, base in self.bases.items():
            config = cliente[base][COLECCION_CONFIGURACION].find_one({"_id": "excepciones"})
            _niveles[slug] = logging.getLevelName(config["nivel"]) if config else NIVEL_POR_DEFECTO
        self.refrescado = time.monotonic()

    def _conectar(self) -> MongoClient | None:
        try:
            return MongoClient(
                proveedor_secretos().obtener("mongodb_uri"), serverSelectionTimeoutMS=5000
            )
        except Exception:
            logging.getLogger(__name__).exception("Bitácora de excepciones sin MongoDB.")
            return None

    def _ejecutar(self) -> None:
        cliente = self._conectar()
        while not self.detener.is_set():
            if cliente is None:
                # Sin configuración de MongoDB se reintenta más tarde; la cola descarta.
                self.detener.wait(REFRESCO_SEGUNDOS)
                cliente = self._conectar()
                continue
            try:
                if time.monotonic() - self.refrescado > REFRESCO_SEGUNDOS:
                    self._refrescar(cliente)
                doc = self.manejador.cola.get(timeout=1)
            except queue.Empty:
                continue
            except Exception:
                logging.getLogger(__name__).exception("No se pudieron leer los tenants.")
                self.refrescado = time.monotonic()
                continue
            base = self.bases.get(doc["tenant"]) or self.plataforma
            try:
                cliente[base][COLECCION].insert_one(doc)
            except Exception:
                logging.getLogger(__name__).exception("No se pudo guardar una excepción.")
        if cliente is not None:
            cliente.close()


_manejador: ManejadorExcepciones | None = None
_escritor: Escritor | None = None


def iniciar(tenants_activos) -> None:
    """Instala el handler en el logger raíz y arranca el hilo escritor."""
    global _manejador, _escritor
    if _manejador is not None:
        return
    _manejador = ManejadorExcepciones()
    logging.getLogger().addHandler(_manejador)
    # Los logs de la aplicación se generan desde DEBUG para que un tenant pueda bajar el
    # nivel; la consola sigue mostrando solo desde LOG_NIVEL.
    logging.getLogger("app").setLevel(logging.DEBUG)
    _escritor = Escritor(
        _manejador, tenants_activos, obtener_configuracion().mongodb_base_plataforma
    )
    _escritor.iniciar()


def detener() -> None:
    global _manejador, _escritor
    if _manejador is not None:
        logging.getLogger().removeHandler(_manejador)
    if _escritor is not None:
        _escritor.detener.set()
    _manejador = _escritor = None
