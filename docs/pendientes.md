# Pendientes

## Decisiones abiertas

| # | Tema | Situación |
|---|------|-----------|
| 1 | **Retención de métricas** | Hoy 30 días (`MONITOR_RETENCION_DIAS`); a confirmar. |

## Por implementar (alineación con el TFI v10)

En este orden, cada uno con sus pruebas:

1. **Todo cambio pasa por el Sandbox:** el chat deja de ejecutar INSERT, UPDATE y DELETE
   directo; se validan en el Sandbox y se promueven.
2. **Idiomas separados al 100 %:** sin textos en inglés en la versión en español, con la
   terminología oficial de Microsoft, y recorrido de todas las pantallas en los dos idiomas.

## Fuera de alcance

- Restaurar versión anterior de schema (CU-000-008-C, quitado del TFI).
- Firma digital del PDF de auditoría.
- Despliegue en Azure: la aplicación queda en Docker.

## Pendientes de infraestructura

- **MongoDB sin autenticación en desarrollo:** el puerto 27017 queda publicado sin usuario.
  Antes de exponer el entorno hay que cerrarlo o activar la autenticación.

- **Túnel público para probar push y biometría en el celular:** el servicio `tunel`
  (Cloudflare Tunnel) ya está en `docker-compose.yml`; falta el dominio propio. Pasos en
  `docs/tunel.md`. Es solo para desarrollo: no forma parte del despliegue.

## Para investigar más adelante

- Herramientas de calidad: evaluar sumar chequeo de tipos (mypy o pyright) y linter para JavaScript y plantillas.
- Recursos del sandbox: cada contenedor SQL Server consume ~2 GB de RAM; con un contenedor por IP registrada hay que dimensionar el host.
- Adaptación móvil (PWA) del diseño: el prototipo no define reglas responsive.
- Notificaciones push: generación y resguardo de las claves.
