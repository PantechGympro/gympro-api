from bson.objectid import ObjectId
from app.mongo import get_db

class TipoDieta:
    collection = "tipos_dieta"

    def __init__(self, nombre, descripcion=None, calorias_objetivo=None, _id=None):
        self._id = _id
        self.nombre = nombre
        self.descripcion = descripcion
        self.calorias_objetivo = calorias_objetivo

    def to_dict(self):
        return {
            "nombre": self.nombre,
            "descripcion": self.descripcion,
            "calorias_objetivo": self.calorias_objetivo
        }

    def save(self):
        db = get_db()
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": self.to_dict()})
        else:
            result = db[self.collection].insert_one(self.to_dict())
            self._id = result.inserted_id
        return self._id

    def get_recetas(self):
        """Obtiene las recetas asociadas a este tipo de dieta"""
        if not self._id: return []
        return list(get_db().recetas.find({"id_tipo_dieta": self._id}))

    @classmethod
    def find_by_nombre(cls, nombre):
        db = get_db()
        data = db[cls.collection].find_one({"nombre": nombre})
        return cls(**data) if data else None