"""Reporte de auditoría en PDF (A4 apaisado, sin firma digital por ahora)."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.modulos.conexiones.entidades import ZONA_HORARIA_POR_DEFECTO
from app.web.i18n import formatear, traducir

LARGO_MAXIMO_CELDA = 120
# Ancho relativo de las columnas que suelen tener textos largos.
PESOS = {"sql": 4, "resumen": 5, "mensaje": 5, "entidad": 2, "accion": 2, "modulo": 2, "fecha": 1.6}


def _texto(valor) -> str:
    """Las fuentes estándar de PDF solo cubren Latin-1: el resto se reemplaza."""
    texto = "" if valor is None else " ".join(str(valor).split())
    if len(texto) > LARGO_MAXIMO_CELDA:
        texto = texto[: LARGO_MAXIMO_CELDA - 1] + "…"
    return texto.encode("latin-1", "replace").decode("latin-1")


def generar(
    titulo: str,
    empresa: str,
    generado_por: str,
    filtros: dict,
    columnas: tuple[tuple[str, str], ...],
    filas: list[dict],
) -> bytes:
    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    ahora = datetime.now(ZoneInfo(ZONA_HORARIA_POR_DEFECTO)).strftime("%Y-%m-%d %H:%M")

    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, _texto(f"DBA Assistant · {titulo}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    detalle = formatear(
        "auditoria.pdf.detalle",
        empresa=empresa,
        usuario=generado_por,
        fecha=ahora,
        registros=len(filas),
    )
    pdf.cell(0, 5, _texto(detalle), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if filtros:
        texto = (
            traducir("auditoria.pdf.filtros")
            + " "
            + ", ".join(f"{k}={v}" for k, v in filtros.items())
        )
        pdf.cell(0, 5, _texto(texto), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pesos = [PESOS.get(campo, 1.2) for _, campo in columnas]
    anchos = [pdf.epw * p / sum(pesos) for p in pesos]
    with pdf.table(
        col_widths=anchos,
        text_align="LEFT",
        line_height=4.2,
        first_row_as_headings=True,
        repeat_headings=1,
    ) as tabla:
        pdf.set_font("Helvetica", "", 7)
        encabezado = tabla.row()
        for titulo_columna, _ in columnas:
            encabezado.cell(_texto(titulo_columna))
        for registro in filas:
            renglon = tabla.row()
            for _, campo in columnas:
                renglon.cell(_texto(registro.get(campo)))
    return bytes(pdf.output())
