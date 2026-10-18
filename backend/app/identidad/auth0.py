"""Cliente de Auth0.

Cubre el flujo *Authorization Code* de OpenID Connect para el ingreso de usuarios,
la validación de los JWT emitidos por Auth0 y las operaciones de la Management API
que necesita el aprovisionamiento automático de tenants.
"""

import time
from typing import Any
from urllib.parse import urlencode

import httpx2
import jwt

from app.core.config import Configuracion, obtener_configuracion
from app.core.errores import ErrorDominio
from app.core.secretos import proveedor_secretos

# Conexiones habilitadas en cada organización: Google y email + contraseña.
CONEXIONES_TENANT = ("google-oauth2", "Username-Password-Authentication")
JWKS_VIGENCIA_SEGUNDOS = 3600


class ClienteAuth0:
    def __init__(
        self,
        cfg: Configuracion | None = None,
        transporte: httpx2.AsyncBaseTransport | None = None,
    ):
        self.cfg = cfg or obtener_configuracion()
        self._transporte = transporte
        self._jwks: tuple[float, jwt.PyJWKSet] | None = None

    @property
    def emisor(self) -> str:
        return f"https://{self.cfg.auth0_domain}/"

    def _http(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url=f"https://{self.cfg.auth0_domain}", transport=self._transporte, timeout=10
        )

    async def _solicitar(self, metodo: str, ruta: str, **kwargs: Any) -> Any:
        try:
            async with self._http() as http:
                respuesta = await http.request(metodo, ruta, **kwargs)
                respuesta.raise_for_status()
        except httpx2.HTTPError as error:
            raise ErrorDominio("AUT_API_000", f"{metodo} {ruta}: {error}") from error
        return respuesta.json() if respuesta.content else None

    # --- Ingreso de usuarios (OpenID Connect) ---------------------------------

    def url_autorizacion(self, redirect_uri: str, state: str, nonce: str, organizacion: str) -> str:
        parametros = {
            "response_type": "code",
            "client_id": self.cfg.auth0_client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email",
            "audience": self.cfg.auth0_audience,
            "state": state,
            "nonce": nonce,
            "organization": organizacion,
        }
        return f"{self.emisor}authorize?{urlencode(parametros)}"

    def url_cierre_sesion(self, volver_a: str) -> str:
        parametros = {"client_id": self.cfg.auth0_client_id, "returnTo": volver_a}
        return f"{self.emisor}v2/logout?{urlencode(parametros)}"

    async def intercambiar_codigo(self, codigo: str, redirect_uri: str) -> dict[str, Any]:
        return await self._solicitar(
            "POST",
            "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "client_id": self.cfg.auth0_client_id,
                "client_secret": proveedor_secretos().obtener("auth0_client_secret"),
                "code": codigo,
                "redirect_uri": redirect_uri,
            },
        )

    async def _claves(self) -> jwt.PyJWKSet:
        if self._jwks is None or time.monotonic() - self._jwks[0] > JWKS_VIGENCIA_SEGUNDOS:
            datos = await self._solicitar("GET", "/.well-known/jwks.json")
            self._jwks = (time.monotonic(), jwt.PyJWKSet.from_dict(datos))
        return self._jwks[1]

    async def validar_token(self, token: str, audiencia: str) -> dict[str, Any]:
        """Verifica firma RS256, emisor, audiencia y vencimiento del JWT."""
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            clave = (await self._claves())[kid]
            return jwt.decode(
                token, clave.key, algorithms=["RS256"], audience=audiencia, issuer=self.emisor
            )
        except (jwt.PyJWTError, KeyError) as error:
            raise ErrorDominio("USR_AUT_000", f"Token inválido: {error}") from error

    # --- Management API (aprovisionamiento) ----------------------------------

    async def _token_administracion(self) -> str:
        respuesta = await self._solicitar(
            "POST",
            "/oauth/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.cfg.auth0_admin_client_id,
                "client_secret": proveedor_secretos().obtener("auth0_admin_client_secret"),
                "audience": f"{self.emisor}api/v2/",
            },
        )
        return respuesta["access_token"]

    async def crear_organizacion(self, nombre: str, nombre_visible: str) -> str:
        """Crea la organización del tenant con sus conexiones y devuelve su id.

        Las conexiones se habilitan con alta automática de miembros: cualquier
        identidad válida puede autenticarse, pero la app solo deja entrar a los
        usuarios dados de alta en la base del tenant.
        """
        cabeceras = {"Authorization": f"Bearer {await self._token_administracion()}"}
        organizacion = await self._solicitar(
            "POST",
            "/api/v2/organizations",
            headers=cabeceras,
            json={"name": nombre, "display_name": nombre_visible},
        )
        for conexion in CONEXIONES_TENANT:
            encontradas = await self._solicitar(
                "GET", "/api/v2/connections", headers=cabeceras, params={"name": conexion}
            )
            if not encontradas:
                raise ErrorDominio("AUT_API_000", f"No existe la conexión {conexion} en Auth0.")
            await self._solicitar(
                "POST",
                f"/api/v2/organizations/{organizacion['id']}/enabled_connections",
                headers=cabeceras,
                json={"connection_id": encontradas[0]["id"], "assign_membership_on_login": True},
            )
        return organizacion["id"]
