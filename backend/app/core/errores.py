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
    "CON_DB_003": DefinicionError("Se perdió la conexión con la instancia.", 503),
    "MNT_DAT_000": DefinicionError("Error obteniendo métricas.", 502),
    "MNT_CFG_000": DefinicionError("El umbral critical debe ser mayor que el warning.", 422),
    "ALR_VAL_000": DefinicionError("Alerta o regla no encontrada.", 404),
    "ALR_VAL_001": DefinicionError("Ya existe una alerta con ese nombre.", 409),
    "ALR_VAL_002": DefinicionError("La alerta ya fue atendida.", 409),
    "NTF_EML_000": DefinicionError("Error enviando el correo.", 502),
    "NTF_PSH_000": DefinicionError("Error enviando notificación push.", 502),
    "SQL_VAL_000": DefinicionError("Sintaxis T-SQL inválida.", 422),
    "WKR_VAL_000": DefinicionError("Workaround no encontrado.", 404),
    "WKR_VAL_001": DefinicionError("Ya existe un workaround con ese nombre.", 409),
    "WKR_EJE_000": DefinicionError("Falla en alguno de los pasos del workaround.", 500),
    "WKR_EJE_001": DefinicionError(
        "La situación cambió desde el diagnóstico; revisalo y volvé a confirmar.", 409
    ),
    "WKR_EJE_002": DefinicionError("No hay nada para ejecutar.", 422),
    "WKR_SBX_000": DefinicionError("Falla en la validación en Sandbox.", 422),
    "WKR_SBX_001": DefinicionError(
        "El workaround no está validado en Sandbox; no se puede ejecutar en PROD.", 403
    ),
    "WKR_AMB_000": DefinicionError("El workaround no aplica a ese ambiente.", 422),
    "WKR_VEN_000": DefinicionError(
        "Fuera de la ventana de mantenimiento: requiere la autorización de otro usuario.", 403
    ),
    "WKR_AUT_000": DefinicionError("Autorización o automatización no encontrada.", 404),
    "WKR_AUT_001": DefinicionError("No se puede autorizar un pedido propio.", 403),
    "WKR_AUT_002": DefinicionError(
        "Solo los workarounds integrados aptos pueden ejecutarse automáticamente.", 422
    ),
    "SBX_DCK_000": DefinicionError("Error en el contenedor Sandbox.", 502),
    "SBX_SNP_000": DefinicionError(
        "La estructura del Sandbox tiene más de 24 horas o no coincide con el origen.", 200
    ),
    "SBX_SNP_001": DefinicionError(
        "El Sandbox todavía no tiene esa base: sincronizalo y volvé a intentar.", 409
    ),
    "SBX_TMO_000": DefinicionError("Tiempo de ejecución excedido (60 s).", 408),
    "SBX_PRM_000": DefinicionError(
        "Solo se puede promover una ejecución exitosa que no haya sido promovida.", 409
    ),
    "SQL_EXE_000": DefinicionError("Falla en la ejecución sobre la instancia.", 422),
    "SQL_VAL_001": DefinicionError(
        "Este tipo de sentencia no se ejecuta directo: validala en el Sandbox y promovela.", 422
    ),
    "SQL_VAL_002": DefinicionError("UPDATE, DELETE e INSERT…SELECT requieren un WHERE.", 422),
    "SQL_LCK_000": DefinicionError(
        "Hay transacciones abiertas o bloqueos sobre las tablas involucradas; reintentá más tarde.",
        409,
    ),
    "IA_API_000": DefinicionError("Error de comunicación con el motor IA.", 502),
    "IA_TRA_000": DefinicionError("No se pudo traducir la consulta.", 422),
    "CHT_VAL_000": DefinicionError("Conversación no encontrada.", 404),
    "CTX_VAL_000": DefinicionError("Elemento del contexto de negocio no encontrado.", 404),
    "CTX_VAL_001": DefinicionError(
        "La operación involucra una tabla sensible: requiere confirmar con el segundo factor.", 403
    ),
    "CTX_VAL_002": DefinicionError("Conflicto con una regla existente.", 409),
    "CTX_VAL_003": DefinicionError("Entidad técnica no encontrada en el esquema.", 422),
    "CTX_VAL_004": DefinicionError("Término ya definido.", 409),
    "CTX_REG_000": DefinicionError("Una regla de negocio bloquea la operación.", 403),
    "CTX_REG_001": DefinicionError(
        "La operación incumple reglas de negocio de advertencia: confirmá para continuar.", 409
    ),
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

    def __init__(self, codigo: str, detalle: str | None = None, datos: dict | None = None):
        if codigo not in CATALOGO:
            raise ValueError(f"Código de error no catalogado: {codigo}")
        self.codigo = codigo
        self.definicion = CATALOGO[codigo]
        self.detalle = detalle
        # Datos que sí se muestran al usuario (el detalle queda solo en el log).
        self.datos = datos
        super().__init__(f"[{codigo}] {self.definicion.mensaje} {detalle or ''}".strip())
