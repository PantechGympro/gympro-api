from flask import Blueprint, request, jsonify
from flask_jwt_extended import create_access_token
from bson.objectid import ObjectId
from app.mongo import get_db
from app.extensions import limiter

# Importamos los nuevos modelos DAO
from app.models.user import User
from app.models.role import Role
from app.models.miembro import Miembro
from app.models.miembro_membresia import MiembroMembresia

auth_bp = Blueprint("auth", __name__)

@auth_bp.route("/login", methods=["POST"])
@limiter.limit("5 per minute; 30 per hour")
def login():
    data = request.get_json()

    if not data or not data.get("email") or not data.get("password"):
        return jsonify({"msg": "Datos incompletos"}), 400

    # Cambiamos User.query.filter_by a User.find_by_email
    user = User.find_by_email(data["email"])

    if not user or not user.check_password(data["password"]):
        return jsonify({"msg": "Credenciales inválidas"}), 401

    if not user.activo:
        return jsonify({"msg": "Usuario inactivo"}), 403

    # Buscamos el rol usando el método de clase
    role = Role.find_by_id(user.id_role)
    nombre_rol = role.nombre if role else "Desconocido"

    # ---------------------------------------------------------
    # VARIABLES DE PERFIL
    # ---------------------------------------------------------
    nombre_membresia = "Sin Plan"
    access_level = "premium"
    perfil_completo = True
    peso_inicial = None

    # ---------------------------------------------------------
    # LÓGICA PARA MIEMBRO
    # ---------------------------------------------------------
    # En Mongo, es mejor verificar por nombre de rol ya que los IDs son ObjectId,
    # pero si usas un sistema mixto o el ID en duro, también podemos comparar el string.
    if nombre_rol == "Miembro" or str(user.id_role) == "4":
        access_level = "basico"
        db = get_db()

        # Buscamos el miembro asociado al usuario
        miembro_data = db.miembros.find_one({"id_usuario": user._id})

        if miembro_data:
            # Instanciamos el miembro para usar sus propiedades
            miembro = Miembro(**miembro_data)
            
            peso_inicial = float(miembro.peso_inicial) if miembro.peso_inicial else None
            perfil_completo = peso_inicial is not None

            # Buscamos la membresía activa más reciente
            membresia_activa_data = db.miembro_membresia.find(
                {
                    "id_miembro": miembro._id,
                    "estado": "Activa"
                }
            ).sort("fecha_fin", -1).limit(1)
            
            membresia_activa_lista = list(membresia_activa_data)

            if membresia_activa_lista:
                mm_activa = membresia_activa_lista[0]
                
                # Buscamos los detalles de la membresía para sacar el nombre
                membresia_info = db.membresias.find_one({"_id": mm_activa["id_membresia"]})
                
                if membresia_info:
                    nombre_membresia = membresia_info.get("nombre", "Sin Plan")

                    planes_premium = ["Premium", "VIP"]
                    if any(p in nombre_membresia for p in planes_premium):
                        access_level = "premium"

    # ---------------------------------------------------------
    # CREAR TOKEN
    # ---------------------------------------------------------
    # Convertimos el ObjectId a string para el identity del token
    user_id_str = str(user._id)
    
    access_token = create_access_token(
        identity=user_id_str,
        additional_claims={
            "email": user.email,
            "role": nombre_rol,
            "plan": nombre_membresia,
            "access_level": access_level,
            "perfil_completo": perfil_completo,
            "peso_inicial": peso_inicial
        }
    )

    return jsonify({
        "access_token": access_token,
        "user": {
            "id": user_id_str, # Antes user.id_usuario
            "nombre": user.nombre,
            "email": user.email,
            "role": nombre_rol,
            "membership_plan": nombre_membresia,
            "access_level": access_level,
            "perfil_completo": perfil_completo,
            "peso_inicial": peso_inicial
        }
    }), 200

@auth_bp.route("/register", methods=["POST"])
@limiter.limit("3 per minute; 10 per hour")
def register():
    data = request.get_json()
    db = get_db()
    
    # Verificamos si el correo ya existe
    if User.find_by_email(data.get("email")):
        return jsonify({"msg": "El correo ya está registrado"}), 400

    try:
        # Buscamos el ObjectId del rol "Miembro"
        rol_miembro = Role.find_by_nombre("Miembro")
        
        # Si no existe el rol por nombre (y dependías de que fuera el ID 4), 
        # asegúrate de que exista en tu script de estáticos.
        if not rol_miembro:
            return jsonify({"msg": "Error interno: Rol 'Miembro' no encontrado en la base de datos"}), 500

        # 1. Crear Usuario
        nuevo_usuario = User(
            nombre=data.get("nombre"),
            email=data.get("email"),
            id_role=rol_miembro._id, 
            activo=True
        )
        nuevo_usuario.set_password(data.get("password"))
        
        # Al hacer save(), se inserta y se le asigna un _id a la instancia
        user_id = nuevo_usuario.save()

        # 2. Crear Miembro
        # Importamos datetime para la fecha de registro
        from datetime import datetime, timezone
        
        nuevo_miembro = Miembro(
            id_usuario=user_id,
            telefono=data.get("telefono"),
            sexo=data.get("sexo"),
            fecha_registro=datetime.now(timezone.utc),
            estado="Activo"
        )
        nuevo_miembro.save()

        # En MongoDB no hay "commit" por defecto (las escrituras son automáticas 
        # a menos que uses transacciones explícitas), así que con el save() basta.

        return jsonify({"msg": "Usuario registrado exitosamente"}), 201

    except Exception as e:
        # Nota: Sin transacciones explícitas en Mongo, si falla la creación del miembro,
        # el usuario ya habrá sido insertado. Para sistemas simples, esto puede bastar.
        # Si necesitas atomicidad estricta, MongoDB soporta sesiones y transacciones.
        return jsonify({"msg": str(e)}), 500