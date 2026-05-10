from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class ProgresoFisico:
    collection = "progreso_fisico"

    def __init__(self, id_miembro, peso=None, bmi=None, grasa_corporal=None, masa_muscular=None,
                 agua_corporal=None, masa_osea=None, cintura=None, cadera=None, pecho=None,
                 brazo_derecho=None, brazo_izquierdo=None, muslo_derecho=None, muslo_izquierdo=None,
                 pantorrilla=None, notas=None, fecha_registro=None, _id=None):
        
        self._id = _id
        self.id_miembro = ObjectId(id_miembro) if isinstance(id_miembro, str) else id_miembro
        
        self.peso = float(peso) if peso else None
        self.bmi = float(bmi) if bmi else None
        self.grasa_corporal = float(grasa_corporal) if grasa_corporal else None
        self.masa_muscular = float(masa_muscular) if masa_muscular else None
        self.agua_corporal = float(agua_corporal) if agua_corporal else None
        self.masa_osea = float(masa_osea) if masa_osea else None
        
        self.cintura = float(cintura) if cintura else None
        self.cadera = float(cadera) if cadera else None
        self.pecho = float(pecho) if pecho else None
        
        self.brazo_derecho = float(brazo_derecho) if brazo_derecho else None
        self.brazo_izquierdo = float(brazo_izquierdo) if brazo_izquierdo else None
        self.muslo_derecho = float(muslo_derecho) if muslo_derecho else None
        self.muslo_izquierdo = float(muslo_izquierdo) if muslo_izquierdo else None
        self.pantorrilla = float(pantorrilla) if pantorrilla else None
        
        self.notas = notas
        self.fecha_registro = fecha_registro or datetime.now(timezone.utc)

    def to_dict(self):
        return {
            "id_miembro": self.id_miembro,
            "peso": self.peso or 0.0,
            "bmi": self.bmi or 0.0,
            "grasa_corporal": self.grasa_corporal,
            "masa_muscular": self.masa_muscular,
            "agua_corporal": self.agua_corporal,
            "masa_osea": self.masa_osea,
            "cintura": self.cintura or 0.0,
            "cadera": self.cadera or 0.0,
            "pecho": self.pecho,
            "brazo_derecho": self.brazo_derecho,
            "brazo_izquierdo": self.brazo_izquierdo,
            "muslo_derecho": self.muslo_derecho,
            "muslo_izquierdo": self.muslo_izquierdo,
            "pantorrilla": self.pantorrilla,
            "notas": self.notas,
            "fecha_registro": self.fecha_registro.strftime('%Y-%m-%d') if isinstance(self.fecha_registro, datetime) else self.fecha_registro
        }

    def save(self):
        db = get_db()
        # Guardamos todos los atributos reales, no solo los del to_dict (que está formateado para la API)
        data = self.__dict__.copy()
        if "_id" in data and not data["_id"]:
            del data["_id"]
            
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    def calcular_bmi(self, estatura_metros):
        if self.peso and estatura_metros and estatura_metros > 0:
            self.bmi = round(self.peso / (estatura_metros ** 2), 2)
            return self.bmi
        return None

    def calcular_relacion_cintura_cadera(self):
        if self.cintura and self.cadera and self.cadera > 0:
            return round(self.cintura / self.cadera, 2)
        return None

    def es_imc_saludable(self):
        if self.bmi:
            return 18.5 <= self.bmi <= 24.9
        return None

    def categoria_imc(self):
        if not self.bmi:
            return "Desconocido"
        
        if self.bmi < 18.5:
            return "Bajo peso"
        elif 18.5 <= self.bmi < 25:
            return "Peso normal"
        elif 25 <= self.bmi < 30:
            return "Sobrepeso"
        else:
            return "Obesidad"

    @classmethod
    def find_by_id(cls, progreso_id):
        try:
            oid = ObjectId(progreso_id) if isinstance(progreso_id, str) else progreso_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None