from sqlalchemy import event


def test_shell_does_not_scan_catalogue_and_uses_one_deferred_application_script(db, client):
    from bs4 import BeautifulSoup

    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", capture)
    try:
        response = client.get("/")
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture)
    assert response.status_code == 200
    assert not any("track_metadata" in sql for sql in statements)
    document = BeautifulSoup(response.text, "html.parser")
    scripts = document.select("script[src]")
    assert len(scripts) == 1 and scripts[0].has_attr("defer")
    bundle = client.get(scripts[0]["src"])
    assert bundle.status_code == 200
    assert "immutable" in bundle.headers["cache-control"]
    assert "window.RadioPlayer" in bundle.text and "window.RadioNavigation" in bundle.text
