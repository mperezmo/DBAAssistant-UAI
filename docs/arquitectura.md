# Arquitectura

![Arquitectura DBA Assistant](arquitectura.png)

**Regla principal:** toda decisión técnica se mantiene lo más fiel posible a este
diagrama. Cualquier desvío se documenta acá con su motivo.

## Correspondencia diagrama → código

| Bloque del diagrama | Implementación |
|---|---|
| Web App (HTML5 + Tailwind + Alpine.js) | `backend/app/web/` — plantillas Jinja2 renderizadas por FastAPI, Alpine.js para la interactividad, Tailwind compilado (`backend/tailwind/`) |
| Mobile PWA (Service Worker + Manifest) | Mismo front adaptado: `static/manifest.webmanifest`, `static/sw.js` |
| Auth0 | Pendiente de configuración (ver `docs/auth0.md`) |
| NGINX (Reverse Proxy + SSL/TLS 1.3) | `nginx/` — TLS 1.3, estáticos y proxy al backend |
| Backend FastAPI (Python + Uvicorn ASGI) | `backend/app/` — un único servicio organizado en módulos |
| Módulos del backend | `backend/app/modulos/<modulo>/` (ver tabla siguiente) |
| Redis Cache (self-hosted en Docker) | `backend/app/datos/cache.py`, servicio `redis` |
| SQL Server 2019+ (ODBC) | `backend/app/datos/sqlserver.py` (pyodbc + ODBC Driver 18), servicio `sqlserver` |
| MongoDB 6+ (motor) | `backend/app/datos/mongo.py`, servicio `mongo` |
| Azure Key Vault | `backend/app/core/secretos.py` — hoy lee `.env`; al migrar se agrega el proveedor de Key Vault |
| Anthropic Claude API | Módulo `chat_ia` (modelo configurable, por defecto Claude Haiku 5.5) |
| Sandbox Docker | Módulo `sandbox` |
| GitHub Actions | `.github/workflows/ci.yml` |
| Azure Deploy | Diferido hasta que el sistema esté funcional |

### Módulos del backend

| Diagrama | Paquete |
|---|---|
| Chat IA (NL → T-SQL) | `chat_ia` |
| Monitor de BD (sesiones, locks, performance) | `monitor` |
| Workarounds (playbooks de remediación) | `workarounds` |
| Sandbox Executor (validación previa) | `sandbox` |
| Contexto de Negocio (tablas, glosario, reglas) | `contexto_negocio` |
| Auditoría (bitácoras, logs) | `auditoria` |
| Histórico Schema (versionado del modelo) | `historico_schema` |
| Buenas Prácticas (análisis y sugerencias) | `buenas_practicas` |
| Web Push Notifier (VAPID, estándar W3C) | `web_push` |
| Alertas *(agregado)* | `alertas` |

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Front | Lo sirve el backend (Jinja2). NGINX entrega los estáticos. |
| Tailwind | Compilado con el ejecutable oficial (sin Node). El CDN no es apto para producción. |
| Backend | Un único servicio (monolito modular). |
| Python | 3.14, la última versión estable. |
| Dependencias | `uv` con `pyproject.toml` y `uv.lock`. |
| Linter / tests | `ruff` (lint y formato) y `pytest` con cobertura mínima del 80%. |
| SQL Server | Contiene toda la estructura funcional de la app. Imagen 2022 en Docker. |
| MongoDB | Solo auditoría, glosarios de las bases monitoreadas y logs. |
| Redis | Contenedor propio. TTL configurables por variable de entorno. |
| Secretos | `.env` en local, detrás de una interfaz; Key Vault por ambiente al migrar a Azure. |
| IA | Claude Haiku 5.5 por defecto. |
| MFA | Obligatorio al ingresar; segundo factor nuevamente al ejecutar acciones sobre conexiones PROD. |
| Login | Solo Google (a futuro, correo empresarial). |
| Instancias monitoreadas | Solo SQL Server sobre Windows. Las registra el usuario. |
| Sandbox | Un contenedor persistente por cada IP registrada, con todas sus bases. Solo estructura. Se sincroniza a diario y se verifica alineación antes de cada validación. |
| Ejecución | Dry-run en sandbox y confirmación del usuario antes de ejecutar en la base real. |
| Alertas | Motor caído, base caída, bloqueos y poco espacio (disco, datos y log). Réplica, más adelante. |
| Ambientes | Uno solo por ahora. Ramas `main` y `dev`. |
| Idioma | Español; textos en `app/web/i18n/` para agregar inglés después. |

## Desvíos respecto del diagrama

| Diagrama | Implementación | Motivo |
|---|---|---|
| Driver `motor` para MongoDB | API asíncrona de PyMongo (`AsyncMongoClient`) | MongoDB deprecó Motor en 2026; la API asíncrona de PyMongo es su reemplazo oficial y mantiene el mismo modelo de uso. |
| MongoDB Atlas | MongoDB en Docker (provisorio) | Pendiente de decisión (ver `docs/pendientes.md`). |
