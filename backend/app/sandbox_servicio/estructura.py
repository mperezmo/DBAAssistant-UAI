"""Copia de la estructura de las bases de origen al Sandbox con sqlpackage (DACPAC).

Solo se copia el esquema (tablas, índices, vistas, procedimientos, funciones), nunca
datos. Usuarios, logins y permisos se excluyen: dependen de la instancia de origen y no
hacen falta para validar.
"""

import logging
import subprocess
import tempfile
import time
from pathlib import Path

import pyodbc

from app.sandbox_servicio.config import configuracion
from app.sandbox_servicio.contenedores import Destino, cadena_sqlclient

log = logging.getLogger(__name__)

EXCLUIDOS = (
    "Users;Logins;RoleMembership;Permissions;Credentials;DatabaseScopedCredentials;"
    "ServerRoleMembership;ServerRoles;ApplicationRoles;DatabaseRoles"
)

# Huella de la estructura: cambia si se agrega, elimina o modifica una tabla, columna u
# objeto programable. No usa fechas: en el Sandbox todos los objetos se crean al publicar.
CONSULTA_HUELLA = """
SELECT
    (SELECT COUNT(*) FROM sys.objects WHERE is_ms_shipped = 0),
    (SELECT CHECKSUM_AGG(CHECKSUM(s.name, o.name, o.type))
     FROM sys.objects AS o JOIN sys.schemas AS s ON s.schema_id = o.schema_id
     WHERE o.is_ms_shipped = 0 AND o.parent_object_id = 0),
    (SELECT CHECKSUM_AGG(CHECKSUM(OBJECT_NAME(c.object_id), c.name, c.system_type_id,
                                  c.max_length, c.is_nullable))
     FROM sys.columns AS c JOIN sys.objects AS o ON o.object_id = c.object_id
     WHERE o.is_ms_shipped = 0 AND o.type = 'U'),
    (SELECT CHECKSUM_AGG(CHECKSUM(OBJECT_NAME(m.object_id), m.definition))
     FROM sys.sql_modules AS m JOIN sys.objects AS o ON o.object_id = m.object_id
     WHERE o.is_ms_shipped = 0)
"""


def huella(cadena: str) -> list:
    with pyodbc.connect(cadena, timeout=10) as conexion:
        return list(conexion.cursor().execute(CONSULTA_HUELLA).fetchone())


def _sqlpackage(argumentos: list[str]) -> None:
    resultado = subprocess.run(
        [*configuracion().comando_sqlpackage, *argumentos],
        capture_output=True,
        text=True,
        timeout=configuracion().timeout_sincronizacion_segundos,
        check=False,
    )
    if resultado.returncode != 0:
        # sqlpackage informa el motivo al final de la salida.
        salida = (resultado.stdout + resultado.stderr).strip().splitlines()
        raise RuntimeError(" ".join(salida[-5:])[:1000])


def sincronizar_base(origen: Destino, base: str, destino: Destino) -> dict:
    """Recrea la base en el Sandbox con la estructura actual del origen."""
    inicio = time.monotonic()
    with tempfile.TemporaryDirectory() as directorio:
        dacpac = str(Path(directorio) / "esquema.dacpac")
        try:
            _sqlpackage(
                [
                    "/Action:Extract",
                    f"/SourceConnectionString:{cadena_sqlclient(origen, base)}",
                    f"/TargetFile:{dacpac}",
                    "/p:ExtractAllTableData=false",
                    "/p:IgnoreUserLoginMappings=true",
                    "/p:VerifyExtraction=false",
                ]
            )
            _sqlpackage(
                [
                    "/Action:Publish",
                    f"/SourceFile:{dacpac}",
                    f"/TargetConnectionString:{cadena_sqlclient(destino, base)}",
                    "/p:CreateNewDatabase=true",
                    "/p:BlockOnPossibleDataLoss=false",
                    f"/p:ExcludeObjectTypes={EXCLUIDOS}",
                ]
            )
        except (RuntimeError, subprocess.TimeoutExpired) as error:
            log.warning("No se pudo sincronizar la base %s: %s", base, error)
            return {
                "base": base,
                "estado": "error",
                "error": str(error)[:1000],
                "duracion_s": round(time.monotonic() - inicio, 1),
            }
    return {
        "base": base,
        "estado": "ok",
        "error": None,
        "duracion_s": round(time.monotonic() - inicio, 1),
    }
