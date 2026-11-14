import json
import re
from pathlib import Path

from app.i18n import CATALOGUES, translate
from app.models import Station


def test_catalogues_keys_and_placeholders_match():
    assert CATALOGUES["fr"].keys() == CATALOGUES["en"].keys()
    for key, value in CATALOGUES["fr"].items():
        assert set(re.findall(r"\{\w+\}", value)) == set(
            re.findall(r"\{\w+\}", CATALOGUES["en"][key])
        ), key
    for path in [*Path("app/static").glob("*.js"), *Path("app/templates").glob("*.html")]:
        for key in re.findall(r"ui\.[a-f0-9]{12}", path.read_text()):
            assert key in CATALOGUES["fr"], (path, key)


def test_language_selection_shared_text_and_untranslated_station(db, client):
    db.add(Station(name="Titre", slug="titre", stream_url="https://example.test/live"))
    db.commit()
    html = client.get("/?lang=en").text
    assert '<html lang="en">' in html
    assert "<h1>Charts</h1>" in html
    assert 'class="station-history-open" type="button" data-id="1">Titre</button>' in html
    french = client.get("/?lang=invalid").text
    assert '<html lang="fr">' in french
    client.cookies.set("radio_language", "en")
    assert "<h1>Advanced search</h1>" in client.get("/recherche").text
    assert '<html lang="fr">' in client.get("/?lang=fr").text
    assert translate("Titre", "en") == "Title"
    assert translate("not_a_known_key", "en") == "not_a_known_key"


def test_interpolation_does_not_translate_metadata():
    key = next(k for k, v in CATALOGUES["fr"].items() if v == "Détails de {p0}")
    assert translate(key, "en", p0="Titre") == "Details of Titre"
    # Escaping is done by Jinja/textContent; the catalogue never becomes HTML.
    assert json.loads(json.dumps(CATALOGUES)) == CATALOGUES
