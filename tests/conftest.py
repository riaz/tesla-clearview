import pytest

from clearview import config


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Keep tests away from the real ~/.config/clearview (tokens, last car location)."""
    default = config.Settings.model_fields["token_file"]
    monkeypatch.setattr(default, "default", tmp_path / "tokens.json")
    config.Settings.model_rebuild(force=True)
