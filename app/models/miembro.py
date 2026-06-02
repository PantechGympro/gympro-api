from bson.objectid import ObjectId
from app.mongo import get_db

class Miembro:
    collection = "miembros"

    def __init__(self, id_usuario=None, telefono=None, fecha_nacimiento=None, sexo=None,
                 peso_inicial=None, estatura=None, fecha_registro=None, estado="Activo",
                 foto_perfil=None, id_entrenador=None, objetivo=None, peso_objetivo=None,
                 grasa_objetivo=None, masa_muscular_objetivo=None, fecha_asignacion=None,
                 ultima_sesion=None, _id=None, **kwargs):
        # **kwargs absorbe campos Sprint 2: id_usuario_pg, id_gimnasio_pg, nombre, email, etc.
        self._id = _id
        self.id_usuario = ObjectId(id_usuario) if isinstance(id_usuario, str) else id_usuario
        # Sprint 2: referencia al usuario PG (int) y datos desnormalizados
        self.id_usuario_pg  = kwargs.get("id_usuario_pg")
        self.id_gimnasio_pg = kwargs.get("id_gimnasio_pg")
        self.nombre         = kwargs.get("nombre")
        self.email          = kwargs.get("email")
        self.telefono = telefono
        self.fecha_nacimiento = fecha_nacimiento
        self.sexo = sexo
        self.peso_inicial = float(peso_inicial) if peso_inicial else None
        self.estatura = float(estatura) if estatura else None
        self.fecha_registro = fecha_registro
        self.estado = estado
        self.foto_perfil = foto_perfil

        self.id_entrenador = ObjectId(id_entrenador) if isinstance(id_entrenador, str) and id_entrenador else id_entrenador
        self.objetivo = objetivo
        self.peso_objetivo = float(peso_objetivo) if peso_objetivo else None
        self.grasa_objetivo = float(grasa_objetivo) if grasa_objetivo else None
        self.masa_muscular_objetivo = float(masa_muscular_objetivo) if masa_muscular_objetivo else None
        self.fecha_asignacion = fecha_asignacion
        self.ultima_sesion = ultima_sesion

    def to_dict(self):
        db = get_db()
        # Solo retornar foto si es base64 válida; paths legacy del filesystem se ignoran
        fp = self.foto_perfil
        foto_url = fp if (fp and fp.startswith("data:image")) else None

        # --- Obtener datos del Usuario (multi-path) ---
        # Prioridad 1: campos desnormalizados del Sprint 2 (nombre/email en el doc)
        # Prioridad 2: lookup a colección Mongo usuarios (legacy pre-Sprint 2)
        # Prioridad 3: lookup a PostgreSQL por id_usuario_pg
        nombre_usuario = None
        email_usuario  = None

        if self.nombre:
            nombre_usuario = self.nombre
            email_usuario  = self.email or "Sin Email"
        elif self.id_usuario:
            usuario_doc = db.usuarios.find_one({"_id": self.id_usuario})
            if usuario_doc:
                nombre_usuario = usuario_doc.get("nombre")
                email_usuario  = usuario_doc.get("email")
        if not nombre_usuario and self.id_usuario_pg:
            try:
                from app.models.pg.usuario import Usuario as UsuarioPG
                u = UsuarioPG.query.get(int(self.id_usuario_pg))
                if u:
                    nombre_usuario = u.nombre
                    email_usuario  = u.email
            except Exception:
                pass

        nombre_usuario = nombre_usuario or "Sin Usuario"
        email_usuario  = email_usuario  or "Sin Email"

        # --- Obtener Membresía Activa ---
        membresia_activa_data = None
        mm_doc = db.miembro_membresia.find_one({"id_miembro": self._id, "estado": "Activa"})
        if mm_doc:
            id_mem = mm_doc.get("id_membresia")
            nombre_mem = "N/A"
            # PG path (integer id, migrado en Sprint 3 / US14)
            try:
                pg_id = int(id_mem)
                from app.models.pg.tipo_membresia import TipoMembresia
                tm = TipoMembresia.query.get(pg_id)
                if tm:
                    nombre_mem = tm.nombre
            except (TypeError, ValueError):
                # Legacy Mongo path
                from bson.objectid import ObjectId as OID
                try:
                    oid = OID(id_mem) if isinstance(id_mem, str) else id_mem
                    legacy = db.membresias.find_one({"_id": oid})
                    if legacy:
                        nombre_mem = legacy.get("nombre", "N/A")
                except Exception:
                    pass
            membresia_activa_data = {
                "nombre":      nombre_mem,
                "fecha_inicio": str(mm_doc.get("fecha_inicio", "")),
                "fecha_fin":    str(mm_doc.get("fecha_fin", "")),
                "estado":       mm_doc.get("estado", ""),
            }

        return {
            "id": str(self._id) if self._id else None,
            "nombre": nombre_usuario,
            "email": email_usuario,
            "telefono": self.telefono,
            "sexo": self.sexo,
            "peso_inicial": self.peso_inicial,
            "estatura": self.estatura,
            "activo": self.estado == "Activo",
            "foto_perfil": foto_url,
            "id_entrenador": str(self.id_entrenador) if self.id_entrenador else None,
            "objetivo": self.objetivo,
            "peso_objetivo": self.peso_objetivo,
            "grasa_objetivo": self.grasa_objetivo,
            "masa_muscular_objetivo": self.masa_muscular_objetivo,
            "fecha_asignacion": str(self.fecha_asignacion) if self.fecha_asignacion else None,
            "ultima_sesion": str(self.ultima_sesion) if self.ultima_sesion else None,
            "membresia": membresia_activa_data
        }

    def save(self):
        db = get_db()
        data = self.__dict__.copy()
        if "_id" in data and not data["_id"]:
            del data["_id"]

        if self._id:
            db[self.collection].update_one({"_id": self._id}, {"$set": data})
        else:
            result = db[self.collection].insert_one(data)
            self._id = result.inserted_id
        return self._id

    @classmethod
    def find_by_id(cls, miembro_id):
        try:
            oid = ObjectId(miembro_id) if isinstance(miembro_id, str) else miembro_id
            data = get_db()[cls.collection].find_one({"_id": oid})
            return cls(**data) if data else None
        except Exception:
            return None
    def to_client_card(self):
        """Convierte miembro a tarjeta de cliente para el dashboard del entrenador"""
        from datetime import date, timedelta
        from app.mongo import get_db
        db = get_db()
        
        # Calcular progreso — materializar el cursor una sola vez
        _tmp_progreso = list(db.progreso_fisico.find({"id_miembro": self._id}).sort("fecha_registro", -1).limit(1))
        progreso_reciente = _tmp_progreso[0] if _tmp_progreso else None
        
        progreso_porcentaje = 0
        if progreso_reciente and self.peso_inicial:
            diferencia = abs(self.peso_inicial - float(progreso_reciente.get("peso", 0)))
            progreso_porcentaje = min(int((diferencia / self.peso_inicial) * 100), 100)
            
        # Calcular racha (días consecutivos con asistencia)
        fecha_actual = date.today()
        racha = 0
        for i in range(30):
            fecha_check = (fecha_actual - timedelta(days=i)).strftime('%Y-%m-%d')
            asistencia = db.asistencias.find_one({
                "id_miembro": self._id,
                "fecha": fecha_check # Recuerda que guardamos fechas aisladas o como str
            })
            if asistencia:
                racha += 1
            elif i > 0:
                break
                
        # Total de sesiones
        sesiones_total = db.sesiones.count_documents({
            "id_miembro": self._id,
            "estado": "completed"
        })
        
        # Última sesión
        ultima_sesion = list(db.sesiones.find({"id_miembro": self._id}).sort("fecha", -1).limit(1))
        ultima_sesion = ultima_sesion[0] if ultima_sesion else None
        
        last_session = "Nunca"
        if ultima_sesion:
            # Lógica simplificada de fechas
            fecha_us = ultima_sesion.get("fecha")
            if isinstance(fecha_us, str):
                from datetime import datetime
                fecha_us = datetime.strptime(fecha_us, '%Y-%m-%d').date()
            elif hasattr(fecha_us, 'date'):
                 fecha_us = fecha_us.date()
                 
            if fecha_us == fecha_actual:
                last_session = "Hoy"
            elif fecha_us == fecha_actual - timedelta(days=1):
                last_session = "Ayer"
            elif ultima_sesion.get("estado") == "in-progress":
                last_session = "En curso"
            else:
                dias = (fecha_actual - fecha_us).days
                last_session = f"Hace {dias} días"

        # Calcular asistencia (últimos 30 días)
        hace_30_dias = fecha_actual - timedelta(days=30)
        # Adaptar consulta según cómo guardas la fecha en sesiones
        sesiones_programadas = db.sesiones.count_documents({
            "id_miembro": self._id,
            "estado": {"$in": ["completed", "cancelled"]}
            # Omitimos el filtro de fecha complejo por simplicidad en Mongo, 
            # asumiendo que el entrenador ve métricas generales.
        })
        
        sesiones_asistidas = db.sesiones.count_documents({
            "id_miembro": self._id,
            "estado": "completed"
        })
        
        attendance = int((sesiones_asistidas / sesiones_programadas * 100)) if sesiones_programadas > 0 else 0
        
        status = 'active' if attendance >= 75 else 'warning'
        trend = 'up' if attendance >= 80 else ('down' if attendance < 60 else 'stable')
        
        from datetime import date
        edad = 0
        if self.fecha_nacimiento:
             # Manejo seguro si es string o date
             fn = self.fecha_nacimiento
             if isinstance(fn, str):
                 from datetime import datetime
                 try: fn = datetime.strptime(fn, '%Y-%m-%d').date()
                 except: fn = None
             if fn: edad = (date.today() - fn).days // 365
             
        objetivo = "Pérdida de peso"
        if progreso_reciente:
            gc = float(progreso_reciente.get("grasa_corporal") or 0)
            mm = float(progreso_reciente.get("masa_muscular") or 0)
            if gc > 0 and gc < 15: objetivo = "Ganancia muscular"
            elif mm > 40: objetivo = "Definición"
            elif gc > 30: objetivo = "Pérdida de peso"
            else: objetivo = "Acondicionamiento"
            
        stats = {
            'weight': {'initial': float(self.peso_inicial or 70), 'current': float(self.peso_inicial or 70), 'goal': float(self.peso_inicial or 70) * 0.9},
            'muscle': {'initial': 30, 'current': 30, 'goal': 36},
            'fat': {'initial': 25, 'current': 25, 'goal': 18}
        }
        
        # Obtener nombre de usuario
        usuario_doc = db.usuarios.find_one({"_id": self.id_usuario})
        nombre_usuario = usuario_doc["nombre"] if usuario_doc else "Sin nombre"

        return {
            'id': str(self._id),
            'name': nombre_usuario,
            'age': edad,
            'goal': objetivo,
            'progress': progreso_porcentaje,
            'lastSession': last_session,
            'streak': racha,
            'sessionsTotal': sesiones_total,
            'attendance': attendance,
            'status': status,
            'trend': trend,
            'stats': stats
        }

    def to_dict_full(self, include_stats=False):
        """Reemplaza al to_dict() sobrecargado, evita recursión infinita"""
        base_dict = self.to_dict() # Llama al to_dict() original que creamos antes
        
        # Añadimos datos extra que pedía tu función original
        base_dict['id'] = str(self._id)
        base_dict['birthDate'] = str(self.fecha_nacimiento) if self.fecha_nacimiento else None
        base_dict['initialWeight'] = float(self.peso_inicial) if self.peso_inicial else None
        base_dict['height'] = float(self.estatura) if self.estatura else None
        base_dict['registrationDate'] = str(self.fecha_registro) if self.fecha_registro else None
        base_dict['profilePhoto'] = self.foto_perfil
        
        if include_stats:
            client_card = self.to_client_card()
            base_dict.update({
                'progress': client_card['progress'],
                'streak': client_card['streak'],
                'sessionsTotal': client_card['sessionsTotal'],
                'attendance': client_card['attendance'],
                'goal': client_card['goal'],
                'stats': client_card['stats']
            })
            
        return base_dict