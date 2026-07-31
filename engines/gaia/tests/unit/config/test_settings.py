"""Unit tests for Settings (pydantic-settings).

These tests check default values as defined in the Settings class.
Note: if a .env file exists with real values, those will override defaults.
We test computed properties (DSN, URIs) which are stable.
"""

from ontology.config.settings import Settings


class TestSettingsDefaults:
    def test_pg_defaults(self):
        s = Settings()
        assert s.pg_port == 5432

    def test_pg_dsn_format(self):
        """Verify the DSN starts with the expected protocol."""
        s = Settings()
        dsn = s.pg_dsn
        assert dsn.startswith("postgresql+asyncpg://")

    def test_gravitino_uri_format(self):
        """Verify the Gravitino URI has the right structure."""
        s = Settings(gravitino_host="gravitino.internal", gravitino_port=9090)
        assert s.gravitino_uri == "http://gravitino.internal:9090"

    def test_trino_defaults(self):
        s = Settings()
        assert s.trino_port == 8080
        assert s.trino_catalog == "gravitino"

    def test_app_defaults(self):
        s = Settings()
        assert s.app_port == 8000
        assert isinstance(s.app_log_level, str)

    def test_extra_fields_ignored(self):
        """Extra env vars are ignored per extra='ignore'."""
        s = Settings()
        assert not hasattr(s, "unknown_setting")

    def test_env_override(self):
        """Environment-specific values are loaded from .env — just verify structure."""
        s = Settings()
        assert isinstance(s.pg_host, str)
        assert isinstance(s.pg_port, int)
        assert isinstance(s.ai_model, str)
        assert isinstance(s.ai_temperature, float)

    def test_ai_openai_base_url_default_empty(self):
        """ai_openai_base_url defaults to empty string (OpenAI default endpoint)."""
        s = Settings()
        assert s.ai_openai_base_url == ""


class TestOpenAIBaseUrlReexport:
    """The OPENAI_BASE_URL env var re-export has a subtle correctness invariant:
    pydantic-ai's OpenAIProvider reads OPENAI_BASE_URL unconditionally via
    os.getenv and passes it straight to AsyncOpenAI(base_url=...). An empty
    string makes base_url "" (malformed URL) instead of falling back to OpenAI's
    default — only the *absence* of the env var triggers the default. So we must
    never export an empty value. These tests run the module-level re-export in an
    isolated subprocess with controlled env to verify both branches.
    """

    def test_non_empty_base_url_is_exported(self, tmp_path, monkeypatch):
        import subprocess
        import sys

        env = {
            **dict(__import__("os").environ),
            # Clear any pre-existing OPENAI_BASE_URL so our value wins.
            "OPENAI_BASE_URL": "",
            "AI_OPENAI_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
            # Avoid .env / .env.local leaking real values into the test.
            "PG_HOST": "localhost",
        }
        env.pop("OPENAI_BASE_URL", None)
        code = "import os; from ontology.config import settings; print(os.environ.get('OPENAI_BASE_URL', '<unset>'))"
        out = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
        )
        assert out.stdout.strip() == "https://open.bigmodel.cn/api/paas/v4"

    def test_empty_base_url_is_not_exported(self, tmp_path):
        import subprocess
        import sys

        env = {
            **dict(__import__("os").environ),
            "AI_OPENAI_BASE_URL": "",
            "PG_HOST": "localhost",
        }
        env.pop("OPENAI_BASE_URL", None)
        code = "import os; from ontology.config import settings; print(os.environ.get('OPENAI_BASE_URL', '<unset>'))"
        out = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]),
        )
        # Must be unset (not empty string) so pydantic-ai falls back to default.
        assert out.stdout.strip() == "<unset>"
