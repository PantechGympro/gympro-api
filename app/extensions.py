from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_jwt_extended import JWTManager
from flask_mail import Mail
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

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
