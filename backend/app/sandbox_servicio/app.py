"""API interna del servicio Sandbox. Solo la usa el backend (token compartido)."""

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.core.registro import configurar_registro
from app.core.secretos import proveedor_secretos
from app.sandbox_servicio import contenedores, ejecucion, estructura

TIMEOUT_MAXIMO_SEGUNDOS = 600


def autenticar(x_sandbox_token: str = Header(default="")) -> None:
    esperado = proveedor_secretos().obtener("sandbox_token")
    if not hmac.compare_digest(x_sandbox_token, esperado):
        raise HTTPException(status_code=401, detail="Token inválido")


class Origen(BaseModel):
    servidor: str = Field(max_length=300)
    usuario: str = Field(max_length=128)
    password: str = Field(max_length=256)


class SincronizacionEntrada(BaseModel):
    origen: Origen
    bases: list[str] = Field(max_length=200)


class EjecucionEntrada(BaseModel):
    base: str = Field(max_length=128)
    lotes: list[str] = Field(min_length=1, max_length=50)
    timeout_segundos: int = Field(default=60, ge=1, le=TIMEOUT_MAXIMO_SEGUNDOS)
    solo_sintaxis: bool = False


def _clave(clave: str) -> str:
    try:
        return contenedores.validar_clave(clave)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _origen(o: Origen) -> contenedores.Destino:
    return contenedores.Destino(o.servidor, o.usuario, o.password)


def crear_app() -> FastAPI:
    configurar_registro("INFO")
    app = FastAPI(title="DBA Assistant · Sandbox", dependencies=[Depends(autenticar)])

    @app.get("/sandboxes/{clave}")
    async def estado(clave: str) -> dict:
        return await run_in_threadpool(contenedores.estado, _clave(clave))

    @app.post("/sandboxes/{clave}")
    async def asegurar(clave: str) -> dict:
        await run_in_threadpool(contenedores.asegurar, _clave(clave))
        return await run_in_threadpool(contenedores.estado, clave)

    @app.delete("/sandboxes/{clave}", status_code=204)
    async def eliminar(clave: str) -> None:
        await run_in_threadpool(contenedores.eliminar, _clave(clave))

    @app.post("/sandboxes/{clave}/sincronizar")
    async def sincronizar(clave: str, datos: SincronizacionEntrada) -> dict:
        destino = await run_in_threadpool(contenedores.asegurar, _clave(clave))
        resultados = []
        for base in datos.bases:
            resultados.append(
                await run_in_threadpool(
                    estructura.sincronizar_base, _origen(datos.origen), base, destino
                )
            )
        return {"bases": resultados}

    @app.post("/sandboxes/{clave}/huellas")
    async def huellas(clave: str, datos: SincronizacionEntrada) -> dict:
        """Compara la estructura del Sandbox con la del origen, base por base."""
        destino = await run_in_threadpool(contenedores.asegurar, _clave(clave))
        resultado = {}
        for base in datos.bases:
            try:
                origen = await run_in_threadpool(
                    estructura.huella, contenedores.cadena_conexion(_origen(datos.origen), base)
                )
                sandbox = await run_in_threadpool(
                    estructura.huella, contenedores.cadena_conexion(destino, base)
                )
                resultado[base] = origen == sandbox
            except Exception:
                resultado[base] = False
        return {"alineadas": resultado}

    @app.post("/sandboxes/{clave}/ejecutar")
    async def ejecutar(clave: str, datos: EjecucionEntrada) -> dict:
        destino = await run_in_threadpool(contenedores.asegurar, _clave(clave))
        return await run_in_threadpool(
            ejecucion.ejecutar,
            destino,
            datos.base,
            datos.lotes,
            datos.timeout_segundos,
            datos.solo_sintaxis,
        )

    return app


app = crear_app()
