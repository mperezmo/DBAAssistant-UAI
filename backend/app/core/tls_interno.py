"""Certificado del tramo interno NGINX → Uvicorn (HTTPS, como indica el diagrama).

En desarrollo y en Docker Compose el backend genera su propio certificado autofirmado
(clave EC P-256) si no se montó uno; NGINX lo toma como única autoridad de confianza para
ese tramo y verifica el nombre del servicio. Se renueva solo cuando está por vencer.
"""

import ipaddress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

VIGENCIA = timedelta(days=825)
RENOVAR_ANTES = timedelta(days=30)


def _vigente(certificado: Path, clave: Path) -> bool:
    if not certificado.is_file() or not clave.is_file():
        return False
    try:
        actual = x509.load_pem_x509_certificate(certificado.read_bytes())
    except ValueError:
        return False
    return actual.not_valid_after_utc - datetime.now(UTC) > RENOVAR_ANTES


def generar_si_falta(certificado: Path, clave: Path, nombre: str) -> bool:
    """Genera el certificado si falta, es ilegible o está por vencer. True si lo generó."""
    if _vigente(certificado, clave):
        return False
    privada = ec.generate_private_key(ec.SECP256R1())
    sujeto = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nombre)])
    ahora = datetime.now(UTC)
    nuevo = (
        x509.CertificateBuilder()
        .subject_name(sujeto)
        .issuer_name(sujeto)
        .public_key(privada.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ahora - timedelta(minutes=5))
        .not_valid_after(ahora + VIGENCIA)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(nombre),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_cert_sign=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(privada, hashes.SHA256())
    )
    certificado.parent.mkdir(parents=True, exist_ok=True)
    clave.parent.mkdir(parents=True, exist_ok=True)
    clave.write_bytes(
        privada.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    clave.chmod(0o600)
    certificado.write_bytes(nuevo.public_bytes(serialization.Encoding.PEM))
    return True
