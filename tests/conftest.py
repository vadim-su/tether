import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep tests away from the real ~/.tether and cwd."""
    monkeypatch.setenv("TETHER_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(tmp_path)
    return tmp_path
