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
  de la petición, bitácora de excepciones y certificado interno (HTTPS NGINX → Uvicorn).
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
- **Sprint 5 ✅ (`v0.5.0`):** Workarounds (CU-000-005): integrados y propios con versiones,
  ejecución paso a paso, ventana de mantenimiento con autorización y ejecución automática.
- **Sprint 6 ✅ (`v0.6.0`):** Sandbox (CU-000-002): contenedores con la estructura del
  origen, validación con rollback, historial y promoción; workarounds propios validados en
  Sandbox para PROD.
- **Sprint 7 ✅ (`v0.7.0`):** Contexto de Negocio (CU-000-006), Chat IA (CU-000-001) y
  panel de Inicio.
- **Sprint 8 ✅ (`v0.8.0`):** Auditoría (CU-000-009), bitácora de excepciones e Histórico
  de Schema (CU-000-008 A y B).
- **Sprint 9 ✅ (`v0.9.0`):** Buenas Prácticas (CU-000-007 A y B), más:
  - el Administrador con las facultades operativas del DBA;
  - Chat IA con el resultado primero (la consulta en el apartado "Query") y confirmación
    en palabras de las modificaciones;
  - workaround WK-08 para poner online una base OFFLINE;
  - multi idioma español e inglés, elegido por usuario.
- **Sprint 10 (en `dev`, pendiente de QA):** gestión de usuarios:
  - aviso de acceso por correo (SMTP) al nuevo usuario, con reenvío desde el Panel Admin y
    al primer Administrador de `crear-tenant`;
  - "Mi perfil" (CU-001-001-C): datos, idioma, cambio de contraseña por Auth0 y
    dispositivos con notificaciones;
  - quien pide autorizar un workaround fuera de la ventana puede autorizarlo él mismo,
    con segundo factor.
- **Sprint 11 (en `dev`, pendiente de QA):** móvil:
  - passkeys (WebAuthn) como segundo factor, registradas en "Mi perfil";
  - barra inferior y flujo de incidentes en el celular;
  - servicio `tunel` (Cloudflare) para usarla con dominio propio (`docs/tunel.md`).
- **Sprint 12 (en `dev`, pendiente de QA):** fidelidad a la arquitectura y ajustes:
  - validación completa contra el diagrama; desvíos documentados en `docs/arquitectura.md`;
  - HTTPS también entre NGINX y Uvicorn (certificado interno verificado por NGINX);
  - `entidades.py` en todos los módulos y consultas fuera de la capa de servicios;
  - Chat IA solo sobre las bases registradas; el motor de la instancia, solo para
    Administrador y DBA;
  - confirmación de acciones sensibles con solo el código del autenticador (sin volver a
    ingresar credenciales; requiere actualizar la Action de Auth0, ver `docs/auth0.md`).
  - documentos de negocio (PDF) en Contexto de Negocio: los carga cualquier rol, se convierten
    a Markdown con MarkItDown y la IA arma un resumen revisable (que se suma al contexto del Chat
    IA y de Buenas Prácticas) y propone términos y reglas que se aceptan con un clic.
- **Sprint 13 (en `dev`, pendiente de QA):** backups, jobs y consultas costosas:
  - estado de los backups por base frente a una política por conexión, con alertas y
    backups programados como job del SQL Server Agent (WK-09 full COPY_ONLY y WK-10 log);
  - jobs del Agent: estado, historial, alertas de fallas y demoras, explicación de la falla
    con IA, reintento (WK-11) e inicio del Agent por WinRM (WK-12);
  - consultas costosas por base desde Query Store, con tendencia, regresiones y cambios de
    plan, y activación de Query Store (WK-13);
  - la prueba de conexión informa los permisos que le faltan al login.
- **Sprint 14 (en `dev`, pendiente de QA):** Soporte, el panel técnico del Administrador:
  - salud de los servicios (movida desde el Panel Admin, con el Sandbox), cola de la bitácora
    de logs y procesos en segundo plano;
  - logs de la aplicación con las respuestas HTTP con error (4xx y 5xx) y los errores del
    código, y la auditoría del sistema; Auditoría queda con la bitácora de transacciones;
  - historia de la base de la aplicación: tablas temporales (datos a través del tiempo, 5
    años), trigger DDL (cada cambio de estructura, también desde SSMS) y versiones de la
    estructura por migración o cambio detectado, comparables y descargables como script.
- **Pendiente:** despliegue en Azure.
- Casos de uso: TFI (`DBAAssistant-PerezMoreno.pdf`, no versionado) y propuestas en
  `docs/casos-de-uso-propuestos.md`.
- Decisiones abiertas: `docs/pendientes.md`.
