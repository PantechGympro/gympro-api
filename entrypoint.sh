#!/bin/sh
# entrypoint.sh — Ejecuta migraciones Alembic antes de iniciar gunicorn.
#
# Esto garantiza que las tablas y tipos PG existan en cualquier entorno
# sin necesidad de correr comandos manuales post-deploy.
set -e

echo "==> Creando directorio de storage para backups..."
mkdir -p /app/storage/backups
echo "==> Storage OK"

echo "==> Aplicando migraciones Alembic..."
alembic upgrade head
echo "==> Migraciones OK"

echo "==> Iniciando gunicorn..."
# Optimización para VPS con poca RAM (8 GB):
#   - 2 workers gthread × 4 threads = 8 requests concurrentes con ~la mitad de
#     RAM que 4 workers sync (cada worker fork carga sklearn+pandas+numpy ≈ 300 MB).
#   - gthread encaja aquí porque la llamada a Ollama es I/O-bound (espera HTTP al
#     servicio ollama hasta 5 min): los threads liberan el GIL durante la espera,
#     a diferencia del modelo sync donde un request largo bloquea el worker entero.
#   - NullPool (config.py) + scoped_session de Flask-SQLAlchemy son thread-safe,
#     así que gthread NO reintroduce el problema de fork documentado en config.py.
#   - max-requests recicla workers periódicamente para evitar fugas de memoria.
#   - Override por entorno: WEB_CONCURRENCY (workers) y GUNICORN_THREADS (threads).
exec /venv/bin/gunicorn \
    --bind 0.0.0.0:5000 \
    --workers "${WEB_CONCURRENCY:-2}" \
    --worker-class gthread \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout 300 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --max-requests 500 \
    --max-requests-jitter 50 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    wsgi:app
