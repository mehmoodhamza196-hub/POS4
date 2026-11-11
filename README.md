# Pizza POS: billing, inventory and sales

Cashier billing with opening and closing cash balance, manager inventory tools, and an owner admin panel with
permission tokens. Python + Flask + SQLite. Works offline on one PC.

## Run on a local PC

Needs Python 3.10 or newer (Windows: tick "Add Python to PATH" when installing).

- **Windows:** double-click `run.bat`
- **Mac / Linux:** `./run.sh`
- **By hand:** `python -m venv venv`, activate it, `pip install -r requirements.txt`, `python app.py`

No internet needed: the `wheels` folder holds Flask for Windows (64-bit, Python 3.10 to 3.14), and `run.bat` installs from it automatically.
If `pip` still shows an error, open a terminal in this folder and run:
`venv\Scripts\activate` then `pip install --no-index --find-links=wheels flask`

The browser opens at http://127.0.0.1:5000. The first start creates `data/pizza.db` with a sample menu.

| Login | Password | Role |
|---|---|---|
| admin | admin123 | Owner (everything) |
| manager | manager123 | Manager |
| cashier | cashier123 | Cashier |

**Change these passwords first** (top right, Account, or Admin panel, Users).

## Open it like a normal desktop app

Two ways, so you never have to use `run.bat` again:

1. **Quick (recommended):** run `run.bat` once, then double-click `make_shortcut.bat`. A **Pizza POS** icon appears on the Desktop.
   Double-click it: the app opens in its own window (Edge or Chrome app mode, no tabs, no black console window).
   Closing the window quits the program.
2. **Standalone .exe:** double-click `build_exe.bat` (works offline, uses the `wheels` folder). It creates
   `dist\PizzaPOS\PizzaPOS.exe` and a Desktop icon. Copy the whole `dist\PizzaPOS` folder to any Windows PC; Python is not needed there.
   Data is stored in a `data` folder next to the .exe. To keep existing data, copy your old `data` folder into `dist\PizzaPOS`.

## What each role does

**Cashier** (Billing and My shift)
1. Opens a shift and enters the opening cash balance.
2. Takes orders: dine-in, takeaway, delivery, sizes, extra toppings, notes, discount (up to the limit), hold and resume.
3. Takes payment: cash (with change), card, online, or a split of them.
4. Prints the receipt and a kitchen slip.
5. Sees opening balance, cash taken and "should be in drawer" live. At the end of the day enters the counted cash and gets a closing report with the difference.

**Manager**: everything a cashier does, plus menu and prices, recipes, stock-in, wastage, stock count, suppliers, refunds, discounts above the limit, all orders, today's report and every cashier's shift.

**Owner**: everything, including total sales, total orders, any date range, CSV export, profit, users, roles, settings, audit log, backups.

## Permission tokens

Admin panel, Roles and permissions: tick the tokens each role gets, or add a new role (for example Supervisor). The Owner role always has all tokens. Users get the tokens of their role.

## Good to know

- Stock goes down automatically per sale using each item's recipe (Admin, Menu and recipes, Recipe).
- Refunds put cash back out of the refunding user's drawer count and can return ingredients to stock.
- A backup is made daily and whenever a shift closes (`data/backups`). Copy that folder to a USB drive or cloud folder too.
- Settings: shop name, tax rate (default 16%, set yours), cashier discount limit, receipt text, shift on or off.
- Receipt printing uses the browser print window. For a thermal printer choose paper "80 mm", margins none, headers and footers off.
- Other devices on the same Wi-Fi: start with `HOST=0.0.0.0 python app.py` (Windows: `set HOST=0.0.0.0` first), then open `http://<this-PC-IP>:5000`.
  Only do this on a trusted network.

## Make a Windows .exe

```
pip install pyinstaller
pyinstaller --onefile --name PizzaPOS --add-data "templates;templates" --add-data "static;static" app.py
```
Keep the `data` folder next to the .exe.

## Run on a virtual machine (Ubuntu)

SQLite is fine for one shop on one VM. With Docker:
```
docker compose up -d --build        # app on port 8000, data kept in ./data
```
Put Nginx with HTTPS in front before exposing it to the internet. Without Docker:
`pip install -r requirements.txt` then `gunicorn -w 1 --threads 4 -b 127.0.0.1:8000 app:app`.
A receipt printer attached to the shop PC still works, because printing happens in the browser there.
Moving to PostgreSQL would need the database code changed to SQLAlchemy; it is not included.

## Put it on GitHub and Vercel (live demo)

The project already includes everything Vercel needs (`api/index.py`, `vercel.json`, `.vercelignore`, `.gitignore`) and a ready demo
database (`demo_data/pizza-demo.db`: one week of sample sales). On Vercel it starts in **demo mode**: a yellow demo banner, and one-click
Cashier / Manager / Owner buttons on the login page.

**1. GitHub** (create an empty repository on github.com first)
```
git init
git add .
git commit -m "Pizza POS"
git branch -M main
git remote add origin https://github.com/YOUR-NAME/pizza-pos.git
git push -u origin main
```

**2. Vercel**
1. vercel.com, Add New, Project, import the GitHub repository.
2. Leave the defaults (Framework: Other). Under Environment Variables add `SECRET_KEY` with any long random text.
3. Press Deploy. Share the link it gives you.

(CLI alternative: `npm i -g vercel`, then `vercel` and `vercel --prod` in this folder.)

**Important limits of a Vercel demo**
- Vercel has no permanent disk. Demo data lives in a temporary folder, so anything entered **resets** when the server restarts or you redeploy,
  and on a busy day two visitors may even hit different server copies. Use it for demos only, never for a real shop.
- The sample sales dates move forward automatically, so the demo always shows "the last 7 days".
- For real use run it on the shop PC (this README, top), or on a VM / a host with a persistent disk (see the VM section).
  A permanent cloud database (for example Postgres) would need code changes and is not included.
- To regenerate the demo data: `python tools/make_demo_db.py`, then commit the new `demo_data/pizza-demo.db`.
- Set `DEMO_MODE=1` to try demo mode on your own PC.

**If the Vercel site shows "404 Page not found"**: open `https://YOUR-SITE.vercel.app/healthz`. It should show `"ok": true` and `"database_ready": true`.
Then open `/login` directly. After changing files, run `git add .`, `git commit -m "fix"`, `git push`; Vercel redeploys by itself.
