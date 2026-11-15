"""Entidades del Chat IA: la traducción que devuelve el motor y el análisis del T-SQL."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

# Tipos de sentencia que se ejecutan directo; el resto va obligatoriamente al Sandbox.
DIRECTAS = ("SELECT", "INSERT", "UPDATE", "DELETE")
SANDBOX = "SANDBOX"

# Alcance de un pedido al chat: los datos de las bases registradas, el estado del motor
# de la instancia (solo Administrador y DBA) o algo ajeno, que no se responde.
DATOS, MOTOR, FUERA = "datos", "motor", "fuera"


@dataclass(frozen=True)
class Tabla:
    base: str
    esquema: str
    nombre: str

    @property
    def nombre_completo(self) -> str:
        return f"{self.esquema}.{self.nombre}"


@dataclass(frozen=True)
class Analisis:
    # SELECT, INSERT, UPDATE o DELETE (una sola sentencia que se puede ejecutar directo)
    # o SANDBOX (DDL, MERGE, EXEC, varias sentencias o algo que no se pudo analizar).
    tipo: str
    acciones: frozenset[str]  # para las reglas de negocio: SELECT/INSERT/UPDATE/DELETE/DDL
    tablas: tuple[Tabla, ...]
    falta_where: bool
    destructiva: bool
    motivo: str | None = None

    @property
    def es_lectura(self) -> bool:
        return self.tipo == "SELECT"

    @property
    def es_escritura(self) -> bool:
        return self.tipo in ("INSERT", "UPDATE", "DELETE")


class Traduccion(BaseModel):
    alcance: Literal["datos", "motor", "fuera"] = Field(
        description="datos: bases registradas; motor: estado de la instancia; fuera: ajeno.",
    )
    sql: str | None = Field(description="Sentencia T-SQL, o null si falta información.")
    base: str | None = Field(description="Base sobre la que se ejecuta.")
    explicacion: str
    confianza: int = Field(ge=0, le=100)
