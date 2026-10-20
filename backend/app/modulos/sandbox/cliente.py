"""Cliente del servicio Sandbox (red interna, token compartido)."""

import httpx2

from app.core.config import obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.secretos import proveedor_secretos

# La sincronización de una base grande puede llevar varios minutos.
TIMEOUT_SINCRONIZACION = 1800
TIMEOUT_EJECUCION = 120


def _http(timeout: float) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=obtener_configuracion().sandbox_url,
        headers={"X-Sandbox-Token": proveedor_secretos().obtener("sandbox_token")},
        timeout=timeout,
    )


async def _pedir(metodo: str, ruta: str, timeout: float, cuerpo: dict | None = None) -> dict:
    try:
        async with _http(timeout) as http:
            respuesta = await http.request(metodo, ruta, json=cuerpo)
            respuesta.raise_for_status()
            return respuesta.json() if respuesta.content else {}
    except httpx2.HTTPError as error:
        raise ErrorDominio("SBX_DCK_000", str(error)) from error


async def estado(clave: str) -> dict:
    return await _pedir("GET", f"/sandboxes/{clave}", 10)


async def sincronizar(clave: str, origen: dict, bases: list[str]) -> list[dict]:
    datos = await _pedir(
        "POST",
        f"/sandboxes/{clave}/sincronizar",
        TIMEOUT_SINCRONIZACION,
        {"origen": origen, "bases": bases},
    )
    return datos["bases"]


async def alineadas(clave: str, origen: dict, bases: list[str]) -> dict[str, bool]:
    datos = await _pedir(
        "POST", f"/sandboxes/{clave}/huellas", 60, {"origen": origen, "bases": bases}
    )
    return datos["alineadas"]


async def ejecutar(clave: str, base: str, lotes: list[str], solo_sintaxis: bool) -> dict:
    return await _pedir(
        "POST",
        f"/sandboxes/{clave}/ejecutar",
        TIMEOUT_EJECUCION,
        {"base": base, "lotes": lotes, "timeout_segundos": 60, "solo_sintaxis": solo_sintaxis},
    )


async def eliminar(clave: str) -> None:
    await _pedir("DELETE", f"/sandboxes/{clave}", 60)
