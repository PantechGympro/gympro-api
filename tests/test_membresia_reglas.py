"""Reglas de vigencia de membresía (utils/membresia.py). Pruebas unitarias puras:
no tocan base de datos; reciben `ahora` fijo para ser deterministas."""
from datetime import datetime, timedelta, timezone

import pytest

from app.utils.membresia import _evaluar_docs, _fin_efectivo, a_utc

UTC = timezone.utc
AHORA = datetime(2026, 11, 18, 15, 30, tzinfo=UTC)  # hora "rara": no es medianoche


def doc(fin, estado="Activa", **extra):
    return {"estado": estado, "fecha_fin": fin, **extra}


pytestmark = pytest.mark.unit


class TestAUtc:
    def test_none(self):
        assert a_utc(None) is None

    def test_naive_se_asume_utc(self):
        assert a_utc(datetime(2026, 1, 1, 10)) == datetime(2026, 1, 1, 10, tzinfo=UTC)

    def test_aware_se_convierte_a_utc(self):
        local = datetime(2026, 1, 1, 10, tzinfo=timezone(timedelta(hours=-6)))
        assert a_utc(local) == datetime(2026, 1, 1, 16, tzinfo=UTC)

    def test_texto_iso_solo_fecha(self):
        assert a_utc("2026-11-20") == datetime(2026, 11, 20, tzinfo=UTC)

    def test_texto_invalido_devuelve_none(self):
        assert a_utc("no-es-fecha") is None


class TestFinEfectivo:
    def test_none(self):
        assert _fin_efectivo(None) is None

    def test_medianoche_vale_hasta_fin_del_dia(self):
        fin = datetime(2026, 11, 20, tzinfo=UTC)
        assert _fin_efectivo(fin) == fin + timedelta(days=1)

    def test_con_hora_no_se_extiende(self):
        fin = datetime(2026, 11, 20, 15, 30, tzinfo=UTC)
        assert _fin_efectivo(fin) == fin


class TestEvaluarDocs:
    def test_sin_documentos(self):
        r = _evaluar_docs([], AHORA)
        assert (r["estado"], r["vigente"], r["codigo"]) == ("sin_membresia", False, "sin_membresia")

    def test_activa_a_30_dias(self):
        r = _evaluar_docs([doc(AHORA + timedelta(days=30))], AHORA)
        assert (r["estado"], r["vigente"], r["codigo"]) == ("activa", True, None)

    def test_por_vencer_a_3_dias(self):
        r = _evaluar_docs([doc(AHORA + timedelta(days=3))], AHORA)
        assert r["estado"] == "por_vencer" and r["vigente"] is True
        assert r["dias_restantes"] == 3

    def test_limite_de_7_dias_incluido(self):
        assert _evaluar_docs([doc(AHORA + timedelta(days=7))], AHORA)["estado"] == "por_vencer"
        assert _evaluar_docs([doc(AHORA + timedelta(days=7, hours=1))], AHORA)["estado"] == "activa"

    def test_vencida_hace_3_dias(self):
        r = _evaluar_docs([doc(AHORA - timedelta(days=3))], AHORA)
        assert (r["estado"], r["vigente"], r["codigo"]) == ("vencida", False, "membresia_vencida")
        assert r["vencio_hace_dias"] == 3

    def test_fecha_texto_vale_hasta_el_final_del_dia(self):
        d = doc("2026-11-20")
        assert _evaluar_docs([d], datetime(2026, 11, 20, 15, 30, tzinfo=UTC))["vigente"] is True
        assert _evaluar_docs([d], datetime(2026, 11, 21, 0, 0, 1, tzinfo=UTC))["vigente"] is False

    def test_con_renovacion_gana_la_vigente_mas_larga(self):
        r = _evaluar_docs([doc(AHORA + timedelta(days=2)), doc(AHORA + timedelta(days=40))], AHORA)
        assert r["estado"] == "activa"
        assert r["dias_restantes"] == 40

    def test_documento_cancelado_no_cuenta_como_vigente(self):
        r = _evaluar_docs([doc(AHORA + timedelta(days=30), estado="Cancelada")], AHORA)
        assert r["vigente"] is False and r["estado"] == "vencida"

    def test_vencida_y_renovada_prefiere_la_vigente(self):
        r = _evaluar_docs([doc(AHORA - timedelta(days=10)), doc(AHORA + timedelta(days=20))], AHORA)
        assert r["vigente"] is True

    def test_sin_fecha_fin_no_revienta(self):
        r = _evaluar_docs([doc(None)], AHORA)
        assert r["vigente"] is False and r["estado"] == "vencida"
