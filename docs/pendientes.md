# Pendientes

## Decisiones abiertas

| # | Tema | Situación |
|---|------|-----------|
| 1 | **Modelo multi-tenant** | Preferencia: una base por tenant. Confirmar si además se mantiene `tenant_id`, la integración con Auth0 Organizations y el mecanismo de alta de tenants. |
| 2 | **Casos de uso propuestos** | Aprobar los flujos de `docs/casos-de-uso-propuestos.md`. |
| 3 | **Cambios de estructura de la base SQL Server de la app** | Scripts T-SQL numerados o una herramienta de migraciones. |
| 4 | **MongoDB: Docker local o Atlas** | Provisoriamente en Docker local. Al migrar a Azure: Atlas desplegado en Azure o Azure Cosmos DB for MongoDB. |
| 5 | **Publicación de imágenes Docker** | Diferido hasta definir el registro (Azure Container Registry al migrar). |
| 6 | **Roles** | Se usan 3 roles; se revisa más adelante frente a los 5 del TFI. |

## En espera

- Restaurar versión anterior de schema (CU-000-008-C).
- Alertas por email.
- Firma digital del PDF de auditoría.

## Para investigar más adelante

- Herramientas de calidad: evaluar sumar chequeo de tipos (mypy o pyright) y linter para JavaScript y plantillas.
- Recursos del sandbox: cada contenedor SQL Server consume ~2 GB de RAM; con un contenedor por IP registrada hay que dimensionar el host.
- Adaptación móvil (PWA) del diseño: el prototipo no define reglas responsive.
- Notificaciones push: generación y resguardo de las claves.
