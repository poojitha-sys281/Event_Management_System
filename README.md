# Event Management System

[![Tests](https://github.com/poojitha-sys281/Event_Management_System/actions/workflows/tests.yml/badge.svg)](https://github.com/poojitha-sys281/Event_Management_System/actions/workflows/tests.yml)

A full-stack event management app: a **Flask REST API** backed by **MongoDB**, with a plain
HTML/CSS/JavaScript dashboard. Organizers create events with a seat limit and manage the
schedule; attendees browse events and register until they are full.

**Live demo:** https://event-management-system-qg8o.onrender.com/
(hosted on Render's free tier, so the first load after a quiet period can take 30–50 seconds)

## What it does

- **Events**: create, list (searchable, paginated), update, delete. Every event has a mandatory attendee limit.
- **Registration**: attendees register and cancel; organizers see live registered / seats-left / checked-in counts, check people in and remove them.
- **Schedule (sessions)**: add talks to an event with speaker, time and venue; listed in time order.
- **Accounts**: separate organizer and attendee roles, email confirmation, password reset.
- Deleting an event also deletes its registrations and schedule, so no orphaned data is left behind.

## Roles

Sign up and log in as one of two account types:

- **Event Creator (organizer):** creates events, edits or deletes only their own events, manages attendees and the schedule.
- **Attendee:** browses all events, registers (until full), cancels, views schedules. Cannot create or change events.

These rules are enforced by the API, not just hidden in the UI. The same email can hold one account per role.

## Design decisions

**Seats can't be oversold.** Registration first inserts the attendee record, then claims a seat with a
conditional update (`registered_count < capacity`). MongoDB applies that check and the increment as one
atomic operation, so two people cannot take the last seat. If the update fails, the attendee record is
removed again and the user gets a 409 "event is full". Trade-off: if the server crashed between the two
steps, a registration could exist without its seat being counted. A MongoDB transaction would close that gap.

**No double registration.** A unique compound index on `(event_id, user_id)` makes the database itself
reject a second registration, so this holds even if two requests arrive at the same time.

**Authentication.** Passwords are hashed (Werkzeug). Login returns a signed, expiring token
(`itsdangerous`, 7-day lifetime) sent as `Authorization: Bearer <token>`. A decorator checks the token
and the required role on each route. Email-confirmation and password-reset links use separate salts and
shorter lifetimes (48 hours and 1 hour), so one kind of token cannot be used as another.

**Throttling that works across workers.** Failed logins are limited to 5 per account and 40 per IP
before a 15-minute lock. The counters live in MongoDB with a TTL index (not in process memory), so the
limit still applies when gunicorn runs several workers.

**Indexes match the queries.** `users (email, role)` unique; `events (owner_id, date)` for an organizer's
paginated list; `attendees (event_id, user_id)` unique and `(event_id, name)` for the attendee list;
`sessions (event_id, start_time)` for the schedule. Lists are paginated with a capped page size.

**Works with no setup.** If `MONGO_URI` is not set, the app uses `mongomock` (an in-memory MongoDB), so
it runs and the tests pass without installing a database. `GET /health` reports which one is in use.

## Project structure

```
event-management-system/
├── backend/
│   ├── app.py              # Flask app and all REST routes
│   ├── db.py               # MongoDB connection and indexes
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   ├── pytest.ini
│   └── tests/
│       ├── conftest.py     # fixtures: in-memory DB, helpers
│       └── test_api.py     # 33 API tests
├── frontend/
│   ├── index.html          # dashboard UI
│   └── app.js              # calls the REST API
├── .github/workflows/tests.yml   # runs the tests on every push
├── render.yaml
└── README.md
```

## Run it locally

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000. With no `MONGO_URI`, data lives in memory and resets when the server restarts.

To use a real MongoDB (local, or a free [MongoDB Atlas](https://www.mongodb.com/atlas) cluster), set the
connection string first:

```bash
export MONGO_URI="mongodb+srv://USER:PASSWORD@cluster.mongodb.net"   # PowerShell: $env:MONGO_URI="..."
python app.py
```

## Run the tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

The 33 tests cover sign-up validation, login lockout, role-based access, ownership checks, registration
edge cases (duplicate, event full, cancel frees the seat, capacity can't drop below registrations),
cascade delete and pagination limits. They use the in-memory database, so no setup is needed. The same
tests run automatically on GitHub for every push.

## API reference

All routes except register, verify, resend, login, forgot and reset need `Authorization: Bearer <token>`.

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/api/auth/register` | public | `{name, email, password, role}`; role is organizer or attendee |
| POST | `/api/auth/verify` | public | `{token}` confirms the email from the emailed link |
| POST | `/api/auth/resend-verification` | public | `{email, role}` sends a new confirmation link |
| POST | `/api/auth/login` | public | `{email, password, role}`; throttled after repeated failures |
| POST | `/api/auth/forgot` | public | `{email, role}` emails a 1-hour reset link |
| POST | `/api/auth/reset` | public | `{token, password}` sets a new password |
| GET | `/api/auth/me` | any | current user |
| GET | `/api/events` | any | paginated (`?page=&page_size=&name=`); organizer sees own events, attendee sees all |
| GET | `/api/events/options` | organizer | id, name and date of all own events (for dropdowns) |
| GET | `/api/events/<id>` | any | one event |
| POST | `/api/events` | organizer | `{name, date, capacity>=1, location?, description?}` |
| PUT / PATCH | `/api/events/<id>` | owner | update; the limit can't drop below current registrations |
| DELETE | `/api/events/<id>` | owner | delete, cascading to registrations and schedule |
| GET | `/api/events/<id>/attendees` | owner | paginated registrations (`?page=&q=&checked_in=`) with counts |
| PATCH / PUT | `/api/attendees/<id>` | owner | `{checked_in: true/false}` |
| DELETE | `/api/attendees/<id>` | owner | remove an attendee and free the seat |
| POST / DELETE | `/api/events/<id>/register` | attendee | register / cancel (409 when full or already registered) |
| GET | `/api/my/registrations` | attendee | events I'm registered for |
| GET | `/api/sessions?event_id=` | any | schedule of an event |
| POST / PUT / PATCH / DELETE | `/api/sessions[/<id>]` | owner | manage the schedule |
| GET | `/health` | public | status and which database is in use |

## Deployment

Deployed on Render (`render.yaml`, gunicorn) with MongoDB Atlas. Required environment variable:
`MONGO_URI`. Optional: `SECRET_KEY`, and `BREVO_API_KEY` / `MAIL_FROM` to send real emails (without them,
accounts are active immediately and email links are only logged). See `DEPLOY.md` for the steps.

## Ideas for next steps

- Run a concurrency test against a real MongoDB to prove the last-seat guarantee under load
- Use a MongoDB transaction for register-and-claim-seat
- Waitlist when an event is full
- QR-code check-in at the venue
