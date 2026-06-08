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
exec /venv/bin/gunicorn \
    --bind 0.0.0.0:5000 \
    --workers 4 \
    --worker-class sync \
    --timeout 300 \
    --graceful-timeout 30 \
    --keep-alive 5 \
    --access-logfile - \
    --error-logfile - \
    --log-level info \
    wsgi:app
