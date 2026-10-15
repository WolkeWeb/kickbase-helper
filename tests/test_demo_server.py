from fastapi.testclient import TestClient

from scripts.demo_server import DEMO_PASSWORD, build_demo_app
from tests.conftest import extract_csrf


def test_demo_app_renders_the_readme_pages(tmp_path):
    with TestClient(build_demo_app(tmp_path), base_url="http://testserver") as client:
        csrf = extract_csrf(client.get("/login").text)
        client.post("/login", data={"password": DEMO_PASSWORD, "csrf": csrf, "next": "/"})
        for path in ["/", "/empfehlungen/elf", "/empfehlungen/kaufen", "/empfehlungen/verkaufen"]:
            assert client.get(path).status_code == 200, path
        assert "Hansen" in client.get("/spieler/6292").text
