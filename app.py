import os
from datetime import date

import pymysql
from flask import Flask, redirect, render_template, request, url_for, session
from werkzeug.security import check_password_hash

app = Flask(__name__)

app.secret_key = os.environ["SECRET_KEY"]

def get_connection():
    # Credentials come from environment variables (or a .env file when running locally).
    # DB_SSL_CA points at Aiven's ca.pem. Leave it unset for a local MySQL with no SSL.
    return pymysql.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 3306)),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ.get("DB_NAME", "hrs"),
        ssl_ca=os.environ.get("DB_SSL_CA"),
        cursorclass=pymysql.cursors.DictCursor,
    )


def get_locations(cur):
    # Used by both pages to fill the branch dropdown
    cur.execute("SELECT LocationID, LocationName FROM tbllocations ORDER BY LocationID")
    return cur.fetchall()

def get_vendors(cur):
    # Only suppliers. AZ Renovation is a painting subcontractor, not a product source.
    cur.execute(
        "SELECT VendorID, VendorName FROM tblvendors WHERE VendorType = 'Supplier' ORDER BY VendorName"
    )
    return cur.fetchall()

@app.before_request
def require_login():
    # Let the login page and static files through; everything else needs a session.
    if request.endpoint in ("login", "static"):
        return None
    if "user" not in session:
        return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT Username, PasswordHash FROM tblstaffusers WHERE Username = %s",
                    (username,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row and check_password_hash(row["PasswordHash"], password):
            session.clear()
            session["user"] = row["Username"]
            return redirect("/")
        error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/")
def index():
    # Staff portal: links to each form
    return render_template("index.html")


@app.route("/employees")
def employees():
    # The dropdown puts the chosen branch in the URL, e.g. /employees?location_id=3
    selected = request.args.get("location_id", type=int)
    staff = []

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            locations = get_locations(cur)

            if selected:
                cur.execute(
                    """
                    SELECT EmployeeID, FirstName, LastName, JobTitle, Phone, Email, HireDate
                    FROM tblemployees
                    WHERE LocationID = %s
                    ORDER BY LastName, FirstName
                    """,
                    (selected,),
                )
                staff = cur.fetchall()
    finally:
        conn.close()

    return render_template("employees.html", locations=locations, staff=staff, selected=selected)


@app.route("/employees/add", methods=["GET", "POST"])
def add_employee():
    # GET shows the blank form. POST saves it.
    error = None

    conn = get_connection()
    try:
        if request.method == "POST":
            location_id = request.form.get("location_id", type=int)
            first_name = request.form.get("first_name", "").strip()
            last_name = request.form.get("last_name", "").strip()
            job_title = request.form.get("job_title", "").strip()
            phone = request.form.get("phone", "").strip()
            email = request.form.get("email", "").strip()
            hire_date = request.form.get("hire_date", "").strip()  # arrives as YYYY-MM-DD

            if all([location_id, first_name, last_name, job_title, phone, email, hire_date]):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tblemployees
                            (LocationID, FirstName, LastName, JobTitle, Phone, Email, HireDate)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (location_id, first_name, last_name, job_title, phone, email, hire_date),
                    )
                conn.commit()  # Nothing is saved until this runs

                # Send the user to the list for that branch so they see the new row
                return redirect(url_for("employees", location_id=location_id))

            error = "Fill in every field."

        with conn.cursor() as cur:
            locations = get_locations(cur)
    finally:
        conn.close()

    # request.form refills the boxes if there was an error, so nothing has to be retyped
    # Hire date starts as today on a blank form
    today = date.today().isoformat()
    return render_template("add_employee.html", locations=locations, error=error, form=request.form, today=today)


STATES = ["AZ", "NV", "NM"]  # HRS service area


@app.route("/customers")
def customers():
    # Optional search box puts the text in the URL, e.g. /customers?q=smith
    search = request.args.get("q", "").strip()

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sql = """
                SELECT CustomerID, FirstName, LastName, Phone, Email, Street, City, State, Zip
                FROM tblcustomers
            """
            if search:
                # The % signs mean "anything before or after", so "smi" matches "Smith"
                like = f"%{search}%"
                cur.execute(
                    sql + " WHERE FirstName LIKE %s OR LastName LIKE %s ORDER BY LastName, FirstName",
                    (like, like),
                )
            else:
                cur.execute(sql + " ORDER BY LastName, FirstName")
            rows = cur.fetchall()
    finally:
        conn.close()

    return render_template("customers.html", customers=rows, search=search)


@app.route("/customers/add", methods=["GET", "POST"])
def add_customer():
    error = None

    if request.method == "POST":
        fields = ["first_name", "last_name", "phone", "email", "street", "city", "state", "zip"]
        values = {f: request.form.get(f, "").strip() for f in fields}

        if not all(values.values()):
            error = "Fill in every field."
        elif values["state"] not in STATES:
            error = "Choose a state from the list."
        else:
            conn = get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tblcustomers
                            (FirstName, LastName, Phone, Email, Street, City, State, Zip)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        tuple(values[f] for f in fields),
                    )
                conn.commit()  # Nothing is saved until this runs
            finally:
                conn.close()

            # Show the list filtered to the new customer's last name
            return redirect(url_for("customers", q=values["last_name"]))

    return render_template("add_customer.html", states=STATES, error=error, form=request.form)

@app.route("/products")
def products():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                    """
                    SELECT ProductID, Category, Brand, ProductDescription, ModelNumber, Cost, ListPrice
                    FROM tblproducts
                    ORDER BY ProductID, Category, Brand, ProductDescription, ModelNumber, Cost, ListPrice
                    """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return render_template("products.html", products=rows)

@app.route("/products/add", methods=["GET", "POST"])
def add_products():
    error = None

    conn = get_connection()
    try:
        if request.method == "POST":
            vendor_id = request.form.get("vendor_id", type=int)
            fields = ["model_number", "brand", "product_description", "category", "cost", "list_price"]
            values = {f: request.form.get(f, "").strip() for f in fields}

            if not vendor_id or not all(values.values()):
                error = "Fill in every field."
            else:
                try:
                    float(values["cost"])
                    float(values["list_price"])
                except ValueError:
                    error = "Cost and list price must be numbers."

            if not error:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tblproducts
                            (VendorID, ModelNumber, Brand, ProductDescription, Category, Cost, ListPrice)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (vendor_id,) + tuple(values[f] for f in fields),
                    )
                conn.commit()  # Nothing is saved until this runs
                return redirect(url_for("products"))

        with conn.cursor() as cur:
            vendors = get_vendors(cur)
    finally:
        conn.close()

    return render_template("add_products.html", vendors=vendors, error=error, form=request.form)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))