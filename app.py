import os
from datetime import date

import pymysql
from flask import Flask, redirect, render_template, request, url_for, session
from werkzeug.security import check_password_hash
from reports import reports_bp

app = Flask(__name__)

app.secret_key = os.environ["SECRET_KEY"]
app.register_blueprint(reports_bp)

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
app.get_connection = get_connection

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

ORDER_STATUSES = ["Open", "Delivered", "Cancelled"]  # Matches the enum on tblorders


def get_customers(cur):
    # Fills the customer dropdown on the new order form
    cur.execute("SELECT CustomerID, FirstName, LastName FROM tblcustomers ORDER BY LastName, FirstName")
    return cur.fetchall()


def get_employees(cur):
    # Fills the salesperson dropdown on the new order form
    cur.execute("SELECT EmployeeID, FirstName, LastName FROM tblemployees ORDER BY LastName, FirstName")
    return cur.fetchall()


def get_products(cur):
    # Fills the product dropdown when adding a line to an order
    cur.execute(
        "SELECT ProductID, Brand, ProductDescription, ModelNumber, ListPrice FROM tblproducts ORDER BY Brand, ProductDescription"
    )
    return cur.fetchall()


@app.route("/orders")
def orders():
    # The dropdown puts the chosen status in the URL, e.g. /orders?status=Open
    status = request.args.get("status", "").strip()
    if status not in ORDER_STATUSES:
        status = ""

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # The subquery adds up each order's line items so every order is one row
            sql = """
                SELECT o.OrderID, o.OrderDate, o.OrderStatus, o.TaxRate, o.DeliveryCharge,
                       c.FirstName AS CustFirst, c.LastName AS CustLast,
                       l.LocationName,
                       e.FirstName AS EmpFirst, e.LastName AS EmpLast,
                       COALESCE(d.ItemCount, 0) AS ItemCount,
                       COALESCE(d.Subtotal, 0) AS Subtotal
                FROM tblorders o
                JOIN tblcustomers c ON c.CustomerID = o.CustomerID
                JOIN tbllocations l ON l.LocationID = o.LocationID
                LEFT JOIN tblemployees e ON e.EmployeeID = o.EmployeeID
                LEFT JOIN (
                    SELECT OrderID, SUM(Quantity) AS ItemCount, SUM(Quantity * UnitPrice) AS Subtotal
                    FROM tblorderdetails
                    GROUP BY OrderID
                ) d ON d.OrderID = o.OrderID
            """
            if status:
                cur.execute(sql + " WHERE o.OrderStatus = %s ORDER BY o.OrderDate DESC, o.OrderID DESC", (status,))
            else:
                cur.execute(sql + " ORDER BY o.OrderDate DESC, o.OrderID DESC")
            rows = cur.fetchall()
    finally:
        conn.close()

    # Total = items + sales tax on the items + delivery
    for o in rows:
        o["Total"] = o["Subtotal"] * (1 + o["TaxRate"]) + o["DeliveryCharge"]

    return render_template("orders.html", orders=rows, statuses=ORDER_STATUSES, status=status)


@app.route("/orders/add", methods=["GET", "POST"])
def add_order():
    # Creates the order header. Line items are added on the order's own page.
    error = None

    conn = get_connection()
    try:
        if request.method == "POST":
            customer_id = request.form.get("customer_id", type=int)
            location_id = request.form.get("location_id", type=int)
            employee_id = request.form.get("employee_id", type=int)  # Optional, can be blank
            order_date = request.form.get("order_date", "").strip()  # arrives as YYYY-MM-DD
            tax_rate = request.form.get("tax_rate", "").strip()
            delivery_charge = request.form.get("delivery_charge", "").strip()

            if not all([customer_id, location_id, order_date, tax_rate, delivery_charge]):
                error = "Fill in every field. Only the salesperson is optional."
            else:
                try:
                    # The form takes tax as a percent (6.5), the table stores a fraction (0.065)
                    tax_fraction = float(tax_rate) / 100
                    float(delivery_charge)
                except ValueError:
                    error = "Tax rate and delivery charge must be numbers."

            if not error:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tblorders
                            (CustomerID, LocationID, EmployeeID, OrderDate, OrderStatus, TaxRate, DeliveryCharge)
                        VALUES (%s, %s, %s, %s, 'Open', %s, %s)
                        """,
                        (customer_id, location_id, employee_id, order_date, tax_fraction, delivery_charge),
                    )
                    order_id = cur.lastrowid
                conn.commit()  # Nothing is saved until this runs

                # Go straight to the new order so the user can add line items
                return redirect(url_for("order_detail", order_id=order_id))

        with conn.cursor() as cur:
            customers = get_customers(cur)
            locations = get_locations(cur)
            employees = get_employees(cur)
    finally:
        conn.close()

    # Order date starts as today on a blank form
    today = date.today().isoformat()
    return render_template(
        "add_order.html",
        customers=customers, locations=locations, employees=employees,
        error=error, form=request.form, today=today,
    )


@app.route("/orders/<int:order_id>", methods=["GET", "POST"])
def order_detail(order_id):
    # GET shows the order. POST either adds a line item or changes the status,
    # depending on which form's button was pressed (the "action" field).
    error = None

    conn = get_connection()
    try:
        if request.method == "POST":
            action = request.form.get("action")

            if action == "add_item":
                product_id = request.form.get("product_id", type=int)
                quantity = request.form.get("quantity", type=int)
                unit_price = request.form.get("unit_price", "").strip()

                if not product_id or not quantity or quantity < 1:
                    error = "Choose a product and a quantity of at least 1."
                else:
                    with conn.cursor() as cur:
                        if unit_price:
                            try:
                                float(unit_price)
                            except ValueError:
                                error = "Unit price must be a number."
                        else:
                            # Blank price means charge the product's current list price
                            cur.execute("SELECT ListPrice FROM tblproducts WHERE ProductID = %s", (product_id,))
                            row = cur.fetchone()
                            if row:
                                unit_price = row["ListPrice"]
                            else:
                                error = "Choose a product from the list."

                        if not error:
                            cur.execute(
                                """
                                INSERT INTO tblorderdetails (OrderID, ProductID, Quantity, UnitPrice)
                                VALUES (%s, %s, %s, %s)
                                """,
                                (order_id, product_id, quantity, unit_price),
                            )
                    if not error:
                        conn.commit()  # Nothing is saved until this runs
                        return redirect(url_for("order_detail", order_id=order_id))

            elif action == "set_status":
                new_status = request.form.get("status", "")
                if new_status in ORDER_STATUSES:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE tblorders SET OrderStatus = %s WHERE OrderID = %s",
                            (new_status, order_id),
                        )
                    conn.commit()
                    return redirect(url_for("order_detail", order_id=order_id))
                error = "Choose a status from the list."

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT o.OrderID, o.OrderDate, o.OrderStatus, o.TaxRate, o.DeliveryCharge,
                       c.FirstName AS CustFirst, c.LastName AS CustLast, c.Phone, c.Email,
                       c.Street, c.City, c.State, c.Zip,
                       l.LocationName,
                       e.FirstName AS EmpFirst, e.LastName AS EmpLast
                FROM tblorders o
                JOIN tblcustomers c ON c.CustomerID = o.CustomerID
                JOIN tbllocations l ON l.LocationID = o.LocationID
                LEFT JOIN tblemployees e ON e.EmployeeID = o.EmployeeID
                WHERE o.OrderID = %s
                """,
                (order_id,),
            )
            order = cur.fetchone()
            if not order:
                return "Order not found", 404

            cur.execute(
                """
                SELECT d.OrderDetailID, d.Quantity, d.UnitPrice,
                       p.Brand, p.ProductDescription, p.ModelNumber
                FROM tblorderdetails d
                JOIN tblproducts p ON p.ProductID = d.ProductID
                WHERE d.OrderID = %s
                ORDER BY d.OrderDetailID
                """,
                (order_id,),
            )
            items = cur.fetchall()
            products = get_products(cur)
    finally:
        conn.close()

    subtotal = sum(i["Quantity"] * i["UnitPrice"] for i in items)
    tax = subtotal * order["TaxRate"]
    total = subtotal + tax + order["DeliveryCharge"]

    return render_template(
        "order_detail.html",
        order=order, items=items, products=products, statuses=ORDER_STATUSES,
        subtotal=subtotal, tax=tax, total=total, error=error, form=request.form,
    )


JOB_STATUSES = ["Out for Bid", "Accepted", "Declined", "In Progress", "Complete"]  # Matches the enum on tbljobs


def get_services(cur):
    # Fills the service dropdown when adding a line to a job
    cur.execute("SELECT ServiceID, ServiceName, HourlyRate FROM tblservices ORDER BY ServiceName")
    return cur.fetchall()


@app.route("/jobs")
def jobs():
    # The dropdown puts the chosen status in the URL, e.g. /jobs?status=In+Progress
    status = request.args.get("status", "").strip()
    if status not in JOB_STATUSES:
        status = ""

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # The subquery adds up each job's services so every job is one row.
            # Lines with no hours or rate (e.g. subcontracted painting) count as 0.
            sql = """
                SELECT j.JobID, j.JobDescription, j.JobStatus, j.BidDate, j.StartDate, j.CompletionDate,
                       c.FirstName AS CustFirst, c.LastName AS CustLast,
                       l.LocationName,
                       e.FirstName AS EmpFirst, e.LastName AS EmpLast,
                       COALESCE(d.Estimate, 0) AS Estimate
                FROM tbljobs j
                JOIN tblcustomers c ON c.CustomerID = j.CustomerID
                JOIN tbllocations l ON l.LocationID = j.LocationID
                LEFT JOIN tblemployees e ON e.EmployeeID = j.EmployeeID
                LEFT JOIN (
                    SELECT JobID, SUM(COALESCE(EstimatedHours * RateCharged, 0)) AS Estimate
                    FROM tbljobdetails
                    GROUP BY JobID
                ) d ON d.JobID = j.JobID
            """
            if status:
                cur.execute(sql + " WHERE j.JobStatus = %s ORDER BY j.BidDate DESC, j.JobID DESC", (status,))
            else:
                cur.execute(sql + " ORDER BY j.BidDate DESC, j.JobID DESC")
            rows = cur.fetchall()
    finally:
        conn.close()

    return render_template("jobs.html", jobs=rows, statuses=JOB_STATUSES, status=status)


@app.route("/jobs/add", methods=["GET", "POST"])
def add_job():
    # Creates the job header. Services are added on the job's own page.
    error = None

    conn = get_connection()
    try:
        if request.method == "POST":
            customer_id = request.form.get("customer_id", type=int)
            location_id = request.form.get("location_id", type=int)
            employee_id = request.form.get("employee_id", type=int)  # Optional, can be blank
            description = request.form.get("job_description", "").strip()
            bid_date = request.form.get("bid_date", "").strip()  # arrives as YYYY-MM-DD

            if all([customer_id, location_id, description, bid_date]):
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO tbljobs
                            (CustomerID, LocationID, EmployeeID, JobDescription, JobStatus, BidDate)
                        VALUES (%s, %s, %s, %s, 'Out for Bid', %s)
                        """,
                        (customer_id, location_id, employee_id, description, bid_date),
                    )
                    job_id = cur.lastrowid
                conn.commit()  # Nothing is saved until this runs

                # Go straight to the new job so the user can add services
                return redirect(url_for("job_detail", job_id=job_id))

            error = "Fill in every field. Only the salesperson is optional."

        with conn.cursor() as cur:
            customers = get_customers(cur)
            locations = get_locations(cur)
            employees = get_employees(cur)
    finally:
        conn.close()

    # Bid date starts as today on a blank form
    today = date.today().isoformat()
    return render_template(
        "add_job.html",
        customers=customers, locations=locations, employees=employees,
        error=error, form=request.form, today=today,
    )


@app.route("/jobs/<int:job_id>", methods=["GET", "POST"])
def job_detail(job_id):
    # GET shows the job. POST either adds a service line or changes the status,
    # depending on which form's button was pressed (the "action" field).
    error = None

    conn = get_connection()
    try:
        if request.method == "POST":
            action = request.form.get("action")

            if action == "add_service":
                service_id = request.form.get("service_id", type=int)
                work_area = request.form.get("work_area", "").strip() or None
                hours = request.form.get("estimated_hours", "").strip() or None
                rate = request.form.get("rate_charged", "").strip() or None

                if not service_id:
                    error = "Choose a service."
                else:
                    try:
                        if hours is not None:
                            float(hours)
                        if rate is not None:
                            float(rate)
                    except ValueError:
                        error = "Hours and rate must be numbers."

                if not error:
                    with conn.cursor() as cur:
                        cur.execute("SELECT HourlyRate FROM tblservices WHERE ServiceID = %s", (service_id,))
                        row = cur.fetchone()
                        if not row:
                            error = "Choose a service from the list."
                        else:
                            # Blank rate means charge the service's standard hourly rate.
                            # Subcontracted services have no rate, so it stays blank.
                            if rate is None:
                                rate = row["HourlyRate"]
                            cur.execute(
                                """
                                INSERT INTO tbljobdetails (JobID, ServiceID, WorkArea, EstimatedHours, RateCharged)
                                VALUES (%s, %s, %s, %s, %s)
                                """,
                                (job_id, service_id, work_area, hours, rate),
                            )
                    if not error:
                        conn.commit()  # Nothing is saved until this runs
                        return redirect(url_for("job_detail", job_id=job_id))

            elif action == "set_status":
                new_status = request.form.get("status", "")
                if new_status in JOB_STATUSES:
                    with conn.cursor() as cur:
                        # Fill in the start or completion date with today the first time
                        # the job reaches that stage. Dates already set are left alone.
                        cur.execute(
                            """
                            UPDATE tbljobs
                            SET JobStatus = %s,
                                StartDate = CASE WHEN %s IN ('In Progress', 'Complete')
                                                 THEN COALESCE(StartDate, CURDATE()) ELSE StartDate END,
                                CompletionDate = CASE WHEN %s = 'Complete'
                                                      THEN COALESCE(CompletionDate, CURDATE()) ELSE CompletionDate END
                            WHERE JobID = %s
                            """,
                            (new_status, new_status, new_status, job_id),
                        )
                    conn.commit()
                    return redirect(url_for("job_detail", job_id=job_id))
                error = "Choose a status from the list."

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.JobID, j.JobDescription, j.JobStatus, j.BidDate, j.StartDate, j.CompletionDate,
                       c.FirstName AS CustFirst, c.LastName AS CustLast, c.Phone, c.Email,
                       c.Street, c.City, c.State, c.Zip,
                       l.LocationName,
                       e.FirstName AS EmpFirst, e.LastName AS EmpLast
                FROM tbljobs j
                JOIN tblcustomers c ON c.CustomerID = j.CustomerID
                JOIN tbllocations l ON l.LocationID = j.LocationID
                LEFT JOIN tblemployees e ON e.EmployeeID = j.EmployeeID
                WHERE j.JobID = %s
                """,
                (job_id,),
            )
            job = cur.fetchone()
            if not job:
                return "Job not found", 404

            cur.execute(
                """
                SELECT d.JobDetailID, d.WorkArea, d.EstimatedHours, d.RateCharged,
                       s.ServiceName, v.VendorName
                FROM tbljobdetails d
                JOIN tblservices s ON s.ServiceID = d.ServiceID
                LEFT JOIN tblvendors v ON v.VendorID = s.VendorID
                WHERE d.JobID = %s
                ORDER BY d.JobDetailID
                """,
                (job_id,),
            )
            lines = cur.fetchall()
            services = get_services(cur)
    finally:
        conn.close()

    # A line only has a total when it has both hours and a rate
    for line in lines:
        if line["EstimatedHours"] is not None and line["RateCharged"] is not None:
            line["LineTotal"] = line["EstimatedHours"] * line["RateCharged"]
        else:
            line["LineTotal"] = None
    estimate = sum(line["LineTotal"] for line in lines if line["LineTotal"] is not None)
    unpriced = any(line["LineTotal"] is None for line in lines)

    return render_template(
        "job_detail.html",
        job=job, lines=lines, services=services, statuses=JOB_STATUSES,
        estimate=estimate, unpriced=unpriced, error=error, form=request.form,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))