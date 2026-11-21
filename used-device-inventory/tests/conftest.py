import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from device_app import create_app
from device_app.db import get_db


@pytest.fixture()
def app(tmp_path):
    app = create_app({
        "TESTING": True,
        "SECRET_KEY": "test",
        "DATABASE": str(tmp_path / "test.db"),
    })
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


def make_admin(client):
    return client.post("/setup", data={
        "username": "admin", "password": "admin-pass-1"})


def login(client, username, password):
    return client.post("/login", data={
        "username": username, "password": password})


@pytest.fixture()
def admin_client(client):
    make_admin(client)
    return client


def complete_inspection(client, device_id=1, kind="smartphone"):
    """Fill the whole checklist with 'pass' through the real UI route."""
    from device_app import CHECKLISTS
    data = {f"result_{key}": "pass" for key, _ in CHECKLISTS[kind]}
    return client.post(f"/devices/{device_id}/inspection", data=data)


DEVICE = {
    "kind": "smartphone", "maker": "Apple", "model": "iPhone 12",
    "serial_number": "SN0001", "imei": "123456789012345",
    "storage": "128GB", "ram": "4GB", "color": "黒",
    "battery_health": "89", "sim_status": "SIMフリー",
    "activation_lock": "解除済み", "network_restriction": "○",
    "cpu": "", "gpu": "", "os_name": "iOS 17", "battery_cycles": "",
    "condition_rank": "A", "status": "stocked",
    "cosmetic_damage": "角に小さな傷", "accessories": "箱、ケーブル",
    "data_erasure_at": "2026-11-02",
    "data_erasure_method": "本体の初期化",
    "purchase_date": "2026-11-01", "purchase_source": "店頭買取",
    "purchase_price": "30000", "planned_price": "45000",
    "sale_date": "", "sale_channel": "", "sale_price": "",
    "warranty_days": "90", "notes": "テスト個体",
}
