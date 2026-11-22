"""Photos (PLAN.md section 8): upload/view/delete on the detail
page, 5 per device, 5 MB each, JPEG/PNG/WebP checked by extension
and magic bytes, history entries, photo count on the list."""
import base64
import io
import os

from conftest import DEVICE
from device_app.db import get_db

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+ip1sAAAAASUVORK5CYII=")
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def upload(client, data=PNG, name="front.png", kind="front", device_id=1):
    return client.post(
        f"/devices/{device_id}/photos",
        data={"kind": kind, "photo": (io.BytesIO(data), name)},
        content_type="multipart/form-data")


def photos_of(client, device_id=1):
    with client.application.app_context():
        return [dict(r) for r in get_db().execute(
            "SELECT * FROM device_photo WHERE device_id = ? ORDER BY id",
            (device_id,)).fetchall()]


def photo_dir(client, device_id=1):
    db_path = client.application.config["DATABASE"]
    return os.path.join(os.path.dirname(db_path), "photos", str(device_id))


def test_upload_view_and_history(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    upload(admin_client)
    rows = photos_of(admin_client)
    assert len(rows) == 1 and rows[0]["kind"] == "front"
    path = os.path.join(photo_dir(admin_client), rows[0]["filename"])
    assert os.path.exists(path)
    resp = admin_client.get(f"/devices/1/photos/{rows[0]['id']}")
    assert resp.status_code == 200
    assert resp.data == PNG
    assert resp.content_type.startswith("image/png")
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "写真追加: 正面" in body  # history records it


def test_upload_jpeg_with_jpg_or_jpeg_name(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    upload(admin_client, data=JPG, name="back.jpg", kind="back")
    upload(admin_client, data=JPG, name="side.jpeg", kind="other")
    assert len(photos_of(admin_client)) == 2


def test_five_photo_limit(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    for _ in range(5):
        upload(admin_client)
    upload(admin_client)  # 6th must be refused
    assert len(photos_of(admin_client)) == 5


def test_rejects_non_image_and_mismatched_content(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    upload(admin_client, data=b"just text", name="note.txt")
    upload(admin_client, data=b"just text", name="fake.png")
    upload(admin_client, data=PNG, name="wrong.jpg")
    assert photos_of(admin_client) == []


def test_rejects_oversize_photo(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    big = PNG + b"\x00" * (5 * 1024 * 1024)
    upload(admin_client, data=big, name="big.png")
    assert photos_of(admin_client) == []


def test_delete_removes_row_file_and_history(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    upload(admin_client)
    row = photos_of(admin_client)[0]
    path = os.path.join(photo_dir(admin_client), row["filename"])
    admin_client.post(f"/devices/1/photos/{row['id']}/delete")
    assert photos_of(admin_client) == []
    assert not os.path.exists(path)
    assert admin_client.get(f"/devices/1/photos/{row['id']}").status_code == 404
    body = admin_client.get("/devices/1").get_data(as_text=True)
    assert "写真削除: 正面" in body


def test_photo_view_requires_login_and_matching_device(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    upload(admin_client)
    row = photos_of(admin_client)[0]
    # Another device's URL for this photo is a 404, not a leak.
    admin_client.post("/devices/new",
                      data=dict(DEVICE, serial_number="SN0002"))
    assert admin_client.get(f"/devices/2/photos/{row['id']}").status_code == 404
    admin_client.post("/logout")
    resp = admin_client.get(f"/devices/1/photos/{row['id']}")
    assert resp.status_code == 302  # bounced to login


def test_list_shows_photo_count(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    upload(admin_client)
    upload(admin_client, kind="back")
    body = admin_client.get("/").get_data(as_text=True)
    assert "写真 2枚" in body


def test_photo_view_sets_nosniff(admin_client):
    admin_client.post("/devices/new", data=DEVICE)
    upload(admin_client)
    row = photos_of(admin_client)[0]
    resp = admin_client.get(f"/devices/1/photos/{row['id']}")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("image/png")
    # A file that merely starts like an image must never be
    # sniffed as anything else (e.g. HTML) by the browser.
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_request_over_the_hard_limit_is_refused_safely(admin_client):
    # Bigger than MAX_CONTENT_LENGTH (5 MB + 64 KB): this exercises
    # the 413 handler itself, not the per-file size check.
    admin_client.post("/devices/new", data=DEVICE)
    huge = PNG + b"\x00" * (5 * 1024 * 1024 + 128 * 1024)
    resp = admin_client.post(
        "/devices/1/photos",
        data={"kind": "front", "photo": (io.BytesIO(huge), "huge.png")},
        content_type="multipart/form-data",
        headers={"Referer": "http://evil.example/landing"})
    assert resp.status_code == 302
    # The redirect target is internal even when the referrer is an
    # external site (same rule as the login "next" check).
    assert "evil.example" not in resp.headers["Location"]
    assert photos_of(admin_client) == []
    body = admin_client.get("/").get_data(as_text=True)
    assert "写真は1枚5MBまで" in body


def test_simultaneous_uploads_cannot_pass_the_limit(app, admin_client):
    # Fill up to 4, then two uploads at the same moment race for
    # the last slot: exactly one may win, the count stays at 5.
    import threading

    from conftest import login

    admin_client.post("/devices/new", data=DEVICE)
    for _ in range(4):
        upload(admin_client)
    assert len(photos_of(admin_client)) == 4

    barrier = threading.Barrier(2)
    errors = []

    def worker():
        try:
            client = app.test_client()
            login(client, "admin", "admin-pass-1")
            barrier.wait(timeout=10)
            upload(client)
        except Exception as exc:  # collected and asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors
    rows = photos_of(admin_client)
    assert len(rows) == 5
    files = os.listdir(photo_dir(admin_client))
    assert sorted(files) == sorted(r["filename"] for r in rows)
