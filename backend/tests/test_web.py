from app.web.i18n import traducir


def test_inicio_renderiza_layout_del_diseno(cliente):
    respuesta = cliente.get("/")

    assert respuesta.status_code == 200
    html = respuesta.text
    assert "sidebar" in html
    assert "Contexto de Negocio" in html
    assert 'class="nav-item active"' in html


def test_service_worker_se_sirve_desde_la_raiz(cliente):
    respuesta = cliente.get("/sw.js")

    assert respuesta.status_code == 200
    assert "javascript" in respuesta.headers["content-type"]


def test_traduccion_inexistente_devuelve_la_clave():
    assert traducir("clave.inexistente") == "clave.inexistente"


def test_idioma_no_disponible_usa_espanol():
    assert traducir("menu.inicio", idioma="xx") == "Inicio"
