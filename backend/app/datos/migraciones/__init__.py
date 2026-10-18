"""Cambios de estructura versionados de las bases SQL Server de la aplicación.

Cada cambio es un archivo T-SQL numerado (``001_descripcion.sql``) dentro del
conjunto que corresponde: ``catalogo`` (base central) o ``tenant`` (base de cada
cliente). Los archivos se aplican en orden, una sola vez, y cada base registra
en ``dbo.migracion_aplicada`` cuáles ya se ejecutaron.

Un archivo puede contener varios lotes separados por ``GO`` (como en SSMS);
todos se ejecutan dentro de la misma transacción.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app.datos import sqlserver

log = logging.getLogger(__name__)

DIRECTORIO = Path(__file__).parent
PATRON_ARCHIVO = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
SEPARADOR_LOTES = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)

TABLA_CONTROL = """
IF OBJECT_ID('dbo.migracion_aplicada', 'U') IS NULL
    CREATE TABLE dbo.migracion_aplicada (
        version     CHAR(3)       NOT NULL CONSTRAINT PK_migracion_aplicada PRIMARY KEY,
        archivo     NVARCHAR(200) NOT NULL,
        aplicada_en DATETIME2(0)  NOT NULL
            CONSTRAINT DF_migracion_aplicada_aplicada_en DEFAULT SYSUTCDATETIME()
    );
"""


class ErrorMigracion(RuntimeError):
    """Falló la aplicación de un archivo de migración."""


@dataclass(frozen=True)
class Migracion:
    version: str
    archivo: Path

    def lotes(self) -> list[str]:
        texto = self.archivo.read_text(encoding="utf-8")
        return [lote.strip() for lote in SEPARADOR_LOTES.split(texto) if lote.strip()]


def listar(conjunto: str) -> list[Migracion]:
    """Devuelve las migraciones del conjunto ordenadas por versión."""
    migraciones = []
    for archivo in sorted((DIRECTORIO / conjunto).glob("*.sql")):
        coincidencia = PATRON_ARCHIVO.match(archivo.name)
        if not coincidencia:
            raise ErrorMigracion(f"Nombre de archivo inválido: {archivo.name}")
        migraciones.append(Migracion(coincidencia.group(1), archivo))

    versiones = [m.version for m in migraciones]
    if len(versiones) != len(set(versiones)):
        raise ErrorMigracion(f"Versiones duplicadas en el conjunto '{conjunto}'")
    return migraciones


def aplicar(base: str, conjunto: str) -> list[str]:
    """Aplica en ``base`` las migraciones pendientes del conjunto.

    Devuelve las versiones aplicadas en esta ejecución.
    """
    aplicadas: list[str] = []
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        cursor.execute(TABLA_CONTROL)
        conexion.commit()
        ya_aplicadas = {
            fila.version for fila in cursor.execute("SELECT version FROM dbo.migracion_aplicada")
        }

        for migracion in listar(conjunto):
            if migracion.version in ya_aplicadas:
                continue
            try:
                for lote in migracion.lotes():
                    cursor.execute(lote)
                cursor.execute(
                    "INSERT INTO dbo.migracion_aplicada (version, archivo) VALUES (?, ?)",
                    migracion.version,
                    migracion.archivo.name,
                )
                conexion.commit()
            except Exception as error:
                conexion.rollback()
                raise ErrorMigracion(
                    f"Falló {migracion.archivo.name} en la base {base}: {error}"
                ) from error
            log.info("Migración %s aplicada en %s", migracion.archivo.name, base)
            aplicadas.append(migracion.version)
    return aplicadas
