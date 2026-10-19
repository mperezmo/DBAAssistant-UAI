# DBA Assistant

Asistente para administrar y optimizar **SQL Server** con IA generativa.
Traduce lenguaje natural a T-SQL, monitorea instancias, detecta condiciones que
dejan una base inaccesible y ejecuta playbooks de remediación con validación
previa en un sandbox.

La arquitectura de referencia está en [`docs/arquitectura.png`](docs/arquitectura.png)
y su correspondencia con el código en [`docs/arquitectura.md`](docs/arquitectura.md).
El diseño visual de referencia es [`design/DBA Assistant.html`](design/DBA%20Assistant.html).

## Stack

| Capa | Tecnología |
|------|------------|
| Cliente | HTML5 + Tailwind CSS + Alpine.js (renderizado por el backend), PWA |
| Identidad | Auth0 (OAuth 2.0 + OpenID Connect, MFA, login con Google) |
| Proxy | NGINX con TLS 1.3 |
| Backend | Python 3.14, FastAPI, Uvicorn (ASGI) |
| Datos | SQL Server (ODBC 18), MongoDB, Redis |
| IA | Anthropic Claude API |

## Puesta en marcha (desarrollo)

Requisitos: Docker y Docker Compose.

```bash
cp .env.example .env        # completar la contraseña de SQL Server y demás valores
docker compose up -d --build
```

- Aplicación: cada empresa entra por su subdominio, por ejemplo <https://acme.localhost>
  (certificado autofirmado: aceptar la advertencia del navegador).
- Estado de las dependencias: <https://localhost/api/salud>.
- SQL Server de la aplicación: `localhost,14330`, accesible desde SSMS.

### Clave de cifrado de credenciales

Las contraseñas de las conexiones se guardan cifradas. Antes del primer uso se genera
la clave maestra y se agrega al `.env`:

```bash
docker compose run --rm backend python -m app.cli generar-clave
```

Si la clave se pierde o cambia, hay que volver a cargar las contraseñas de las conexiones.

### Alta de un cliente (tenant)

Cada cliente tiene su propia base SQL Server y su propia base MongoDB. El alta es
un único comando que crea todo y registra al primer Administrador:

```bash
docker compose exec backend python -m app.cli crear-tenant \
  --slug acme --nombre "ACME S.A." --admin admin@acme.com
```

Crea también la organización del cliente en Auth0 (ver `docs/auth0.md`). Desde ese
momento el Administrador ingresa por `https://acme.localhost` con Google o con email y
contraseña; en el primer ingreso Auth0 le pide configurar el doble factor.

Si un paso falla, el tenant queda en estado `error` y volver a ejecutar el mismo
comando retoma el aprovisionamiento.

Los cambios de estructura de las bases (`backend/app/datos/migraciones/`) se
aplican solos al arrancar el backend. También se pueden aplicar a mano:

```bash
docker compose exec backend python -m app.cli migrar
```

### Conectarse desde SSMS

El SQL Server corre en Linux dentro de Docker, por lo que **no admite autenticación de Windows**:

| Campo | Valor |
|---|---|
| Server name | `localhost,14330` |
| Authentication | **SQL Server Authentication** |
| Login | `sa` |
| Password | el valor de `SECRETO_SQLSERVER_PASSWORD` del `.env` |
| Encryption | Mandatory, con **Trust server certificate** tildado |

### Desarrollo del backend sin Docker

Requiere [uv](https://docs.astral.sh/uv/) y el driver *ODBC Driver 18 for SQL Server*.

```bash
cd backend
uv sync
./tailwind/compilar.sh          # genera app/web/static/css/app.css
uv run uvicorn app.main:app --reload
```

### Calidad

```bash
cd backend
uv run ruff check . && uv run ruff format --check .
uv run pytest                       # unitarios (cobertura mínima 80%)
uv run pytest -m integracion --no-cov   # requiere SQL Server y MongoDB (ver tests/integracion)
```

## Ramas

- `main`: versión estable. Cada sprint cerrado se etiqueta `vX.Y.0`.
- `dev`: integración del trabajo en curso.

Commits con [Conventional Commits](https://www.conventionalcommits.org/es/).

## Documentación

- [`docs/arquitectura.md`](docs/arquitectura.md): diagrama, decisiones y correspondencia con el código.
- [`docs/auth0.md`](docs/auth0.md): configuración de Auth0 paso a paso.
- [`docs/pendientes.md`](docs/pendientes.md): decisiones abiertas y temas a investigar.
