import os


class Config:
    # ── Seguridad ─────────────────────────────────────────────────────────────
    SECRET_KEY     = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    # DEBUG siempre False en producción; run.py lo sobreescribe en desarrollo.
    # Gunicorn ignora esta variable, pero la dejamos explícita como salvaguarda.
    DEBUG = os.getenv("FLASK_DEBUG", "0") == "1"

    # ── LEGACY — SQLAlchemy / MySQL (se elimina en Sprint 2) ─────────────────
    # El sistema ya usa MongoDB vía PyMongo; SQLAlchemy quedó de la versión original.
    # La URI se construye aquí para evitar que extensions.py explote al importar,
    # pero nunca se ejecuta ninguna query real contra esta base de datos.
    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{os.getenv('DB_USER', 'legacy')}:"
        f"{os.getenv('DB_PASSWORD', 'legacy')}@"
        f"{os.getenv('DB_HOST', 'localhost')}/"
        f"{os.getenv('DB_NAME', 'gymdb')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

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
