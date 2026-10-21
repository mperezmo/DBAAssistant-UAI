"""Traducciones de la interfaz.

Cada idioma es un archivo ``<codigo>.json`` con pares clave/texto. Las plantillas
usan ``t("clave")``; si falta una traducción se usa la del idioma predeterminado y, si
tampoco existe, se muestra la clave, lo que facilita detectar textos pendientes.

El idioma de cada petición queda en ``contexto.idioma`` (el que eligió el usuario o, si
nunca eligió, el del navegador). Los procesos sin petición, como los avisos a varios
destinatarios, lo fijan con ``en_idioma``.
"""

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from app.core import contexto

DIRECTORIO = Path(__file__).parent
IDIOMA_PREDETERMINADO = "es"
IDIOMAS = ("es", "en")
# Para Intl en el navegador (formatos de fecha y número) y para Auth0.
LOCALES = {"es": "es-AR", "en": "en-US"}
# Parámetros que son a su vez una clave de traducción (por ejemplo, el nombre de una
# métrica dentro del título de una alerta) llevan este prefijo.
PREFIJO_CLAVE = "@"


@lru_cache
def textos(idioma: str) -> dict[str, str]:
    archivo = DIRECTORIO / f"{idioma}.json"
    if not archivo.exists():
        archivo = DIRECTORIO / f"{IDIOMA_PREDETERMINADO}.json"
    return json.loads(archivo.read_text(encoding="utf-8"))


def idioma_actual() -> str:
    idioma = contexto.idioma.get()
    return idioma if idioma in IDIOMAS else IDIOMA_PREDETERMINADO


def traducir(clave: str, idioma: str | None = None) -> str:
    idioma = idioma if idioma in IDIOMAS else idioma_actual()
    texto = textos(idioma).get(clave)
    if texto is None and idioma != IDIOMA_PREDETERMINADO:
        texto = textos(IDIOMA_PREDETERMINADO).get(clave)
    return clave if texto is None else texto


def formatear(clave: str, idioma: str | None = None, **parametros) -> str:
    """Texto con sus parámetros; los que empiezan con ``@`` se traducen también."""
    valores = {
        k: traducir(v[1:], idioma) if isinstance(v, str) and v.startswith(PREFIJO_CLAVE) else v
        for k, v in parametros.items()
    }
    return traducir(clave, idioma).format(**valores)


def idioma_de_cabecera(accept_language: str | None) -> str:
    """Primer idioma disponible de la cabecera Accept-Language, en orden de preferencia."""
    preferidos = []
    for parte in (accept_language or "").split(","):
        codigo, _, calidad = parte.strip().partition(";q=")
        try:
            peso = float(calidad) if calidad else 1.0
        except ValueError:
            continue  # calidad inválida: se ignora esa entrada
        base = re.split(r"[-_]", codigo.strip().lower())[0]
        if base in IDIOMAS:
            preferidos.append((peso, base))
    return max(preferidos, key=lambda p: p[0])[1] if preferidos else IDIOMA_PREDETERMINADO


@contextmanager
def en_idioma(idioma: str | None) -> Iterator[str]:
    """Fija el idioma mientras dura el bloque (avisos, tareas en segundo plano)."""
    elegido = idioma if idioma in IDIOMAS else IDIOMA_PREDETERMINADO
    marca = contexto.idioma.set(elegido)
    try:
        yield elegido
    finally:
        contexto.idioma.reset(marca)
