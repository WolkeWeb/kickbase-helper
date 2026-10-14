import os
import shutil
import stat
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from dotenv import dotenv_values

from scripts import setup

REPO = Path(__file__).resolve().parents[1]
SAMPLE = {
    "league": "Meine 'Liga' $2026 \\ #1",
    "email": "fixture@example.com",
    "kickbase_password": "fixture-password",
    "app_password": "fixture-app-password",
}


@pytest.fixture
def destination(tmp_path):
    shutil.copyfile(REPO / ".env.example", tmp_path / ".env.example")
    return tmp_path


def test_first_install_creates_valid_config_and_private_secrets(destination, capsys):
    setup.create_setup(destination, **SAMPLE)
    values = dotenv_values(destination / ".env")
    assert values["KICKBASE_LEAGUE_NAME"] == SAMPLE["league"]
    assert PasswordHasher().verify(values["APP_PASSWORD_HASH"], SAMPLE["app_password"])
    assert len(values["SESSION_SECRET"]) == len(values["POSTGRES_PASSWORD"]) == 64
    assert values["SESSION_SECRET"] != values["POSTGRES_PASSWORD"]
    assert values["COMPOSE_FILE"] == os.pathsep.join(("compose.yaml", "compose.local.yaml"))
    assert values["PUBLIC_URL"] == "http://localhost:8080"
    secrets_dir = destination / "secrets"
    assert (secrets_dir / "kickbase_email").read_text() == SAMPLE["email"]
    assert (secrets_dir / "kickbase_password").read_text() == SAMPLE["kickbase_password"]
    assert not (secrets_dir / "ionos_token").exists()
    for name in ("llm_api_key", "telegram_bot_token", "claude_bridge_token"):
        assert (secrets_dir / name).read_text() == ""
    for path in [destination / ".env", *(secrets_dir / name for name in setup.SECRET_NAMES)]:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(secrets_dir.stat().st_mode) == 0o700
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("existing", [".env", "secrets"])
def test_existing_installation_is_never_overwritten(destination, existing):
    path = destination / existing
    path.write_text("existing fixture")
    with pytest.raises(ValueError):
        setup.create_setup(destination, **SAMPLE)
    assert path.read_text() == "existing fixture"
    assert not (destination / ("secrets" if existing == ".env" else ".env")).exists()


def test_broken_symlink_cannot_redirect_a_secret_write(destination):
    target = destination / "missing"
    (destination / ".env").symlink_to(target)
    with pytest.raises(ValueError):
        setup.create_setup(destination, **SAMPLE)
    assert not target.exists()


@pytest.mark.parametrize("key", ["league", "email", "kickbase_password"])
def test_multiline_input_is_rejected_without_writing_or_echoing(destination, key):
    sample = SAMPLE | {key: "private-first-line\nprivate-second-line"}
    with pytest.raises(ValueError) as error:
        setup.create_setup(destination, **sample)
    assert "private-first-line" not in str(error.value)
    assert not (destination / ".env").exists()
    assert not (destination / "secrets").exists()


def test_partial_failure_rolls_back_only_new_files(destination, monkeypatch):
    write = setup.write_private

    def fail_on_config(path, value):
        if path.name == ".env":
            # Simulate a racing setup; do not remove this other writer's file.
            path.write_text("other setup")
        write(path, value)

    monkeypatch.setattr(setup, "write_private", fail_on_config)
    with pytest.raises(FileExistsError):
        setup.create_setup(destination, **SAMPLE)
    assert (destination / ".env").read_text() == "other setup"
    assert not (destination / "secrets").exists()


def test_noninteractive_terminal_is_rejected_before_asking_for_credentials(
    destination, monkeypatch, capsys
):
    monkeypatch.chdir(destination)
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: False)
    assert setup.main() == 1
    assert not (destination / ".env").exists()
    assert "interaktives Terminal" in capsys.readouterr().err
