from conftest import DEVICE
from device_app.db import get_db


def add_expense(client, amount, category="parts", note="", device_id=1,
                date="2026-11-03"):
    return client.post(f"/devices/{device_id}/expenses", data={
        "expense_date": date, "category": category,
        "amount": str(amount), "note": note})


def expenses(client, device_id=1):
    with client.application.app_context():
        return get_db().execute(
            "SELECT * FROM expense WHERE device_id = ? ORDER BY id",
            (device_id,)).fetchall()


def test_add_and_delete_expense(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    add_expense(admin_client, 3000, "parts", "バッテリー交換")
    add_expense(admin_client, 800, "shipping")
    rows = expenses(admin_client)
    assert [(r["category"], r["amount"]) for r in rows] == [
        ("parts", 3000), ("shipping", 800)]
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "3,800 円" in body  # total

    admin_client.post(f"/devices/1/expenses/{rows[0]['id']}/delete")
    rows = expenses(admin_client)
    assert len(rows) == 1 and rows[0]["amount"] == 800


def test_invalid_expense_rejected(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    add_expense(admin_client, -500)
    add_expense(admin_client, "abc")
    admin_client.post("/devices/1/expenses", data={
        "expense_date": "", "category": "unknown", "amount": "100",
        "note": ""})
    assert expenses(admin_client) == []


def test_expense_history_is_recorded(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    add_expense(admin_client, 1200, "fee", "フリマ手数料")
    with admin_client.application.app_context():
        rows = get_db().execute(
            "SELECT * FROM history WHERE device_id = 1"
            " AND action = 'expense'").fetchall()
    assert len(rows) == 1
    assert "販売手数料" in rows[0]["detail"] and "1,200円" in rows[0]["detail"]


def test_gross_profit_on_detail_and_list(admin_client):
    sold = dict(DEVICE, sale_price="45000", sale_date="2026-11-10",
                  sale_channel="店頭")
    admin_client.post("/devices/new", data=sold)
    add_expense(admin_client, 3000, "parts")
    add_expense(admin_client, 2000, "fee")
    # 45000 - 30000 (purchase) - 5000 (expenses) = 10000
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "10,000 円" in body
    # planned: 45000 planned - 30000 - 5000 = 10000 as well in this fixture
    body = admin_client.get("/").get_data(as_text=True)
    assert "10,000 円" in body


def test_profit_is_blank_until_sold(admin_client):
    admin_client.post("/devices/new", data=DEVICE)  # no sale_price
    add_expense(admin_client, 1000)
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "―（実売価格か仕入れ価格が未記録）" in body


def test_csv_export(admin_client):
    sold = dict(DEVICE, sale_price="45000")
    admin_client.post("/devices/new", data=sold)
    add_expense(admin_client, 3000, "parts")
    resp = admin_client.get("/export.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.content_type
    raw = resp.get_data()
    assert raw.startswith(b"\xef\xbb\xbf")  # BOM for Excel
    text = raw.decode("utf-8-sig")
    assert "シリアル番号" in text and "粗利" in text
    assert "SN0001" in text
    assert "2345" in text  # IMEI last 4
    assert "123456789012345" not in text  # full IMEI never exported
    assert "12000" in text  # 45000 - 30000 - 3000


def test_csv_respects_filters(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    pc = dict(DEVICE, kind="pc", serial_number="SN-PC7", imei="",
              network_restriction="", maker="Lenovo", model="ThinkPad X1")
    admin_client.post("/devices/new", data=pc)
    text = admin_client.get("/export.csv?kind=pc").get_data().decode("utf-8-sig")
    assert "ThinkPad X1" in text
    assert "iPhone 12" not in text


def test_csv_requires_login(client):
    from conftest import make_admin
    make_admin(client)
    client.post("/logout")
    resp = client.get("/export.csv")
    assert resp.status_code == 302
