"""
run.py — Servidor de desarrollo local (NO usar en producción).

Para producción usar Gunicorn via wsgi.py:
    gunicorn --bind 0.0.0.0:5000 wsgi:app

Para desarrollo:
    FLASK_DEBUG=1 python run.py
"""
import os
from app import create_app
from flask_cors import CORS

app = create_app()

# En desarrollo se permite localhost; en producción usar wsgi.py con ALLOWED_ORIGINS
CORS(app, origins=["http://localhost:3000", "http://127.0.0.1:3000"],
     supports_credentials=True)

if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=5000, debug=debug)
