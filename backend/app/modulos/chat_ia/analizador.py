"""Análisis estático de T-SQL antes de ejecutarlo.

Clasifica la sentencia (qué se puede ejecutar directo y qué pasa obligatoriamente por el
Sandbox), detecta las tablas involucradas y las condiciones que exige la política de
ejecución: WHERE en las modificaciones y doble confirmación en las destructivas.
Lo que no se puede analizar con certeza se trata como lo más restrictivo.
"""

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

DIRECTAS = ("SELECT", "INSERT", "UPDATE", "DELETE")
SANDBOX = "SANDBOX"
# Respaldo textual: si el parser no reconoce algo, igual se pide la doble confirmación.
PATRON_DESTRUCTIVA = re.compile(r"\b(DELETE|TRUNCATE|DROP)\b", re.IGNORECASE)
PATRON_COMENTARIOS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


@dataclass(frozen=True)
class Tabla:
    base: str
    esquema: str
    nombre: str

    @property
    def nombre_completo(self) -> str:
        return f"{self.esquema}.{self.nombre}"


@dataclass(frozen=True)
class Analisis:
    # SELECT, INSERT, UPDATE o DELETE (una sola sentencia que se puede ejecutar directo)
    # o SANDBOX (DDL, MERGE, EXEC, varias sentencias o algo que no se pudo analizar).
    tipo: str
    acciones: frozenset[str]  # para las reglas de negocio: SELECT/INSERT/UPDATE/DELETE/DDL
    tablas: tuple[Tabla, ...]
    falta_where: bool
    destructiva: bool
    motivo: str | None = None

    @property
    def es_lectura(self) -> bool:
        return self.tipo == "SELECT"

    @property
    def es_escritura(self) -> bool:
        return self.tipo in ("INSERT", "UPDATE", "DELETE")


def _sin_comentarios(sql: str) -> str:
    return PATRON_COMENTARIOS.sub(" ", sql)


def _tablas(sentencia: exp.Expression, base: str) -> list[Tabla]:
    """Tablas reales referenciadas, sin los nombres de CTE ni los alias."""
    ctes = {c.alias_or_name.lower() for c in sentencia.find_all(exp.CTE)}
    alias = {t.alias.lower() for t in sentencia.find_all(exp.Table) if t.alias}
    tablas = []
    for t in sentencia.find_all(exp.Table):
        nombre = t.name
        if not nombre or (not t.db and nombre.lower() in ctes | alias):
            continue
        tablas.append(Tabla(t.catalog or base, t.db or "dbo", nombre))
    return tablas


def _clasificar(sentencia: exp.Expression) -> tuple[str, bool]:
    """Devuelve (tipo, falta_where) de una sentencia."""
    if isinstance(sentencia, exp.Query):
        # SELECT ... INTO crea una tabla: es DDL.
        if sentencia.find(exp.Into):
            return SANDBOX, False
        return "SELECT", False
    if isinstance(sentencia, exp.Insert):
        origen = sentencia.expression
        if isinstance(origen, exp.Values):
            return "INSERT", False
        if isinstance(origen, exp.Query):
            selects = list(origen.find_all(exp.Select))
            return "INSERT", not selects or any(not s.args.get("where") for s in selects)
        return SANDBOX, False  # INSERT ... EXEC y otras variantes
    if isinstance(sentencia, exp.Update | exp.Delete):
        tipo = "UPDATE" if isinstance(sentencia, exp.Update) else "DELETE"
        return tipo, not sentencia.args.get("where")
    return SANDBOX, False


def _accion(sentencia: exp.Expression, tipo: str) -> str:
    if tipo in DIRECTAS:
        return tipo
    if isinstance(sentencia, exp.Merge):
        return "UPDATE"
    return "DDL"


def analizar(sql: str, base: str) -> Analisis:
    texto = (sql or "").strip()
    destructiva_textual = bool(PATRON_DESTRUCTIVA.search(_sin_comentarios(texto)))
    try:
        sentencias = [s for s in sqlglot.parse(texto, read="tsql") if s is not None]
    except SqlglotError:
        sentencias = []
    if not sentencias:
        return Analisis(
            SANDBOX, frozenset({"DDL"}), (), False, destructiva_textual, "no_analizable"
        )

    tipos, acciones, tablas, falta_where = [], set(), [], False
    for sentencia in sentencias:
        tipo, sin_where = _clasificar(sentencia)
        tipos.append(tipo)
        acciones.add(_accion(sentencia, tipo))
        falta_where |= sin_where
        tablas += _tablas(sentencia, base)
    destructiva = destructiva_textual or any(
        isinstance(s, exp.Delete | exp.TruncateTable | exp.Drop) for s in sentencias
    )
    unicas = tuple(dict.fromkeys(tablas))

    if len(sentencias) > 1:
        return Analisis(
            SANDBOX, frozenset(acciones), unicas, falta_where, destructiva, "varias_sentencias"
        )
    tipo = tipos[0]
    motivo = None if tipo != SANDBOX else "tipo_de_sentencia"
    return Analisis(tipo, frozenset(acciones), unicas, falta_where, destructiva, motivo)
