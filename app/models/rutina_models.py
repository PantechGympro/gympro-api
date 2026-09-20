from datetime import datetime, timezone
from bson.objectid import ObjectId
from app.mongo import get_db

class Rutina:
    collection = "rutinas"

    def __init__(self, nombre, id_miembro=None, id_entrenador=None, objetivo=None,
                 categoria=None, dificultad=None, duracion_minutos=60, descripcion=None,
                 activa=True, fecha_creacion=None, fecha_actualizacion=None, _id=None):
        self._id = _id
        self.id_miembro = ObjectId(id_miembro) if isinstance(id_miembro, str) and id_miembro else id_miembro
        self.id_entrenador = ObjectId(id_entrenador) if isinstance(id_entrenador, str) and id_entrenador else id_entrenador
        self.nombre = nombre
        self.objetivo = objetivo
        self.categoria = categoria
        self.dificultad = dificultad
        self.duracion_minutos = duracion_minutos
        self.descripcion = descripcion
        self.activa = activa
        self.fecha_creacion = fecha_creacion or datetime.now(timezone.utc)
        self.fecha_actualizacion = fecha_actualizacion or datetime.now(timezone.utc)

    def to_dict(self):
        return {
            "id_miembro": self.id_miembro,
            "id_entrenador": self.id_entrenador,
            "nombre": self.nombre,
            "objetivo": self.objetivo,
            "categoria": self.categoria,
            "dificultad": self.dificultad,
            "duracion_minutos": self.duracion_minutos,
            "descripcion": self.descripcion,
            "activa": self.activa,
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

    def get_dias(self):
        """Obtiene los días de la rutina y sus ejercicios"""
        if not self._id: return []
        db = get_db()
        
        # Busca los días ordenados por el campo 'orden'
        dias_cursor = db.rutina_dias.find({"id_rutina": self._id}).sort("orden", 1)
        dias = list(dias_cursor)
        
        # Para cada día, busca sus ejercicios ordenados por 'orden'
        for dia in dias:
            ejercicios_cursor = db.rutina_ejercicios.find({"id_rutina_dia": dia["_id"]}).sort("orden", 1)
            dia["ejercicios"] = list(ejercicios_cursor)
            
        return dias

    @classmethod
    def find_by_id(cls, rutina_id):
        try:
            oid = ObjectId(rutina_id) if isinstance(rutina_id, str) else rutina_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None


class RutinaDia:
    collection = "rutina_dias"

    def __init__(self, id_rutina, dia_semana=None, grupo_muscular=None, orden=0, _id=None):
        self._id = _id
        self.id_rutina = ObjectId(id_rutina) if isinstance(id_rutina, str) else id_rutina
        self.dia_semana = dia_semana
        self.grupo_muscular = grupo_muscular
        self.orden = orden

    def to_dict(self):
        return {
            "id_rutina": self.id_rutina,
            "dia_semana": self.dia_semana,
            "grupo_muscular": self.grupo_muscular,
            "orden": self.orden
        }

    def save(self):
        db = get_db()
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": self.to_dict()})
        else:
            result = db[self.collection].insert_one(self.to_dict())
            self._id = result.inserted_id
        return self._id


class RutinaEjercicio:
    collection = "rutina_ejercicios"

    def __init__(self, id_rutina_dia, nombre_ejercicio, series="3", repeticiones="12", 
                 peso=None, notas=None, orden=0, _id=None):
        self._id = _id
        self.id_rutina_dia = ObjectId(id_rutina_dia) if isinstance(id_rutina_dia, str) else id_rutina_dia
        self.nombre_ejercicio = nombre_ejercicio
        self.series = series
        self.repeticiones = repeticiones
        self.peso = peso
        self.notas = notas
        self.orden = orden

    def to_dict(self):
        return {
            "id_rutina_dia": self.id_rutina_dia,
            "nombre_ejercicio": self.nombre_ejercicio,
            "series": self.series,
            "repeticiones": self.repeticiones,
            "peso": self.peso,
            "notas": self.notas,
            "orden": self.orden
        }

    def save(self):
        db = get_db()
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": self.to_dict()})
        else:
            result = db[self.collection].insert_one(self.to_dict())
            self._id = result.inserted_id
        return self._id


class MiembroRutina:
    collection = "miembro_rutina"

    def __init__(self, id_miembro, id_rutina, fecha_asignacion=None, activa=True, fecha_fin=None, _id=None):
        self._id = _id
        self.id_miembro = ObjectId(id_miembro) if isinstance(id_miembro, str) else id_miembro
        self.id_rutina = ObjectId(id_rutina) if isinstance(id_rutina, str) else id_rutina
        self.fecha_asignacion = fecha_asignacion or datetime.now(timezone.utc)
        self.activa = activa
        self.fecha_fin = fecha_fin

    def to_dict(self):
        return {
            "id_miembro": self.id_miembro,
            "id_rutina": self.id_rutina,
            "fecha_asignacion": self.fecha_asignacion,
            "activa": self.activa,
            "fecha_fin": self.fecha_fin
        }

    def save(self):
        db = get_db()
        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": self.to_dict()})
        else:
            result = db[self.collection].insert_one(self.to_dict())
            self._id = result.inserted_id
        return self._id