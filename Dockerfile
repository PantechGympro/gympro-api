# =============================================================================
# GymPro API — Dockerfile multi-stage
# =============================================================================
# Stage 1 (builder): instala dependencias Python en un venv aislado
# Stage 2 (runtime): imagen mínima con Java (PySpark) + venv + código
# =============================================================================

# ─── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

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
FROM python:3.11-slim AS runtime

# Java 17 JRE — requerido por PySpark (Spark corre en la JVM)
# Se usa -headless para excluir componentes de GUI (~200 MB menos)
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
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

USER gymuser

EXPOSE 5000

# Gunicorn en modo producción:
#   --workers 4     → 4 procesos síncronos (regla: 2*CPU+1, ajustar en EC2)
#   --timeout 120   → margen para endpoints Spark (pueden tardar)
#   --access-logfile - → logs a stdout (capturados por Docker)
#   wsgi:app        → entry point en wsgi.py
CMD ["/venv/bin/gunicorn", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "4", \
     "--worker-class", "sync", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "wsgi:app"]
