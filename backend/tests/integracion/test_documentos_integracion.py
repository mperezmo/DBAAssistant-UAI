"""Documentos de negocio contra MongoDB real: archivos en GridFS, metadatos y propuestas en
el contexto."""

import pytest

from app.datos import mongo
from app.modulos.contexto_negocio import repositorios

pytestmark = pytest.mark.integracion


async def test_archivos_y_metadatos_en_mongodb(prefijo_aislado):
    base = f"{prefijo_aislado.mongodb_prefijo_base}_t_documentos"
    await mongo.preparar_base_tenant(base)
    try:
        archivo_id = await repositorios.guardar_archivo(
            base, "t1", "a.pdf", b"%PDF-contenido", "application/pdf"
        )
        assert await repositorios.leer_archivo(base, "t1", archivo_id) == b"%PDF-contenido"
        # Otro tenant u otro id no lo leen.
        assert await repositorios.leer_archivo(base, "t2", archivo_id) is None
        assert await repositorios.leer_archivo(base, "t1", "0" * 24) is None
        assert await repositorios.leer_archivo(base, "t1", "no-es-id") is None

        documento_id = await repositorios.guardar(
            base, "t1", None, {"tipo": "documento", "estado": "procesando", "resumen": ""}
        )
        await repositorios.actualizar(
            base, "t1", "documento", documento_id, {"estado": "listo", "resumen": "ok"}
        )
        guardado = await repositorios.obtener(base, "t1", "documento", documento_id)
        assert guardado["estado"] == "listo" and guardado["resumen"] == "ok"
        # Una propuesta se resuelve una sola vez, aunque dos usuarios la acepten a la vez.
        propuestas = [
            {"id": "p1", "tipo": "termino", "estado": "pendiente", "datos": {}},
            {"id": "p2", "tipo": "regla", "estado": "pendiente", "datos": {}},
        ]
        await repositorios.actualizar(
            base, "t1", "documento", documento_id, {"propuestas": propuestas}
        )
        resolver = repositorios.resolver_propuesta
        cambio = {"estado": "aceptada"}
        assert await resolver(base, "t1", documento_id, "p1", "pendiente", cambio)
        assert not await resolver(base, "t1", documento_id, "p1", "pendiente", cambio)
        assert not await resolver(base, "t2", documento_id, "p2", "pendiente", cambio)
        assert not await resolver(base, "t1", "no-es-id", "p2", "pendiente", cambio)
        assert await resolver(base, "t1", documento_id, "p1", "aceptada", {"elemento_id": "x"})
        guardado = await repositorios.obtener(base, "t1", "documento", documento_id)
        assert guardado["propuestas"][0] == {
            **propuestas[0],
            "estado": "aceptada",
            "elemento_id": "x",
        }
        assert guardado["propuestas"][1]["estado"] == "pendiente"
        # Dos documentos sin clave no chocan con el índice único del contexto.
        await repositorios.guardar(base, "t1", None, {"tipo": "documento", "estado": "listo"})

        await repositorios.eliminar_archivo(base, archivo_id)
        await repositorios.eliminar_archivo(base, archivo_id)  # ya no está: no falla
        assert await repositorios.leer_archivo(base, "t1", archivo_id) is None
    finally:
        await mongo.cliente().drop_database(base)
