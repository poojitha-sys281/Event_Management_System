"""API tests for the Event Management System (edge cases, roles, capacity, throttling)."""
import db
from conftest import auth, signup, make_event


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------

def test_health_reports_ok(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


# --------------------------------------------------------------------------
# sign-up validation and accounts
# --------------------------------------------------------------------------

def test_register_rejects_short_password(client):
    res = client.post("/api/auth/register",
                      json={"name": "A", "email": "a@b.com", "password": "short", "role": "attendee"})
    assert res.status_code == 400


def test_register_rejects_bad_email(client):
    res = client.post("/api/auth/register",
                      json={"name": "A", "email": "not-an-email", "password": "password123", "role": "attendee"})
    assert res.status_code == 400


def test_register_rejects_unknown_role(client):
    res = client.post("/api/auth/register",
                      json={"name": "A", "email": "a@b.com", "password": "password123", "role": "admin"})
    assert res.status_code == 400


def test_duplicate_email_same_role_is_409(client):
    signup(client, "dup@example.com", "attendee")
    res = client.post("/api/auth/register",
                      json={"name": "B", "email": "dup@example.com", "password": "password123", "role": "attendee"})
    assert res.status_code == 409


def test_same_email_can_hold_both_roles(client):
    signup(client, "both@example.com", "attendee")
    signup(client, "both@example.com", "organizer")  # asserts 201


def test_email_is_normalised_to_lowercase(client):
    signup(client, "MiXeD@Example.com", "attendee")
    res = client.post("/api/auth/login",
                      json={"email": "mixed@example.com", "password": "password123", "role": "attendee"})
    assert res.status_code == 200


def test_password_is_stored_hashed_and_never_returned(client):
    res = client.post("/api/auth/register",
                      json={"name": "A", "email": "h@b.com", "password": "password123", "role": "attendee"})
    assert "password" not in str(res.get_json()["user"]).lower()
    stored = db.users.find_one({"email": "h@b.com"})
    assert stored["password_hash"] != "password123"


# --------------------------------------------------------------------------
# login, tokens, throttling
# --------------------------------------------------------------------------

def test_login_with_wrong_password_is_401(client, attendee):
    res = client.post("/api/auth/login",
                      json={"email": "att@example.com", "password": "wrongpass1", "role": "attendee"})
    assert res.status_code == 401


def test_login_with_wrong_role_gives_helpful_403(client, attendee):
    res = client.post("/api/auth/login",
                      json={"email": "att@example.com", "password": "password123", "role": "organizer"})
    assert res.status_code == 403
    assert "attendee" in res.get_json()["error"]


def test_five_wrong_passwords_lock_the_account(client, attendee):
    bad = {"email": "att@example.com", "password": "wrongpass1", "role": "attendee"}
    for _ in range(5):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    assert client.post("/api/auth/login", json=bad).status_code == 429
    # even the correct password is refused while locked
    good = {"email": "att@example.com", "password": "password123", "role": "attendee"}
    assert client.post("/api/auth/login", json=good).status_code == 429


def test_protected_route_needs_a_token(client):
    assert client.get("/api/events").status_code == 401


def test_tampered_token_is_rejected(client):
    res = client.get("/api/events", headers=auth("not.a.real.token"))
    assert res.status_code == 401


# --------------------------------------------------------------------------
# role-based access
# --------------------------------------------------------------------------

def test_attendee_cannot_create_event(client, attendee):
    res = client.post("/api/events", headers=auth(attendee),
                      json={"name": "X", "date": "2026-12-01", "capacity": 5})
    assert res.status_code == 403


def test_organizer_cannot_register_for_events(client, organizer):
    ev = make_event(client, organizer)
    res = client.post(f"/api/events/{ev['_id']}/register", headers=auth(organizer))
    assert res.status_code == 403


def test_attendee_cannot_add_schedule_sessions(client, organizer, attendee):
    ev = make_event(client, organizer)
    res = client.post("/api/sessions", headers=auth(attendee),
                      json={"event_id": ev["_id"], "title": "Keynote", "start_time": "10:00"})
    assert res.status_code == 403


# --------------------------------------------------------------------------
# events: validation and ownership
# --------------------------------------------------------------------------

def test_create_event_requires_name_and_date(client, organizer):
    res = client.post("/api/events", headers=auth(organizer), json={"capacity": 5})
    assert res.status_code == 400


def test_create_event_rejects_zero_or_junk_capacity(client, organizer):
    for bad in (0, -3, "abc", None):
        res = client.post("/api/events", headers=auth(organizer),
                          json={"name": "X", "date": "2026-12-01", "capacity": bad})
        assert res.status_code == 400, bad


def test_organizer_only_sees_their_own_events(client, organizer):
    other = signup(client, "org2@example.com", "organizer")
    make_event(client, organizer, name="Mine")
    make_event(client, other, name="Theirs")
    items = client.get("/api/events", headers=auth(organizer)).get_json()["items"]
    assert [e["name"] for e in items] == ["Mine"]


def test_organizer_cannot_edit_or_delete_someone_elses_event(client, organizer):
    other = signup(client, "org2@example.com", "organizer")
    ev = make_event(client, other)
    assert client.patch(f"/api/events/{ev['_id']}", headers=auth(organizer),
                        json={"name": "Hijacked"}).status_code == 403
    assert client.delete(f"/api/events/{ev['_id']}", headers=auth(organizer)).status_code == 403


def test_invalid_event_id_is_a_clean_400_not_a_crash(client, organizer):
    res = client.get("/api/events/not-an-objectid", headers=auth(organizer))
    assert res.status_code == 400


def test_unknown_event_is_404(client, organizer):
    res = client.get("/api/events/" + "a" * 24, headers=auth(organizer))
    assert res.status_code == 404


# --------------------------------------------------------------------------
# registration and capacity
# --------------------------------------------------------------------------

def test_registering_uses_up_a_seat(client, organizer, attendee):
    ev = make_event(client, organizer, capacity=2)
    res = client.post(f"/api/events/{ev['_id']}/register", headers=auth(attendee))
    assert res.status_code == 201
    body = res.get_json()
    assert body["registered_count"] == 1
    assert body["seats_left"] == 1
    assert body["is_registered"] is True


def test_cannot_register_twice_for_the_same_event(client, organizer, attendee):
    ev = make_event(client, organizer)
    client.post(f"/api/events/{ev['_id']}/register", headers=auth(attendee))
    res = client.post(f"/api/events/{ev['_id']}/register", headers=auth(attendee))
    assert res.status_code == 409
    # and the seat count was not inflated by the failed attempt
    assert db.events.find_one({"name": "Hack Night"})["registered_count"] == 1


def test_full_event_rejects_new_registrations(client, organizer, attendee):
    ev = make_event(client, organizer, capacity=1)
    second = signup(client, "att2@example.com", "attendee")
    assert client.post(f"/api/events/{ev['_id']}/register", headers=auth(attendee)).status_code == 201
    res = client.post(f"/api/events/{ev['_id']}/register", headers=auth(second))
    assert res.status_code == 409
    assert "full" in res.get_json()["error"].lower()
    # the rejected person must not be left behind as an attendee
    assert db.attendees.count_documents({"user_id": {"$exists": True}}) == 1
    assert db.events.find_one({"name": "Hack Night"})["registered_count"] == 1


def test_cancelling_frees_the_seat_for_someone_else(client, organizer, attendee):
    ev = make_event(client, organizer, capacity=1)
    second = signup(client, "att2@example.com", "attendee")
    client.post(f"/api/events/{ev['_id']}/register", headers=auth(attendee))
    assert client.delete(f"/api/events/{ev['_id']}/register", headers=auth(attendee)).status_code == 200
    assert client.post(f"/api/events/{ev['_id']}/register", headers=auth(second)).status_code == 201


def test_cancelling_when_not_registered_is_404(client, organizer, attendee):
    ev = make_event(client, organizer)
    res = client.delete(f"/api/events/{ev['_id']}/register", headers=auth(attendee))
    assert res.status_code == 404


def test_cannot_lower_capacity_below_registered_count(client, organizer, attendee):
    ev = make_event(client, organizer, capacity=3)
    client.post(f"/api/events/{ev['_id']}/register", headers=auth(attendee))
    other = signup(client, "att2@example.com", "attendee")
    client.post(f"/api/events/{ev['_id']}/register", headers=auth(other))
    res = client.patch(f"/api/events/{ev['_id']}", headers=auth(organizer), json={"capacity": 1})
    assert res.status_code == 400
    ok = client.patch(f"/api/events/{ev['_id']}", headers=auth(organizer), json={"capacity": 2})
    assert ok.status_code == 200


def test_my_registrations_lists_only_my_events(client, organizer, attendee):
    a = make_event(client, organizer, name="A")
    make_event(client, organizer, name="B")
    client.post(f"/api/events/{a['_id']}/register", headers=auth(attendee))
    mine = client.get("/api/my/registrations", headers=auth(attendee)).get_json()
    assert [e["name"] for e in mine] == ["A"]


# --------------------------------------------------------------------------
# organizer tools: check-in, removal, cascade delete, schedule
# --------------------------------------------------------------------------

def test_organizer_can_check_in_and_remove_an_attendee(client, organizer, attendee):
    ev = make_event(client, organizer, capacity=2)
    client.post(f"/api/events/{ev['_id']}/register", headers=auth(attendee))
    listing = client.get(f"/api/events/{ev['_id']}/attendees", headers=auth(organizer)).get_json()
    reg_id = listing["attendees"][0]["_id"]

    res = client.patch(f"/api/attendees/{reg_id}", headers=auth(organizer), json={"checked_in": True})
    assert res.get_json()["checked_in"] is True

    assert client.delete(f"/api/attendees/{reg_id}", headers=auth(organizer)).status_code == 200
    assert db.events.find_one({"name": "Hack Night"})["registered_count"] == 0


def test_deleting_an_event_removes_its_attendees_and_sessions(client, organizer, attendee):
    ev = make_event(client, organizer)
    client.post(f"/api/events/{ev['_id']}/register", headers=auth(attendee))
    client.post("/api/sessions", headers=auth(organizer),
                json={"event_id": ev["_id"], "title": "Keynote", "start_time": "10:00"})
    assert client.delete(f"/api/events/{ev['_id']}", headers=auth(organizer)).status_code == 200
    assert db.attendees.count_documents({}) == 0
    assert db.sessions.count_documents({}) == 0


def test_session_needs_title_and_start_time(client, organizer):
    ev = make_event(client, organizer)
    res = client.post("/api/sessions", headers=auth(organizer), json={"event_id": ev["_id"]})
    assert res.status_code == 400


# --------------------------------------------------------------------------
# pagination
# --------------------------------------------------------------------------

def test_pagination_clamps_page_size_and_page_number(client, organizer):
    for i in range(12):
        make_event(client, organizer, name=f"E{i:02d}", date=f"2026-12-{i + 1:02d}")
    page1 = client.get("/api/events?page_size=5", headers=auth(organizer)).get_json()
    assert len(page1["items"]) == 5 and page1["pages"] == 3 and page1["total"] == 12
    # a page past the end falls back to the last page instead of returning nothing
    last = client.get("/api/events?page=99&page_size=5", headers=auth(organizer)).get_json()
    assert last["page"] == 3 and len(last["items"]) == 2
    # junk values do not crash
    assert client.get("/api/events?page=abc&page_size=xyz", headers=auth(organizer)).status_code == 200
    # an oversized page_size is capped at 50
    assert client.get("/api/events?page_size=9999", headers=auth(organizer)).get_json()["page_size"] == 50
