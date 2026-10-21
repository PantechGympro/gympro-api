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

El destinatario del correo NUNCA se guarda aqui: siempre es el Propietario
de la organizacion del gimnasio (usuario_propietario_de(), en utils/alcance.py),
sin importar si quien configuro la automatizacion fue el Propietario o un
Administrador -- asi lo pidio el usuario.
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

    ultima_ejecucion = db.Column(db.DateTime(timezone=True), nullable=True)
    ultimo_error     = db.Column(db.String(300), nullable=True)

    creado_en      = db.Column(db.DateTime(timezone=True), nullable=False,
                               default=lambda: datetime.now(timezone.utc))
    actualizado_en = db.Column(db.DateTime(timezone=True), nullable=False,
                               default=lambda: datetime.now(timezone.utc),
                               onupdate=lambda: datetime.now(timezone.utc))

    def lista_secciones(self) -> list[str]:
        return [s.strip() for s in (self.secciones or "").split(",") if s.strip()]

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
            "ultima_ejecucion": self.ultima_ejecucion.isoformat() if self.ultima_ejecucion else None,
            "ultimo_error":     self.ultimo_error,
            "creado_en":        self.creado_en.isoformat() if self.creado_en else None,
        }

    def __repr__(self):
        return f"<ConfiguracionReporteAutomatico {self.id} gym={self.id_gimnasio} {self.frecuencia}>"
