"""Configuración de la aplicación.

Los valores se leen de variables de entorno (o del archivo .env en desarrollo).
Los secretos NO viven acá: se obtienen a través de ``core.secretos`` para poder
reemplazar el origen (hoy .env, luego Azure Key Vault) sin tocar el resto del código.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracion(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_nombre: str = "DBA Assistant"
    app_entorno: str = "desarrollo"
    log_nivel: str = "INFO"

    # Base SQL Server propia de la aplicación (estructura funcional).
    sqlserver_host: str = "sqlserver"
    sqlserver_puerto: int = 1433
    sqlserver_base: str = "master"
    sqlserver_usuario: str = "sa"
    sqlserver_driver: str = "ODBC Driver 18 for SQL Server"
    sqlserver_confiar_certificado: bool = True
    sqlserver_timeout_segundos: int = 5

    # MongoDB: auditoría, glosarios de las bases monitoreadas y logs.
    mongodb_base: str = "dba_assistant"

    # Redis: caché de queries de IA, contexto y métricas. TTL en segundos.
    redis_url: str = "redis://redis:6379/0"
    cache_ttl_ia: int = 86_400
    cache_ttl_contexto: int = 600
    cache_ttl_metricas: int = 30

    # IA generativa (Anthropic Claude API).
    ia_modelo: str = "claude-haiku-5-5"


@lru_cache
def obtener_configuracion() -> Configuracion:
    """Devuelve la configuración cargada una única vez por proceso."""
    return Configuracion()
