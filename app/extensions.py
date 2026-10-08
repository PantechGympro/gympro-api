import os

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import redis

db      = SQLAlchemy()
migrate = Migrate()
jwt     = JWTManager()
mail    = Mail()

# Límites globales aplicados a todos los endpoints salvo los que definen uno propio.
# Los contadores viven en Redis (RATELIMIT_STORAGE_URI en config.py).
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per day", "60 per hour"],
)

# Cliente Redis para la lista de revocación de JWT (Actividad 09, PR-01 y
# PR-02 de sesión — J. C. Pérez Nava). Mismo Redis y misma base que usa
# Flask-Limiter (RATELIMIT_STORAGE_URI en config.py): no hay colisión de
# claves porque las de revocación usan siempre el prefijo "revocado:" o
# "familia_revocada:".
redis_cliente = redis.from_url(
    os.getenv("REDIS_URL", "redis://redis:6379/0"),
    decode_responses=True,
)


@jwt.token_in_blocklist_loader
def _token_esta_revocado(_cabecera_jwt, carga_jwt):
    """Flask-JWT-Extended consulta esto en CADA petición autenticada.

    Antes no existía: cerrar sesión solo limpiaba el dispositivo y el
    token de refresco seguía siendo válido en el servidor durante sus 90
    días completos (PR-01). Ahora /api/auth/logout marca el jti del token
    aquí, y /api/auth/refresh marca también cada token de refresco ya
    canjeado para detectar reutilización (PR-02).
    """
    jti = carga_jwt.get("jti")
    familia = carga_jwt.get("fam")
    if jti and redis_cliente.get(f"revocado:{jti}"):
        return True
    if familia and redis_cliente.get(f"familia_revocada:{familia}"):
        return True
    return False
