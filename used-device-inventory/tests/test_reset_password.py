import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reset_password as rp
from conftest import login, make_admin


def test_reset_lets_user_log_in_with_new_password(app, client):
    make_admin(client)
    db_path = app.config["DATABASE"]
    rp.reset_password(db_path, "admin", "brand-new-pass")
    fresh = app.test_client()
    assert login(fresh, "admin", "admin-pass-1").status_code == 200  # rejected
    r = login(app.test_client(), "admin", "brand-new-pass")
    assert r.status_code == 302


def test_reset_changes_only_that_user(app, client):
    make_admin(client)
    client.post("/admin/users", data={
        "username": "u1", "password": "user-pass-1"})
    rp.reset_password(app.config["DATABASE"], "u1", "another-pass-9")
    assert login(app.test_client(), "admin", "admin-pass-1").status_code == 302
    assert login(app.test_client(), "u1", "another-pass-9").status_code == 302


def test_short_password_rejected_and_nothing_changed(app, client):
    make_admin(client)
    with pytest.raises(ValueError):
        rp.reset_password(app.config["DATABASE"], "admin", "short")
    assert login(app.test_client(), "admin", "admin-pass-1").status_code == 302


def test_unknown_user_is_an_error(app, client):
    make_admin(client)
    with pytest.raises(ValueError):
        rp.reset_password(app.config["DATABASE"], "nobody", "long-enough-1")


def test_missing_database_is_not_created(tmp_path):
    path = str(tmp_path / "none.db")
    with pytest.raises(ValueError):
        rp.reset_password(path, "admin", "long-enough-1")
    assert not os.path.exists(path)


def test_list_users_shows_admin_flag(app, client):
    make_admin(client)
    rows = rp.list_users(app.config["DATABASE"])
    assert [(r[0], r[1]) for r in rows] == [("admin", 1)]


def test_main_mismatched_confirmation_changes_nothing(app, client, monkeypatch):
    make_admin(client)
    monkeypatch.setenv("DEVICE_DB", app.config["DATABASE"])
    answers = iter(["new-pass-aaa", "new-pass-bbb"])
    monkeypatch.setattr(rp.getpass, "getpass", lambda _p: next(answers))
    assert rp.main(["reset_password.py", "admin"]) == 1
    assert login(app.test_client(), "admin", "admin-pass-1").status_code == 302


def test_main_success(app, client, monkeypatch):
    make_admin(client)
    monkeypatch.setenv("DEVICE_DB", app.config["DATABASE"])
    monkeypatch.setattr(rp.getpass, "getpass", lambda _p: "new-pass-ok1")
    assert rp.main(["reset_password.py", "admin"]) == 0
    assert login(app.test_client(), "admin", "new-pass-ok1").status_code == 302
