from bson.objectid import ObjectId
from app.mongo import get_db

class DetalleVenta:
    collection = "detalle_venta"

    def __init__(self, id_venta, id_producto, cantidad, subtotal, _id=None):
        self._id = _id
        self.id_venta = ObjectId(id_venta) if isinstance(id_venta, str) else id_venta
        self.id_producto = ObjectId(id_producto) if isinstance(id_producto, str) else id_producto
        self.cantidad = int(cantidad) if cantidad else 0
        self.subtotal = float(subtotal) if subtotal else 0.0

    def to_dict(self):
        db = get_db()
        # Buscar el nombre del producto
        producto_doc = db.productos.find_one({"_id": self.id_producto})
        nombre_producto = producto_doc["nombre"] if producto_doc else "N/A"

        return {
            "id_detalle": self._id,
            "id_venta": self.id_venta,
            "id_producto": self.id_producto,
            "nombre_producto": nombre_producto,
            "cantidad": self.cantidad,
            "subtotal": self.subtotal
        }

    def save(self):
        db = get_db()
        data = {
            "id_venta": self.id_venta,
            "id_producto": self.id_producto,
            "cantidad": self.cantidad,
            "subtotal": self.subtotal
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, detalle_id):
        try:
            oid = ObjectId(detalle_id) if isinstance(detalle_id, str) else detalle_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None