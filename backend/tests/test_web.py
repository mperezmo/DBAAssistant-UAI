from app.core.config import obtener_configuracion
from app.web.i18n import traducir


def test_dominio_base_muestra_la_pantalla_de_acceso_del_diseno(cliente):
    respuesta = cliente.get("/")

    assert respuesta.status_code == 200
    assert "auth-card" in respuesta.text


def test_service_worker_se_sirve_desde_la_raiz(cliente):
    respuesta = cliente.get("/sw.js")

    assert respuesta.status_code == 200
    assert "javascript" in respuesta.headers["content-type"]


def test_traduccion_inexistente_devuelve_la_clave():
    assert traducir("clave.inexistente") == "clave.inexistente"


def test_idioma_no_disponible_usa_espanol():
    assert traducir("menu.inicio", idioma="xx") == "Inicio"


async def test_inicio_reintenta_si_sql_server_no_responde(monkeypatch):
    import pyodbc

    from app import main

    intentos = []

    def migrar():
        intentos.append(1)
        if len(intentos) < 3:
            raise pyodbc.OperationalError("HYT00", "Login timeout expired")

    async def sin_espera(_):
        return None

    async def preparar_mongo():
        intentos.append("mongo")

    monkeypatch.setenv("MONITOR_RECOLECTOR_ACTIVO", "false")
    obtener_configuracion.cache_clear()
    monkeypatch.setattr(main.tenants, "migrar_todo", migrar)
    monkeypatch.setattr(main.tenants, "preparar_mongo_todo", preparar_mongo)
    monkeypatch.setattr(main.asyncio, "sleep", sin_espera)

    async with main.ciclo_de_vida(main.app):
        pass

    assert intentos == [1, 1, 1, "mongo"]
    obtener_configuracion.cache_clear()


async def test_inicio_lanza_y_detiene_el_recolector(monkeypatch):
    from app import main

    eventos = []

    class RecolectorFalso:
        async def ejecutar(self):
            eventos.append("ejecutar")

        def detener(self):
            eventos.append("detener")

    async def nada():
        return None

    monkeypatch.setattr(main.tenants, "migrar_todo", lambda: None)
    monkeypatch.setattr(main.tenants, "preparar_mongo_todo", nada)
    monkeypatch.setattr(main, "Recolector", RecolectorFalso)

    async with main.ciclo_de_vida(main.app):
        await main.asyncio.sleep(0)

    assert eventos == ["ejecutar", "detener"]
