from pathlib import Path

from app.config import PROJECT_ROOT, Settings


def test_default_data_dir_resolves_to_project_root():
    settings = Settings(app_data_dir="./data", app_db_path="./data/app.db")
    assert settings.data_dir == (PROJECT_ROOT / "data").resolve()
    assert settings.database_path == (PROJECT_ROOT / "data" / "app.db").resolve()


def test_relative_db_name_resolves_under_data_dir():
    settings = Settings(app_data_dir="./data", app_db_path="app-local.db")
    assert settings.database_path == (PROJECT_ROOT / "data" / "app-local.db").resolve()
