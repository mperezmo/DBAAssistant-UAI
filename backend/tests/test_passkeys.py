"""Passkeys (WebAuthn): registro desde Mi perfil y confirmación biométrica del segundo factor."""

import json
import time
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.identidad import passkeys, sesiones
from app.identidad import repositorios_passkeys as repositorio
from tests.autenticador_falso import AutenticadorFalso
from tests.test_usuarios import (  # noqa: F401
    CABECERA,
    _navegador_con_sesion,
    dispositivos,
    entorno,
)


@pytest.fixture
def guardadas(monkeypatch):
    """Repositorio de passkeys en memoria."""
    lista: dict[str, repositorio.Passkey] = {}

    def crear(base, tenant_id, usuario_id, credencial_id, clave, contador, nombre):
        nuevo = str(len(lista) + 1).rjust(12, "0")
        identificador = f"00000000-0000-0000-0000-{nuevo}"
        lista[identificador] = repositorio.Passkey(
            identificador,
            usuario_id,
            credencial_id,
            clave,
            contador,
            nombre,
            datetime.now(UTC),
            None,
        )

    def registrar_uso(base, tenant_id, passkey_id, contador):
        lista[passkey_id] = replace(
            lista[passkey_id], contador=contador, ultimo_uso=datetime.now(UTC)
        )

    def eliminar(base, tenant_id, usuario_id, passkey_id):
        p = lista.get(passkey_id)
        return bool(p and p.usuario_id == usuario_id and lista.pop(passkey_id))

    monkeypatch.setattr(
        repositorio, "listar", lambda b, t, u: [p for p in lista.values() if p.usuario_id == u]
    )
    monkeypatch.setattr(repositorio, "crear", crear)
    monkeypatch.setattr(repositorio, "registrar_uso", registrar_uso)
    monkeypatch.setattr(repositorio, "eliminar", eliminar)
    return lista


async def _navegador(entorno, segundo_factor: bool):  # noqa: F811
    navegador = await _navegador_con_sesion(entorno["admin"])
    if segundo_factor:
        await sesiones.registrar_segundo_factor(navegador.cookies[sesiones.COOKIE])
    return navegador


async def _sesion(navegador):
    return await sesiones.obtener(navegador.cookies[sesiones.COOKIE])


async def test_registrar_y_confirmar_con_biometria(entorno, guardadas):  # noqa: F811
    celular = AutenticadorFalso("acme.localhost", "https://acme.localhost")
    navegador = await _navegador(entorno, segundo_factor=False)

    # Registrar una passkey exige el segundo factor de Auth0 reciente.
    assert navegador.post("/api/perfil/passkeys/opciones", headers=CABECERA).json()["codigo"] == (
        "AMB_VAL_000"
    )
    await sesiones.registrar_segundo_factor(navegador.cookies[sesiones.COOKIE])
    opciones = navegador.post("/api/perfil/passkeys/opciones", headers=CABECERA).json()
    assert opciones["rp"]["id"] == "acme.localhost"
    assert opciones["authenticatorSelection"]["userVerification"] == "required"
    respuesta = navegador.post(
        "/api/perfil/passkeys",
        json={"credencial": celular.crear(opciones), "nombre": "Mi celular"},
        headers=CABECERA,
    )
    assert respuesta.status_code == 204
    [registrada] = navegador.get("/api/perfil/passkeys").json()["passkeys"]
    assert registrada["nombre"] == "Mi celular" and registrada["ultimo_uso"] is None
    assert entorno["eventos"][-1] == ("usuario.passkey_registrada", {"nombre": "Mi celular"})
    # El desafío de registro se usa una sola vez.
    otra = navegador.post(
        "/api/perfil/passkeys", json={"credencial": celular.crear(opciones)}, headers=CABECERA
    )
    assert otra.json()["codigo"] == "USR_AUT_007"

    # Más tarde, sin segundo factor vigente: la biometría lo confirma sin pasar por Auth0.
    sesion = await _sesion(navegador)
    await _vencer_segundo_factor(navegador)
    desafio = navegador.post("/api/perfil/passkeys/desafio", headers=CABECERA).json()
    firma = celular.firmar(desafio)
    confirmada = navegador.post(
        "/api/perfil/passkeys/confirmar", json={"credencial": firma}, headers=CABECERA
    )
    assert confirmada.status_code == 204
    despues = await _sesion(navegador)
    assert despues.segundo_factor_en and time.time() - despues.segundo_factor_en < 5
    assert despues.segundo_factor_en != sesion.segundo_factor_en
    assert entorno["eventos"][-1] == (
        "sesion.segundo_factor_confirmado",
        {"metodo": "passkey", "passkey": "Mi celular"},
    )
    [usada] = guardadas.values()
    assert usada.contador == 1 and usada.ultimo_uso is not None

    # La misma firma no se puede reutilizar (desafío consumido).
    repetida = navegador.post(
        "/api/perfil/passkeys/confirmar", json={"credencial": firma}, headers=CABECERA
    )
    assert repetida.json()["codigo"] == "USR_AUT_007"


async def _vencer_segundo_factor(navegador):
    clave = sesiones.PREFIJO_SESION + navegador.cookies[sesiones.COOKIE]
    redis = sesiones._redis()
    registro = json.loads(await redis.get(clave))
    registro["sesion"]["segundo_factor_en"] = time.time() - 3600
    await redis.set(clave, json.dumps(registro), keepttl=True)


async def test_firma_invalida_o_de_otro_dispositivo(entorno, guardadas):  # noqa: F811
    celular = AutenticadorFalso("acme.localhost", "https://acme.localhost")
    navegador = await _navegador(entorno, segundo_factor=True)
    opciones = navegador.post("/api/perfil/passkeys/opciones", headers=CABECERA).json()
    navegador.post(
        "/api/perfil/passkeys", json={"credencial": celular.crear(opciones)}, headers=CABECERA
    )

    # Un sitio falso (otro origen) no puede usar la passkey.
    impostor = AutenticadorFalso("acme.localhost", "https://acme-falso.example")
    impostor.clave, impostor.credencial_id = celular.clave, celular.credencial_id
    desafio = navegador.post("/api/perfil/passkeys/desafio", headers=CABECERA).json()
    respuesta = navegador.post(
        "/api/perfil/passkeys/confirmar",
        json={"credencial": impostor.firmar(desafio)},
        headers=CABECERA,
    )
    assert respuesta.json()["codigo"] == "USR_AUT_007"
    # Una passkey que no es del usuario.
    ajeno = AutenticadorFalso("acme.localhost", "https://acme.localhost")
    desafio = navegador.post("/api/perfil/passkeys/desafio", headers=CABECERA).json()
    desafio["allowCredentials"].append({"id": ajeno.credencial_id.hex(), "type": "public-key"})
    firma = ajeno.firmar(
        {**desafio, "allowCredentials": [{"id": _b64(ajeno.credencial_id), "type": "public-key"}]}
    )
    respuesta = navegador.post(
        "/api/perfil/passkeys/confirmar", json={"credencial": firma}, headers=CABECERA
    )
    assert respuesta.json()["codigo"] == "USR_AUT_007"


def _b64(datos: bytes) -> str:
    from webauthn.helpers import bytes_to_base64url

    return bytes_to_base64url(datos)


async def test_sin_passkeys_se_usa_auth0_y_se_pueden_quitar(entorno, dispositivos, guardadas):  # noqa: F811
    navegador = await _navegador(entorno, segundo_factor=True)
    # Sin passkeys registradas el navegador cae a la reautenticación de Auth0.
    assert navegador.post("/api/perfil/passkeys/desafio", headers=CABECERA).json() is None
    assert (
        navegador.post(
            "/api/perfil/passkeys/confirmar", json={"credencial": {}}, headers=CABECERA
        ).json()["codigo"]
        == "USR_AUT_007"
    )

    celular = AutenticadorFalso("acme.localhost", "https://acme.localhost")
    opciones = navegador.post("/api/perfil/passkeys/opciones", headers=CABECERA).json()
    navegador.post(
        "/api/perfil/passkeys", json={"credencial": celular.crear(opciones)}, headers=CABECERA
    )
    [registrada] = navegador.get("/api/perfil").json()["passkeys"]
    # Un segundo registro del mismo dispositivo queda excluido en las opciones.
    opciones = navegador.post("/api/perfil/passkeys/opciones", headers=CABECERA).json()
    assert [c["id"] for c in opciones["excludeCredentials"]] == [_b64(celular.credencial_id)]

    for passkey_id, estado in ((registrada["id"], 204), (registrada["id"], 422), ("x", 422)):
        assert (
            navegador.delete(f"/api/perfil/passkeys/{passkey_id}", headers=CABECERA).status_code
            == estado
        )
    assert guardadas == {}
    assert passkeys.MAXIMO_POR_USUARIO == 10
