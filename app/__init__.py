from flask import Flask, send_from_directory
import os
from .config import Config
from .extensions import db, migrate, jwt, mail, limiter
from .utils.tenant import init_tenant_middleware

# Blueprints por rol
from app.routes.owner_gym.miembros            import miembros_bp
from app.routes.owner_gym.pagos               import pagos_bp
from app.routes.owner_gym.dashboard_routes    import dashboard_bp
from app.routes.owner_gym.billing             import billing_bp
from app.routes.owner_gym.billing_stripe      import billing_stripe_bp
from app.routes.owner_gym.pasarelas           import pasarelas_bp
from app.routes.pagos_online                  import pagos_online_bp
from app.routes.owner_gym.onboarding          import onboarding_bp
from app.routes.owner_gym.catalogos           import catalogos_bp
from app.routes.owner_gym.reports             import reports_bp
from app.routes.owner_gym.notifications       import notifications_bp, init_scheduler
from app.routes.owner_gym.ventas              import ventas_bp
from app.routes.owner_gym.owner_dashboard     import owner_dashboard_bp
from app.routes.owner_gym.owner_profile       import owner_profile_bp
from app.routes.owner_gym.owner_trainers      import owner_trainers_bp
from app.routes.owner_gym.owner_membresias    import owner_membresias_bp
from app.routes.owner_gym.reportes_negocio    import reportes_negocio_bp
from app.routes.owner_gym.owner_productos     import owner_productos_bp
from app.routes.miembro.user_dashboard        import user_dashboard_bp
from app.routes.miembro.user_payments         import user_payments_bp
from app.routes.miembro.user_profile          import user_profile_bp
from app.routes.miembro.user_health           import user_health_bp
from app.routes.miembro.user_body_progress    import user_body_progress_bp
from app.routes.miembro.user_membership       import user_membership_bp
from app.routes.miembro.user_routine          import user_routines_bp
from app.routes.miembro.training              import training_bp
from app.routes.miembro.user_nutrition        import user_nutrition_bp
from app.routes.entrenador.trainer_routes     import trainer_bp
from app.routes.entrenador.diet_routes        import diet_bp
from app.routes.recepcionista.recepcionista_routes import recepcionista_bp
from app.routes.ia.spark_mapreduce            import spark_mapreduce_bp
from app.routes.ia.spark_kmeans               import spark_kmeans_bp
from app.routes.ia.spark_regresion            import spark_regresion_bp
from app.routes.ia.spark_cancelaciones        import spark_cancelaciones_bp
from app.routes.ia.spark_rutinas              import spark_rutinas_bp
from app.routes.ia.spark_negocio              import spark_negocio_bp
from app.routes.ia.spark_modelos              import spark_modelos_bp
from app.routes.compartido.membresias         import membresias_bp
from app.routes.compartido.miembro_membresias import miembro_membresias_bp
from app.routes.compartido.notificaciones     import notificaciones_bp
from app.backups.routes                       import backups_bp
from app.backups.tenant_routes                import tenant_backups_bp
from app.routes.superadmin.gimnasios          import gimnasios_admin_bp
from app.routes.superadmin.suscripciones      import suscripciones_admin_bp
from app.routes.superadmin.planes             import planes_admin_bp
from app.routes.superadmin.usuarios           import usuarios_admin_bp
from app.routes.superadmin.backups_admin      import backups_admin_bp
from app.routes.superadmin.spark_platform     import spark_platform_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY")

    from app.models.pg.rol                 import Rol                 # noqa: F401
    from app.models.pg.gimnasio            import Gimnasio            # noqa: F401
    from app.models.pg.usuario             import Usuario             # noqa: F401
    from app.models.pg.plan_suscripcion    import PlanSuscripcion     # noqa: F401
    from app.models.pg.suscripcion         import Suscripcion         # noqa: F401
    from app.models.pg.factura_suscripcion import FacturaSuscripcion  # noqa: F401
    from app.models.pg.tipo_membresia      import TipoMembresia       # noqa: F401
    from app.models.pg.ejercicio           import Ejercicio           # noqa: F401
    from app.models.pg.tipo_clase          import TipoClase           # noqa: F401

    db.init_app(app)
    migrate.init_app(app, db, directory="migrations")
    jwt.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)
    init_tenant_middleware(app)

    from .auth.routes              import auth_bp
    from .routes.compartido.health import health_bp

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
    app.register_blueprint(diet_bp)
    app.register_blueprint(user_health_bp)
    app.register_blueprint(user_body_progress_bp)
    app.register_blueprint(user_membership_bp)
    app.register_blueprint(user_dashboard_bp)
    app.register_blueprint(spark_mapreduce_bp)
    app.register_blueprint(spark_kmeans_bp)
    app.register_blueprint(spark_regresion_bp)
    app.register_blueprint(user_routines_bp,      url_prefix="/api/user")
    app.register_blueprint(training_bp,           url_prefix="/api/user/training")
    app.register_blueprint(user_nutrition_bp)
    app.register_blueprint(miembro_membresias_bp, url_prefix="/api")
    app.register_blueprint(billing_bp)
    app.register_blueprint(billing_stripe_bp)
    app.register_blueprint(pasarelas_bp)
    app.register_blueprint(pagos_online_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(catalogos_bp,          url_prefix="/api")
    app.register_blueprint(reports_bp)
    app.register_blueprint(spark_cancelaciones_bp)
    app.register_blueprint(spark_rutinas_bp)
    app.register_blueprint(spark_negocio_bp)
    app.register_blueprint(spark_modelos_bp)
    app.register_blueprint(notifications_bp)
    app.register_blueprint(notificaciones_bp)
    app.register_blueprint(ventas_bp)
    app.register_blueprint(owner_dashboard_bp,     url_prefix="/api/owner_gym")
    app.register_blueprint(owner_profile_bp,       url_prefix="/api/owner_gym")
    app.register_blueprint(owner_trainers_bp,      url_prefix="/api/owner_gym")
    app.register_blueprint(owner_membresias_bp,    url_prefix="/api/owner_gym")
    app.register_blueprint(reportes_negocio_bp,    url_prefix="/api/owner_gym")
    app.register_blueprint(owner_productos_bp,     url_prefix="/api/owner_gym")
    app.register_blueprint(tenant_backups_bp,      url_prefix="/api/owner_gym")
    app.register_blueprint(recepcionista_bp,       url_prefix="/api/recepcionista")
    app.register_blueprint(gimnasios_admin_bp,     url_prefix="/api/superadmin")
    app.register_blueprint(suscripciones_admin_bp, url_prefix="/api/superadmin")
    app.register_blueprint(planes_admin_bp,        url_prefix="/api/superadmin")
    app.register_blueprint(usuarios_admin_bp,      url_prefix="/api/superadmin")
    app.register_blueprint(backups_admin_bp,       url_prefix="/api/superadmin")
    app.register_blueprint(spark_platform_bp,      url_prefix="/api/superadmin")

    # ── Ruta para servir fotos de perfil subidas ─────────────────────────────
    # Usa /app/storage/uploads/ (bind-mounted desde el host) para garantizar
    # persistencia entre rebuilds sin depender de named volumes ni permisos.
    @app.route("/api/uploads/<path:filename>")
    def serve_upload(filename):
        upload_dir = "/app/storage/uploads"
        return send_from_directory(upload_dir, filename)

    init_scheduler(app)
    return app
