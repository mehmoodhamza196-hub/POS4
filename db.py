"""Database layer: schema, seed data and permission list (SQLite)."""
import os, sys, json, shutil, sqlite3, tempfile
from datetime import datetime
from werkzeug.security import generate_password_hash as _gph


def generate_password_hash(p):
    return _gph(p, method="pbkdf2:sha256")

# When packaged as an .exe, keep the data folder next to the .exe (not in a temp folder).
BASE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
DEMO = bool(os.environ.get("VERCEL")) or os.environ.get("DEMO_MODE") == "1"
# Vercel's disk is read-only except /tmp, so demo data lives there (and resets on cold start).
DATA = os.environ.get("PIZZA_DATA_DIR") or (os.path.join(tempfile.gettempdir(), "pizza-pos-data") if os.environ.get("VERCEL") else os.path.join(BASE, "data"))
BACKUPS = os.path.join(DATA, "backups")
DB_PATH = os.path.join(DATA, "pizza.db")
os.makedirs(BACKUPS, exist_ok=True)

# Permission tokens the admin can hand out to any role.
PERMISSIONS = [
    ("take_order", "Cashier: open shift, take orders, hold orders, take payments, print bills"),
    ("apply_discount", "Cashier: give discounts up to the cashier limit"),
    ("discount_override", "Give discounts above the cashier limit"),
    ("view_own_orders", "View own orders (today only)"),
    ("view_all_orders", "View and reprint all orders, any date"),
    ("refund_orders", "Refund orders"),
    ("manage_inventory", "Ingredients, stock-in, wastage and stock count"),
    ("manage_suppliers", "Manage suppliers"),
    ("manage_menu", "Add and edit menu items, prices, add-ons and recipes"),
    ("view_reports", "See the dashboard and today's sales report"),
    ("view_reports_full", "Reports for any date range and CSV export"),
    ("view_profit", "See cost and profit figures"),
    ("view_all_shifts", "See every cashier's shift and cash difference"),
    ("manage_users", "Create and edit users"),
    ("manage_roles", "Assign permissions to roles"),
    ("manage_settings", "Change shop details, tax and limits"),
    ("view_audit", "View the audit log"),
    ("manage_backup", "Create and download backups"),
]
ALL = [k for k, _ in PERMISSIONS]
CASHIER = ["take_order", "apply_discount", "view_own_orders"]
MANAGER = CASHIER + ["discount_override", "view_all_orders", "refund_orders", "manage_inventory",
                     "manage_suppliers", "manage_menu", "view_reports", "view_all_shifts"]

DEFAULT_SETTINGS = {
    "shop_name": "My Pizza Shop", "shop_address": "Shop address here", "shop_phone": "0300-0000000",
    "currency": "Rs", "tax_name": "GST", "tax_rate": "16", "cashier_max_discount": "10",
    "require_shift": "1", "receipt_footer": "Thank you! Visit again.",
}


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def connect():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS roles(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, is_admin INTEGER DEFAULT 0, perms TEXT DEFAULT '[]');
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role_id INTEGER NOT NULL REFERENCES roles(id), active INTEGER DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS menu_items(id INTEGER PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL,
  allow_addons INTEGER DEFAULT 0, active INTEGER DEFAULT 1, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS variants(id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES menu_items(id),
  label TEXT NOT NULL, price REAL NOT NULL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS addons(id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS ingredients(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, unit TEXT NOT NULL,
  stock REAL DEFAULT 0, reorder_level REAL DEFAULT 0, cost REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS recipes(variant_id INTEGER NOT NULL REFERENCES variants(id),
  ingredient_id INTEGER NOT NULL REFERENCES ingredients(id), qty REAL NOT NULL, PRIMARY KEY(variant_id, ingredient_id));
CREATE TABLE IF NOT EXISTS addon_recipes(addon_id INTEGER NOT NULL REFERENCES addons(id),
  ingredient_id INTEGER NOT NULL REFERENCES ingredients(id), qty REAL NOT NULL, PRIMARY KEY(addon_id, ingredient_id));
CREATE TABLE IF NOT EXISTS suppliers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS stock_moves(id INTEGER PRIMARY KEY, ingredient_id INTEGER NOT NULL, change REAL NOT NULL,
  reason TEXT NOT NULL, ref TEXT, supplier_id INTEGER, unit_cost REAL, user_id INTEGER, created_at TEXT);
CREATE TABLE IF NOT EXISTS shifts(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
  opened_at TEXT, opening_balance REAL NOT NULL, closed_at TEXT, counted_cash REAL, expected_cash REAL,
  difference REAL, note TEXT);
CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, code TEXT UNIQUE, created_at TEXT, user_id INTEGER, shift_id INTEGER,
  order_type TEXT, table_no TEXT, customer_name TEXT, customer_phone TEXT, address TEXT, note TEXT,
  subtotal REAL, discount REAL DEFAULT 0, tax REAL DEFAULT 0, total REAL, status TEXT DEFAULT 'paid',
  refund_reason TEXT, refunded_by INTEGER, refunded_at TEXT);
CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id),
  variant_id INTEGER, name TEXT, category TEXT, qty INTEGER, unit_price REAL, addons TEXT DEFAULT '[]', note TEXT, unit_cost REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), shift_id INTEGER,
  method TEXT, amount REAL, kind TEXT DEFAULT 'sale', created_at TEXT);
CREATE TABLE IF NOT EXISTS held_orders(id INTEGER PRIMARY KEY, user_id INTEGER, label TEXT, data TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, action TEXT, detail TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS ix_orders_date ON orders(created_at);
CREATE INDEX IF NOT EXISTS ix_pay_shift ON payments(shift_id);
"""


def refresh_demo_dates():
    """Move the bundled demo history forward so it always ends 'yesterday'."""
    from datetime import date, timedelta, datetime
    c = connect()
    row = c.execute("SELECT value FROM settings WHERE key='demo_anchor'").fetchone()
    if row:
        anchor = datetime.strptime(row[0], "%Y-%m-%d").date()
        delta = (date.today() - timedelta(days=1) - anchor).days
        if delta > 0:
            shift = f"+{delta} days"
            for tbl, cols in (("orders", ("created_at", "refunded_at")), ("payments", ("created_at",)), ("shifts", ("opened_at", "closed_at")),
                              ("stock_moves", ("created_at",)), ("audit_log", ("created_at",))):
                for col in cols:
                    c.execute(f"UPDATE {tbl} SET {col}=datetime({col}, ?) WHERE {col} IS NOT NULL", (shift,))
            for i in range(0, 61):                  # newest day first, so renamed codes never collide
                old, new = (anchor - timedelta(days=i)).strftime("%Y%m%d"), (anchor - timedelta(days=i) + timedelta(days=delta)).strftime("%Y%m%d")
                c.execute("UPDATE orders SET code=replace(code, ?, ?) WHERE code LIKE ?", (old, new, old + "%"))
                c.execute("UPDATE stock_moves SET ref=replace(ref, ?, ?) WHERE ref LIKE ?", (old, new, old + "%"))
                c.execute("UPDATE audit_log SET detail=replace(detail, ?, ?) WHERE detail LIKE ?", (old, new, "%" + old + "%"))
            c.execute("UPDATE settings SET value=? WHERE key='demo_anchor'", ((anchor + timedelta(days=delta)).isoformat(),))
    c.commit(); c.close()


def init_db():
    demo_src = os.path.join(BASE, "demo_data", "pizza-demo.db")
    if DEMO and not os.path.exists(DB_PATH) and os.path.exists(demo_src):
        shutil.copy(demo_src, DB_PATH)
        refresh_demo_dates()
    c = connect()
    c.executescript(SCHEMA)
    if not c.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        seed(c)
    c.commit()
    c.close()


def seed(c):
    for k, v in DEFAULT_SETTINGS.items():
        c.execute("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v))
    c.execute("INSERT INTO roles(name,is_admin,perms) VALUES('Owner',1,'[]')")
    c.execute("INSERT INTO roles(name,is_admin,perms) VALUES('Manager',0,?)", (json.dumps(MANAGER),))
    c.execute("INSERT INTO roles(name,is_admin,perms) VALUES('Cashier',0,?)", (json.dumps(CASHIER),))
    for u, n, r, p in [("admin", "Shop Owner", 1, "admin123"), ("manager", "Shop Manager", 2, "manager123"),
                       ("cashier", "Counter Cashier", 3, "cashier123")]:
        c.execute("INSERT INTO users(username,full_name,password_hash,role_id,created_at) VALUES(?,?,?,?,?)",
                  (u, n, generate_password_hash(p), r, now()))
    ing = {}
    for name, unit, stock, reorder, cost in [
        ("Dough", "g", 20000, 5000, 0.25), ("Pizza sauce", "g", 8000, 2000, 0.5),
        ("Mozzarella cheese", "g", 15000, 4000, 1.8), ("Chicken", "g", 10000, 2000, 1.2),
        ("Pepperoni", "g", 4000, 1000, 2.5), ("Olives", "g", 2000, 500, 1.5),
        ("Jalapenos", "g", 1500, 400, 1.0), ("Pizza box", "pcs", 300, 50, 40),
        ("Cola 345ml", "pcs", 120, 24, 55), ("Water bottle", "pcs", 100, 24, 30),
        ("Potato fries", "g", 8000, 2000, 0.35), ("Garlic bread", "pcs", 60, 15, 40)]:
        cur = c.execute("INSERT INTO ingredients(name,unit,stock,reorder_level,cost) VALUES(?,?,?,?,?)",
                        (name, unit, stock, reorder, cost))
        ing[name] = cur.lastrowid
    def item(cat, name, addons, variants):
        iid = c.execute("INSERT INTO menu_items(category,name,allow_addons) VALUES(?,?,?)", (cat, name, addons)).lastrowid
        out = []
        for label, price, recipe in variants:
            vid = c.execute("INSERT INTO variants(item_id,label,price) VALUES(?,?,?)", (iid, label, price)).lastrowid
            for k, q in recipe.items():
                c.execute("INSERT INTO recipes VALUES(?,?,?)", (vid, ing[k], q))
            out.append(vid)
    base = {"S": (150, 50, 80), "M": (250, 80, 140), "L": (350, 110, 200)}
    def pizza(name, price3, topping=None):
        vs = []
        for (lbl, key), price, tq in zip([("Small", "S"), ("Medium", "M"), ("Large", "L")], price3, (1, 1.7, 2.3)):
            d, s, ch = base[key]
            r = {"Dough": d, "Pizza sauce": s, "Mozzarella cheese": ch, "Pizza box": 1}
            if topping:
                r[topping[0]] = topping[1] * tq
            vs.append((lbl, price, r))
        item("Pizza", name, 1, vs)
    pizza("Margherita", (550, 950, 1300))
    pizza("Pepperoni", (650, 1150, 1500), ("Pepperoni", 30))
    pizza("Chicken Fajita", (650, 1100, 1450), ("Chicken", 60))
    pizza("BBQ Chicken Tikka", (650, 1100, 1450), ("Chicken", 60))
    item("Sides", "Garlic Bread", 0, [("Regular", 250, {"Garlic bread": 1})])
    item("Sides", "Fries", 0, [("Regular", 200, {"Potato fries": 150}), ("Large", 300, {"Potato fries": 250})])
    item("Drinks", "Cola 345ml", 0, [("Regular", 100, {"Cola 345ml": 1})])
    item("Drinks", "Water", 0, [("Regular", 60, {"Water bottle": 1})])
    for name, price, ingr, q in [("Extra cheese", 150, "Mozzarella cheese", 50), ("Olives", 80, "Olives", 20),
                                 ("Jalapenos", 80, "Jalapenos", 20)]:
        aid = c.execute("INSERT INTO addons(name,price) VALUES(?,?)", (name, price)).lastrowid
        c.execute("INSERT INTO addon_recipes VALUES(?,?,?)", (aid, ing[ingr], q))
    c.execute("INSERT INTO suppliers(name,phone,notes) VALUES('Fresh Dairy Supplies','0300-1111111','Cheese, sauce')")
