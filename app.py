"""Pizza shop billing, inventory and sales software. Run: python app.py"""
import os, json, csv, io, secrets, shutil, sqlite3
from datetime import datetime, date, timedelta
from functools import wraps
from flask import (Flask, g, request, session, redirect, url_for, render_template, jsonify,
                   abort, flash, send_file, Response)
from werkzeug.security import check_password_hash, generate_password_hash as _gph


def generate_password_hash(p):
    return _gph(p, method="pbkdf2:sha256")
import db as D

app = Flask(__name__)
D.init_db()
_key = os.path.join(D.DATA, "secret.key")
if os.environ.get("SECRET_KEY"):
    app.secret_key = os.environ["SECRET_KEY"]
elif D.DEMO:
    app.secret_key = "pizza-pos-demo-only"      # demo instances hold sample data only; set SECRET_KEY to override
else:
    if not os.path.exists(_key):
        open(_key, "w").write(secrets.token_hex(32))
    app.secret_key = open(_key).read().strip()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=bool(os.environ.get("VERCEL")))
app.permanent_session_lifetime = timedelta(hours=12)
ORDER_TYPES = {"dine_in": "Dine-in", "takeaway": "Takeaway", "delivery": "Delivery"}


# ---------- helpers ----------
def q(sql, args=(), one=False):
    rows = g.db.execute(sql, args).fetchall()
    return (rows[0] if rows else None) if one else rows


def settings():
    if "settings" not in g:
        g.settings = {r["key"]: r["value"] for r in q("SELECT * FROM settings")}
    return g.settings


def S(key, default=""):
    return settings().get(key, default)


def S_num(key, default=0.0):
    try:
        return float(S(key, default))
    except ValueError:
        return float(default)


def can(perm):
    return bool(g.get("user")) and (g.is_admin or perm in g.perms)


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def audit(action, detail="", user=None):
    u = user or g.get("user")
    g.db.execute("INSERT INTO audit_log(user_id,username,action,detail,created_at) VALUES(?,?,?,?,?)",
                 (u["id"] if u else None, u["username"] if u else "", action, detail, D.now()))


def num(v, default=0.0):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return default


def money_fmt(n):
    n = float(n or 0)
    return f"{n:,.0f}" if abs(n - round(n)) < 0.005 else f"{n:,.2f}"


def need(*perms):
    def deco(f):
        @wraps(f)
        def w(*a, **kw):
            if not g.user:
                return redirect(url_for("login", next=request.path))
            if not g.is_admin and not all(p in g.perms for p in perms):
                abort(403)
            return f(*a, **kw)
        return w
    return deco


@app.before_request
def load_user():
    g.db = D.connect()
    g.user, g.perms, g.is_admin = None, set(), False
    uid = session.get("uid")
    if uid:
        u = q("SELECT u.*, r.name role_name, r.is_admin, r.perms FROM users u JOIN roles r ON r.id=u.role_id "
              "WHERE u.id=? AND u.active=1", (uid,), one=True)
        if u:
            g.user, g.is_admin = u, bool(u["is_admin"])
            g.perms = set(D.ALL) if g.is_admin else set(json.loads(u["perms"] or "[]"))
        else:
            session.clear()
    if request.method == "POST":
        tok = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        if not tok or tok != session.get("csrf"):
            abort(400, "Security token expired. Reload the page and try again.")


@app.teardown_request
def close_db(exc):
    conn = g.pop("db", None)
    if conn:
        conn.commit() if exc is None else conn.rollback()
        conn.close()


@app.context_processor
def inject():
    return dict(demo=D.DEMO, user=g.get("user"), can=can, S=S, csrf_token=csrf_token, money=money_fmt, perms_list=D.PERMISSIONS,
                order_types=ORDER_TYPES, cur=S("currency", "Rs") if g.get("db") else "Rs")


@app.errorhandler(Exception)
def on_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    app.logger.exception("Unhandled error on %s", request.path)
    detail = f"{type(e).__name__}: {e}" if (D.DEMO or app.debug) else ""
    try:
        return render_template("error.html", code=500, msg="Something went wrong on the server.", detail=detail), 500
    except Exception:
        return f"Server error. {detail}", 500


@app.errorhandler(403)
def e403(e):
    return render_template("error.html", code=403, msg="Your role does not have permission for this page."), 403


@app.errorhandler(400)
def e400(e):
    return render_template("error.html", code=400, msg=getattr(e, "description", "Bad request")), 400


@app.errorhandler(404)
def e404(e):
    return render_template("error.html", code=404, msg="Page not found."), 404


@app.template_filter("rs")
def rs_filter(n):
    return f"{S('currency', 'Rs')} {money_fmt(n)}"


def safe_next(url):
    return url if url and url.startswith("/") and not url.startswith("//") else None


def auto_backup(force=False):
    today = date.today().strftime("%Y%m%d")
    if not force and any(f.startswith("pizza-" + today) for f in os.listdir(D.BACKUPS)):
        return None
    name = f"pizza-{datetime.now():%Y%m%d-%H%M%S}.db"
    dest = sqlite3.connect(os.path.join(D.BACKUPS, name))
    src = sqlite3.connect(D.DB_PATH)
    src.backup(dest)
    dest.close(); src.close()
    return name


# ---------- login / account ----------
@app.route("/healthz")
def healthz():
    """Plain status page for checking a deployment (no secrets)."""
    import sys, hashlib
    return jsonify(ok=True, path=request.path, original_path=request.environ.get("pizza.original_path", request.path),
                   demo=D.DEMO, database_ready=os.path.exists(D.DB_PATH), orders=g.db.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                   users=g.db.execute("SELECT COUNT(*) FROM users").fetchone()[0], python=sys.version.split()[0],
                   sqlite=sqlite3.sqlite_version, scrypt=hasattr(hashlib, "scrypt"), data_dir=D.DATA)


@app.route("/")
def home():
    if not g.user:
        return redirect(url_for("login"))
    if can("view_reports"):
        return redirect(url_for("admin_dash"))
    if can("take_order"):
        return redirect(url_for("pos"))
    if can("view_own_orders") or can("view_all_orders"):
        return redirect(url_for("orders"))
    abort(403)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q("SELECT * FROM users WHERE username=? AND active=1", (request.form.get("username", "").strip().lower(),), one=True)
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session.clear(); session.permanent = True
            session["uid"] = u["id"]; csrf_token()
            audit("login", "", user=u)
            return redirect(safe_next(request.args.get("next")) or url_for("home"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    if g.user:
        audit("logout")
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@need()
def account():
    if request.method == "POST":
        if not check_password_hash(g.user["password_hash"], request.form.get("old", "")):
            flash("Current password is wrong.", "error")
        elif len(request.form.get("new", "")) < 6:
            flash("New password must be at least 6 characters.", "error")
        else:
            g.db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(request.form["new"]), g.user["id"]))
            audit("password_change"); flash("Password changed.", "ok")
            return redirect(url_for("account"))
    return render_template("account.html")


# ---------- shifts (opening / closing balance) ----------
def open_shift(uid):
    return q("SELECT * FROM shifts WHERE user_id=? AND closed_at IS NULL", (uid,), one=True)


def shift_summary(sh):
    sid = sh["id"]
    pay = {r["method"]: r["t"] for r in q("SELECT method, SUM(amount) t FROM payments WHERE shift_id=? GROUP BY method", (sid,))}
    o = q("SELECT COUNT(*) n, COALESCE(SUM(total),0) total, COALESCE(SUM(discount),0) disc, COALESCE(SUM(tax),0) tax "
          "FROM orders WHERE shift_id=? AND status='paid'", (sid,), one=True)
    refunds = q("SELECT COALESCE(SUM(-amount),0) t FROM payments WHERE shift_id=? AND kind='refund'", (sid,), one=True)["t"]
    cash = pay.get("cash", 0) or 0
    return dict(id=sid, opened_at=sh["opened_at"], closed_at=sh["closed_at"], opening=sh["opening_balance"],
                cash_net=round(cash, 2), card=round(pay.get("card", 0) or 0, 2), online=round(pay.get("online", 0) or 0, 2),
                refunds=round(refunds, 2), expected=round(sh["opening_balance"] + cash, 2), orders=o["n"],
                sales=round(o["total"], 2), discounts=round(o["disc"], 2), tax=round(o["tax"], 2),
                counted=sh["counted_cash"], difference=sh["difference"], note=sh["note"])


@app.route("/shift")
@need("take_order")
def shift():
    sh = open_shift(g.user["id"])
    recent = q("SELECT * FROM shifts WHERE user_id=? AND closed_at IS NOT NULL ORDER BY id DESC LIMIT 10", (g.user["id"],))
    return render_template("shift.html", sh=sh, s=shift_summary(sh) if sh else None, recent=recent)


@app.route("/shift/open", methods=["POST"])
@need("take_order")
def shift_open():
    if open_shift(g.user["id"]):
        flash("You already have an open shift.", "error"); return redirect(url_for("pos"))
    bal = num(request.form.get("opening_balance"), -1)
    if bal < 0:
        flash("Enter the cash in the drawer (0 or more).", "error"); return redirect(url_for("shift"))
    g.db.execute("INSERT INTO shifts(user_id,opened_at,opening_balance) VALUES(?,?,?)", (g.user["id"], D.now(), bal))
    audit("shift_open", f"opening balance {bal}")
    return redirect(url_for("pos"))


@app.route("/shift/close", methods=["POST"])
@need("take_order")
def shift_close():
    sh = open_shift(g.user["id"])
    if not sh:
        flash("No open shift to close.", "error"); return redirect(url_for("shift"))
    counted = num(request.form.get("counted_cash"), -1)
    if counted < 0:
        flash("Count the cash in the drawer and enter it.", "error"); return redirect(url_for("shift"))
    s = shift_summary(sh)
    diff = round(counted - s["expected"], 2)
    g.db.execute("UPDATE shifts SET closed_at=?, counted_cash=?, expected_cash=?, difference=?, note=? WHERE id=?",
                 (D.now(), counted, s["expected"], diff, request.form.get("note", "").strip(), sh["id"]))
    audit("shift_close", f"expected {s['expected']} counted {counted} difference {diff}")
    g.db.commit()
    auto_backup()
    return redirect(url_for("shift_view", sid=sh["id"]))


@app.route("/shift/<int:sid>")
@need()
def shift_view(sid):
    sh = q("SELECT s.*, u.full_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.id=?", (sid,), one=True)
    if not sh or (sh["user_id"] != g.user["id"] and not can("view_all_shifts")):
        abort(403 if sh else 404)
    orders_ = q("SELECT * FROM orders WHERE shift_id=? ORDER BY id", (sid,))
    return render_template("shift_view.html", sh=sh, s=shift_summary(sh), orders=orders_)


# ---------- point of sale ----------
@app.route("/pos")
@need("take_order")
def pos():
    sh = open_shift(g.user["id"])
    if not sh and S("require_shift", "1") == "1":
        flash("Open your shift with the opening cash balance to start billing.", "ok")
        return redirect(url_for("shift"))
    items = q("SELECT * FROM menu_items WHERE active=1 ORDER BY category, sort, name")
    vs = q("SELECT * FROM variants WHERE active=1 ORDER BY price")
    menu = [dict(id=i["id"], category=i["category"], name=i["name"], allow_addons=bool(i["allow_addons"]),
                 variants=[dict(id=v["id"], label=v["label"], price=v["price"]) for v in vs if v["item_id"] == i["id"]])
            for i in items]
    menu = [m for m in menu if m["variants"]]
    addons = [dict(id=a["id"], name=a["name"], price=a["price"]) for a in q("SELECT * FROM addons WHERE active=1 ORDER BY name")]
    cfg = dict(currency=S("currency", "Rs"), tax_rate=S_num("tax_rate"), tax_name=S("tax_name", "Tax"),
               max_discount=S_num("cashier_max_discount", 10), can_discount=can("apply_discount"),
               csrf=csrf_token(), override=can("discount_override"))
    return render_template("pos.html", menu=menu, addons=addons, cfg=cfg, s=shift_summary(sh) if sh else None)


def err(msg, code=400):
    return jsonify(ok=False, error=msg), code


def usage_for(variant_id, addon_ids, qty):
    use = {}
    for r in q("SELECT ingredient_id, qty FROM recipes WHERE variant_id=?", (variant_id,)):
        use[r["ingredient_id"]] = use.get(r["ingredient_id"], 0) + r["qty"] * qty
    for aid in addon_ids:
        for r in q("SELECT ingredient_id, qty FROM addon_recipes WHERE addon_id=?", (aid,)):
            use[r["ingredient_id"]] = use.get(r["ingredient_id"], 0) + r["qty"] * qty
    return use


def unit_cost(variant_id, addon_ids):
    total = 0.0
    for ing, qty in usage_for(variant_id, addon_ids, 1).items():
        total += qty * q("SELECT cost FROM ingredients WHERE id=?", (ing,), one=True)["cost"]
    return round(total, 2)


def stock_move(ing_id, change, reason, ref="", supplier=None, ucost=None):
    g.db.execute("UPDATE ingredients SET stock = stock + ? WHERE id=?", (change, ing_id))
    g.db.execute("INSERT INTO stock_moves(ingredient_id,change,reason,ref,supplier_id,unit_cost,user_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
                 (ing_id, change, reason, ref, supplier, ucost, g.user["id"] if g.user else None, D.now()))


@app.route("/api/order", methods=["POST"])
@need("take_order")
def api_order():
    d = request.get_json(silent=True) or {}
    sh = open_shift(g.user["id"])
    if not sh and S("require_shift", "1") == "1":
        return err("Open your shift first.")
    lines = d.get("items") or []
    if not lines:
        return err("The cart is empty.")
    otype = d.get("order_type", "dine_in")
    if otype not in ORDER_TYPES:
        return err("Choose an order type.")
    if otype == "delivery" and not (d.get("address") or "").strip():
        return err("Enter the delivery address.")
    built, subtotal = [], 0.0
    for ln in lines:
        qty = int(num(ln.get("qty"), 1))
        if qty < 1 or qty > 99:
            return err("Quantity must be between 1 and 99.")
        v = q("SELECT v.*, m.name item_name, m.category, m.allow_addons FROM variants v JOIN menu_items m ON m.id=v.item_id "
              "WHERE v.id=? AND v.active=1 AND m.active=1", (ln.get("variant_id"),), one=True)
        if not v:
            return err("An item in the cart is no longer on the menu. Remove it and try again.")
        ads = []
        if v["allow_addons"]:
            for aid in ln.get("addons") or []:
                a = q("SELECT * FROM addons WHERE id=? AND active=1", (aid,), one=True)
                if a:
                    ads.append(a)
        unit = v["price"] + sum(a["price"] for a in ads)
        name = v["item_name"] + ("" if v["label"] == "Regular" else f" ({v['label']})")
        built.append(dict(v=v, qty=qty, ads=ads, unit=unit, name=name, note=(ln.get("note") or "").strip()[:120]))
        subtotal += unit * qty
    subtotal = round(subtotal, 2)
    dval = max(num(d.get("discount_value")), 0)
    disc = subtotal * dval / 100 if d.get("discount_type", "percent") == "percent" else dval
    disc = round(min(disc, subtotal), 2)
    if disc > 0:
        if not can("apply_discount"):
            return err("Your role cannot give discounts.", 403)
        limit = S_num("cashier_max_discount", 10)
        if disc / subtotal * 100 > limit + 1e-9 and not can("discount_override"):
            return err(f"Discounts above {limit:g}% need a manager.", 403)
    taxable = subtotal - disc
    tax = round(taxable * S_num("tax_rate") / 100, 2)
    total = round(taxable + tax, 2)
    p = d.get("payments") or {}
    card, online = max(num(p.get("card")), 0), max(num(p.get("online")), 0)
    cash_due = round(total - card - online, 2)
    if cash_due < -0.005:
        return err("Card and online amounts are more than the total.")
    received = num(p.get("cash_received"), cash_due)
    if cash_due > 0.005 and received + 0.005 < cash_due:
        return err("Cash received is less than the amount due.")
    # ---- all checks passed, write ----
    today = date.today()
    n = q("SELECT COUNT(*) c FROM orders WHERE date(created_at)=?", (today.isoformat(),), one=True)["c"] + 1
    code = f"{today:%Y%m%d}-{n:03d}"
    oid = g.db.execute(
        "INSERT INTO orders(code,created_at,user_id,shift_id,order_type,table_no,customer_name,customer_phone,address,note,"
        "subtotal,discount,tax,total) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (code, D.now(), g.user["id"], sh["id"] if sh else None, otype, (d.get("table_no") or "")[:20],
         (d.get("customer_name") or "")[:60], (d.get("customer_phone") or "")[:30], (d.get("address") or "")[:200],
         (d.get("note") or "")[:200], subtotal, disc, tax, total)).lastrowid
    use = {}
    for b in built:
        aids = [a["id"] for a in b["ads"]]
        g.db.execute("INSERT INTO order_items(order_id,variant_id,name,category,qty,unit_price,addons,note,unit_cost) VALUES(?,?,?,?,?,?,?,?,?)",
                     (oid, b["v"]["id"], b["name"], b["v"]["category"], b["qty"], b["unit"],
                      json.dumps([dict(id=a["id"], name=a["name"], price=a["price"]) for a in b["ads"]]), b["note"],
                      unit_cost(b["v"]["id"], aids)))
        for ing, qty in usage_for(b["v"]["id"], aids, b["qty"]).items():
            use[ing] = use.get(ing, 0) + qty
    for ing, qty in use.items():
        stock_move(ing, -qty, "sale", code)
    for method, amt in (("cash", cash_due), ("card", card), ("online", online)):
        if amt > 0.004:
            g.db.execute("INSERT INTO payments(order_id,shift_id,method,amount,kind,created_at) VALUES(?,?,?,?,'sale',?)",
                         (oid, sh["id"] if sh else None, method, amt, D.now()))
    if d.get("held_id"):
        g.db.execute("DELETE FROM held_orders WHERE id=? AND user_id=?", (d["held_id"], g.user["id"]))
    audit("order", f"{code} total {total}" + (f" discount {disc}" if disc else ""))
    g.db.commit()
    return jsonify(ok=True, id=oid, code=code, total=total, change=round(max(received - cash_due, 0), 2) if cash_due > 0.005 else 0,
                   summary=shift_summary(sh) if sh else None)


@app.route("/api/hold", methods=["POST"])
@need("take_order")
def api_hold():
    d = request.get_json(silent=True) or {}
    if not (d.get("cart") or {}).get("items"):
        return err("Nothing to hold.")
    label = (d.get("label") or "").strip()[:40] or "Held order"
    g.db.execute("INSERT INTO held_orders(user_id,label,data,created_at) VALUES(?,?,?,?)",
                 (g.user["id"], label, json.dumps(d["cart"]), D.now()))
    return jsonify(ok=True)


@app.route("/api/held")
@need("take_order")
def api_held():
    rows = q("SELECT * FROM held_orders WHERE user_id=? ORDER BY id DESC", (g.user["id"],))
    return jsonify([dict(id=r["id"], label=r["label"], created_at=r["created_at"], cart=json.loads(r["data"])) for r in rows])


@app.route("/api/held/<int:hid>/delete", methods=["POST"])
@need("take_order")
def api_held_delete(hid):
    g.db.execute("DELETE FROM held_orders WHERE id=? AND user_id=?", (hid, g.user["id"]))
    return jsonify(ok=True)


# ---------- orders ----------
def order_access(o):
    return o and (can("view_all_orders") or (can("view_own_orders") and o["user_id"] == g.user["id"]))


@app.route("/orders")
@need()
def orders():
    allp = can("view_all_orders")
    if not (allp or can("view_own_orders")):
        abort(403)
    today = date.today().isoformat()
    d1 = request.args.get("from") or today
    d2 = request.args.get("to") or d1
    if not allp:
        d1 = d2 = today
    sql = ("SELECT o.*, u.full_name cashier FROM orders o LEFT JOIN users u ON u.id=o.user_id "
           "WHERE date(o.created_at) BETWEEN ? AND ?")
    args = [d1, d2]
    if not allp:
        sql += " AND o.user_id=?"; args.append(g.user["id"])
    if request.args.get("status"):
        sql += " AND o.status=?"; args.append(request.args["status"])
    term = request.args.get("q", "").strip()
    if term:
        sql += " AND (o.code LIKE ? OR o.customer_name LIKE ? OR o.customer_phone LIKE ?)"; args += [f"%{term}%"] * 3
    rows = q(sql + " ORDER BY o.id DESC LIMIT 500", args)
    paid = sum(r["total"] for r in rows if r["status"] == "paid")
    return render_template("orders.html", rows=rows, d1=d1, d2=d2, allp=allp, paid=paid, term=term, status=request.args.get("status", ""))


@app.route("/orders/<int:oid>")
@need()
def order_view(oid):
    o = q("SELECT o.*, u.full_name cashier FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE o.id=?", (oid,), one=True)
    if not order_access(o):
        abort(403 if o else 404)
    items = [dict(r, ads=json.loads(r["addons"] or "[]")) for r in q("SELECT * FROM order_items WHERE order_id=?", (oid,))]
    pays = q("SELECT * FROM payments WHERE order_id=? ORDER BY id", (oid,))
    return render_template("order_view.html", o=o, items=items, pays=pays)


@app.route("/orders/<int:oid>/refund", methods=["POST"])
@need("refund_orders")
def order_refund(oid):
    o = q("SELECT * FROM orders WHERE id=?", (oid,), one=True)
    if not o:
        abort(404)
    reason = request.form.get("reason", "").strip()
    if o["status"] != "paid" or not reason:
        flash("Only paid orders can be refunded, and a reason is required.", "error")
        return redirect(url_for("order_view", oid=oid))
    sh = open_shift(g.user["id"])
    for p in q("SELECT * FROM payments WHERE order_id=? AND kind='sale'", (oid,)):
        g.db.execute("INSERT INTO payments(order_id,shift_id,method,amount,kind,created_at) VALUES(?,?,?,?,'refund',?)",
                     (oid, sh["id"] if sh else None, p["method"], -p["amount"], D.now()))
    g.db.execute("UPDATE orders SET status='refunded', refund_reason=?, refunded_by=?, refunded_at=? WHERE id=?",
                 (reason, g.user["id"], D.now(), oid))
    if request.form.get("restore_stock"):
        for it in q("SELECT * FROM order_items WHERE order_id=?", (oid,)):
            aids = [a["id"] for a in json.loads(it["addons"] or "[]")]
            for ing, qty in usage_for(it["variant_id"], aids, it["qty"]).items():
                stock_move(ing, qty, "refund", o["code"])
    audit("refund", f"{o['code']} total {o['total']} reason: {reason}")
    flash("Order refunded." + ("" if sh else " You have no open shift, so the cash refund is not in any drawer count."), "ok")
    return redirect(url_for("order_view", oid=oid))


@app.route("/receipt/<int:oid>")
@need()
def receipt(oid):
    o = q("SELECT o.*, u.full_name cashier FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE o.id=?", (oid,), one=True)
    if not order_access(o):
        abort(403 if o else 404)
    items = [dict(r, ads=json.loads(r["addons"] or "[]")) for r in q("SELECT * FROM order_items WHERE order_id=?", (oid,))]
    pays = q("SELECT method, SUM(amount) amount FROM payments WHERE order_id=? AND kind='sale' GROUP BY method", (oid,))
    return render_template("receipt.html", o=o, items=items, pays=pays, kitchen=bool(request.args.get("kitchen")),
                           auto=bool(request.args.get("print")))


# ---------- admin panel ----------
def date_range():
    today = date.today().isoformat()
    d1 = request.args.get("from") or today
    d2 = request.args.get("to") or d1
    if not can("view_reports_full"):
        d1 = d2 = today
    return d1, d2


@app.route("/admin")
@need("view_reports")
def admin_dash():
    today = date.today().isoformat()
    st = q("SELECT COUNT(*) n, COALESCE(SUM(total),0) t FROM orders WHERE status='paid' AND date(created_at)=?", (today,), one=True)
    pay = q("SELECT method, SUM(amount) t FROM payments WHERE date(created_at)=? GROUP BY method", (today,))
    low = q("SELECT * FROM ingredients WHERE stock <= reorder_level ORDER BY name") if can("manage_inventory") else []
    shifts = q("SELECT s.*, u.full_name FROM shifts s JOIN users u ON u.id=s.user_id WHERE s.closed_at IS NULL") if can("view_all_shifts") else []
    shifts = [dict(s, **{k: v for k, v in shift_summary(s).items() if k in ("expected", "orders", "sales")}) for s in shifts]
    top = q("SELECT oi.name, SUM(oi.qty) qty FROM order_items oi JOIN orders o ON o.id=oi.order_id "
            "WHERE o.status='paid' AND date(o.created_at)=? GROUP BY oi.name ORDER BY qty DESC LIMIT 5", (today,))
    recent = q("SELECT o.*, u.full_name cashier FROM orders o LEFT JOIN users u ON u.id=o.user_id ORDER BY o.id DESC LIMIT 8") if can("view_all_orders") else []
    return render_template("admin/dashboard.html", st=st, pay=pay, low=low, shifts=shifts, top=top, recent=recent, today=today)


@app.route("/admin/reports")
@need("view_reports")
def reports():
    d1, d2 = date_range()
    B = (d1, d2)
    W = "date(o.created_at) BETWEEN ? AND ? AND o.status='paid'"
    summ = q(f"SELECT COUNT(*) n, COALESCE(SUM(total),0) total, COALESCE(SUM(subtotal),0) subtotal, COALESCE(SUM(discount),0) disc, "
             f"COALESCE(SUM(tax),0) tax FROM orders o WHERE {W}", B, one=True)
    refunded = q("SELECT COUNT(*) n, COALESCE(SUM(total),0) t FROM orders WHERE status='refunded' AND date(created_at) BETWEEN ? AND ?", B, one=True)
    by_pay = q("SELECT method, SUM(amount) t FROM payments WHERE date(created_at) BETWEEN ? AND ? GROUP BY method", B)
    item_sql = f"FROM order_items oi JOIN orders o ON o.id=oi.order_id WHERE {W}"
    by_cat = q(f"SELECT oi.category c, SUM(oi.qty) qty, SUM(oi.qty*oi.unit_price) rev {item_sql} GROUP BY oi.category ORDER BY rev DESC", B)
    top = q(f"SELECT oi.name c, SUM(oi.qty) qty, SUM(oi.qty*oi.unit_price) rev {item_sql} GROUP BY oi.name ORDER BY rev DESC LIMIT 15", B)
    by_user = q(f"SELECT u.full_name c, COUNT(*) n, SUM(o.total) t FROM orders o JOIN users u ON u.id=o.user_id WHERE {W} GROUP BY u.id ORDER BY t DESC", B)
    by_type = q(f"SELECT o.order_type c, COUNT(*) n, SUM(o.total) t FROM orders o WHERE {W} GROUP BY o.order_type", B)
    by_day = q(f"SELECT date(o.created_at) c, COUNT(*) n, SUM(o.total) t FROM orders o WHERE {W} GROUP BY date(o.created_at) ORDER BY 1", B)
    profit = None
    if can("view_profit"):
        cost = q(f"SELECT COALESCE(SUM(oi.qty*oi.unit_cost),0) c {item_sql}", B, one=True)["c"]
        rev = summ["subtotal"] - summ["disc"]
        profit = dict(revenue=rev, cost=cost, profit=rev - cost, margin=(rev - cost) / rev * 100 if rev else 0)
    maxday = max([r["t"] for r in by_day] or [1])
    return render_template("admin/reports.html", d1=d1, d2=d2, summ=summ, refunded=refunded, by_pay=by_pay, by_cat=by_cat, top=top,
                           by_user=by_user, by_type=by_type, by_day=by_day, profit=profit, maxday=maxday, full=can("view_reports_full"))


@app.route("/admin/export/orders.csv")
@need("view_reports_full")
def export_orders():
    d1, d2 = date_range()
    rows = q("SELECT o.code, o.created_at, u.full_name cashier, o.order_type, o.customer_name, o.subtotal, o.discount, o.tax, o.total, o.status "
             "FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE date(o.created_at) BETWEEN ? AND ? ORDER BY o.id", (d1, d2))
    buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(["Order", "Date", "Cashier", "Type", "Customer", "Subtotal", "Discount", "Tax", "Total", "Status"])
    for r in rows:
        w.writerow(list(r))
    audit("export", f"orders {d1} to {d2}")
    return Response(buf.getvalue(), mimetype="text/csv", headers={"Content-Disposition": f"attachment; filename=orders-{d1}-to-{d2}.csv"})


@app.route("/admin/shifts")
@need("view_all_shifts")
def admin_shifts():
    rows = q("SELECT s.*, u.full_name FROM shifts s JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 100")
    return render_template("admin/shifts.html", rows=rows)


# ----- menu -----
def parse_variants(text):
    out = []
    for part in (text or "").replace("\n", ",").split(","):
        if ":" in part:
            label, price = part.rsplit(":", 1)
            if label.strip() and num(price, -1) >= 0:
                out.append((label.strip(), num(price)))
    return out


@app.route("/admin/menu")
@need("manage_menu")
def admin_menu():
    items = q("SELECT * FROM menu_items ORDER BY category, name")
    vs = q("SELECT * FROM variants ORDER BY price")
    data = [dict(i, variants=[v for v in vs if v["item_id"] == i["id"]]) for i in items]
    cats = sorted({i["category"] for i in items})
    return render_template("admin/menu.html", items=data, cats=cats, addons=q("SELECT * FROM addons ORDER BY name"))


@app.route("/admin/menu/item/add", methods=["POST"])
@need("manage_menu")
def menu_item_add():
    f = request.form
    vs = parse_variants(f.get("variants"))
    if not f.get("name", "").strip() or not f.get("category", "").strip() or not vs:
        flash("Enter a category, a name and at least one size with a price, like: Small:450, Large:1200", "error")
        return redirect(url_for("admin_menu"))
    iid = g.db.execute("INSERT INTO menu_items(category,name,allow_addons) VALUES(?,?,?)",
                       (f["category"].strip(), f["name"].strip(), 1 if f.get("allow_addons") else 0)).lastrowid
    for label, price in vs:
        g.db.execute("INSERT INTO variants(item_id,label,price) VALUES(?,?,?)", (iid, label, price))
    audit("menu_add", f["name"]); flash("Item added. Set its recipe so stock is deducted automatically.", "ok")
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/item/<int:iid>/save", methods=["POST"])
@need("manage_menu")
def menu_item_save(iid):
    f = request.form
    g.db.execute("UPDATE menu_items SET name=?, category=?, allow_addons=?, active=? WHERE id=?",
                 (f.get("name", "").strip(), f.get("category", "").strip(), 1 if f.get("allow_addons") else 0, 1 if f.get("active") else 0, iid))
    audit("menu_edit", f"item {iid} {f.get('name')}"); flash("Item saved.", "ok")
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/variant/add", methods=["POST"])
@need("manage_menu")
def menu_variant_add():
    f = request.form
    if f.get("label", "").strip() and num(f.get("price"), -1) >= 0:
        g.db.execute("INSERT INTO variants(item_id,label,price) VALUES(?,?,?)", (f["item_id"], f["label"].strip(), num(f["price"])))
        audit("menu_edit", f"variant added to item {f['item_id']}")
    else:
        flash("Enter a size name and a price.", "error")
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/variant/<int:vid>/save", methods=["POST"])
@need("manage_menu")
def menu_variant_save(vid):
    f = request.form
    old = q("SELECT price FROM variants WHERE id=?", (vid,), one=True)
    g.db.execute("UPDATE variants SET label=?, price=?, active=? WHERE id=?",
                 (f.get("label", "").strip(), num(f.get("price")), 1 if f.get("active") else 0, vid))
    audit("price_change", f"variant {vid}: {old['price'] if old else '?'} -> {num(f.get('price'))}")
    flash("Size saved.", "ok")
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/addon/add", methods=["POST"])
@need("manage_menu")
def addon_add():
    f = request.form
    if f.get("name", "").strip() and num(f.get("price"), -1) >= 0:
        g.db.execute("INSERT INTO addons(name,price) VALUES(?,?)", (f["name"].strip(), num(f["price"])))
        audit("menu_add", "addon " + f["name"])
    return redirect(url_for("admin_menu"))


@app.route("/admin/menu/addon/<int:aid>/save", methods=["POST"])
@need("manage_menu")
def addon_save(aid):
    f = request.form
    g.db.execute("UPDATE addons SET name=?, price=?, active=? WHERE id=?",
                 (f.get("name", "").strip(), num(f.get("price")), 1 if f.get("active") else 0, aid))
    audit("menu_edit", f"addon {aid}"); flash("Add-on saved.", "ok")
    return redirect(url_for("admin_menu"))


@app.route("/admin/recipe/<kind>/<int:rid>", methods=["GET", "POST"])
@need("manage_menu")
def recipe(kind, rid):
    if kind not in ("variant", "addon"):
        abort(404)
    table, col = ("recipes", "variant_id") if kind == "variant" else ("addon_recipes", "addon_id")
    if kind == "variant":
        row = q("SELECT v.id, m.name || ' (' || v.label || ')' title FROM variants v JOIN menu_items m ON m.id=v.item_id WHERE v.id=?", (rid,), one=True)
    else:
        row = q("SELECT id, name title FROM addons WHERE id=?", (rid,), one=True)
    if not row:
        abort(404)
    ings = q("SELECT * FROM ingredients ORDER BY name")
    if request.method == "POST":
        g.db.execute(f"DELETE FROM {table} WHERE {col}=?", (rid,))
        for i in ings:
            qty = num(request.form.get(f"ing_{i['id']}"))
            if qty > 0:
                g.db.execute(f"INSERT INTO {table}({col},ingredient_id,qty) VALUES(?,?,?)", (rid, i["id"], qty))
        audit("recipe_edit", f"{kind} {rid}"); flash("Recipe saved.", "ok")
        return redirect(url_for("admin_menu"))
    cur_ = {r["ingredient_id"]: r["qty"] for r in q(f"SELECT ingredient_id, qty FROM {table} WHERE {col}=?", (rid,))}
    return render_template("admin/recipe.html", row=row, ings=ings, cur_=cur_)


# ----- inventory & suppliers -----
@app.route("/admin/inventory")
@need("manage_inventory")
def admin_inventory():
    moves = q("SELECT m.*, i.name, i.unit, u.full_name, s.name supplier FROM stock_moves m JOIN ingredients i ON i.id=m.ingredient_id "
              "LEFT JOIN users u ON u.id=m.user_id LEFT JOIN suppliers s ON s.id=m.supplier_id ORDER BY m.id DESC LIMIT 60")
    ings = q("SELECT * FROM ingredients ORDER BY (stock <= reorder_level) DESC, name")
    value = sum(i["stock"] * i["cost"] for i in ings)
    return render_template("admin/inventory.html", ings=ings, moves=moves, suppliers=q("SELECT * FROM suppliers ORDER BY name"), value=value)


@app.route("/admin/inventory/add", methods=["POST"])
@need("manage_inventory")
def ing_add():
    f = request.form
    if not f.get("name", "").strip() or not f.get("unit", "").strip():
        flash("Enter an ingredient name and unit.", "error"); return redirect(url_for("admin_inventory"))
    try:
        iid = g.db.execute("INSERT INTO ingredients(name,unit,reorder_level,cost) VALUES(?,?,?,?)",
                           (f["name"].strip(), f["unit"].strip(), num(f.get("reorder")), num(f.get("cost")))).lastrowid
    except sqlite3.IntegrityError:
        flash("That ingredient already exists.", "error"); return redirect(url_for("admin_inventory"))
    if num(f.get("stock")) > 0:
        stock_move(iid, num(f["stock"]), "opening", "Opening stock")
    audit("ingredient_add", f["name"]); flash("Ingredient added.", "ok")
    return redirect(url_for("admin_inventory"))


@app.route("/admin/inventory/<int:iid>/save", methods=["POST"])
@need("manage_inventory")
def ing_save(iid):
    f = request.form
    g.db.execute("UPDATE ingredients SET name=?, unit=?, reorder_level=?, cost=? WHERE id=?",
                 (f.get("name", "").strip(), f.get("unit", "").strip(), num(f.get("reorder")), num(f.get("cost")), iid))
    audit("ingredient_edit", f"{iid} {f.get('name')}"); flash("Ingredient saved.", "ok")
    return redirect(url_for("admin_inventory"))


@app.route("/admin/stock/<action>", methods=["POST"])
@need("manage_inventory")
def stock_action(action):
    f = request.form
    ing = q("SELECT * FROM ingredients WHERE id=?", (f.get("ingredient_id"),), one=True)
    qty = num(f.get("qty"), -1)
    if not ing or action not in ("in", "waste", "count"):
        abort(400)
    if action == "count":
        counted = num(f.get("counted"), -1)
        if counted < 0:
            flash("Enter the counted quantity.", "error"); return redirect(url_for("admin_inventory"))
        diff = counted - ing["stock"]
        if abs(diff) > 1e-9:
            stock_move(ing["id"], diff, "count", f.get("reason", "Stock count"))
        audit("stock_count", f"{ing['name']}: {ing['stock']:g} -> {counted:g}")
    elif qty <= 0:
        flash("Quantity must be more than zero.", "error"); return redirect(url_for("admin_inventory"))
    elif action == "in":
        ucost = num(f.get("unit_cost"))
        stock_move(ing["id"], qty, "purchase", f.get("reason", ""), supplier=f.get("supplier_id") or None, ucost=ucost or None)
        if ucost > 0:
            g.db.execute("UPDATE ingredients SET cost=? WHERE id=?", (ucost, ing["id"]))
        audit("stock_in", f"{ing['name']} +{qty:g}")
    else:
        stock_move(ing["id"], -qty, "wastage", f.get("reason", ""))
        audit("wastage", f"{ing['name']} -{qty:g} {f.get('reason', '')}")
    flash("Stock updated.", "ok")
    return redirect(url_for("admin_inventory"))


@app.route("/admin/suppliers", methods=["GET", "POST"])
@need("manage_suppliers")
def admin_suppliers():
    if request.method == "POST":
        f = request.form
        if f.get("name", "").strip():
            g.db.execute("INSERT INTO suppliers(name,phone,notes) VALUES(?,?,?)", (f["name"].strip(), f.get("phone", ""), f.get("notes", "")))
            audit("supplier_add", f["name"]); flash("Supplier added.", "ok")
        return redirect(url_for("admin_suppliers"))
    return render_template("admin/suppliers.html", rows=q("SELECT * FROM suppliers ORDER BY name"))


@app.route("/admin/suppliers/<int:sid>/<action>", methods=["POST"])
@need("manage_suppliers")
def supplier_edit(sid, action):
    if action == "delete":
        g.db.execute("UPDATE stock_moves SET supplier_id=NULL WHERE supplier_id=?", (sid,))
        g.db.execute("DELETE FROM suppliers WHERE id=?", (sid,)); audit("supplier_delete", str(sid))
    else:
        f = request.form
        g.db.execute("UPDATE suppliers SET name=?, phone=?, notes=? WHERE id=?", (f.get("name", ""), f.get("phone", ""), f.get("notes", ""), sid))
        audit("supplier_edit", str(sid))
    return redirect(url_for("admin_suppliers"))


# ----- users, roles, settings -----
def active_admins(exclude=None):
    return q("SELECT COUNT(*) c FROM users u JOIN roles r ON r.id=u.role_id WHERE r.is_admin=1 AND u.active=1 AND u.id!=?", (exclude or 0,), one=True)["c"]


@app.route("/admin/users", methods=["GET", "POST"])
@need("manage_users")
def admin_users():
    if request.method == "POST":
        f = request.form
        uname = f.get("username", "").strip().lower()
        if not uname or not f.get("full_name", "").strip() or len(f.get("password", "")) < 6:
            flash("Enter a username, a full name and a password of at least 6 characters.", "error")
        else:
            try:
                g.db.execute("INSERT INTO users(username,full_name,password_hash,role_id,created_at) VALUES(?,?,?,?,?)",
                             (uname, f["full_name"].strip(), generate_password_hash(f["password"]), f["role_id"], D.now()))
                audit("user_add", uname); flash("User created.", "ok")
            except sqlite3.IntegrityError:
                flash("That username is taken.", "error")
        return redirect(url_for("admin_users"))
    rows = q("SELECT u.*, r.name role_name FROM users u JOIN roles r ON r.id=u.role_id ORDER BY u.active DESC, u.username")
    return render_template("admin/users.html", rows=rows, roles=q("SELECT * FROM roles ORDER BY id"))


@app.route("/admin/users/<int:uid>/save", methods=["POST"])
@need("manage_users")
def user_save(uid):
    f = request.form
    u = q("SELECT u.*, r.is_admin FROM users u JOIN roles r ON r.id=u.role_id WHERE u.id=?", (uid,), one=True)
    if not u:
        abort(404)
    new_role = q("SELECT * FROM roles WHERE id=?", (f.get("role_id"),), one=True)
    active = 1 if f.get("active") else 0
    if uid == g.user["id"] and (not active or new_role["id"] != u["role_id"]):
        flash("You cannot deactivate yourself or change your own role.", "error"); return redirect(url_for("admin_users"))
    if u["is_admin"] and (not active or not new_role["is_admin"]) and active_admins(exclude=uid) == 0:
        flash("There must be at least one active owner.", "error"); return redirect(url_for("admin_users"))
    g.db.execute("UPDATE users SET full_name=?, role_id=?, active=? WHERE id=?", (f.get("full_name", "").strip(), new_role["id"], active, uid))
    if f.get("password"):
        if len(f["password"]) < 6:
            flash("Password must be at least 6 characters.", "error"); return redirect(url_for("admin_users"))
        g.db.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(f["password"]), uid))
    audit("user_edit", f"{u['username']} role={new_role['name']} active={active}"); flash("User saved.", "ok")
    return redirect(url_for("admin_users"))


@app.route("/admin/roles")
@need("manage_roles")
def admin_roles():
    roles = [dict(r, plist=json.loads(r["perms"] or "[]"), n=q("SELECT COUNT(*) c FROM users WHERE role_id=?", (r["id"],), one=True)["c"])
             for r in q("SELECT * FROM roles ORDER BY id")]
    return render_template("admin/roles.html", roles=roles)


@app.route("/admin/roles/add", methods=["POST"])
@need("manage_roles")
def role_add():
    name = request.form.get("name", "").strip()
    try:
        if not name: raise sqlite3.IntegrityError
        g.db.execute("INSERT INTO roles(name,perms) VALUES(?,?)", (name, json.dumps(D.CASHIER)))
        audit("role_add", name); flash("Role created with cashier permissions. Adjust them below.", "ok")
    except sqlite3.IntegrityError:
        flash("Enter a unique role name.", "error")
    return redirect(url_for("admin_roles"))


@app.route("/admin/roles/<int:rid>/save", methods=["POST"])
@need("manage_roles")
def role_save(rid):
    r = q("SELECT * FROM roles WHERE id=?", (rid,), one=True)
    if not r or r["is_admin"]:
        abort(400, "The Owner role always has every permission.")
    chosen = [p for p in request.form.getlist("perm") if p in D.ALL]
    g.db.execute("UPDATE roles SET perms=? WHERE id=?", (json.dumps(chosen), rid))
    audit("role_perms", f"{r['name']}: {', '.join(chosen)}"); flash(f"Permissions for {r['name']} saved.", "ok")
    return redirect(url_for("admin_roles"))


@app.route("/admin/roles/<int:rid>/delete", methods=["POST"])
@need("manage_roles")
def role_delete(rid):
    r = q("SELECT * FROM roles WHERE id=?", (rid,), one=True)
    used = q("SELECT COUNT(*) c FROM users WHERE role_id=?", (rid,), one=True)["c"]
    if not r or r["is_admin"] or used:
        flash("A role can only be deleted when no users have it.", "error")
    else:
        g.db.execute("DELETE FROM roles WHERE id=?", (rid,)); audit("role_delete", r["name"])
    return redirect(url_for("admin_roles"))


@app.route("/admin/settings", methods=["GET", "POST"])
@need("manage_settings")
def admin_settings():
    if request.method == "POST":
        for k in D.DEFAULT_SETTINGS:
            if k == "require_shift":
                v = "1" if request.form.get(k) else "0"
            else:
                v = request.form.get(k, "").strip()
            if k in ("tax_rate", "cashier_max_discount") and not (0 <= num(v, -1) <= 100):
                flash(f"{k.replace('_', ' ').capitalize()} must be between 0 and 100.", "error"); return redirect(url_for("admin_settings"))
            g.db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, v))
        audit("settings", "updated"); flash("Settings saved.", "ok")
        return redirect(url_for("admin_settings"))
    return render_template("admin/settings.html")


@app.route("/admin/audit")
@need("view_audit")
def admin_audit():
    return render_template("admin/audit.html", rows=q("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300"))


@app.route("/admin/backup", methods=["GET", "POST"])
@need("manage_backup")
def admin_backup():
    if request.method == "POST":
        g.db.commit()
        flash(f"Backup created: {auto_backup(force=True)}", "ok"); audit("backup", "manual")
        return redirect(url_for("admin_backup"))
    files = sorted(os.listdir(D.BACKUPS), reverse=True)
    return render_template("admin/backup.html", files=[(f, round(os.path.getsize(os.path.join(D.BACKUPS, f)) / 1024)) for f in files])


@app.route("/admin/backup/<name>")
@need("manage_backup")
def backup_download(name):
    if name not in os.listdir(D.BACKUPS):
        abort(404)
    audit("backup_download", name)
    return send_file(os.path.join(D.BACKUPS, name), as_attachment=True)


if __name__ == "__main__":
    import threading, webbrowser
    auto_backup()
    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", 5000))
    if host == "127.0.0.1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    print(f"Pizza POS running at http://{host}:{port}  (Ctrl+C to stop)")
    app.run(host=host, port=port, debug=False, threaded=True)
