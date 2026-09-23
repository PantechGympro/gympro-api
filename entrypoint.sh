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

# ── Seed automático de datos de demostración (SOLO gympro-server) ───────────
# RUN_SEED_ON_START no se define en el docker-compose.yml de desarrollo ni en
# el override local, así que este bloque es un no-op para cualquier
# desarrollador que levante el stack normal: cero cambio de comportamiento.
# docker-compose.server.yml es el único lugar que la pone en "true", para que
# la imagen del servidor quede cargada con los 3 gimnasios de demo (SCRUM-163)
# desde el primer arranque.
#
# Se verifica primero si ya existe al menos un Gimnasio en Postgres: el seed
# es destructivo (TRUNCATE + drop de colecciones Mongo), así que si el
# contenedor se reinicia después del primer arranque —o alguien hace
# `docker compose restart` tras cargar datos reales de demo en vivo— NO se
# vuelve a ejecutar y no se pierde nada.
if [ "${RUN_SEED_ON_START:-false}" = "true" ]; then
    echo "==> RUN_SEED_ON_START=true: verificando si la base ya tiene datos..."
    GIMNASIOS_EXISTENTES=$(python -c "
from app import create_app
from app.models.pg.gimnasio import Gimnasio
app = create_app()
with app.app_context():
    print(Gimnasio.query.count())
" 2>/dev/null || echo "0")

    if [ "$GIMNASIOS_EXISTENTES" = "0" ]; then
        echo "==> Base vacia: ejecutando seed de servidor (3 gimnasios de demo)..."
        python -m app.seeds.seed_server
        echo "==> Seed de servidor OK"
    else
        echo "==> Base ya tiene ${GIMNASIOS_EXISTENTES} gimnasio(s): se omite el seed (evita perder datos en reinicios)."
    fi
fi

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
