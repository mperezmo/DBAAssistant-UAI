"""Entidades del Sandbox: estado de la estructura copiada y quién promueve a la base real."""

from datetime import timedelta

from app.identidad.entidades import ROLES_OPERATIVOS

# Estado del Sandbox de un servidor de origen.
SINCRONIZANDO, LISTO, ERROR = "sincronizando", "listo", "error"

ROLES_PROMUEVEN = ROLES_OPERATIVOS
# Con más antigüedad que esta, la estructura copiada se considera desactualizada.
VIGENCIA_ESTRUCTURA = timedelta(hours=24)
