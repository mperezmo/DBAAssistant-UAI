"""Roles finales del TFI: Consulta pasa a Lectura y se suma Analista (migración 016)."""

import pytest

from app.datos import migraciones, mongo, sqlserver
from app.tenants import servicios

pytestmark = pytest.mark.integracion


def _volver_a_consulta(cursor, tenant_id) -> None:
    """Deja la base como antes de la 016: rol Consulta y listas de roles con Consulta."""
    cursor.execute("ALTER TABLE dbo.usuario DROP CONSTRAINT CK_usuario_rol")
    cursor.execute(
        "ALTER TABLE dbo.usuario ADD CONSTRAINT CK_usuario_rol "
        "CHECK (rol IN ('ADMINISTRADOR', 'DBA', 'CONSULTA'))"
    )
    cursor.execute(
        "INSERT INTO dbo.usuario (tenant_id, email, rol) VALUES (?, 'lu@acme.com', 'CONSULTA')",
        tenant_id,
    )
    cursor.execute(
        "INSERT INTO dbo.regla_alerta (tenant_id, nombre, metrica, operador, valor, severidad, "
        "sostenida_segundos, destinatarios, push, creada_por) "
        "VALUES (?, 'CPU', 'cpu', '>', 90, 'warning', 60, 'DBA,CONSULTA', 1, 'a@acme.com')",
        tenant_id,
    )
    cursor.execute(
        "INSERT INTO dbo.regla_alerta (tenant_id, nombre, metrica, operador, valor, severidad, "
        "sostenida_segundos, destinatarios, push, creada_por) "
        "VALUES (?, 'Memoria', 'memoria', '>', 90, 'warning', 60, 'DBA', 1, 'a@acme.com')",
        tenant_id,
    )
    cursor.execute(
        "INSERT INTO dbo.workaround (tenant_id, nombre, descripcion, categoria, criticidad, "
        "ambientes, roles, transaccional, version_actual, creado_por) "
        "VALUES (?, 'Propio', 'x', 'performance', 'info', 'DEV', 'ADMINISTRADOR,CONSULTA', 1, "
        "1, 'a@acme.com')",
        tenant_id,
    )
    cursor.execute("DELETE FROM dbo.migracion_aplicada WHERE version = '016'")


async def test_migracion_016_pasa_consulta_a_lectura(prefijo_aislado):
    tenant = await servicios.aprovisionar("acme", "ACME S.A.", "admin@acme.com")
    with sqlserver.conectar(tenant.base_sqlserver) as conexion:
        _volver_a_consulta(conexion.cursor(), tenant.id)
        conexion.commit()

    assert migraciones.aplicar(tenant.base_sqlserver, "tenant") == ["016"]

    with sqlserver.conectar(tenant.base_sqlserver) as conexion:
        cursor = conexion.cursor()
        rol = cursor.execute("SELECT rol FROM dbo.usuario WHERE email = 'lu@acme.com'").fetchone()
        reglas = dict(
            cursor.execute("SELECT nombre, destinatarios FROM dbo.regla_alerta").fetchall()
        )
        roles = cursor.execute("SELECT roles FROM dbo.workaround").fetchone()[0]
        # El rol viejo ya no se acepta; los nuevos, sí.
        alta = "INSERT INTO dbo.usuario (tenant_id, email, rol) VALUES (?, ?, ?)"
        with pytest.raises(Exception, match="CK_usuario_rol"):
            cursor.execute(alta, tenant.id, "x@acme.com", "CONSULTA")
        conexion.rollback()
        cursor.execute(alta, tenant.id, "an@acme.com", "ANALISTA")
        conexion.rollback()
    assert rol[0] == "LECTURA"
    assert reglas == {"CPU": "DBA,ANALISTA,LECTURA", "Memoria": "DBA"}
    assert roles == "ADMINISTRADOR,ANALISTA,LECTURA"


async def test_tablas_sensibles_pasan_consulta_a_analista_y_lectura(prefijo_aislado):
    tenant = await servicios.aprovisionar("acme", "ACME S.A.", "admin@acme.com")
    db = mongo.base(tenant.base_mongodb)
    coleccion = db[mongo.COLECCION_CONTEXTO]
    await coleccion.insert_many(
        [
            {
                "tenant_id": str(tenant.id),
                "tipo": "sensible",
                "clave": "a",
                "roles_consulta": ["ADMINISTRADOR", "DBA", "CONSULTA"],
                "roles_ven_columnas": ["DBA", "CONSULTA"],
            },
            {
                "tenant_id": str(tenant.id),
                "tipo": "sensible",
                "clave": "b",
                "roles_consulta": ["ADMINISTRADOR", "DBA"],
                "roles_ven_columnas": [],
            },
        ]
    )

    assert await mongo.migrar_roles_contexto(db) == 2
    a = await coleccion.find_one({"clave": "a"})
    b = await coleccion.find_one({"clave": "b"})
    assert a["roles_consulta"] == ["ADMINISTRADOR", "DBA", "ANALISTA", "LECTURA"]
    assert a["roles_ven_columnas"] == ["DBA", "ANALISTA", "LECTURA"]
    assert b["roles_consulta"] == ["ADMINISTRADOR", "DBA"]
    # Idempotente: al volver a arrancar no cambia nada.
    assert await mongo.migrar_roles_contexto(db) == 0
    await mongo.cliente().drop_database(tenant.base_mongodb)


async def test_permisos_por_ambiente_en_la_base(prefijo_aislado):
    """Migración 017: restricciones por ambiente, con historia temporal."""
    from app.identidad import repositorios as usuarios

    tenant = await servicios.aprovisionar("acme", "ACME S.A.", "admin@acme.com")
    base, tenant_id = tenant.base_sqlserver, str(tenant.id)
    admin = usuarios.obtener_por_email(base, tenant_id, "admin@acme.com")
    assert admin.restricciones == ()

    usuarios.guardar_permisos(
        base, tenant_id, admin.id, ("DEV", "PROD"), [("PROD", "modificar"), ("PROD", "promover")]
    )
    admin = usuarios.obtener(base, tenant_id, admin.id)
    assert admin.ambientes == ("DEV", "PROD")
    assert admin.restricciones == ("PROD:modificar", "PROD:promover")

    usuarios.guardar_permisos(base, tenant_id, admin.id, ("DEV",), [])
    admin = usuarios.obtener(base, tenant_id, admin.id)
    assert (admin.ambientes, admin.restricciones) == (("DEV",), ())

    with sqlserver.conectar(base) as conexion:
        cursor = conexion.cursor()
        # Lo que se quitó queda en la historia de la tabla temporal.
        historia = cursor.execute("SELECT COUNT(*) FROM historia.usuario_restriccion").fetchone()
        assert historia[0] == 2
        with pytest.raises(Exception, match="CK_usuario_restriccion_accion"):
            cursor.execute(
                "INSERT INTO dbo.usuario_restriccion (usuario_id, tenant_id, ambiente, accion) "
                "VALUES (?, ?, 'DEV', 'borrar_todo')",
                admin.id,
                tenant_id,
            )
        conexion.rollback()
