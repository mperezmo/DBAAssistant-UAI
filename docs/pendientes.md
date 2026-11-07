# Pendientes

## Decisiones abiertas

| # | Tema | Situación |
|---|------|-----------|
| 1 | **Casos de uso propuestos** | Aprobar los flujos de `docs/casos-de-uso-propuestos.md`. |
| 2 | **MongoDB: Docker local o Atlas** | Provisoriamente en Docker local. Al migrar a Azure: Atlas desplegado en Azure o Azure Cosmos DB for MongoDB. |
| 3 | **Publicación de imágenes Docker** | Diferido hasta definir el registro (Azure Container Registry al migrar). |
| 4 | **Roles** | Se usan 3 roles; se revisa más adelante frente a los 5 del TFI. |
| 5 | **Retención de métricas** | Hoy 30 días (`MONITOR_RETENCION_DIAS`); a confirmar. |

## En espera

- Restaurar versión anterior de schema (CU-000-008-C).
- Firma digital del PDF de auditoría.

## Pendientes de infraestructura

- **MongoDB sin autenticación en desarrollo:** el puerto 27017 queda publicado sin usuario.
  Antes de exponer el entorno hay que cerrarlo o activar la autenticación.

- **Túnel público para probar push y biometría en el celular:** el servicio `tunel`
  (Cloudflare Tunnel) ya está en `docker-compose.yml`; falta el dominio propio. Pasos en
  `docs/tunel.md`. Es un desvío temporal del diagrama hasta el despliegue en Azure.

## Para investigar más adelante

- Herramientas de calidad: evaluar sumar chequeo de tipos (mypy o pyright) y linter para JavaScript y plantillas.
- Recursos del sandbox: cada contenedor SQL Server consume ~2 GB de RAM; con un contenedor por IP registrada hay que dimensionar el host.
- Adaptación móvil (PWA) del diseño: el prototipo no define reglas responsive.
- Notificaciones push: generación y resguardo de las claves.
