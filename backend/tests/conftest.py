"""
Test setup.

The app falls back to an in-memory mongomock database when MONGO_URI is not set,
so the tests need no real database. We also make sure email is switched off so
sign-ups are active immediately and no real email is ever sent.
"""
import os
import sys

# must happen BEFORE the app is imported
os.environ.pop("MONGO_URI", None)
os.environ.pop("BREVO_API_KEY", None)
os.environ.pop("MAIL_FROM", None)
os.environ["SECRET_KEY"] = "test-secret"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import app as app_module
import db


@pytest.fixture(autouse=True)
def clean_db():
    """Start every test with empty collections (indexes are kept)."""
    for coll in (db.users, db.events, db.attendees, db.sessions, db.rate_limits):
        coll.delete_many({})
    yield


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    return app_module.app.test_client()


# ---- small helpers used by the tests -------------------------------------

def signup(client, email, role, name="Test User", password="password123"):
    res = client.post("/api/auth/register",
                      json={"name": name, "email": email, "password": password, "role": role})
    assert res.status_code == 201, res.get_json()
    return res.get_json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def make_event(client, token, name="Hack Night", capacity=2, date="2026-12-01"):
    res = client.post("/api/events", headers=auth(token),
                      json={"name": name, "date": date, "capacity": capacity})
    assert res.status_code == 201, res.get_json()
    return res.get_json()


@pytest.fixture
def organizer(client):
    return signup(client, "org@example.com", "organizer", "Olivia Organizer")


@pytest.fixture
def attendee(client):
    return signup(client, "att@example.com", "attendee", "Adam Attendee")
