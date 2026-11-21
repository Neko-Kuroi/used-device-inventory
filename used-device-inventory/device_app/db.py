"""Database layer: standard-library sqlite3 only.

Schema version is tracked with PRAGMA user_version (see SCHEMA_VERSION).
"""
import sqlite3

from flask import current_app, g

SCHEMA_VERSION = 4

# Columns added after v1, applied by migration to existing databases.
ADDED_COLUMNS = {
    "inspection_done": "INTEGER NOT NULL DEFAULT 0",
    "data_erasure_at": "TEXT NOT NULL DEFAULT ''",
    "data_erasure_method": "TEXT NOT NULL DEFAULT ''",
    "data_erasure_by": "INTEGER REFERENCES user(id)",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS device (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('smartphone', 'pc', 'tablet')),
    maker TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    serial_number TEXT NOT NULL UNIQUE,
    -- Smartphone-specific (IMEI may be NULL when it cannot be identified)
    imei TEXT,
    storage TEXT NOT NULL DEFAULT '',
    ram TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '',
    battery_health INTEGER,
    sim_status TEXT NOT NULL DEFAULT '',
    activation_lock TEXT NOT NULL DEFAULT '',
    network_restriction TEXT NOT NULL DEFAULT '',
    -- PC-specific
    cpu TEXT NOT NULL DEFAULT '',
    gpu TEXT NOT NULL DEFAULT '',
    os_name TEXT NOT NULL DEFAULT '',
    battery_cycles INTEGER,
    -- Common
    condition_rank TEXT NOT NULL DEFAULT 'B'
        CHECK (condition_rank IN ('S', 'A', 'B', 'C', 'junk')),
    status TEXT NOT NULL DEFAULT 'stocked'
        CHECK (status IN ('stocked', 'inspecting', 'maintaining',
                          'ready', 'reserved', 'sold', 'returned', 'disposed')),
    cosmetic_damage TEXT NOT NULL DEFAULT '',
    accessories TEXT NOT NULL DEFAULT '',
    inspection_done INTEGER NOT NULL DEFAULT 0,
    data_erasure_at TEXT NOT NULL DEFAULT '',
    data_erasure_method TEXT NOT NULL DEFAULT '',
    data_erasure_by INTEGER REFERENCES user(id),
    purchase_date TEXT NOT NULL DEFAULT '',
    purchase_source TEXT NOT NULL DEFAULT '',
    purchase_price INTEGER,
    planned_price INTEGER,
    sale_date TEXT NOT NULL DEFAULT '',
    sale_channel TEXT NOT NULL DEFAULT '',
    sale_price INTEGER,
    warranty_days INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES user(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES device(id),
    user_id INTEGER REFERENCES user(id),
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inspection (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES device(id),
    item_key TEXT NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('pass', 'fail', 'na')),
    note TEXT NOT NULL DEFAULT '',
    checked_by INTEGER REFERENCES user(id),
    checked_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (device_id, item_key)
);

CREATE TABLE IF NOT EXISTS expense (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES device(id),
    expense_date TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL
        CHECK (category IN ('parts', 'shipping', 'fee', 'other')),
    amount INTEGER NOT NULL CHECK (amount >= 0),
    note TEXT NOT NULL DEFAULT '',
    created_by INTEGER REFERENCES user(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_history_device ON history(device_id, id);
CREATE INDEX IF NOT EXISTS idx_expense_device ON expense(device_id, id);
CREATE INDEX IF NOT EXISTS idx_device_status ON device(status);
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    # Migration for databases created by an older schema version:
    # add any missing columns instead of recreating the tables.
    existing = {row["name"] for row in
                db.execute("PRAGMA table_info(device)").fetchall()}
    for name, decl in ADDED_COLUMNS.items():
        if name not in existing:
            db.execute(f"ALTER TABLE device ADD COLUMN {name} {decl}")
    db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    db.commit()


def user_count():
    return get_db().execute("SELECT COUNT(*) AS c FROM user").fetchone()["c"]
