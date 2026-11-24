"""Permisos por ambiente (CU-001-002-C): verificación en el servidor."""

from app.core.errores import ErrorDominio
from app.identidad.entidades import AMBIENTES, Sesion, techo


def exigir(sesion: Sesion, accion: str, ambiente: str | None) -> None:
    """Rechaza la acción si el rol no la permite (USR_PER_000) o si el usuario no tiene el
    ambiente habilitado o el Administrador se la quitó en ese ambiente (USR_PER_006)."""
    if accion not in techo(sesion.rol, ambiente):
        raise ErrorDominio("USR_PER_000", f"{accion} en {ambiente}")
    if not sesion.puede(accion, ambiente):
        raise ErrorDominio("USR_PER_006", f"{accion} en {ambiente}")


def exigir_en(sesion: Sesion, accion: str, ambiente: str | None) -> None:
    """Como ``exigir``; sin ambiente (algo que alcanza a todas las conexiones), en todos."""
    for un_ambiente in AMBIENTES if ambiente is None else (ambiente,):
        exigir(sesion, accion, un_ambiente)
