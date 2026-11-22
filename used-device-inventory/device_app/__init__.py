"""used-device-inventory: individual-unit inventory for used phones and PCs.

Phase 1 (foundation): setup/login, device create/edit/list/search, history.
Flask + standard-library sqlite3 only.
"""
import os
import sqlite3
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from . import db as dbmod

KINDS = {"smartphone": "スマートフォン", "pc": "パソコン", "tablet": "タブレット"}
RANKS = ["S", "A", "B", "C", "junk"]
STATUSES = {
    "stocked": "仕入済み",
    "inspecting": "検査中",
    "maintaining": "整備中",
    "ready": "販売可能",
    "reserved": "取り置き",
    "sold": "販売済み",
    "returned": "返品・保証対応",
    "disposed": "処分",
}
NETWORK_RESTRICTION = ["", "○", "△", "×", "確認できず"]

# Lifecycle (PLAN.md section 5). "returned" and "disposed" are branches
# off the main flow; "disposed" is terminal.
FLOW_ORDER = ["stocked", "inspecting", "maintaining", "ready",
              "reserved", "sold"]
TRANSITIONS = {
    "stocked": {"inspecting", "maintaining", "disposed"},
    "inspecting": {"maintaining", "ready", "stocked", "disposed"},
    "maintaining": {"inspecting", "ready", "disposed"},
    "ready": {"reserved", "sold", "maintaining", "inspecting"},
    "reserved": {"sold", "ready"},
    "sold": {"returned"},
    "returned": {"maintaining", "disposed"},
    "disposed": set(),
}


def ready_blockers(device):
    """Why this device cannot move to 販売可能 yet (empty list = allowed)."""
    blockers = []
    if not device["inspection_done"]:
        blockers.append("検査が完了していません")
    if not device["data_erasure_at"]:
        blockers.append("データ消去の記録がありません")
    if device["network_restriction"] == "×":
        blockers.append("ネットワーク利用制限が × です")
    return blockers


# The spine of the flow. "maintaining" and "reserved" are optional
# side steps, so they are not on this spine: moving into or out of
# them is not "skipping" (e.g. inspecting -> ready is the normal
# path when no maintenance is needed, and ready -> sold is the
# normal path when there is no reservation).
MAIN_ORDER = ["stocked", "inspecting", "ready", "sold"]
REASON_PAIRS = {("reserved", "ready"), ("ready", "maintaining"),
                ("maintaining", "inspecting")}


def needs_reason(current, target):
    """Skipping steps on the spine, going backwards, and the
    returned/disposed branches always need a reason in history."""
    if target in ("returned", "disposed"):
        return True
    if (current, target) in REASON_PAIRS:
        return True
    if current in MAIN_ORDER and target in MAIN_ORDER:
        diff = MAIN_ORDER.index(target) - MAIN_ORDER.index(current)
        return diff < 0 or diff > 1
    return False

# Fields taken from the device form, in a fixed order (also used for
# building the change summary recorded in history).
DEVICE_FIELDS = [
    "kind", "maker", "model", "serial_number", "imei",
    "storage", "ram", "color", "battery_health", "sim_status",
    "activation_lock", "network_restriction",
    "cpu", "gpu", "os_name", "battery_cycles",
    "condition_rank", "status", "cosmetic_damage", "accessories",
    "inspection_done", "data_erasure_at", "data_erasure_method",
    "purchase_date", "purchase_source", "purchase_price",
    "planned_price", "sale_date", "sale_channel", "sale_price",
    "warranty_days", "notes",
]
INT_FIELDS = {"battery_health", "battery_cycles", "purchase_price",
              "planned_price", "sale_price", "warranty_days"}


# Inspection checklists per kind (PLAN.md section 6).
CHECKLISTS = {
    "smartphone": [
        ("power", "電源が入る"), ("screen_touch", "画面表示とタッチ"),
        ("camera", "カメラ（前後）"), ("speaker_mic", "スピーカーとマイク"),
        ("buttons", "ボタン類"), ("charging", "充電"),
        ("wifi", "Wi-Fi"), ("mobile", "モバイル通信"),
        ("biometrics", "指紋・顔認証"), ("battery", "バッテリー状態"),
    ],
    "pc": [
        ("power", "電源と起動"), ("display", "画面表示"),
        ("keyboard", "キーボード全キー"), ("trackpad", "トラックパッド"),
        ("camera", "カメラ"), ("speaker_mic", "スピーカーとマイク"),
        ("charging_battery", "充電とバッテリー"), ("wifi", "Wi-Fi"),
        ("lan", "有線 LAN"), ("ports", "USB 等の端子"),
        ("storage_health", "ストレージの健康状態"),
    ],
    "tablet": [
        ("power", "電源が入る"), ("screen_touch", "画面表示とタッチ"),
        ("camera", "カメラ（前後）"), ("speaker_mic", "スピーカーとマイク"),
        ("buttons", "ボタン類"), ("charging", "充電"),
        ("wifi", "Wi-Fi"), ("battery", "バッテリー状態"),
    ],
}
RESULTS = {"pass": "合格", "fail": "不合格", "na": "対象外"}

EXPENSE_CATEGORIES = {"parts": "部品代", "shipping": "送料",
                      "fee": "販売手数料", "other": "その他"}


def expense_total(db, device_id):
    row = db.execute(
        "SELECT COALESCE(SUM(amount), 0) AS t FROM expense"
        " WHERE device_id = ?", (device_id,)).fetchone()
    return row["t"]


def gross_profit(device, total):
    """実売価格 - 仕入れ価格 - 経費合計. None when it cannot be computed
    (not sold yet, or the purchase price was never recorded)."""
    if device["sale_price"] is None or device["purchase_price"] is None:
        return None
    return device["sale_price"] - device["purchase_price"] - total


def planned_profit(device, total):
    if device["planned_price"] is None or device["purchase_price"] is None:
        return None
    return device["planned_price"] - device["purchase_price"] - total


def inspection_state(db, device):
    """Summarize the checklist for one device.

    Complete means every item has a result and none of them is fail
    ('na' counts as done: the item does not apply to this unit)."""
    items = CHECKLISTS[device["kind"]]
    rows = db.execute(
        "SELECT * FROM inspection WHERE device_id = ?",
        (device["id"],)).fetchall()
    by_key = {r["item_key"]: r for r in rows}
    fails = [label for key, label in items
             if key in by_key and by_key[key]["result"] == "fail"]
    done = sum(1 for key, _ in items if key in by_key)
    complete = done == len(items) and not fails
    return {"by_key": by_key, "done": done, "total": len(items),
            "complete": complete, "fails": fails}


def csv_safe(value):
    """Neutralize spreadsheet formula injection: a cell starting with
    =, +, - or @ would be executed as a formula by Excel."""
    if isinstance(value, str) and value.lstrip()[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def mask_imei(imei):
    """Show only the last 4 digits; full IMEI is admin-only."""
    if not imei:
        return ""
    return "****" + imei[-4:]


def create_app(test_config=None):
    app = Flask(__name__)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app.config.from_mapping(
        SECRET_KEY="dev-change-me",
        DATABASE=os.path.join(base, "instance", "devices.db"),
    )
    if test_config:
        app.config.update(test_config)
    else:
        key_file = os.path.join(base, "instance", "secret_key")
        if not os.path.exists(key_file):
            import secrets
            os.makedirs(os.path.dirname(key_file), exist_ok=True)
            with open(key_file, "w") as f:
                f.write(secrets.token_hex(32))
            os.chmod(key_file, 0o600)
        with open(key_file) as f:
            app.config["SECRET_KEY"] = f.read().strip()

    os.makedirs(os.path.dirname(app.config["DATABASE"]), exist_ok=True)
    app.teardown_appcontext(dbmod.close_db)
    with app.app_context():
        dbmod.init_db()

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("user_id")
        if uid is not None:
            g.user = dbmod.get_db().execute(
                "SELECT * FROM user WHERE id = ?", (uid,)).fetchone()

    def login_required(view):
        @wraps(view)
        def wrapped(**kwargs):
            if dbmod.user_count() == 0:
                return redirect(url_for("setup"))
            if g.user is None:
                return redirect(url_for("login", next=request.path))
            return view(**kwargs)
        return wrapped

    def admin_required(view):
        @wraps(view)
        def wrapped(**kwargs):
            if g.user is None or not g.user["is_admin"]:
                abort(403)
            return view(**kwargs)
        return wrapped

    # ---------- setup / auth ----------

    @app.route("/setup", methods=["GET", "POST"])
    def setup():
        if dbmod.user_count() > 0:
            return redirect(url_for("login"))
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not username or len(password) < 8:
                flash("ユーザー名と、8文字以上のパスワードを入れてください。")
            else:
                db = dbmod.get_db()
                cur = db.execute(
                    "INSERT INTO user (username, password_hash, is_admin)"
                    " VALUES (?, ?, 1)",
                    (username, generate_password_hash(password)))
                db.commit()
                session["user_id"] = cur.lastrowid
                flash("管理者を作成しました。")
                return redirect(url_for("device_list"))
        return render_template("setup.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if dbmod.user_count() == 0:
            return redirect(url_for("setup"))
        if request.method == "POST":
            row = dbmod.get_db().execute(
                "SELECT * FROM user WHERE username = ?",
                (request.form.get("username", "").strip(),)).fetchone()
            if row and check_password_hash(
                    row["password_hash"], request.form.get("password", "")):
                session["user_id"] = row["id"]
                # Only internal paths may be used as the redirect target;
                # an absolute URL here would be an open redirect.
                nxt = request.args.get("next") or ""
                if not nxt.startswith("/") or nxt.startswith("//"):
                    nxt = url_for("device_list")
                return redirect(nxt)
            flash("ユーザー名かパスワードが違います。")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ---------- devices ----------

    def parse_device_form(fixed_status, fixed_inspection=0):
        """Return (values dict, error string or None).

        Status is never taken from this form: new devices start at
        仕入済み, and existing devices keep their status here. Status
        changes go through the dedicated transition route. The
        inspection_done flag is likewise derived from the checklist,
        never from this form."""
        values = {}
        for field in DEVICE_FIELDS:
            if field == "status":
                values[field] = fixed_status
                continue
            if field == "inspection_done":
                values[field] = fixed_inspection
                continue
            raw = (request.form.get(field) or "").strip()
            if field in INT_FIELDS:
                if raw == "":
                    values[field] = None
                else:
                    try:
                        values[field] = int(raw)
                    except ValueError:
                        return None, f"{field} は数字で入れてください。"
                    if values[field] < 0:
                        return None, f"{field} は0以上で入れてください。"
            elif field == "imei":
                # Only the IMEI distinguishes "not recorded" (None)
                # from an empty value: the checks below (and the IMEI
                # masking in the views) test it against None. Every
                # other text field simply keeps "" for "not entered".
                values[field] = raw if raw else None
            else:
                values[field] = raw
        if values["kind"] not in KINDS:
            return None, "種別が正しくありません。"
        if values["condition_rank"] not in RANKS:
            return None, "状態ランクが正しくありません。"
        if values["status"] not in STATUSES:
            return None, "状態が正しくありません。"
        if not values["serial_number"]:
            return None, "シリアル番号は必須です。"
        if values["imei"]:
            digits = values["imei"].replace(" ", "").replace("-", "")
            if not digits.isdigit() or len(digits) != 15:
                return None, "IMEI は15桁の数字で入れてください（判別できない場合は空欄のまま）。"
            values["imei"] = digits
        if values["network_restriction"] not in NETWORK_RESTRICTION:
            return None, "ネットワーク利用制限の値が正しくありません。"
        if values["imei"] is None and values["network_restriction"] == "":
            # No IMEI means the restriction check cannot be done. The
            # network restriction only applies to smartphones, so PCs
            # and tablets simply leave it empty.
            values["network_restriction"] = (
                "確認できず" if values["kind"] == "smartphone" else "")
        return values, None

    def filtered_devices():
        """Devices matching the list filters (shared by list and CSV)."""
        q = (request.args.get("q") or "").strip()
        kind = request.args.get("kind") or ""
        status = request.args.get("status") or ""
        sql = "SELECT * FROM device WHERE 1 = 1"
        args = []
        if q:
            # Escape the LIKE wildcards so % and _ in the query text
            # are matched literally, not as patterns.
            esc = (q.replace("\\", "\\\\").replace("%", "\\%")
                   .replace("_", "\\_"))
            sql += (" AND (maker LIKE ? ESCAPE '\\' OR model LIKE ? ESCAPE '\\'"
                    " OR serial_number LIKE ? ESCAPE '\\'"
                    " OR notes LIKE ? ESCAPE '\\')")
            args += [f"%{esc}%"] * 4
        if kind in KINDS:
            sql += " AND kind = ?"
            args.append(kind)
        if status in STATUSES:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY id DESC"
        rows = dbmod.get_db().execute(sql, args).fetchall()
        return rows, q, kind, status

    @app.route("/")
    @login_required
    def device_list():
        devices, q, kind, status = filtered_devices()
        db = dbmod.get_db()
        rows = []
        for d in devices:
            total = expense_total(db, d["id"])
            row = dict(d)
            row["expense_total"] = total
            row["profit"] = gross_profit(d, total)
            rows.append(row)
        return render_template(
            "list.html", devices=rows, q=q, kind=kind, status=status,
            kinds=KINDS, statuses=STATUSES, mask_imei=mask_imei)

    @app.route("/export.csv")
    @login_required
    def export_csv():
        import csv
        import io

        from flask import Response
        devices, _q, _kind, _status = filtered_devices()
        db = dbmod.get_db()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "ID", "種別", "メーカー", "機種", "シリアル番号", "IMEI末尾4桁",
            "状態ランク", "状態", "ネットワーク利用制限", "検査完了",
            "データ消去日", "仕入れ日", "仕入れ先", "仕入れ価格",
            "販売予定価格", "販売日", "販売先", "実売価格",
            "経費合計", "粗利", "備考", "登録日時", "更新日時"])
        for d in devices:
            total = expense_total(db, d["id"])
            profit = gross_profit(d, total)
            writer.writerow([csv_safe(v) for v in [
                d["id"], KINDS[d["kind"]], d["maker"], d["model"],
                d["serial_number"],
                d["imei"][-4:] if d["imei"] else "",
                d["condition_rank"], STATUSES[d["status"]],
                d["network_restriction"],
                "完了" if d["inspection_done"] else "",
                d["data_erasure_at"], d["purchase_date"],
                d["purchase_source"],
                d["purchase_price"] if d["purchase_price"] is not None else "",
                d["planned_price"] if d["planned_price"] is not None else "",
                d["sale_date"], d["sale_channel"],
                d["sale_price"] if d["sale_price"] is not None else "",
                total, profit if profit is not None else "",
                d["notes"], d["created_at"], d["updated_at"]]])
        # utf-8-sig so Excel opens the Japanese text correctly.
        body = buf.getvalue().encode("utf-8-sig")
        return Response(body, content_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 "attachment; filename=devices.csv"})

    @app.route("/devices/new", methods=["GET", "POST"])
    @login_required
    def device_new():
        if request.method == "POST":
            values, err = parse_device_form("stocked")
            if err:
                flash(err)
            else:
                db = dbmod.get_db()
                try:
                    cur = db.execute(
                        "INSERT INTO device ({}) VALUES ({})".format(
                            ", ".join(DEVICE_FIELDS),
                            ", ".join("?" * len(DEVICE_FIELDS))),
                        [values[f] for f in DEVICE_FIELDS])
                except sqlite3.IntegrityError:
                    # The only expected failure is the serial number
                    # being already registered; anything else should
                    # surface as a real error, not as "duplicate".
                    db.rollback()
                    flash("そのシリアル番号は既に登録されています。")
                else:
                    device_id = cur.lastrowid
                    db.execute(
                        "UPDATE device SET created_by = ? WHERE id = ?",
                        (g.user["id"], device_id))
                    if values["data_erasure_at"]:
                        # The recorder is part of the erasure record,
                        # on creation just as on edit.
                        db.execute(
                            "UPDATE device SET data_erasure_by = ?"
                            " WHERE id = ?", (g.user["id"], device_id))
                    db.execute(
                        "INSERT INTO history (device_id, user_id, action, detail)"
                        " VALUES (?, ?, 'create', ?)",
                        (device_id, g.user["id"],
                         f"{values['maker']} {values['model']} を登録"))
                    db.commit()
                    flash("登録しました。")
                    return redirect(url_for("device_detail", device_id=device_id))
        return render_template("form.html", device=None, kinds=KINDS,
                               ranks=RANKS, statuses=STATUSES,
                               restrictions=NETWORK_RESTRICTION)

    def get_device_or_404(device_id):
        row = dbmod.get_db().execute(
            "SELECT * FROM device WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            abort(404)
        return row

    @app.route("/devices/<int:device_id>")
    @login_required
    def device_detail(device_id):
        device = get_device_or_404(device_id)
        history = dbmod.get_db().execute(
            "SELECT h.*, u.username FROM history h"
            " LEFT JOIN user u ON u.id = h.user_id"
            " WHERE h.device_id = ? ORDER BY h.id DESC",
            (device_id,)).fetchall()
        imei_view = device["imei"] if (device["imei"] and g.user["is_admin"]) \
            else mask_imei(device["imei"])
        next_statuses = [
            {"key": key, "label": STATUSES[key],
             "needs_reason": needs_reason(device["status"], key),
             "blockers": ready_blockers(device) if key == "ready" else []}
            for key in sorted(TRANSITIONS[device["status"]],
                              key=lambda k: (FLOW_ORDER.index(k)
                                             if k in FLOW_ORDER else 99))
        ]
        erasure_by_name = ""
        if device["data_erasure_by"]:
            row = dbmod.get_db().execute(
                "SELECT username FROM user WHERE id = ?",
                (device["data_erasure_by"],)).fetchone()
            erasure_by_name = row["username"] if row else ""
        db = dbmod.get_db()
        expenses = db.execute(
            "SELECT * FROM expense WHERE device_id = ? ORDER BY id",
            (device_id,)).fetchall()
        total = expense_total(db, device_id)
        return render_template(
            "detail.html", device=device, history=history,
            kinds=KINDS, statuses=STATUSES, imei_view=imei_view,
            next_statuses=next_statuses,
            inspection=inspection_state(db, device),
            erasure_by_name=erasure_by_name,
            expenses=expenses, expense_total=total,
            profit=gross_profit(device, total),
            planned=planned_profit(device, total),
            expense_categories=EXPENSE_CATEGORIES)

    @app.route("/devices/<int:device_id>/expenses", methods=["POST"])
    @login_required
    def expense_add(device_id):
        get_device_or_404(device_id)
        category = request.form.get("category") or ""
        date = (request.form.get("expense_date") or "").strip()
        note = (request.form.get("note") or "").strip()
        raw_amount = (request.form.get("amount") or "").strip()
        try:
            amount = int(raw_amount)
        except ValueError:
            amount = -1
        if category not in EXPENSE_CATEGORIES or amount < 0:
            flash("経費の種類と金額（0以上の数字）を入れてください。")
            return redirect(url_for("device_detail", device_id=device_id))
        db = dbmod.get_db()
        db.execute(
            "INSERT INTO expense (device_id, expense_date, category,"
            " amount, note, created_by) VALUES (?, ?, ?, ?, ?, ?)",
            (device_id, date, category, amount, note, g.user["id"]))
        db.execute(
            "INSERT INTO history (device_id, user_id, action, detail)"
            " VALUES (?, ?, 'expense', ?)",
            (device_id, g.user["id"],
             f"経費追加: {EXPENSE_CATEGORIES[category]} {amount:,}円"
             + (f"（{note}）" if note else "")))
        db.commit()
        flash("経費を追加しました。")
        return redirect(url_for("device_detail", device_id=device_id))

    @app.route("/devices/<int:device_id>/expenses/<int:expense_id>/delete",
               methods=["POST"])
    @login_required
    def expense_delete(device_id, expense_id):
        db = dbmod.get_db()
        row = db.execute(
            "SELECT * FROM expense WHERE id = ? AND device_id = ?",
            (expense_id, device_id)).fetchone()
        if row is None:
            abort(404)
        db.execute("DELETE FROM expense WHERE id = ?", (expense_id,))
        db.execute(
            "INSERT INTO history (device_id, user_id, action, detail)"
            " VALUES (?, ?, 'expense', ?)",
            (device_id, g.user["id"],
             f"経費削除: {EXPENSE_CATEGORIES[row['category']]}"
             f" {row['amount']:,}円"))
        db.commit()
        flash("経費を削除しました。")
        return redirect(url_for("device_detail", device_id=device_id))

    @app.route("/devices/<int:device_id>/inspection",
               methods=["GET", "POST"])
    @login_required
    def device_inspection(device_id):
        device = get_device_or_404(device_id)
        db = dbmod.get_db()
        items = CHECKLISTS[device["kind"]]
        if request.method == "POST":
            for key, _label in items:
                result = request.form.get(f"result_{key}") or ""
                note = (request.form.get(f"note_{key}") or "").strip()
                if result in RESULTS:
                    db.execute(
                        "INSERT INTO inspection"
                        " (device_id, item_key, result, note, checked_by,"
                        "  checked_at)"
                        " VALUES (?, ?, ?, ?, ?, datetime('now'))"
                        " ON CONFLICT(device_id, item_key) DO UPDATE SET"
                        "  result = excluded.result, note = excluded.note,"
                        "  checked_by = excluded.checked_by,"
                        "  checked_at = excluded.checked_at",
                        (device_id, key, result, note, g.user["id"]))
                elif result == "":
                    db.execute(
                        "DELETE FROM inspection"
                        " WHERE device_id = ? AND item_key = ?",
                        (device_id, key))
            state = inspection_state(db, device)
            new_done = 1 if state["complete"] else 0
            db.execute(
                "UPDATE device SET inspection_done = ?,"
                " updated_at = datetime('now') WHERE id = ?",
                (new_done, device_id))
            summary = f"検査: {state['done']}/{state['total']} 項目"
            if state["fails"]:
                summary += "、不合格: " + ", ".join(state["fails"])
            if state["complete"]:
                summary += "（完了）"
            db.execute(
                "INSERT INTO history (device_id, user_id, action, detail)"
                " VALUES (?, ?, 'inspection', ?)",
                (device_id, g.user["id"], summary))
            # The gate must hold after the fact, not only at the moment
            # of the status change: if a device on sale stops being
            # complete (a fail was recorded), it goes back to 検査中
            # automatically. Recording the fail itself is never blocked.
            demoted = False
            if device["status"] in ("ready", "reserved") \
                    and not state["complete"]:
                db.execute(
                    "UPDATE device SET status = 'inspecting' WHERE id = ?",
                    (device_id,))
                db.execute(
                    "INSERT INTO history (device_id, user_id, action, detail)"
                    " VALUES (?, ?, 'status', ?)",
                    (device_id, g.user["id"],
                     f"{STATUSES[device['status']]} → 検査中"
                     "（検査で条件が崩れたため自動で戻しました）"))
                demoted = True
            db.commit()
            if demoted:
                flash("検査結果を保存しました。条件が崩れたため、"
                      "状態を「検査中」に戻しました。")
            else:
                flash("検査結果を保存しました。")
            return redirect(url_for("device_detail", device_id=device_id))
        state = inspection_state(db, device)
        return render_template(
            "inspection.html", device=device, items=items,
            state=state, results=RESULTS, kinds=KINDS)

    @app.route("/devices/<int:device_id>/status", methods=["POST"])
    @login_required
    def device_status(device_id):
        device = get_device_or_404(device_id)
        target = request.form.get("to_status") or ""
        reason = (request.form.get("reason") or "").strip()
        current = device["status"]
        if target not in TRANSITIONS[current]:
            flash(f"{STATUSES[current]} から {STATUSES.get(target, target)}"
                  " へは進めません。")
            return redirect(url_for("device_detail", device_id=device_id))
        if target == "ready":
            # Do not trust the stored inspection_done flag here:
            # recompute it from the checklist itself and sync the
            # flag, so a stale flag can never open (or wrongly
            # close) the gate.
            db = dbmod.get_db()
            state = inspection_state(db, device)
            fresh = 1 if state["complete"] else 0
            if fresh != device["inspection_done"]:
                db.execute(
                    "UPDATE device SET inspection_done = ? WHERE id = ?",
                    (fresh, device_id))
                db.commit()
                device = get_device_or_404(device_id)
            blockers = ready_blockers(device)
            if blockers:
                flash("販売可能にできません: " + " / ".join(blockers))
                return redirect(url_for("device_detail", device_id=device_id))
        if needs_reason(current, target) and not reason:
            flash("この変更には理由が必要です。")
            return redirect(url_for("device_detail", device_id=device_id))
        db = dbmod.get_db()
        db.execute(
            "UPDATE device SET status = ?, updated_at = datetime('now')"
            " WHERE id = ?", (target, device_id))
        detail = f"{STATUSES[current]} → {STATUSES[target]}"
        if reason:
            detail += f"（理由: {reason}）"
        if target == "returned":
            # A returned unit may hold the buyer's data and its
            # condition may have changed: the previous inspection and
            # erasure records no longer describe it. Reset both, so
            # the unit must be inspected and erased again before it
            # can be sold again.
            db.execute("DELETE FROM inspection WHERE device_id = ?",
                       (device_id,))
            db.execute(
                "UPDATE device SET inspection_done = 0,"
                " data_erasure_at = '', data_erasure_method = '',"
                " data_erasure_by = NULL WHERE id = ?", (device_id,))
            detail += "（返品のため検査結果とデータ消去の記録をリセットしました）"
        db.execute(
            "INSERT INTO history (device_id, user_id, action, detail)"
            " VALUES (?, ?, 'status', ?)",
            (device_id, g.user["id"], detail))
        db.commit()
        flash("状態を変更しました。")
        return redirect(url_for("device_detail", device_id=device_id))

    @app.route("/devices/<int:device_id>/edit", methods=["GET", "POST"])
    @login_required
    def device_edit(device_id):
        device = get_device_or_404(device_id)
        if request.method == "POST":
            values, err = parse_device_form(device["status"],
                                            device["inspection_done"])
            if err:
                flash(err)
            else:
                # A non-admin cannot see the full IMEI, so the form sends
                # it empty; treat that as "unchanged", not "cleared".
                if (not g.user["is_admin"] and values["imei"] is None
                        and device["imei"]):
                    values["imei"] = device["imei"]
                    if not (request.form.get("network_restriction") or "").strip():
                        values["network_restriction"] = device["network_restriction"]
                # While a unit is on sale (販売可能・取り置き), the facts
                # the ready gate rests on are locked: kind, IMEI,
                # network restriction and the erasure record may not be
                # edited out from under the gate. Move the status back
                # first if one of them really has to change.
                if device["status"] in ("ready", "reserved"):
                    locked = []
                    if values["kind"] != device["kind"]:
                        locked.append("種別")
                    if values["imei"] != device["imei"]:
                        locked.append("IMEI")
                    if (values["network_restriction"]
                            != device["network_restriction"]):
                        locked.append("ネットワーク利用制限")
                    if (values["data_erasure_at"] != device["data_erasure_at"]
                            or values["data_erasure_method"]
                            != device["data_erasure_method"]):
                        locked.append("データ消去の記録")
                    if locked:
                        flash("販売可能・取り置きの間は "
                              + "、".join(locked)
                              + " は変更できません。先に状態を戻してください。")
                        # Re-render the form with the submitted values
                        # instead of redirecting, so the rest of what
                        # was typed (a long note, say) is not lost.
                        shown = dict(device)
                        shown.update(values)
                        return render_template(
                            "form.html", device=shown, kinds=KINDS,
                            ranks=RANKS, statuses=STATUSES,
                            restrictions=NETWORK_RESTRICTION)
                changed = [f for f in DEVICE_FIELDS
                           if (device[f] if device[f] is not None else None)
                           != values[f]]
                db = dbmod.get_db()
                try:
                    db.execute(
                        "UPDATE device SET {}, updated_at = datetime('now')"
                        " WHERE id = ?".format(
                            ", ".join(f"{f} = ?" for f in DEVICE_FIELDS)),
                        [values[f] for f in DEVICE_FIELDS] + [device_id])
                except sqlite3.IntegrityError:
                    db.rollback()
                    flash("そのシリアル番号は既に登録されています。")
                else:
                    if (values["data_erasure_at"] != device["data_erasure_at"]
                            or values["data_erasure_method"]
                            != device["data_erasure_method"]):
                        # Who recorded the erasure is part of the record.
                        db.execute(
                            "UPDATE device SET data_erasure_by = ?"
                            " WHERE id = ?",
                            (g.user["id"] if values["data_erasure_at"] else None,
                             device_id))
                    db.execute(
                        "INSERT INTO history (device_id, user_id, action, detail)"
                        " VALUES (?, ?, 'update', ?)",
                        (device_id, g.user["id"],
                         "変更: " + ", ".join(changed) if changed else "変更なし"))
                    if values["kind"] != device["kind"]:
                        # The checklist differs per kind, so results
                        # recorded for the old kind say nothing about
                        # the new one: reset them.
                        db.execute(
                            "DELETE FROM inspection WHERE device_id = ?",
                            (device_id,))
                        db.execute(
                            "UPDATE device SET inspection_done = 0"
                            " WHERE id = ?", (device_id,))
                        db.execute(
                            "INSERT INTO history"
                            " (device_id, user_id, action, detail)"
                            " VALUES (?, ?, 'inspection', ?)",
                            (device_id, g.user["id"],
                             "種別を変更したため、検査結果をリセットしました"))
                    db.commit()
                    flash("保存しました。")
                    return redirect(url_for("device_detail", device_id=device_id))
        return render_template("form.html", device=device, kinds=KINDS,
                               ranks=RANKS, statuses=STATUSES,
                               restrictions=NETWORK_RESTRICTION)

    # ---------- admin: users ----------

    @app.route("/admin/users", methods=["GET", "POST"])
    @login_required
    @admin_required
    def admin_users():
        db = dbmod.get_db()
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            is_admin = 1 if request.form.get("is_admin") else 0
            if not username or len(password) < 8:
                flash("ユーザー名と、8文字以上のパスワードを入れてください。")
            else:
                try:
                    db.execute(
                        "INSERT INTO user (username, password_hash, is_admin)"
                        " VALUES (?, ?, ?)",
                        (username, generate_password_hash(password), is_admin))
                    db.commit()
                    flash("ユーザーを追加しました。")
                except sqlite3.IntegrityError:
                    db.rollback()
                    flash("そのユーザー名は既に使われています。")
        users = db.execute("SELECT * FROM user ORDER BY id").fetchall()
        return render_template("users.html", users=users)

    app.jinja_env.globals.update(kinds=KINDS, statuses=STATUSES)
    return app
