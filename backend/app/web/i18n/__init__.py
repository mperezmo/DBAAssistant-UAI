"""Traducciones de la interfaz.

Cada idioma es un archivo ``<codigo>.json`` con pares clave/texto. Las plantillas
usan ``t("clave")``; si falta una traducción se muestra la clave, lo que facilita
detectar textos pendientes.
"""

import json
from functools import lru_cache
from pathlib import Path

DIRECTORIO = Path(__file__).parent
IDIOMA_PREDETERMINADO = "es"


@lru_cache
def textos(idioma: str) -> dict[str, str]:
    archivo = DIRECTORIO / f"{idioma}.json"
    if not archivo.exists():
        archivo = DIRECTORIO / f"{IDIOMA_PREDETERMINADO}.json"
    return json.loads(archivo.read_text(encoding="utf-8"))


def traducir(clave: str, idioma: str = IDIOMA_PREDETERMINADO) -> str:
    return textos(idioma).get(clave, clave)
