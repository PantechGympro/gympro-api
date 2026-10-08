import os
from datetime import timedelta
from sqlalchemy.pool import NullPool


class Config:
    # ── Seguridad ─────────────────────────────────────────────────────────────
    SECRET_KEY     = os.getenv("SECRET_KEY")
    JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")

    # 8 horas — evita expiración accidental durante sesiones largas (e.g. Ollama
    # puede tardar hasta 5 min por request; el default de 1h es demasiado corto
    # para flujos de trabajo reales en un turno de entrenador).
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(hours=8)

    # Token de refresco: 90 días. Es lo que hace que la sesión se sienta
    # permanente (como en WhatsApp o YouTube) sin sacrificar seguridad: el
    # access token sigue caducando pronto y solo el refresco, que nunca viaja
    # en las peticiones normales, tiene vida larga. Al refrescar se revalidan
    # rol y estado del usuario contra la base.
    JWT_REFRESH_TOKEN_EXPIRES = timedelta(days=90)

    # Lista de revocación (Actividad 09 — J. C. Pérez Nava): sin esto el
    # token_in_blocklist_loader registrado en extensions.py queda definido
    # pero nunca se consulta, y /api/auth/logout no tendría ningún efecto.
    JWT_BLOCKLIST_ENABLED      = True
    JWT_BLOCKLIST_TOKEN_CHECKS = ["access", "refresh"]

    # Cookies de sesión para el portal web (Actividad 09, PR-01 — M. Arriaga
    # Mora). El móvil sigue mandando el token por cabecera Authorization
    # (headers va primero); el portal web puede además recibirlo en una
    # cookie HttpOnly que JavaScript no puede leer, para que un script
    # inyectado no pueda robar la sesión leyendo localStorage.
    JWT_TOKEN_LOCATION      = ["headers", "cookies"]
    JWT_COOKIE_SECURE       = os.getenv("FLASK_DEBUG", "0") != "1"  # True salvo en dev local sin TLS
    JWT_COOKIE_SAMESITE     = "Lax"
    JWT_ACCESS_COOKIE_PATH  = "/"
    JWT_REFRESH_COOKIE_PATH = "/"
    # CSRF de las cookies de JWT desactivado por ahora: todas las peticiones
    # mutantes del portal son JSON vía fetch desde el mismo origen (no hay
    # formularios cross-site), y la cookie ya lleva SameSite=Lax, que un
    # navegador moderno no envía en una petición cross-site que no sea una
    # navegación de nivel superior. Queda anotado como mejora pendiente si
    # se necesita soportar un origen distinto para el frontend.
    JWT_COOKIE_CSRF_PROTECT = False

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
    # NullPool: crea una conexión nueva por request y la cierra al terminar.
    # Necesario con Gunicorn en modo fork (--worker-class sync): los workers
    # heredan las conexiones del proceso master al hacer fork(), dejando el
    # estado TCP compartido entre procesos. Con un pool persistente (QueuePool)
    # los workers corruptos responden None silenciosamente → fallthrough a Mongo → 401.
    # NullPool elimina el pool completamente; cada worker abre y cierra su propia
    # conexión sin estado compartido. Overhead mínimo en producción con pgBouncer,
    # aceptable en dev/staging directo a Postgres.
    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": NullPool,
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
    # ── Upload size ───────────────────────────────────────────────────────────
    # 15 MB — permite hasta 3 imágenes base64 de ~4 MB c/u antes de encoding.
    MAX_CONTENT_LENGTH = 15 * 1024 * 1024

    RATELIMIT_STORAGE_URI  = os.getenv("REDIS_URL", "redis://redis:6379/0")
    RATELIMIT_HEADERS_ENABLED = True   # Agrega X-RateLimit-* headers a las respuestas

    # Falla en CERRADO en producción (Actividad 09, PR-02 — M. Hernández
    # Cervantes). Antes era True ("swallow") sin condición: si Redis dejaba
    # de responder, el contador desaparecía por completo y /api/auth/login
    # aceptaba intentos ilimitados sin que nada lo advirtiera. Se conserva
    # el comportamiento permisivo SOLO en desarrollo (FLASK_DEBUG=1) para
    # no entorpecer las pruebas del equipo cuando Redis no está levantado.
    RATELIMIT_SWALLOW_ERRORS = os.getenv("FLASK_DEBUG", "0") == "1"

    # Respaldo en memoria: si Redis no responde, cada worker de Gunicorn
    # sigue contando por su cuenta. El límite global se degrada (deja de
    # ser compartido entre workers) pero no desaparece.
    RATELIMIT_IN_MEMORY_FALLBACK_ENABLED = True

    # Hacer visible la caída del almacén de límites (ver Flask-Limiter):
    # @limiter.request_filter / signal "flask_limiter.redis_unreachable"
    # no existe como tal; el propio Flask-Limiter registra un logger.warning
    # al caer al respaldo en memoria. Se deja documentado aquí porque es el
    # punto que explica por qué los registros muestran esa advertencia.
