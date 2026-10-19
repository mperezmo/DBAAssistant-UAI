"""Cifrado simétrico de credenciales de terceros (contraseñas de conexiones).

Hasta migrar a Azure Key Vault, las credenciales se guardan cifradas en la base del
tenant con una clave maestra (``SECRETO_CLAVE_CIFRADO``). Se usa Fernet (AES-128-CBC
con HMAC-SHA256), que además detecta cualquier alteración del texto cifrado.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.errores import ErrorDominio
from app.core.secretos import SecretoNoEncontradoError, proveedor_secretos


def generar_clave() -> str:
    return Fernet.generate_key().decode()


@lru_cache
def _fernet() -> Fernet:
    try:
        return Fernet(proveedor_secretos().obtener("clave_cifrado").encode())
    except (SecretoNoEncontradoError, ValueError) as error:
        raise ErrorDominio("AKV_000", "Falta o es inválida SECRETO_CLAVE_CIFRADO.") from error


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def descifrar(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as error:
        raise ErrorDominio("AKV_000", "No se pudo descifrar la credencial.") from error


def reiniciar() -> None:
    """Descarta la clave en memoria (pruebas o rotación de clave)."""
    _fernet.cache_clear()
