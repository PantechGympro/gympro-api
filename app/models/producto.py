from bson.objectid import ObjectId
from app.mongo import get_db

class Producto:
    collection = "productos"

    def __init__(self, nombre, precio=0.0, stock=0, _id=None):
        self._id = _id
        self.nombre = nombre
        self.precio = float(precio)
        self.stock = int(stock)

    def to_dict(self):
        return {
            "nombre": self.nombre,
            "precio": self.precio,
            "stock": self.stock
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
    def find_by_id(cls, producto_id):
        try:
            oid = ObjectId(producto_id) if isinstance(producto_id, str) else producto_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None