# Guía de desarrollo

Reglas, forma de trabajo y estado del proyecto para retomar el desarrollo.

## Reglas

1. **Fidelidad al diagrama de arquitectura** (`docs/arquitectura.png`). Todo desvío se
   consulta antes y se documenta en `docs/arquitectura.md`.
2. **No asumir.** Ante una duda funcional o técnica, se consulta antes de implementar.
3. El diseño visual de referencia es `design/DBA Assistant.html`.
4. Todo en **español**: código, comentarios, documentación y commits. Los textos de la
   interfaz van en `backend/app/web/i18n/` (`es.json` y `en.json`, siempre con las mismas
   claves y marcadores; una prueba lo verifica).
5. Comentarios profesionales y medidos: explicar el porqué, no lo obvio.
6. Autor de los commits: `mperezmo <martinpmoreno01@gmail.com>`.
7. Nunca versionar `.env` ni secretos.

## Forma de trabajo

- Por sprint: implementación → tests → commit (Conventional Commits) → push a `dev`
  → merge a `main` y tag `vX.Y.0` al cerrar el sprint.
- Ramas: `main` (estable) y `dev` (integración).
- Antes de cada push: `uv run ruff check .`, `uv run ruff format --check .` y `uv run pytest`
  (desde `backend/`). Cobertura mínima: 80%. Pruebas de integración (SQL Server y MongoDB
  reales): `uv run pytest -m integracion`.

## Estructura

- `backend/app/core/`: configuración, secretos (interfaz para Key Vault), logging, contexto
  de la petición y bitácora de excepciones.
- `backend/app/core/errores.py`: catálogo de códigos de error (nomenclatura del TFI).
- `backend/app/datos/`: SQL Server (pyodbc), MongoDB (PyMongo async), Redis, correo, WinRM y
  migraciones.
- `backend/app/identidad/`: Auth0 (OIDC y Management API), sesiones, login, usuarios y
  reautenticación con segundo factor.
- `backend/app/tenants/`: catálogo y aprovisionamiento de tenants; `backend/app/cli.py`: comandos.
- `backend/app/modulos/`: un paquete por bloque del diagrama, en capas `api`, `servicios`,
  `repositorios` y `entidades`.
- `backend/app/sandbox_servicio/`: servicio Sandbox (contenedores Docker y sqlpackage).
- `backend/app/web/`: plantillas Jinja2, estáticos, i18n y menú.
- `backend/tailwind/`: estilos (tokens del diseño + Tailwind).
- `nginx/`: reverse proxy con TLS 1.3.

## Estado

- **Sprint 0 (fundaciones):** estructura, Docker Compose, NGINX TLS 1.3, chequeo de
  salud de dependencias, layout del diseño, PWA base, CI.
- **Sprint 1 ✅ (`v0.1.0`):** multi-tenant, bitácora de auditoría, catálogo de errores,
  login con Auth0 + MFA por subdominio, sesiones en Redis, menú por rol, Panel Admin con
  estado de servicios y gestión de usuarios.
- **Sprint 2 ✅ (`v0.2.0`):** conexiones a SQL Server (CU-000-000), credenciales cifradas,
  WinRM y reautenticación con segundo factor para PROD.
- **Sprint 3 ✅ (`v0.3.0`):** monitoreo (CU-000-003): recolector en segundo plano,
  métricas por DMVs, histórico en MongoDB y umbrales por conexión.
- **Sprint 4 ✅ (`v0.4.0`):** alertas (CU-000-004): motor de umbrales, eventos y reglas,
  desestimar condiciones esperadas, Web Push con VAPID y correo SMTP.
- **Sprints 5 y 6 (en `dev`, pendientes de QA):** Workarounds (CU-000-005) y Sandbox
  (CU-000-002), con validación de workarounds propios en Sandbox para PROD.
- **Sprint 7 (en `dev`, pendiente de QA):** Contexto de Negocio (CU-000-006) y Chat IA
  (CU-000-001); panel de Inicio.
- **Sprint 8 (en `dev`, pendiente de QA):** Auditoría (CU-000-009), bitácora de
  excepciones e Histórico de Schema (CU-000-008 A y B).
- **Sprint 9 (en `dev`, pendiente de QA):** Buenas Prácticas (CU-000-007 A y B).
- **Pendiente:** biometría en la PWA, push en el celular
  (dominio + túnel) y despliegue en Azure.
- Casos de uso: TFI (`DBAAssistant-PerezMoreno.pdf`, no versionado) y propuestas en
  `docs/casos-de-uso-propuestos.md`.
- Decisiones abiertas: `docs/pendientes.md`.
