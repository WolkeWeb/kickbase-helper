"""Startet die App mit den anonymisierten Test-Fixtures statt echter Kickbase-Daten.

Gedacht für die README-Screenshots (`scripts/readme_screenshots.py`): keine Zugangsdaten, kein
Netzwerkzugriff auf Kickbase, keine privaten Liga- oder Kontodaten.

Usage:
    uv run python -m scripts.demo_server [--port 8099]
Login-Passwort: demo
"""

import argparse
import sys
import tempfile
from pathlib import Path

import uvicorn
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.auth import hash_password  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db.models import Base  # noqa: E402
from app.main import create_app  # noqa: E402
from scripts.demo_data import seed_demo_data  # noqa: E402
from tests.fakes import (  # noqa: E402
    FakeOpenLigaDb,
    add_recommendation_endpoints,
    fake_with_team_data,
    fixture,
    install_fake_kickbase,
    make_oldb_client,
)

DEMO_PASSWORD = "demo"


def build_demo_app(workdir: Path):
    settings = Settings(
        _env_file=None,
        app_password_hash=hash_password(DEMO_PASSWORD),
        session_secret="d" * 40,
        session_https_only=False,
        database_url=f"sqlite+pysqlite:///{workdir / 'demo.db'}",
        secrets_dir=workdir / "secrets",
        kickbase_league_name="Testliga",
    )
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    seed_demo_data(engine)
    engine.dispose()
    app = create_app(settings)
    fake = fake_with_team_data()
    add_recommendation_endpoints(fake)
    player = fixture("competition_player")["i"]
    fake.on("GET", f"/v4/competitions/1/players/{player}", fixture("competition_player"))
    fake.on(
        "GET",
        f"/v4/competitions/1/players/{player}/marketValue/92",
        fixture("competition_player_market_value_92"),
    )
    fake.on(
        "GET",
        f"/v4/competitions/1/players/{player}/performance",
        fixture("competition_player_performance"),
    )
    install_fake_kickbase(app, fake)
    app.state.openligadb = make_oldb_client(FakeOpenLigaDb())
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        uvicorn.run(
            build_demo_app(Path(tmp)), host="127.0.0.1", port=args.port, log_level="warning"
        )


if __name__ == "__main__":
    main()
