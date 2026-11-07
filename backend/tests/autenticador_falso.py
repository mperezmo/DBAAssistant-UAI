"""Autenticador WebAuthn en software para las pruebas: crea passkeys y firma desafíos
como lo haría el celular (clave EC P-256, atestación "none", verificación de usuario)."""

import hashlib
import json
import os
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

PRESENCIA, VERIFICACION, CON_CREDENCIAL = 0x01, 0x04, 0x40


class AutenticadorFalso:
    def __init__(self, rp_id: str, origen: str):
        self.rp_id, self.origen = rp_id, origen
        self.clave = ec.generate_private_key(ec.SECP256R1())
        self.credencial_id = os.urandom(32)
        self.contador = 0

    def _datos_cliente(self, tipo: str, desafio: str) -> bytes:
        return json.dumps(
            {"type": tipo, "challenge": desafio, "origin": self.origen, "crossOrigin": False}
        ).encode()

    def _datos_autenticador(self, banderas: int, extra: bytes = b"") -> bytes:
        rp = hashlib.sha256(self.rp_id.encode()).digest()
        return rp + bytes([banderas]) + struct.pack(">I", self.contador) + extra

    def crear(self, opciones: dict) -> dict:
        """Respuesta a navigator.credentials.create() con las opciones del servidor."""
        numeros = self.clave.public_key().public_numbers()
        cose = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numeros.x.to_bytes(32, "big"),
                -3: numeros.y.to_bytes(32, "big"),
            }
        )
        credencial = (
            bytes(16) + struct.pack(">H", len(self.credencial_id)) + self.credencial_id + cose
        )
        datos = self._datos_autenticador(PRESENCIA | VERIFICACION | CON_CREDENCIAL, credencial)
        atestacion = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": datos})
        identificador = bytes_to_base64url(self.credencial_id)
        return {
            "id": identificador,
            "rawId": identificador,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(
                    self._datos_cliente("webauthn.create", opciones["challenge"])
                ),
                "attestationObject": bytes_to_base64url(atestacion),
            },
            "clientExtensionResults": {},
        }

    def firmar(self, opciones: dict) -> dict:
        """Respuesta a navigator.credentials.get(): firma el desafío con la clave privada."""
        self.contador += 1
        datos = self._datos_autenticador(PRESENCIA | VERIFICACION)
        cliente = self._datos_cliente("webauthn.get", opciones["challenge"])
        firma = self.clave.sign(datos + hashlib.sha256(cliente).digest(), ec.ECDSA(hashes.SHA256()))
        identificador = bytes_to_base64url(self.credencial_id)
        assert any(
            base64url_to_bytes(c["id"]) == self.credencial_id
            for c in opciones.get("allowCredentials", [])
        )
        return {
            "id": identificador,
            "rawId": identificador,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(cliente),
                "authenticatorData": bytes_to_base64url(datos),
                "signature": bytes_to_base64url(firma),
                "userHandle": None,
            },
            "clientExtensionResults": {},
        }
