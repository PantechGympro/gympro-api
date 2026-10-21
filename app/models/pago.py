from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class Pago:
    collection = "pagos"

    def __init__(self, id_miembro, monto, metodo_pago, concepto,
                 id_entrenador=None, fecha_pago=None, _id=None, **kwargs):
        # **kwargs absorbe campos extra del documento Mongo (id_gimnasio, estado, referencia, etc.)
        self._id = _id
        self.id_miembro    = ObjectId(id_miembro) if isinstance(id_miembro, str) else id_miembro
        self.id_entrenador = ObjectId(id_entrenador) if isinstance(id_entrenador, str) and id_entrenador else id_entrenador
        self.monto         = float(monto)
        self.metodo_pago   = metodo_pago
        self.concepto      = concepto
        self.fecha_pago    = fecha_pago or datetime.now(timezone.utc)
        # Campos opcionales del Sprint 2 (multi-tenant + seed)
        self.id_gimnasio   = kwargs.get("id_gimnasio")
        self.estado        = kwargs.get("estado", "Pagado")
        self.referencia    = kwargs.get("referencia")
        # Campos opcionales de cobro manual (owner_gym/pagos.py::registrar_pago) --
        # distinguen una renovacion de una inscripcion y primer pago, cuyo costo
        # de inscripcion lo captura el staff a mano en cada cobro (no es un precio
        # fijo del catalogo de membresias). costo_inscripcion queda en None para
        # cualquier pago que no sea una inscripcion (incluye todo el historico
        # previo a este campo).
        self.tipo_pago        = kwargs.get("tipo_pago", "renovacion")
        self.costo_inscripcion = kwargs.get("costo_inscripcion")

    def to_dict(self):
        db = get_db()
        nombre_mostrar = "Desconocido"

        # Buscar el nombre del miembro — soporta estructura legacy (id_usuario Mongo)
        # y estructura Sprint 2 (nombre directo en el doc del miembro)
        if self.id_miembro:
            miembro_doc = db.miembros.find_one({"_id": self.id_miembro})
            if miembro_doc:
                if "nombre" in miembro_doc:
                    # Sprint 2: nombre desnormalizado en el doc del miembro
                    nombre_mostrar = miembro_doc["nombre"]
                elif "id_usuario" in miembro_doc:
                    # Legacy: lookup a colección usuarios de Mongo
                    usuario_doc = db.usuarios.find_one({"_id": miembro_doc["id_usuario"]})
                    nombre_mostrar = usuario_doc.get("nombre", "Miembro sin usuario") if usuario_doc else "Miembro sin usuario"

        return {
            "id_miembro": str(self.id_miembro) if self.id_miembro else None,
            "id_entrenador": str(self.id_entrenador) if self.id_entrenador else None,
            "nombre_miembro": nombre_mostrar,
            "monto": self.monto,
            "metodo_pago": self.metodo_pago,
            "concepto": self.concepto,
            "fecha_pago": self.fecha_pago.isoformat() if isinstance(self.fecha_pago, datetime) else self.fecha_pago,
            "tipo_pago": self.tipo_pago,
            "costo_inscripcion": self.costo_inscripcion,
        }

    def save(self):
        db = get_db()
        # Guardamos en la BD los datos puros (sin el nombre calculado para no duplicar datos)
        data = {
            "id_miembro":    self.id_miembro,
            "id_entrenador": self.id_entrenador,
            "monto":         self.monto,
            "metodo_pago":   self.metodo_pago,
            "concepto":      self.concepto,
            "fecha_pago":    self.fecha_pago,
            "id_gimnasio":   self.id_gimnasio,   # tenant — requerido para aislamiento multi-gym
            "estado":        self.estado,
            "referencia":    self.referencia,
            "tipo_pago":         self.tipo_pago,
            "costo_inscripcion": self.costo_inscripcion,
        }
        
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, pago_id):
        try:
            oid = ObjectId(pago_id) if isinstance(pago_id, str) else pago_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None