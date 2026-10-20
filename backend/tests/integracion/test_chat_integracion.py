"""Contexto de negocio y Chat IA contra SQL Server y MongoDB reales.

El motor IA se reemplaza por respuestas fijas (la prueba no depende de la API de
Anthropic); todo lo demás es real: lectura del esquema, persistencia del contexto y de los
hilos, enmascaramiento con sp_describe_first_result_set, chequeo de bloqueos y escrituras.
"""

import os
import time
from dataclasses import replace

import pytest
from cryptography.fernet import Fernet
from pymongo import MongoClient

from app.core import cifrado
from app.core.errores import ErrorDominio
from app.core.secretos import proveedor_secretos
from app.datos import sqlserver
from app.identidad import repositorios as usuarios
from app.identidad.entidades import Sesion
from app.modulos.chat_ia import motor_ia, servicios
from app.modulos.chat_ia.motor_ia import Traduccion
from app.modulos.conexiones import servicios as conexiones
from app.modulos.conexiones.entidades import DatosConexion
from app.modulos.contexto_negocio import servicios as contexto
from app.modulos.contexto_negocio.entidades import Regla, TablaSensible, Termino
from app.tenants import servicios as tenants

pytestmark = pytest.mark.integracion

HOST = os.environ.get("SQLSERVER_HOST", "localhost")
PUERTO = int(os.environ.get("SQLSERVER_PUERTO", "14330"))


@pytest.fixture
def entorno(monkeypatch, redis_falso):
    monkeypatch.setenv("SECRETO_CLAVE_CIFRADO", Fernet.generate_key().decode())
    cifrado.reiniciar()
    respuestas = []

    async def traducir(texto_contexto, historial, pedido):
        assert "dbo.clientes" in texto_contexto
        return respuestas.pop(0)

    monkeypatch.setattr(motor_ia, "traducir", traducir)
    yield respuestas
    cifrado.reiniciar()


async def test_chat_de_punta_a_punta(prefijo_aislado, entorno):
    respuestas = entorno
    tenant = await tenants.aprovisionar("acme", "ACME", "admin@acme.com")
    dba_id = usuarios.crear(
        tenant.base_sqlserver, str(tenant.id), "dba@acme.com", None, "DBA", ("DEV",)
    )
    adm_id = usuarios.crear(
        tenant.base_sqlserver, str(tenant.id), "adm@acme.com", None, "ADMINISTRADOR", ("DEV",)
    )
    dba = Sesion("s", dba_id, str(tenant.id), "acme", "dba@acme.com", "Dba", "DBA", 0)
    adm = Sesion("s2", adm_id, str(tenant.id), "acme", "adm@acme.com", "Adm", "ADMINISTRADOR", 0)
    password = proveedor_secretos().obtener("sqlserver_password")
    base = f"{prefijo_aislado.sqlserver_prefijo_base}_Ventas"
    sqlserver.crear_base_si_no_existe(base)
    with sqlserver.conectar_externo(f"{HOST},{PUERTO}", "sa", password, base) as c:
        cursor = c.cursor()
        cursor.execute(
            "CREATE TABLE dbo.clientes (id INT PRIMARY KEY, cuit VARCHAR(11), estado INT)"
        )
        cursor.execute(
            "CREATE TABLE dbo.pedidos (id INT PRIMARY KEY, "
            "cliente_id INT REFERENCES dbo.clientes(id))"
        )
        cursor.execute(
            "INSERT dbo.clientes SELECT TOP 1500 ROW_NUMBER() OVER (ORDER BY (SELECT 1)), "
            "'20123456789', 0 FROM sys.all_objects a CROSS JOIN sys.all_objects b"
        )
    conexion = await conexiones.crear(
        tenant, dba, DatosConexion("LOCAL", HOST, PUERTO, None, "DEV", "sa", password)
    )

    # Contexto de negocio sobre el esquema real.
    await contexto.guardar_sensible(
        tenant,
        dba,
        None,
        TablaSensible(
            conexion.id, base, "dbo.clientes", ("cuit",), ("DBA", "ADMINISTRADOR"), ("DBA",), True
        ),
    )
    termino = await contexto.guardar_termino(
        tenant,
        dba,
        None,
        Termino(conexion.id, base, "cliente", "dbo.clientes", ("cte",), "estado = 1"),
    )
    with pytest.raises(ErrorDominio) as error:
        await contexto.guardar_termino(
            tenant, dba, None, Termino(conexion.id, base, "Cliente", "dbo.clientes")
        )
    assert error.value.codigo == "CTX_VAL_004"
    await contexto.guardar_regla(
        tenant,
        dba,
        None,
        Regla("Pedidos solo lectura", ("UPDATE", "DELETE"), "bloquear", tablas=("dbo.pedidos",)),
    )
    listado = await contexto.listar(tenant, conexion.id, base)
    assert listado["terminos"][0]["id"] == termino["id"] and len(listado["reglas"]) == 1
    texto = await contexto.texto_para_ia(tenant, conexion)
    assert "FK: cliente_id→dbo.clientes.id" in texto and "«cliente»" in texto

    # SELECT: 1000 en pantalla de 1500; el Administrador ve el CUIT enmascarado, aun con alias.
    respuestas.append(
        Traduccion(
            sql="SELECT id, cuit AS c FROM dbo.clientes ORDER BY id",
            base=base,
            explicacion="x",
            confianza=95,
        )
    )
    r = await servicios.enviar(tenant, adm, None, conexion.id, "clientes")
    hilo_id, mensaje = r["hilo_id"], r["respuesta"]
    assert mensaje["revision"]["directo"] is True
    resultado = await servicios.ejecutar(tenant, adm, hilo_id, mensaje["id"], mensaje["sql"], base)
    assert resultado["total"] == 1500 and len(resultado["filas"]) == 1000
    assert resultado["filas"][0] == [1, "****"] and resultado["enmascaradas"] == [1]
    filas, auditar = await servicios.exportar(tenant, adm, hilo_id, mensaje["id"])
    exportadas = list(filas)
    assert len(exportadas) == 1501 and exportadas[1] == [1, "****"]
    await auditar(1500)

    # El DBA ve el dato real.
    respuestas.append(
        Traduccion(
            sql="SELECT TOP 1 cuit FROM dbo.clientes", base=base, explicacion="x", confianza=95
        )
    )
    r = await servicios.enviar(tenant, dba, None, conexion.id, "cuit")
    resultado = await servicios.ejecutar(
        tenant, dba, r["hilo_id"], r["respuesta"]["id"], r["respuesta"]["sql"], base
    )
    assert resultado["filas"] == [["20123456789"]]

    # UPDATE en tabla sensible con doble confirmación: pide el segundo factor.
    update = "UPDATE dbo.clientes SET estado = 1 WHERE id <= 10"
    respuestas.append(Traduccion(sql=update, base=base, explicacion="x", confianza=90))
    r = await servicios.enviar(tenant, dba, None, conexion.id, "activá los primeros 10")
    hilo_id, m = r["hilo_id"], r["respuesta"]
    assert m["revision"]["segundo_factor"] is True
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(tenant, dba, hilo_id, m["id"], update, base)
    assert error.value.codigo == "CTX_VAL_001"
    con_factor = replace(dba, segundo_factor_en=time.time())

    # Otra sesión con una transacción abierta sobre la tabla: no se ejecuta.
    bloqueante = sqlserver.abrir_externo(f"{HOST},{PUERTO}", "sa", password, base)
    bloqueante.autocommit = False
    bloqueante.cursor().execute("UPDATE dbo.clientes SET estado = 5 WHERE id = 1400")
    try:
        with pytest.raises(ErrorDominio) as error:
            await servicios.ejecutar(tenant, con_factor, hilo_id, m["id"], update, base)
        assert error.value.codigo == "SQL_LCK_000" and error.value.datos["sesiones"]
    finally:
        bloqueante.rollback()
        bloqueante.close()
    resultado = await servicios.ejecutar(tenant, con_factor, hilo_id, m["id"], update, base)
    assert resultado["filas_afectadas"] == 10
    with sqlserver.conectar_externo(f"{HOST},{PUERTO}", "sa", password, base) as c:
        assert c.cursor().execute("SELECT SUM(estado) FROM dbo.clientes").fetchone()[0] == 10

    # Regla de bloqueo y derivación obligatoria al Sandbox.
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(
            tenant, con_factor, hilo_id, m["id"], "DELETE FROM dbo.pedidos WHERE id = 1", base
        )
    assert error.value.codigo == "CTX_REG_000"
    with pytest.raises(ErrorDominio) as error:
        await servicios.ejecutar(
            tenant, con_factor, hilo_id, m["id"], "ALTER TABLE dbo.clientes ADD x INT", base
        )
    assert error.value.codigo == "SQL_VAL_001"

    # Persistencia del hilo y de la bitácora.
    guardado = await servicios.hilo(tenant, dba, hilo_id)
    assert guardado["mensajes"][1]["ejecucion"]["filas_afectadas"] == 10
    assert [h["id"] for h in await servicios.hilos(tenant, dba)][0] == hilo_id
    cliente = MongoClient(proveedor_secretos().obtener("mongodb_uri"))
    transacciones = cliente[tenant.base_mongodb]["bitacora_transacciones"]
    assert transacciones.count_documents({"accion": "chat.consulta_ejecutada"}) == 3
    assert transacciones.count_documents({"accion": "chat.exportado", "detalle.filas": 1500}) == 1
    auditoria = cliente[tenant.base_mongodb]["bitacora_auditoria"]
    assert auditoria.count_documents({"accion": {"$regex": "^contexto\\."}}) == 3
    await servicios.eliminar_hilo(tenant, dba, hilo_id)
    cliente.drop_database(tenant.base_mongodb)
    cliente.close()
