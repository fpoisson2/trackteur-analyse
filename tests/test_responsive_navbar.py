from tests.utils import login


def test_index_navbar_responsive(make_app):
    app = make_app()
    client = app.test_client()
    login(client)
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.data.decode()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    toggler = soup.select_one("button.navbar-toggler")
    assert toggler is not None
    assert toggler.get("data-bs-target") == "#navbarNav"
    nav_container = soup.select_one("div#navbarNav")
    assert nav_container is not None
    assert "navbar-collapse" in nav_container.get("class", [])
