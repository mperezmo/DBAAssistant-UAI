"""Casos de uso de conexiones: configurar, modificar, eliminar y probar (CU-000-000)."""

import json
import logging
import re
from dataclasses import replace

from starlette.concurrency import run_in_threadpool

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.datos import sqlserver, winrm
from app.identidad import reautenticacion
from app.identidad.entidades import AMBIENTES, Sesion
from app.modulos.auditoria import repositorios as auditoria
from app.modulos.auditoria.entidades import EventoAuditoria
from app.modulos.conexiones import repositorios
from app.modulos.conexiones.entidades import (
    Conexion,
    DatosConexion,
    ResultadoPrueba,
    ServicioWindows,
)
from app.tenants.entidades import Tenant

log = logging.getLogger(__name__)
PATRON_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}$")
PATRON_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,254}$")
PUERTO_WINRM_HTTP, PUERTO_WINRM_HTTPS = 5985, 5986
CONSULTA_PRUEBA = """
    SELECT CAST(SERVERPROPERTY('ProductVersion') AS NVARCHAR(128)),
           CAST(SERVERPROPERTY('Edition') AS NVARCHAR(128)),
           (SELECT COUNT(*) FROM sys.databases WHERE database_id > 4)
"""


def _texto(valor: str | None) -> str | None:
    valor = (valor or "").strip()
    return valor or None


def _validar(datos: DatosConexion, nueva: bool) -> DatosConexion:
    alias, host = (datos.alias or "").strip(), (datos.host or "").strip()
    usuario = (datos.usuario_sql or "").strip()
    if not alias or not host or not usuario or (nueva and not datos.password_sql):
        raise ErrorDominio("USR_VAL_002", "Alias, host, usuario y contraseña son obligatorios.")
    if not PATRON_ALIAS.match(alias):
        raise ErrorDominio("USR_VAL_002", "Alias inválido.")
    if not PATRON_HOST.match(host):
        raise ErrorDominio("USR_VAL_002", "Host inválido.")
    if datos.ambiente not in AMBIENTES:
        raise ErrorDominio("USR_VAL_002", "Ambiente inválido.")
    for puerto in (datos.puerto, datos.winrm_puerto):
        if puerto is not None and not 1 <= puerto <= 65535:
            raise ErrorDominio("USR_VAL_002", "Puerto inválido.")

    winrm_usuario = _texto(datos.winrm_usuario)
    winrm_puerto = datos.winrm_puerto
    if winrm_usuario and not winrm_puerto:
        winrm_puerto = PUERTO_WINRM_HTTPS if datos.winrm_https else PUERTO_WINRM_HTTP
    return replace(
        datos,
        alias=alias,
        host=host,
        usuario_sql=usuario,
        instancia=_texto(datos.instancia),
        winrm_usuario=winrm_usuario,
        winrm_puerto=winrm_puerto if winrm_usuario else None,
    )


async def _auditar(tenant: Tenant, actor: Sesion, accion: str, detalle: dict) -> None:
    await auditoria.registrar(
        tenant.base_mongodb,
        EventoAuditoria(
            accion=accion, usuario=actor.email, tenant_id=str(tenant.id), detalle=detalle
        ),
    )


async def listar(tenant: Tenant) -> list[Conexion]:
    return await run_in_threadpool(repositorios.listar, tenant.base_sqlserver, str(tenant.id))


async def obtener(tenant: Tenant, conexion_id: str) -> Conexion:
    conexion = await run_in_threadpool(
        repositorios.obtener, tenant.base_sqlserver, str(tenant.id), conexion_id
    )
    if conexion is None:
        raise ErrorDominio("CON_DB_002", conexion_id)
    return conexion


def _probar(servidor: str, usuario: str, password: str) -> ResultadoPrueba:
    try:
        with sqlserver.conectar_externo(servidor, usuario, password) as conexion:
            version, edicion, bases = conexion.cursor().execute(CONSULTA_PRUEBA).fetchone()
    except Exception as error:
        # El mensaje del driver identifica la causa (login, red, nombre de instancia).
        raise ErrorDominio("CON_DB_000", str(error)) from error
    return ResultadoPrueba(version=version, edicion=edicion, bases=bases)


async def probar(tenant: Tenant, datos: DatosConexion, conexion_id: str | None) -> ResultadoPrueba:
    """Prueba los datos del formulario; sin contraseña usa la guardada de la conexión."""
    password = datos.password_sql
    if not password and conexion_id:
        password = cifrado.descifrar((await obtener(tenant, conexion_id)).password_sql_cifrada)
    datos = _validar(replace(datos, password_sql=password), nueva=True)
    servidor = sqlserver.servidor_externo(datos.host, datos.puerto, datos.instancia)
    return await run_in_threadpool(_probar, servidor, datos.usuario_sql, password)


# Lista los servicios del motor (MSSQLSERVER o MSSQL$INSTANCIA) con su estado.
SCRIPT_SERVICIOS_SQL = (
    "Get-Service -Name 'MSSQLSERVER','MSSQL$*' -ErrorAction SilentlyContinue | "
    "Select-Object Name, DisplayName, @{n='Status';e={$_.Status.ToString()}} | "
    "ConvertTo-Json -Compress"
)


def _probar_winrm(
    host: str, puerto: int, https: bool, usuario: str, password: str
) -> list[ServicioWindows]:
    try:
        resultado = winrm.ejecutar_powershell(
            host, puerto, https, usuario, password, SCRIPT_SERVICIOS_SQL
        )
    except Exception as error:
        # Red, puerto cerrado, credenciales rechazadas (401) o certificado.
        raise ErrorDominio("WRM_000", str(error)) from error
    if resultado.codigo != 0:
        raise ErrorDominio("WRM_000", resultado.error.strip()[:500])
    try:
        datos = json.loads(resultado.salida) if resultado.salida.strip() else []
    except ValueError as error:
        raise ErrorDominio("WRM_000", "Respuesta inesperada de PowerShell.") from error
    if isinstance(datos, dict):  # PowerShell devuelve un objeto si hay un solo servicio
        datos = [datos]
    return [ServicioWindows(d["Name"], d["DisplayName"], d["Status"]) for d in datos]


async def probar_winrm(
    tenant: Tenant, datos: DatosConexion, conexion_id: str | None
) -> list[ServicioWindows]:
    """Prueba WinRM con los datos del formulario y lista los servicios de SQL Server.

    Sin contraseña de Windows usa la guardada de la conexión.
    """
    password = datos.winrm_password
    if not password and conexion_id:
        cifrada = (await obtener(tenant, conexion_id)).winrm_password_cifrada
        password = cifrado.descifrar(cifrada) if cifrada else None
    datos = _validar(datos, nueva=False)
    if not datos.winrm_usuario or not password:
        raise ErrorDominio("USR_VAL_002", "Usuario y contraseña de Windows son obligatorios.")
    return await run_in_threadpool(
        _probar_winrm,
        datos.host,
        datos.winrm_puerto,
        datos.winrm_https,
        datos.winrm_usuario,
        password,
    )


async def crear(tenant: Tenant, actor: Sesion, datos: DatosConexion) -> Conexion:
    datos = _validar(datos, nueva=True)
    return await _guardar(tenant, actor, datos, None)


async def modificar(
    tenant: Tenant, actor: Sesion, conexion_id: str, datos: DatosConexion
) -> Conexion:
    actual = await obtener(tenant, conexion_id)
    datos = _validar(datos, nueva=False)
    return await _guardar(tenant, actor, datos, actual)


async def _guardar(
    tenant: Tenant, actor: Sesion, datos: DatosConexion, actual: Conexion | None
) -> Conexion:
    if await run_in_threadpool(
        repositorios.existe_alias,
        tenant.base_sqlserver,
        str(tenant.id),
        datos.alias,
        actual.id if actual else None,
    ):
        raise ErrorDominio("CON_DB_001", datos.alias)

    # Las contraseñas vacías en una edición conservan las guardadas.
    password_sql = (
        cifrado.cifrar(datos.password_sql) if datos.password_sql else actual.password_sql_cifrada
    )
    winrm_password = None
    if datos.winrm_usuario:
        winrm_password = (
            cifrado.cifrar(datos.winrm_password)
            if datos.winrm_password
            else (actual.winrm_password_cifrada if actual else None)
        )

    conexion = Conexion(
        id=actual.id if actual else "",
        tenant_id=str(tenant.id),
        alias=datos.alias,
        host=datos.host,
        puerto=datos.puerto,
        instancia=datos.instancia,
        ambiente=datos.ambiente,
        usuario_sql=datos.usuario_sql,
        password_sql_cifrada=password_sql,
        winrm_usuario=datos.winrm_usuario,
        winrm_password_cifrada=winrm_password,
        winrm_puerto=datos.winrm_puerto,
        winrm_https=datos.winrm_https,
    )
    conexion_id = await run_in_threadpool(
        repositorios.guardar, tenant.base_sqlserver, conexion, actual is None
    )
    await _auditar(
        tenant,
        actor,
        "conexion.creada" if actual is None else "conexion.modificada",
        {
            "alias": datos.alias,
            "host": datos.host,
            "ambiente": datos.ambiente,
            "credenciales_actualizadas": bool(datos.password_sql or datos.winrm_password),
        },
    )
    return await obtener(tenant, conexion_id)


async def eliminar(tenant: Tenant, actor: Sesion, conexion_id: str) -> None:
    conexion = await obtener(tenant, conexion_id)
    if conexion.ambiente == "PROD":
        reautenticacion.exigir_segundo_factor_reciente(actor)
    await run_in_threadpool(
        repositorios.eliminar, tenant.base_sqlserver, str(tenant.id), conexion.id
    )
    await _auditar(
        tenant,
        actor,
        "conexion.eliminada",
        {"alias": conexion.alias, "host": conexion.host, "ambiente": conexion.ambiente},
    )
