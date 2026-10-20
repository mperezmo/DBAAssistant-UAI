"""Catálogo de errores de dominio.

Los códigos siguen la nomenclatura del documento de casos de uso del TFI
(``AREA_TIPO_NNN``). Cada error define el mensaje para el usuario y el
código HTTP con el que se informa desde la API.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DefinicionError:
    mensaje: str
    estado_http: int


CATALOGO: dict[str, DefinicionError] = {
    "USR_VAL_000": DefinicionError("Usuario no encontrado.", 404),
    "USR_VAL_001": DefinicionError("El email ya existe.", 409),
    "USR_VAL_002": DefinicionError("Datos requeridos no completados.", 422),
    "USR_VAL_005": DefinicionError("El usuario está deshabilitado.", 403),
    "USR_VAL_006": DefinicionError(
        "El usuario fue dado de baja recientemente; se puede rehabilitar.", 409
    ),
    "USR_AUT_000": DefinicionError("Credenciales inválidas.", 401),
    "USR_AUT_001": DefinicionError(
        "El segundo factor de autenticación es inválido o no se completó.", 401
    ),
    "USR_AUT_005": DefinicionError("La sesión expiró o no existe.", 401),
    "USR_AUT_006": DefinicionError("Solicitud no permitida.", 403),
    "USR_AUT_003": DefinicionError("El email todavía no fue verificado.", 401),
    "USR_AUT_004": DefinicionError("La solicitud de ingreso expiró o no es válida.", 400),
    "USR_PER_000": DefinicionError("El rol no tiene permisos para esta acción.", 403),
    "USR_PER_002": DefinicionError("Rol no encontrado.", 404),
    "USR_PER_003": DefinicionError("Los permisos exceden los del rol base.", 422),
    "USR_PER_004": DefinicionError(
        "Un Administrador no puede quitarse su propio rol ni darse de baja.", 422
    ),
    "USR_PER_005": DefinicionError("Debe quedar al menos un Administrador activo.", 422),
    "AUT_API_000": DefinicionError("Falla en el servicio de identidad.", 502),
    "CON_DB_000": DefinicionError("No se logró conectar a la instancia.", 422),
    "CON_DB_001": DefinicionError("Ya existe una conexión con ese alias.", 409),
    "CON_DB_002": DefinicionError("Conexión no encontrada.", 404),
    "WRM_000": DefinicionError("No se logró conectar al servidor Windows (WinRM).", 422),
    "AKV_000": DefinicionError("Falla en el almacenamiento de credenciales.", 500),
    "AMB_VAL_000": DefinicionError(
        "La operación sobre PROD requiere confirmar con el segundo factor.", 403
    ),
    "TEN_VAL_000": DefinicionError("Tenant no encontrado.", 404),
    "TEN_VAL_001": DefinicionError("El identificador del tenant ya existe.", 409),
    "TEN_VAL_002": DefinicionError("El identificador del tenant no es válido.", 422),
    "TEN_VAL_003": DefinicionError("La empresa no está habilitada.", 403),
    "TEN_APR_000": DefinicionError("Falló el aprovisionamiento del tenant.", 500),
}


class ErrorDominio(Exception):
    """Error de negocio identificado por un código del catálogo."""

    def __init__(self, codigo: str, detalle: str | None = None):
        if codigo not in CATALOGO:
            raise ValueError(f"Código de error no catalogado: {codigo}")
        self.codigo = codigo
        self.definicion = CATALOGO[codigo]
        self.detalle = detalle
        super().__init__(f"[{codigo}] {self.definicion.mensaje} {detalle or ''}".strip())
