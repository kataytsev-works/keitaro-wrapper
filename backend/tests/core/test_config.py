from app.core.config import Settings


def make_settings(**overrides: str | int) -> Settings:
    values: dict[str, str | int] = {
        "keitaro_url": "https://tracker.test",
        "keitaro_api_key": "secret-key",
        "postgres_user": "user",
        "postgres_password": "p@ss/word",
        "postgres_db": "db",
        "postgres_host": "db.local",
        "postgres_port": 6543,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]  # values are validated by pydantic


def test_database_url_built_from_postgres_parts_with_escaped_password() -> None:
    settings: Settings = make_settings()

    assert (
        settings.database_url.render_as_string(hide_password=False)
        == "postgresql+asyncpg://user:p%40ss%2Fword@db.local:6543/db"
    )


def test_database_url_hides_password_when_printed() -> None:
    settings: Settings = make_settings()

    assert str(settings.database_url) == "postgresql+asyncpg://user:***@db.local:6543/db"
    assert "p@ss" not in repr(settings.database_url)
    assert "p%40ss" not in repr(settings.database_url)


def test_keitaro_url_trailing_slash_is_stripped() -> None:
    settings: Settings = make_settings(keitaro_url="https://tracker.test/")

    assert settings.keitaro_url == "https://tracker.test"
    assert settings.keitaro_api_base_url == "https://tracker.test/admin_api/v1"


def test_secrets_are_hidden_in_repr() -> None:
    settings: Settings = make_settings()

    assert "secret-key" not in repr(settings)
    assert "p@ss/word" not in repr(settings)
