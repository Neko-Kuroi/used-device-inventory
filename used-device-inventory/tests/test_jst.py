"""Japan-time display of the UTC timestamps (step 1: the helper and the
Jinja filter). The database keeps UTC; only the display changes."""
import pytest

from device_app import to_jst


@pytest.mark.parametrize("utc, jst", [
    ("2026-11-21 15:13:26", "2026-11-22 00:13:26"),   # ordinary
    ("2026-11-21 20:00:00", "2026-11-22 05:00:00"),   # crosses midnight
    ("2026-12-31 15:00:00", "2027-01-01 00:00:00"),   # crosses the year
    ("2028-02-28 15:00:00", "2028-02-29 00:00:00"),   # leap day
    ("2026-11-21 00:00:00", "2026-11-21 09:00:00"),   # start of day
])
def test_to_jst_adds_nine_hours(utc, jst):
    assert to_jst(utc) == jst


@pytest.mark.parametrize("value", [None, ""])
def test_empty_values_give_empty_text(value):
    assert to_jst(value) == ""


@pytest.mark.parametrize("value", [
    "not a date", "2026-11-21", "2026-11-21T15:13:26", "2026-13-40 00:00:00",
])
def test_unreadable_values_are_returned_unchanged(value):
    assert to_jst(value) == value


def test_jst_filter_is_registered(app):
    assert app.jinja_env.filters["jst"] is to_jst
    out = app.jinja_env.from_string("{{ x | jst }}").render(
        x="2026-11-21 15:13:26")
    assert out == "2026-11-22 00:13:26"


# ---- step 2: the screens show Japan time, the database keeps UTC ----

from conftest import DEVICE, complete_inspection  # noqa: E402
from device_app.db import get_db  # noqa: E402

UTC = "2026-11-21 20:00:00"
JST = "2026-11-22 05:00:00"   # +9 hours, and the next day


def stamp_everything(app):
    """Overwrite every system timestamp with one known UTC value."""
    with app.app_context():
        db = get_db()
        db.execute("UPDATE device SET created_at = ?, updated_at = ?", (UTC, UTC))
        db.execute("UPDATE history SET created_at = ?", (UTC,))
        db.execute("UPDATE inspection SET checked_at = ?", (UTC,))
        db.execute("UPDATE user SET created_at = ?", (UTC,))
        db.commit()


def page(client, url):
    return client.get(url).data.decode()


def test_detail_shows_japan_time_for_dates_and_history(admin_client, app):
    admin_client.post("/devices/new", data=DEVICE)
    stamp_everything(app)
    html = page(admin_client, "/devices/1")
    assert f"{JST} / {JST}" in html          # 登録 / 更新
    assert f"<td>{JST}</td><td>create</td>" in html   # 履歴
    assert UTC not in html


def test_inspection_screen_shows_japan_time(admin_client, app):
    admin_client.post("/devices/new", data=DEVICE)
    complete_inspection(admin_client)
    stamp_everything(app)
    html = page(admin_client, "/devices/1/inspection")
    assert JST in html
    assert UTC not in html


def test_users_screen_shows_japan_time(admin_client, app):
    stamp_everything(app)
    html = page(admin_client, "/admin/users")
    assert f"<td>{JST}</td>" in html
    assert UTC not in html


def test_database_keeps_utc_and_user_entered_dates_are_untouched(
        admin_client, app):
    admin_client.post("/devices/new", data=DEVICE)
    stamp_everything(app)
    with app.app_context():
        raw = get_db().execute(
            "SELECT created_at FROM device WHERE id = 1").fetchone()[0]
    assert raw == UTC                          # stored as UTC
    html = page(admin_client, "/devices/1")
    assert "2026-11-01" in html                # 仕入れ日 (user-entered)
    assert "2026-11-02" in html                # データ消去日 (user-entered)


# ---- step 3: the CSV export shows Japan time too ----

def csv_rows(client, url="/export.csv"):
    import csv
    import io
    text = client.get(url).get_data().decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text)))


def test_csv_timestamps_are_japan_time(admin_client, app):
    admin_client.post("/devices/new", data=DEVICE)
    stamp_everything(app)
    header, row = csv_rows(admin_client)
    assert row[header.index("登録日時")] == JST
    assert row[header.index("更新日時")] == JST
    assert UTC not in ",".join(row)


def test_csv_headers_and_user_entered_dates_are_unchanged(admin_client, app):
    admin_client.post("/devices/new", data=DEVICE)
    stamp_everything(app)
    header, row = csv_rows(admin_client)
    assert header[-2:] == ["登録日時", "更新日時"]     # headers as before
    assert row[header.index("仕入れ日")] == "2026-11-01"
    assert row[header.index("データ消去日")] == "2026-11-02"
