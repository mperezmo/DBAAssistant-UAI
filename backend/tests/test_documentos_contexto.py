"""Documentos de negocio en Contexto de Negocio: carga del PDF, resumen revisable y su uso
como contexto de la IA."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fpdf import FPDF

from app.core.errores import ErrorDominio
from app.modulos.conexiones import servicios as conexiones
from app.modulos.contexto_negocio import documentos, servicios
from app.modulos.contexto_negocio.entidades import Documento
from tests import contexto_falso
from tests.test_conexiones import CABECERA, TENANT, _actor, _datos, _navegador
from tests.test_contexto import ctx, entorno  # noqa: F401 (fixtures)


def _pdf(*paginas: str) -> bytes:
    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for texto in paginas:
        pdf.add_page()
        if texto:
            pdf.multi_cell(0, 6, texto)
    return bytes(pdf.output())


FACTURACION = _pdf(
    "Proceso de facturacion. Cada pedido aprobado genera una factura al cliente. "
    "Un cliente con estado 3 esta dado de baja y no se le factura.",
    "Requisito no funcional: las facturas se conservan 10 anios.",
)


async def _esperar_resumenes() -> None:
    await asyncio.gather(*servicios._tareas)


@pytest.fixture
def resumenes(monkeypatch):
    pedidos = []

    async def resumir(esquema_texto, nombre, clase, descripcion, texto):
        pedidos.append((esquema_texto, nombre, clase, descripcion, texto))
        if "falla" in descripcion:
            raise ErrorDominio("IA_API_000", "sin conexión")
        return documentos.ResumenDocumento(
            resumen=f"- Facturación: dbo.pedidos → factura ({clase})", tablas=["dbo.pedidos"]
        )

    monkeypatch.setattr(documentos, "resumir", resumir)
    return pedidos


def test_lectura_del_pdf():
    texto, paginas = documentos.extraer_texto(FACTURACION)
    assert paginas == 2 and "estado 3" in texto and "10 anios" in texto
    for contenido, codigo in (
        (b"no es un pdf", "CTX_DOC_000"),
        (b"%PDF-1.7 roto", "CTX_DOC_000"),
        (_pdf(""), "CTX_DOC_001"),  # sin texto, como un escaneo
    ):
        with pytest.raises(ErrorDominio) as error:
            documentos.extraer_texto(contenido)
        assert error.value.codigo == codigo


async def test_resumen_recorta_documentos_largos(monkeypatch):
    pedidos = []

    async def solicitar(instrucciones, contexto, mensajes, formato, tokens, codigo, timeout):
        pedidos.append((contexto, mensajes[0]["content"]))
        return formato(resumen="ok", tablas=[])

    monkeypatch.setattr(documentos.motor_ia, "solicitar", solicitar)
    largo = "x" * (documentos.MAXIMO_CARACTERES + 10)
    await documentos.resumir("# esquema", "a.pdf", "funcional", "desc", largo)
    contexto, pedido = pedidos[0]
    assert contexto == "# esquema" and "Descripción: desc" in pedido
    assert "[Documento recortado" in pedido and len(pedido) < len(largo) + 500


async def test_carga_resumen_y_contexto_para_la_ia(ctx, resumenes):  # noqa: F811
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    dba = _actor("DBA")
    datos = Documento(conexion.id, "ventas", "funcional", "facturacion.pdf", "Relevamiento")

    cargado = await servicios.cargar_documento(TENANT, dba, datos, FACTURACION)
    assert cargado["estado"] == "procesando" and cargado["base"] == "Ventas"
    assert cargado["paginas"] == 2 and cargado["bytes"] == len(FACTURACION)
    assert ctx["eventos"][-1][0] == "contexto.documento_cargado"
    await _esperar_resumenes()

    # La IA recibe el esquema de la base y el texto del PDF.
    esquema_texto, nombre, clase, descripcion, texto = resumenes[0]
    assert "## Base Ventas" in esquema_texto and "dbo.clientes" in esquema_texto
    assert (nombre, clase, descripcion) == ("facturacion.pdf", "funcional", "Relevamiento")
    assert "estado 3" in texto

    listado = (await servicios.listar(TENANT, conexion.id, "Ventas"))["documentos"]
    assert [d["estado"] for d in listado] == ["listo"] and listado[0]["tablas"] == ["dbo.pedidos"]
    para_ia = await servicios.texto_para_ia(TENANT, conexion)
    assert "# Documentos de negocio (resúmenes)" in para_ia
    assert "## facturacion.pdf (documento funcional, base Ventas)" in para_ia
    assert "dbo.pedidos → factura" in para_ia
    # El documento de una base no aparece al ver otra.
    assert (await servicios.listar(TENANT, conexion.id, "Compras"))["documentos"] == []

    # Corrección manual: queda auditada con el valor anterior y el nuevo.
    corregido = await servicios.guardar_resumen(
        TENANT, dba, cargado["id"], "  - Clientes en estado 3: baja  "
    )
    assert corregido["resumen"] == "- Clientes en estado 3: baja" and corregido["editado"]
    accion, detalle = ctx["eventos"][-1]
    assert accion == "contexto.documento_resumen_modificado"
    assert detalle["anterior"].startswith("- Facturación") and detalle["nuevo"].endswith("baja")
    assert "estado 3: baja" in await servicios.texto_para_ia(TENANT, conexion)
    with pytest.raises(ErrorDominio):
        await servicios.guardar_resumen(TENANT, dba, cargado["id"], "   ")

    # Volver a resumir descarta la corrección.
    regenerado = await servicios.regenerar_resumen(TENANT, dba, cargado["id"])
    assert regenerado["estado"] == "procesando"
    await _esperar_resumenes()
    listado = (await servicios.listar(TENANT, conexion.id, "Ventas"))["documentos"]
    assert listado[0]["editado"] is False and listado[0]["resumen"].startswith("- Facturación")

    nombre, contenido = await servicios.descargar_documento(TENANT, cargado["id"])
    assert nombre == "facturacion.pdf" and contenido == FACTURACION

    await servicios.eliminar(TENANT, dba, "documento", cargado["id"])
    assert contexto_falso.archivos_de(ctx["almacen"]) == {}
    assert ctx["eventos"][-1][0] == "contexto.documento_eliminado"
    assert "Documentos de negocio" not in await servicios.texto_para_ia(TENANT, conexion)


async def test_documento_para_todas_las_bases_y_fallas(ctx, resumenes):  # noqa: F811
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    dba = _actor("DBA")
    general = Documento(conexion.id, None, "no_funcional", "sla.pdf", "falla")
    cargado = await servicios.cargar_documento(TENANT, dba, general, FACTURACION)
    await _esperar_resumenes()
    # Para todas las bases: aparece en cualquiera; si la IA falla, queda sin resumen.
    [visto] = (await servicios.listar(TENANT, conexion.id, "Compras"))["documentos"]
    assert visto["base"] is None and visto["estado"] == "error" and visto["error"] == "IA_API_000"
    assert "Documentos de negocio" not in await servicios.texto_para_ia(TENANT, conexion)

    # Un resumen que quedó "procesando" (por ejemplo, por un reinicio) se ve interrumpido.
    viejo = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    ctx["almacen"][cargado["id"]].update(estado="procesando", actualizado_en=viejo)
    [visto] = (await servicios.listar(TENANT, conexion.id, "Ventas"))["documentos"]
    assert visto["estado"] == "error" and visto["error"] == "interrumpido"

    casos = (
        (_actor("CONSULTA"), general, FACTURACION, "USR_PER_000"),
        (dba, Documento(conexion.id, "Inexistente", "funcional", "a.pdf"), FACTURACION, None),
        (dba, Documento(conexion.id, None, "otra", "a.pdf"), FACTURACION, None),
        (dba, general, b"x" * (10 * 1024 * 1024 + 1), "CTX_DOC_002"),
        (dba, general, b"hola", "CTX_DOC_000"),
    )
    for actor, datos, contenido, codigo in casos:
        with pytest.raises(ErrorDominio) as error:
            await servicios.cargar_documento(TENANT, actor, datos, contenido)
        assert error.value.codigo == (codigo or "USR_VAL_002")
    with pytest.raises(ErrorDominio) as error:
        await servicios.guardar_resumen(TENANT, _actor("CONSULTA"), cargado["id"], "x")
    assert error.value.codigo == "USR_PER_000"
    with pytest.raises(ErrorDominio) as error:
        await servicios.descargar_documento(TENANT, "no-existe")
    assert error.value.codigo == "CTX_VAL_000"


async def test_api_documentos(ctx, resumenes):  # noqa: F811
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    dba = await _navegador(ctx, "DBA")
    consulta = await _navegador(ctx, "CONSULTA")
    formulario = {"conexion_id": conexion.id, "clase": "funcional", "base": "Ventas"}
    archivo = {"archivo": ("Facturación.pdf", FACTURACION, "application/pdf")}

    r = dba.post("/api/contexto/documentos", data=formulario, files=archivo, headers=CABECERA)
    assert r.status_code == 201 and r.json()["nombre"] == "Facturación.pdf"
    documento_id = r.json()["id"]
    await _esperar_resumenes()
    # Sin la cabecera anti CSRF no se acepta.
    assert dba.post("/api/contexto/documentos", data=formulario, files=archivo).status_code == 403
    rechazado = consulta.post(
        "/api/contexto/documentos", data=formulario, files=archivo, headers=CABECERA
    )
    assert rechazado.status_code == 403
    grande = {"archivo": ("g.pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024), "application/pdf")}
    r = dba.post("/api/contexto/documentos", data=formulario, files=grande, headers=CABECERA)
    assert r.status_code == 413 and r.json()["codigo"] == "CTX_DOC_002"

    # Consulta ve y descarga; Administrador y DBA corrigen, vuelven a resumir y eliminan.
    listado = consulta.get(f"/api/contexto/conexiones/{conexion.id}?base=Ventas").json()
    assert listado["documentos"][0]["estado"] == "listo"
    pdf = consulta.get(f"/api/contexto/documentos/{documento_id}/archivo")
    assert pdf.status_code == 200 and pdf.content == FACTURACION
    assert pdf.headers["content-type"] == "application/pdf"
    assert "Facturaci%C3%B3n.pdf" in pdf.headers["content-disposition"]
    r = dba.put(
        f"/api/contexto/documentos/{documento_id}/resumen",
        json={"resumen": "Corregido"},
        headers=CABECERA,
    )
    assert r.status_code == 200 and r.json()["editado"] is True
    r = dba.post(f"/api/contexto/documentos/{documento_id}/resumen", headers=CABECERA)
    assert r.status_code == 200 and r.json()["estado"] == "procesando"
    await _esperar_resumenes()
    eliminado = dba.delete(f"/api/contexto/documentos/{documento_id}", headers=CABECERA)
    assert eliminado.status_code == 204
    assert dba.get(f"/api/contexto/documentos/{documento_id}/archivo").status_code == 404
