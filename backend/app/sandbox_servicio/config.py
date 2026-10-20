"""Configuración del servicio Sandbox."""

import shlex
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfiguracionSandbox(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SANDBOX_", extra="ignore")

    imagen: str = "mcr.microsoft.com/mssql/server:2022-latest"
    memoria: str = "2g"
    # Red de Docker compartida con este servicio. Vacía: los contenedores publican el
    # puerto en el host y se accede por 127.0.0.1 (desarrollo y pruebas fuera de Compose).
    red: str = ""
    prefijo: str = "dba-sbx-"
    # Comando para invocar sqlpackage (en la imagen: dotnet + paquete oficial).
    sqlpackage: str = "dotnet /opt/sqlpackage/sqlpackage.dll"
    espera_inicio_segundos: int = 180
    timeout_sincronizacion_segundos: int = 1800
    driver: str = "ODBC Driver 18 for SQL Server"

    @property
    def comando_sqlpackage(self) -> list[str]:
        return shlex.split(self.sqlpackage)


@lru_cache
def configuracion() -> ConfiguracionSandbox:
    return ConfiguracionSandbox()
