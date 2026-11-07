"""Passkeys de los usuarios en la base SQL Server del tenant."""

from dataclasses import dataclass
from datetime import datetime

from app.datos import sqlserver


@dataclass(frozen=True)
class Passkey:
    id: str
    usuario_id: str
    credencial_id: str
    clave_publica: bytes
    contador: int
    nombre: str
    creada_en: datetime
    ultimo_uso: datetime | None


COLUMNAS = "id, usuario_id, credencial_id, clave_publica, contador, nombre, creada_en, ultimo_uso"


def _passkey(f) -> Passkey:
    return Passkey(
        str(f.id),
        str(f.usuario_id),
        f.credencial_id,
        bytes(f.clave_publica),
        f.contador,
        f.nombre,
        f.creada_en,
        f.ultimo_uso,
    )


def listar(base: str, tenant_id: str, usuario_id: str) -> list[Passkey]:
    with sqlserver.conectar(base) as conexion:
        filas = (
            conexion.cursor()
            .execute(
                f"""SELECT {COLUMNAS} FROM dbo.passkey
                    WHERE tenant_id = ? AND usuario_id = ? ORDER BY creada_en""",
                tenant_id,
                usuario_id,
            )
            .fetchall()
        )
    return [_passkey(f) for f in filas]


def crear(
    base: str,
    tenant_id: str,
    usuario_id: str,
    credencial_id: str,
    clave_publica: bytes,
    contador: int,
    nombre: str,
) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            """INSERT INTO dbo.passkey
                   (tenant_id, usuario_id, credencial_id, clave_publica, contador, nombre)
               VALUES (?, ?, ?, ?, ?, ?)""",
            tenant_id,
            usuario_id,
            credencial_id,
            clave_publica,
            contador,
            nombre,
        )
        conexion.commit()


def registrar_uso(base: str, tenant_id: str, passkey_id: str, contador: int) -> None:
    with sqlserver.conectar(base) as conexion:
        conexion.cursor().execute(
            """UPDATE dbo.passkey SET contador = ?, ultimo_uso = SYSUTCDATETIME()
               WHERE tenant_id = ? AND id = ?""",
            contador,
            tenant_id,
            passkey_id,
        )
        conexion.commit()


def eliminar(base: str, tenant_id: str, usuario_id: str, passkey_id: str) -> bool:
    with sqlserver.conectar(base) as conexion:
        borradas = (
            conexion.cursor()
            .execute(
                "DELETE FROM dbo.passkey WHERE tenant_id = ? AND usuario_id = ? AND id = ?",
                tenant_id,
                usuario_id,
                passkey_id,
            )
            .rowcount
        )
        conexion.commit()
    return borradas == 1
