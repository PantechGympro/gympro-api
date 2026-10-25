"""
owner_gym/reportes_automaticos.py — Automatizar el reporte ejecutivo por correo.

Petición explícita del usuario: poder elegir qué tipo de reporte generar,
con qué filtros y cada cuándo (día/semana/mes/año), para que se envíe solo
por correo al Propietario y así tener mejor control del corte de caja sin
entrar a Reportes a descargarlo a mano cada vez.

Reutiliza del módulo hermano reportes_negocio.py:
  - _resolver_gimnasio_objetivo()  → misma lógica multi-sucursal que el
    reporte manual (Propietario: cualquier sucursal de su organización;
    Administrador: solo las que tiene asignadas).
  - _construir_reporte_pdf()       → el mismo documento, generado fuera del
    ciclo request/response para que lo pueda llamar el job programado.
  - SECCIONES_VALIDAS / MESES_ES   → mismo catálogo, para no tener dos
    listas de secciones que puedan desincronizarse.

Endpoints (todos bajo /api/owner_gym, Propietario/Administrador, mismo
candado de plan que el reporte manual -- reportes_pdf):
  GET    /reportes/automaticos                listar configuraciones
  POST   /reportes/automaticos                crear una
  PUT    /reportes/automaticos/<id>           editar una
  DELETE /reportes/automaticos/<id>           eliminar una
  POST   /reportes/automaticos/<id>/ejecutar  mandarla ya (probar)

Job programado: tick_reportes_automaticos(app), registrado en
notifications.py::init_scheduler(). Corre una vez al día y decide, por
configuración activa, si hoy le toca generarse según su frecuencia.
"""
from datetime import datetime, timezone

from flask import Blueprint, request, jsonify, g
from flask_jwt_extended import jwt_required, get_jwt, get_jwt_identity

from app.extensions import db
from app.models.pg.gimnasio import Gimnasio
from app.models.pg.reporte_automatico import (
    ConfiguracionReporteAutomatico, FRECUENCIAS_VALIDAS, FRECUENCIA_LABEL,
    MOMENTOS_VALIDOS,
)
from app.utils.tenant import require_tenant
from app.utils.security import require_role, require_funcion_plan
from app.utils.alcance import usuario_tiene_alcance, usuario_propietario_de
from app.utils.timezone import APP_TZ, local_today_bounds_naive, local_now_naive
from app.constants.roles import normalizar_rol, ROL_ADMINISTRADOR
from app.routes.owner_gym.reportes_negocio import (
    _resolver_gimnasio_objetivo, _construir_reporte_pdf, SECCIONES_VALIDAS, MESES_ES,
)

reportes_automaticos_bp = Blueprint("reportes_automaticos", __name__)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _verificar_acceso_gimnasio(id_gimnasio):
    """
    True si el usuario actual puede administrar reportes automáticos de
    `id_gimnasio`. Mismo criterio que _resolver_gimnasio_objetivo() en
    reportes_negocio.py, pero partiendo de un id_gimnasio ya conocido (el
    dueño de una fila existente), no de un query param.
    """
    if id_gimnasio == g.tenant_id:
        return True
    mi_gym  = Gimnasio.query.get(g.tenant_id)
    destino = Gimnasio.query.get(id_gimnasio)
    if not mi_gym or not destino or destino.id_organizacion != mi_gym.id_organizacion:
        return False
    claims = get_jwt()
    if normalizar_rol(claims.get("role")) == ROL_ADMINISTRADOR:
        actor_id = int(get_jwt_identity())
        return usuario_tiene_alcance(actor_id, [ROL_ADMINISTRADOR], id_gimnasio_objetivo=id_gimnasio)
    return True


def _periodo_para_frecuencia(frecuencia, momento_envio, hoy0):
    """
    El periodo a reportar, visto desde `hoy0` (medianoche local del día en
    que corre el job).

    Con momento_envio="dia_siguiente" (el comportamiento original) es el
    periodo que ya CERRÓ -- nunca el periodo en curso, para que el corte de
    caja llegue completo, no a medias. Con "mismo_dia" es el periodo EN
    CURSO que se está cerrando justo hoy (se manda esa misma noche, con lo
    acumulado hasta la hora configurada).
    Devuelve (desde, hasta, etiqueta), mismo formato que _resolver_periodo().
    """
    from datetime import timedelta

    mismo_dia = momento_envio == "mismo_dia"

    if frecuencia == "diaria":
        desde = hoy0 if mismo_dia else hoy0 - timedelta(days=1)
        hasta = desde + timedelta(days=1)
        return desde, hasta, desde.strftime("%d/%m/%Y")

    if frecuencia == "semanal":
        lunes_esta_semana = hoy0 - timedelta(days=hoy0.weekday())
        desde = lunes_esta_semana if mismo_dia else lunes_esta_semana - timedelta(days=7)
        hasta = desde + timedelta(days=7)
        etiqueta = (f"Semana del {desde.strftime('%d/%m/%Y')} al "
                    f"{(hasta - timedelta(days=1)).strftime('%d/%m/%Y')}")
        return desde, hasta, etiqueta

    if frecuencia == "mensual":
        primero_mes_actual = datetime(hoy0.year, hoy0.month, 1)
        if mismo_dia:
            mes_sig  = 1 if hoy0.month == 12 else hoy0.month + 1
            anio_sig = hoy0.year + 1 if hoy0.month == 12 else hoy0.year
            desde = primero_mes_actual
            hasta = datetime(anio_sig, mes_sig, 1)
            return desde, hasta, f"{MESES_ES[hoy0.month - 1].capitalize()} {hoy0.year}"
        mes_ant  = 12 if hoy0.month == 1 else hoy0.month - 1
        anio_ant = hoy0.year - 1 if hoy0.month == 1 else hoy0.year
        desde = datetime(anio_ant, mes_ant, 1)
        hasta = primero_mes_actual
        return desde, hasta, f"{MESES_ES[mes_ant - 1].capitalize()} {anio_ant}"

    # anual
    if mismo_dia:
        desde = datetime(hoy0.year, 1, 1)
        hasta = datetime(hoy0.year + 1, 1, 1)
        return desde, hasta, f"Año {hoy0.year}"
    desde = datetime(hoy0.year - 1, 1, 1)
    hasta = datetime(hoy0.year, 1, 1)
    return desde, hasta, f"Año {hoy0.year - 1}"


def _hoy_es_cierre(frecuencia, momento_envio, hoy0):
    """
    True si `hoy0` es el día en que, según `momento_envio`, le toca a esta
    frecuencia dispararse -- "dia_siguiente" el día DESPUÉS de que el
    periodo cerró (comportamiento original: lunes/día 1/1-enero); "mismo_dia"
    el ÚLTIMO día del periodo que se está cerrando (domingo/último día del
    mes/31-diciembre). Diaria es igual en ambos casos: todos los días son,
    a la vez, "el día siguiente" de ayer y "el mismo día" de hoy.
    """
    from datetime import timedelta

    if frecuencia == "diaria":
        return True
    if frecuencia == "semanal":
        return hoy0.weekday() == (6 if momento_envio == "mismo_dia" else 0)  # domingo | lunes
    if frecuencia == "mensual":
        if momento_envio == "mismo_dia":
            return (hoy0 + timedelta(days=1)).month != hoy0.month
        return hoy0.day == 1
    # anual
    if momento_envio == "mismo_dia":
        return hoy0.month == 12 and hoy0.day == 31
    return hoy0.day == 1 and hoy0.month == 1


def _ya_ejecuto_hoy(cfg, hoy0):
    """Compara en hora local -- ultima_ejecucion se guarda en UTC."""
    if not cfg.ultima_ejecucion:
        return False
    ultima = cfg.ultima_ejecucion
    if ultima.tzinfo is not None:
        ultima = ultima.astimezone(APP_TZ).replace(tzinfo=None)
    return ultima.date() == hoy0.date()


def _validar_payload(data, *, crear):
    """
    Valida y normaliza el payload de crear/actualizar una configuración.
    En creación los campos ausentes toman su valor por omisión; en edición
    parcial solo se tocan los campos que llegaron en el body.
    Devuelve (campos_limpios, None) o (None, (response, status)).
    """
    campos = {}

    if crear or "nombre" in data:
        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            return None, (jsonify({"msg": "El nombre es obligatorio"}), 400)
        campos["nombre"] = nombre[:120]

    if crear or "frecuencia" in data:
        frecuencia = (data.get("frecuencia") or "").strip()
        if frecuencia not in FRECUENCIAS_VALIDAS:
            return None, (jsonify({
                "msg": f"frecuencia debe ser una de: {', '.join(FRECUENCIAS_VALIDAS)}",
            }), 400)
        campos["frecuencia"] = frecuencia

    if crear or "secciones" in data:
        pedidas_raw = data.get("secciones")
        if isinstance(pedidas_raw, list):
            pedidas = [str(s).strip() for s in pedidas_raw if str(s).strip()]
        else:
            pedidas = [s.strip() for s in (pedidas_raw or "").split(",") if s.strip()]
        secciones = [s for s in pedidas if s in SECCIONES_VALIDAS] or SECCIONES_VALIDAS
        campos["secciones"] = ",".join(secciones)

    if crear or "comparar" in data:
        campos["comparar"] = bool(data.get("comparar", True))
    if crear or "graficas" in data:
        campos["graficas"] = bool(data.get("graficas", True))
    if crear or "activo" in data:
        campos["activo"] = bool(data.get("activo", True))

    if crear or "momento_envio" in data:
        momento = (data.get("momento_envio") or "dia_siguiente").strip()
        if momento not in MOMENTOS_VALIDOS:
            return None, (jsonify({
                "msg": f"momento_envio debe ser uno de: {', '.join(MOMENTOS_VALIDOS)}",
            }), 400)
        campos["momento_envio"] = momento

    if crear or "hora_envio" in data:
        try:
            hora = int(data.get("hora_envio", 6))
        except (TypeError, ValueError):
            return None, (jsonify({"msg": "hora_envio debe ser un número entero entre 0 y 23"}), 400)
        if not (0 <= hora <= 23):
            return None, (jsonify({"msg": "hora_envio debe ser un número entero entre 0 y 23"}), 400)
        campos["hora_envio"] = hora

    return campos, None


def _enviar_reporte_automatico(cfg, hoy0=None):
    """
    Genera y manda por correo el reporte de UNA configuración. No hace
    commit -- lo decide el llamador (el job procesa varias filas en una
    sola transacción; el endpoint manual hace una sola).
    Devuelve {"ok": bool, "destino": str|None, "error": str|None}.
    """
    from flask_mail import Message
    from app.extensions import mail
    from app.utils.email_remitentes import remitente

    if hoy0 is None:
        hoy0, _ = local_today_bounds_naive()

    try:
        gym = Gimnasio.query.get(cfg.id_gimnasio)
        if not gym:
            raise RuntimeError("Gimnasio no encontrado")

        propietario = usuario_propietario_de(cfg.id_gimnasio)
        destino = propietario.email if propietario and propietario.email else None
        if not destino:
            raise RuntimeError("El gimnasio no tiene un Propietario con correo registrado")

        desde, hasta, etiqueta = _periodo_para_frecuencia(cfg.frecuencia, cfg.momento_envio, hoy0)
        secciones = cfg.lista_secciones() or SECCIONES_VALIDAS

        pdf_bytes, nombre_archivo = _construir_reporte_pdf(
            gym, desde, hasta, etiqueta, secciones, cfg.comparar, cfg.graficas,
        )

        asunto = (f"Corte de caja {FRECUENCIA_LABEL.get(cfg.frecuencia, cfg.frecuencia).lower()} "
                  f"— {gym.nombre or 'tu gimnasio'} ({etiqueta})")
        html = (
            "<div style=\"font-family:sans-serif;max-width:600px;margin:0 auto;"
            "background:#0f0f1a;color:#fff;padding:32px;border-radius:12px;\">"
            "<h2 style=\"color:#fbe379;margin-top:0;\">GymPro — Reporte automático</h2>"
            f"<p>Adjunto va el reporte <strong>{cfg.nombre}</strong> "
            f"correspondiente a <strong>{etiqueta}</strong>.</p>"
            "<p style=\"margin-top:28px;font-size:12px;color:#888;\">"
            "GymPro SaaS — Este mensaje es automático, por favor no respondas.</p></div>"
        )
        msg = Message(subject=asunto, recipients=[destino], html=html,
                       sender=remitente("MAIL_SENDER_FACTURACION"))
        msg.attach(nombre_archivo, "application/pdf", pdf_bytes)
        mail.send(msg)

        cfg.ultima_ejecucion = datetime.now(timezone.utc)
        cfg.ultimo_error = None
        db.session.add(cfg)
        return {"ok": True, "destino": destino, "error": None}
    except Exception as exc:
        cfg.ultimo_error = str(exc)[:300]
        db.session.add(cfg)
        return {"ok": False, "destino": None, "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# Job programado (ver notifications.py::init_scheduler)
# ═══════════════════════════════════════════════════════════════════════════

_reportes_auto_status = {"ultima_ejecucion": None, "ultimo_resultado": None}


def tick_reportes_automaticos(app=None):
    """
    Corre cada hora en punto. Por cada configuración activa cuya hora
    configurada (hora_envio) coincide con la hora local actual, y a la que
    hoy le toca dispararse (según su frecuencia y momento_envio -- ver
    _hoy_es_cierre), y que no se haya mandado ya hoy, genera el PDF del
    periodo correspondiente y lo envía por correo al Propietario.

    No bloqueante por fila: un fallo en una configuración (SMTP, datos del
    gimnasio, lo que sea) no debe impedir que las demás se procesen -- se
    registra en ultimo_error de esa fila, nunca tumba el job completo.
    """
    ctx = app.app_context() if app else None
    if ctx:
        ctx.push()

    try:
        hoy0, _ = local_today_bounds_naive()
        hora_actual = local_now_naive().hour

        configs = ConfiguracionReporteAutomatico.query.filter_by(activo=True).all()
        procesadas = enviadas = fallidas = 0

        for cfg in configs:
            if cfg.hora_envio != hora_actual:
                continue
            if not _hoy_es_cierre(cfg.frecuencia, cfg.momento_envio, hoy0):
                continue
            if _ya_ejecuto_hoy(cfg, hoy0):
                continue
            procesadas += 1
            resultado = _enviar_reporte_automatico(cfg, hoy0)
            if resultado["ok"]:
                enviadas += 1
            else:
                fallidas += 1

        db.session.commit()

        resultado = {
            "ejecutado_en":             hoy0.isoformat(),
            "configuraciones_activas":  len(configs),
            "procesadas":               procesadas,
            "enviadas":                 enviadas,
            "fallidas":                 fallidas,
        }
        _reportes_auto_status["ultima_ejecucion"] = hoy0.isoformat()
        _reportes_auto_status["ultimo_resultado"] = resultado
        return resultado
    finally:
        if ctx:
            ctx.pop()


# ═══════════════════════════════════════════════════════════════════════════
# Endpoints
# ═══════════════════════════════════════════════════════════════════════════

@reportes_automaticos_bp.route("/reportes/automaticos", methods=["GET"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
@require_funcion_plan("reportes_pdf")
def listar_reportes_automaticos():
    gym_id, error = _resolver_gimnasio_objetivo()
    if error:
        return error
    configs = (
        ConfiguracionReporteAutomatico.query
        .filter_by(id_gimnasio=gym_id)
        .order_by(ConfiguracionReporteAutomatico.creado_en.desc())
        .all()
    )
    return jsonify([c.to_dict() for c in configs]), 200


@reportes_automaticos_bp.route("/reportes/automaticos", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
@require_funcion_plan("reportes_pdf")
def crear_reporte_automatico():
    gym_id, error = _resolver_gimnasio_objetivo()
    if error:
        return error

    data = request.get_json(silent=True) or {}
    campos, error = _validar_payload(data, crear=True)
    if error:
        return error

    cfg = ConfiguracionReporteAutomatico(
        id_gimnasio=gym_id,
        id_usuario_creador=int(get_jwt_identity()),
        **campos,
    )
    db.session.add(cfg)
    db.session.commit()
    return jsonify(cfg.to_dict()), 201


@reportes_automaticos_bp.route("/reportes/automaticos/<int:config_id>", methods=["PUT"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
@require_funcion_plan("reportes_pdf")
def actualizar_reporte_automatico(config_id):
    cfg = ConfiguracionReporteAutomatico.query.get(config_id)
    if not cfg or not _verificar_acceso_gimnasio(cfg.id_gimnasio):
        return jsonify({"msg": "Configuración no encontrada"}), 404

    data = request.get_json(silent=True) or {}
    campos, error = _validar_payload(data, crear=False)
    if error:
        return error

    for campo, valor in campos.items():
        setattr(cfg, campo, valor)
    db.session.commit()
    return jsonify(cfg.to_dict()), 200


@reportes_automaticos_bp.route("/reportes/automaticos/<int:config_id>", methods=["DELETE"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
@require_funcion_plan("reportes_pdf")
def eliminar_reporte_automatico(config_id):
    cfg = ConfiguracionReporteAutomatico.query.get(config_id)
    if not cfg or not _verificar_acceso_gimnasio(cfg.id_gimnasio):
        return jsonify({"msg": "Configuración no encontrada"}), 404

    db.session.delete(cfg)
    db.session.commit()
    return jsonify({"msg": "Configuración eliminada"}), 200


@reportes_automaticos_bp.route("/reportes/automaticos/<int:config_id>/ejecutar", methods=["POST"])
@jwt_required()
@require_role("owner_gym", "Administrador")
@require_tenant
@require_funcion_plan("reportes_pdf")
def ejecutar_reporte_automatico_ahora(config_id):
    """
    Dispara de inmediato el envío de esta configuración (botón "Probar" en
    el frontend) -- para confirmar que el correo del Propietario está bien
    antes de depender del job nocturno.
    """
    cfg = ConfiguracionReporteAutomatico.query.get(config_id)
    if not cfg or not _verificar_acceso_gimnasio(cfg.id_gimnasio):
        return jsonify({"msg": "Configuración no encontrada"}), 404

    resultado = _enviar_reporte_automatico(cfg)
    db.session.commit()
    if not resultado["ok"]:
        return jsonify({"msg": f"No se pudo enviar: {resultado['error']}"}), 502
    return jsonify({"msg": f"Reporte enviado a {resultado['destino']}"}), 200
