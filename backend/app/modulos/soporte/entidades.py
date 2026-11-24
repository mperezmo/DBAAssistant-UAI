"""Entidades de Soporte: el panel técnico del Administrador."""

import re
from dataclasses import dataclass

# Tablas de la base de la aplicación con historia de datos (tablas temporales, migraciones
# 014 y 017). Limita qué tabla se puede consultar: el nombre llega desde la pantalla.
TABLAS_CON_HISTORIA = (
    "usuario",
    "usuario_ambiente",
    "usuario_restriccion",
    "conexion",
    "umbral_metrica",
    "regla_alerta",
    "politica_backup",
    "workaround",
    "automatizacion_workaround",
    "alerta_desestimada",
)
# Columnas que nunca se muestran, aunque estén cifradas.
PATRON_SENSIBLE = re.compile(r"password|contrasena|cifrad|secreto|token", re.IGNORECASE)
COLUMNA_DESDE = "valido_desde"
COLUMNA_HASTA = "valido_hasta"
LIMITE_FILAS = 500
DDL_POR_PAGINA = 50

# Origen de cada versión de la estructura.
ORIGEN_INICIAL = "inicial"  # primera captura
ORIGEN_MIGRACION = "migracion"  # cambió por migraciones de la aplicación
ORIGEN_DETECTADO = "detectado"  # cambió sin migraciones nuevas (por ejemplo, desde SSMS)
ORIGEN_MANUAL = "manual"  # captura pedida por el Administrador

MODO_MOMENTO = "momento"  # la tabla tal como estaba en un momento
MODO_CAMBIOS = "cambios"  # todas las versiones de las filas, de la más nueva a la más vieja

# Un proceso en segundo plano se considera detenido si no da señales en este múltiplo de
# su intervalo.
TOLERANCIA_LATIDOS = 3

# Orden de los objetos en el script de estructura: primero lo que otros referencian.
ORDEN_SCRIPT = ("tabla", "indice", "funcion", "vista", "procedimiento", "trigger")


@dataclass(frozen=True)
class TablaConHistoria:
    nombre: str
    columnas: tuple[str, ...]  # visibles, en el orden de la tabla
    clave: tuple[str, ...]  # columnas de la clave primaria
