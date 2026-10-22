"""
models/pg/complemento_capacidad.py -- Lotes de capacidad extra de miembros.

Peticion explicita del usuario: que el Propietario pueda comprar mas
espacio para miembros por encima del tope de su plan, en lotes de tamano
fijo (10/50/100/200/500), cada uno con su propio precio.

Decision de arquitectura (no hay forma simple de resolverlo de otro modo):
un lote NO se suma al cobro recurrente ya autorizado en PayPal/Mercado
Pago -- ninguna de las dos pasarelas deja modificar el monto de un
acuerdo que ya esta corriendo (ver suscripcion_recurrente.py). Por eso un
lote se paga con el MISMO checkout de un solo cobro que ya usa
pagos_online.py (contexto "lote_capacidad", ver ese archivo), y su
vigencia queda ATADA al ciclo de facturacion elegido al comprarlo (30
dias si "mensual", 365 si "anual") -- igual que ya se "renueva a mano" la
suscripcion cuando no hay cargo recurrente activo (ver
OwnerSubscription.jsx). Si el ciclo vence sin volver a comprarse, el lote
deja de contar -- "vigente" se decide por fecha_fin en el momento de la
consulta (ver utils/capacidad.py::extra_miembros_de()), no por un job que
tenga que marcarlo vencido.

La capacidad extra es POR SUCURSAL (id_gimnasio), igual que el tope base
del plan (ver utils/capacidad.py) -- un lote comprado en una sucursal no
amplia el tope de las demas sucursales de la misma organizacion.
"""
from datetime import datetime, timezone

from app.extensions import db

# Catalogo de lotes -- tamano fijo, precio en centavos MXN. El precio
# ANUAL sigue la misma convencion que los planes (mensual x 10, "2 meses
# gratis", ver migracion 023) en vez de definirse aparte.
LOTES_CAPACIDAD = [
    {"cantidad_miembros": 10,  "precio_mensual_mxn": 14900},
    {"cantidad_miembros": 50,  "precio_mensual_mxn": 59900},
    {"cantidad_miembros": 100, "precio_mensual_mxn": 99900},
    {"cantidad_miembros": 200, "precio_mensual_mxn": 179900},
    {"cantidad_miembros": 500, "precio_mensual_mxn": 399900},
]
_LOTES_POR_CANTIDAD = {l["cantidad_miembros"]: l for l in LOTES_CAPACIDAD}

CICLOS_VALIDOS  = ["mensual", "anual"]
ESTADOS_VALIDOS = ["activo", "cancelado"]


def precio_lote_mxn(cantidad_miembros, ciclo):
    """
    Precio en centavos del lote `cantidad_miembros` para `ciclo`, o None
    si `cantidad_miembros` no es uno de los tamanos del catalogo.
    """
    lote = _LOTES_POR_CANTIDAD.get(cantidad_miembros)
    if not lote:
        return None
    if ciclo == "anual":
        return lote["precio_mensual_mxn"] * 10
    return lote["precio_mensual_mxn"]


def catalogo_lotes():
    """Catalogo completo con precio mensual Y anual ya calculado, para el frontend."""
    return [
        {
            "cantidad_miembros":  l["cantidad_miembros"],
            "precio_mensual_mxn": l["precio_mensual_mxn"],
            "precio_anual_mxn":   l["precio_mensual_mxn"] * 10,
        }
        for l in LOTES_CAPACIDAD
    ]


class ComplementoCapacidad(db.Model):
    """Un lote de capacidad extra comprado por una sucursal."""
    __tablename__ = "complementos_capacidad"

    id                = db.Column(db.Integer, primary_key=True)
    id_gimnasio       = db.Column(db.Integer, db.ForeignKey("gimnasios.id", ondelete="CASCADE"),
                                   nullable=False, index=True)

    cantidad_miembros = db.Column(db.Integer, nullable=False)
    ciclo             = db.Column(db.String(10), nullable=False, default="mensual", server_default="mensual")
    precio_mxn        = db.Column(db.Integer, nullable=False)  # snapshot del catalogo al comprarlo
    estado            = db.Column(db.String(12), nullable=False, default="activo", server_default="activo")

    fecha_inicio      = db.Column(db.DateTime(timezone=True), nullable=False,
                                   default=lambda: datetime.now(timezone.utc))
    fecha_fin         = db.Column(db.DateTime(timezone=True), nullable=False)

    # Transaccion (PayPal/Mercado Pago) que lo origino -- ver pagos_online.py
    id_transaccion    = db.Column(db.Integer, db.ForeignKey("transacciones_pago.id", ondelete="SET NULL"),
                                   nullable=True)

    creado_en         = db.Column(db.DateTime(timezone=True), nullable=False,
                                   default=lambda: datetime.now(timezone.utc))

    def vigente(self, ahora=None):
        ahora = ahora or datetime.now(timezone.utc)
        fin = self.fecha_fin
        if fin is not None and fin.tzinfo is None:
            fin = fin.replace(tzinfo=timezone.utc)
        return self.estado == "activo" and fin is not None and fin >= ahora

    def to_dict(self):
        return {
            "id":                self.id,
            "cantidad_miembros": self.cantidad_miembros,
            "ciclo":             self.ciclo,
            "precio_mxn":        self.precio_mxn,
            "precio_display":    f"${self.precio_mxn / 100:,.2f} MXN/{'ano' if self.ciclo == 'anual' else 'mes'}",
            "estado":            self.estado,
            "vigente":           self.vigente(),
            "fecha_inicio":      self.fecha_inicio.isoformat() if self.fecha_inicio else None,
            "fecha_fin":         self.fecha_fin.isoformat() if self.fecha_fin else None,
        }

    def __repr__(self):
        return f"<ComplementoCapacidad gym={self.id_gimnasio} +{self.cantidad_miembros} {self.estado}>"
