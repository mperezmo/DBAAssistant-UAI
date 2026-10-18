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
| SQL Server | Estructura funcional: tenants, conexiones, usuarios, roles, permisos por ambiente, métricas y umbrales, reglas de alerta, alertas, workarounds y versiones. Imagen 2022 en Docker, accesible desde SSMS en `localhost,14330`. |
| MongoDB | Bitácoras (transacciones, auditoría, excepciones, sandbox), contexto de negocio, informes de buenas prácticas, snapshots de schema y dispositivos push. |
| Redis | Contenedor propio. TTL configurables solo por variable de entorno. |
| Secretos | `.env` en local, detrás de una interfaz; Key Vault por ambiente al migrar a Azure. |
| IA | Claude Haiku 5.5 por defecto. |
| MFA | Obligatorio al ingresar; segundo factor nuevamente al ejecutar acciones sobre conexiones PROD. |
| Login | Google y email + contraseña, siempre con MFA. |
| Roles | Tres: Administrador, DBA y Consulta (no los cinco del diseño). Módulos por rol en la tabla siguiente. |
| Instancias monitoreadas | Solo SQL Server sobre Windows. Las registra el usuario. |
| Sandbox (detalle) | Un contenedor persistente por cada IP registrada, con todas sus bases. Solo estructura. Se sincroniza a diario y se verifica alineación antes de cada validación. |
| Ejecución | Dry-run en sandbox y confirmación del usuario antes de ejecutar en la base real. |
| Alertas | Motor caído, base caída, bloqueos y poco espacio (disco, datos y log). Réplica, más adelante. Notificación por la PWA; email más adelante. |
| Ambientes | Uno solo por ahora. Ramas `main` y `dev`. |
| Idioma | Español; textos en `app/web/i18n/` para agregar inglés después. |
| Multi-tenant | Desde el inicio. Base central `<prefijo>_Catalogo` con la lista de tenants; por cada tenant una base SQL Server `<prefijo>_T_<slug>` y una base MongoDB `<prefijo>_t_<slug>`. Las tablas llevan además `tenant_id` como segunda barrera. Alta automática con `python -m app.cli crear-tenant`. |
| Identidad por tenant | Auth0 Organizations: una organización por cliente; cada cliente entra por su subdominio (`<cliente>.localhost` en desarrollo). |
| Sesión | Login en el backend con Auth0; JWT guardado en Redis y cookie segura (HttpOnly) en el navegador. Duración fija de 8 horas, sin cierre por inactividad. |
| Roles | Se guardan en la base del tenant; Auth0 solo verifica identidad, MFA y pertenencia a la organización. |
| Sandbox | Un contenedor por IP registrada y por tenant. |
| Cambios de estructura | Scripts T-SQL numerados en `backend/app/datos/migraciones/{catalogo,tenant}`, aplicados automáticamente al arrancar. |
| Monitoreo | Refresco de métricas cada 3 segundos. |
| Retención | Bitácoras y excepciones: 5 años (requisito del TFI). |
| Capas | Cada módulo con `api`, `servicios`, `repositorios` y `entidades`; catálogo central de códigos de error del TFI. |
| Biometría móvil | Confirmación con WebAuthn (Face ID / Touch ID o PIN) en la PWA. |
| Perímetro de consulta | Tablas autorizadas por rol (a detallar). |
| Restaurar schema | En espera. |
| Baja de usuarios | Desaparece del Panel Admin y queda deshabilitado 30 días (se puede rehabilitar); luego se elimina y hay que darlo de alta de nuevo. La bitácora conserva su historial. |
| Cambio de rol | No cierra la sesión: el rol se relee en cada petición y se ve al recargar. |
| Permisos por ambiente | Por ahora, ambientes habilitados por usuario; las acciones finas se suman con cada módulo. |
| Notificaciones push | Web Push con VAPID (W3C), sin servicios de terceros. |
| Exportación de auditoría | CSV y PDF sin firma digital (firma omitida por ahora). |
| Histórico de métricas | Colecciones *time-series* de MongoDB. |
| Histórico de schema | Registra lo ejecutado desde la app: usuario, query y quién la autorizó. Lo ejecutado fuera de la app (SSMS, etc.) queda fuera de alcance. |

## Módulos por rol

| Módulo | Administrador | DBA | Consulta |
|---|---|---|---|
| Inicio, Alertas, Monitoreo | ✔ | ✔ | Solo lectura |
| Chat IA | ✔ | ✔ | Solo consultas SELECT |
| Contexto de Negocio, Histórico de Schema | ✔ | ✔ | Solo lectura |
| Workarounds, Sandbox | ✔ | ✔ | ✘ |
| Auditoría (incluye visor de logs) | ✔ | ✔ | ✘ |
| Panel Admin (conexiones, usuarios, reglas) | ✔ | ✘ | ✘ |

## Desvíos respecto del diagrama

| Diagrama | Implementación | Motivo |
|---|---|---|
| Driver `motor` para MongoDB | API asíncrona de PyMongo (`AsyncMongoClient`) | MongoDB deprecó Motor en 2026; la API asíncrona de PyMongo es su reemplazo oficial y mantiene el mismo modelo de uso. Aprobado. |
| Pantalla de ingreso del diseño con campos de email, contraseña y código MFA | La pantalla de la app solo tiene el botón **Ingresar**; credenciales y MFA se cargan en la pantalla de Auth0 (Universal Login) | Las credenciales nunca pasan por la app: es la práctica recomendada por Auth0 y la que permite Google + MFA. La pantalla de Auth0 se puede personalizar con los colores del diseño. |
| Invitación por email al nuevo usuario (CU-001-000-A) | El usuario se da de alta en la base del tenant y entra por la dirección de su empresa | El envío de emails quedó para más adelante. Auth0 suma a la organización a quien se autentica, pero la app solo deja entrar a quien está dado de alta en el tenant y, en el primer ingreso, con email verificado. |
| MongoDB Atlas | MongoDB en Docker (provisorio) | Pendiente de decisión (ver `docs/pendientes.md`). |
