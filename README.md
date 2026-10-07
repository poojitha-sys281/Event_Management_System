# Event Management System

A full-stack event management app: **Flask REST API + MongoDB**, with full CRUD
for events, attendees, and scheduling — matching the project on the resume:

> Designed and implemented a NoSQL database schema (MongoDB) with full CRUD
> operations, optimizing queries for efficient attendee and scheduling data
> retrieval.

## What it does

- **Events** — create, list (searchable/sortable), update, delete
- **Attendees** — register attendees to an event, list/filter by event or
  check-in status, update check-in status, remove
- **Schedule (Sessions)** — add agenda items (talks/sessions) to an event with
  speaker/time/venue, list sorted by time, update, delete
- Deleting an event cascades to remove its attendees and schedule (keeps data
  consistent)
- A simple browser dashboard (tabs: Events / Attendees / Schedule) talks to the
  API so you can demo it without Postman

## Where the "optimized queries" part comes in

`backend/db.py` creates these indexes on startup:

- `events`: index on `date`, `name`
- `attendees`: index on `event_id`; **unique compound index** on
  `(event_id, email)` so the same person can't double-register for one event
- `sessions`: index on `event_id`; compound index on `(event_id, start_time)`
  so the schedule view sorts fast without a full collection scan

These map directly to the two access patterns the resume calls out: "attendee"
lookups and "scheduling data" lookups, both scoped by event.

## Project structure

```
event-management-system/
├── backend/
│   ├── app.py            # Flask app, all REST routes
│   ├── db.py             # MongoDB connection, schema/indexes, demo seed data
│   └── requirements.txt
├── frontend/
│   ├── index.html        # Dashboard UI
│   └── app.js             # Talks to the REST API
└── README.md
```

## Run it

**No MongoDB installed? No problem.** By default the app uses `mongomock`, an
in-memory MongoDB-compatible database, so it runs immediately with zero setup.
The database starts completely empty — no demo data is inserted — so
everything you see is whatever you create through the app. Note: with
`mongomock`, data resets each time you restart the server (switch to a real
MongoDB via `MONGO_URI` below if you want it to persist).

```bash
cd backend
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5000** in your browser.

### Using a real MongoDB instance

Set `MONGO_URI` before starting the app (works with local MongoDB or a free
[MongoDB Atlas](https://www.mongodb.com/atlas) cluster):

```bash
export MONGO_URI="mongodb://localhost:27017"
# or: export MONGO_URI="mongodb+srv://user:pass@cluster.mongodb.net"
python app.py
```

`GET /health` tells you which mode you're in:

```json
{ "status": "ok", "db": "mongomock (in-memory)" }
```

## Roles

Two separate account types. Sign up / log in by choosing one:

- **Event Creator (organizer)** - creates events with a **mandatory attendee
  limit**, edits/deletes only their own events, sees live registered / seats-left /
  checked-in counts, checks people in, removes attendees, manages the schedule.
- **Attendee** - browses all events, registers (until the event is full),
  cancels, and views schedules. Cannot create or change events (enforced by
  the API, not just hidden in the UI).

The same email can hold one account per role. Seats are claimed atomically, so
two people can never take the last seat.

## API reference (all routes except register/login need `Authorization: Bearer <token>`)

| Method | Path | Who | Description |
|---|---|---|---|
| POST | `/api/auth/register` | public | `{name, email, password, role}` role = organizer/attendee |
| POST | `/api/auth/verify` | public | `{token}` confirms email from the emailed link |
| POST | `/api/auth/resend-verification` | public | `{email, role}` sends a new confirmation link |
| POST | `/api/auth/login` | public | `{email, password, role}` (throttled after repeated failures) |
| POST | `/api/auth/forgot` | public | `{email, role}` emails a 1-hour reset link |
| POST | `/api/auth/reset` | public | `{token, password}` sets a new password (single-use link) |
| GET | `/api/auth/me` | any | current user |
| GET | `/api/events` | any | paginated (`?page=&page_size=&name=`): organizer = own events; attendee = all events (with seats_left) |
| GET | `/api/events/options` | organizer | id/name/date of all own events (for dropdowns) |
| POST | `/api/events` | organizer | `{name, date, capacity>=1, location?, description?}` |
| PUT | `/api/events/<id>` | owner | update (limit can't drop below current registrations) |
| DELETE | `/api/events/<id>` | owner | delete + cascade registrations/schedule |
| GET | `/api/events/<id>/attendees` | owner | paginated registrations (`?page=&q=`) + counts |
| PATCH | `/api/attendees/<id>` | owner | `{checked_in: true/false}` |
| DELETE | `/api/attendees/<id>` | owner | remove attendee, frees a seat |
| POST / DELETE | `/api/events/<id>/register` | attendee | register / cancel (409 when full) |
| GET | `/api/my/registrations` | attendee | events I'm registered for |
| GET | `/api/sessions?event_id=` | any | schedule of an event |
| POST/PUT/DELETE | `/api/sessions[/<id>]` | owner | manage schedule |

## Possible extensions (if you want to go further for an interview)

- Auth (JWT) so each organizer only sees their own events
- Pagination on `/api/attendees` for large events
- Email confirmation on registration
- Deploy the API on AWS (Elastic Beanstalk / Lambda) + MongoDB Atlas — ties
  back to the "Basics of AWS" line on the resume
