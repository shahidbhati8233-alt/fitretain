# FitRetain — Gym Member Retention SaaS

A lightweight, responsive multi-gym web application using Python's standard library, SQLite, and vanilla HTML/CSS/JS. No external packages or payment gateway keys required.

## Run locally

Requires Python 3.10+.

```bash
python3 server.py
```

Open http://127.0.0.1:8080. The database is created automatically as `fitretain.db` next to `server.py`. Set `PORT` or `FITRETAIN_DB` to change the defaults.

## Included

- Gym account registration/sign-in with PBKDF2 password hashing and an HttpOnly, SameSite session cookie.
- Per-gym data ownership enforced in database queries for members, attendance, and payments.
- Member creation, search/status filters, renewal, deletion with confirmation.
- Attendance check-ins, with duplicate same-day check-ins prevented by a database constraint.
- Manual payment recording in INR and revenue metrics based on stored payment rows.
- At-risk flags from real expiry/attendance data and WhatsApp click-to-chat drafts (the user must review and send).
- Responsive charts rendered with inline SVG; no framework-specific chart library.
- Input validation, empty states, and visible error/success messages.

## Important deployment notes

This is a runnable MVP, not a fully production-hardened public deployment. Sessions are in-memory and expire after 12 hours, so restarting the server signs everyone out. Serve behind HTTPS in production. Add persistent session storage, rate limiting, account recovery, audit logging, backups, migrations, automated integration tests, and a WSGI/ASGI or production HTTP server before exposing it publicly. Do not run multiple worker processes with this in-memory session store. No seeded members or payments are inserted; all records start empty, so there is no unlabeled demo data.
