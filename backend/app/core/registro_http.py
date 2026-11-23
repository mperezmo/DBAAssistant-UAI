"""Registro de las respuestas HTTP con error en la bitácora de logs de la aplicación.

Cada respuesta 4xx queda como WARNING y cada 5xx como ERROR (con la traza si fue una
excepción no controlada). Así entran en la bitácora según el nivel que eligió el
Administrador, igual que los logs del código.

Los datos que se conocen dentro de la petición (usuario, código de error de negocio) los
deja el endpoint en ``request.state``: las variables de contexto que se fijan ahí no
llegan hasta el middleware, que corre en otra tarea.
"""

import logging
import time

from starlette.requests import Request

log = logging.getLogger("app.http")


def registrar(
    request: Request, estado: int, inicio: float, error: BaseException | None = None
) -> None:
    codigo = getattr(request.state, "codigo_error", None)
    detalle = getattr(request.state, "detalle_error", None)
    mensaje = f"{request.method} {request.url.path} {estado}"
    if codigo:
        mensaje += f" {codigo}" + (f": {detalle}" if detalle else "")
    log.log(
        logging.ERROR if estado >= 500 else logging.WARNING,
        mensaje,
        exc_info=error,
        extra={
            "http": {
                "metodo": request.method,
                "estado": estado,
                "duracion_ms": round((time.perf_counter() - inicio) * 1000),
                "usuario": getattr(request.state, "usuario", None),
            }
        },
    )
