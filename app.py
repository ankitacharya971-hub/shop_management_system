from flask import Flask, render_template, request, redirect, session, jsonify, send_file
import sqlite3
import random
import datetime
import requests
from datetime import timedelta
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__)
app.secret_key = "supersecretkey"

# ================= SESSION SETTINGS =================
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=180)
app.config["SESSION_REFRESH_EACH_REQUEST"] = True


# ================= DATABASE =================
def get_conn():
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mobile TEXT UNIQUE
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_mobile TEXT,
        name TEXT,
        main_unit TEXT,
        alt_unit TEXT,
        conversion REAL,
        stock_alt REAL,
        purchase_price_main REAL,
        purchase_price_alt REAL,
        min_price_main REAL,
        min_price_alt REAL,
        low_stock_limit_alt REAL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_mobile TEXT,
        customer TEXT,
        total REAL,
        payment TEXT,
        due_date TEXT,
        date TEXT
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS sale_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sale_id INTEGER,
        product_id INTEGER,
        sale_unit TEXT,
        qty REAL,
        base_qty REAL,
        price REAL
    )
    """)

    c.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_mobile TEXT,
        customer TEXT,
        type TEXT,
        amount REAL,
        date TEXT
    )
    """)

    conn.commit()
    conn.close()


init_db()


# ================= SESSION AUTO REFRESH =================
@app.before_request
def make_session_permanent():
    if session.get("logged_in"):
        session.permanent = True


# ================= HELPERS =================
def require_login():
    return session.get("logged_in") and session.get("mobile")


def safe_float(value, default=0):
    try:
        return float(value)
    except:
        return default


def format_stock_parts(stock_alt, conversion):
    conversion = float(conversion or 1)
    stock_alt = float(stock_alt or 0)

    if conversion <= 0:
        conversion = 1

    main_qty = int(stock_alt // conversion)
    alt_qty = round(stock_alt - (main_qty * conversion), 2)

    if isinstance(alt_qty, float) and alt_qty.is_integer():
        alt_qty = int(alt_qty)

    return main_qty, alt_qty


def format_stock_text(stock_alt, conversion, main_unit, alt_unit):
    main_qty, alt_qty = format_stock_parts(stock_alt, conversion)
    return f"{main_qty} {main_unit} {alt_qty} {alt_unit}"


def enrich_product(product):
    product = dict(product)

    product["stock_text"] = format_stock_text(
        product["stock_alt"],
        product["conversion"],
        product["main_unit"],
        product["alt_unit"]
    )

    main_qty, alt_qty = format_stock_parts(product["stock_alt"], product["conversion"])
    product["stock_main_part"] = main_qty
    product["stock_alt_part"] = alt_qty

    return product


# ================= OTP SMS =================
def send_otp_sms(mobile, otp):
    api_key = "YOUR_FAST2SMS_API_KEY"

    if api_key == "YOUR_FAST2SMS_API_KEY":
        print("OTP:", otp)
        return

    url = "https://www.fast2sms.com/dev/bulkV2"
    payload = {
        "route": "otp",
        "variables_values": otp,
        "numbers": mobile
    }
    headers = {
        "authorization": api_key
    }

    try:
        response = requests.post(url, data=payload, headers=headers, timeout=15)
        print("SMS Response:", response.text)
    except Exception as e:
        print("SMS Error:", e)
        print("OTP:", otp)


# ================= LOGIN =================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/send_otp", methods=["POST"])
def send_otp():
    mobile = request.form.get("mobile", "").strip()

    if not mobile:
        return "Mobile number required"

    otp = str(random.randint(1000, 9999))
    session["otp"] = otp
    session["mobile"] = mobile

    send_otp_sms(mobile, otp)
    return render_template("otp.html")


@app.route("/verify_otp", methods=["POST"])
def verify():
    user_otp = request.form.get("otp", "").strip()

    if user_otp == session.get("otp"):
        session["logged_in"] = True
        session["mobile"] = session.get("mobile")
        session.permanent = True

        mobile = session.get("mobile")

        conn = get_conn()
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (mobile) VALUES (?)", (mobile,))
        conn.commit()
        conn.close()

        return redirect("/dashboard")

    return "Wrong OTP ❌"


# ================= DASHBOARD =================
@app.route("/dashboard")
def dashboard():
    if not require_login():
        return redirect("/")
    return render_template("dashboard.html")


# ================= STOCK =================
@app.route("/stock")
def stock():
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE user_mobile=? ORDER BY id DESC", (mobile,))
    rows = c.fetchall()
    conn.close()

    products = [enrich_product(row) for row in rows]
    return render_template("stock.html", products=products)


@app.route("/add_stock", methods=["POST"])
def add_stock():
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")
    data = request.form

    name = data.get("name", "").strip()
    main_unit = data.get("main_unit", "").strip()
    alt_unit = data.get("alt_unit", "").strip()

    if not name or not main_unit or not alt_unit:
        return "Product name, main unit, alt unit required"

    conversion = safe_float(data.get("conversion"), 1)
    opening_main_stock = safe_float(data.get("main_stock"), 0)
    opening_alt_stock = safe_float(data.get("alt_stock"), 0)
    purchase_price_main = safe_float(data.get("purchase_price_main"), 0)
    min_price_main = safe_float(data.get("min_price_main"), 0)
    low_stock_limit_alt = safe_float(data.get("low_stock_limit_alt"), 0)

    if conversion <= 0:
        return "Conversion must be greater than 0"

    total_added_alt = (opening_main_stock * conversion) + opening_alt_stock
    purchase_price_alt = purchase_price_main / conversion
    min_price_alt = min_price_main / conversion

    conn = get_conn()
    c = conn.cursor()

    c.execute(
        "SELECT * FROM products WHERE name=? AND user_mobile=?",
        (name, mobile)
    )
    product = c.fetchone()

    if product:
        new_stock_alt = float(product["stock_alt"]) + total_added_alt

        c.execute("""
            UPDATE products
            SET
                main_unit=?,
                alt_unit=?,
                conversion=?,
                stock_alt=?,
                purchase_price_main=?,
                purchase_price_alt=?,
                min_price_main=?,
                min_price_alt=?,
                low_stock_limit_alt=?
            WHERE id=?
        """, (
            main_unit,
            alt_unit,
            conversion,
            new_stock_alt,
            purchase_price_main,
            purchase_price_alt,
            min_price_main,
            min_price_alt,
            low_stock_limit_alt,
            product["id"]
        ))
    else:
        c.execute("""
            INSERT INTO products
            (user_mobile, name, main_unit, alt_unit, conversion, stock_alt,
             purchase_price_main, purchase_price_alt, min_price_main, min_price_alt, low_stock_limit_alt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            mobile,
            name,
            main_unit,
            alt_unit,
            conversion,
            total_added_alt,
            purchase_price_main,
            purchase_price_alt,
            min_price_main,
            min_price_alt,
            low_stock_limit_alt
        ))

    conn.commit()
    conn.close()

    return redirect("/stock")


# ================= BILLING =================
@app.route("/billing")
def billing():
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE user_mobile=? ORDER BY name ASC", (mobile,))
    rows = c.fetchall()
    conn.close()

    products = [enrich_product(row) for row in rows]
    return render_template("billing.html", products=products)


@app.route("/save_bill", methods=["POST"])
def save_bill():
    if not require_login():
        return jsonify({"status": "error", "message": "Login required"}), 401

    mobile = session.get("mobile")
    data = request.json

    if not data or not data.get("items"):
        return jsonify({"status": "error", "message": "No items in cart"}), 400

    customer = data.get("customer", "Walk-in Customer").strip() or "Walk-in Customer"
    payment = data.get("payment", "Cash")
    today = str(datetime.date.today())

    conn = get_conn()
    c = conn.cursor()

    total = 0.0
    validated_items = []

    for item in data["items"]:
        try:
            product_id = int(item["id"])
            qty = float(item["qty"])
            price = float(item["price"])
            sale_unit = item.get("sale_unit", "alt").strip().lower()
        except (ValueError, KeyError, TypeError):
            conn.close()
            return jsonify({"status": "error", "message": "Invalid item data"}), 400

        c.execute("SELECT * FROM products WHERE id=? AND user_mobile=?", (product_id, mobile))
        product = c.fetchone()

        if not product:
            conn.close()
            return jsonify({"status": "error", "message": "Product not found"}), 400

        if qty <= 0 or price <= 0:
            conn.close()
            return jsonify({"status": "error", "message": "Invalid qty or price"}), 400

        if sale_unit not in ["main", "alt"]:
            conn.close()
            return jsonify({"status": "error", "message": "Invalid sale unit"}), 400

        if float(product["conversion"]) <= 0:
            conn.close()
            return jsonify({"status": "error", "message": f"Invalid conversion for {product['name']}"}), 400

        if sale_unit == "main":
            base_qty = qty * float(product["conversion"])
        else:
            base_qty = qty

        if float(product["stock_alt"]) < base_qty:
            conn.close()
            return jsonify({
                "status": "error",
                "message": f"Insufficient stock for {product['name']}. Available: {format_stock_text(product['stock_alt'], product['conversion'], product['main_unit'], product['alt_unit'])}"
            }), 400

        line_total = qty * price
        total += line_total

        validated_items.append({
            "product_id": product_id,
            "qty": qty,
            "price": price,
            "sale_unit": sale_unit,
            "base_qty": base_qty
        })

    c.execute("""
        INSERT INTO sales (user_mobile, customer, total, payment, date)
        VALUES (?, ?, ?, ?, ?)
    """, (mobile, customer, total, payment, today))

    sale_id = c.lastrowid

    for item in validated_items:
        c.execute("""
            INSERT INTO sale_items (sale_id, product_id, sale_unit, qty, base_qty, price)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            sale_id,
            item["product_id"],
            item["sale_unit"],
            item["qty"],
            item["base_qty"],
            item["price"]
        ))

        c.execute("""
            UPDATE products
            SET stock_alt = stock_alt - ?
            WHERE id=? AND user_mobile=?
        """, (item["base_qty"], item["product_id"], mobile))

    if payment == "Udhar":
        c.execute("""
        INSERT INTO transactions (user_mobile, customer, type, amount, date)
        VALUES (?, ?, 'udhar', ?, ?)
        """, (mobile, customer, total, today))

    conn.commit()
    conn.close()

    return jsonify({"status": "success", "sale_id": sale_id})


# ================= PAYMENT / LEDGER =================
@app.route("/add_payment", methods=["POST"])
def add_payment():
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")
    customer = request.form.get("customer", "").strip()
    amount = safe_float(request.form.get("amount"), 0)
    today = str(datetime.date.today())

    if not customer or amount <= 0:
        return redirect(f"/udhar_details/{customer}")

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    INSERT INTO transactions (user_mobile, customer, type, amount, date)
    VALUES (?, ?, 'payment', ?, ?)
    """, (mobile, customer, amount, today))

    conn.commit()
    conn.close()

    return redirect(f"/udhar_details/{customer}")


# ================= UDHAR =================
@app.route("/udhar")
def udhar():
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    SELECT DISTINCT customer
    FROM transactions
    WHERE user_mobile=?
    ORDER BY customer ASC
    """, (mobile,))
    customers = [row[0] for row in c.fetchall()]

    conn.close()
    return render_template("udhar.html", customers=customers)


@app.route("/udhar_details/<customer>")
def udhar_details(customer):
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
    SELECT *
    FROM transactions
    WHERE user_mobile=? AND customer=?
    ORDER BY date ASC, id ASC
    """, (mobile, customer))
    rows = c.fetchall()

    balance = 0
    data = []

    for r in rows:
        amount = float(r["amount"])
        tx_type = r["type"]

        if tx_type == "udhar":
            balance += amount
        else:
            balance -= amount

        data.append({
            "date": r["date"],
            "type": tx_type,
            "amount": round(amount, 2),
            "balance": round(balance, 2)
        })

    current_balance = round(balance, 2)

    conn.close()
    return render_template(
        "udhar_details.html",
        customer=customer,
        data=data,
        current_balance=current_balance
    )


# ================= LOW STOCK =================
@app.route("/low_stock")
def low_stock():
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM products
        WHERE user_mobile=? AND stock_alt <= low_stock_limit_alt
        ORDER BY stock_alt ASC
    """, (mobile,))
    rows = c.fetchall()
    conn.close()

    products = [enrich_product(row) for row in rows]
    return render_template("low_stock.html", products=products)


# ================= PROFIT =================
@app.route("/profit")
def profit():
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")
    today = str(datetime.date.today())

    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        SELECT SUM((sale_items.price * sale_items.qty) - (sale_items.base_qty * products.purchase_price_alt))
        FROM sale_items
        JOIN products ON sale_items.product_id = products.id
        JOIN sales ON sale_items.sale_id = sales.id
        WHERE sales.user_mobile = ? AND sales.date = ?
    """, (mobile, today))

    total_profit = c.fetchone()[0]
    conn.close()

    return render_template("profit.html", profit=round(total_profit or 0, 2))


# ================= HTML INVOICE =================
@app.route("/invoice_html/<int:sale_id>")
def invoice_html(sale_id):
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")

    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT * FROM sales WHERE id=? AND user_mobile=?", (sale_id, mobile))
    sale = c.fetchone()

    if not sale:
        conn.close()
        return "Invoice not found"

    c.execute("""
        SELECT
            products.name,
            sale_items.sale_unit,
            sale_items.qty,
            sale_items.price,
            products.main_unit,
            products.alt_unit
        FROM sale_items
        JOIN products ON sale_items.product_id = products.id
        WHERE sale_items.sale_id=?
    """, (sale_id,))
    rows = c.fetchall()
    conn.close()

    items = []
    for row in rows:
        unit_label = row["main_unit"] if row["sale_unit"] == "main" else row["alt_unit"]
        items.append({
            "name": row["name"],
            "qty": row["qty"],
            "price": row["price"],
            "unit_label": unit_label,
            "line_total": round(float(row["qty"]) * float(row["price"]), 2)
        })

    return render_template(
        "invoice.html",
        customer=sale["customer"],
        date=sale["date"],
        items=items,
        total=round(float(sale["total"]), 2)
    )


# ================= PDF INVOICE =================
@app.route("/invoice_pdf/<int:sale_id>")
def invoice_pdf(sale_id):
    if not require_login():
        return redirect("/")

    mobile = session.get("mobile")

    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT * FROM sales WHERE id=? AND user_mobile=?", (sale_id, mobile))
    sale = c.fetchone()

    if not sale:
        conn.close()
        return "Invoice not found"

    c.execute("""
        SELECT
            products.name,
            sale_items.sale_unit,
            sale_items.qty,
            sale_items.price,
            products.main_unit,
            products.alt_unit
        FROM sale_items
        JOIN products ON sale_items.product_id = products.id
        WHERE sale_items.sale_id=?
    """, (sale_id,))
    rows = c.fetchall()

    conn.close()

    file_name = f"invoice_{sale_id}.pdf"
    doc = SimpleDocTemplate(file_name)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("ACHARYA HARDWARE & ELECTRICALS", styles["Title"]))
    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Customer: {sale['customer']}", styles["Normal"]))
    elements.append(Paragraph(f"Date: {sale['date']}", styles["Normal"]))
    elements.append(Spacer(1, 10))

    for row in rows:
        unit_label = row["main_unit"] if row["sale_unit"] == "main" else row["alt_unit"]
        line = f"{row['name']} | Qty: {row['qty']} {unit_label} | ₹ {row['price']}"
        elements.append(Paragraph(line, styles["Normal"]))

    elements.append(Spacer(1, 10))
    elements.append(Paragraph(f"Total: ₹ {round(float(sale['total']), 2)}", styles["Heading2"]))

    doc.build(elements)
    return send_file(file_name, as_attachment=False)


# ================= LOGOUT =================
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# ================= RUN =================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)