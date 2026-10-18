# CLAUDE.md

Guía para retomar el proyecto en cada sesión.

## Reglas

1. **Primera ley: fidelidad al diagrama de arquitectura** (`docs/arquitectura.png`).
   Todo desvío se consulta antes y se documenta en `docs/arquitectura.md`.
2. **No asumir.** Ante una duda funcional o técnica, preguntar antes de implementar.
3. El diseño visual de referencia es `design/DBA Assistant.html`.
4. Todo en **español**: código, comentarios, documentación y commits.
   Los textos de la interfaz van en `backend/app/web/i18n/` (luego se suma inglés).
5. Comentarios profesionales y medidos: explicar el porqué, no lo obvio.
6. **Sin rastros de IA** en el repositorio: nada de menciones en código, comentarios
   o commits, sin trailers `Co-Authored-By` ni enlaces de sesión. Autor de los
   commits: `mperezmo <martinpmoreno01@gmail.com>`. Avisar al usuario si algo
   pudiera dejar rastro.
7. Nunca versionar `.env` ni secretos.

## Forma de trabajo

- Por sprint: implementación → tests → commit (Conventional Commits) → push a `dev`
  → merge a `main` y tag `vX.Y.0` al cerrar el sprint.
- Ramas: `main` (estable) y `dev` (integración). Ramas de trabajo sin prefijos de herramientas.
- Antes de cada push: `uv run ruff check .`, `uv run ruff format --check .` y `uv run pytest`
  (desde `backend/`). Cobertura mínima: 80%.

## Estructura

- `backend/app/core/`: configuración, secretos (interfaz para Key Vault) y logging.
- `backend/app/datos/`: SQL Server (pyodbc), MongoDB (PyMongo async), Redis y migraciones.
- `backend/app/identidad/`: Auth0 (OIDC y Management API), sesiones y login.
- `backend/app/tenants/`: catálogo y aprovisionamiento de tenants; `backend/app/cli.py`: comandos.
- `backend/app/core/errores.py`: catálogo de códigos de error (nomenclatura del TFI).
- `backend/app/modulos/`: un paquete por bloque del diagrama, en capas `api`, `servicios`,
  `repositorios` y `entidades`.
- `backend/app/web/`: plantillas Jinja2, estáticos, i18n y menú.
- `backend/tailwind/`: estilos (tokens del diseño + Tailwind).
- `nginx/`: reverse proxy con TLS 1.3.

## Estado

- **Sprint 0 (fundaciones):** estructura, Docker Compose, NGINX TLS 1.3, chequeo de
  salud de dependencias, layout del diseño, PWA base, CI.
- **Sprint 1 ✅ (`v0.1.0`):** multi-tenant (catálogo, base SQL y Mongo por tenant,
  migraciones automáticas, `crear-tenant` con organización y URLs en Auth0), bitácora
  de auditoría, catálogo de errores, login con Auth0 + MFA por subdominio, sesiones en
  Redis (8 h fijas), menú por rol, Panel Admin con estado de servicios y gestión de
  usuarios (baja con 30 días de gracia; el rol se relee en cada petición).
- **Próximo — Sprint 2:** conexiones a SQL Server (CU-000-000).
- Casos de uso: TFI (`DBAAssistant-PerezMoreno.pdf`, no versionado) y propuestas en
  `docs/casos-de-uso-propuestos.md`.
- Decisiones abiertas: `docs/pendientes.md`.
