from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class User:
    # Nombre de la colección en MongoDB
    collection = "usuarios"

    def __init__(self, id_role, nombre=None, email=None, password=None, activo=True, fecha_creacion=None, _id=None):
        """
        Inicializa un objeto de usuario en memoria.
        El parámetro _id es opcional, se genera automáticamente al guardar por primera vez.
        """
        self._id = _id
        
        # Las llaves foráneas en Mongo se manejan como ObjectId
        self.id_role = ObjectId(id_role) if isinstance(id_role, str) else id_role
        
        self.nombre = nombre
        self.email = email
        self.password = password
        self.activo = activo
        
        # Reemplazamos datetime.utcnow() (deprecado en Python 3.12+) por datetime.now(timezone.utc)
        self.fecha_creacion = fecha_creacion or datetime.now(timezone.utc)

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

    def to_dict(self):
        """Prepara los datos para ser insertados en MongoDB."""
        return {
            "id_role": self.id_role,
            "nombre": self.nombre,
            "email": self.email,
            "password": self.password,
            "activo": self.activo,
            "fecha_creacion": self.fecha_creacion
        }

    def save(self):
        """Inserta un nuevo usuario o actualiza uno existente."""
        db = get_db()
        if self._id:
            # Si ya tiene un _id, actualizamos el documento
            db[self.collection].update_one(
                {"_id": self._id},
                {"$set": self.to_dict()}
            )
        else:
            # Si no tiene _id, es un documento nuevo
            result = db[self.collection].insert_one(self.to_dict())
            self._id = result.inserted_id
        return self._id

    # ──────────────────────────────────────────────
    # MÉTODOS DE BÚSQUEDA (Class Methods)
    # ──────────────────────────────────────────────

    @classmethod
    def find_by_email(cls, email):
        """Busca un usuario por su email."""
        db = get_db()
        user_data = db[cls.collection].find_one({"email": email})
        if user_data:
            # Instanciamos la clase con los datos de Mongo usando unpacking (**)
            return cls(**user_data)
        return None

    @classmethod
    def find_by_id(cls, user_id):
        """Busca un usuario por su ID (convirtiéndolo a ObjectId primero)."""
        db = get_db()
        try:
            oid = ObjectId(user_id) if isinstance(user_id, str) else user_id
        except Exception:
            return None # Falla si el string no es un ObjectId válido de 24 caracteres
            
        user_data = db[cls.collection].find_one({"_id": oid})
        if user_data:
            return cls(**user_data)
        return None