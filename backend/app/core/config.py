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

    # SQL Server propio de la aplicación: una base central (catálogo de tenants)
    # y una base por tenant con la estructura funcional.
    sqlserver_host: str = "sqlserver"
    sqlserver_puerto: int = 1433
    sqlserver_prefijo_base: str = "DBAAssistant"
    sqlserver_usuario: str = "sa"
    sqlserver_driver: str = "ODBC Driver 18 for SQL Server"
    sqlserver_confiar_certificado: bool = True
    sqlserver_timeout_segundos: int = 5

    # MongoDB: una base por tenant (bitácoras, contexto de negocio, métricas)
    # y una de plataforma para eventos sin tenant.
    mongodb_prefijo_base: str = "dba_assistant"

    # Redis: caché de queries de IA, contexto y métricas. TTL en segundos.
    redis_url: str = "redis://redis:6379/0"
    cache_ttl_ia: int = 86_400
    cache_ttl_contexto: int = 600
    cache_ttl_metricas: int = 30

    # Monitoreo: el recolector lee cada instancia registrada cada N segundos y guarda
    # las métricas en MongoDB durante los días de retención.
    monitor_recolector_activo: bool = True
    monitor_intervalo_segundos: int = 3
    monitor_retencion_dias: int = 30

    # Notificaciones push (Web Push con VAPID). La clave privada es un secreto
    # (SECRETO_VAPID_CLAVE_PRIVADA); se generan con ``python -m app.cli generar-vapid``.
    vapid_clave_publica: str = ""
    vapid_contacto: str = ""

    # Correo saliente (SMTP) para notificar alertas. La contraseña es un secreto
    # (SECRETO_SMTP_PASSWORD). Sin servidor configurado no se envían correos.
    smtp_host: str = ""
    smtp_puerto: int = 587
    smtp_usuario: str = ""
    smtp_remitente: str = ""
    smtp_starttls: bool = True

    # Identidad (Auth0). Cada tenant es una Organization y entra por su subdominio
    # (<slug>.<dominio_base>).
    dominio_base: str = "localhost"
    auth0_domain: str = ""
    auth0_client_id: str = ""
    auth0_audience: str = ""
    auth0_admin_client_id: str = ""
    auth0_exigir_mfa: bool = True
    # Tolerancia ante relojes desfasados entre Auth0 y el servidor (p. ej. Docker en WSL2).
    auth0_tolerancia_reloj_segundos: int = 60
    sesion_duracion_segundos: int = 8 * 60 * 60

    # IA generativa (Anthropic Claude API).
    ia_modelo: str = "claude-haiku-5-5"

    @property
    def auth0_configurado(self) -> bool:
        return bool(self.auth0_domain and self.auth0_client_id)

    @property
    def sqlserver_base_catalogo(self) -> str:
        return f"{self.sqlserver_prefijo_base}_Catalogo"

    @property
    def mongodb_base_plataforma(self) -> str:
        return f"{self.mongodb_prefijo_base}_plataforma"


@lru_cache
def obtener_configuracion() -> Configuracion:
    """Devuelve la configuración cargada una única vez por proceso."""
    return Configuracion()
