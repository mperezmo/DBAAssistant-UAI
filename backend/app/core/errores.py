"""Catálogo de errores de dominio.

Los códigos siguen la nomenclatura del documento de casos de uso del TFI
(``AREA_TIPO_NNN``). Cada error define el código HTTP con el que se informa desde la
API; el mensaje para el usuario está en las traducciones, con la clave ``error.<codigo>``.
"""

from dataclasses import dataclass

from app.web.i18n import traducir


@dataclass(frozen=True)
class DefinicionError:
    codigo: str
    estado_http: int

    @property
    def mensaje(self) -> str:
        """Mensaje para el usuario en el idioma de la petición (``error.<codigo>``)."""
        return traducir(f"error.{self.codigo}")


# Código → estado HTTP. Los mensajes están en las traducciones (app/web/i18n).
ESTADOS_HTTP: dict[str, int] = {
    "USR_VAL_000": 404,
    "USR_VAL_001": 409,
    "USR_VAL_002": 422,
    "USR_VAL_005": 403,
    "USR_VAL_006": 409,
    "USR_AUT_000": 401,
    "USR_AUT_001": 401,
    "USR_AUT_005": 401,
    "USR_AUT_006": 403,
    "USR_AUT_003": 401,
    "USR_AUT_004": 400,
    "USR_PER_000": 403,
    "USR_PER_002": 404,
    "USR_PER_003": 422,
    "USR_PER_004": 422,
    "USR_PER_005": 422,
    "AUT_API_000": 502,
    "CON_DB_000": 422,
    "CON_DB_001": 409,
    "CON_DB_002": 404,
    "CON_DB_003": 503,
    "MNT_DAT_000": 502,
    "MNT_CFG_000": 422,
    "ALR_VAL_000": 404,
    "ALR_VAL_001": 409,
    "ALR_VAL_002": 409,
    "NTF_EML_000": 502,
    "NTF_PSH_000": 502,
    "SQL_VAL_000": 422,
    "WKR_VAL_000": 404,
    "WKR_VAL_001": 409,
    "WKR_EJE_000": 500,
    "WKR_EJE_001": 409,
    "WKR_EJE_002": 422,
    "WKR_SBX_000": 422,
    "WKR_SBX_001": 403,
    "WKR_AMB_000": 422,
    "WKR_VEN_000": 403,
    "WKR_AUT_000": 404,
    "WKR_AUT_001": 403,
    "WKR_AUT_002": 422,
    "SBX_DCK_000": 502,
    "SBX_SNP_000": 200,
    "SBX_SNP_001": 409,
    "SBX_TMO_000": 408,
    "SBX_PRM_000": 409,
    "SQL_EXE_000": 422,
    "SQL_VAL_001": 422,
    "SQL_VAL_002": 422,
    "SQL_LCK_000": 409,
    "EXP_LIM_000": 422,
    "BPR_VAL_000": 404,
    "BPR_VAL_001": 422,
    "SCH_SNP_000": 404,
    "IA_API_000": 502,
    "IA_TRA_000": 422,
    "CHT_VAL_000": 404,
    "CTX_VAL_000": 404,
    "CTX_VAL_001": 403,
    "CTX_VAL_002": 409,
    "CTX_VAL_003": 422,
    "CTX_VAL_004": 409,
    "CTX_REG_000": 403,
    "CTX_REG_001": 409,
    "WRM_000": 422,
    "AKV_000": 500,
    "AMB_VAL_000": 403,
    "TEN_VAL_000": 404,
    "TEN_VAL_001": 409,
    "TEN_VAL_002": 422,
    "TEN_VAL_003": 403,
    "TEN_APR_000": 500,
}
CATALOGO: dict[str, DefinicionError] = {
    codigo: DefinicionError(codigo, estado) for codigo, estado in ESTADOS_HTTP.items()
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
