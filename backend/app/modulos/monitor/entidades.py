"""Entidades del monitoreo de instancias SQL Server (CU-000-003), backups y jobs del
SQL Server Agent."""

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
    # Backups y jobs se leen con menos frecuencia; cada lectura lleva el último valor.
    # None: no se pudieron leer (por ejemplo, sin permiso sobre msdb).
    backups: list[dict] | None = None
    jobs: list[dict] | None = None
    # Servicio SQL Server Agent ({"estado", "inicio"}); None si la instancia no lo tiene.
    agente: dict | None = None
    # Prefijos de las condiciones que esta lectura no pudo evaluar: sus alertas no se
    # dan por normalizadas.
    no_evaluadas: tuple[str, ...] = ()


# --- Backups ------------------------------------------------------------------------

# Modelos de recuperación que necesitan backups de log.
RECUPERACION_CON_LOG = ("FULL", "BULK_LOGGED")
FULL_HORAS_POR_DEFECTO = 24
LOG_MINUTOS_POR_DEFECTO = 60
LIMITES_FULL_HORAS = (1, 744)  # hasta 31 días
LIMITES_LOG_MINUTOS = (5, 1440)


@dataclass(frozen=True)
class PoliticaBackup:
    """Frecuencia esperada de backups de una conexión."""

    habilitada: bool = True
    full_horas: int = FULL_HORAS_POR_DEFECTO
    log_minutos: int = LOG_MINUTOS_POR_DEFECTO


def estado_backup(base: dict, politica: PoliticaBackup) -> dict[str, str | None]:
    """Estado del backup full y del de log de una base frente a la política.

    ``ok``, ``warning`` o ``critical``; None si no aplica (política deshabilitada, o log
    en una base sin modelo de recuperación con log o todavía sin backup full).
    """
    if not politica.habilitada:
        return {"full": None, "log": None}
    plazo_full = politica.full_horas * 60
    full = base["full_min"]
    if full is None:
        # Una base recién creada todavía está dentro del plazo de su primer backup.
        estado_full = "ok" if base["creada_min"] < plazo_full else "critical"
    elif full > 2 * plazo_full:
        estado_full = "critical"
    else:
        estado_full = "warning" if full > plazo_full else "ok"

    estado_log = None
    if base["recuperacion"] in RECUPERACION_CON_LOG and full is not None:
        # Sin backups de log, la cadena empezó con el último full.
        transcurrido = base["log_min"] if base["log_min"] is not None else full
        estado_log = "warning" if transcurrido > politica.log_minutos else "ok"
    return {"full": estado_full, "log": estado_log}


# --- Jobs del SQL Server Agent ------------------------------------------------------

# Una falla vieja (por ejemplo, de un job que ya no se programa) no genera alertas.
VIGENCIA_FALLA_JOB_MINUTOS = 7 * 24 * 60
# Un job está colgado si lleva corriendo más que este múltiplo de su duración promedio.
FACTOR_JOB_COLGADO = 3
MINIMO_JOB_COLGADO_SEGUNDOS = 300
# Nombre de los jobs que crean los workarounds de backup programado.
PREFIJO_JOB = "DBA Assistant · "
PREFIJO_JOB_BACKUP = PREFIJO_JOB + "Backup"


def estado_job(job: dict) -> str:
    """deshabilitado, colgado, en_curso, falla, sin_ejecuciones u ok."""
    if not job["habilitado"]:
        return "deshabilitado"
    if job["corriendo_s"] is not None:
        limite = max(FACTOR_JOB_COLGADO * (job["promedio_s"] or 0), MINIMO_JOB_COLGADO_SEGUNDOS)
        colgado = job["promedio_s"] is not None and job["corriendo_s"] > limite
        return "colgado" if colgado else "en_curso"
    if job["resultado"] is None:
        return "sin_ejecuciones"
    return "falla" if job["resultado"] == "falla" else "ok"


def falla_vigente(job: dict) -> bool:
    return (
        estado_job(job) == "falla"
        and job["hace_min"] is not None
        and job["hace_min"] <= VIGENCIA_FALLA_JOB_MINUTOS
    )


def agente_detenido(agente: dict | None) -> bool:
    """El Agent debería correr (inicio automático) y no está corriendo. En las ediciones
    o instalaciones donde está deshabilitado (por ejemplo, Express) no es una falla."""
    return bool(agente) and agente["inicio"] == "Automatic" and agente["estado"] != "Running"
