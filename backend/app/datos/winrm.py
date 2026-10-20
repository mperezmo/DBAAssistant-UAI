"""Acceso a servidores Windows por WinRM (autenticación NTLM).

Se usa para consultar e iniciar el servicio de SQL Server cuando el motor no acepta
conexiones T-SQL. pywinrm es sincrónico: invocar con ``run_in_threadpool``.
"""

from dataclasses import dataclass

import winrm


@dataclass(frozen=True)
class ResultadoComando:
    codigo: int
    salida: str
    error: str


def ejecutar_powershell(
    host: str, puerto: int, https: bool, usuario: str, password: str, script: str
) -> ResultadoComando:
    esquema = "https" if https else "http"
    sesion = winrm.Session(
        f"{esquema}://{host}:{puerto}/wsman",
        auth=(usuario, password),
        transport="ntlm",
        # Igual que con SQL Server, los servidores de clientes suelen usar certificados
        # autofirmados; el tráfico NTLM va cifrado igualmente por el protocolo.
        server_cert_validation="ignore",
        operation_timeout_sec=10,
        read_timeout_sec=15,
    )
    respuesta = sesion.run_ps(script)
    return ResultadoComando(
        codigo=respuesta.status_code,
        salida=respuesta.std_out.decode("utf-8", errors="replace"),
        error=respuesta.std_err.decode("utf-8", errors="replace"),
    )
