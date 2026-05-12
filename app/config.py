import os


class Config:
    # ── Seguridad ─────────────────────────────────────────────────────────────
    SECRET_KEY     = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    # DEBUG siempre False en producción; run.py lo sobreescribe en desarrollo.
    # Gunicorn ignora esta variable, pero la dejamos explícita como salvaguarda.
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    # ── PostgreSQL (Sprint 2 — plataforma y finanzas) ────────────────────────
    # Roles, Gimnasios, Usuarios y entidades financieras viven aquí.
    # En docker-compose este valor se sobreescribe automáticamente desde la
    # variable POSTGRES_URI inyectada por el servicio postgres.
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "POSTGRES_URI",
        "postgresql+psycopg2://gymuser:gympassword@localhost:5432/gymprodb"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,       # verifica la conexión antes de usarla
        "pool_recycle": 300,         # recicla conexiones cada 5 minutos
        "pool_size": 5,
        "max_overflow": 10,
    }

    # ── Email ─────────────────────────────────────────────────────────────────
    MAIL_SERVER        = os.getenv("MAIL_SERVER", "smtp.gmail.com")
    MAIL_PORT          = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS       = os.getenv("MAIL_USE_TLS", "True") == "True"
    MAIL_USERNAME      = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD      = os.getenv("MAIL_PASSWORD")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER")
    MAIL_RECIPIENT     = os.getenv("MAIL_RECIPIENT", os.getenv("MAIL_USERNAME"))

    # ── Rate Limiting (Flask-Limiter + Redis) ─────────────────────────────────
    # Redis compartido entre todos los workers de Gunicorn para contadores globales.
    # Sin Redis, cada worker tendría su propio contador y los límites serían ineficaces.
    RATELIMIT_STORAGE_URI  = os.getenv("REDIS_URL", "redis://redis:6379/0")
    RATELIMIT_HEADERS_ENABLED = True   # Agrega X-RateLimit-* headers a las respuestas
    RATELIMIT_SWALLOW_ERRORS  = True   # Fail open: si Redis cae, no bloquea requests
