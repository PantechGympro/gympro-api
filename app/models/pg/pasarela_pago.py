"""
models/pg/pasarela_pago.py — Pasarelas de pago por gimnasio y transacciones.

Modelo de cobro adoptado: CREDENCIALES PROPIAS POR GIMNASIO.
Cada gimnasio registra sus propias credenciales de PayPal y/o Mercado Pago, de
modo que el dinero de membresías y productos cae DIRECTAMENTE en su cuenta;
la plataforma nunca custodia esos fondos. Para la suscripción SaaS (el gimnasio
paga su plan a GymPro) se usan las credenciales de la plataforma, tomadas de
variables de entorno.

Las credenciales se guardan CIFRADAS (Fernet, ver utils/crypto.py). Nunca se
devuelven al frontend: los endpoints exponen solo un enmascarado del último
tramo para que el usuario confirme cuál cargó.

Tablas:
  configuracion_pasarela  1 fila por (gimnasio, proveedor)
  transacciones_pago      1 fila por intento de cobro, cualquier contexto
"""
import enum
from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from app.extensions import db


class ProveedorPagoEnum(enum.Enum):
    paypal      = "paypal"
    mercadopago = "mercadopago"


class ModoPasarelaEnum(enum.Enum):
    sandbox = "sandbox"   # credenciales de prueba, no mueve dinero real
    live    = "live"      # credenciales productivas


class ContextoPagoEnum(enum.Enum):
    membresia   = "membresia"     # miembro paga su membresía al gimnasio
    producto    = "producto"      # venta de productos (POS) del gimnasio
    suscripcion = "suscripcion"   # gimnasio paga su plan SaaS a la plataforma


class EstadoTransaccionEnum(enum.Enum):
    pendiente  = "pendiente"    # checkout creado, esperando al pagador
    aprobado   = "aprobado"     # pago confirmado por la pasarela
    rechazado  = "rechazado"    # la pasarela rechazó el cobro
    cancelado  = "cancelado"    # el pagador abandonó el checkout
    reembolsado = "reembolsado"


# create_type=False: los tipos los crea Alembic en la migración 011.
_proveedor_enum = PGEnum("paypal", "mercadopago", name="proveedor_pago_enum", create_type=False)
_modo_enum      = PGEnum("sandbox", "live", name="modo_pasarela_enum", create_type=False)
_contexto_enum  = PGEnum("membresia", "producto", "suscripcion", name="contexto_pago_enum", create_type=False)
_estado_tx_enum = PGEnum("pendiente", "aprobado", "rechazado", "cancelado", "reembolsado",
                         name="estado_transaccion_enum", create_type=False)


class ConfiguracionPasarela(db.Model):
    """Credenciales de cobro de un gimnasio para un proveedor concreto."""
    __tablename__ = "configuracion_pasarela"
    __table_args__ = (
        db.UniqueConstraint("id_gimnasio", "proveedor", name="uq_pasarela_gym_proveedor"),
    )

    id            = db.Column(db.Integer, primary_key=True)
    id_gimnasio   = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    proveedor     = db.Column(_proveedor_enum, nullable=False)
    modo          = db.Column(_modo_enum, nullable=False, default="sandbox", server_default="sandbox")
    activo        = db.Column(db.Boolean, nullable=False, default=False, server_default="false")

    # JSON cifrado con Fernet. PayPal: {client_id, client_secret}
    #                          Mercado Pago: {access_token, public_key}
    credenciales  = db.Column(db.Text, nullable=True)

    moneda        = db.Column(db.String(3), nullable=False, default="MXN", server_default="MXN")

    # Nombre visible de la cuenta receptora (informativo, lo escribe el owner)
    titular_cuenta = db.Column(db.String(150), nullable=True)

    # Resultado de la última verificación de credenciales
    verificado_en  = db.Column(db.DateTime(timezone=True), nullable=True)
    ultimo_error   = db.Column(db.String(300), nullable=True)

    created_at    = db.Column(db.DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(timezone.utc))
    updated_at    = db.Column(db.DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(timezone.utc),
                              onupdate=lambda: datetime.now(timezone.utc))

    @staticmethod
    def _val(campo):
        return campo.value if hasattr(campo, "value") else campo

    def to_dict(self, pista: str | None = None):
        """
        Representación segura: NUNCA incluye las credenciales en claro.
        'pista' es el enmascarado (p. ej. '····abcd') que calcula la ruta.
        """
        return {
            "id":             self.id,
            "proveedor":      self._val(self.proveedor),
            "modo":           self._val(self.modo),
            "activo":         self.activo,
            "moneda":         self.moneda,
            "titular_cuenta": self.titular_cuenta,
            "configurado":    bool(self.credenciales),
            "credencial_pista": pista,
            "verificado_en":  self.verificado_en.isoformat() if self.verificado_en else None,
            "ultimo_error":   self.ultimo_error,
        }

    def __repr__(self):
        return f"<ConfiguracionPasarela gym={self.id_gimnasio} {self._val(self.proveedor)}>"


class TransaccionPago(db.Model):
    """Un intento de cobro a través de una pasarela, en cualquier contexto."""
    __tablename__ = "transacciones_pago"

    id            = db.Column(db.Integer, primary_key=True)
    id_gimnasio   = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
                              nullable=False, index=True)
    proveedor     = db.Column(_proveedor_enum, nullable=False)
    contexto      = db.Column(_contexto_enum, nullable=False)
    estado        = db.Column(_estado_tx_enum, nullable=False, default="pendiente",
                              server_default="pendiente", index=True)

    # Identificador del lado de la pasarela (order id de PayPal, preference id de MP)
    referencia_externa = db.Column(db.String(120), nullable=True, index=True)
    # Id del pago capturado (payment id), cuando aplica
    referencia_pago    = db.Column(db.String(120), nullable=True)

    monto         = db.Column(db.Numeric(10, 2), nullable=False)
    moneda        = db.Column(db.String(3), nullable=False, default="MXN", server_default="MXN")
    descripcion   = db.Column(db.String(255), nullable=True)

    # A qué apunta el cobro según el contexto:
    #   membresia   -> id del miembro (Mongo ObjectId en texto)
    #   producto    -> id de la venta
    #   suscripcion -> id de la suscripción
    referencia_local = db.Column(db.String(80), nullable=True, index=True)

    # Quién inició el pago (id de usuario PG, si aplica)
    id_usuario    = db.Column(db.Integer, nullable=True)

    # Payload útil para conciliación y depuración
    metadatos     = db.Column(db.JSON, nullable=True, default=dict)

    created_at    = db.Column(db.DateTime(timezone=True), nullable=False,
                              default=lambda: datetime.now(timezone.utc), index=True)
    fecha_pago    = db.Column(db.DateTime(timezone=True), nullable=True)

    @staticmethod
    def _val(campo):
        return campo.value if hasattr(campo, "value") else campo

    def to_dict(self):
        return {
            "id":                 self.id,
            "id_gimnasio":        self.id_gimnasio,
            "proveedor":          self._val(self.proveedor),
            "contexto":           self._val(self.contexto),
            "estado":             self._val(self.estado),
            "referencia_externa": self.referencia_externa,
            "referencia_pago":    self.referencia_pago,
            "monto":              float(self.monto) if self.monto is not None else None,
            "moneda":             self.moneda,
            "descripcion":        self.descripcion,
            "referencia_local":   self.referencia_local,
            "created_at":         self.created_at.isoformat() if self.created_at else None,
            "fecha_pago":         self.fecha_pago.isoformat() if self.fecha_pago else None,
        }

    def __repr__(self):
        return f"<TransaccionPago {self.id} {self._val(self.proveedor)} {self._val(self.estado)}>"
