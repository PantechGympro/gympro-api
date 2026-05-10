from bson.objectid import ObjectId
from app.mongo import get_db

class MiembroMembresia:
    collection = "miembro_membresia"

    def __init__(self, id_miembro, id_membresia, fecha_inicio=None, fecha_fin=None, estado=None, _id=None):
        self._id = _id
        self.id_miembro = ObjectId(id_miembro) if isinstance(id_miembro, str) else id_miembro
        self.id_membresia = ObjectId(id_membresia) if isinstance(id_membresia, str) else id_membresia
        self.fecha_inicio = fecha_inicio
        self.fecha_fin = fecha_fin
        self.estado = estado

    def to_dict(self):
        db = get_db()
        # Buscar el nombre de la membresía
        membresia_doc = db.membresias.find_one({"_id": self.id_membresia})
        nombre_membresia = membresia_doc["nombre"] if membresia_doc else "N/A"

        return {
            "id": self._id,
            "id_miembro": self.id_miembro,
            "id_membresia": self.id_membresia,
            "nombre_membresia": nombre_membresia,
            "fecha_inicio": str(self.fecha_inicio) if self.fecha_inicio else None,
            "fecha_fin": str(self.fecha_fin) if self.fecha_fin else None,
            "estado": self.estado
        }

    def save(self):
        db = get_db()
        data = {
            "id_miembro": self.id_miembro,
            "id_membresia": self.id_membresia,
            "fecha_inicio": self.fecha_inicio,
            "fecha_fin": self.fecha_fin,
            "estado": self.estado
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, mm_id):
        try:
            oid = ObjectId(mm_id) if isinstance(mm_id, str) else mm_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None