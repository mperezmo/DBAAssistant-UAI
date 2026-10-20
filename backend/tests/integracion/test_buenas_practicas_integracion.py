"""Buenas prácticas contra SQL Server y MongoDB reales (el motor IA se reemplaza)."""

import asyncio
import os

import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado
from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.identidad import repositorios as usuarios
from app.identidad.entidades import Sesion
from app.modulos.buenas_practicas import analisis_ia, chequeos, servicios
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import DatosConexion
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion

HOST = os.environ.get("SQLSERVER_HOST", "localhost")
PUERTO = int(os.environ.get("SQLSERVER_PUERTO", "14330"))


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    recibidos = []

    async def analizar(contexto, base, hallazgos, reglas):
        recibidos.append((base, hallazgos, reglas))
        return analisis_ia.Informe(
            resumen="Revisión",
            sugerencias=[
                analisis_ia.Sugerencia(
                    titulo=h["descripcion"],
                    tipo=h["tipo"],
                    severidad=h["severidad"],
                    objeto=h["objeto"],
                    descripcion=h["descripcion"],
                    sql=h["sql"],
                    indice_hallazgo=n,
                )
                for n, h in enumerate(hallazgos)
            ],
        )

    monkeypatch.setattr(analisis_ia, "analizar", analizar)
    yield recibidos
    cifrado.reiniciar()


async def test_analisis_de_punta_a_punta(prefijo_aislado, entorno):
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    dba_id = usuarios.crear(
        tenant.base_sqlserver, str(tenant.id), "dba@acme.com", None, "DBA", ("DEV",)
    )
    dba = Sesion("s", dba_id, str(tenant.id), "acme", "dba@acme.com", "Dba", "DBA", 0)
    password = proveedor_secretos().obtener("sqlserver_password")
    base = f"{prefijo_aislado.sqlserver_prefijo_base}_Ventas"
    sqlserver.crear_base_si_no_existe(base)
    servidor = f"{HOST},{PUERTO}"
    with sqlserver.conectar_externo(servidor, "sa", password, base) as c:
        cursor = c.cursor()
        cursor.execute("CREATE TABLE dbo.clientes (id INT PRIMARY KEY, nombre NVARCHAR(50))")
        cursor.execute(
            "CREATE TABLE dbo.pedidos (id INT PRIMARY KEY NONCLUSTERED, "
            "cliente_id INT REFERENCES dbo.clientes(id), estado INT, nota NTEXT)"
        )
        cursor.execute("CREATE TABLE dbo.logs (texto NVARCHAR(100))")
        cursor.execute(
            "INSERT dbo.clientes SELECT TOP 20000 ROW_NUMBER() OVER (ORDER BY (SELECT 1)), 'x' "
            "FROM sys.all_objects a CROSS JOIN sys.all_objects b"
        )
        cursor.execute(
            "INSERT dbo.pedidos (id, cliente_id, estado) SELECT id, id, id % 7 FROM dbo.clientes"
        )
        # Consultas que dejan rastro en las DMVs (estadísticas e índices faltantes).
        # Con un JOIN el optimizador hace la optimización completa y registra el índice
        # faltante (en un plan trivial no lo hace).
        for _ in range(3):
            cursor.execute(
                "SELECT c.nombre, COUNT(*) FROM dbo.pedidos p JOIN dbo.clientes c "
                "ON c.id = p.cliente_id WHERE p.estado = 3 GROUP BY c.nombre"
            ).fetchall()
    conexion = await conexiones.crear(
        tenant, dba, DatosConexion("LOCAL", HOST, PUERTO, None, "DEV", "sa", password)
    )

    # Las consultas de los chequeos corren contra el motor real.
    with sqlserver.conectar_externo(servidor, "sa", password, base) as c:
        hallazgos = chequeos.ejecutar(c, list(chequeos.TIPOS))
    esquema = {h["descripcion"] for h in hallazgos if h["tipo"] == "esquema"}
    assert "Tabla sin clave primaria ni índice clustered" in esquema  # dbo.logs
    assert "Tabla heap: la clave primaria no es clustered" in esquema  # dbo.pedidos
    assert any(d.startswith("Clave foránea") and d.endswith("cliente_id") for d in esquema)
    assert "Tipo de dato obsoleto (ntext)" in esquema
    assert any(h["tipo"] == "consultas" for h in hallazgos)
    indices = [h for h in hallazgos if h["tipo"] == "indices"]
    assert indices and "[estado]" in indices[0]["sql"]

    # Flujo completo: informe en segundo plano, guardado en MongoDB.
    await servicios.guardar_regla(tenant, dba, None, "Toda tabla debe tener clave primaria", True)
    informe = await servicios.solicitar(tenant, dba, conexion.id, base, ["esquema", "indices"])
    for _ in range(100):
        await asyncio.sleep(0.1)
        informe = await servicios.informe(tenant, dba, informe["id"])
        if informe["estado"] != "en_curso":
            break
    assert informe["estado"] == "listo" and informe["sugerencias"]
    _, _, reglas = entorno[-1]
    assert reglas == ["Toda tabla debe tener clave primaria"]
    assert [i["id"] for i in await servicios.informes(tenant, dba, conexion.id)] == [informe["id"]]

    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
