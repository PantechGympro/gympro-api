"""
routes/owner_gym/pasarelas.py — Configuración de cobros del gimnasio.

El dueño del gimnasio registra aquí SUS credenciales de PayPal y/o Mercado Pago.
Con ello, el dinero de membresías y productos se acredita directamente en su
cuenta: la plataforma no custodia esos fondos.

Endpoints (prefijo /api/owner/pasarelas):
    GET    /                     lista el estado de ambos proveedores
    PUT    /<proveedor>          guarda o actualiza credenciales
    POST   /<proveedor>/probar   verifica que las credenciales funcionan
    PATCH  /<proveedor>/toggle   activa o desactiva el método de pago
    DELETE /<proveedor>          elimina las credenciales guardadas

Las credenciales se cifran con Fernet y NUNCA se devuelven en claro.
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt

from app.extensions import db
from app.models.pg.pasarela_pago import ConfiguracionPasarela
from app.utils.tenant import require_tenant
from app.utils.crypto import cifrar_dict, descifrar_dict, enmascarar, crypto_disponible
from app.services.payments import PROVEEDORES_INFO, construir_pasarela

logger = logging.getLogger(__name__)

pasarelas_bp = Blueprint("pasarelas", __name__, url_prefix="/api/owner/pasarelas")

_ROLES_ADMIN = ("owner_gym", "superadmin")


def _es_admin(claims: dict) -> bool:
    return claims.get("role") in _ROLES_ADMIN


def _validar_proveedor(proveedor: str):
    proveedor = (proveedor or "").lower()
    if proveedor not in PROVEEDORES_INFO:
        return None, (jsonify({"msg": f"Proveedor no soportado: {proveedor}"}), 400)
    return proveedor, None


def _pista(cfg: ConfiguracionPasarela) -> str | None:
    """Enmascarado del campo secreto principal, para mostrar en la interfaz."""
    if not cfg or not cfg.credenciales:
        return None
    datos = descifrar_dict(cfg.credenciales)
    prov = cfg.proveedor.value if hasattr(cfg.proveedor, "value") else cfg.proveedor
    clave = "client_secret" if prov == "paypal" else "access_token"
    return enmascarar(datos.get(clave))


# ── Listado ──────────────────────────────────────────────────────────────────

@pasarelas_bp.route("", methods=["GET"])
@pasarelas_bp.route("/", methods=["GET"])
@jwt_required()
@require_tenant
def listar_pasarelas():
    """Estado de configuración de cada proveedor para el gimnasio actual."""
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede ver la configuración de cobros"}), 403

    existentes = {
        (c.proveedor.value if hasattr(c.proveedor, "value") else c.proveedor): c
        for c in ConfiguracionPasarela.query.filter_by(id_gimnasio=g.tenant_id).all()
    }

    salida = []
    for prov, info in PROVEEDORES_INFO.items():
        cfg = existentes.get(prov)
        base = {
            "proveedor": prov,
            "nombre":    info["nombre"],
            "campos":    info["campos"],
            "ayuda":     info["ayuda"],
        }
        if cfg:
            base.update(cfg.to_dict(pista=_pista(cfg)))
        else:
            base.update({
                "modo": "sandbox", "activo": False, "moneda": "MXN",
                "titular_cuenta": None, "configurado": False,
                "credencial_pista": None, "verificado_en": None, "ultimo_error": None,
            })
        salida.append(base)

    return jsonify({
        "pasarelas": salida,
        "cifrado_disponible": crypto_disponible(),
    }), 200


# ── Guardar credenciales ─────────────────────────────────────────────────────

@pasarelas_bp.route("/<proveedor>", methods=["PUT"])
@jwt_required()
@require_tenant
def guardar_pasarela(proveedor):
    """
    Guarda o actualiza las credenciales del proveedor.

    Body JSON:
      { "credenciales": {...}, "modo": "sandbox|live",
        "moneda": "MXN", "titular_cuenta": "Gimnasio X", "activo": true }

    Si 'credenciales' viene vacío se conservan las ya guardadas (permite
    cambiar solo el modo o la moneda sin volver a capturar los secretos).
    """
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede configurar los cobros"}), 403

    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err

    if not crypto_disponible():
        return jsonify({
            "msg": "El servidor no tiene configurada la clave de cifrado "
                   "(PAYMENTS_ENCRYPTION_KEY). Contacta al administrador de la plataforma."
        }), 503

    data = request.get_json() or {}
    nuevas = data.get("credenciales") or {}
    modo   = (data.get("modo") or "sandbox").lower()
    if modo not in ("sandbox", "live"):
        return jsonify({"msg": "El modo debe ser 'sandbox' o 'live'"}), 400

    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=g.tenant_id, proveedor=proveedor).first()
    if not cfg:
        cfg = ConfiguracionPasarela(id_gimnasio=g.tenant_id, proveedor=proveedor)
        db.session.add(cfg)

    # Solo se reemplazan las credenciales si llegan valores no vacíos
    nuevas = {k: str(v).strip() for k, v in nuevas.items() if str(v or "").strip()}
    if nuevas:
        requeridos = [c["clave"] for c in PROVEEDORES_INFO[proveedor]["campos"] if c["secreto"]]
        actuales = descifrar_dict(cfg.credenciales)
        combinadas = {**actuales, **nuevas}
        faltantes = [c for c in requeridos if not combinadas.get(c)]
        if faltantes:
            return jsonify({"msg": f"Faltan credenciales obligatorias: {', '.join(faltantes)}"}), 400
        cfg.credenciales = cifrar_dict(combinadas)
        cfg.verificado_en = None
        cfg.ultimo_error = None

    cfg.modo = modo
    if data.get("moneda"):
        cfg.moneda = str(data["moneda"]).upper()[:3]
    if "titular_cuenta" in data:
        cfg.titular_cuenta = (data.get("titular_cuenta") or None)
    if "activo" in data:
        cfg.activo = bool(data["activo"]) and bool(cfg.credenciales)

    db.session.commit()
    logger.info("Pasarela %s actualizada para gimnasio %s", proveedor, g.tenant_id)
    return jsonify({"msg": "Configuración guardada", "pasarela": cfg.to_dict(pista=_pista(cfg))}), 200


# ── Probar credenciales ──────────────────────────────────────────────────────

@pasarelas_bp.route("/<proveedor>/probar", methods=["POST"])
@jwt_required()
@require_tenant
def probar_pasarela(proveedor):
    """Verifica contra la API del proveedor que las credenciales sirven."""
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede probar los cobros"}), 403

    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err

    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=g.tenant_id, proveedor=proveedor).first()
    if not cfg or not cfg.credenciales:
        return jsonify({"ok": False, "msg": "Primero guarda las credenciales."}), 400

    credenciales = descifrar_dict(cfg.credenciales)
    modo = cfg.modo.value if hasattr(cfg.modo, "value") else cfg.modo
    try:
        pasarela = construir_pasarela(proveedor, credenciales, modo=modo, moneda=cfg.moneda)
        ok, mensaje = pasarela.verificar_credenciales()
    except Exception as exc:
        ok, mensaje = False, str(exc)

    cfg.verificado_en = datetime.now(timezone.utc) if ok else None
    cfg.ultimo_error  = None if ok else mensaje[:300]
    db.session.commit()

    return jsonify({"ok": ok, "msg": mensaje}), (200 if ok else 400)


# ── Activar / desactivar ─────────────────────────────────────────────────────

@pasarelas_bp.route("/<proveedor>/toggle", methods=["PATCH"])
@jwt_required()
@require_tenant
def toggle_pasarela(proveedor):
    """Habilita o deshabilita el método de pago para los cobros del gimnasio."""
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede cambiar los cobros"}), 403

    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err

    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=g.tenant_id, proveedor=proveedor).first()
    if not cfg or not cfg.credenciales:
        return jsonify({"msg": "Configura las credenciales antes de activar este método."}), 400

    cfg.activo = not cfg.activo
    db.session.commit()
    estado = "activado" if cfg.activo else "desactivado"
    return jsonify({"msg": f"Método de pago {estado}", "activo": cfg.activo}), 200


# ── Eliminar ─────────────────────────────────────────────────────────────────

@pasarelas_bp.route("/<proveedor>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_pasarela(proveedor):
    """Borra las credenciales guardadas del proveedor."""
    claims = get_jwt()
    if not _es_admin(claims):
        return jsonify({"msg": "Solo el administrador puede eliminar los cobros"}), 403

    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err

    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=g.tenant_id, proveedor=proveedor).first()
    if cfg:
        db.session.delete(cfg)
        db.session.commit()
    return jsonify({"msg": "Configuración eliminada"}), 200
