"""Entidades del panel principal (Inicio)."""

from datetime import timedelta

# Filtro de ambiente que abarca todas las conexiones.
TODOS = "TODOS"
# Un bloqueo cuenta como prolongado desde esta duración (como la alerta de bloqueo).
BLOQUEO_PROLONGADO_SEGUNDOS = 30
VENTANA_LATENCIA = timedelta(hours=1)
VENTANA_PENDIENTES = timedelta(days=7)
