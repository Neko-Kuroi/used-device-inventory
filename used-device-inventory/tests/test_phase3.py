from conftest import DEVICE, complete_inspection
from device_app import CHECKLISTS
from device_app.db import get_db


def device_row(client, device_id=1):
    with client.application.app_context():
        return get_db().execute(
            "SELECT * FROM device WHERE id = ?", (device_id,)).fetchone()


def fill(client, results, device_id=1):
    return client.post(f"/devices/{device_id}/inspection", data=results)


def test_checklist_items_differ_by_kind(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    body = admin_client.get("/devices/1/inspection").get_data(as_text=True)
    assert "モバイル通信" in body
    assert "キーボード全キー" not in body

    pc = dict(DEVICE, kind="pc", serial_number="SN-PC9", imei="",
              network_restriction="")
    admin_client.post("/devices/new", data=pc)
    body = admin_client.get("/devices/2/inspection").get_data(as_text=True)
    assert "キーボード全キー" in body
    assert "モバイル通信" not in body


def test_partial_checklist_is_not_complete(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    fill(admin_client, {"result_power": "pass", "result_wifi": "pass"})
    assert device_row(admin_client)["inspection_done"] == 0
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "2/10 項目" in body


def test_all_pass_completes_and_opens_ready_gate(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    assert device_row(admin_client)["inspection_done"] == 1
    admin_client.post("/devices/1/status", data={"to_status": "inspecting"})
    resp = admin_client.post("/devices/1/status", data={"to_status": "ready"})
    assert device_row(admin_client)["status"] == "ready"


def test_one_fail_blocks_completion(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    data = {f"result_{k}": "pass" for k, _ in CHECKLISTS["smartphone"]}
    data["result_camera"] = "fail"
    fill(admin_client, data)
    assert device_row(admin_client)["inspection_done"] == 0
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "不合格: カメラ（前後）" in body


def test_na_counts_as_done(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    data = {f"result_{k}": "pass" for k, _ in CHECKLISTS["smartphone"]}
    data["result_biometrics"] = "na"  # no fingerprint/face hardware
    data["result_mobile"] = "na"
    fill(admin_client, data)
    assert device_row(admin_client)["inspection_done"] == 1


def test_results_record_who_and_when_and_history(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    with admin_client.application.app_context():
        db = get_db()
        rows = db.execute(
            "SELECT * FROM inspection WHERE device_id = 1").fetchall()
        hist = db.execute(
            "SELECT * FROM history WHERE device_id = 1"
            " AND action = 'inspection'").fetchall()
    assert len(rows) == len(CHECKLISTS["smartphone"])
    assert all(r["checked_by"] == 1 and r["checked_at"] for r in rows)
    assert len(hist) == 1 and "（完了）" in hist[0]["detail"]


def test_result_can_be_updated_and_cleared(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    assert device_row(admin_client)["inspection_done"] == 1
    data = {f"result_{k}": "pass" for k, _ in CHECKLISTS["smartphone"]}
    data["result_power"] = ""  # back to not-done
    fill(admin_client, data)
    assert device_row(admin_client)["inspection_done"] == 0


def test_edit_form_no_longer_resets_inspection_done(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    edited = dict(DEVICE, notes="編集しても検査完了は維持される")
    admin_client.post("/devices/1/edit", data=edited)
    assert device_row(admin_client)["inspection_done"] == 1


def test_erasure_recorder_is_saved(admin_client):
    data = dict(DEVICE, data_erasure_at="", data_erasure_method="")
    admin_client.post("/devices/new", data=data)
    assert device_row(admin_client)["data_erasure_by"] is None
    edited = dict(data, data_erasure_at="2026-11-05",
                  data_erasure_method="専用ソフトで消去")
    admin_client.post("/devices/1/edit", data=edited)
    row = device_row(admin_client)
    assert row["data_erasure_by"] == 1
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "実施者: admin" in body
