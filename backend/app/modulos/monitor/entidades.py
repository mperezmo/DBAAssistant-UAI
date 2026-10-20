"""Entidades del monitoreo de instancias SQL Server (CU-000-003)."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DefinicionMetrica:
    """Métrica disponible y sus umbrales por defecto (warning y critical)."""

    id: str
    unidad: str
    warning: float | None = None
    critical: float | None = None


# Los umbrales por defecto son los del diseño de referencia.
METRICAS: dict[str, DefinicionMetrica] = {
    m.id: m
    for m in (
        DefinicionMetrica("cpu", "%", 70, 85),
        DefinicionMetrica("memoria", "%", 80, 90),
        DefinicionMetrica("disco", "%", 75, 90),
        DefinicionMetrica("latencia", "ms", 300, 450),
        DefinicionMetrica("sesiones", ""),
        DefinicionMetrica("bloqueos", ""),
        DefinicionMetrica("deadlocks", ""),
        DefinicionMetrica("conexiones_seg", "/s"),
    )
}


@dataclass(frozen=True)
class Umbral:
    """Configuración de una métrica para una conexión."""

    metrica: str
    habilitada: bool = True
    warning: float | None = None
    critical: float | None = None

    @classmethod
    def por_defecto(cls, metrica: str) -> Umbral:
        definicion = METRICAS[metrica]
        return cls(metrica, True, definicion.warning, definicion.critical)


@dataclass
class Lectura:
    """Resultado de leer una instancia en un momento dado."""

    valores: dict[str, float | None]
    bloqueos: list[dict] = field(default_factory=list)
    volumenes: list[dict] = field(default_factory=list)
    bases_no_disponibles: list[dict] = field(default_factory=list)
    logs: list[dict] = field(default_factory=list)
