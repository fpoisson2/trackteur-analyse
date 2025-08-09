import re


def extract_csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def get_csrf(client, url: str) -> str:
    resp = client.get(url)
    return extract_csrf_token(resp.get_data(as_text=True))


def login(client, username: str = "admin", password: str = "pass"):
    token = get_csrf(client, "/login")
    return client.post(
        "/login",
        data={"username": username, "password": password, "csrf_token": token},
    )
