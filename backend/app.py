"""
Event Management System — Flask REST API + MongoDB, with two separate roles.

  organizer  -> creates events (with a fixed attendee limit), manages the
                schedule, tracks registrations, checks attendees in.
  attendee   -> browses events, registers / cancels, sees their own schedule.
                Cannot create or modify events.

Auth: email + password + role. The server returns a signed token that the
browser sends back as `Authorization: Bearer <token>`.
"""

from flask import Flask, request, jsonify, send_from_directory, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from pymongo.errors import DuplicateKeyError
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.middleware.proxy_fix import ProxyFix
import json
import math
import os
import re
import time
import urllib.request

from db import users, events, attendees, sessions, rate_limits, USING_MOCK

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-secret-change-me")
TOKEN_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
RESET_MAX_AGE = 60 * 60           # reset links last 1 hour
VERIFY_MAX_AGE = 60 * 60 * 48     # verification links last 48 hours
MIN_PASSWORD = 8
BREVO_API_KEY = os.environ.get("BREVO_API_KEY")   # optional: enables reset emails
MAIL_FROM = os.environ.get("MAIL_FROM")           # sender verified in Brevo
APP_URL = (os.environ.get("APP_URL") or "").rstrip("/")  # e.g. https://my-app.onrender.com
# Email verification is enforced only when email can actually be sent;
# otherwise nobody could ever receive the link and sign-up would be impossible.
EMAIL_ENABLED = bool(BREVO_API_KEY and MAIL_FROM)
ROLES = ("organizer", "attendee")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = Flask(__name__, static_folder="../frontend", static_url_path="")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # correct client IP / https behind Render
CORS(app)
serializer = URLSafeTimedSerializer(SECRET_KEY, salt="ems-auth")
reset_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="ems-reset")
verify_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="ems-verify")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def err(message, status=400):
    return jsonify({"error": message}), status


def oid(id_str):
    try:
        return ObjectId(id_str)
    except (InvalidId, TypeError):
        return None


def public(doc):
    if doc is None:
        return None
    doc = dict(doc)
    doc["_id"] = str(doc["_id"])
    doc.pop("password_hash", None)
    return doc


def body():
    return request.get_json(silent=True) or {}


def clean(value):
    return value.strip() if isinstance(value, str) else value


# --- throttling (stored in MongoDB so it works across multiple workers) ---

def rl_blocked(key, limit, window):
    """Seconds left on the block if `key` hit `limit` within `window`, else 0."""
    d = rate_limits.find_one({"_id": key})
    if not d:
        return 0
    remaining = d["start"] + window - time.time()
    return int(remaining) + 1 if remaining > 0 and d["count"] >= limit else 0


def rl_hit(key, window):
    now = time.time()
    d = rate_limits.find_one({"_id": key})
    if not d or d["start"] + window <= now:
        rate_limits.replace_one(
            {"_id": key},
            {"_id": key, "count": 1, "start": now,
             "expire_at": datetime.utcnow() + timedelta(seconds=window)},
            upsert=True)
    else:
        rate_limits.update_one({"_id": key}, {"$inc": {"count": 1}})


def rl_clear(key):
    rate_limits.delete_one({"_id": key})


def too_many(seconds):
    mins = max(1, math.ceil(seconds / 60))
    return err(f"Too many attempts. Please try again in {mins} minute{'s' if mins != 1 else ''}.", 429)


def client_ip():
    return request.remote_addr or "unknown"


# --- email ---

def _send_email(user, subject, text, link):
    """Send via Brevo's HTTPS API. Without email config the link is only logged."""
    if not EMAIL_ENABLED:
        app.logger.warning("Email not configured; link for %s: %s", user["email"], link)
        return False
    try:
        req = urllib.request.Request(
            "https://api.brevo.com/v3/smtp/email",
            data=json.dumps({"sender": {"name": "Event Management System", "email": MAIL_FROM},
                             "to": [{"email": user["email"], "name": user["name"]}],
                             "subject": subject, "textContent": text}).encode(),
            headers={"api-key": BREVO_API_KEY, "Content-Type": "application/json",
                     "accept": "application/json"},
            method="POST")
        urllib.request.urlopen(req, timeout=8).read()
        return True
    except Exception as ex:  # never let email trouble break the request
        app.logger.error("Could not send email: %s", ex)
        return False


def deliver_reset(user, link):
    return _send_email(
        user, "Reset your Event Management System password",
        f"Hi {user['name']},\n\nSomeone asked to reset the password for your {user['role']} "
        f"account. Use this link within 1 hour:\n\n{link}\n\n"
        f"If this wasn't you, ignore this email - your password is unchanged.", link)


def deliver_verification(user, link):
    return _send_email(
        user, "Confirm your email for Event Management System",
        f"Hi {user['name']},\n\nWelcome! Please confirm your email address to activate your "
        f"{user['role']} account. This link works for 48 hours:\n\n{link}\n\n"
        f"If you didn't create this account, you can ignore this email.", link)


def send_verification(user):
    token = verify_serializer.dumps({"uid": str(user["_id"]), "email": user["email"]})
    base = APP_URL or request.host_url.rstrip("/")
    return deliver_verification(user, f"{base}/#verify={token}")


# --- pagination ---

def paginate(coll, query, sort, default_size=10, max_size=50):
    try:
        page = max(int(request.args.get("page", 1)), 1)
    except ValueError:
        page = 1
    try:
        size = min(max(int(request.args.get("page_size", default_size)), 1), max_size)
    except ValueError:
        size = default_size
    total = coll.count_documents(query)
    pages = max(math.ceil(total / size), 1)
    page = min(page, pages)
    docs = list(coll.find(query).sort(sort).skip((page - 1) * size).limit(size))
    return docs, {"page": page, "pages": pages, "total": total, "page_size": size}


def auth_required(role=None):
    """Require a valid token; optionally require a specific role."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            if not header.startswith("Bearer "):
                return err("Please log in", 401)
            try:
                data = serializer.loads(header[7:], max_age=TOKEN_MAX_AGE)
            except SignatureExpired:
                return err("Session expired, please log in again", 401)
            except BadSignature:
                return err("Invalid session, please log in again", 401)
            user = users.find_one({"_id": oid(data.get("uid"))})
            if not user:
                return err("Account no longer exists", 401)
            if role and user["role"] != role:
                who = "event organizers" if role == "organizer" else "attendees"
                return err(f"This action is only available to {who}", 403)
            g.user = user
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def event_view(ev, viewer=None):
    """Event JSON with seat counts (and registration flag for attendees)."""
    out = public(ev)
    registered = int(ev.get("registered_count", 0))
    capacity = int(ev.get("capacity", 0))
    out["registered_count"] = registered
    out["seats_left"] = max(capacity - registered, 0)
    out["is_full"] = registered >= capacity
    if viewer and viewer["role"] == "attendee":
        out["is_registered"] = attendees.count_documents(
            {"event_id": str(ev["_id"]), "user_id": str(viewer["_id"])}
        ) > 0
    return out


def owned_event_or_error(event_id):
    """Return (event, None) if the logged-in organizer owns it, else (None, response)."""
    o = oid(event_id)
    if not o:
        return None, err("Invalid event id")
    ev = events.find_one({"_id": o})
    if not ev:
        return None, err("Event not found", 404)
    if ev.get("owner_id") != str(g.user["_id"]):
        return None, err("You can only manage events you created", 403)
    return ev, None


def parse_capacity(value):
    try:
        cap = int(value)
    except (TypeError, ValueError):
        return None
    return cap if cap >= 1 else None


# ---------------------------------------------------------------------------
# Static frontend / health
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok", "db": "mongomock (in-memory)" if USING_MOCK else "mongodb"})


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

VERIFY_SENT = ("Account created. We sent a confirmation link to your email - "
               "open it to activate your account, then log in.")


@app.route("/api/auth/register", methods=["POST"])
def register():
    d = body()
    ip_key = f"register:{client_ip()}"
    wait = rl_blocked(ip_key, 100, 3600)  # generous: a venue may share one IP
    if wait:
        return too_many(wait)
    rl_hit(ip_key, 3600)
    name, email = clean(d.get("name")), (clean(d.get("email")) or "").lower()
    password, role = d.get("password") or "", d.get("role")
    if not name:
        return err("Name is required")
    if not EMAIL_RE.match(email):
        return err("Enter a valid email address")
    if len(password) < MIN_PASSWORD:
        return err(f"Password must be at least {MIN_PASSWORD} characters")
    if role not in ROLES:
        return err("Choose a role: organizer or attendee")
    doc = {
        "name": name,
        "email": email,
        "role": role,
        "password_hash": generate_password_hash(password),
        "verified": not EMAIL_ENABLED,   # no email service -> can't verify, so don't block
        "created_at": datetime.utcnow().isoformat(),
    }
    for _ in range(2):
        try:
            res = users.insert_one(doc)
            break
        except DuplicateKeyError:
            existing = users.find_one({"email": email, "role": role})
            if existing and existing.get("verified") is False:
                age = datetime.utcnow() - datetime.fromisoformat(existing["created_at"])
                if age > timedelta(seconds=VERIFY_MAX_AGE):
                    # never confirmed within 48h: free the email so its real owner can sign up
                    users.delete_one({"_id": existing["_id"]})
                    continue
                send_verification(existing)  # re-send; don't touch the pending account
                return jsonify({"verification_required": True, "message": VERIFY_SENT}), 200
            return err(f"An {role} account with this email already exists", 409)
    doc["_id"] = res.inserted_id
    if not doc["verified"]:
        send_verification(doc)
        return jsonify({"verification_required": True, "message": VERIFY_SENT}), 201
    token = serializer.dumps({"uid": str(res.inserted_id)})
    return jsonify({"token": token, "user": public(doc)}), 201


@app.route("/api/auth/verify", methods=["POST"])
def verify_email():
    try:
        data = verify_serializer.loads(body().get("token") or "", max_age=VERIFY_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return err("This confirmation link is invalid or has expired. Log in to get a new one.")
    user = users.find_one({"_id": oid(data.get("uid"))})
    if not user or user["email"] != data.get("email"):
        return err("This confirmation link is invalid or has expired. Log in to get a new one.")
    users.update_one({"_id": user["_id"]}, {"$set": {"verified": True}})
    return jsonify({"message": "Email confirmed! You can log in now.", "role": user["role"]})


@app.route("/api/auth/resend-verification", methods=["POST"])
def resend_verification():
    d = body()
    email, role = (clean(d.get("email")) or "").lower(), d.get("role")
    if role not in ROLES or not EMAIL_RE.match(email):
        return err("Enter your email and choose your account type")
    keys = [(f"verify:{role}:{email}", 3), (f"verifyip:{client_ip()}", 10)]  # per hour
    wait = max((rl_blocked(k, lim, 3600) for k, lim in keys), default=0)
    if wait:
        return too_many(wait)
    for k, _ in keys:
        rl_hit(k, 3600)
    user = users.find_one({"email": email, "role": role})
    if user and user.get("verified") is False:
        send_verification(user)
    return jsonify({"message": "If that account is waiting for confirmation, a new link has been sent."})


@app.route("/api/auth/login", methods=["POST"])
def login():
    d = body()
    email, role = (clean(d.get("email")) or "").lower(), d.get("role")
    if role not in ROLES:
        return err("Choose how you want to log in: organizer or attendee")
    # 5 wrong passwords per account / 40 per IP -> locked for 15 minutes
    WINDOW = 15 * 60
    acct_key, ip_key = f"login:{role}:{email}", f"loginip:{client_ip()}"
    wait = rl_blocked(acct_key, 5, WINDOW) or rl_blocked(ip_key, 40, WINDOW)
    if wait:
        return too_many(wait)
    user = users.find_one({"email": email, "role": role})
    if not user or not check_password_hash(user["password_hash"], d.get("password") or ""):
        rl_hit(acct_key, WINDOW)
        rl_hit(ip_key, WINDOW)
        # if the email exists under the other role, say so (helps with the common mix-up)
        other = users.find_one({"email": email, "role": {"$ne": role}})
        if other and check_password_hash(other["password_hash"], d.get("password") or ""):
            return err(f"This account is registered as an {other['role']}. Switch the login type.", 403)
        return err("Incorrect email or password", 401)
    rl_clear(acct_key)
    if EMAIL_ENABLED and user.get("verified", True) is False:   # older accounts have no flag = verified
        return jsonify({"error": "Please confirm your email first - check your inbox for the link.",
                        "code": "unverified"}), 403
    token = serializer.dumps({"uid": str(user["_id"])})
    return jsonify({"token": token, "user": public(user)})


@app.route("/api/auth/forgot", methods=["POST"])
def forgot_password():
    d = body()
    email, role = (clean(d.get("email")) or "").lower(), d.get("role")
    if role not in ROLES or not EMAIL_RE.match(email):
        return err("Enter your email and choose your account type")
    keys = [(f"forgot:{role}:{email}", 3), (f"forgotip:{client_ip()}", 10)]  # per hour
    wait = max((rl_blocked(k, lim, 3600) for k, lim in keys), default=0)
    if wait:
        return too_many(wait)
    for k, _ in keys:
        rl_hit(k, 3600)
    user = users.find_one({"email": email, "role": role})
    if user:
        token = reset_serializer.dumps({"uid": str(user["_id"]), "ph": user["password_hash"][-16:]})
        base = APP_URL or request.host_url.rstrip("/")
        deliver_reset(user, f"{base}/#reset={token}")
    # same answer whether or not the account exists (doesn't reveal who is registered)
    return jsonify({"message": "If that account exists, a reset link has been sent to the email address."})


@app.route("/api/auth/reset", methods=["POST"])
def reset_password():
    d = body()
    password = d.get("password") or ""
    if len(password) < MIN_PASSWORD:
        return err(f"Password must be at least {MIN_PASSWORD} characters")
    try:
        data = reset_serializer.loads(d.get("token") or "", max_age=RESET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return err("This reset link is invalid or has expired. Request a new one.")
    user = users.find_one({"_id": oid(data.get("uid"))})
    # the link embeds part of the old password hash, so it works only once
    if not user or user["password_hash"][-16:] != data.get("ph"):
        return err("This reset link is invalid or has expired. Request a new one.")
    users.update_one({"_id": user["_id"]}, {"$set": {"password_hash": generate_password_hash(password), "verified": True}})
    rl_clear(f"login:{user['role']}:{user['email']}")
    return jsonify({"message": "Password updated. You can log in now."})


@app.route("/api/auth/me")
@auth_required()
def me():
    return jsonify(public(g.user))


# ---------------------------------------------------------------------------
# EVENTS
# ---------------------------------------------------------------------------

@app.route("/api/events", methods=["GET"])
@auth_required()
def list_events():
    """Organizers see only their own events. Attendees see all events."""
    query = {}
    if g.user["role"] == "organizer":
        query["owner_id"] = str(g.user["_id"])
    name = request.args.get("name")
    if name:
        query["name"] = {"$regex": re.escape(name), "$options": "i"}
    docs, meta = paginate(events, query, [("date", 1), ("_id", 1)], default_size=10)
    return jsonify({"items": [event_view(e, g.user) for e in docs], **meta})


@app.route("/api/events/options", methods=["GET"])
@auth_required("organizer")
def event_options():
    """Light list of ALL the organizer's events (for the dropdowns)."""
    cur = events.find({"owner_id": str(g.user["_id"])}, {"name": 1, "date": 1}).sort("date", 1).limit(1000)
    return jsonify([{"_id": str(e["_id"]), "name": e["name"], "date": e.get("date", "")} for e in cur])


@app.route("/api/events/<event_id>", methods=["GET"])
@auth_required()
def get_event(event_id):
    o = oid(event_id)
    if not o:
        return err("Invalid event id")
    ev = events.find_one({"_id": o})
    if not ev:
        return err("Event not found", 404)
    if g.user["role"] == "organizer" and ev.get("owner_id") != str(g.user["_id"]):
        return err("Event not found", 404)
    return jsonify(event_view(ev, g.user))


@app.route("/api/events", methods=["POST"])
@auth_required("organizer")
def create_event():
    d = body()
    name, date = clean(d.get("name")), clean(d.get("date"))
    if not name or not date:
        return err("Missing required field(s): name, date")
    capacity = parse_capacity(d.get("capacity"))
    if capacity is None:
        return err("Set an attendee limit of at least 1")
    doc = {
        "name": name,
        "description": clean(d.get("description")) or "",
        "date": date,
        "location": clean(d.get("location")) or "",
        "capacity": capacity,
        "registered_count": 0,
        "owner_id": str(g.user["_id"]),
        "owner_name": g.user["name"],
        "created_at": datetime.utcnow().isoformat(),
    }
    res = events.insert_one(doc)
    doc["_id"] = res.inserted_id
    return jsonify(event_view(doc)), 201


@app.route("/api/events/<event_id>", methods=["PUT", "PATCH"])
@auth_required("organizer")
def update_event(event_id):
    ev, e = owned_event_or_error(event_id)
    if e:
        return e
    d = body()
    updates = {k: clean(d[k]) for k in ("name", "description", "date", "location") if k in d}
    if "capacity" in d:
        cap = parse_capacity(d["capacity"])
        if cap is None:
            return err("Attendee limit must be at least 1")
        if cap < int(ev.get("registered_count", 0)):
            return err(f"{ev['registered_count']} people are already registered; "
                       f"the limit can't be lower than that")
        updates["capacity"] = cap
    if not updates:
        return err("No valid fields to update")
    if "name" in updates and not updates["name"]:
        return err("Name can't be empty")
    events.update_one({"_id": ev["_id"]}, {"$set": updates})
    return jsonify(event_view(events.find_one({"_id": ev["_id"]})))


@app.route("/api/events/<event_id>", methods=["DELETE"])
@auth_required("organizer")
def delete_event(event_id):
    ev, e = owned_event_or_error(event_id)
    if e:
        return e
    events.delete_one({"_id": ev["_id"]})
    attendees.delete_many({"event_id": event_id})
    sessions.delete_many({"event_id": event_id})
    return jsonify({"message": "Event and related registrations/schedule deleted"})


# ---------------------------------------------------------------------------
# ORGANIZER: attendee list for an event they own
# ---------------------------------------------------------------------------

@app.route("/api/events/<event_id>/attendees", methods=["GET"])
@auth_required("organizer")
def event_attendees(event_id):
    ev, e = owned_event_or_error(event_id)
    if e:
        return e
    query = {"event_id": event_id}
    checked_in = request.args.get("checked_in")
    if checked_in is not None:
        query["checked_in"] = checked_in.lower() == "true"
    q = (request.args.get("q") or "").strip()
    if q:
        rx = {"$regex": re.escape(q), "$options": "i"}
        query["$or"] = [{"name": rx}, {"email": rx}]
    docs, meta = paginate(attendees, query, [("name", 1), ("_id", 1)], default_size=25, max_size=100)
    return jsonify({
        "event": event_view(ev),
        "checked_in_count": attendees.count_documents({"event_id": event_id, "checked_in": True}),
        "attendees": [public(a) for a in docs],
        **meta,
    })


@app.route("/api/attendees/<attendee_id>", methods=["PATCH", "PUT"])
@auth_required("organizer")
def check_in(attendee_id):
    a = attendees.find_one({"_id": oid(attendee_id)}) if oid(attendee_id) else None
    if not a:
        return err("Registration not found", 404)
    _, e = owned_event_or_error(a["event_id"])
    if e:
        return e
    if "checked_in" not in body():
        return err("Nothing to update")
    attendees.update_one({"_id": a["_id"]}, {"$set": {"checked_in": bool(body()["checked_in"])}})
    return jsonify(public(attendees.find_one({"_id": a["_id"]})))


@app.route("/api/attendees/<attendee_id>", methods=["DELETE"])
@auth_required("organizer")
def remove_attendee(attendee_id):
    """Organizer removes someone from their own event (frees a seat)."""
    a = attendees.find_one({"_id": oid(attendee_id)}) if oid(attendee_id) else None
    if not a:
        return err("Registration not found", 404)
    _, e = owned_event_or_error(a["event_id"])
    if e:
        return e
    release_seat(a)
    return jsonify({"message": "Attendee removed"})


def release_seat(reg):
    attendees.delete_one({"_id": reg["_id"]})
    events.update_one({"_id": oid(reg["event_id"]), "registered_count": {"$gt": 0}},
                      {"$inc": {"registered_count": -1}})


# ---------------------------------------------------------------------------
# ATTENDEE: register / cancel / my registrations
# ---------------------------------------------------------------------------

@app.route("/api/events/<event_id>/register", methods=["POST"])
@auth_required("attendee")
def register_for_event(event_id):
    o = oid(event_id)
    ev = events.find_one({"_id": o}) if o else None
    if not ev:
        return err("Event not found", 404)

    reg = {
        "event_id": event_id,
        "user_id": str(g.user["_id"]),
        "name": g.user["name"],
        "email": g.user["email"],
        "checked_in": False,
        "registered_at": datetime.utcnow().isoformat(),
    }
    # 1) unique index (event_id, user_id) blocks double registration
    try:
        res = attendees.insert_one(reg)
    except DuplicateKeyError:
        return err("You are already registered for this event", 409)

    # 2) atomically claim a seat: only succeeds while registered_count < capacity,
    #    so two people can never take the last seat at the same time.
    claimed = events.update_one(
        {"_id": o, "registered_count": {"$lt": int(ev.get("capacity", 0))}},
        {"$inc": {"registered_count": 1}},
    )
    if claimed.modified_count == 0:
        attendees.delete_one({"_id": res.inserted_id})
        return err("Sorry, this event is full", 409)

    return jsonify(event_view(events.find_one({"_id": o}), g.user)), 201


@app.route("/api/events/<event_id>/register", methods=["DELETE"])
@auth_required("attendee")
def cancel_registration(event_id):
    reg = attendees.find_one({"event_id": event_id, "user_id": str(g.user["_id"])})
    if not reg:
        return err("You are not registered for this event", 404)
    release_seat(reg)
    return jsonify({"message": "Registration cancelled"})


@app.route("/api/my/registrations", methods=["GET"])
@auth_required("attendee")
def my_registrations():
    regs = list(attendees.find({"user_id": str(g.user["_id"])}))
    ids = [oid(r["event_id"]) for r in regs if oid(r["event_id"])]
    evs = {str(e["_id"]): e for e in events.find({"_id": {"$in": ids}})}
    out = []
    for r in regs:
        ev = evs.get(r["event_id"])
        if ev:
            item = event_view(ev, g.user)
            item["checked_in"] = r.get("checked_in", False)
            out.append(item)
    out.sort(key=lambda x: x.get("date", ""))
    return jsonify(out)


# ---------------------------------------------------------------------------
# SESSIONS (schedule) — everyone can read, only the owning organizer can write
# ---------------------------------------------------------------------------

@app.route("/api/sessions", methods=["GET"])
@auth_required()
def list_sessions():
    event_id = request.args.get("event_id")
    if not event_id:
        return err("event_id is required")
    return jsonify([public(s) for s in sessions.find({"event_id": event_id}).sort("start_time", 1)])


@app.route("/api/sessions", methods=["POST"])
@auth_required("organizer")
def create_session():
    d = body()
    title, start = clean(d.get("title")), clean(d.get("start_time"))
    if not d.get("event_id") or not title or not start:
        return err("Missing required field(s): event_id, title, start_time")
    _, e = owned_event_or_error(d["event_id"])
    if e:
        return e
    doc = {
        "event_id": d["event_id"],
        "title": title,
        "speaker": clean(d.get("speaker")) or "",
        "start_time": start,
        "end_time": clean(d.get("end_time")) or "",
        "venue": clean(d.get("venue")) or "",
    }
    res = sessions.insert_one(doc)
    doc["_id"] = res.inserted_id
    return jsonify(public(doc)), 201


def owned_session_or_error(session_id):
    s = sessions.find_one({"_id": oid(session_id)}) if oid(session_id) else None
    if not s:
        return None, err("Session not found", 404)
    _, e = owned_event_or_error(s["event_id"])
    return (None, e) if e else (s, None)


@app.route("/api/sessions/<session_id>", methods=["PUT", "PATCH"])
@auth_required("organizer")
def update_session(session_id):
    s, e = owned_session_or_error(session_id)
    if e:
        return e
    d = body()
    updates = {k: clean(d[k]) for k in ("title", "speaker", "start_time", "end_time", "venue") if k in d}
    if not updates:
        return err("No valid fields to update")
    sessions.update_one({"_id": s["_id"]}, {"$set": updates})
    return jsonify(public(sessions.find_one({"_id": s["_id"]})))


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
@auth_required("organizer")
def delete_session(session_id):
    s, e = owned_session_or_error(session_id)
    if e:
        return e
    sessions.delete_one({"_id": s["_id"]})
    return jsonify({"message": "Session deleted"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")
