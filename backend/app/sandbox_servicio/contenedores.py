"""Ciclo de vida de los contenedores SQL Server del Sandbox (Docker SDK)."""

import contextlib
import logging
import re
import time
from dataclasses import dataclass

import docker
import pyodbc
from docker.errors import NotFound

from app.core.secretos import proveedor_secretos
from app.sandbox_servicio.config import configuracion

log = logging.getLogger(__name__)

PATRON_CLAVE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
PUERTO_SQL = "1433/tcp"


@dataclass(frozen=True)
class Destino:
    """Cómo conectarse al SQL Server de un Sandbox."""

    servidor: str
    usuario: str
    password: str


def validar_clave(clave: str) -> str:
    if not PATRON_CLAVE.match(clave):
        raise ValueError("Clave de Sandbox inválida.")
    return clave


def _cliente() -> docker.DockerClient:
    return docker.from_env()


def _nombre(clave: str) -> str:
    return configuracion().prefijo + validar_clave(clave)


def password_sa() -> str:
    return proveedor_secretos().obtener("sandbox_sa_password")


def cadena_conexion(destino: Destino, base: str = "master") -> str:
    return (
        f"DRIVER={{{configuracion().driver}}};SERVER={destino.servidor};DATABASE={base};"
        f"UID={destino.usuario};PWD={{{destino.password}}};Encrypt=yes;TrustServerCertificate=yes"
    )


def cadena_sqlclient(destino: Destino, base: str) -> str:
    """Cadena en el formato de .NET (SqlClient), el que entiende sqlpackage."""

    def valor(texto: str) -> str:
        return '"' + texto.replace('"', '""') + '"'

    return (
        f"Server={destino.servidor};Database={valor(base)};User Id={valor(destino.usuario)};"
        f"Password={valor(destino.password)};Encrypt=True;TrustServerCertificate=True"
    )


def _destino(contenedor) -> Destino:
    cfg = configuracion()
    if cfg.red:
        servidor = f"{contenedor.name},1433"
    else:
        contenedor.reload()
        puerto = contenedor.ports[PUERTO_SQL][0]["HostPort"]
        servidor = f"127.0.0.1,{puerto}"
    return Destino(servidor, "sa", password_sa())


def _esperar_motor(destino: Destino) -> None:
    """SQL Server tarda en aceptar conexiones después de arrancar el contenedor."""
    limite = time.monotonic() + configuracion().espera_inicio_segundos
    while True:
        try:
            with pyodbc.connect(cadena_conexion(destino), timeout=5) as conexion:
                conexion.cursor().execute("SELECT 1").fetchone()
            return
        except pyodbc.Error:
            if time.monotonic() > limite:
                raise
            time.sleep(3)


def asegurar(clave: str) -> Destino:
    """Devuelve el Sandbox listo para usar; lo crea o lo arranca si hace falta."""
    cfg = configuracion()
    cliente = _cliente()
    nombre = _nombre(clave)
    try:
        contenedor = cliente.containers.get(nombre)
        if contenedor.status != "running":
            contenedor.start()
    except NotFound:
        log.info("Creando el Sandbox %s", nombre)
        contenedor = cliente.containers.run(
            cfg.imagen,
            name=nombre,
            detach=True,
            environment={
                "ACCEPT_EULA": "Y",
                "MSSQL_PID": "Developer",
                "MSSQL_SA_PASSWORD": password_sa(),
            },
            mem_limit=cfg.memoria,
            volumes={f"{nombre}-datos": {"bind": "/var/opt/mssql", "mode": "rw"}},
            labels={"dba-assistant.sandbox": clave},
            restart_policy={"Name": "unless-stopped"},
            **({"network": cfg.red} if cfg.red else {"ports": {PUERTO_SQL: ("127.0.0.1", None)}}),
        )
    destino = _destino(contenedor)
    _esperar_motor(destino)
    return destino


def estado(clave: str) -> dict:
    try:
        contenedor = _cliente().containers.get(_nombre(clave))
    except NotFound:
        return {"existe": False}
    imagen = contenedor.image.tags[0] if contenedor.image.tags else contenedor.image.short_id
    return {
        "existe": True,
        "estado": contenedor.status,
        "imagen": imagen,
        "iniciado_en": contenedor.attrs.get("State", {}).get("StartedAt"),
        "memoria_limite": configuracion().memoria,
    }


def eliminar(clave: str) -> None:
    """Elimina el contenedor y su volumen (por ejemplo, al dar de baja la conexión)."""
    cliente = _cliente()
    nombre = _nombre(clave)
    with contextlib.suppress(NotFound):
        cliente.containers.get(nombre).remove(force=True)
    with contextlib.suppress(NotFound):
        cliente.volumes.get(f"{nombre}-datos").remove(force=True)
