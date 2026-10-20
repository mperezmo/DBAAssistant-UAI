"""Envío de correo por SMTP (Gmail, Outlook, Azure Communication Services, etc.).

smtplib es sincrónico: desde código async se invoca con ``run_in_threadpool``.
"""

import smtplib
from email.message import EmailMessage

from app.core.config import obtener_configuracion
from app.core.secretos import SecretoNoEncontradoError, proveedor_secretos

TIMEOUT_SEGUNDOS = 15


def _password() -> str | None:
    try:
        return proveedor_secretos().obtener("smtp_password")
    except SecretoNoEncontradoError:
        return None


def configurado() -> bool:
    cfg = obtener_configuracion()
    return bool(cfg.smtp_host and cfg.smtp_remitente)


def enviar(destinatarios: list[str], asunto: str, texto: str) -> None:
    """Envía un mensaje de texto. Los destinatarios van en copia oculta entre sí."""
    cfg = obtener_configuracion()
    mensaje = EmailMessage()
    mensaje["From"] = cfg.smtp_remitente
    mensaje["To"] = cfg.smtp_remitente
    mensaje["Subject"] = asunto
    mensaje.set_content(texto)
    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_puerto, timeout=TIMEOUT_SEGUNDOS) as smtp:
        if cfg.smtp_starttls:
            smtp.starttls()
        password = _password()
        if cfg.smtp_usuario and password:
            smtp.login(cfg.smtp_usuario, password)
        smtp.send_message(mensaje, to_addrs=destinatarios)
