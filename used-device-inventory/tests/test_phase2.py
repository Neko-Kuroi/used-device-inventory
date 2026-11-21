from conftest import DEVICE, complete_inspection
from device_app.db import get_db


def status_of(client, device_id=1):
    with client.application.app_context():
        row = get_db().execute(
            "SELECT status FROM device WHERE id = ?", (device_id,)).fetchone()
    return row["status"]


def move(client, to, reason="", device_id=1):
    return client.post(f"/devices/{device_id}/status",
                       data={"to_status": to, "reason": reason})


def test_new_device_always_starts_stocked(admin_client):
    data = dict(DEVICE, status="sold")  # form status must be ignored
    admin_client.post("/devices/new", data=data)
    assert status_of(admin_client) == "stocked"


def test_forward_flow(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    move(admin_client, "inspecting")
    assert status_of(admin_client) == "inspecting"
    move(admin_client, "maintaining")
    move(admin_client, "ready")
    move(admin_client, "reserved")
    move(admin_client, "sold")
    assert status_of(admin_client) == "sold"


def test_illegal_transition_rejected(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    move(admin_client, "sold")  # stocked -> sold is not allowed
    assert status_of(admin_client) == "stocked"
    move(admin_client, "returned")  # returned is only from sold
    assert status_of(admin_client) == "stocked"


def test_ready_gate_blocks_without_inspection_and_erasure(admin_client):
    data = dict(DEVICE, data_erasure_at="", data_erasure_method="")
    admin_client.post("/devices/new", data=data)
    move(admin_client, "inspecting")
    move(admin_client, "ready")
    assert status_of(admin_client) == "inspecting"
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "検査が完了していません" in body or "販売可能にできません" in body


def test_ready_gate_blocks_network_restriction_x(admin_client):
    data = dict(DEVICE, network_restriction="×")
    admin_client.post("/devices/new", data=data)
    complete_inspection(admin_client)  # so the x is the only blocker
    move(admin_client, "inspecting")
    move(admin_client, "ready")
    assert status_of(admin_client) == "inspecting"


def test_skip_requires_reason(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    move(admin_client, "inspecting")
    # inspecting -> stocked is backwards: no reason, no move
    move(admin_client, "stocked")
    assert status_of(admin_client) == "inspecting"
    move(admin_client, "stocked", reason="登録ミスのため差し戻し")
    assert status_of(admin_client) == "stocked"


def test_returned_only_from_sold_and_disposed_is_terminal(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    for to in ("inspecting", "ready", "sold"):
        move(admin_client, to)
    move(admin_client, "returned")  # needs a reason
    assert status_of(admin_client) == "sold"
    move(admin_client, "returned", reason="初期不良のため返品")
    assert status_of(admin_client) == "returned"
    move(admin_client, "disposed", reason="修理不能のため部品取り")
    assert status_of(admin_client) == "disposed"
    move(admin_client, "stocked", reason="戻したい")
    assert status_of(admin_client) == "disposed"


def test_status_changes_are_recorded_in_history(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    move(admin_client, "inspecting")
    move(admin_client, "ready")
    with admin_client.application.app_context():
        rows = get_db().execute(
            "SELECT * FROM history WHERE device_id = 1 AND action = 'status'"
            " ORDER BY id").fetchall()
    assert len(rows) == 2
    assert "仕入済み → 検査中" in rows[0]["detail"]
    assert "検査中 → 販売可能" in rows[1]["detail"]


def test_v1_database_is_migrated(tmp_path):
    """A database created with the v1 schema gains the v2 columns."""
    import sqlite3

    from device_app import create_app
    from device_app.db import ADDED_COLUMNS

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE user (id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE device (id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL, maker TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '', serial_number TEXT NOT NULL UNIQUE,
            imei TEXT, storage TEXT NOT NULL DEFAULT '',
            ram TEXT NOT NULL DEFAULT '', color TEXT NOT NULL DEFAULT '',
            battery_health INTEGER, sim_status TEXT NOT NULL DEFAULT '',
            activation_lock TEXT NOT NULL DEFAULT '',
            network_restriction TEXT NOT NULL DEFAULT '',
            cpu TEXT NOT NULL DEFAULT '', gpu TEXT NOT NULL DEFAULT '',
            os_name TEXT NOT NULL DEFAULT '', battery_cycles INTEGER,
            condition_rank TEXT NOT NULL DEFAULT 'B', status TEXT NOT NULL DEFAULT 'stocked',
            cosmetic_damage TEXT NOT NULL DEFAULT '', accessories TEXT NOT NULL DEFAULT '',
            purchase_date TEXT NOT NULL DEFAULT '', purchase_source TEXT NOT NULL DEFAULT '',
            purchase_price INTEGER, planned_price INTEGER,
            sale_date TEXT NOT NULL DEFAULT '', sale_channel TEXT NOT NULL DEFAULT '',
            sale_price INTEGER, warranty_days INTEGER, notes TEXT NOT NULL DEFAULT '',
            created_by INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL, user_id INTEGER,
            action TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT (datetime('now')));
        PRAGMA user_version = 1;
    """)
    conn.commit()
    conn.close()

    app = create_app({"TESTING": True, "SECRET_KEY": "t",
                      "DATABASE": str(db_path)})
    with app.app_context():
        db = get_db()
        cols = {r["name"] for r in db.execute("PRAGMA table_info(device)")}
        version = db.execute("PRAGMA user_version").fetchone()[0]
    assert set(ADDED_COLUMNS) <= cols
    assert version == 4
