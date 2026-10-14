"""Check all access modes using synthetic config; optionally smoke-test a fresh local setup.

Requires Docker Compose. Never loads the operator's .env or credentials. The smoke test only
starts an isolated database and app; it never starts a worker or calls the Kickbase API.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.setup import create_setup

REPO = Path(__file__).resolve().parents[1]
MODES = ("local", "https", "external", "ionos")


def command(root: Path, mode: str | None, project: str) -> list[str]:
    args = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--project-directory",
        str(root),
        "--env-file",
        str(root / ".env"),
        "-f",
        str(REPO / "compose.yaml"),
    ]
    if mode:
        args += ["-f", str(REPO / f"compose.{mode}.yaml")]
    return args


def inspect_mode(root: Path, mode: str | None) -> dict:
    # Explicit env-file/project directory and a scrubbed environment prevent loading real data.
    env = {key: value for key, value in os.environ.items() if key not in CONFIG_KEYS}
    output = subprocess.check_output(
        [*command(root, mode, "kbh-config-check"), "config", "--format", "json"], env=env
    )
    return json.loads(output)


CONFIG_KEYS = {
    "COMPOSE_FILE",
    "COMPOSE_PROFILES",
    "APP_UID",
    "APP_GID",
    "APP_HTTP_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "KICKBASE_EMAIL_FILE",
    "KICKBASE_PASSWORD_FILE",
    "IONOS_TOKEN_FILE",
    "PUBLIC_URL",
    "PROXY_BIND_ADDRESS",
    "PROXY_HTTP_PORT",
    "PROXY_HTTPS_PORT",
    "TZ",
}


def check_modes(root: Path) -> None:
    # URLs below are fixture values, never real installation addresses.
    env_file = root / ".env"
    original = env_file.read_text()
    for mode in (None, *MODES):
        content = original
        if mode in ("https", "external", "ionos"):
            content = content.replace("http://localhost:8080", "https://kickbase.example.com")
        env_file.write_text(content)
        config = inspect_mode(root, mode)
        services = config["services"]
        assert not services["db"].get("ports"), "Database must stay private"
        assert ("ionos_token" in config["secrets"]) == (mode == "ionos")
        if mode in ("local", "external"):
            ports = services["app"]["ports"]
            assert len(ports) == 1 and ports[0]["host_ip"] == "127.0.0.1"
            assert ports[0]["target"] == 8000
            assert services["app"]["environment"]["SESSION_HTTPS_ONLY"] == (
                "false" if mode == "local" else "true"
            )
            assert "proxy" not in services
        elif mode:
            assert not services["app"].get("ports")
            proxy = services["proxy"]
            assert proxy["environment"]["PUBLIC_URL"] == "https://kickbase.example.com"
            assert services["app"]["environment"]["SESSION_HTTPS_ONLY"] == "true"
            if mode == "https":
                assert proxy["image"] == "caddy:2" and "build" not in proxy
                assert {port["target"] for port in proxy["ports"]} == {80, 443}
                assert not proxy.get("secrets")
            else:
                assert proxy["image"] == "kickbase-helper-proxy:latest"
                assert {port["target"] for port in proxy["ports"]} == {443}
                assert proxy["secrets"][0]["source"] == "ionos_token"
                assert {v["source"] for v in proxy["volumes"] if v["type"] == "volume"} == {
                    "caddy_data",
                    "caddy_config",
                }
        else:
            assert set(services) == {"app", "worker", "db"}
            assert not services["app"].get("ports")
        print(f"Compose {mode or 'core'}: ok")
    env_file.write_text(original)


def smoke_test(root: Path, image: str) -> None:
    project = "kbh-setup-test-" + uuid.uuid4().hex[:10]
    override = root / "smoke.yaml"
    # Let Docker choose a free loopback port; never use the production project/ports/volumes.
    override.write_text(
        "services:\n  app:\n    image: " + json.dumps(image) + "\n"
        "    ports: !override\n      - '127.0.0.1::8000'\n"
    )
    args = [*command(root, "local", project), "-f", str(override)]
    env = {key: value for key, value in os.environ.items() if key not in CONFIG_KEYS}
    try:
        subprocess.run([*args, "up", "-d", "--no-build", "db", "app"], env=env, check=True)
        # `config` escapes '$' for re-usable output; verify the actual runtime value as well.
        subprocess.run(
            [
                *args,
                "exec",
                "-T",
                "app",
                "python",
                "-c",
                "import os; assert os.environ['KICKBASE_LEAGUE_NAME'] == "
                + repr("Fixture 'Liga' $1 \\ Test"),
            ],
            env=env,
            check=True,
        )
        port = subprocess.check_output([*args, "port", "app", "8000"], env=env, text=True).strip()
        with httpx.Client(base_url="http://" + port, timeout=3, trust_env=False) as client:
            for _ in range(30):
                try:
                    response = client.get("/health")
                    if response.status_code == 200 and response.json().get("db") == "ok":
                        break
                except httpx.TransportError:
                    pass
                time.sleep(1)
            else:
                raise RuntimeError("Fresh local app did not become healthy")
            login = client.get("/login")
            csrf = re.search(r'name="csrf" value="([^"]+)"', login.text)
            assert login.status_code == 200 and csrf
            response = client.post(
                "/login", data={"password": "fixture-app", "csrf": csrf[1], "next": "/"}
            )
            assert response.status_code == 303
            assert "session=" in response.headers["set-cookie"]
            assert "secure" not in response.headers["set-cookie"].lower()
            # Check authentication without loading a page that would fetch Kickbase data.
            assert client.get("/login").status_code == 303
        print("Fresh local setup: database, migrations, health and HTTP login ok")
    finally:
        subprocess.run([*args, "down", "--volumes"], env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-image", help="Built app image for isolated local first-start test")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="kbh-setup-check-") as directory:
        root = Path(directory)
        (root / ".env.example").write_bytes((REPO / ".env.example").read_bytes())
        sample = {
            "league": "Fixture 'Liga' $1 \\ Test",
            "email": "fixture@example.com",
            "kickbase_password": "fixture-password",
            "app_password": "fixture-app",
        }
        if args.smoke_image:
            # The documented Docker-only setup path also works for host users other than 1000.
            subprocess.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--user",
                    f"{os.getuid()}:{os.getgid()}",
                    "--volume",
                    f"{root}:/setup",
                    "--workdir",
                    "/setup",
                    args.smoke_image,
                    "python",
                    "-c",
                    "import runpy; from pathlib import Path; "
                    "setup = runpy.run_path('/srv/setup.py'); "
                    f"setup['create_setup'](Path.cwd(), **{sample!r})",
                ],
                check=True,
            )
        else:
            create_setup(root, **sample)
        # Only the IONOS matrix case needs this extra synthetic secret.
        (root / "secrets" / "ionos_token").write_text("fixture.fixture")
        check_modes(root)
        config = inspect_mode(root, "local")
        assert config["services"]["app"]["environment"]["KICKBASE_LEAGUE_NAME"].replace(
            "$$", "$"
        ) == ("Fixture 'Liga' $1 \\ Test"), "Compose must preserve quoted league names exactly"
        if args.smoke_image:
            smoke_test(root, args.smoke_image)


if __name__ == "__main__":
    main()
