"""
utils/chat_moderacion.py — Reglas de bloqueo/suspensión del chat (HU-12).

Punto único de verdad para la pregunta que training.py (miembro) y
trainer_routes.py (entrenador) necesitan responder ANTES de leer o
enviar un mensaje: ¿puede este par de usuarios intercambiar mensajes
ahora mismo? Centralizarlo aquí evita que las reglas de bloqueo se
reimplementen distinto en cada lado del chat (bug real que ya pasó en
este proyecto con el filtro de tenant, ver utils/tenant.py).

Uso típico en un endpoint de chat:

    from app.utils.chat_moderacion import existe_bloqueo, verificar_acceso_chat

    error = verificar_acceso_chat(mid, trainer_id)
    if error:
        return jsonify(error[0]), error[1]
"""
from app.extensions import db
from app.models.pg.usuario import Usuario
from app.models.pg.bloqueo_chat import BloqueoChat


def existe_bloqueo(id_usuario_a: int, id_usuario_b: int) -> bool:
    """
    True si CUALQUIERA de los dos bloqueó al otro.

    AC1 ("el bloqueo impide nuevos mensajes en ambos sentidos") no
    distingue quién bloqueó a quién -- una vez que una de las partes
    bloquea, la conversación se cierra para los dos, así que la
    dirección del bloqueo no importa para esta pregunta.
    """
    return db.session.query(BloqueoChat.id).filter(
        db.or_(
            db.and_(BloqueoChat.id_usuario_bloqueador == id_usuario_a,
                    BloqueoChat.id_usuario_bloqueado == id_usuario_b),
            db.and_(BloqueoChat.id_usuario_bloqueador == id_usuario_b,
                    BloqueoChat.id_usuario_bloqueado == id_usuario_a),
        )
    ).first() is not None


def verificar_acceso_chat(id_actor: int, id_contraparte: int):
    """
    Valida, en este orden, que `id_actor` pueda abrir o escribir en la
    conversación con `id_contraparte`:

      1. Su propio chat no está suspendido (AC3 -- la suspensión es por
         usuario, no por conversación: si al Propietario le suspendió el
         chat a un entrenador, ese entrenador no puede hablar con NINGÚN
         miembro, no solo con el que lo reportó).
      2. No existe un bloqueo vigente entre ambos (AC1).

    Devuelve None si todo está en orden, o una tupla
    (body_json, status_code) lista para `return jsonify(body), status`
    en el endpoint que llama.
    """
    actor = Usuario.query.get(id_actor)
    if actor and actor.chat_suspendido:
        return (
            {"error": "Tu acceso al chat está suspendido. Contacta al propietario del gimnasio."},
            403,
        )

    if existe_bloqueo(id_actor, id_contraparte):
        return (
            {"error": "No es posible intercambiar mensajes con este usuario."},
            403,
        )

    return None
