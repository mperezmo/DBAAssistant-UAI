"""Documentos de negocio: conversión del PDF a Markdown e interpretación con el motor IA.

El PDF se convierte a Markdown con MarkItDown: conserva títulos, listas y tablas y descarta
el formato, así la IA lo entiende mejor y con menos tokens. Después, la IA lo interpreta una
sola vez: arma un resumen orientado a los datos, lista las tablas relacionadas y propone
términos para el glosario y reglas. El documento entero no viaja en cada consulta: el Chat IA
y Buenas Prácticas reciben solo el resumen.
"""

import io
import logging
import re
from typing import Literal

from markitdown import StreamInfo
from markitdown.converters import PdfConverter
from pydantic import BaseModel, Field
from pypdf import PdfReader

from app.core.errores import ErrorDominio
from app.modulos.chat_ia import motor_ia

log = logging.getLogger(__name__)

MAXIMO_TOKENS = 8000
TIMEOUT_SEGUNDOS = 180
# Tope del Markdown que se manda a interpretar (~50.000 tokens); lo que sigue se descarta.
MAXIMO_CARACTERES = 200_000
MINIMO_CARACTERES = 50  # menos que esto: el PDF no tiene texto (escaneo)

INSTRUCCIONES = """\
Sos un analista funcional que prepara el contexto de negocio de DBA Assistant, una \
herramienta que traduce pedidos en lenguaje natural a T-SQL sobre la base descripta en el \
esquema.

Recibís un documento de la empresa convertido a Markdown. Puede ser funcional (procesos, \
casos de uso, reglas) o no funcional (volúmenes, retención, disponibilidad, seguridad); \
interpretalo según lo que contenga. Devolvé:

- "resumen": lo que otro modelo necesita para entender el negocio y elegir bien las tablas. \
Procesos y entidades con la tabla o vista que les corresponde (esquema.tabla) cuando se \
pueda deducir con seguridad; términos propios (estados, códigos, siglas); reglas y cálculos \
(cómo se obtiene un indicador, qué filtros aplica); requisitos no funcionales que afecten a \
los datos. Viñetas breves agrupadas por tema, como máximo unas 800 palabras.
- "tablas": las tablas o vistas del esquema relacionadas con el documento.
- "terminos": términos de negocio para el glosario, cada uno con la tabla o vista que lo \
representa ("entidad", esquema.tabla), sinónimos y una definición que diga cómo se obtiene \
en el modelo (filtros, joins, cálculos). Solo términos que el documento define o usa con un \
significado claro y que se puedan ubicar en el esquema.
- "reglas": reglas operativas que se desprenden del documento y restringen o advierten sobre \
operaciones SQL: "acciones" (SELECT, INSERT, UPDATE, DELETE o DDL), "efecto" ("bloquear" si \
el documento lo prohíbe, "advertir" si pide cuidado o una revisión) y las "tablas" alcanzadas. \
Por ejemplo, "las facturas emitidas no se modifican" bloquea UPDATE y DELETE sobre la tabla \
de facturas.

No inventes: si el documento no lo dice, no lo agregues. No copies texto de relleno \
(índices, versiones, firmas). Usá solo tablas que existen en el esquema (esquema.tabla) y no \
repitas términos ni reglas que ya figuran en el contexto actual. Si no hay términos o reglas \
para proponer, devolvé las listas vacías.\
"""


class TerminoPropuesto(BaseModel):
    termino: str
    entidad: str = Field(description="Tabla o vista que lo representa (esquema.tabla).")
    sinonimos: list[str] = Field(default_factory=list)
    descripcion: str = Field(description="Cómo se obtiene en el modelo: filtros, joins, cálculos.")


class ReglaPropuesta(BaseModel):
    descripcion: str
    acciones: list[Literal["SELECT", "INSERT", "UPDATE", "DELETE", "DDL"]]
    efecto: Literal["bloquear", "advertir"]
    tablas: list[str] = Field(description="Tablas alcanzadas (esquema.tabla).")


class Interpretacion(BaseModel):
    resumen: str
    tablas: list[str] = Field(description="Tablas o vistas del esquema relacionadas.")
    terminos: list[TerminoPropuesto]
    reglas: list[ReglaPropuesta]


def _paginas(contenido: bytes) -> int:
    """Cantidad de páginas; de paso confirma que el archivo es un PDF legible."""
    try:
        lector = PdfReader(io.BytesIO(contenido))
        if lector.is_encrypted:
            lector.decrypt("")
        return len(lector.pages)
    except Exception as error:  # pypdf puede fallar de muchas formas con un PDF dañado
        raise ErrorDominio("CTX_DOC_000", str(error)) from error


def convertir(contenido: bytes) -> tuple[str, int]:
    """Markdown del PDF y cantidad de páginas. CTX_DOC_000 si no es un PDF; CTX_DOC_001 si
    no tiene texto (por ejemplo, un escaneo sin OCR)."""
    if not contenido.startswith(b"%PDF-"):
        raise ErrorDominio("CTX_DOC_000")
    paginas = _paginas(contenido)
    try:
        # El conversor de PDF directo: MarkItDown completo, ante un PDF que no puede leer,
        # lo trataría como texto plano y devolvería el binario.
        resultado = PdfConverter().convert(
            io.BytesIO(contenido), StreamInfo(extension=".pdf", mimetype="application/pdf")
        )
    except Exception as error:
        log.warning("CTX_DOC_000 conversión del PDF: %s", error)
        raise ErrorDominio("CTX_DOC_000", str(error)) from error
    # Saltos de página y líneas en blanco de más solo suman tokens.
    markdown = re.sub(r"\n{3,}", "\n\n", resultado.markdown.replace("\f", "\n\n")).strip()
    if len(markdown) < MINIMO_CARACTERES:
        raise ErrorDominio("CTX_DOC_001")
    return markdown, paginas


async def interpretar(contexto: str, nombre: str, markdown: str) -> Interpretacion:
    recortado = markdown[:MAXIMO_CARACTERES]
    aviso = (
        f"\n\n[Documento recortado: se interpretan los primeros {MAXIMO_CARACTERES} caracteres.]"
        if len(markdown) > MAXIMO_CARACTERES
        else ""
    )
    pedido = f"Documento: {nombre}\n\n<documento>\n{recortado}\n</documento>{aviso}"
    return await motor_ia.solicitar(
        INSTRUCCIONES,
        contexto,
        [{"role": "user", "content": pedido}],
        Interpretacion,
        MAXIMO_TOKENS,
        "IA_TRA_000",
        timeout=TIMEOUT_SEGUNDOS,
    )
