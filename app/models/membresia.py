from bson.objectid import ObjectId
from app.mongo import get_db

class Membresia:
    collection = "membresias"

    def __init__(self, nombre, duracion_meses, precio, _id=None):
        self._id = _id
        self.nombre = nombre
        self.duracion_meses = int(duracion_meses) if duracion_meses else 0
        self.precio = float(precio) if precio else 0.0

    def to_dict(self):
        return {
            "id_membresia": self._id,
            "nombre": self.nombre,
            "duracion_meses": self.duracion_meses,
            "precio": self.precio
        }

    def save(self):
        db = get_db()
        data = {
            "nombre": self.nombre,
            "duracion_meses": self.duracion_meses,
            "precio": self.precio
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, membresia_id):
        try:
            oid = ObjectId(membresia_id) if isinstance(membresia_id, str) else membresia_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None