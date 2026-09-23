from bson.objectid import ObjectId
from app.mongo import get_db

class Role:
    collection = "roles"

    def __init__(self, nombre, _id=None):
        self._id = _id
        self.nombre = nombre

    def to_dict(self):
        return {
            "nombre": self.nombre
        }

    def save(self):
        db = get_db()
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": self.to_dict()})
        else:
            result = db[self.collection].insert_one(self.to_dict())
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, role_id):
        try:
            oid = ObjectId(role_id) if isinstance(role_id, str) else role_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None

    @classmethod
    def find_by_nombre(cls, nombre):
        data = get_db()[cls.collection].find_one({"nombre": nombre})
        return cls(**data) if data else None