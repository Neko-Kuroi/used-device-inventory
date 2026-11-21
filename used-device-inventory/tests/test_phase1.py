from conftest import DEVICE, complete_inspection, login, make_admin
from device_app.db import get_db


def test_root_redirects_to_setup_when_no_users(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/setup" in resp.headers["Location"]


def test_setup_creates_admin_and_logs_in(client):
    resp = make_admin(client)
    assert resp.status_code == 302
    assert client.get("/").status_code == 200
    # Setup is closed once a user exists.
    assert client.get("/setup").status_code == 302


def test_setup_rejects_short_password(client):
    resp = client.post("/setup", data={"username": "a", "password": "short"})
    assert resp.status_code == 200
    assert client.get("/login").status_code == 302  # still no users -> setup


def test_login_required(client):
    make_admin(client)
    client.post("/logout")
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_wrong_password(client):
    make_admin(client)
    client.post("/logout")
    resp = login(client, "admin", "wrong-password")
    assert resp.status_code == 200
    assert client.get("/").status_code == 302


def test_create_device_and_detail(admin_client):
    resp = admin_client.post("/devices/new", data=DEVICE)
    assert resp.status_code == 302
    detail = admin_client.get("/devices/1")
    assert detail.status_code == 200
    body = detail.get_data(as_text=True)
    assert "iPhone 12" in body
    assert "123456789012345" in body  # admin sees the full IMEI
    assert "仕入済み" in body


def test_list_masks_imei(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    body = admin_client.get("/").get_data(as_text=True)
    assert "****2345" in body
    assert "123456789012345" not in body  # never the full IMEI in the list


def test_non_admin_cannot_see_full_imei(admin_client, app):
    admin_client.post("/devices/new", data=DEVICE)
    admin_client.post("/admin/users", data={
        "username": "staff", "password": "staff-pass-1"})
    admin_client.post("/logout")
    login(admin_client, "staff", "staff-pass-1")
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "****2345" in body
    assert "123456789012345" not in body
    # The admin page itself is forbidden for staff.
    assert admin_client.get("/admin/users").status_code == 403


def test_non_admin_edit_preserves_imei(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    admin_client.post("/admin/users", data={
        "username": "staff", "password": "staff-pass-1"})
    admin_client.post("/logout")
    login(admin_client, "staff", "staff-pass-1")
    edited = dict(DEVICE, imei="", model="iPhone 12 (編集済)")
    resp = admin_client.post("/devices/1/edit", data=edited)
    assert resp.status_code == 302
    with admin_client.application.app_context():
        row = get_db().execute(
            "SELECT * FROM device WHERE id = 1").fetchone()
    assert row["imei"] == "123456789012345"
    assert row["model"] == "iPhone 12 (編集済)"


def test_device_without_imei_gets_restriction_unknown(admin_client):
    data = dict(DEVICE, serial_number="SN0002", imei="",
                network_restriction="")
    resp = admin_client.post("/devices/new", data=data)
    assert resp.status_code == 302
    with admin_client.application.app_context():
        row = get_db().execute(
            "SELECT * FROM device WHERE serial_number = 'SN0002'").fetchone()
    assert row["imei"] is None
    assert row["network_restriction"] == "確認できず"


def test_invalid_imei_rejected(admin_client):
    data = dict(DEVICE, serial_number="SN0003", imei="12345")
    resp = admin_client.post("/devices/new", data=data)
    assert resp.status_code == 200  # form shown again with an error
    with admin_client.application.app_context():
        rows = get_db().execute("SELECT * FROM device").fetchall()
    assert rows == []


def test_duplicate_serial_rejected(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    resp = admin_client.post("/devices/new", data=DEVICE)
    assert resp.status_code == 200
    with admin_client.application.app_context():
        rows = get_db().execute("SELECT * FROM device").fetchall()
    assert len(rows) == 1


def test_search_and_filters(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    pc = dict(DEVICE, kind="pc", maker="Lenovo", model="ThinkPad X1",
              serial_number="SN-PC1", imei="", network_restriction="")
    admin_client.post("/devices/new", data=pc)
    # Status is no longer set from the form (Phase 2): walk it to ready.
    # The ready gate needs a completed checklist (Phase 3).
    complete_inspection(admin_client, device_id=2, kind="pc")
    admin_client.post("/devices/2/status", data={"to_status": "inspecting"})
    admin_client.post("/devices/2/status", data={"to_status": "ready"})
    body = admin_client.get("/?q=ThinkPad").get_data(as_text=True)
    assert "ThinkPad X1" in body and "iPhone 12" not in body
    body = admin_client.get("/?kind=smartphone").get_data(as_text=True)
    assert "iPhone 12" in body and "ThinkPad X1" not in body
    body = admin_client.get("/?status=ready").get_data(as_text=True)
    assert "ThinkPad X1" in body and "iPhone 12" not in body


def test_edit_records_history(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    edited = dict(DEVICE, planned_price="48000")
    admin_client.post("/devices/1/edit", data=edited)
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "48,000" in body
    assert "planned_price" in body  # change summary in history
    with admin_client.application.app_context():
        rows = get_db().execute(
            "SELECT * FROM history WHERE device_id = 1 ORDER BY id").fetchall()
    assert [r["action"] for r in rows] == ["create", "update"]


def test_missing_serial_rejected(admin_client):
    data = dict(DEVICE, serial_number="")
    resp = admin_client.post("/devices/new", data=data)
    assert resp.status_code == 200
    with admin_client.application.app_context():
        assert get_db().execute("SELECT * FROM device").fetchall() == []
