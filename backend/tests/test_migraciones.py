import pytest

from app.datos import migraciones


@pytest.fixture
def conjunto(tmp_path, monkeypatch):
    monkeypatch.setattr(migraciones, "DIRECTORIO", tmp_path)
    (tmp_path / "prueba").mkdir()
    return tmp_path / "prueba"


def test_lista_en_orden_y_separa_lotes_por_go(conjunto):
    (conjunto / "002_segunda.sql").write_text("SELECT 2;")
    (conjunto / "001_primera.sql").write_text("CREATE TABLE a (x INT);\nGO\n\ngo  \nSELECT 1;")

    resultado = migraciones.listar("prueba")

    assert [m.version for m in resultado] == ["001", "002"]
    assert resultado[0].lotes() == ["CREATE TABLE a (x INT);", "SELECT 1;"]


def test_rechaza_nombres_invalidos(conjunto):
    (conjunto / "1_sin_formato.sql").write_text("SELECT 1;")

    with pytest.raises(migraciones.ErrorMigracion, match="Nombre de archivo inválido"):
        migraciones.listar("prueba")


def test_rechaza_versiones_duplicadas(conjunto):
    (conjunto / "001_una.sql").write_text("SELECT 1;")
    (conjunto / "001_otra.sql").write_text("SELECT 1;")

    with pytest.raises(migraciones.ErrorMigracion, match="duplicadas"):
        migraciones.listar("prueba")


def test_conjuntos_reales_son_validos():
    assert migraciones.listar("catalogo")
    assert migraciones.listar("tenant")


class CursorFalso:
    def __init__(self, registro, aplicadas, falla_en=None):
        self.registro = registro
        self.aplicadas = aplicadas
        self.falla_en = falla_en
        self._resultado = []

    def execute(self, sql, *parametros):
        if self.falla_en and self.falla_en in sql:
            raise RuntimeError("error de sintaxis")
        self.registro.append((sql.strip().split()[0], parametros))
        if sql.startswith("SELECT version"):
            self._resultado = [type("Fila", (), {"version": v}) for v in self.aplicadas]
        return self

    def __iter__(self):
        return iter(self._resultado)


class ConexionFalsa:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def base_falsa(monkeypatch, conjunto):
    from contextlib import contextmanager

    (conjunto / "001_uno.sql").write_text("CREATE TABLE uno (x INT);")
    (conjunto / "002_dos.sql").write_text(
        "CREATE TABLE dos (x INT);\nGO\nCREATE INDEX i ON dos (x);"
    )

    def preparar(aplicadas=(), falla_en=None):
        registro = []
        conexion = ConexionFalsa(CursorFalso(registro, list(aplicadas), falla_en))

        @contextmanager
        def conectar(base):
            yield conexion

        monkeypatch.setattr(migraciones.sqlserver, "conectar", conectar)
        return conexion, registro

    return preparar


def test_aplicar_ejecuta_solo_las_pendientes(base_falsa):
    conexion, registro = base_falsa(aplicadas=["001"])

    aplicadas = migraciones.aplicar("base", "prueba")

    assert aplicadas == ["002"]
    sentencias = [sentencia for sentencia, _ in registro]
    assert "CREATE" in sentencias and sentencias.count("INSERT") == 1
    assert ("INSERT", ("002", "002_dos.sql")) in registro


def test_aplicar_revierte_y_reporta_si_falla_un_archivo(base_falsa):
    conexion, _ = base_falsa(falla_en="CREATE INDEX")

    with pytest.raises(migraciones.ErrorMigracion, match="002_dos.sql"):
        migraciones.aplicar("base", "prueba")

    assert conexion.rollbacks == 1
