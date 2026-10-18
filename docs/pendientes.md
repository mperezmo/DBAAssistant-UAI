# Pendientes

## Decisiones abiertas

| # | Tema | Situación |
|---|------|-----------|
| 1 | **Casos de uso propuestos** | Aprobar los flujos de `docs/casos-de-uso-propuestos.md`. |
| 2 | **MongoDB: Docker local o Atlas** | Provisoriamente en Docker local. Al migrar a Azure: Atlas desplegado en Azure o Azure Cosmos DB for MongoDB. |
| 3 | **Publicación de imágenes Docker** | Diferido hasta definir el registro (Azure Container Registry al migrar). |
| 4 | **Roles** | Se usan 3 roles; se revisa más adelante frente a los 5 del TFI. |

| 5 | **Retención de métricas** | Cuánto tiempo se conservan las métricas (colección time-series). |

## En espera

- Restaurar versión anterior de schema (CU-000-008-C).
- Alertas por email.
- Firma digital del PDF de auditoría.

## Para investigar más adelante

- Herramientas de calidad: evaluar sumar chequeo de tipos (mypy o pyright) y linter para JavaScript y plantillas.
- Recursos del sandbox: cada contenedor SQL Server consume ~2 GB de RAM; con un contenedor por IP registrada hay que dimensionar el host.
- Adaptación móvil (PWA) del diseño: el prototipo no define reglas responsive.
- Notificaciones push: generación y resguardo de las claves.
