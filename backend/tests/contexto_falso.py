"""Contexto de negocio en memoria (reemplaza la colección de MongoDB en las pruebas)."""

import itertools

from app.modulos.contexto_negocio import repositorios


def _coincide(documento: dict, filtro: dict) -> bool:
    for campo, condicion in filtro.items():
        valor = documento.get(campo)
        if isinstance(condicion, dict) and "$in" in condicion:
            if valor not in condicion["$in"]:
                return False
        elif valor != condicion:
            return False
    return True


def instalar(monkeypatch) -> dict:
    """Reemplaza el repositorio y devuelve el almacén (id -> documento)."""
    almacen: dict[str, dict] = {}
    secuencia = itertools.count(1)

    def salida(id_, d):
        return {
            **{k: v for k, v in d.items() if k not in ("tenant_id", "clave")},
            "id": id_,
        }

    async def listar(base, tenant_id, filtro):
        filas = [
            salida(i, d)
            for i, d in almacen.items()
            if d["tenant_id"] == tenant_id and _coincide(d, filtro)
        ]
        return sorted(filas, key=lambda d: (d.get("prioridad", 0), d["id"]))

    async def obtener(base, tenant_id, tipo, elemento_id):
        d = almacen.get(elemento_id)
        return salida(elemento_id, d) if d and d["tipo"] == tipo else None

    async def guardar(base, tenant_id, elemento_id, documento):
        clave = documento.get("clave")
        for i, d in almacen.items():
            if (
                clave is not None
                and i != elemento_id
                and (d["tipo"], d.get("conexion_id"), d.get("base"), d.get("clave"))
                == (documento["tipo"], documento.get("conexion_id"), documento.get("base"), clave)
            ):
                raise repositorios.Duplicado
        nuevo = elemento_id or f"{next(secuencia):024x}"
        almacen[nuevo] = {**documento, "tenant_id": tenant_id}
        return nuevo

    async def eliminar(base, tenant_id, tipo, elemento_id):
        return almacen.pop(elemento_id, None) is not None

    for nombre, funcion in (
        ("listar", listar),
        ("obtener", obtener),
        ("guardar", guardar),
        ("eliminar", eliminar),
    ):
        monkeypatch.setattr(repositorios, nombre, funcion)
    return almacen
