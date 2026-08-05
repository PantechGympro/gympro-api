# syntax=docker/dockerfile:1
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

# Instalar dependencias (cacheado mientras requirements.txt no cambie).
# --mount=type=cache reutiliza las ruedas ya descargadas entre builds, así que
# cuando cambia requirements.txt sólo se recompila lo nuevo. Se quita
# --no-cache-dir para que el cache del mount tenga efecto real.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip && \
    pip install -r requirements.txt


# ─── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim-bookworm AS runtime

# NOTA: el stack de IA fue migrado de PySpark → scikit-learn (ver spark_config.py),
#       por lo que ya NO se instala Java/JRE. Eliminarlo reduce la imagen ~200 MB,
#       acelera el build y baja la superficie de ataque. Ningún módulo de app/
#       importa pyspark; los scripts en api/spark/ son legacy y no los carga gunicorn.
# postgresql-client-16 — pg_dump DEBE coincidir con la versión del servidor (postgres:16)
#   Se instala desde el repo PGDG oficial; el paquete genérico "postgresql-client"
#   en Debian bookworm instala v15 que rechaza conectarse a servidores v16.
# mongodb-database-tools — provee mongodump/mongorestore para backups tipo "full"
#   Se instala desde el repositorio oficial de MongoDB (no está en repos Debian base)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gnupg \
        curl \
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
        # tzdata — sin esto el contenedor solo conoce UTC y datetime.now()
        # guarda la hora equivocada: una venta de las 19:00 del día 31 en
        # México queda registrada como día 1 (01:00 UTC). Con tzdata + la
        # variable TZ, datetime.now() ya devuelve la hora local del gimnasio.
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# Variables de entorno para Python
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH="/app"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Zona horaria por defecto. docker-compose la sobrescribe con APP_TIMEZONE.
# Afecta a todo el código que llama datetime.now(), que es la mayoría.
ENV TZ=America/Mexico_City
# FLASK_ENV y FLASK_DEBUG se inyectan desde .env / docker-compose (no hardcoded)

# Usuario no-root — el proceso de la API nunca corre como root
RUN useradd --create-home --uid 1000 gymuser

WORKDIR /app

# Copiar venv compilado desde el builder (sin herramientas de build)
COPY --from=builder /venv /venv

# Copiar código fuente (el .dockerignore excluye lo que no debe ir)
COPY --chown=gymuser:gymuser . .

# Normalizar finales de línea y dar permisos de ejecución al entrypoint.
#
# El `sed` no es decorativo: si el archivo llega con finales de línea de Windows
# (CRLF), el kernel de Linux lee el shebang como `#!/bin/sh\r`, busca un
# intérprete llamado "sh\r" que no existe y aborta con un mensaje engañoso:
#
#     exec /app/entrypoint.sh: no such file or directory
#
# —engañoso porque el archivo SÍ está ahí y el `chmod` funcionó. Detrás, nginx
# cae con "host not found in upstream 'api'" al no poder resolver un contenedor
# que está en bucle de reinicio, y parece un problema de red que no lo es.
#
# El .gitattributes de la raíz ya obliga a que los .sh se guarden con LF en el
# repositorio, así que un clon limpio nunca tiene el problema. Esta línea cubre
# el caso que aquel no puede: `docker build` copia desde el DISCO, no desde git,
# de modo que un editor que guarde el archivo con CRLF rompería el arranque aun
# con el repositorio correcto. Normalizar aquí hace la imagen inmune a ambos.
RUN sed -i 's/\r$//' /app/entrypoint.sh \
 && chmod +x /app/entrypoint.sh

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
