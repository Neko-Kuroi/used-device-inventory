"""Reset a user's password from the host PC (no web screen for this).

Usage (run on the machine that hosts instance/devices.db):

    .venv/bin/python reset_password.py               # list users
    .venv/bin/python reset_password.py <username>    # set a new password

The new password is typed at a prompt, never on the command line, so
it does not end up in the shell history. Only someone who can run
programs on the host PC can use this; the web app is not involved.
Running sessions stay logged in; the new password applies from the
next login.
"""
import getpass
import os
import sqlite3
import sys

from werkzeug.security import generate_password_hash

MIN_PASSWORD_LENGTH = 8  # same rule as the setup / user-admin screens
DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "instance", "devices.db")


def _connect(db_path):
    # Never create an empty database by accident (e.g. a typo in the
    # path): resetting a password only makes sense on an existing one.
    if not os.path.exists(db_path):
        raise ValueError(f"データベースが見つかりません: {db_path}")
    return sqlite3.connect(db_path)


def list_users(db_path):
    conn = _connect(db_path)
    try:
        return conn.execute(
            "SELECT username, is_admin FROM user ORDER BY id").fetchall()
    finally:
        conn.close()


def reset_password(db_path, username, password):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(
            f"パスワードは{MIN_PASSWORD_LENGTH}文字以上にしてください。")
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE user SET password_hash = ? WHERE username = ?",
            (generate_password_hash(password), username))
        if cur.rowcount != 1:
            conn.rollback()
            raise ValueError(f"そのユーザーはいません: {username}")
        conn.commit()
    finally:
        conn.close()


def main(argv):
    db_path = os.environ.get("DEVICE_DB", DEFAULT_DB)
    try:
        if len(argv) < 2:
            users = list_users(db_path)
            print("ユーザー一覧 (* は管理者):")
            for name, is_admin in users:
                print(f"  {'*' if is_admin else ' '} {name}")
            print("パスワードを作り直す: .venv/bin/python reset_password.py <ユーザー名>")
            return 0
        username = argv[1]
        password = getpass.getpass("新しいパスワード: ")
        if password != getpass.getpass("もう一度: "):
            print("2回の入力が違います。何も変更していません。")
            return 1
        reset_password(db_path, username, password)
    except ValueError as e:
        print(e)
        return 1
    print(f"{username} のパスワードを変更しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
