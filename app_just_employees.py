import os
from datetime import date

import pymysql
from flask import Flask, redirect, render_template, request, url_for

app = Flask(__name__)


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


@app.route("/")
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
