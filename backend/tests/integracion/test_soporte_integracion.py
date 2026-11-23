"""Historia de la base de la aplicación (Soporte) contra SQL Server y MongoDB reales."""

import asyncio

import pytest
from cryptography.fernet import Fernet

from app.core import cifrado
from app.datos import sqlserver
from app.modulos.soporte import historia, repositorios
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    yield
    cifrado.reiniciar()


def _ejecutar(base: str, sql: str, *parametros):
    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        cursor.execute(sql, *parametros)
        fila = cursor.fetchone() if cursor.description else None
        conexion.commit()
    return fila


async def test_historia_de_estructura_y_datos(prefijo_aislado, entorno):
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    base = tenant.base_sqlserver

    # Estructura inicial: tablas temporales y trigger DDL de la migración 014.
    assert await historia.registrar_estructura(tenant) == 1
    assert await historia.registrar_estructura(tenant) is None
    script = await historia.script(tenant, 1)
    assert "HISTORY_TABLE = [historia].[usuario], HISTORY_RETENTION_PERIOD = 5 YEARS" in script
    assert "-- trigger: trg_historial_ddl" in script

    # Un cambio hecho por fuera de la aplicación (como desde SSMS): lo registra el trigger
    # y al capturar se guarda como cambio detectado.
    _ejecutar(base, "CREATE TABLE dbo.tabla_externa (id INT PRIMARY KEY)")
    assert await historia.registrar_estructura(tenant) == 2
    versiones = (await historia.versiones(tenant))["versiones"]
    assert [(v["version"], v["origen"]) for v in versiones] == [(2, "detectado"), (1, "inicial")]
    detalle = await historia.detalle(tenant, 2)
    assert [(c["operacion"], c["nombre"]) for c in detalle["detalle"]] == [
        ("agregado", "dbo.tabla_externa")
    ]
    ddl = await historia.historial_ddl(tenant, 1, "tabla_externa")
    evento = ddl["registros"][0]
    assert (evento["evento"], evento["objeto"], evento["login"], evento["programa"]) == (
        "CREATE_TABLE",
        "tabla_externa",
        "sa",
        "DBA Assistant",
    )
    assert "CREATE TABLE dbo.tabla_externa" in (await historia.ddl(tenant, evento["id"]))["tsql"]
    comparacion = await historia.comparar(tenant, 1, historia.ACTUAL)
    assert comparacion["resumen"]["agregado"] == 1

    # Datos: la fila tal como estaba antes de modificarla y todas sus versiones.
    antes = _ejecutar(base, "SELECT SYSUTCDATETIME()")[0]
    # Pausa para que la modificación quede claramente después del momento consultado: el
    # inicio del período y SYSUTCDATETIME() pueden diferir en algunos milisegundos.
    await asyncio.sleep(1)
    _ejecutar(base, "UPDATE dbo.usuario SET nombre = ? WHERE email = ?", "Nuevo", "admin@acme.com")
    usuario_id = str(
        _ejecutar(base, "SELECT id FROM dbo.usuario WHERE email = ?", "admin@acme.com")[0]
    )

    pasado = await historia.datos(tenant, "usuario", "momento", antes, usuario_id)
    columnas = pasado["columnas"]
    assert pasado["filas"][0][columnas.index("nombre")] != "Nuevo"
    versiones_fila = await historia.datos(tenant, "usuario", "cambios", None, usuario_id)
    nombres = [f[columnas.index("nombre")] for f in versiones_fila["filas"]]
    assert nombres[0] == "Nuevo" and len(nombres) == 2
    # La versión vigente vence "al fin de los tiempos".
    assert versiones_fila["filas"][0][columnas.index("valido_hasta")].startswith("9999-12-31")

    # Las contraseñas de las conexiones nunca se muestran.
    conexion = await asyncio.to_thread(repositorios.tabla_con_historia, base, "conexion")
    assert "password_sql_cifrada" not in conexion.columnas
    assert "winrm_password_cifrada" not in conexion.columnas and conexion.clave == ("id",)
