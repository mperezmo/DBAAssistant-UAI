"""Documentos de negocio en Contexto de Negocio: conversión del PDF a Markdown, interpretación
de la IA (resumen, tablas y propuestas) y permisos de quien lo carga."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fpdf import FPDF

from app.core.errores import ErrorDominio
from app.modulos.conexiones import servicios as conexiones
from app.modulos.contexto_negocio import documentos, servicios
from app.modulos.contexto_negocio.entidades import Documento, Termino
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
TERMINO = documentos.TerminoPropuesto(
    termino="Cliente dado de baja",
    entidad="[dbo].[clientes]",
    sinonimos=["baja", " baja ", ""],
    descripcion="clientes con estado = 3",
)
REGLA = documentos.ReglaPropuesta(
    descripcion="Los pedidos facturados no se modifican",
    acciones=["UPDATE", "DELETE"],
    efecto="bloquear",
    tablas=["Ventas.dbo.pedidos"],
)


def _consulta(email="consulta@acme.com"):
    return replace(_actor("CONSULTA"), email=email)


async def _esperar() -> None:
    await asyncio.gather(*servicios._tareas)


@pytest.fixture
def interpretaciones(monkeypatch):
    """La IA simulada: propone un término y una regla válidos, más otros que se descartan
    (tablas inexistentes, repetidos, sin acciones)."""
    pedidos = []

    async def interpretar(contexto, nombre, markdown):
        pedidos.append((contexto, nombre, markdown))
        if "falla" in nombre:
            raise ErrorDominio("IA_API_000", "sin conexión")
        return documentos.Interpretacion(
            resumen="  - Facturación: dbo.pedidos → factura  ",
            tablas=["dbo.pedidos", "dbo.inexistente", "pedidos"],
            terminos=[
                TERMINO,
                TERMINO.model_copy(update={"termino": "cliente dado de baja"}),
                TERMINO.model_copy(update={"termino": "Factura", "entidad": "dbo.facturas"}),
            ],
            reglas=[
                REGLA,
                REGLA.model_copy(update={"descripcion": "Sin tablas", "tablas": ["dbo.nada"]}),
                REGLA.model_copy(update={"descripcion": "Sin acciones", "acciones": []}),
            ],
        )

    monkeypatch.setattr(documentos, "interpretar", interpretar)
    return pedidos


def test_conversion_a_markdown():
    markdown, paginas = documentos.convertir(FACTURACION)
    assert paginas == 2 and "estado 3" in markdown and "10 anios" in markdown
    assert "\f" not in markdown and "\n\n\n" not in markdown
    for contenido, codigo in (
        (b"no es un pdf", "CTX_DOC_000"),
        (b"%PDF-1.7 roto", "CTX_DOC_000"),
        (_pdf(""), "CTX_DOC_001"),  # sin texto, como un escaneo
    ):
        with pytest.raises(ErrorDominio) as error:
            documentos.convertir(contenido)
        assert error.value.codigo == codigo


def test_pdf_que_markitdown_no_puede_convertir(monkeypatch):
    class Roto:
        def convert(self, *_):
            raise ValueError("estructura inválida")

    monkeypatch.setattr(documentos, "PdfConverter", Roto)
    with pytest.raises(ErrorDominio) as error:
        documentos.convertir(FACTURACION)
    assert error.value.codigo == "CTX_DOC_000"


async def test_interpretacion_recorta_documentos_largos(monkeypatch):
    pedidos = []

    async def solicitar(instrucciones, contexto, mensajes, formato, tokens, codigo, timeout):
        pedidos.append((contexto, mensajes[0]["content"]))
        return formato(resumen="ok", tablas=[], terminos=[], reglas=[])

    monkeypatch.setattr(documentos.motor_ia, "solicitar", solicitar)
    largo = "x" * (documentos.MAXIMO_CARACTERES + 10)
    await documentos.interpretar("# esquema", "a.pdf", largo)
    contexto, pedido = pedidos[0]
    assert contexto == "# esquema" and pedido.startswith("Documento: a.pdf")
    assert "[Documento recortado" in pedido and len(pedido) < len(largo) + 500


async def test_carga_interpretacion_y_propuestas(ctx, interpretaciones):  # noqa: F811
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    autor, otro, dba = _consulta(), _consulta("otro@acme.com"), _actor("DBA")

    # Cualquier rol carga; el documento queda en la base elegida.
    datos = Documento(conexion.id, "ventas", "facturacion.pdf")
    cargado = await servicios.cargar_documento(TENANT, autor, datos, FACTURACION)
    assert cargado["estado"] == "procesando" and cargado["base"] == "Ventas"
    assert cargado["paginas"] == 2 and cargado["puede_gestionar"] is True
    assert ctx["eventos"][-1][0] == "contexto.documento_cargado"
    # Se guardan el PDF y su Markdown.
    assert len(contexto_falso.archivos_de(ctx["almacen"])) == 2
    await _esperar()

    contexto, nombre, markdown = interpretaciones[0]
    assert "## Base Ventas" in contexto and "dbo.clientes" in contexto
    assert nombre == "facturacion.pdf" and "estado 3" in markdown

    [visto] = (await servicios.listar(TENANT, conexion.id, "Ventas", autor))["documentos"]
    assert visto["estado"] == "listo" and visto["resumen"] == "- Facturación: dbo.pedidos → factura"
    assert visto["tablas"] == ["dbo.pedidos"]
    # Solo quedan las propuestas con tablas que existen, sin repetidos.
    termino, regla = visto["propuestas"]
    assert termino["tipo"] == "termino" and termino["estado"] == "pendiente"
    assert termino["datos"] == {
        "termino": "Cliente dado de baja",
        "entidad": "dbo.clientes",
        "sinonimos": ["baja"],
        "descripcion": "clientes con estado = 3",
    }
    assert regla["datos"] == {
        "descripcion": "Los pedidos facturados no se modifican",
        "acciones": ["UPDATE", "DELETE"],
        "efecto": "bloquear",
        "tablas": ["dbo.pedidos"],
    }
    assert (await servicios.listar(TENANT, conexion.id, "Ventas", otro))["documentos"][0][
        "puede_gestionar"
    ] is False
    para_ia = await servicios.texto_para_ia(TENANT, conexion)
    assert "## facturacion.pdf (base Ventas)\n- Facturación" in para_ia

    # Quien lo cargó corrige el resumen; otro usuario de Consulta, no.
    corregido = await servicios.guardar_resumen(TENANT, autor, cargado["id"], " Corregido ")
    assert corregido["resumen"] == "Corregido" and corregido["editado"]
    for intento in (
        servicios.guardar_resumen(TENANT, otro, cargado["id"], "x"),
        servicios.regenerar_resumen(TENANT, otro, cargado["id"]),
        servicios.descartar_propuesta(TENANT, otro, cargado["id"], regla["id"]),
        servicios.eliminar(TENANT, otro, "documento", cargado["id"]),
        # Aceptar crea un término o una regla: es de Administrador y DBA.
        servicios.aceptar_propuesta(TENANT, autor, cargado["id"], termino["id"]),
    ):
        with pytest.raises(ErrorDominio) as error:
            await intento
        assert error.value.codigo == "USR_PER_000"

    aceptado = await servicios.aceptar_propuesta(TENANT, dba, cargado["id"], termino["id"])
    resuelta = aceptado["propuestas"][0]
    assert resuelta["estado"] == "aceptada" and resuelta["resuelta_por"] == dba.email
    [glosario] = (await servicios.listar(TENANT, conexion.id, "Ventas"))["terminos"]
    assert glosario["id"] == resuelta["elemento_id"] and glosario["entidad"] == "dbo.clientes"
    acciones = [accion for accion, _ in ctx["eventos"][-2:]]
    assert acciones == ["contexto.termino_creado", "contexto.documento_propuesta_aceptada"]
    with pytest.raises(ErrorDominio) as error:
        await servicios.aceptar_propuesta(TENANT, dba, cargado["id"], termino["id"])
    assert error.value.codigo == "CTX_DOC_003"

    descartado = await servicios.descartar_propuesta(TENANT, autor, cargado["id"], regla["id"])
    assert descartado["propuestas"][1]["estado"] == "descartada"
    assert ctx["eventos"][-1][0] == "contexto.documento_propuesta_descartada"
    assert (await servicios.listar(TENANT, conexion.id, "Ventas"))["reglas"] == []

    # Volver a interpretar: la IA recibe el glosario actual y no se vuelve a ofrecer lo que
    # ya se aceptó o descartó.
    regenerado = await servicios.regenerar_resumen(TENANT, autor, cargado["id"])
    assert regenerado["estado"] == "procesando"
    await _esperar()
    assert "# Glosario actual\n- «Cliente dado de baja» → dbo.clientes" in interpretaciones[-1][0]
    [visto] = (await servicios.listar(TENANT, conexion.id, "Ventas"))["documentos"]
    assert visto["editado"] is False and visto["resumen"].startswith("- Facturación")
    assert [p["estado"] for p in visto["propuestas"]] == ["aceptada", "descartada"]

    nombre, contenido = await servicios.descargar_documento(TENANT, cargado["id"])
    assert nombre == "facturacion.pdf" and contenido == FACTURACION

    await servicios.eliminar(TENANT, autor, "documento", cargado["id"])
    assert contexto_falso.archivos_de(ctx["almacen"]) == {}
    assert ctx["eventos"][-1][0] == "contexto.documento_eliminado"
    assert "Documentos de negocio" not in await servicios.texto_para_ia(TENANT, conexion)


async def test_propuesta_que_no_se_puede_crear_queda_pendiente(ctx, interpretaciones):  # noqa: F811
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    dba = _actor("DBA")
    datos = Documento(conexion.id, "Ventas", "a.pdf")
    cargado = await servicios.cargar_documento(TENANT, dba, datos, FACTURACION)
    await _esperar()
    [termino, regla] = (await servicios._documento(TENANT, cargado["id"]))["propuestas"]
    # Mientras tanto alguien cargó el mismo término a mano.
    await servicios.guardar_termino(
        TENANT, dba, None, Termino(conexion.id, "Ventas", "Cliente dado de baja", "dbo.clientes")
    )
    with pytest.raises(ErrorDominio) as error:
        await servicios.aceptar_propuesta(TENANT, dba, cargado["id"], termino["id"])
    assert error.value.codigo == "CTX_VAL_004"
    actual = await servicios._documento(TENANT, cargado["id"])
    assert actual["propuestas"][0]["estado"] == "pendiente"

    # La regla se crea con el alcance del documento (esta conexión y esta base).
    await servicios.aceptar_propuesta(TENANT, dba, cargado["id"], regla["id"])
    [creada] = (await servicios.listar(TENANT, conexion.id, "Ventas"))["reglas"]
    assert (creada["conexion_id"], creada["base"], creada["prioridad"]) == (
        conexion.id,
        "Ventas",
        2,
    )
    assert creada["efecto"] == "bloquear" and creada["tablas"] == ["dbo.pedidos"]
    with pytest.raises(ErrorDominio) as error:
        await servicios.aceptar_propuesta(TENANT, dba, cargado["id"], "no-existe")
    assert error.value.codigo == "CTX_VAL_000"


async def test_fallas_y_validaciones(ctx, interpretaciones):  # noqa: F811
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    dba = _actor("DBA")
    datos = Documento(conexion.id, "Ventas", "falla.pdf")
    cargado = await servicios.cargar_documento(TENANT, dba, datos, FACTURACION)
    await _esperar()
    # Si la IA falla, queda sin interpretar y no suma nada al contexto.
    [visto] = (await servicios.listar(TENANT, conexion.id, "Ventas"))["documentos"]
    assert visto["estado"] == "error" and visto["error"] == "IA_API_000"
    assert "Documentos de negocio" not in await servicios.texto_para_ia(TENANT, conexion)

    # Una interpretación que quedó "procesando" (por ejemplo, por un reinicio) se ve interrumpida.
    viejo = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    ctx["almacen"][cargado["id"]].update(estado="procesando", actualizado_en=viejo)
    [visto] = (await servicios.listar(TENANT, conexion.id, "Ventas"))["documentos"]
    assert visto["estado"] == "error" and visto["error"] == "interrumpido"

    # Un documento sin Markdown guardado se vuelve a convertir al reinterpretarlo.
    ctx["almacen"][cargado["id"]].pop("markdown_id")
    await servicios.regenerar_resumen(TENANT, dba, cargado["id"])
    await _esperar()
    markdown_id = ctx["almacen"][cargado["id"]]["markdown_id"]
    assert b"estado 3" in contexto_falso.archivos_de(ctx["almacen"])[markdown_id][1]

    casos = (
        (Documento(conexion.id, "Inexistente", "a.pdf"), FACTURACION, "USR_VAL_002"),
        (Documento(conexion.id, " ", "a.pdf"), FACTURACION, "USR_VAL_002"),
        (Documento(conexion.id, "Ventas", "a.pdf"), b"x" * (10 * 1024 * 1024 + 1), "CTX_DOC_002"),
        (Documento(conexion.id, "Ventas", "a.pdf"), b"hola", "CTX_DOC_000"),
    )
    for datos, contenido, codigo in casos:
        with pytest.raises(ErrorDominio) as error:
            await servicios.cargar_documento(TENANT, dba, datos, contenido)
        assert error.value.codigo == codigo
    with pytest.raises(ErrorDominio) as error:
        await servicios.descargar_documento(TENANT, "no-existe")
    assert error.value.codigo == "CTX_VAL_000"


async def test_api_documentos(ctx, interpretaciones):  # noqa: F811
    conexion = await conexiones.crear(TENANT, _actor(), _datos())
    dba = await _navegador(ctx, "DBA")
    consulta = await _navegador(ctx, "CONSULTA")
    formulario = {"conexion_id": conexion.id, "base": "Ventas"}
    archivo = {"archivo": ("Facturación.pdf", FACTURACION, "application/pdf")}

    # Consulta también carga documentos.
    r = consulta.post("/api/contexto/documentos", data=formulario, files=archivo, headers=CABECERA)
    assert r.status_code == 201 and r.json()["nombre"] == "Facturación.pdf"
    documento_id = r.json()["id"]
    await _esperar()
    # Sin la cabecera anti CSRF no se acepta.
    assert dba.post("/api/contexto/documentos", data=formulario, files=archivo).status_code == 403
    grande = {"archivo": ("g.pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024), "application/pdf")}
    r = dba.post("/api/contexto/documentos", data=formulario, files=grande, headers=CABECERA)
    assert r.status_code == 413 and r.json()["codigo"] == "CTX_DOC_002"

    [visto] = consulta.get(f"/api/contexto/conexiones/{conexion.id}?base=Ventas").json()[
        "documentos"
    ]
    assert visto["estado"] == "listo" and visto["puede_gestionar"] is True
    termino, regla = (p["id"] for p in visto["propuestas"])
    pdf = consulta.get(f"/api/contexto/documentos/{documento_id}/archivo")
    assert pdf.status_code == 200 and pdf.content == FACTURACION
    assert pdf.headers["content-type"] == "application/pdf"
    assert "Facturaci%C3%B3n.pdf" in pdf.headers["content-disposition"]

    r = consulta.put(
        f"/api/contexto/documentos/{documento_id}/resumen",
        json={"resumen": "Corregido"},
        headers=CABECERA,
    )
    assert r.status_code == 200 and r.json()["editado"] is True
    ruta = f"/api/contexto/documentos/{documento_id}/propuestas"
    assert consulta.post(f"{ruta}/{termino}/aceptar", headers=CABECERA).status_code == 403
    r = dba.post(f"{ruta}/{termino}/aceptar", headers=CABECERA)
    assert r.status_code == 200 and r.json()["propuestas"][0]["estado"] == "aceptada"
    r = consulta.post(f"{ruta}/{regla}/descartar", headers=CABECERA)
    assert r.status_code == 200 and r.json()["propuestas"][1]["estado"] == "descartada"
    assert dba.post(f"{ruta}/{regla}/aceptar", headers=CABECERA).status_code == 409
    assert dba.post(f"{ruta}/{regla}/otra", headers=CABECERA).status_code == 422

    r = consulta.post(f"/api/contexto/documentos/{documento_id}/resumen", headers=CABECERA)
    assert r.status_code == 200 and r.json()["estado"] == "procesando"
    await _esperar()
    eliminado = consulta.delete(f"/api/contexto/documentos/{documento_id}", headers=CABECERA)
    assert eliminado.status_code == 204
    assert dba.get(f"/api/contexto/documentos/{documento_id}/archivo").status_code == 404
