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


# --- Descripción de las modificaciones (para confirmarlas antes de ejecutar) ----------

# Argumentos con los que el conteo previo ya no representa lo que se modifica (UPDATE con
# FROM o JOIN, DELETE TOP, CTE, OUTPUT…): en esos casos no se estima.
ARGUMENTOS_SIMPLES = {"this", "expressions", "where", "using", "cluster"}


def _texto(expresion: exp.Expression | None) -> str | None:
    return expresion.sql(dialect="tsql") if expresion is not None else None


def _unica(sql: str) -> exp.Expression | None:
    try:
        sentencias = [s for s in sqlglot.parse((sql or "").strip(), read="tsql") if s is not None]
    except SqlglotError:
        return None
    return sentencias[0] if len(sentencias) == 1 else None


def _tabla_destino(sentencia: exp.Expression, base: str) -> str | None:
    destino = sentencia.this
    if isinstance(destino, exp.Schema):
        destino = destino.this
    # En "UPDATE u SET … FROM dbo.tabla u" el destino es un alias: se busca la tabla real.
    if isinstance(destino, exp.Table) and (destino.db or not sentencia.args.get("from_")):
        return f"{destino.db or 'dbo'}.{destino.name}"
    alias = destino.name.lower() if isinstance(destino, exp.Table) else ""
    for tabla in sentencia.find_all(exp.Table):
        if tabla.alias and tabla.alias.lower() == alias:
            return f"{tabla.db or 'dbo'}.{tabla.name}"
    tablas = _tablas(sentencia, base)
    return tablas[0].nombre_completo if tablas else _texto(destino)


def describir(sql: str, base: str) -> dict | None:
    """Qué modifica un INSERT, UPDATE o DELETE: tabla, valores y condición.

    Se arma desde el propio T-SQL (no desde la explicación de la IA), para que lo que el
    usuario confirma sea exactamente lo que se ejecuta.
    """
    sentencia = _unica(sql)
    if not isinstance(sentencia, exp.Insert | exp.Update | exp.Delete):
        return None
    descripcion = {
        "operacion": type(sentencia).__name__.upper(),
        "tabla": _tabla_destino(sentencia, base),
        "cambios": [],
        "condicion": None,
        "filas": None,
        "desde_consulta": False,
    }
    if isinstance(sentencia, exp.Update):
        descripcion["cambios"] = [
            {"columna": _texto(e.this), "valor": _texto(e.expression)}
            for e in sentencia.expressions
        ]
    if isinstance(sentencia, exp.Update | exp.Delete):
        where = sentencia.args.get("where")
        descripcion["condicion"] = _texto(where.this) if where else None
        return descripcion

    destino, origen = sentencia.this, sentencia.expression
    columnas = [_texto(c) for c in destino.expressions] if isinstance(destino, exp.Schema) else []
    if isinstance(origen, exp.Values):
        filas = origen.expressions
        descripcion["filas"] = len(filas)
        valores = [_texto(v) for v in filas[0].expressions] if filas else []
        descripcion["cambios"] = [
            {"columna": columnas[i] if i < len(columnas) else None, "valor": v}
            for i, v in enumerate(valores)
        ]
    elif isinstance(origen, exp.Query):
        descripcion["desde_consulta"] = True
        descripcion["cambios"] = [{"columna": c, "valor": None} for c in columnas]
        where = origen.args.get("where")
        descripcion["condicion"] = _texto(where.this) if where else None
    return descripcion


def consulta_de_conteo(sql: str) -> str | None:
    """SELECT que cuenta las filas que tocaría un UPDATE o DELETE simple."""
    sentencia = _unica(sql)
    if not isinstance(sentencia, exp.Update | exp.Delete):
        return None
    usados = {k for k, v in sentencia.args.items() if v not in (None, False, [])}
    where = sentencia.args.get("where")
    if not where or usados - ARGUMENTOS_SIMPLES:
        return None
    return f"SELECT COUNT_BIG(*) FROM {_texto(sentencia.this)} WHERE {_texto(where.this)}"
