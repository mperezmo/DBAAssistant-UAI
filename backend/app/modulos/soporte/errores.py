"""Errores de la aplicación sin datos de la empresa, para la sesión de soporte del operador.

De la bitácora de logs solo se toman los errores de la aplicación (nivel ERROR o
CRITICAL: excepciones del código y respuestas HTTP 5xx) y de cada uno se muestra lo que
describe al código, nunca a la empresa:

- la ruta como plantilla de la API (``/api/schema/{conexion_id}/{base}``), sin los valores
  (la guarda el registro HTTP; los errores anteriores a ese cambio no la tienen);
- el código de error y su mensaje genérico del catálogo;
- el tipo de excepción, sin su mensaje (puede llevar datos: servidores, consultas, emails);
- los marcos de la traza dentro de ``app/`` (archivo, línea y función), sin las líneas.

Quedan fuera el usuario, la IP, el mensaje del log y cualquier otro campo.
"""

import re
from datetime import UTC, datetime, timedelta

from app.core.excepciones import COLECCION
from app.datos import mongo
from app.tenants.entidades import Tenant
from app.web.i18n import traducir

NIVELES = ("ERROR", "CRITICAL")
DIAS = 7
CANTIDAD = 100
RUTA_DESCONOCIDA = "—"
MARCO = re.compile(r'File "(?P<archivo>[^"]+)", line (?P<linea>\d+), in (?P<funcion>[\w<>.]+)')
TIPO_EXCEPCION = re.compile(r"^(?P<tipo>[A-Za-z_][\w.]*)(?::|$)")


def marcos(traza: str | None) -> list[str]:
    """Marcos de la traza dentro del código de la aplicación, del más externo al interno."""
    resultado = []
    for m in MARCO.finditer(traza or ""):
        archivo = m["archivo"].replace("\\", "/")
        if "/app/" not in archivo:
            continue
        relativo = "app/" + archivo.rsplit("/app/", 1)[1]
        resultado.append(f"{relativo}:{m['linea']} · {m['funcion']}")
    return resultado


def tipo_de_excepcion(traza: str | None) -> str | None:
    """Clase de la excepción final, sin su mensaje.

    En la traza de Python, la línea de la excepción es la primera sin sangría después del
    último marco (``  File ...``); se toma solo el nombre de la clase de esa línea.
    """
    lineas = (traza or "").splitlines()
    ultimo_marco = max((i for i, x in enumerate(lineas) if MARCO.search(x)), default=None)
    if ultimo_marco is None:
        return None
    for linea in lineas[ultimo_marco + 1 :]:
        if linea and not linea.startswith((" ", "\t")):
            coincidencia = TIPO_EXCEPCION.match(linea)
            return coincidencia["tipo"] if coincidencia else None
    return None


def _fecha(valor: datetime | None) -> str | None:
    if valor is None:
        return None
    # MongoDB devuelve la hora UTC sin zona.
    return valor.replace(tzinfo=None).isoformat() + "Z"


def sanear(documento: dict) -> dict:
    http = documento.get("http") or {}
    codigo = documento.get("codigo")
    mensaje = traducir(f"error.{codigo}") if codigo else None
    return {
        "ts": _fecha(documento.get("ts")),
        "nivel": documento.get("nivel"),
        "modulo": documento.get("modulo"),
        "origen": documento.get("origen"),
        "metodo": http.get("metodo"),
        "estado": http.get("estado"),
        "ruta": http.get("plantilla") or RUTA_DESCONOCIDA,
        "codigo": codigo,
        "mensaje": mensaje if mensaje and mensaje != f"error.{codigo}" else None,
        "excepcion": tipo_de_excepcion(documento.get("traza")),
        "marcos": marcos(documento.get("traza")),
    }


async def listar(tenant: Tenant, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(UTC)
    desde = ahora - timedelta(days=DIAS)
    filtro = {"nivel": {"$in": list(NIVELES)}, "ts": {"$gte": desde}}
    # Se leen solo los campos que se muestran (saneados); el resto no sale de la base.
    campos = {"_id": 0, "ts": 1, "nivel": 1, "modulo": 1, "origen": 1, "codigo": 1}
    campos |= {"http.metodo": 1, "http.estado": 1, "http.plantilla": 1, "traza": 1}
    coleccion = mongo.base(tenant.base_mongodb)[COLECCION]
    cursor = coleccion.find(filtro, campos).sort("ts", -1).limit(CANTIDAD)
    errores = [sanear(d) async for d in cursor]
    return {"errores": errores, "dias": DIAS, "maximo": CANTIDAD}
