"""Multi idioma: elección por usuario, traducciones y textos armados en el idioma de cada uno."""

import json
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.core import contexto
from app.core.errores import CATALOGO, ErrorDominio
from app.identidad import usuarios
from app.main import app
from app.modulos.alertas import condiciones, notificaciones
from app.modulos.alertas.entidades import texto_visible
from app.modulos.workarounds import integrados
from app.web import i18n
from tests.test_usuarios import CABECERA, TENANT, _navegador_con_sesion, _sesion, entorno  # noqa: F401

MARCADOR = re.compile(r"\{[a-z_0-9]*\}")


def test_mismas_claves_y_marcadores_en_todos_los_idiomas():
    base = i18n.textos("es")
    for idioma in i18n.IDIOMAS:
        archivo = json.loads((i18n.DIRECTORIO / f"{idioma}.json").read_text(encoding="utf-8"))
        assert list(archivo) == list(base), idioma
        distintos = [
            k
            for k in base
            if sorted(MARCADOR.findall(base[k])) != sorted(MARCADOR.findall(archivo[k]))
        ]
        assert distintos == [], idioma
    # Todo error del catálogo tiene su mensaje.
    assert all(f"error.{codigo}" in base for codigo in CATALOGO)


def test_idioma_del_navegador():
    assert i18n.idioma_de_cabecera("en-US,en;q=0.9,es;q=0.8") == "en"
    assert i18n.idioma_de_cabecera("fr-FR, es-AR;q=0.7, en;q=0.5") == "es"
    assert i18n.idioma_de_cabecera("de-DE") == "es"
    assert i18n.idioma_de_cabecera(None) == "es"
    assert i18n.idioma_de_cabecera("en;q=abc") == "es"


def test_traducir_formatear_y_respaldo(monkeypatch):
    monkeypatch.setattr(
        i18n,
        "textos",
        lambda idioma: {"es": {"a": "Hola {x}", "b": "Base"}, "en": {"a": "Hi {x}"}}[idioma],
    )
    with i18n.en_idioma("en"):
        assert i18n.traducir("a") == "Hi {x}"
        assert i18n.traducir("b") == "Base"  # falta en inglés: se usa el español
        assert i18n.traducir("nada") == "nada"
        assert i18n.formatear("a", x="@b") == "Hi Base"
        assert i18n.formatear("a", "es", x=1) == "Hola 1"
    assert contexto.idioma.get() is None
    with i18n.en_idioma("xx") as elegido:
        assert elegido == "es" and i18n.idioma_actual() == "es"


def test_mensajes_y_catalogos_en_el_idioma_actual():
    with i18n.en_idioma("en"):
        assert CATALOGO["USR_VAL_002"].mensaje == i18n.textos("en")["error.USR_VAL_002"]
        assert (
            integrados.CATALOGO["base_online"].nombre
            == i18n.textos("en")["workarounds.integrado.base_online.nombre"]
        )
        assert str(ErrorDominio("CON_DB_002")).startswith("[CON_DB_002]")
    assert CATALOGO["USR_VAL_002"].mensaje == "Datos requeridos no completados."


def test_alerta_guardada_con_clave_se_ve_en_cada_idioma():
    [c] = condiciones.por_eventos(
        SimpleNamespace(
            bases_no_disponibles=[{"base": "Ventas", "estado": "OFFLINE"}], bloqueos=[], logs=[]
        )
    )
    # El texto guardado queda en español (búsquedas y bitácora); la referencia, para mostrar.
    assert c.titulo == "Base Ventas no disponible"
    with i18n.en_idioma("en"):
        assert texto_visible(c.titulo, c.textos, "titulo") == i18n.textos("en")[
            "alertas.evento.base.titulo"
        ].format(base="Ventas")
    # Alertas viejas o de reglas propias: el texto guardado.
    assert texto_visible("Mi regla", None, "titulo") == "Mi regla"
    lectura = SimpleNamespace(valores={"cpu": 99.0})
    [umbral] = condiciones.por_umbrales(lectura, {})
    assert umbral.textos["titulo"][1]["metrica"] == "@monitor.metrica.cpu"


def test_correos_agrupados_por_idioma():
    grupos = notificaciones._por_idioma(
        [("a@x", "en"), ("b@x", None), ("c@x", "es"), ("d@x", "en")]
    )
    assert grupos == {"en": ["a@x", "d@x"], "es": ["b@x", "c@x"]}


async def test_aviso_en_el_idioma_de_cada_destinatario(monkeypatch):
    enviados = []
    monkeypatch.setattr(notificaciones.correo, "configurado", lambda: True)
    monkeypatch.setattr(
        notificaciones.correo, "enviar", lambda para, asunto, texto: enviados.append((para, asunto))
    )
    monkeypatch.setattr(
        notificaciones.usuarios,
        "emails_por_roles",
        lambda base, t, roles: [("a@x", "en"), ("b@x", "es")],
    )

    async def push(tenant, roles, mensaje):
        return 0

    monkeypatch.setattr(notificaciones.push, "enviar", push)
    await notificaciones.avisar(
        TENANT, ("DBA",), lambda: i18n.traducir("idioma.elegir"), lambda: "x", "/inicio"
    )
    assert sorted(enviados) == [
        (["a@x"], i18n.textos("en")["idioma.elegir"]),
        (["b@x"], "Idioma"),
    ]


async def test_cada_usuario_elige_su_idioma(entorno):  # noqa: F811
    navegador = await _navegador_con_sesion(entorno["admin"])

    assert (
        navegador.put("/api/perfil/idioma", json={"idioma": "en"}, headers=CABECERA).status_code
        == 204
    )
    assert entorno["repo"].usuarios[entorno["admin"].id].idioma == "en"
    assert entorno["eventos"][-1] == ("usuario.idioma", {"idioma": "en"})
    # Desde ahora los mensajes salen en inglés, sin importar el navegador.
    respuesta = navegador.patch(
        "/api/usuarios/no-existe",
        json={"rol": "DBA", "ambientes": []},
        headers={**CABECERA, "Accept-Language": "es"},
    )
    assert respuesta.json()["mensaje"] == i18n.textos("en")[f"error.{respuesta.json()['codigo']}"]
    assert (
        navegador.put("/api/perfil/idioma", json={"idioma": "fr"}, headers=CABECERA).status_code
        == 422
    )
    with pytest.raises(ErrorDominio):
        await usuarios.cambiar_idioma(TENANT, _sesion(entorno["admin"]), "fr")


def test_pantallas_sin_sesion_en_el_idioma_del_navegador(entorno):  # noqa: F811
    navegador = TestClient(app, base_url="https://acme.localhost")
    pagina = navegador.get("/ingresar", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert '<html lang="en" data-locale="en-US">' in pagina.text
    manifest = navegador.get("/manifest.webmanifest", headers={"Accept-Language": "en"}).json()
    assert (
        manifest["lang"] == "en" and manifest["description"] == i18n.textos("en")["app.descripcion"]
    )
    assert '<html lang="es"' in navegador.get("/ingresar").text
