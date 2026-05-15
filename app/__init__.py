from flask import Flask
import os
from .config import Config
from .extensions import db, migrate, jwt, mail, limiter
from .utils.tenant import init_tenant_middleware

# ── Blueprints ────────────────────────────────────────────────────────────────
from app.backups.routes               import backups_bp
from app.routes.miembros              import miembros_bp
from app.routes.pagos                 import pagos_bp
from app.routes.membresias            import membresias_bp
from app.routes.miembro_membresias    import miembro_membresias_bp
from app.routes.dashboard_routes      import dashboard_bp
from app.routes.user_dashboard        import user_dashboard_bp
from app.routes.user_payments         import user_payments_bp
from app.routes.user_profile          import user_profile_bp
from app.routes.user_health           import user_health_bp
from app.routes.user_body_progress    import user_body_progress_bp
from app.routes.user_membership       import user_membership_bp
from app.routes.user_routine          import user_routines_bp
from app.routes.trainer_routes        import trainer_bp
from app.routes.spark_mapreduce       import spark_mapreduce_bp
from app.routes.spark_kmeans          import spark_kmeans_bp
from app.routes.spark_regresion       import spark_regresion_bp
from app.routes.billing               import billing_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY")

    # ── Extensiones ───────────────────────────────────────────────────────────
    # Importar modelos PG antes de migrate.init_app para que detecte el metadata
    from app.models.pg.rol                 import Rol                 # noqa: F401
    from app.models.pg.gimnasio            import Gimnasio            # noqa: F401
    from app.models.pg.usuario             import Usuario             # noqa: F401
    from app.models.pg.plan_suscripcion    import PlanSuscripcion     # noqa: F401
    from app.models.pg.suscripcion         import Suscripcion         # noqa: F401
    from app.models.pg.factura_suscripcion import FacturaSuscripcion  # noqa: F401

    db.init_app(app)
    migrate.init_app(app, db, directory="migrations")
    jwt.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)

    # ── Tenant middleware ────────────────────────────────────────────────────
    # Extrae id_gimnasio del JWT y lo coloca en flask.g.tenant_id en cada request.
    # Debe registrarse DESPUES de jwt.init_app() para que el contexto JWT este listo.
    init_tenant_middleware(app)

    # ── Blueprints ────────────────────────────────────────────────────────────
    from .auth.routes    import auth_bp
    from .routes.health  import health_bp

    app.register_blueprint(auth_bp,               url_prefix="/api/auth")
    app.register_blueprint(health_bp,             url_prefix="/api")
    app.register_blueprint(backups_bp)
    app.register_blueprint(membresias_bp)
    app.register_blueprint(miembros_bp)
    app.register_blueprint(dashboard_bp,          url_prefix="/api")
    app.register_blueprint(pagos_bp)
    app.register_blueprint(user_payments_bp)
    app.register_blueprint(user_profile_bp)
    app.register_blueprint(trainer_bp)
    app.register_blueprint(user_health_bp)
    app.register_blueprint(user_body_progress_bp)
    app.register_blueprint(user_membership_bp)
    app.register_blueprint(user_dashboard_bp)
    app.register_blueprint(spark_mapreduce_bp)
    app.register_blueprint(spark_kmeans_bp)
    app.register_blueprint(spark_regresion_bp)
    app.register_blueprint(user_routines_bp,       url_prefix="/api/user")
    app.register_blueprint(miembro_membresias_bp,  url_prefix="/api")
    app.register_blueprint(billing_bp)

    return app
