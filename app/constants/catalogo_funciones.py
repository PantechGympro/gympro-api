"""
constants/catalogo_funciones.py -- Qué incluye cada plan, rol por rol.

Fuente única para "ver todo lo que estás pagando": la web (superadmin, propietario,
landing) y la app móvil leen `funciones_por_rol` de `PlanSuscripcion.to_dict()` y
pintan ✓ / 🔒 sin repetir esta lista.

Reglas
------
* Una función con `bandera=None` es BASE: viene en todos los planes.
* Una función con `bandera="x"` se incluye si `plan.limites["x"]` es verdadero
  (mismas banderas que el API valida con @require_funcion_plan: lo que aquí se
  marca como bloqueable, el servidor realmente lo rechaza con 402).
* `bandera="multi_sucursal"` es especial: se incluye si el plan permite 2+ sucursales.
* Solo se listan funciones que existen hoy en la web o en la app móvil.
"""

SECCION_PROPIETARIO = "Propietario y administrador"
SECCION_RECEPCION = "Recepción"
SECCION_ENTRENADOR = "Entrenador"
SECCION_MIEMBRO = "Miembro"

# (nombre, descripción corta, bandera de plan o None)
CATALOGO = {
    SECCION_PROPIETARIO: [
        ("Tablero del gimnasio", "Miembros activos, ingresos, asistencias y vencimientos de un vistazo.", None),
        ("Alta y gestión de miembros", "Altas, edición, papelera, historial de asistencias y envío de credenciales.", None),
        ("Cobro de membresías y pagos", "Cobra en mostrador, registra pagos y consulta el historial.", None),
        ("Membresías por vencer y registro de entrada", "Avisos de renovación y check-in manual con aviso si la membresía venció.", None),
        ("Equipo de trabajo (staff)", "Crea cuentas de administrador, recepción y entrenadores con permisos por rol.", None),
        ("Finanzas y flujo", "Ingresos, egresos y tendencia del negocio.", None),
        ("Perfil y página pública del gimnasio", "Tu gimnasio visible para nuevos socios.", None),
        ("Punto de venta e inventario", "Vende productos, controla existencias y entregas pendientes.", "pos"),
        ("Cobros en línea", "Tus miembros pagan con tarjeta, PayPal o Mercado Pago.", "cobros_en_linea"),
        ("Reportes en PDF y automáticos", "Reportes listos para imprimir y envío programado por correo.", "reportes_pdf"),
        ("Analíticas con IA", "Riesgo de abandono, segmentación de clientes por valor y tendencias.", "analiticas_ia"),
        ("Respaldos a demanda", "Respalda y restaura la información de tu gimnasio cuando lo necesites.", "respaldos"),
        ("Laboratorio de Machine Learning", "Entrena y compara modelos con los datos de tu gimnasio.", "ml_lab"),
        ("Varias sucursales con un solo contrato", "Administra todas tus sedes y traslada miembros entre ellas.", "multi_sucursal"),
        ("Soporte prioritario con SLA", "Atención con tiempos de respuesta acordados y acompañamiento.", "soporte_prioritario"),
    ],
    SECCION_RECEPCION: [
        ("Registro de entradas", "Check-in por búsqueda o QR, con aviso empático si la membresía venció.", None),
        ("Miembros y membresías por vencer", "Consulta y contacta a quien está por renovar.", None),
        ("Cobrar membresía y pagos del día", "Cobro en mostrador y bitácora de movimientos.", None),
        ("Citas, entregas y tareas", "Agenda de citas, entregas pendientes y lista de pendientes del turno.", None),
        ("App móvil para recepción", "Check-ins y consulta de miembros desde el celular.", "app_movil"),
        ("Punto de venta", "Cobra productos desde el mostrador.", "pos"),
        ("Analíticas con IA", "Vista general, tipos de clientes y predicción para atender mejor.", "analiticas_ia"),
    ],
    SECCION_ENTRENADOR: [
        ("Mis clientes y solicitudes de entrenamiento personal", "Seguimiento individual y aceptación de solicitudes.", None),
        ("Mensajes con tus clientes", "Chat directo con cada miembro asignado.", None),
        ("Agenda y sesiones", "Planea tus sesiones y horarios.", None),
        ("Rutinas y dietas", "Crea rutinas, dietas y recetas y asígnalas a tus clientes.", None),
        ("App móvil para entrenadores", "Clientes, mensajes, agenda y rutinas desde el celular.", "app_movil"),
        ("Reportes en PDF de tus clientes", "Resúmenes de avance listos para compartir.", "reportes_pdf"),
        ("Importar rutinas y dietas con IA", "Sube un PDF o Excel y la IA lo convierte en rutina o dieta.", "rutinas_ia"),
        ("IA Coach: segmentación y predicciones", "Detecta quién necesita atención y proyecta resultados.", "analiticas_ia"),
    ],
    SECCION_MIEMBRO: [
        ("Mi panel y mi membresía", "Estado, vencimiento, historial de pagos y renovación.", None),
        ("Rutinas y registro de entrenos", "Tus rutinas del gimnasio y tu bitácora de ejercicio.", None),
        ("Entrenamiento personal y chat con tu entrenador", "Solicita un entrenador y escríbele.", None),
        ("Progreso físico y salud", "Medidas, peso, IMC y evolución.", None),
        ("Nutrición y recetas", "Tu plan de comidas y recetas.", None),
        ("Logros y notificaciones", "Metas cumplidas y avisos del gimnasio.", None),
        ("App móvil con check-in por QR", "Entra al gimnasio y entrena desde tu celular.", "app_movil"),
        ("Tienda del gimnasio", "Compra productos desde tu cuenta.", "pos"),
        ("Pago de membresía en línea", "Renueva con tarjeta, PayPal o Mercado Pago.", "cobros_en_linea"),
        ("Predicción de peso con IA", "Proyecta tu progreso con tu propio historial.", "analiticas_ia"),
    ],
}


def _incluye(plan, bandera):
    if bandera is None:
        return True
    if bandera == "multi_sucursal":
        try:
            return int((plan.limites or {}).get("max_sucursales") or 1) >= 2
        except (TypeError, ValueError):
            return False
    return bool(plan.permite(bandera))


def funciones_por_rol(plan):
    """
    {rol: [{nombre, descripcion, bandera, incluido}]} para un PlanSuscripcion.
    `bandera` viaja para que el front calcule "disponible desde el plan X"
    comparando contra los demás planes, sin duplicar aquí los nombres de plan.
    """
    return {
        rol: [
            {"nombre": n, "descripcion": d, "bandera": b, "incluido": _incluye(plan, b)}
            for (n, d, b) in items
        ]
        for rol, items in CATALOGO.items()
    }
