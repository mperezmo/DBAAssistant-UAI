"""Entidades de Buenas Prácticas: el informe que devuelve el motor IA."""

from typing import Literal

from pydantic import BaseModel, Field

ESTADOS_ANALISIS = ("en_curso", "listo", "error")


class Sugerencia(BaseModel):
    titulo: str
    tipo: Literal["consultas", "indices", "fragmentacion", "esquema", "regla"]
    severidad: Literal["alta", "media", "baja"]
    objeto: str
    descripcion: str
    sql: str | None = Field(description="T-SQL que aplica la sugerencia, o null.")
    indice_hallazgo: int | None


class Informe(BaseModel):
    resumen: str
    sugerencias: list[Sugerencia]
