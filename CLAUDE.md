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
- `backend/app/datos/`: SQL Server (pyodbc), MongoDB (PyMongo async) y Redis.
- `backend/app/modulos/`: un paquete por bloque del backend del diagrama.
- `backend/app/web/`: plantillas Jinja2, estáticos, i18n y menú.
- `backend/tailwind/`: estilos (tokens del diseño + Tailwind).
- `nginx/`: reverse proxy con TLS 1.3.

## Estado

- **Sprint 0 (fundaciones):** estructura, Docker Compose, NGINX TLS 1.3, chequeo de
  salud de dependencias, layout del diseño, PWA base, CI.
- Próximo: casos de uso de negocio (los provee el usuario).
- Decisiones abiertas: `docs/pendientes.md`.
