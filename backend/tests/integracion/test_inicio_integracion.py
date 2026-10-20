"""Lecturas del panel principal contra SQL Server y MongoDB reales."""

from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado
from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.identidad.entidades import Sesion
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import DatosConexion
from app.modulos.inicio import repositorios, servicios
from app.modulos.monitor import repositorios as monitor
from app.modulos.workarounds import integrados
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    yield
    cifrado.reiniciar()


async def test_resumen_con_datos_reales(prefijo_aislado, entorno):
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    dba = Sesion("s", "u", str(tenant.id), "acme", "dba@acme.com", "Dba", "DBA", 0)
    datos = DatosConexion("PROD-01", "127.0.0.1", 14330, None, "PROD", "sa", "x")
    prod = await conexiones.crear(tenant, dba, datos)
    dev = await conexiones.crear(
        tenant, dba, DatosConexion(**{**datos.__dict__, "alias": "DEV-01", "ambiente": "DEV"})
    )

    ahora = datetime.now(UTC)
    hace_una_hora = (ahora - timedelta(hours=1)).replace(tzinfo=None)
    with sqlserver.conectar(tenant.base_sqlserver) as c:
        cursor = c.cursor()
        for clave, severidad, normalizada in (
            ("evento:instancia", "critical", None),
            ("umbral:cpu", "warning", None),
        ):
            cursor.execute(
                """INSERT dbo.alerta (tenant_id, conexion_id, conexion_alias, servidor, ambiente,
                       origen, clave, titulo, descripcion, severidad, disparada_en, normalizada_en)
                   VALUES (?, ?, 'PROD-01', 'x', 'PROD', 'evento', ?, 't', 'd', ?, ?, ?)""",
                str(tenant.id),
                prod.id,
                clave,
                severidad,
                hace_una_hora,
                normalizada,
            )
        c.commit()

    for i, latencia in enumerate(range(1, 101)):
        await monitor.insertar_muestra(
            tenant.base_mongodb,
            prod.id,
            ahora - timedelta(seconds=i),
            {"latencia": float(latencia)},
        )
    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente[tenant.base_mongodb]["bitacora_sandbox"].insert_many(
        [
            {
                "tenant_id": str(tenant.id),
                "estado": "ok",
                "promocion": None,
                "ambiente": "PROD",
                "ts": ahora,
                "sql": "DELETE dbo.t WHERE a = 1",
                "base": "B",
            },
            {
                "tenant_id": str(tenant.id),
                "estado": "ok",
                "promocion": {"por": "x"},
                "ambiente": "PROD",
                "ts": ahora,
                "sql": "DELETE dbo.t WHERE a = 1",
                "base": "B",
            },
            {
                "tenant_id": str(tenant.id),
                "estado": "ok",
                "promocion": None,
                "ambiente": "DEV",
                "ts": ahora,
                "sql": "DELETE dbo.t WHERE a = 1",
                "base": "B",
            },
        ]
    )
    await monitor.guardar_estado(
        str(tenant.id), dev.id, {"estado": "ok", "valores": {"sesiones": 5}, "bloqueos": []}
    )

    # SQL Server guarda creado_en en DATETIME2(0), que redondea al segundo: se mide un poco
    # después para que la instancia recién creada tenga un período positivo.
    resumen = await servicios.resumen(tenant, dba, "PROD", ahora + timedelta(seconds=2))
    assert resumen["instancias"] == {"total": 1, "nuevas_mes": 1}
    assert resumen["alertas"] == {"activas": 2, "criticas": 1}
    assert 90 <= resumen["salud"]["latencia_p95"] <= 100
    # Registrada hace instantes y caída desde hace una hora: el período es casi todo caída.
    assert resumen["salud"]["disponibilidad"] < 5
    assert resumen["pendientes_promocion"] == 1
    assert resumen["workarounds"]["disponibles"] == len(integrados.CATALOGO)

    todos = await servicios.resumen(tenant, dba, servicios.TODOS, ahora)
    assert todos["salud"]["online"] == 1 and todos["salud"]["sesiones"] == 5
    caidas = repositorios.caidas(
        tenant.base_sqlserver,
        str(tenant.id),
        (ahora - timedelta(days=1)).replace(tzinfo=None),
        ahora.replace(tzinfo=None),
    )
    assert [c["conexion_id"] for c in caidas] == [prod.id]

    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
