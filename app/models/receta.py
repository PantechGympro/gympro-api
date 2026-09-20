from bson.objectid import ObjectId
from app.mongo import get_db

class Receta:
    collection = "recetas"

    def __init__(self, id_tipo_dieta, nombre, tipo_comida, ingredientes, preparacion,
                 calorias=0.0, proteinas=0.0, carbohidratos=0.0, grasas=0.0, 
                 tiempo_preparacion=0, _id=None):
        self._id = _id
        self.id_tipo_dieta = ObjectId(id_tipo_dieta) if isinstance(id_tipo_dieta, str) else id_tipo_dieta
        self.nombre = nombre
        self.tipo_comida = tipo_comida
        self.ingredientes = ingredientes
        self.preparacion = preparacion
        self.calorias = float(calorias) if calorias else 0.0
        self.proteinas = float(proteinas) if proteinas else 0.0
        self.carbohidratos = float(carbohidratos) if carbohidratos else 0.0
        self.grasas = float(grasas) if grasas else 0.0
        self.tiempo_preparacion = tiempo_preparacion

    def to_dict(self):
        db = get_db()
        # Buscamos el nombre del tipo de dieta para el diccionario
        tipo_dieta_doc = db.tipos_dieta.find_one({"_id": self.id_tipo_dieta})
        nombre_tipo_dieta = tipo_dieta_doc["nombre"] if tipo_dieta_doc else "N/A"

        return {
            "id_tipo_dieta": self.id_tipo_dieta,
            "nombre_tipo_dieta": nombre_tipo_dieta,
            "nombre": self.nombre,
            "tipo_comida": self.tipo_comida,
            "ingredientes": self.ingredientes,
            "preparacion": self.preparacion,
            "calorias": self.calorias,
            "proteinas": self.proteinas,
            "carbohidratos": self.carbohidratos,
            "grasas": self.grasas,
            "tiempo_preparacion": self.tiempo_preparacion
        }

    def save(self):
        db = get_db()
        data = {
            "id_tipo_dieta": self.id_tipo_dieta,
            "nombre": self.nombre,
            "tipo_comida": self.tipo_comida,
            "ingredientes": self.ingredientes,
            "preparacion": self.preparacion,
            "calorias": self.calorias,
            "proteinas": self.proteinas,
            "carbohidratos": self.carbohidratos,
            "grasas": self.grasas,
            "tiempo_preparacion": self.tiempo_preparacion
        }
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, receta_id):
        try:
            oid = ObjectId(receta_id) if isinstance(receta_id, str) else receta_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None