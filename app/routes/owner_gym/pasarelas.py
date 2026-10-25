"""
routes/owner_gym/pasarelas.py -- Configuracion de cobros del gimnasio.

El propietario registra aqui las credenciales de PayPal y/o Mercado Pago de
su organizacion (o de una sucursal en particular). Con ello, el dinero de
membresias y productos se acredita directamente en esa cuenta: la
plataforma no custodia esos fondos.

SCRUM-221 / HU-50 -- "Cuenta de cobro bajo control exclusivo del propietario"
------------------------------------------------------------------------------
Hasta esta historia, un Administrador de sucursal (rol de HU-45) podia
configurar la cuenta de cobro de SU propia sucursal exactamente igual que
el Propietario. HU-50 cierra ese hueco: ahora **solo el Propietario (o el
superadmin de plataforma) puede fijar o cambiar una cuenta de cobro**; el
resto, Administrador incluido, se queda en solo lectura (GET sigue
permitido para que puedan ver con que cuenta esta cobrando su sucursal).

Alcance decidido con el usuario: se mantiene el modelo de credenciales por
GIMNASIO que ya existia (con herencia por copia al crear una sucursal, ver
sucursales.py::crear_sucursal) en vez de subirlas a nivel organizacion con
sobreescritura por sucursal -- el modelo actual ya cumple el mismo
comportamiento observable sin una migracion de datos mayor. Ver migracion
024 para el detalle.

Para que el Propietario pueda cumplir "de la organizacion o de CUALQUIER
sucursal" (criterio 1) sin estar autenticado como esa sucursal, este
modulo expone DOS familias de rutas sobre la misma logica:

  - Rutas "propias" (igual que antes): /api/owner/pasarelas/...
    Operan sobre g.tenant_id (la sucursal del token). Lectura: Propietario,
    Administrador y superadmin. Escritura: solo Propietario/superadmin.

  - Rutas "de sucursal" (nuevas): /api/owner/pasarelas/sucursal/<id_gimnasio>/...
    Solo Propietario, solo sobre una sucursal de SU propia organizacion
    (ver _sucursal_de_mi_organizacion). Permiten cambiar la cuenta de una
    sucursal que no es la suya propia sin impersonarla. No hay pantalla
    nueva en el panel para esto todavia -- decision de alcance explicita,
    el criterio de API queda cubierto, la pantalla de "administrar el cobro
    de otra sucursal desde la mia" se deja para una entrega aparte.

Checklist de los 7 criterios de aceptacion y donde viven:
  1. Acceso exclusivo de escritura -> _puede_escribir() + rutas de sucursal.
  2. Herencia al crear sucursal -> ya resuelto en sucursales.py::crear_sucursal.
  3. Reautenticacion con contrasena -> _verificar_password(), en guardar/eliminar.
  4. Aviso por correo al propietario -> _enviar_aviso_cambio().
  5. Bitacora no editable desde el panel -> registrar_evento() (utils/alcance.py),
     ya es de solo backend por diseno (ver su propio docstring).
  6. Ventana de enfriamiento de 24h -> columnas credenciales_anteriores/
     cambiado_en (migracion 024) + services/payments/factory.py.
  7. Cifrado Fernet + enmascarado -> ya resuelto desde antes de esta historia
     (utils/crypto.py), sin cambios.

Endpoints (prefijo /api/owner/pasarelas):
    GET    /                                     estado de ambos proveedores (propio)
    PUT    /<proveedor>                          guarda o actualiza credenciales (propio)
    POST   /<proveedor>/probar                   verifica que las credenciales funcionan (propio)
    PATCH  /<proveedor>/toggle                    activa o desactiva el metodo de pago (propio)
    DELETE /<proveedor>                           elimina las credenciales guardadas (propio)
    GET    /sucursal/<id_gimnasio>                      idem, para otra sucursal de mi organizacion
    PUT    /sucursal/<id_gimnasio>/<proveedor>          idem
    POST   /sucursal/<id_gimnasio>/<proveedor>/probar   idem
    PATCH  /sucursal/<id_gimnasio>/<proveedor>/toggle    idem
    DELETE /sucursal/<id_gimnasio>/<proveedor>          idem

Las credenciales se cifran con Fernet y NUNCA se devuelven en claro.
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity
from flask_mail import Message

from app.extensions import db, mail
from app.models.pg.pasarela_pago import ConfiguracionPasarela
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.usuario import Usuario
from app.models.pg.rol import Rol
from app.utils.tenant import require_tenant
from app.utils.security import require_role
from app.utils.alcance import registrar_evento, usuario_propietario_de
from app.utils.crypto import cifrar_dict, descifrar_dict, enmascarar, crypto_disponible
from app.utils.email_remitentes import remitente, correo_contacto
from app.services.payments import PROVEEDORES_INFO, construir_pasarela, plataforma_en_produccion
from app.constants.roles import ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_SUPERADMIN, normalizar_rol

logger = logging.getLogger(__name__)

pasarelas_bp = Blueprint("pasarelas", __name__, url_prefix="/api/owner/pasarelas")

# Lectura: igual que antes de HU-50 -- el Administrador de sucursal puede
# VER con que cuenta esta cobrando (enmascarada), solo no puede cambiarla.
_ROLES_LECTURA = (ROL_PROPIETARIO, ROL_ADMINISTRADOR, ROL_SUPERADMIN)

# SCRUM-221 / HU-50, criterio 1: escritura exclusiva del propietario. El
# Administrador queda fuera a proposito -- antes de esta historia estaba
# aqui tambien (ver git log de este archivo si se necesita el comportamiento
# previo).
_ROLES_ESCRITURA = (ROL_PROPIETARIO, ROL_SUPERADMIN)


def _puede_leer(claims: dict) -> bool:
    return normalizar_rol(claims.get("role")) in _ROLES_LECTURA


def _puede_escribir(claims: dict) -> bool:
    return normalizar_rol(claims.get("role")) in _ROLES_ESCRITURA


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


# ── Alcance entre sucursales (HU-45/SCRUM-221) ──────────────────────────────

def _sucursal_de_mi_organizacion(id_gimnasio: int) -> bool:
    """
    True si `id_gimnasio` pertenece a la misma organizacion que g.tenant_id
    (la sucursal del propietario autenticado). Mismo criterio que
    sucursales.py::_sucursal_de_mi_organizacion -- reimplementado aqui en
    vez de importarlo porque es un helper privado de ese modulo.
    """
    mi_gimnasio = Gimnasio.query.get(g.tenant_id)
    if mi_gimnasio is None:
        return False
    objetivo = Gimnasio.query.get(id_gimnasio)
    return objetivo is not None and objetivo.id_organizacion == mi_gimnasio.id_organizacion


# ── Reautenticacion y notificaciones (SCRUM-221 / HU-50, criterios 3 y 4) ───

def _verificar_password(password: str):
    """
    Confirma la contrasena del usuario autenticado contra la base, aunque
    su sesion ya este abierta (criterio 3: "cambiar una cuenta pide la
    contrasena otra vez"). Devuelve (usuario, None) si es correcta, o
    (None, (response, status)) listo para retornar si no.
    """
    usuario = Usuario.query.get(int(get_jwt_identity()))
    if not usuario:
        return None, (jsonify({"msg": "Usuario no encontrado."}), 404)
    if not password:
        return None, (jsonify({"msg": "Captura tu contraseña para confirmar el cambio."}), 400)
    if not usuario.check_password(password):
        return None, (jsonify({"msg": "Contraseña incorrecta."}), 401)
    return usuario, None


# _usuario_propietario_de() vivia aqui; se movio a utils/alcance.py como
# usuario_propietario_de() (publica) porque utils/recibos.py (recibos de
# venta/membresia) necesita exactamente la misma resolucion
# Gimnasio -> Organizacion -> sede principal -> Usuario Propietario.


def _enviar_aviso_cambio(id_gimnasio: int, proveedor: str, accion: str,
                          pista_anterior: str | None, pista_nueva: str | None) -> None:
    """
    SCRUM-221 / HU-50, criterio 4: aviso por correo al propietario en cada
    cambio, con cuenta anterior y nueva. Nunca se manda la credencial en
    claro -- solo las mismas "pistas" enmascaradas que ya se muestran en
    la interfaz. Falla silenciosamente si Mail no esta configurado, igual
    que _enviar_bienvenida en onboarding.py: un correo que no llega no debe
    tumbar el cambio que ya se guardo.
    """
    propietario = usuario_propietario_de(id_gimnasio)
    if not propietario or not propietario.email:
        logger.warning(
            "No se encontró el propietario para avisar el cambio de cuenta de cobro (gimnasio %s)",
            id_gimnasio,
        )
        return
    nombre_prov = PROVEEDORES_INFO.get(proveedor, {}).get("nombre", proveedor)
    ahora = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    try:
        msg = Message(
            subject=f"GymPro — Cambio en tu cuenta de cobro de {nombre_prov}",
            sender=remitente("MAIL_SENDER_FACTURACION"),
            recipients=[propietario.email],
            html=f"""
<html><body style="font-family:sans-serif;color:#333;max-width:600px;margin:auto">
  <div style="background:#1a1a2e;padding:32px;border-radius:12px 12px 0 0;text-align:center">
    <h1 style="color:#fff;margin:0">GYM<span style="color:#6c63ff">PRO</span></h1>
  </div>
  <div style="background:#f9f9f9;padding:32px;border-radius:0 0 12px 12px">
    <h2>Se {accion} tu cuenta de cobro de {nombre_prov}</h2>
    <p>Fecha: {ahora}</p>
    <table style="width:100%;border-collapse:collapse;margin:16px 0">
      <tr><td style="padding:6px 0;color:#888">Cuenta anterior</td>
          <td style="padding:6px 0;font-family:monospace">{pista_anterior or "— (no había ninguna)"}</td></tr>
      <tr><td style="padding:6px 0;color:#888">Cuenta nueva</td>
          <td style="padding:6px 0;font-family:monospace">{pista_nueva or "— (eliminada)"}</td></tr>
    </table>
    <p style="background:#fff3cd;border:1px solid #ffe69c;border-radius:8px;padding:14px;color:#664d03">
      Durante las próximas 24 horas, los cobros que ya estaban en curso seguirán
      entrando a la cuenta anterior.
    </p>
    <p style="color:#888;font-size:13px">
      Si tú no hiciste este cambio, contacta de inmediato a {correo_contacto()}<br>
      Equipo GymPro SaaS
    </p>
  </div>
</body></html>
""",
        )
        mail.send(msg)
        logger.info("Aviso de cambio de cuenta de cobro enviado a %s", propietario.email)
    except Exception as exc:
        logger.warning("No se pudo enviar el aviso de cambio de cuenta de cobro: %s", exc)


# ── Logica compartida (misma implementacion para "propia" y "de sucursal") ──

def _listar(id_gimnasio: int):
    existentes = {
        (c.proveedor.value if hasattr(c.proveedor, "value") else c.proveedor): c
        for c in ConfiguracionPasarela.query.filter_by(id_gimnasio=id_gimnasio).all()
    }
    salida = []
    for prov, info in PROVEEDORES_INFO.items():
        cfg = existentes.get(prov)
        # SCRUM-224: en produccion no se deja elegir modo de prueba -- el
        # frontend desactiva el selector con esta bandera, y _guardar() la
        # hace cumplir tambien del lado del servidor, no solo en la UI.
        forzado = plataforma_en_produccion()
        base = {
            "proveedor": prov,
            "nombre":    info["nombre"],
            "campos":    info["campos"],
            "ayuda":     info["ayuda"],
            "modo_forzado": forzado,
        }
        if cfg:
            base.update(cfg.to_dict(pista=_pista(cfg)))
        else:
            base.update({
                "modo": "live" if forzado else "sandbox", "activo": False, "moneda": "MXN",
                "titular_cuenta": None, "configurado": False,
                "credencial_pista": None, "verificado_en": None, "ultimo_error": None,
            })
        salida.append(base)
    return jsonify({
        "pasarelas": salida,
        "cifrado_disponible": crypto_disponible(),
    }), 200


def _guardar(id_gimnasio: int, proveedor: str, data: dict):
    """
    Guarda o actualiza las credenciales del proveedor para `id_gimnasio`.

    Body JSON:
      { "credenciales": {...}, "modo": "sandbox|live", "moneda": "MXN",
        "titular_cuenta": "Gimnasio X", "activo": true,
        "password_actual": "..." }   <- SCRUM-221/HU-50, criterio 3

    Si 'credenciales' viene vacío se conservan las ya guardadas (permite
    cambiar solo el modo o la moneda sin volver a capturar los secretos,
    y en ese caso NO se pide contraseña ni se manda correo -- no es un
    cambio de A QUÉ CUENTA entra el dinero, solo de un dato secundario).
    """
    if not crypto_disponible():
        return jsonify({
            "msg": "El servidor no tiene configurada la clave de cifrado "
                   "(PAYMENTS_ENCRYPTION_KEY). Contacta al administrador de la plataforma."
        }), 503

    nuevas = data.get("credenciales") or {}
    nuevas = {k: str(v).strip() for k, v in nuevas.items() if str(v or "").strip()}
    modo   = (data.get("modo") or "sandbox").lower()
    if modo not in ("sandbox", "live"):
        return jsonify({"msg": "El modo debe ser 'sandbox' o 'live'"}), 400
    if plataforma_en_produccion():
        # SCRUM-224: este entorno no admite pasarelas en modo de prueba --
        # se fuerza a "live" sin importar lo que haya mandado el formulario
        # (incluso si el frontend quedo con una version vieja en cache).
        modo = "live"

    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=id_gimnasio, proveedor=proveedor).first()
    es_nuevo = cfg is None
    if es_nuevo:
        cfg = ConfiguracionPasarela(id_gimnasio=id_gimnasio, proveedor=proveedor)
        db.session.add(cfg)

    cambia_cuenta = bool(nuevas)
    if cambia_cuenta:
        # Reautenticacion (criterio 3) solo cuando de verdad se está
        # fijando o cambiando la cuenta -- no para ajustar moneda/modo.
        usuario, err = _verificar_password(data.get("password_actual"))
        if err:
            return err

        requeridos = [c["clave"] for c in PROVEEDORES_INFO[proveedor]["campos"] if c["secreto"]]
        actuales = descifrar_dict(cfg.credenciales)
        combinadas = {**actuales, **nuevas}
        faltantes = [c for c in requeridos if not combinadas.get(c)]
        if faltantes:
            return jsonify({"msg": f"Faltan credenciales obligatorias: {', '.join(faltantes)}"}), 400

        pista_anterior = _pista(cfg) if not es_nuevo else None
        # Ventana de enfriamiento de 24h (criterio 6): solo tiene sentido
        # conservar un "anterior" si YA había algo configurado.
        if not es_nuevo and cfg.credenciales:
            cfg.credenciales_anteriores = cfg.credenciales
            cfg.cambiado_en = datetime.now(timezone.utc)

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

    pista_nueva = _pista(cfg)

    if cambia_cuenta:
        registrar_evento(
            int(get_jwt_identity()), "pasarela.cambiar_cuenta",
            id_gimnasio=id_gimnasio,
            detalle={
                "proveedor": proveedor,
                "pista_anterior": pista_anterior,
                "pista_nueva": pista_nueva,
                "alta_inicial": es_nuevo,
            },
            ip_origen=request.remote_addr,
        )

    db.session.commit()
    logger.info("Pasarela %s actualizada para gimnasio %s", proveedor, id_gimnasio)

    if cambia_cuenta:
        _enviar_aviso_cambio(
            id_gimnasio, proveedor,
            "configuró" if es_nuevo else "cambió",
            pista_anterior, pista_nueva,
        )

    return jsonify({"msg": "Configuración guardada", "pasarela": cfg.to_dict(pista=pista_nueva)}), 200


def _probar(id_gimnasio: int, proveedor: str):
    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=id_gimnasio, proveedor=proveedor).first()
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


def _toggle(id_gimnasio: int, proveedor: str):
    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=id_gimnasio, proveedor=proveedor).first()
    if not cfg or not cfg.credenciales:
        return jsonify({"msg": "Configura las credenciales antes de activar este método."}), 400

    cfg.activo = not cfg.activo
    registrar_evento(
        int(get_jwt_identity()),
        "pasarela.activar" if cfg.activo else "pasarela.desactivar",
        id_gimnasio=id_gimnasio,
        detalle={"proveedor": proveedor},
        ip_origen=request.remote_addr,
    )
    db.session.commit()
    estado = "activado" if cfg.activo else "desactivado"
    return jsonify({"msg": f"Método de pago {estado}", "activo": cfg.activo}), 200


def _eliminar(id_gimnasio: int, proveedor: str, data: dict):
    """
    Elimina las credenciales guardadas. SCRUM-221/HU-50: pide contraseña
    (criterio 3, es un cambio tan drástico como rotar la cuenta) y notifica
    (criterio 4). No aplica la ventana de enfriamiento de 24h (criterio 6):
    al ser borrado físico de la fila no queda "cuenta anterior" a la que
    enrutar un cobro -- un intento de cobro después de esto simplemente
    falla con el mismo mensaje de "no configurado" que ya existía, lo cual
    es seguro (nunca cobra a la cuenta equivocada), solo que no sigue
    cobrando a la vieja. Decisión de alcance documentada aquí a propósito.
    """
    cfg = ConfiguracionPasarela.query.filter_by(
        id_gimnasio=id_gimnasio, proveedor=proveedor).first()
    if not cfg:
        return jsonify({"msg": "Configuración eliminada"}), 200

    usuario, err = _verificar_password(data.get("password_actual"))
    if err:
        return err

    pista_anterior = _pista(cfg)
    registrar_evento(
        int(get_jwt_identity()), "pasarela.eliminar",
        id_gimnasio=id_gimnasio,
        detalle={"proveedor": proveedor, "pista_anterior": pista_anterior},
        ip_origen=request.remote_addr,
    )
    db.session.delete(cfg)
    db.session.commit()

    _enviar_aviso_cambio(id_gimnasio, proveedor, "eliminó", pista_anterior, None)

    return jsonify({"msg": "Configuración eliminada"}), 200


# ─────────────────────────────────────────────────────────────────────────────
# Rutas "propias" -- igual URL que antes de HU-50, operan sobre g.tenant_id
# ─────────────────────────────────────────────────────────────────────────────

@pasarelas_bp.route("", methods=["GET"])
@pasarelas_bp.route("/", methods=["GET"])
@jwt_required()
@require_tenant
def listar_pasarelas():
    """Estado de configuración de cada proveedor para el gimnasio actual."""
    if not _puede_leer(get_jwt()):
        return jsonify({"msg": "No tienes permiso para ver la configuración de cobros"}), 403
    return _listar(g.tenant_id)


@pasarelas_bp.route("/<proveedor>", methods=["PUT"])
@jwt_required()
@require_tenant
def guardar_pasarela(proveedor):
    if not _puede_escribir(get_jwt()):
        return jsonify({"msg": "Solo el propietario puede configurar la cuenta de cobro"}), 403
    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err
    return _guardar(g.tenant_id, proveedor, request.get_json() or {})


@pasarelas_bp.route("/<proveedor>/probar", methods=["POST"])
@jwt_required()
@require_tenant
def probar_pasarela(proveedor):
    if not _puede_escribir(get_jwt()):
        return jsonify({"msg": "Solo el propietario puede probar los cobros"}), 403
    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err
    return _probar(g.tenant_id, proveedor)


@pasarelas_bp.route("/<proveedor>/toggle", methods=["PATCH"])
@jwt_required()
@require_tenant
def toggle_pasarela(proveedor):
    if not _puede_escribir(get_jwt()):
        return jsonify({"msg": "Solo el propietario puede cambiar los cobros"}), 403
    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err
    return _toggle(g.tenant_id, proveedor)


@pasarelas_bp.route("/<proveedor>", methods=["DELETE"])
@jwt_required()
@require_tenant
def eliminar_pasarela(proveedor):
    if not _puede_escribir(get_jwt()):
        return jsonify({"msg": "Solo el propietario puede eliminar los cobros"}), 403
    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err
    return _eliminar(g.tenant_id, proveedor, request.get_json() or {})


# ─────────────────────────────────────────────────────────────────────────────
# Rutas "de sucursal" -- NUEVAS en HU-50: el propietario, desde su propia
# sesión, administra la cuenta de cobro de CUALQUIER sucursal de su
# organización (criterio 1), sin impersonarla.
# ─────────────────────────────────────────────────────────────────────────────

@pasarelas_bp.route("/sucursal/<int:id_gimnasio>", methods=["GET"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def listar_pasarelas_sucursal(id_gimnasio):
    if not _sucursal_de_mi_organizacion(id_gimnasio):
        return jsonify({"msg": "Sucursal no encontrada"}), 404
    return _listar(id_gimnasio)


@pasarelas_bp.route("/sucursal/<int:id_gimnasio>/<proveedor>", methods=["PUT"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def guardar_pasarela_sucursal(id_gimnasio, proveedor):
    if not _sucursal_de_mi_organizacion(id_gimnasio):
        return jsonify({"msg": "Sucursal no encontrada"}), 404
    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err
    return _guardar(id_gimnasio, proveedor, request.get_json() or {})


@pasarelas_bp.route("/sucursal/<int:id_gimnasio>/<proveedor>/probar", methods=["POST"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def probar_pasarela_sucursal(id_gimnasio, proveedor):
    if not _sucursal_de_mi_organizacion(id_gimnasio):
        return jsonify({"msg": "Sucursal no encontrada"}), 404
    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err
    return _probar(id_gimnasio, proveedor)


@pasarelas_bp.route("/sucursal/<int:id_gimnasio>/<proveedor>/toggle", methods=["PATCH"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def toggle_pasarela_sucursal(id_gimnasio, proveedor):
    if not _sucursal_de_mi_organizacion(id_gimnasio):
        return jsonify({"msg": "Sucursal no encontrada"}), 404
    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err
    return _toggle(id_gimnasio, proveedor)


@pasarelas_bp.route("/sucursal/<int:id_gimnasio>/<proveedor>", methods=["DELETE"])
@jwt_required()
@require_role(ROL_PROPIETARIO)
@require_tenant
def eliminar_pasarela_sucursal(id_gimnasio, proveedor):
    if not _sucursal_de_mi_organizacion(id_gimnasio):
        return jsonify({"msg": "Sucursal no encontrada"}), 404
    proveedor, err = _validar_proveedor(proveedor)
    if err:
        return err
    return _eliminar(id_gimnasio, proveedor, request.get_json() or {})
