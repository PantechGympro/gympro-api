"""
Health checks.

  GET /api/health        Liveness: el proceso responde. Barato y SIN dependencias;
                         lo usan el HEALTHCHECK de Docker y el rollback post-despliegue.
                         Mantiene la clave histórica "status".
  GET /api/health/ready  Readiness: verifica PostgreSQL, MongoDB y Redis. 200 si todo
                         responde; 503 si alguna dependencia falla (con su detalle
                         sanitizado: nunca la cadena de conexión ni la excepción cruda).

Ambas son públicas, no consumen cuota de rate-limit y exponen la versión
desplegada (commit) para poder verificar qué corre en cada ambiente.
"""
import os
import time
from pathlib import Path

from flask import Blueprint, jsonify
from sqlalchemy import text

from app.extensions import db, limiter, redis_cliente
from app.mongo import get_client

health_bp = Blueprint("health", __name__)

_RAIZ = Path(__file__).resolve().parents[3]
_INICIO = time.time()


def _version():
    """Commit desplegado: env GIT_SHA o archivo BUILD_INFO escrito por el workflow de deploy."""
    sha = os.getenv("GIT_SHA", "").strip()
    if not sha:
        try:
            sha = (_RAIZ / "BUILD_INFO").read_text(encoding="utf-8").strip()
        except OSError:
            sha = ""
    return {"commit": sha[:12] or "desconocido", "version": os.getenv("APP_VERSION", "dev")}


@health_bp.route("/health", methods=["GET"])
@limiter.exempt
def health():
    return jsonify({"status": "API GYM activa", **_version()}), 200


def _medir(nombre, fn):
    t0 = time.perf_counter()
    try:
        fn()
        return {"estado": "ok", "ms": round((time.perf_counter() - t0) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 - se reporta el tipo, nunca el mensaje (puede traer credenciales)
        return {"estado": "error", "error": type(exc).__name__}


@health_bp.route("/health/ready", methods=["GET"])
@limiter.exempt
def readiness():
    comprobaciones = {
        "postgres": _medir("postgres", lambda: db.session.execute(text("SELECT 1"))),
        "mongo": _medir("mongo", lambda: get_client().admin.command("ping")),
        "redis": _medir("redis", lambda: redis_cliente.ping()),
    }
    listo = all(c["estado"] == "ok" for c in comprobaciones.values())
    return jsonify({
        "status": "ready" if listo else "degraded",
        "uptime_s": int(time.time() - _INICIO),
        "checks": comprobaciones,
        **_version(),
    }), (200 if listo else 503)
