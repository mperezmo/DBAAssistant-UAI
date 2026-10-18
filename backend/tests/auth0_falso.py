"""Doble de Auth0: emite JWT firmados con una clave RSA propia y responde la API."""

import json
import time
from urllib.parse import parse_qs

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

DOMINIO = "dba-test.us.auth0.com"
EMISOR = f"https://{DOMINIO}/"
CLIENT_ID = "cliente-web"
AUDIENCIA = "https://api.dba-assistant"
ORGANIZACION = "org_acme"


class Auth0Falso:
    def __init__(self):
        self.clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.clave.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": "k1", "alg": "RS256", "use": "sig"}]}
        self.identidad = {}
        self.llamadas: list[tuple[str, str, dict]] = []
        self.cliente_web = {
            "callbacks": ["https://otra.localhost/auth/callback"],
            "allowed_logout_urls": [],
            "web_origins": None,
        }

    def firmar(self, **claims) -> str:
        base = {"iss": EMISOR, "iat": int(time.time()), "exp": int(time.time()) + 600}
        return jwt.encode({**base, **claims}, self.clave, algorithm="RS256", headers={"kid": "k1"})

    def responder(self, solicitud: httpx2.Request) -> httpx2.Response:
        ruta = solicitud.url.path
        cuerpo = solicitud.content.decode() if solicitud.content else ""
        datos = (
            parse_qs(cuerpo)
            if "x-www-form-urlencoded" in solicitud.headers.get("content-type", "")
            else (json.loads(cuerpo) if cuerpo else {})
        )
        self.llamadas.append((solicitud.method, ruta, datos))

        if ruta == "/.well-known/jwks.json":
            return httpx2.Response(200, json=self.jwks)
        if ruta == "/oauth/token" and datos.get("grant_type") == ["client_credentials"]:
            return httpx2.Response(200, json={"access_token": "token-admin"})
        if ruta == "/oauth/token":
            return httpx2.Response(
                200,
                json={
                    "id_token": self.firmar(aud=CLIENT_ID, **self.identidad),
                    "access_token": self.firmar(aud=AUDIENCIA, org_id=self.identidad.get("org_id")),
                },
            )
        if ruta == "/api/v2/organizations":
            return httpx2.Response(201, json={"id": ORGANIZACION})
        if ruta == "/api/v2/connections":
            nombre = solicitud.url.params["name"]
            return httpx2.Response(200, json=[{"id": f"con_{nombre}"}])
        if ruta == f"/api/v2/clients/{CLIENT_ID}" and solicitud.method == "GET":
            return httpx2.Response(200, json=self.cliente_web)
        if ruta == f"/api/v2/clients/{CLIENT_ID}" and solicitud.method == "PATCH":
            self.cliente_web.update(datos)
            return httpx2.Response(200, json=self.cliente_web)
        if ruta.endswith("/enabled_connections"):
            return httpx2.Response(201, json={})
        return httpx2.Response(404)

    def transporte(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.responder)
