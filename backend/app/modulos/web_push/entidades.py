"""Entidades de Web Push."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Suscripcion:
    endpoint: str
    p256dh: str
    auth: str
    idioma: str | None = None  # el del usuario dueño del dispositivo
