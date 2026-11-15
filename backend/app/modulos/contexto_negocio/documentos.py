"""Documentos de negocio: lectura del PDF y resumen con el motor IA.

El documento entero no viaja en cada consulta: al cargarlo, la IA arma un resumen
orientado a los datos (procesos, entidades y las tablas del esquema que les corresponden,
reglas y requisitos no funcionales). Administrador y DBA lo revisan y corrigen; ese
resumen es lo que reciben el Chat IA y Buenas Prácticas como parte del contexto.
"""

import io
import logging

from pydantic import BaseModel, Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.errores import ErrorDominio
from app.modulos.chat_ia import motor_ia

log = logging.getLogger(__name__)

MAXIMO_TOKENS = 6000
TIMEOUT_SEGUNDOS = 180
# Tope del texto que se manda a resumir (~50.000 tokens); lo que sigue se descarta.
MAXIMO_CARACTERES = 200_000
MINIMO_CARACTERES = 50  # menos que esto: el PDF no tiene texto (escaneo)

INSTRUCCIONES = """\
Sos un analista funcional que prepara el contexto de negocio de DBA Assistant, una \
herramienta que traduce pedidos en lenguaje natural a T-SQL sobre las bases de la \
instancia descripta en el esquema.

Recibís el texto de un documento de la empresa (funcional o no funcional). Escribí un \
resumen que le sirva a otro modelo para entender el negocio y elegir bien las tablas:
- Procesos y entidades de negocio que describe y, para cada una, la tabla o vista del \
esquema que le corresponde (esquema.tabla) cuando se pueda deducir con seguridad.
- Términos propios del negocio y su significado (estados, códigos, siglas).
- Reglas de negocio y cálculos (cómo se obtiene un indicador, qué filtros aplica).
- Requisitos no funcionales que afecten a los datos: volúmenes, retención, horarios, \
disponibilidad, seguridad o datos sensibles.
No inventes: si el documento no menciona algo, no lo agregues. No copies texto de relleno \
(índices, versiones, firmas). Usá viñetas breves, agrupadas por tema, y como máximo unas \
800 palabras. En "tablas" listá solo objetos que existen en el esquema (esquema.tabla).\
"""


class ResumenDocumento(BaseModel):
    resumen: str
    tablas: list[str] = Field(description="Tablas o vistas del esquema relacionadas.")


def extraer_texto(contenido: bytes) -> tuple[str, int]:
    """Texto del PDF y cantidad de páginas. CTX_DOC_000 si no es un PDF; CTX_DOC_001 si
    no tiene texto (por ejemplo, un escaneo sin OCR)."""
    if not contenido.startswith(b"%PDF-"):
        raise ErrorDominio("CTX_DOC_000")
    try:
        lector = PdfReader(io.BytesIO(contenido))
        if lector.is_encrypted:
            lector.decrypt("")
        paginas = [pagina.extract_text() or "" for pagina in lector.pages]
    except (PdfReadError, ValueError, KeyError, TypeError) as error:
        raise ErrorDominio("CTX_DOC_000", str(error)) from error
    except Exception as error:  # pypdf puede fallar de muchas formas con un PDF dañado
        log.warning("CTX_DOC_000 lectura del PDF: %s", error)
        raise ErrorDominio("CTX_DOC_000", str(error)) from error
    texto = "\n\n".join(p.strip() for p in paginas if p.strip())
    if len(texto) < MINIMO_CARACTERES:
        raise ErrorDominio("CTX_DOC_001")
    return texto, len(paginas)


async def resumir(
    esquema_texto: str, nombre: str, clase: str, descripcion: str, texto: str
) -> ResumenDocumento:
    recortado = texto[:MAXIMO_CARACTERES]
    aviso = (
        f"\n\n[Documento recortado: se resumen los primeros {MAXIMO_CARACTERES} caracteres.]"
        if len(texto) > MAXIMO_CARACTERES
        else ""
    )
    pedido = (
        f"Documento: {nombre} (tipo: {clase})\n"
        + (f"Descripción: {descripcion}\n" if descripcion else "")
        + f"\n<documento>\n{recortado}\n</documento>{aviso}"
    )
    return await motor_ia.solicitar(
        INSTRUCCIONES,
        esquema_texto,
        [{"role": "user", "content": pedido}],
        ResumenDocumento,
        MAXIMO_TOKENS,
        "IA_TRA_000",
        timeout=TIMEOUT_SEGUNDOS,
    )
