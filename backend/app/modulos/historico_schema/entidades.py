"""Entidades del Histórico de Schema."""

from dataclasses import dataclass, field

from app.modulos.conexiones.entidades import Conexion

# Identificador del estado actual de la base al comparar versiones.
ACTUAL = "actual"


@dataclass
class Captura:
    """Estado de las bases alcanzadas por un DDL antes de ejecutarlo."""

    conexion: Conexion
    antes: dict[str, dict | None] = field(default_factory=dict)
