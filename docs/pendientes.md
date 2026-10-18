# Pendientes

## Decisiones abiertas

| # | Tema | Situación |
|---|------|-----------|
| 1 | **Módulos por rol** | Se usan 3 roles (Administrador, DBA, Consulta). Falta confirmar qué módulos habilita cada uno. |
| 2 | **MongoDB: Docker local o Atlas** | Provisoriamente en Docker local; el diagrama indica Atlas. Al migrar a Azure: Atlas desplegado en Azure o Azure Cosmos DB for MongoDB. |
| 3 | **Cambios de estructura de la base SQL Server de la app** | Elegir entre scripts T-SQL numerados o una herramienta de migraciones. |
| 4 | **Publicación de imágenes Docker** | El pipeline del diagrama incluye *Docker push*; queda diferido hasta definir el registro (Azure Container Registry al migrar). |

## Para investigar más adelante

- Herramientas de calidad: evaluar sumar chequeo de tipos (mypy o pyright) y linter para JavaScript y plantillas.
- Recursos del sandbox: cada contenedor SQL Server consume ~2 GB de RAM; con un contenedor por IP registrada hay que dimensionar el host.
- Adaptación móvil (PWA) del diseño: el prototipo no define reglas responsive; la navegación móvil se agrega en el sprint correspondiente.
- Notificaciones push (VAPID): generación y resguardo de las claves.
- Visor de logs dentro de la app (módulo Auditoría), dado que MongoDB no es cómodo para soporte.
