"""Confirmación con segundo factor para operaciones sobre PROD.

Antes de una acción sensible el usuario vuelve a autenticarse en Auth0 (que exige el
segundo factor). La confirmación habilita las acciones sobre PROD durante unos minutos.
"""

import time

from app.core.errores import ErrorDominio
from app.identidad.entidades import Sesion

VIGENCIA_SEGUNDOS = 5 * 60


def segundo_factor_vigente(sesion: Sesion) -> bool:
    return (
        sesion.segundo_factor_en is not None
        and time.time() - sesion.segundo_factor_en <= VIGENCIA_SEGUNDOS
    )


def exigir_segundo_factor_reciente(sesion: Sesion) -> None:
    if not segundo_factor_vigente(sesion):
        raise ErrorDominio("AMB_VAL_000")


def ruta_de_retorno_valida(volver: str | None) -> str:
    """Solo se aceptan rutas internas, para no redirigir a sitios externos."""
    if volver and volver.startswith("/") and not volver.startswith("//"):
        return volver
    return "/"
