import mysql.connector
from faker import Faker
import random
from datetime import datetime, timedelta

# ==========================================
# CONFIGURACIÓN
# ==========================================
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',      
    'password': '',      
    'database': 'gym_db'
}

# Configuración de cantidades
CANTIDAD_MIEMBROS = 5  
CANTIDAD_ENTRENADORES = 1
CANTIDAD_RECEPCIONISTAS = 1

# Contraseña por defecto
DEFAULT_PASSWORD = 'scrypt:32768:8:1$U1umhxfH8LDLojFb$7c6b302a3cdc30296572691480bfcad86209846fe6896a4f69c0f22caf58e23a85d8396fdce945a45fad84d8b27682c9e9f6919a4d7481bd912aa2d001470309'

fake = Faker('es_MX')

def connect_db():
    """Conecta a la base de datos"""
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as err:
        print(f"❌ Error al conectar con la base de datos: {err}")
        exit(1)

def limpiar_base_datos(cursor, conn):
    """Limpia todos los datos existentes"""
    print("🧹 Limpiando base de datos...")
    
    tablas = [
        'sesiones', 'rutina_ejercicios', 'rutina_dias', 'rutinas', 'correo_miembro',
        'detalle_venta', 'progreso_fisico', 'asistencias', 'pagos',
        'miembro_membresia', 'correos_enviados', 'ventas', 
        'miembros', 'usuarios', 'productos', 'membresias', 'roles'
    ]
    
    cursor.execute("SET FOREIGN_KEY_CHECKS=0")
    for tabla in tablas:
        try:
            cursor.execute(f"TRUNCATE TABLE {tabla}")
        except mysql.connector.Error as err:
            print(f"⚠️ Advertencia: No se pudo limpiar la tabla {tabla} ({err})")
            
    cursor.execute("SET FOREIGN_KEY_CHECKS=1")
    conn.commit()
    print("   ✅ Base de datos limpia")

def insertar_datos_estaticos(cursor, conn):
    """Inserta roles, membresías y productos"""
    print("📋 Insertando datos estáticos...")
    
    # Roles
    roles = [('Administrador',), ('Entrenador',), ('Recepcionista',), ('Miembro',)]
    cursor.executemany("INSERT INTO roles (nombre) VALUES (%s)", roles)
    
    # Membresías
    membresias = [
        ('Básica Mensual', 1, 80.00), ('Premium Mensual', 1, 90.00),
        ('Básica Anual', 12, 300.00), ('Premium Anual', 12, 550.00),
        ('Estudiante', 1, 25.00), ('Familiar', 1, 80.00), ('VIP', 1, 100.00)
    ]
    cursor.executemany("INSERT INTO membresias (nombre, duracion_meses, precio) VALUES (%s, %s, %s)", membresias)
    
    # Productos
    productos = [
        ('Proteína Whey 1kg', 450.00, 50), ('Creatina 300g', 280.00, 30),
        ('BCAA 200 caps', 320.00, 25), ('Pre-Workout', 380.00, 20),
        ('Shaker 600ml', 80.00, 100), ('Toalla deportiva', 120.00, 60),
        ('Guantes gimnasio', 150.00, 40), ('Botella agua 1L', 95.00, 80),
        ('Barra proteína', 35.00, 200), ('Electrolitos', 180.00, 45)
    ]
    cursor.executemany("INSERT INTO productos (nombre, precio, stock) VALUES (%s, %s, %s)", productos)
    
    conn.commit()
    print("   ✅ Datos estáticos insertados")

def obtener_ids_referencia(cursor):
    """Obtiene IDs necesarios y también el NOMBRE de la membresía"""
    cursor.execute("SELECT id_role FROM roles WHERE nombre = 'Administrador'")
    id_admin = cursor.fetchone()[0]
    
    cursor.execute("SELECT id_role FROM roles WHERE nombre = 'Entrenador'")
    id_entrenador = cursor.fetchone()[0]
    
    cursor.execute("SELECT id_role FROM roles WHERE nombre = 'Recepcionista'")
    id_recepcionista = cursor.fetchone()[0]
    
    cursor.execute("SELECT id_role FROM roles WHERE nombre = 'Miembro'")
    id_miembro = cursor.fetchone()[0]
    
    cursor.execute("SELECT id_membresia, precio, duracion_meses, nombre FROM membresias")
    membresias = cursor.fetchall()
    
    cursor.execute("SELECT id_producto, precio FROM productos")
    productos = cursor.fetchall()
    
    return {
        'roles': {'admin': id_admin, 'entrenador': id_entrenador, 'recepcionista': id_recepcionista, 'miembro': id_miembro},
        'membresias': membresias,
        'productos': productos
    }

def obtener_ids_entrenadores(cursor):
    """Obtiene lista de IDs de usuarios que son entrenadores"""
    cursor.execute("""
        SELECT u.id_usuario 
        FROM usuarios u
        JOIN roles r ON u.id_role = r.id_role
        WHERE r.nombre = 'Entrenador'
    """)
    return [row[0] for row in cursor.fetchall()]

def crear_administrador(cursor, conn, id_role_admin):
    print("👨‍💼 Creando administrador...")
    cursor.execute("""
        INSERT INTO usuarios (id_role, nombre, email, PASSWORD, activo, fecha_creacion) 
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (id_role_admin, 'Carlos Admin', 'admin@gym.com', DEFAULT_PASSWORD, 1, datetime.now() - timedelta(days=365)))
    conn.commit()

def crear_personal(cursor, conn, id_role_entrenador, id_role_recepcionista):
    print(f"👨‍🏫 Creando {CANTIDAD_ENTRENADORES} entrenadores y {CANTIDAD_RECEPCIONISTAS} recepcionista(s)...")
    personal_data = []
    
    # Entrenadores
    for i in range(CANTIDAD_ENTRENADORES):
        sexo = random.choice(['M', 'F'])
        nombre = fake.name_male() if sexo == 'M' else fake.name_female()
        personal_data.append((id_role_entrenador, nombre, f"entrenador{i+1}@gym.com", DEFAULT_PASSWORD, 1, fake.date_time_between(start_date='-2y', end_date='-6m')))
    
    # Recepcionistas
    for i in range(CANTIDAD_RECEPCIONISTAS):
        sexo = random.choice(['M', 'F'])
        nombre = fake.name_female() if sexo == 'F' else fake.name_male()
        personal_data.append((id_role_recepcionista, nombre, f"recepcion{i+1}@gym.com", DEFAULT_PASSWORD, 1, fake.date_time_between(start_date='-2y', end_date='-6m')))
    
    cursor.executemany("INSERT INTO usuarios (id_role, nombre, email, PASSWORD, activo, fecha_creacion) VALUES (%s, %s, %s, %s, %s, %s)", personal_data)
    conn.commit()
    print("   ✅ Personal creado")

def crear_miembros_y_datos(cursor, conn, id_role_miembro, membresias, productos, ids_entrenadores):
    """Crea miembros y asigna entrenador si es VIP/Premium"""
    print(f"👥 Creando {CANTIDAD_MIEMBROS} miembros con datos completos...")
    
    sql_user = "INSERT INTO usuarios (id_role, nombre, email, PASSWORD, activo, fecha_creacion) VALUES (%s, %s, %s, %s, %s, %s)"
    
    sql_miembro = """
        INSERT INTO miembros (id_usuario, id_entrenador, telefono, fecha_nacimiento, sexo, peso_inicial, 
                             estatura, fecha_registro, estado, foto_perfil) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    
    membresias_data, pagos_data, asistencias_data, progreso_data = [], [], [], []
    fecha_actual = datetime.now()
    
    for i in range(CANTIDAD_MIEMBROS):
        # 1. Crear Usuario
        sexo = random.choice(['M', 'F'])
        nombre = fake.name_male() if sexo == 'M' else fake.name_female()
        email = f"{fake.user_name()}{random.randint(1000,9999)}@{fake.free_email_domain()}"
        fecha_registro = fake.date_time_between(start_date='-2y', end_date='-1m')
        
        cursor.execute(sql_user, (id_role_miembro, nombre, email, DEFAULT_PASSWORD, 1, fecha_registro))
        id_usuario = cursor.lastrowid
        
        # 2. Elegir Membresía y Entrenador
        membresia_obj = random.choice(membresias)
        id_membresia, precio_membresia, duracion_meses, nombre_membresia = membresia_obj
        
        # LÓGICA DE ASIGNACIÓN
        id_entrenador = None
        if 'Premium' in nombre_membresia or 'VIP' in nombre_membresia:
            id_entrenador = random.choice(ids_entrenadores)
            
        # 3. Crear Miembro
        telefono = fake.phone_number()[:20]
        fecha_nacimiento = fake.date_of_birth(minimum_age=16, maximum_age=65)
        
        if sexo == 'M':
            peso_inicial, estatura = round(random.uniform(65.0, 110.0), 2), round(random.uniform(1.65, 1.95), 2)
            foto_perfil = 'male.jpg'
        else:
            peso_inicial, estatura = round(random.uniform(50.0, 85.0), 2), round(random.uniform(1.50, 1.75), 2)
            foto_perfil = 'female.jpg'

        cursor.execute(sql_miembro, (
            id_usuario, id_entrenador, telefono, fecha_nacimiento, sexo, 
            peso_inicial, estatura, fecha_registro.date(), 'Activo', foto_perfil
        ))
        id_miembro = cursor.lastrowid
        
        # 4. Registrar Membresía
        dias_desde_reg = (fecha_actual - fecha_registro).days
        inicio_memb = fecha_registro + timedelta(days=random.randint(0, min(dias_desde_reg, 45)))
        fin_memb = inicio_memb + timedelta(days=duracion_meses * 30)
        estado_memb = 'Activa' if fin_memb > fecha_actual else ('Vencida' if (fecha_actual - fin_memb).days <= 30 else 'Cancelada')
        
        membresias_data.append((id_miembro, id_membresia, inicio_memb.date(), fin_memb.date(), estado_memb))
        
        # 5. Registrar Pago
        pagos_data.append((id_miembro, precio_membresia, random.choice(['Efectivo', 'Tarjeta', 'Transferencia']), f"Pago {nombre_membresia}", inicio_memb))
        
        # 6. Generar Asistencias
        dias_activo = min((fecha_actual - inicio_memb).days, 90)
        if dias_activo > 0 and estado_memb == 'Activa':
            nivel = random.choices(['alto', 'medio', 'bajo'], weights=[0.3, 0.5, 0.2])[0]
            freq = random.randint(4, 6) if nivel == 'alto' else (random.randint(2, 4) if nivel == 'medio' else random.randint(1, 2))
            
            for dia in range(min(dias_activo, 60)):
                fecha_dia = fecha_actual.date() - timedelta(days=dia)
                if random.random() < (0.5 * (freq / 5)):
                    hora = random.randint(6, 21)
                    hora_ent = f"{hora:02d}:{random.choice([0,15,30,45]):02d}:00"
                    duracion = random.randint(45, 120)
                    dt_ent = datetime.combine(fecha_dia, datetime.strptime(hora_ent, "%H:%M:%S").time())
                    dt_sal = dt_ent + timedelta(minutes=duracion)
                    hora_sal = None if (fecha_dia == fecha_actual.date() and (fecha_actual - dt_ent).seconds < 7200) else dt_sal.strftime("%H:%M:%S")
                    asistencias_data.append((id_miembro, fecha_dia, hora_ent, hora_sal))
        
        # 7. Generar Progreso
        peso_act = peso_inicial
        for idx in range(random.randint(3, 5)):
            fecha_med = fecha_actual - timedelta(days=[60, 45, 30, 15, 7][idx])
            if idx > 0: peso_act += random.uniform(-1.5, 1.0)
            bmi = peso_act / ((estatura/100) ** 2)
            grasa = round(random.uniform(12, 25) if sexo == 'M' else random.uniform(18, 32), 2)
            musculo = round(random.uniform(35, 50) if sexo == 'M' else random.uniform(28, 40), 2)
            progreso_data.append((id_miembro, round(peso_act, 2), round(bmi, 2), grasa, musculo, 
                                  round(random.uniform(50, 65), 2), round(random.uniform(2.5, 4), 2),
                                  round(random.uniform(70, 100), 2), round(random.uniform(85, 115), 2),
                                  round(random.uniform(80, 110), 2), round(random.uniform(25, 40), 2),
                                  round(random.uniform(25, 40), 2), round(random.uniform(45, 65), 2),
                                  round(random.uniform(45, 65), 2), round(random.uniform(30, 42), 2), fecha_med.date()))

        if (i + 1) % 100 == 0: print(f"   📊 {i + 1}/{CANTIDAD_MIEMBROS} miembros procesados...")

    print("   💾 Insertando datos masivos...")
    cursor.executemany("INSERT INTO miembro_membresia (id_miembro, id_membresia, fecha_inicio, fecha_fin, estado) VALUES (%s, %s, %s, %s, %s)", membresias_data)
    cursor.executemany("INSERT INTO pagos (id_miembro, monto, metodo_pago, concepto, fecha_pago) VALUES (%s, %s, %s, %s, %s)", pagos_data)
    
    batch_size = 5000
    for i in range(0, len(asistencias_data), batch_size):
        cursor.executemany("INSERT INTO asistencias (id_miembro, fecha, hora_entrada, hora_salida) VALUES (%s, %s, %s, %s)", asistencias_data[i:i+batch_size])
        conn.commit()
        
    cursor.executemany("""INSERT INTO progreso_fisico (id_miembro, peso, bmi, grasa_corporal, masa_muscular, 
        agua_corporal, masa_osea, cintura, cadera, pecho, brazo_derecho, brazo_izquierdo,
        muslo_derecho, muslo_izquierdo, pantorrilla, fecha_registro) 
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", progreso_data)
    conn.commit()
    print("   ✅ Miembros insertados y vinculados a entrenadores (si aplica)")

def crear_usuarios_especificos(cursor, conn, id_role_miembro, membresias, ids_entrenadores):
    """Crea los dos usuarios específicos solicitados con Membresía VIP"""
    print("🧪 Creando usuarios de prueba (Obeso vs Fit) con VIP...")

    membresia_vip = next((m for m in membresias if 'VIP' in m[3]), None)
    
    if not membresia_vip:
        print("⚠️ No se encontró membresía VIP explícita, usando la última de la lista.")
        membresia_vip = membresias[-1]
    
    id_memb_vip = membresia_vip[0]
    duracion_vip = membresia_vip[2]

    # Datos de los dos perfiles (Ambos VIP)
    perfiles = [
        {
            'nombre': 'Juan Obeso VIP',
            'email': 'juan.obeso@gym.com',
            'peso': 140.0, 'estatura': 1.70,
            'tipo': 'Malo',
            'grasa': 45.0, 'musculo': 22.0, 'cintura': 135.0, 'pecho': 125.0
        },
        {
            'nombre': 'Pedro Fit VIP',
            'email': 'pedro.fit@gym.com',
            'peso': 82.0, 'estatura': 1.82,
            'tipo': 'Bueno',
            'grasa': 11.5, 'musculo': 48.0, 'cintura': 80.0, 'pecho': 110.0
        }
    ]

    fecha_hoy = datetime.now()

    for p in perfiles:
        cursor.execute("""
            INSERT INTO usuarios (id_role, nombre, email, PASSWORD, activo, fecha_creacion) 
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (id_role_miembro, p['nombre'], p['email'], DEFAULT_PASSWORD, 1, fecha_hoy))
        id_usuario = cursor.lastrowid

        id_entrenador = random.choice(ids_entrenadores)

        cursor.execute("""
            INSERT INTO miembros (id_usuario, id_entrenador, telefono, fecha_nacimiento, sexo, 
                                 peso_inicial, estatura, fecha_registro, estado, foto_perfil) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            id_usuario, id_entrenador, '555-TEST-VIP', 
            fake.date_of_birth(minimum_age=25, maximum_age=40), 
            'M', p['peso'], p['estatura'], fecha_hoy.date(), 'Activo', 'male.jpg'
        ))
        id_miembro = cursor.lastrowid

        fecha_fin = fecha_hoy + timedelta(days=duracion_vip * 30)
        cursor.execute("""
            INSERT INTO miembro_membresia (id_miembro, id_membresia, fecha_inicio, fecha_fin, estado) 
            VALUES (%s, %s, %s, %s, %s)
        """, (id_miembro, id_memb_vip, fecha_hoy.date(), fecha_fin.date(), 'Activa'))

        bmi = p['peso'] / (p['estatura'] ** 2)
        
        if p['tipo'] == 'Malo':
            brazo = 42.0
            pierna = 75.0
            cadera = 140.0
        else:
            brazo = 41.0
            pierna = 62.0
            cadera = 95.0

        cursor.execute("""
            INSERT INTO progreso_fisico (id_miembro, peso, bmi, grasa_corporal, masa_muscular, 
            agua_corporal, masa_osea, cintura, cadera, pecho, brazo_derecho, brazo_izquierdo,
            muslo_derecho, muslo_izquierdo, pantorrilla, fecha_registro) 
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            id_miembro, p['peso'], bmi, p['grasa'], p['musculo'], 
            (100 - p['grasa'] - p['musculo']),
            random.uniform(2.5, 3.5),
            p['cintura'], cadera, p['pecho'], 
            brazo, brazo, pierna, pierna, pierna * 0.6, 
            fecha_hoy.date()
        ))

    conn.commit()
    print("   ✅ Usuarios VIP de prueba creados: juan.obeso@gym.com y pedro.fit@gym.com")

def generar_sesiones_entrenamiento(cursor, conn, ids_entrenadores):
    """Genera sesiones de entrenamiento para los últimos 7 días y próximos 7 días"""
    print("📅 Generando sesiones de entrenamiento...")
    
    # Obtener miembros con entrenador asignado
    cursor.execute("""
        SELECT m.id_miembro, m.id_entrenador
        FROM miembros m
        WHERE m.id_entrenador IS NOT NULL AND m.estado = 'Activo'
    """)
    miembros_con_entrenador = cursor.fetchall()
    
    if not miembros_con_entrenador:
        print("   ⚠️ No hay miembros con entrenador asignado")
        return
    
    sesiones_data = []
    fecha_actual = datetime.now()
    
    # Tipos de sesión y ubicaciones
    tipos_sesion = ['Personal', 'Grupal', 'Consulta']
    ubicaciones = ['Sala 1', 'Sala 2', 'Sala Principal', 'Online', 'Box', 'Sala Cycling', 'Exterior']
    estados = ['scheduled', 'completed', 'in-progress', 'cancelled']
    
    # Generar sesiones para cada entrenador
    for id_entrenador in ids_entrenadores:
        # Filtrar miembros de este entrenador
        miembros_entrenador = [m[0] for m in miembros_con_entrenador if m[1] == id_entrenador]
        
        if not miembros_entrenador:
            continue
        
        # Generar sesiones para los últimos 7 días
        for dias_atras in range(7, 0, -1):
            fecha_sesion = (fecha_actual - timedelta(days=dias_atras)).date()
            
            # 2-4 sesiones por día
            num_sesiones = random.randint(2, 4)
            
            for _ in range(num_sesiones):
                tipo = random.choice(tipos_sesion)
                hora_inicio = f"{random.randint(7, 19):02d}:{random.choice([0, 30]):02d}:00"
                duracion = random.choice([45, 60, 90])
                ubicacion = random.choice(ubicaciones)
                
                # Sesiones pasadas están completadas o canceladas
                if dias_atras > 1:
                    estado = random.choices(estados[:2] + [estados[3]], weights=[0.85, 0.1, 0.05])[0]
                else:  # Ayer: algunas completadas, otras en progreso
                    estado = random.choices(estados[:3], weights=[0.7, 0.2, 0.1])[0]
                
                # Asignar miembro para sesiones personales
                id_miembro = random.choice(miembros_entrenador) if tipo == 'Personal' else None
                nombre_sesion = None if tipo == 'Personal' else f"{random.choice(['Grupo Funcional', 'Spinning Matutino', 'CrossFit', 'Yoga & Fuerza', 'HIIT Avanzado'])}"
                
                # Generar notas para sesiones completadas
                notas = None
                num_ejercicios = 0
                asistencia = 0
                
                if estado == 'completed':
                    notas = random.choice([
                        'Excelente progreso. Cliente muy motivado.',
                        'Trabajó fuerza de tren superior. Gran esfuerzo.',
                        'Sesión intensa. Mejoró técnica en sentadillas.',
                        'Cliente reporta menos fatiga. Buen avance.',
                        'Completó todos los ejercicios sin problemas.'
                    ])
                    num_ejercicios = random.randint(6, 12)
                    asistencia = 1
                elif estado == 'cancelled':
                    notas = random.choice([
                        'Cliente canceló por enfermedad',
                        'Emergencia personal',
                        'Cancelado por el entrenador'
                    ])
                
                sesiones_data.append((
                    id_entrenador, id_miembro, fecha_sesion, hora_inicio,
                    duracion, tipo, ubicacion, estado, nombre_sesion,
                    notas, num_ejercicios, asistencia
                ))
        
        # Generar sesiones para hoy
        fecha_hoy = fecha_actual.date()
        num_sesiones_hoy = random.randint(3, 6)
        
        for i in range(num_sesiones_hoy):
            tipo = random.choice(tipos_sesion)
            hora_inicio = f"{random.randint(8, 18):02d}:{random.choice([0, 30]):02d}:00"
            duracion = random.choice([45, 60, 90])
            ubicacion = random.choice(ubicaciones)
            
            # Sesiones de hoy: algunas completadas, algunas en progreso, resto programadas
            if i < num_sesiones_hoy // 3:
                estado = 'completed'
                notas = 'Sesión matutina completada exitosamente'
                num_ejercicios = random.randint(6, 10)
                asistencia = 1
            elif i < num_sesiones_hoy // 2:
                estado = 'in-progress'
                notas = None
                num_ejercicios = 0
                asistencia = 1
            else:
                estado = 'scheduled'
                notas = None
                num_ejercicios = 0
                asistencia = 0
            
            id_miembro = random.choice(miembros_entrenador) if tipo == 'Personal' else None
            nombre_sesion = None if tipo == 'Personal' else f"{random.choice(['Grupo Funcional', 'Spinning', 'HIIT', 'Yoga'])}"
            
            sesiones_data.append((
                id_entrenador, id_miembro, fecha_hoy, hora_inicio,
                duracion, tipo, ubicacion, estado, nombre_sesion,
                notas, num_ejercicios, asistencia
            ))
        
        # Generar sesiones futuras (próximos 7 días)
        for dias_adelante in range(1, 8):
            fecha_futura = (fecha_actual + timedelta(days=dias_adelante)).date()
            
            # 3-5 sesiones por día futuro
            num_sesiones = random.randint(3, 5)
            
            for _ in range(num_sesiones):
                tipo = random.choice(tipos_sesion)
                hora_inicio = f"{random.randint(7, 19):02d}:{random.choice([0, 30]):02d}:00"
                duracion = random.choice([45, 60, 90])
                ubicacion = random.choice(ubicaciones)
                estado = 'scheduled'
                
                id_miembro = random.choice(miembros_entrenador) if tipo == 'Personal' else None
                nombre_sesion = None if tipo == 'Personal' else f"{random.choice(['Grupo Funcional', 'Spinning', 'CrossFit', 'Yoga', 'HIIT'])}"
                
                sesiones_data.append((
                    id_entrenador, id_miembro, fecha_futura, hora_inicio,
                    duracion, tipo, ubicacion, estado, nombre_sesion,
                    None, 0, 0
                ))
    
    # Insertar sesiones
    cursor.executemany("""
        INSERT INTO sesiones (id_entrenador, id_miembro, fecha, hora_inicio, duracion_minutos,
                            tipo, ubicacion, estado, nombre_sesion, notas, num_ejercicios, asistencia)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, sesiones_data)
    conn.commit()
    
    print(f"   ✅ {len(sesiones_data)} sesiones generadas")

def generar_ventas(cursor, conn, productos):
    print("💰 Generando ventas...")
    ventas_data, detalles_data, updates = [], [], []
    
    for _ in range(200):
        ventas_data.append([(datetime.now() - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23))), 0])
    cursor.executemany("INSERT INTO ventas (fecha, total) VALUES (%s, %s)", ventas_data)
    conn.commit()
    
    cursor.execute("SELECT id_venta FROM ventas ORDER BY id_venta DESC LIMIT 200")
    ids = [r[0] for r in cursor.fetchall()]
    
    for id_v in ids:
        total = 0
        for _ in range(random.randint(1, 4)):
            prod = random.choice(productos)
            cant = random.randint(1, 3)
            sub = float(prod[1]) * cant
            total += sub
            detalles_data.append((id_v, prod[0], cant, sub))
        updates.append((total, id_v))
        
    cursor.executemany("INSERT INTO detalle_venta (id_venta, id_producto, cantidad, subtotal) VALUES (%s, %s, %s, %s)", detalles_data)
    cursor.executemany("UPDATE ventas SET total = %s WHERE id_venta = %s", updates)
    conn.commit()
    print("   ✅ Ventas generadas")

def mostrar_estadisticas(cursor):
    print("\n" + "="*60 + "\n📊 ESTADÍSTICAS FINALES\n" + "="*60)
    cursor.execute("SELECT COUNT(*) FROM usuarios")
    print(f"👥 Total usuarios: {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM miembros WHERE id_entrenador IS NOT NULL")
    print(f"🏋️ Miembros con Entrenador (VIP/Premium): {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM asistencias")
    print(f"📍 Total asistencias: {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM sesiones")
    print(f"📅 Total sesiones: {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM sesiones WHERE estado = 'completed'")
    print(f"✅ Sesiones completadas: {cursor.fetchone()[0]}")
    cursor.execute("SELECT COUNT(*) FROM sesiones WHERE fecha = CURDATE()")
    print(f"📆 Sesiones hoy: {cursor.fetchone()[0]}")
    print("="*60 + "\n")

def main():
    print("\n🚀 SCRIPT DE POBLACIÓN COMPLETA - GYM DATABASE\n")
    conn = connect_db()
    cursor = conn.cursor()
    try:
        limpiar_base_datos(cursor, conn)
        insertar_datos_estaticos(cursor, conn)
        refs = obtener_ids_referencia(cursor)
        
        crear_administrador(cursor, conn, refs['roles']['admin'])
        crear_personal(cursor, conn, refs['roles']['entrenador'], refs['roles']['recepcionista'])
        
        ids_entrenadores = obtener_ids_entrenadores(cursor)
        
        crear_miembros_y_datos(cursor, conn, refs['roles']['miembro'], refs['membresias'], refs['productos'], ids_entrenadores)
        crear_usuarios_especificos(cursor, conn, refs['roles']['miembro'], refs['membresias'], ids_entrenadores)
        
        # Generar sesiones de entrenamiento
        generar_sesiones_entrenamiento(cursor, conn, ids_entrenadores)
        
        generar_ventas(cursor, conn, refs['productos'])
        
        mostrar_estadisticas(cursor)
        print("✅ ¡POBLACIÓN EXITOSA!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback; traceback.print_exc()
        conn.rollback()
    finally:
        cursor.close(); conn.close()

if __name__ == "__main__":
    main()