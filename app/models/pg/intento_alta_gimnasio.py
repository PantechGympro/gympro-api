"""
models/pg/intento_alta_gimnasio.py — Bitácora de altas de gimnasio.

Guarda cada intento de registrar un gimnasio con las señales ya normalizadas
(ver utils/antiabuso.py). Tiene dos trabajos:

  1. Detectar: un alta nueva se compara contra esta tabla para saber si esa
     identidad ya abrió un gimnasio, o si desde el mismo equipo se abrieron
     varios en serie.
  2. Rendir cuentas: cada bloqueo queda registrado con su motivo, para que el
     superadmin pueda revisarlo y autorizar a mano lo que haga falta. Un
     bloqueo invisible es una venta perdida de la que nadie se entera.

Sobrevive a la purga. `id_organizacion` es un entero SIN clave foránea a
propósito: cuando un gimnasio se da de baja y se purga, su organización
desaparece, y es justo ese hueco el que delata el ciclo de "crear cuenta,
usarla, borrarla y volver a empezar". Con una FK en cascada ese rastro se
borraría solo, que es lo contrario de lo que esta tabla sirve.

Qué NO se guarda: ni el nombre, ni el correo tal cual, ni el teléfono tal
cual. Solo sus formas normalizadas, que sirven para comparar y no para
contactar a nadie. La huella es un hash sin datos personales dentro.
"""
from datetime import datetime, timezone

from app.extensions import db


class IntentoAltaGimnasio(db.Model):
    __tablename__ = "intentos_alta_gimnasio"

    id = db.Column(db.Integer, primary_key=True)

    # Señales normalizadas con las que se compara (ver utils/antiabuso.py).
    correo_normalizado   = db.Column(db.String(254), nullable=False, index=True)
    telefono_normalizado = db.Column(db.String(10), nullable=True, index=True)
    huella               = db.Column(db.String(64), nullable=True, index=True)
    ip                   = db.Column(db.String(45), nullable=True)   # cabe IPv6

    # 'creado'   -- el alta se hizo
    # 'bloqueado'-- se frenó; `motivo` dice cuál regla
    # 'excepcion'-- el superadmin autorizó esta identidad a mano
    resultado = db.Column(db.String(20), nullable=False, index=True)
    motivo    = db.Column(db.String(40), nullable=True)

    # Organización creada, si la hubo. Entero suelto, sin FK (ver docstring).
    id_organizacion = db.Column(db.Integer, nullable=True, index=True)

    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    def __repr__(self):
        return f"<IntentoAltaGimnasio {self.resultado} {self.correo_normalizado}>"

    def to_dict(self):
        return {
            "id":                   self.id,
            "correo_normalizado":   self.correo_normalizado,
            "telefono_normalizado": self.telefono_normalizado,
            "huella":               self.huella,
            "ip":                   self.ip,
            "resultado":            self.resultado,
            "motivo":               self.motivo,
            "id_organizacion":      self.id_organizacion,
            "created_at":           self.created_at.isoformat() if self.created_at else None,
        }
