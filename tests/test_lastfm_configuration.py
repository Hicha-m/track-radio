import pytest

from scripts.configure_lastfm import configure


def test_private_configuration_replaces_duplicates_and_preserves_other_settings(tmp_path):
    secret, target = tmp_path / "key", tmp_path / ".env"
    secret.write_text("a" * 32)
    target.write_text("ADMIN_TOKEN=example\nLASTFM_API_KEY=old\nexport LASTFM_API_KEY=old\n")
    configure(secret, target)
    assert target.read_text() == "ADMIN_TOKEN=example\nLASTFM_API_KEY=" + "a" * 32 + "\n"
    assert target.stat().st_mode & 0o777 == 0o600
    secret.write_text("invalid\nKEY=injected")
    with pytest.raises(ValueError):
        configure(secret, target)
    assert target.read_text().startswith("ADMIN_TOKEN=example\nLASTFM_API_KEY=aaaa")


def test_youtube_configuration_is_private_and_preserves_lastfm(tmp_path):
    secret, target = tmp_path / "key", tmp_path / ".env"
    secret.write_text("AIza" + "a" * 35)
    target.write_text("LASTFM_API_KEY=existing\nYOUTUBE_API_KEY=old\n")
    configure(secret, target, "YOUTUBE_API_KEY")
    assert "LASTFM_API_KEY=existing" in target.read_text()
    assert target.read_text().count("YOUTUBE_API_KEY=") == 1
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        configure(secret, target, "UNSUPPORTED")
