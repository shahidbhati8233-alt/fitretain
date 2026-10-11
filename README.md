# FitRetain — Gym Member Retention SaaS

FitRetain is a lightweight gym-management web app built with Python's standard library, SQLite, and vanilla HTML/CSS/JavaScript.

## Project layout

- `server.py` — repository-root entry point for hosting providers.
- `fitretain/server.py` — application server and API.
- `fitretain/index.html` — responsive web UI.
- `fitretain/tests/` — QR check-in integration tests.
- `fitretain/fitretain.db` — existing database file; do not replace it without first verifying and backing up its contents.

## Run locally

Requires Python 3.10+.

```bash
python server.py
```

Open http://127.0.0.1:8080. The app's default database is beside `fitretain/server.py`. Set `PORT` or `FITRETAIN_DB` to override the defaults.

## Render configuration

For a Render **Web Service** using the repository root as its Root Directory:

- Runtime: Python
- Build command: `pip install -r requirements.txt`
- Start command: `python server.py`
- Health check path: `/api/me`

The root entry point delegates to the existing server inside `fitretain/`; it does not replace the application or migrate/delete the database. If the service's Root Directory is already set to `fitretain`, use `python -m py_compile server.py` as the build command and `python server.py` as the start command instead.

**Important data note:** Render's local filesystem may be ephemeral. Before using real gym records, configure a persistent disk and set `FITRETAIN_DB` to a path on that disk, or migrate to a managed database with a tested backup/restore plan. Do not point a new deployment at a fresh database if the existing one contains records that must be preserved.

## Tests

```bash
python -m unittest discover -s fitretain/tests -v
```

## Current limitations

This is an MVP, not a fully production-hardened service. Sessions are held in memory and are lost on restart; use one server process until session storage is made persistent. Before exposing real member data publicly, add durable session storage, HTTPS-only secure cookies, account recovery, audit logging, backups, migrations, and broader automated security/integration tests. The app uses no external Python dependencies.
