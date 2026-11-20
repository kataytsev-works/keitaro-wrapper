from functools import lru_cache
from typing import ClassVar

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Application settings read from environment variables and `backend/.env`.

    Variable names are the field names in upper case (`KEITARO_URL`). Environment variables win
    over the file. Fields without a default are required: the app does not start without them.
    The database URL is built from the `POSTGRES_*` parts, the same variables the PostgreSQL
    container reads, so the password is written once.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    keitaro_url: str
    keitaro_api_key: SecretStr
    postgres_user: str
    postgres_password: SecretStr
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    @field_validator("keitaro_url")
    @classmethod
    def strip_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")

    @property
    def keitaro_api_base_url(self) -> str:
        """Base URL of the Keitaro Admin API, e.g. `https://tracker.example.com/admin_api/v1`."""
        return f"{self.keitaro_url}/admin_api/v1"

    @property
    def database_url(self) -> URL:
        """SQLAlchemy URL with the async driver, built from the `postgres_*` fields.

        A `URL` object, not a string: `create_async_engine` takes it as is, and `str()` / `repr()`
        print the password as `***`, so logging it is safe. Where a plain string is unavoidable,
        render it explicitly with `render_as_string(hide_password=False)`.
        """
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Return the application settings, read once and cached."""
    return Settings()
