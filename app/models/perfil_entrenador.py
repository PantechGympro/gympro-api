from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class PerfilEntrenador:
    collection = "perfil_entrenador"

    def __init__(self, id_entrenador, telefono=None, direccion=None, especializacion=None, 
                 biografia=None, redes_sociales=None, fecha_creacion=None, fecha_actualizacion=None, _id=None):
        self._id = _id
        self.id_entrenador = ObjectId(id_entrenador) if isinstance(id_entrenador, str) else id_entrenador
        self.telefono = telefono
        self.direccion = direccion
        self.especializacion = especializacion
        self.biografia = biografia
        # En MongoDB, guardamos el JSON directamente como un diccionario de Python
        self.redes_sociales = redes_sociales or {}
        
        self.fecha_creacion = fecha_creacion or datetime.now(timezone.utc)
        self.fecha_actualizacion = fecha_actualizacion or datetime.now(timezone.utc)

    def to_dict(self):
        return {
            "id_entrenador": self.id_entrenador,
            "telefono": self.telefono,
            "direccion": self.direccion,
            "especializacion": self.especializacion,
            "biografia": self.biografia,
            "redes_sociales": self.redes_sociales,
            "fecha_creacion": self.fecha_creacion,
            "fecha_actualizacion": datetime.now(timezone.utc)
        }

    def save(self):
        db = get_db()
        data = self.to_dict()
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_entrenador_id(cls, entrenador_id):
        try:
            oid = ObjectId(entrenador_id) if isinstance(entrenador_id, str) else entrenador_id
            data = get_db()[cls.collection].find_one({"id_entrenador": oid})
            return cls(**data) if data else None
        except Exception:
            return None