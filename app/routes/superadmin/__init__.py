"""
routes/superadmin/ — Módulo de gestión de plataforma SaaS.

Solo accesible para usuarios con role == 'superadmin'.
Todos los blueprints aquí se registran con url_prefix='/api/superadmin'.

Blueprints:
    gimnasios_admin_bp   — CRUD + toggle + métricas de gimnasios
    suscripciones_bp     — gestión de suscripciones SaaS por gimnasio
    planes_admin_bp      — CRUD de planes de suscripción
    usuarios_admin_bp    — listado de usuarios de plataforma + impersonate
    backups_admin_bp     — backups centralizados + programación + log
    spark_platform_bp    — analytics de plataforma con Spark
"""
