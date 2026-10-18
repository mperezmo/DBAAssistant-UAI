# Pendientes

## Decisiones abiertas

| # | Tema | Situación |
|---|------|-----------|
| 1 | **Roles** | El diseño define 5 roles: DBA Senior, DBA Junior, Desarrollador, Usuario de Negocio y Administrador, cada uno con sus módulos habilitados. Se había propuesto un esquema de 3 (Administrador, DBA, Consulta). Falta confirmar cuál se usa. |
| 2 | **MongoDB: Docker local o Atlas** | Provisoriamente en Docker local; el diagrama indica Atlas. |
| 3 | **Copia de la base de la app en el SQL Server local** | Definir si alcanza con acceder por SSMS al contenedor (`localhost,14330`) o si se necesita una copia en otra instancia, y con qué mecanismo. |
| 4 | **Migraciones del esquema SQL Server** | Elegir entre scripts SQL versionados o una herramienta de migraciones. |
| 5 | **TTL de caché editables desde la app** | Hoy se configuran por variables de entorno. |
| 6 | **Publicación de imágenes Docker** | El pipeline del diagrama incluye *Docker push*; queda diferido hasta definir el registro (Azure Container Registry al migrar). |

## Para investigar más adelante

- Herramientas de calidad: evaluar sumar chequeo de tipos (mypy o pyright) y linter para JavaScript y plantillas.
- Recursos del sandbox: cada contenedor SQL Server consume ~2 GB de RAM; con un contenedor por IP registrada hay que dimensionar el host.
- Adaptación móvil (PWA) del diseño: el prototipo no define reglas responsive; la navegación móvil se agrega en el sprint correspondiente.
- Notificaciones push (VAPID): generación y resguardo de las claves.
- Visor de logs dentro de la app (módulo Auditoría), dado que MongoDB no es cómodo para soporte.
