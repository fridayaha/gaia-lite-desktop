from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = (
        "postgresql+psycopg://hub_user:hub_password@localhost:5432/hub_poc"
    )
    test_database_url: str = "sqlite:///:memory:"

    storage_backend: str = "disabled"
    storage_local_root: str = ".hub_storage"

    gitleaks_enabled: bool = False
    gitleaks_bin: str = "gitleaks"
    gitleaks_timeout_seconds: int = 30

    betterleaks_enabled: bool = False
    betterleaks_bin: str = "betterleaks"
    betterleaks_timeout_seconds: int = 30
    betterleaks_config: str = ""

    hub_tenant_legacy_visible: bool = False

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()
