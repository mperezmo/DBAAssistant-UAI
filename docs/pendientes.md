# Pendientes

## Decisiones abiertas

| # | Tema | Situación |
|---|------|-----------|
| 1 | **Modelo multi-tenant** | Definir aislamiento de datos (SQL Server, MongoDB), integración con Auth0 y quién da de alta los tenants. |
| 2 | **Web Push: VAPID o Firebase** | El diagrama indica VAPID (W3C); el TFI menciona Firebase. |
| 3 | **Captura de cambios de schema** | Registrar quién ejecutó qué DDL requiere instalar algo en la instancia del cliente (sesión de Extended Events o trigger DDL). |
| 4 | **Firma digital del PDF de auditoría** | CU-000-009-D: definir certificado o si se omite la firma. |
| 5 | **Histórico de métricas** | Dónde se guarda la serie temporal (colección time-series de MongoDB o SQL Server). |
| 6 | **Casos de uso propuestos** | Aprobar los flujos de `docs/casos-de-uso-propuestos.md`. |
| 7 | **Cambios de estructura de la base SQL Server de la app** | Scripts T-SQL numerados o una herramienta de migraciones. |
| 8 | **MongoDB: Docker local o Atlas** | Provisoriamente en Docker local. Al migrar a Azure: Atlas desplegado en Azure o Azure Cosmos DB for MongoDB. |
| 9 | **Publicación de imágenes Docker** | Diferido hasta definir el registro (Azure Container Registry al migrar). |
| 10 | **Roles** | Se usan 3 roles; se revisa más adelante frente a los 5 del TFI. |

## En espera

- Restaurar versión anterior de schema (CU-000-008-C).
- Alertas por email.

## Para investigar más adelante

- Herramientas de calidad: evaluar sumar chequeo de tipos (mypy o pyright) y linter para JavaScript y plantillas.
- Recursos del sandbox: cada contenedor SQL Server consume ~2 GB de RAM; con un contenedor por IP registrada hay que dimensionar el host.
- Adaptación móvil (PWA) del diseño: el prototipo no define reglas responsive.
- Notificaciones push: generación y resguardo de las claves.
