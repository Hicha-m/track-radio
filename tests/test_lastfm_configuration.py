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
