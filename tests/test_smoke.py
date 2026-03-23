"""Smoke tests — verify project structure and imports work."""


def test_import_src():
    """Verify src package is importable."""
    import src

    assert src is not None


def test_import_db():
    """Verify db module is importable."""
    from src import db

    assert hasattr(db, "check_connection")
    assert hasattr(db, "get_db")
    assert hasattr(db, "engine")


def test_project_structure():
    """Verify critical project files exist."""
    import os

    project_root = os.path.dirname(os.path.dirname(__file__))
    required_files = [
        "requirements.txt",
        "pyproject.toml",
        "Dockerfile",
        "docker-compose.yml",
        "CLAUDE.md",
        "src/__init__.py",
        "src/db.py",
    ]
    for f in required_files:
        path = os.path.join(project_root, f)
        assert os.path.exists(path), f"Missing required file: {f}"


def test_config_dirs_exist():
    """Verify config and template directories exist."""
    import os

    project_root = os.path.dirname(os.path.dirname(__file__))
    required_dirs = ["src", "config", "tests", "templates"]
    for d in required_dirs:
        path = os.path.join(project_root, d)
        assert os.path.isdir(path), f"Missing required directory: {d}"
