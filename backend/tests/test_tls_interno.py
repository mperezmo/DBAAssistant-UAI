"""Certificado del tramo interno NGINX → Uvicorn."""

from datetime import UTC, datetime, timedelta

from cryptography import x509

from app import cli
from app.core import tls_interno


def test_genera_una_vez_y_renueva_al_vencer(tmp_path, monkeypatch):
    certificado, clave = tmp_path / "tls" / "backend.crt", tmp_path / "tls" / "backend.key"
    assert tls_interno.generar_si_falta(certificado, clave, "backend")
    leido = x509.load_pem_x509_certificate(certificado.read_bytes())
    nombres = leido.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "backend" in nombres.get_values_for_type(x509.DNSName)
    assert oct(clave.stat().st_mode & 0o777) == "0o600"
    # Vigente: no se toca.
    assert not tls_interno.generar_si_falta(certificado, clave, "backend")
    # Por vencer o ilegible: se genera de nuevo.
    monkeypatch.setattr(tls_interno, "RENOVAR_ANTES", timedelta(days=10_000))
    assert tls_interno.generar_si_falta(certificado, clave, "backend")
    monkeypatch.setattr(tls_interno, "RENOVAR_ANTES", timedelta(days=30))
    certificado.write_text("roto")
    assert tls_interno.generar_si_falta(certificado, clave, "backend")
    assert x509.load_pem_x509_certificate(certificado.read_bytes()).not_valid_after_utc > (
        datetime.now(UTC) + timedelta(days=800)
    )


def test_comando_certificado_interno(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TLS_INTERNO_CERTIFICADO", str(tmp_path / "b.crt"))
    monkeypatch.setenv("TLS_INTERNO_CLAVE", str(tmp_path / "b.key"))
    cli.obtener_configuracion.cache_clear()
    try:
        assert cli.main(["certificado-interno"]) == 0
        assert "generado" in capsys.readouterr().out
        assert cli.main(["certificado-interno"]) == 0
        assert capsys.readouterr().out == ""
    finally:
        cli.obtener_configuracion.cache_clear()
