# =============================================================================
# GymPro API — Dockerfile multi-stage
# =============================================================================
# Stage 1 (builder): instala dependencias Python en un venv aislado
# Stage 2 (runtime): imagen mínima con Java (PySpark) + venv + código
# =============================================================================

# ─── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS builder

WORKDIR /build

# Dependencias de compilación (solo en builder, no llegan al runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Crear virtualenv y actualizar pip
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Instalar dependencias (cacheado mientras requirements.txt no cambie)
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# Java 17 JRE — requerido por PySpark (Spark corre en la JVM)
# postgresql-client-16 — pg_dump DEBE coincidir con la versión del servidor (postgres:16)
#   Se instala desde el repo PGDG oficial; el paquete genérico "postgresql-client"
#   en Debian bookworm instala v15 que rechaza conectarse a servidores v16.
# mongodb-database-tools — provee mongodump/mongorestore para backups tipo "full"
#   Se instala desde el repositorio oficial de MongoDB (no está en repos Debian base)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gnupg \
        curl \
        openjdk-17-jre-headless \
    # ── Repo PGDG para postgresql-client-16 ──────────────────────────────────
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
       | gpg --dearmor -o /usr/share/keyrings/postgresql.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] \
       https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    # ── Repo MongoDB para mongodump ───────────────────────────────────────────
    && curl -fsSL https://www.mongodb.org/static/pgp/server-7.0.asc \
       | gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg \
    && echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] \
       https://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" \
       > /etc/apt/sources.list.d/mongodb-org-7.0.list \
    && apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client-16 \
        mongodb-database-tools \
    && rm -rf /var/lib/apt/lists/*

# Variables de entorno para Java y Python
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH="$JAVA_HOME/bin:/venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# FLASK_ENV y FLASK_DEBUG se inyectan desde .env / docker-compose (no hardcoded)

# Usuario no-root — el proceso de la API nunca corre como root
RUN useradd --create-home --uid 1000 gymuser

WORKDIR /app

# Copiar venv compilado desde el builder (sin herramientas de build)
COPY --from=builder /venv /venv

# Copiar código fuente (el .dockerignore excluye lo que no debe ir)
COPY --chown=gymuser:gymuser . .

# Dar permisos de ejecución al entrypoint
RUN chmod +x /app/entrypoint.sh

# Crear directorios de storage y uploads; ownership a gymuser ANTES de USER gymuser.
# Los named volumes Docker se inicializan copiando el directorio de la imagen
# (incluyendo ownership). Si se crea después de USER gymuser, el volume root puede
# bloquear la escritura.
RUN mkdir -p /app/storage/backups \
 && mkdir -p /app/app/static/uploads \
 && chown -R gymuser:gymuser /app/storage \
 && chown -R gymuser:gymuser /app/app/static/uploads

USER gymuser

EXPOSE 5000

# entrypoint.sh: corre `flask db upgrade` (migraciones Alembic) y luego gunicorn.
# Esto garantiza que tablas y tipos PG existan antes de aceptar tráfico.
CMD ["/app/entrypoint.sh"]
