# Imagen del backend FastAPI (Python + Uvicorn + driver ODBC 18 para SQL Server).
FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# Driver ODBC oficial de Microsoft.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
    | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
    -o /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends \
    msodbcsql18 unixodbc libgssapi-krb5-2 \
 && apt-get purge -y curl gnupg \
 && rm -rf /var/lib/apt/lists/*

# El driver se carga dinámicamente: si le falta alguna librería del sistema, el error
# aparece recién al conectar. Este control lo detecta durante la construcción.
RUN DRIVER="$(ls /opt/microsoft/msodbcsql18/lib64/libmsodbcsql-18.*.so.*)" \
 && ! ldd "$DRIVER" | grep "not found"

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /srv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app

RUN useradd --system --uid 1001 app
USER app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
