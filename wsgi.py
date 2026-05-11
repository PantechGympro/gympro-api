"""
wsgi.py — Entry point de Gunicorn para producción.

Gunicorn importa este módulo y llama al objeto `app`.
NO usar app.run() aquí; Gunicorn gestiona el servidor.

Uso:
    gunicorn --bind 0.0.0.0:5000 --workers 4 wsgi:app
"""
import os
from app import create_app
from flask_cors import CORS

app = create_app()

# CORS restringido: orígenes permitidos vienen de la variable de entorno.
# Formato: "https://gym1.gymsaas.com,https://gym2.gymsaas.com"
# En desarrollo: "http://localhost:3000"
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

CORS(
    app,
    origins=_allowed_origins,
    supports_credentials=True,
    allow_headers=["Content-Type", "Authorization", "X-Tenant-Slug"],
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
