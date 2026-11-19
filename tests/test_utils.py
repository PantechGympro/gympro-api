"""Utilidades puras: roles, búsqueda, cifrado, rate-limit y parsers del ETL (Ollama)."""
import pytest
from cryptography.fernet import Fernet

from app.constants.roles import ROLES_CANONICOS, normalizar_rol
from app.utils import crypto
from app.utils.busqueda import normalizar_texto, patron_insensible
from app.utils.etl_ollama import chunk_text, parse_llm_json
from app.utils.rate_limit import politica_para

pytestmark = pytest.mark.unit


class TestRoles:
    @pytest.mark.parametrize("alias,canonico", [
        ("owner_gym", "Propietario"), ("OWNER", "Propietario"), ("  admin ", "Administrador"),
        ("trainer", "Entrenador"), ("receptionist", "Recepcionista"), ("user", "Miembro"),
        ("SUPERADMIN", "superadmin"),
    ])
    def test_alias_se_resuelven(self, alias, canonico):
        assert normalizar_rol(alias) == canonico

    def test_desconocido_se_devuelve_tal_cual(self):
        assert normalizar_rol("hacker") == "hacker"

    def test_none_no_revienta(self):
        assert normalizar_rol(None) is None

    def test_los_canonicos_son_estables(self):
        assert set(ROLES_CANONICOS) == {"superadmin", "Propietario", "Administrador",
                                        "Entrenador", "Recepcionista", "Miembro"}
        assert all(normalizar_rol(r) == r for r in ROLES_CANONICOS)


class TestBusqueda:
    def test_normalizar_quita_acentos_y_minusculas(self):
        assert normalizar_texto("JOSÉ Peña") == "jose pena"

    def test_normalizar_vacio(self):
        assert normalizar_texto("") == "" and normalizar_texto(None) == ""

    def test_patron_insensible_a_acentos(self):
        import re
        rx = re.compile(patron_insensible("jose"), re.IGNORECASE)
        assert rx.search("José") and rx.search("JOSE") and rx.search("jose")


class TestCrypto:
    def test_sin_clave_lanza(self, monkeypatch):
        monkeypatch.delenv("PAYMENTS_ENCRYPTION_KEY", raising=False)
        with pytest.raises(crypto.CryptoNoConfigurado):
            crypto.cifrar_dict({"a": 1})

    def test_clave_invalida_lanza(self, monkeypatch):
        monkeypatch.setenv("PAYMENTS_ENCRYPTION_KEY", "no-es-una-clave")
        with pytest.raises(crypto.CryptoNoConfigurado):
            crypto.cifrar_dict({"a": 1})

    def test_ida_y_vuelta_y_no_es_texto_plano(self, monkeypatch):
        monkeypatch.setenv("PAYMENTS_ENCRYPTION_KEY", Fernet.generate_key().decode())
        datos = {"token": "secreto-ñ", "n": 3}
        cifrado = crypto.cifrar_dict(datos)
        assert "secreto" not in cifrado
        assert crypto.descifrar_dict(cifrado) == datos

    def test_descifrar_vacio(self):
        assert crypto.descifrar_dict(None) == {} and crypto.descifrar_dict("") == {}

    def test_enmascarar(self):
        assert crypto.enmascarar("1234567890123456") .endswith("3456")
        assert "1234567890" not in crypto.enmascarar("1234567890123456")
        assert crypto.enmascarar(None) is None


class TestRateLimitPolitica:
    def test_ruta_sin_politica_devuelve_none(self):
        assert politica_para("/ruta/que/no/existe", {"GET"}) is None

    def test_es_determinista(self):
        assert politica_para("/api/auth/login", {"POST"}) == politica_para("/api/auth/login", {"POST"})


class TestEtlParsers:
    def test_parse_llm_json_limpio(self):
        assert parse_llm_json('{"a": 1}') == {"a": 1}

    def test_parse_llm_json_con_fences(self):
        assert parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_parse_llm_json_con_texto_alrededor(self):
        assert parse_llm_json('Claro, aquí está: {"a": {"b": 2}} espero que sirva') == {"a": {"b": 2}}

    @pytest.mark.parametrize("raw", ["", None, "sin json", "[1, 2]", '{"a": ', "```"])
    def test_parse_llm_json_invalido_devuelve_none(self, raw):
        assert parse_llm_json(raw) is None

    def test_chunk_respeta_tamano_y_no_pierde_lineas(self):
        texto = "\n".join(f"linea {i:03d}" for i in range(100))
        chunks, truncado = chunk_text(texto, max_chars=100, max_chunks=1000)
        assert truncado is False
        assert "\n".join(chunks) == texto
        assert all(len(c) <= 100 for c in chunks)

    def test_chunk_marca_truncado_si_excede(self):
        texto = "\n".join("x" * 50 for _ in range(100))
        chunks, truncado = chunk_text(texto, max_chars=120, max_chunks=2)
        assert truncado is True and len(chunks) == 2

    def test_chunk_texto_vacio(self):
        assert chunk_text("", max_chars=100) == ([], False)
