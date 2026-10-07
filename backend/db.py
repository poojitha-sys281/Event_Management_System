"""
MongoDB connection layer for the Event Management System.

Uses a real MongoDB instance if MONGO_URI is set in the environment,
otherwise falls back to an in-memory mongomock database so the whole
project runs out of the box with zero setup.

No demo/seed data is inserted — the database starts empty and is
populated entirely by what users create through the app.
"""

import os

MONGO_URI = os.environ.get("MONGO_URI")
DB_NAME = os.environ.get("MONGO_DB_NAME", "event_management")

if MONGO_URI:
    from pymongo import MongoClient
    client = MongoClient(MONGO_URI)
    USING_MOCK = False
else:
    import mongomock
    client = mongomock.MongoClient()
    USING_MOCK = True

db = client[DB_NAME]

users = db["users"]
events = db["events"]
attendees = db["attendees"]
sessions = db["sessions"]  # scheduling / agenda items for an event
rate_limits = db["rate_limits"]  # login / reset throttling counters (shared by all workers)


def create_indexes():
    """
    Indexes chosen to optimize the two access patterns called out on the
    resume: attendee lookups and schedule lookups, both scoped by event.
    """
    # one account per (email, role): the same person may hold separate
    # organizer and attendee accounts, but they never mix.
    users.create_index([("email", 1), ("role", 1)], unique=True)

    events.create_index("date")
    events.create_index([("owner_id", 1), ("date", 1)])  # organizer's paginated list
    events.create_index("name")

    attendees.create_index("event_id")
    attendees.create_index("user_id")
    attendees.create_index([("event_id", 1), ("name", 1)])  # paginated attendee list
    # a user can register for a given event only once
    attendees.create_index([("event_id", 1), ("user_id", 1)], unique=True)

    sessions.create_index("event_id")
    sessions.create_index([("event_id", 1), ("start_time", 1)])

    # let MongoDB clean up expired throttle counters automatically
    try:
        rate_limits.create_index("expire_at", expireAfterSeconds=0)
    except Exception:
        pass


create_indexes()
