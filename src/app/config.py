import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.azd_env import get_azd_env_value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
    # App
    app_port: int = Field(8000, alias="APP_PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # Redis
    redis_enabled: bool = Field(False, alias="REDIS_ENABLED")
    redis_ssl: bool = Field(True, alias="REDIS_SSL")
    # Prefer azd env when available; fallback to env vars
    redis_host: str | None = get_azd_env_value(
        "AZURE_REDIS_HOST", os.getenv("REDIS_HOST")
    )
    # Azure Managed Redis defaults to 10000/TLS
    redis_port: int = int(
        get_azd_env_value("AZURE_REDIS_PORT", os.getenv("REDIS_PORT", "10000"))
    )
    redis_max_connections: int = Field(50, alias="REDIS_MAX_CONNECTIONS")
    redis_socket_timeout: float = Field(3.0, alias="REDIS_SOCKET_TIMEOUT")
    redis_socket_connect_timeout: float = Field(
        3.0, alias="REDIS_SOCKET_CONNECT_TIMEOUT"
    )
    redis_max_retries: int = Field(1, alias="REDIS_MAX_RETRIES")
    redis_backoff_base: float = Field(1.0, alias="REDIS_BACKOFF_BASE")
    redis_backoff_cap: float = Field(3.0, alias="REDIS_BACKOFF_CAP")

    # Entra ID (Workload Identity/UAMI)
    # DefaultAzureCredential selects the target UAMI when AZURE_CLIENT_ID is set
    # In azd environments, AKS Workload Identity injects AZURE_CLIENT_ID into the Pod
    azure_client_id: str | None = get_azd_env_value("AZURE_CLIENT_ID", None)

    # Telemetry
    # Keep both names for compatibility
    appinsights_connection_string: str | None = Field(
        default=None, alias="APPLICATIONINSIGHTS_CONNECTION_STRING"
    )
    applicationinsights_connection_string: str | None = get_azd_env_value(
        "APPLICATIONINSIGHTS_CONNECTION_STRING", None
    )
    telemetry_enabled: bool = Field(True, alias="TELEMETRY_ENABLED")
    custom_metrics_enabled: bool = Field(True, alias="CUSTOM_METRICS_ENABLED")
    telemetry_sampling_rate: float = Field(0.1, alias="TELEMETRY_SAMPLING_RATE")

    model_config = {
        "populate_by_name": True,
        "extra": "ignore",
    }
