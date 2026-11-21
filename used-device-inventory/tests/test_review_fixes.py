"""Fixes for the post-release review findings.

Gate integrity (hybrid policy): while a unit is 販売可能/取り置き the
facts the gate rests on cannot be edited; if an inspection result
breaks the gate, the unit goes back to 検査中 automatically. A return
resets inspection and erasure. Plus the small findings: login next,
smartphone-only 確認できず, reason for 整備中→検査中, erasure recorder
on creation, LIKE escaping, CSV formula injection."""
from conftest import DEVICE, complete_inspection
from device_app.db import get_db


def row_of(client, device_id=1):
    with client.application.app_context():
        return dict(get_db().execute(
            "SELECT * FROM device WHERE id = ?", (device_id,)).fetchone())


def move(client, to, reason="", device_id=1):
    return client.post(f"/devices/{device_id}/status",
                       data={"to_status": to, "reason": reason})


def make_ready(client):
    client.post("/devices/new", data=DEVICE)
    complete_inspection(client)
    move(client, "inspecting")
    move(client, "ready")
    assert row_of(client)["status"] == "ready"


def test_edit_cannot_clear_erasure_while_ready(admin_client):
    make_ready(admin_client)
    admin_client.post("/devices/1/edit",
                      data=dict(DEVICE, data_erasure_at="",
                                data_erasure_method=""))
    row = row_of(admin_client)
    assert row["status"] == "ready"
    assert row["data_erasure_at"] == "2026-11-02"


def test_edit_cannot_set_restriction_x_while_ready(admin_client):
    make_ready(admin_client)
    admin_client.post("/devices/1/edit",
                      data=dict(DEVICE, network_restriction="×"))
    assert row_of(admin_client)["network_restriction"] == "○"


def test_edit_cannot_change_kind_while_ready(admin_client):
    make_ready(admin_client)
    admin_client.post("/devices/1/edit", data=dict(DEVICE, kind="pc"))
    row = row_of(admin_client)
    assert row["kind"] == "smartphone"
    assert row["inspection_done"] == 1


def test_locked_edit_keeps_the_typed_input(admin_client):
    make_ready(admin_client)
    resp = admin_client.post("/devices/1/edit", data=dict(
        DEVICE, data_erasure_at="", data_erasure_method="",
        notes="長く書いた備考テキスト"))
    body = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "変更できません" in body
    assert "長く書いた備考テキスト" in body  # input is not lost
    row = row_of(admin_client)  # and nothing was saved
    assert row["notes"] == "テスト個体"
    assert row["data_erasure_at"] == "2026-11-02"


def test_edit_other_fields_still_allowed_while_ready(admin_client):
    make_ready(admin_client)
    admin_client.post("/devices/1/edit",
                      data=dict(DEVICE, notes="値下げ対象"))
    assert row_of(admin_client)["notes"] == "値下げ対象"


def test_inspection_fail_demotes_ready_to_inspecting(admin_client):
    from device_app import CHECKLISTS
    make_ready(admin_client)
    data = {f"result_{k}": "pass" for k, _ in CHECKLISTS["smartphone"]}
    data["result_camera"] = "fail"
    admin_client.post("/devices/1/inspection", data=data)
    row = row_of(admin_client)
    assert row["status"] == "inspecting"
    assert row["inspection_done"] == 0
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "自動で戻しました" in body


def test_kind_change_resets_inspection(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    assert row_of(admin_client)["inspection_done"] == 1
    admin_client.post("/devices/1/edit", data=dict(DEVICE, kind="pc"))
    row = row_of(admin_client)
    assert row["kind"] == "pc"
    assert row["inspection_done"] == 0
    with admin_client.application.app_context():
        n = get_db().execute(
            "SELECT COUNT(*) AS c FROM inspection WHERE device_id = 1"
        ).fetchone()["c"]
    assert n == 0


def test_return_resets_inspection_and_erasure(admin_client):
    make_ready(admin_client)
    move(admin_client, "sold")
    move(admin_client, "returned", reason="初期不良")
    row = row_of(admin_client)
    assert row["status"] == "returned"
    assert row["inspection_done"] == 0
    assert row["data_erasure_at"] == ""
    assert row["data_erasure_by"] is None
    # And it cannot go back on sale without redoing both.
    move(admin_client, "maintaining")
    move(admin_client, "ready")
    assert row_of(admin_client)["status"] == "maintaining"


def test_maintaining_back_to_inspecting_needs_reason(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    move(admin_client, "inspecting")
    move(admin_client, "maintaining")
    move(admin_client, "inspecting")  # no reason
    assert row_of(admin_client)["status"] == "maintaining"
    move(admin_client, "inspecting", reason="再検査のため")
    assert row_of(admin_client)["status"] == "inspecting"


def test_login_next_external_url_is_ignored(admin_client):
    admin_client.post("/logout")
    resp = admin_client.post(
        "/login?next=http://evil.example/",
        data={"username": "admin", "password": "admin-pass-1"})
    assert resp.status_code == 302
    assert "evil.example" not in resp.headers["Location"]


def test_login_next_internal_path_still_works(admin_client):
    admin_client.post("/logout")
    resp = admin_client.post(
        "/login?next=/devices/1",
        data={"username": "admin", "password": "admin-pass-1"})
    assert resp.headers["Location"].endswith("/devices/1")


def test_uncheckable_restriction_is_smartphone_only(admin_client):
    admin_client.post("/devices/new", data=dict(
        DEVICE, kind="pc", serial_number="PC1", imei="",
        network_restriction=""))
    assert row_of(admin_client, 1)["network_restriction"] == ""
    admin_client.post("/devices/new", data=dict(
        DEVICE, serial_number="SP2", imei="", network_restriction=""))
    assert row_of(admin_client, 2)["network_restriction"] == "確認できず"


def test_erasure_recorder_is_set_on_creation(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    assert row_of(admin_client)["data_erasure_by"] == 1


def test_search_percent_is_literal(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    admin_client.post("/devices/new",
                      data=dict(DEVICE, serial_number="SN0002"))
    body = admin_client.get("/?q=%25").get_data(as_text=True)
    assert "SN0001" not in body and "SN0002" not in body
    body = admin_client.get("/?q=SN000").get_data(as_text=True)
    assert "SN0001" in body and "SN0002" in body


def test_csv_formula_injection_is_neutralized(admin_client):
    admin_client.post("/devices/new",
                      data=dict(DEVICE, notes="=2+5"))
    body = admin_client.get("/export.csv").get_data(as_text=True)
    assert "'=2+5" in body
