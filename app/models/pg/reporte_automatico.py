"""
models/pg/reporte_automatico.py -- Configuracion de reportes automaticos.

Peticion explicita del usuario: poder elegir que tipo de reporte generar,
con que filtros y cada cuanto (dia/semana/mes/año), para que se envie por
correo al propietario y tener mejor control del corte de caja sin tener
que entrar a Reportes y descargar el PDF a mano cada vez.

1 fila = 1 "receta" de reporte automatico para un gimnasio. El job
programado (routes/owner_gym/reportes_automaticos.py::tick_reportes_automaticos)
corre una vez al dia y decide, por fila, si hoy toca generarla segun su
frecuencia y `ultima_ejecucion` -- ese campo es lo que evita reenviar dos
veces el mismo periodo si el job se dispara más de una vez el mismo día.

Destinatarios (migracion 035): se eligen por receta -- el Propietario de la
organizacion (usuario_propietario_de(), utils/alcance.py), los Administradores
activos de la sucursal (administradores_de()), y/o hasta MAX_CORREOS_EXTRA
correos libres. Antes de la 035 era siempre el Propietario; las filas viejas
conservan ese comportamiento (enviar_propietario=true por omision).
"""
from datetime import datetime, timezone

from app.extensions import db

# Mismo criterio que SECCIONES_VALIDAS en reportes_negocio.py -- se
# importa desde ahi en las rutas para no mantener dos copias de la lista.
FRECUENCIAS_VALIDAS = ["diaria", "semanal", "mensual", "anual"]

FRECUENCIA_LABEL = {
    "diaria":  "Diaria",
    "semanal": "Semanal",
    "mensual": "Mensual",
    "anual":   "Anual",
}

# momento_envio: en que punto del periodo se manda el correo.
#   "dia_siguiente" (por omision, comportamiento original) -- el dia DESPUES
#     de que el periodo cerro, con el corte ya completo (ayer/semana pasada/
#     mes pasado/año pasado).
#   "mismo_dia" -- el ULTIMO dia del periodo que se esta cerrando, con los
#     datos acumulados hasta ese momento (hoy/esta semana/este mes/este año).
MOMENTOS_VALIDOS = ["mismo_dia", "dia_siguiente"]

MOMENTO_LABEL = {
    "mismo_dia":     "Mismo dia",
    "dia_siguiente": "Dia siguiente",
}

# Tope de correos libres por receta: suficiente para contador/socios y
# evita usar el reporte como lista de difusion. Mismo valor en el frontend
# (components/owner_gym/SelectorDestinatarios.jsx).
MAX_CORREOS_EXTRA = 5


class ConfiguracionReporteAutomatico(db.Model):
    """Receta de un reporte ejecutivo que se genera y envia solo, por correo."""
    __tablename__ = "configuracion_reporte_automatico"

    id               = db.Column(db.Integer, primary_key=True)
    id_gimnasio      = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
                                 nullable=False, index=True)
    # Solo auditoria -- ver nota del modulo sobre el destinatario del correo.
    id_usuario_creador = db.Column(db.Integer, db.ForeignKey("usuarios.id", ondelete="SET NULL"),
                                   nullable=True)

    nombre      = db.Column(db.String(120), nullable=False)
    frecuencia  = db.Column(db.String(10), nullable=False)   # diaria | semanal | mensual | anual

    # CSV de ids de SECCIONES_VALIDAS -- mismo formato que ya manda el
    # frontend al generar un reporte manual (secciones.join(",")).
    secciones   = db.Column(db.String(200), nullable=False,
                            default="resumen,ingresos,membresias,pos,asistencias,miembros")
    comparar    = db.Column(db.Boolean, nullable=False, default=True, server_default="true")
    graficas    = db.Column(db.Boolean, nullable=False, default=True, server_default="true")
    activo      = db.Column(db.Boolean, nullable=False, default=True, server_default="true")

    # Cuando se manda dentro del dia que le toca -- ver nota de MOMENTOS_VALIDOS.
    momento_envio = db.Column(db.String(20), nullable=False,
                              default="dia_siguiente", server_default="dia_siguiente")
    hora_envio    = db.Column(db.Integer, nullable=False, default=6, server_default="6")

    # A quien le llega -- ver nota del modulo. Al menos uno es obligatorio
    # (CHECK ck_reporte_auto_destinatario + validacion en la API).
    enviar_propietario   = db.Column(db.Boolean, nullable=False, default=True, server_default="true")
    enviar_administrador = db.Column(db.Boolean, nullable=False, default=False, server_default="false")
    # CSV en minusculas; None si no hay.
    correos_extra        = db.Column(db.String(500), nullable=True)

    ultima_ejecucion = db.Column(db.DateTime(timezone=True), nullable=True)
    ultimo_error     = db.Column(db.String(300), nullable=True)

    creado_en      = db.Column(db.DateTime(timezone=True), nullable=False,
                               default=lambda: datetime.now(timezone.utc))
    actualizado_en = db.Column(db.DateTime(timezone=True), nullable=False,
                               default=lambda: datetime.now(timezone.utc),
                               onupdate=lambda: datetime.now(timezone.utc))

    def lista_secciones(self) -> list[str]:
        return [s.strip() for s in (self.secciones or "").split(",") if s.strip()]

    def lista_correos_extra(self) -> list[str]:
        return [c.strip() for c in (self.correos_extra or "").split(",") if c.strip()]

    def destinatarios_label(self) -> str:
        """Texto para la tarjeta: 'Propietario, Administrador, a@b.com'."""
        partes = []
        if self.enviar_propietario:
            partes.append("Propietario")
        if self.enviar_administrador:
            partes.append("Administrador")
        partes.extend(self.lista_correos_extra())
        return ", ".join(partes)

    def to_dict(self):
        return {
            "id":               self.id,
            "id_gimnasio":      self.id_gimnasio,
            "nombre":           self.nombre,
            "frecuencia":       self.frecuencia,
            "frecuencia_label": FRECUENCIA_LABEL.get(self.frecuencia, self.frecuencia),
            "secciones":        self.lista_secciones(),
            "comparar":         self.comparar,
            "graficas":         self.graficas,
            "activo":           self.activo,
            "momento_envio":       self.momento_envio,
            "momento_envio_label": MOMENTO_LABEL.get(self.momento_envio, self.momento_envio),
            "hora_envio":          self.hora_envio,
            "enviar_propietario":   self.enviar_propietario,
            "enviar_administrador": self.enviar_administrador,
            "correos_extra":        self.lista_correos_extra(),
            "destinatarios_label":  self.destinatarios_label(),
            "ultima_ejecucion": self.ultima_ejecucion.isoformat() if self.ultima_ejecucion else None,
            "ultimo_error":     self.ultimo_error,
            "creado_en":        self.creado_en.isoformat() if self.creado_en else None,
        }

    def __repr__(self):
        return f"<ConfiguracionReporteAutomatico {self.id} gym={self.id_gimnasio} {self.frecuencia}>"
