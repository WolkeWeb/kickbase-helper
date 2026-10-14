"""First-install wizard; creates local configuration without printing secret values.

Run using the app image: python /srv/setup.py (mount the repository as working directory),
or on the host: uv run python scripts/setup.py. Never overwrites an existing installation.
"""

import getpass
import os
import re
import secrets
import sys
from pathlib import Path

from argon2 import PasswordHasher

SECRET_NAMES = (
    "kickbase_email",
    "kickbase_password",
    "telegram_bot_token",
    "llm_api_key",
    "claude_bridge_token",
)


def check_new_installation(root: Path) -> None:
    if (root / ".env").exists() or (root / ".env").is_symlink():
        raise ValueError("Eine .env existiert bereits. Bestehende Einrichtung bleibt unverändert.")
    if (root / "secrets").exists() or (root / "secrets").is_symlink():
        raise ValueError("Ein secrets-Pfad existiert bereits. Bitte manuell konfigurieren.")
    if not (root / ".env.example").is_file():
        raise ValueError("Im Repository-Verzeichnis mit .env.example ausführen.")


def quote_env(value: str) -> str:
    if any(character in value for character in ("\n", "\r", "\0")):
        raise ValueError("Konfigurationswerte dürfen keine Zeilenumbrüche enthalten.")
    # Compose and python-dotenv decode backslashes differently. These sequences either
    # break the closing quote or change the value; reject them before creating any files.
    if re.search(r"\\(?:[\\']|$)", value):
        raise ValueError(
            "Konfigurationswerte dürfen keinen abschließenden Backslash oder Backslash "
            "vor einem Apostroph oder weiteren Backslash enthalten."
        )
    return "'" + value.replace("'", "\\'") + "'"


def write_private(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
    except BaseException:
        path.unlink()
        raise


def create_setup(
    root: Path,
    *,
    league: str,
    email: str,
    kickbase_password: str,
    app_password: str,
) -> None:
    check_new_installation(root)
    if not all(value.strip() for value in (league, email, kickbase_password, app_password)):
        raise ValueError("Liga, Zugangsdaten und App-Passwort dürfen nicht leer sein.")
    if any(char in value for value in (email, kickbase_password) for char in ("\n", "\r", "\0")):
        raise ValueError("Zugangsdaten dürfen keine Zeilenumbrüche enthalten.")
    # Quote every substituted value: league names and Argon2 hashes can contain '$' and quotes.
    values = {
        "COMPOSE_FILE": os.pathsep.join(("compose.yaml", "compose.local.yaml")),
        "APP_UID": str(getattr(os, "getuid", lambda: 1000)()),
        "APP_GID": str(getattr(os, "getgid", lambda: 1000)()),
        "KICKBASE_LEAGUE_NAME": league,
        "APP_PASSWORD_HASH": PasswordHasher().hash(app_password),
        "SESSION_SECRET": secrets.token_hex(32),
        "POSTGRES_PASSWORD": secrets.token_hex(32),
    }
    template = (root / ".env.example").read_text(encoding="utf-8")
    for key, value in values.items():
        quoted = quote_env(value)
        template, count = re.subn(
            rf"^{key}=.*$", lambda _, line=f"{key}={quoted}": line, template, flags=re.MULTILINE
        )
        if count != 1:
            raise ValueError("Die .env.example ist unvollständig oder enthält doppelte Einträge.")
    secret_dir = root / "secrets"
    secret_dir.mkdir(mode=0o700)
    created: list[Path] = []
    try:
        for name in SECRET_NAMES:
            value = email if name == "kickbase_email" else ""
            if name == "kickbase_password":
                value = kickbase_password
            path = secret_dir / name
            write_private(path, value)
            created.append(path)
        write_private(root / ".env", template)
        created.append(root / ".env")
    except BaseException:
        # Only remove files created by this invocation; never pre-existing files.
        for path in reversed(created):
            path.unlink()
        secret_dir.rmdir()
        raise


def main() -> int:
    root = Path.cwd()
    try:
        check_new_installation(root)
        if not sys.stdin.isatty():
            raise ValueError("Ein interaktives Terminal verwenden (Docker: --rm -it).")
        print("Neue lokale Installation auf http://localhost:8080 einrichten.")
        print("Passwörter und E-Mail werden verdeckt eingegeben und nur lokal gespeichert.")
        league = input("Exakter Kickbase-Liganame: ").strip()
        email = getpass.getpass("Kickbase-E-Mail (verdeckt): ").strip()
        kickbase_password = getpass.getpass("Kickbase-Passwort: ")
        app_password = getpass.getpass("Eigenes Dashboard-Passwort: ")
        if app_password != getpass.getpass("Dashboard-Passwort wiederholen: "):
            raise ValueError("Die Dashboard-Passwörter stimmen nicht überein.")
        create_setup(
            root,
            league=league,
            email=email,
            kickbase_password=kickbase_password,
            app_password=app_password,
        )
    except (ValueError, OSError) as exc:
        # Values never form part of our validation errors.
        print(f"Setup abgebrochen: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("Setup abgebrochen.", file=sys.stderr)
        return 1
    print("Einrichtung gespeichert. Jetzt: docker compose up -d")
    print("HTTPS, eigener Proxy und IONOS sind optional: docs/self-hosting.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
