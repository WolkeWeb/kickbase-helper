import pytest

from scripts.check_public_tree import private_path


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        "deploy/.env.production",
        "secrets/telegram_bot_token",
        "nested/secrets/token",
        "artifacts/capture.png",
        "docs/screenshots/old.png",
        "docs/images/team.png",
        "docs/images/demo/nested/team.png",
        "docs/images/demo/notes.txt",
        "backup.sql.gz",
        "private.PEM",
        "backup.bundle",
        "nested/node_modules/file.js",
    ],
)
def test_private_artifacts_are_blocked_even_when_force_added(name):
    assert private_path(name)


@pytest.mark.parametrize(
    "name",
    [
        ".env.example",
        "docs/images/banner.png",
        "docs/images/demo/app-heute.png",
        "app/secret_files.py",
        "app/web/static/icons/icon-192.png",
        "tests/fixtures/kickbase/user_me.json",
        "licenses/chartjs-MIT.txt",
    ],
)
def test_public_code_and_anonymised_fixtures_remain_allowed(name):
    assert not private_path(name)
