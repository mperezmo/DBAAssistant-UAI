"""Menú lateral de la aplicación, tal como lo define el diseño de referencia."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ItemMenu:
    id: str
    clave: str
    icono: str
    ruta: str | None = None  # None mientras el módulo no esté implementado


@dataclass(frozen=True)
class SeccionMenu:
    clave: str
    items: tuple[ItemMenu, ...]


MENU: tuple[SeccionMenu, ...] = (
    SeccionMenu(
        "menu.operaciones",
        (
            ItemMenu("inicio", "menu.inicio", "home", "/"),
            ItemMenu("chat", "menu.chat", "chat"),
            ItemMenu("monitor", "menu.monitor", "monitor", "/monitor"),
            ItemMenu("alertas", "menu.alertas", "bell", "/alertas"),
            ItemMenu("workarounds", "menu.workarounds", "play"),
            ItemMenu("sandbox", "menu.sandbox", "sandbox"),
        ),
    ),
    SeccionMenu(
        "menu.conocimiento",
        (
            ItemMenu("contexto", "menu.contexto", "book"),
            ItemMenu("schema", "menu.schema", "history"),
            ItemMenu("auditoria", "menu.auditoria", "audit"),
        ),
    ),
    SeccionMenu(
        "menu.sistema",
        (
            ItemMenu("conexiones", "menu.conexiones", "db", "/conexiones"),
            ItemMenu("admin", "menu.admin", "settings", "/admin"),
        ),
    ),
)

# Trazos SVG de los íconos de línea del diseño (viewBox 0 0 20 20).
ICONOS = {
    "home": "M3 11 L10 4 L17 11 M5 9 V17 H15 V9",
    "plus": "M10 4 V16 M4 10 H16",
    "chat": "M3 4 H17 V13 H10 L6 17 V13 H3 Z",
    "monitor": "M2 10 L5 10 L7 4 L11 16 L13 8 L15 10 L18 10",
    "bell": "M5 14 V9 A5 5 0 0 1 15 9 V14 L17 16 H3 Z M8 18 H12",
    "play": "M6 4 L16 10 L6 16 Z",
    "sandbox": "M4 4 H16 V16 H4 Z M4 8 H16 M8 4 V16 M12 4 V16",
    "book": "M3 4 H10 A2 2 0 0 1 12 6 V17 A1.5 1.5 0 0 0 13.5 15.5 H17 V4 M3 4 V17 H10",
    "history": "M10 4 A6 6 0 1 1 4 10 M4 4 V8 H8 M10 7 V10 L13 12",
    "audit": "M4 3 H14 L17 6 V17 H4 Z M7 9 H14 M7 12 H14 M7 15 H11",
    "settings": (
        "M10 7 A3 3 0 1 0 10 13 A3 3 0 0 0 10 7 Z M10 2 V4 M10 16 V18 M2 10 H4 M16 10 H18 "
        "M4.2 4.2 L5.6 5.6 M14.4 14.4 L15.8 15.8 M4.2 15.8 L5.6 14.4 M14.4 5.6 L15.8 4.2"
    ),
    "db": (
        "M4 5 C4 3 6 2 10 2 C14 2 16 3 16 5 V15 C16 17 14 18 10 18 C6 18 4 17 4 15 Z "
        "M4 5 C4 7 6 8 10 8 C14 8 16 7 16 5 M4 10 C4 12 6 13 10 13 C14 13 16 12 16 10"
    ),
}
