"""IMEI input: ASCII digits only (full-width and other Unicode digits
used to be accepted by str.isdigit() and stored as typed)."""
import pytest

from conftest import DEVICE
from device_app.db import get_db

GOOD = "123456789012345"


def stored_imei(app, serial="SN0001"):
    with app.app_context():
        row = get_db().execute(
            "SELECT imei FROM device WHERE serial_number = ?",
            (serial,)).fetchone()
    return None if row is None else row[0]


@pytest.mark.parametrize("bad", [
    "１２３４５６７８９０１２３４５",            # full-width digits
    "١٢٣٤٥٦٧٨٩٠١٢٣٤٥",                          # Arabic-Indic digits
    "²" * 15,                                    # superscript: isdigit() is True
    "12345678901234５",                          # one full-width digit mixed in
])
def test_non_ascii_digits_are_rejected_on_create(admin_client, app, bad):
    resp = admin_client.post("/devices/new", data=dict(DEVICE, imei=bad))
    assert "15桁の半角数字" in resp.get_data(as_text=True)
    assert stored_imei(app) is None            # nothing was registered


def test_non_ascii_digits_are_rejected_on_edit(admin_client, app):
    admin_client.post("/devices/new", data=DEVICE)
    resp = admin_client.post(
        "/devices/1/edit", data=dict(DEVICE, imei="１２３４５６７８９０１２３４５"))
    assert "15桁の半角数字" in resp.get_data(as_text=True)
    assert stored_imei(app) == GOOD            # unchanged


def test_ascii_with_spaces_and_hyphens_still_works(admin_client, app):
    admin_client.post("/devices/new",
                      data=dict(DEVICE, imei="12345 6789-0123 45"))
    assert stored_imei(app) == GOOD


def test_wrong_length_is_still_rejected(admin_client, app):
    resp = admin_client.post("/devices/new", data=dict(DEVICE, imei="1234"))
    assert "15桁" in resp.get_data(as_text=True)
    assert stored_imei(app) is None
