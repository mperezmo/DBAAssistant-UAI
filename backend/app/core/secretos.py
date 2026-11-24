"""Acceso a secretos (credenciales, tokens, claves).

Toda credencial se pide por nombre a un proveedor, que la lee de las variables de
entorno del despliegue en Docker (en desarrollo, del archivo .env). La interfaz
permite cambiar el origen sin tocar el resto del código.
"""

import os
from functools import lru_cache
from typing import Protocol


class SecretoNoEncontradoError(LookupError):
    """El secreto solicitado no está definido en el proveedor."""


class ProveedorSecretos(Protocol):
    def obtener(self, nombre: str) -> str: ...


class ProveedorEntorno:
    """Lee secretos de variables de entorno (``SECRETO_<NOMBRE>``)."""

    PREFIJO = "SECRETO_"

    def obtener(self, nombre: str) -> str:
        clave = f"{self.PREFIJO}{nombre.upper()}"
        valor = os.environ.get(clave)
        if not valor:
            raise SecretoNoEncontradoError(f"Falta definir la variable {clave}")
        return valor


@lru_cache
def proveedor_secretos() -> ProveedorSecretos:
    return ProveedorEntorno()
