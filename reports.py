from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, request, abort, current_app
import calendar

def execute(sql: str, args: dict|None=None):
    connection = current_app.get_connection()

    if args is None:
        args = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, args)
            return cursor.fetchall()
    finally:
        connection.close()
reports_bp = Blueprint('reports', __name__, url_prefix='/reports')

@reports_bp.route('/')
def index():
    return render_template('reports/index.html')

@reports_bp.route('/customer')
def customer():
    customers = execute("""
        SELECT
            *
        FROM tblcustomers
    """)
    purchased_products = execute("""
        SELECT
            customer_products.CustomerID,
            customer_products.Quantity,
            p.Brand,
            p.ModelNumber,
            p.ProductDescription,
            p.Category
        FROM tblproducts p
        JOIN (    
            SELECT
                p.ProductID,
                o.CustomerID,
                SUM(od.Quantity) AS Quantity
            FROM tblproducts p
            JOIN tblorderdetails od ON p.ProductID = od.ProductID
            JOIN tblorders o ON od.OrderID = o.OrderID
            GROUP BY p.ProductID, o.CustomerID
        ) customer_products ON p.ProductID = customer_products.ProductID
    """)
    products_by_customer = {}
    for p in purchased_products:
        products_by_customer.setdefault(p.pop('CustomerID'), []).append(p)

    return render_template('reports/customer.html', customers=customers, products_by_customer=products_by_customer)

def get_delivery_cost(p, default_delivery_cost=50, large_surcharge=100):
    """
    Returns delivery cost of a product
    based on its category and if it's a large item.
    """
    return ({
        'Appliances': 75,
        'Cabinets': 75,
        'Sinks & Faucets': 50,
        'Lighting': 25,
        'Countertops & Tile': 200
    }.get(p['Category'], default_delivery_cost)
    + (large_surcharge * p['IsLargeItem']))

@reports_bp.route('/product')
def product():
    products = execute("""
        SELECT
            p.*,
            COALESCE(SoldQuantity, 0) as SoldQuantity,
            COALESCE(OnHandQuantity, 0) as OnHandQuantity            
        FROM tblproducts p
        LEFT JOIN (
            SELECT
                ProductID,
                SUM(Quantity) AS SoldQuantity
            FROM tblorderdetails
            GROUP BY ProductID
        ) products_sold ON p.ProductID = products_sold.ProductID
        LEFT JOIN (
            SELECT
                ProductID,
                COUNT(InventoryItemID) AS OnHandQuantity
            FROM tblinventoryitems
            WHERE ItemStatus = 'In Stock'
            GROUP BY ProductID
        ) on_hand_products ON p.ProductID = on_hand_products.ProductID
    """)
    for p in products:
        p['DeliveryCost'] = get_delivery_cost(p)
    return render_template('reports/product.html', products=products)

@reports_bp.route('/sales')
def sales_summary():
    filters = []
    args = {}
    if request.args.get("start"):
        filters.append("AND OrderDate >= %(start)s")
        args['start'] = request.args.get("start")
    if request.args.get("end"):
        filters.append("AND OrderDate <= %(end)s")
        args['end'] = request.args.get("end")

    sales = execute(f"""
        SELECT
            o.*,
            COALESCE(product_orders.LineItemTotal, 0) AS LineItemTotal,
            CONCAT(c.FirstName, ' ', c.LastName) as CustomerName,
            COALESCE(CONCAT(e.FirstName, ' ', e.LastName), '') as EmployeeName,
            l.LocationName
        FROM tblorders o
        LEFT JOIN (
            SELECT
                OrderID,
                SUM(Quantity * UnitPrice) AS LineItemTotal
            FROM tblorderdetails
            GROUP BY OrderID
        ) product_orders ON o.OrderID = product_orders.OrderID
        JOIN tblcustomers c ON o.CustomerID = c.CustomerID
        LEFT JOIN tblemployees e ON o.EmployeeID = e.EmployeeID
        JOIN tbllocations l ON o.LocationID = l.LocationID
        WHERE o.OrderStatus <> 'Cancelled' {' '.join(filters)}
        ORDER BY o.OrderDate, o.OrderID
    """, args)

    #Filtered by timeframe
    total = sum([s['LineItemTotal'] for s in sales])
    return render_template('reports/sales.html', sales=sales, total=total, args=args)

def get_salary(e, default_salary=50000):
    """Returns estimated salary based on job title."""
    job_title = e['JobTitle']

    if 'CEO' in job_title:
        return 150000
    if 'CFO' in job_title:
        return 125000
    if 'Manager' in job_title:
        return 100000
    if 'Sales' in job_title:
        return 75000
    if 'Technician' in job_title:
        return 60000

    return default_salary

@reports_bp.route('/employee')
def employee():
    employees = execute("""
        SELECT
            e.*,
            l.LocationName
        FROM tblemployees e
        JOIN tbllocations l ON e.LocationID = l.LocationID
    """)
    for e in employees:
        e['Salary'] = get_salary(e)
    return render_template('reports/employee.html', employees=employees)

@reports_bp.route('/customer/<customer_id>/statement')
def customer_statements(customer_id):

    try:
        customer_id = int(customer_id)
        selected_customer = execute("""
            SELECT
                CustomerID,
                CONCAT(FirstName, ' ', LastName) AS CustomerName
            FROM tblcustomers
            WHERE CustomerID = %(CustomerID)s
        """, {'CustomerID': customer_id})[0]
    except (ValueError, IndexError):
        abort(404)

    statements = execute("""
        SELECT DISTINCT
            YEAR(OrderDate) AS StatementYear,
            MONTH(OrderDate) AS StatementMonth
        FROM tblorders o
        WHERE o.CustomerID = %(CustomerID)s
        ORDER BY StatementYear, StatementMonth
    """, {'CustomerID': customer_id})

    return render_template(
        'reports/customer_statements.html',
        customer=selected_customer,
        statements=statements
    )

def get_orders_base_query(filters, payment_filter):
    """
    Returns query used directly
    to get orders for a statement,
    and as a subquery for calculating
    the statement's beginning balance.
    payment_filter limits which payments count, by payment date,
    so a statement only shows payments made by that point in time.
    """
    filters.append('o.CustomerID = %(customer_id)s')
    filters.append("o.OrderStatus <> 'Cancelled'")  # Cancelled orders are not owed
    return f"""
        SELECT
            o.*,
            COALESCE(od_grouped.LineItemTotal, 0) AS LineItemTotal,
            COALESCE(p_grouped.TotalPaid, 0) AS TotalPaid,
            p_grouped.LastPaymentDate,
            -- Tax applies to the items only, then delivery is added (same as the orders page)
            ROUND(COALESCE(od_grouped.LineItemTotal, 0) * (1 + o.TaxRate) + o.DeliveryCharge, 2) AS OrderTotal,
            ROUND(COALESCE(od_grouped.LineItemTotal, 0) * (1 + o.TaxRate) + o.DeliveryCharge, 2)
                - COALESCE(p_grouped.TotalPaid, 0) AS AmountDue
        FROM tblorders o
        LEFT JOIN (
            SELECT
                OrderID,
                SUM(Quantity * UnitPrice) AS LineItemTotal
            FROM tblorderdetails
            GROUP BY OrderID
        ) od_grouped ON o.OrderID = od_grouped.OrderID
        LEFT JOIN (
            SELECT
                OrderID,
                SUM(Amount) AS TotalPaid,
                MAX(PaymentDate) AS LastPaymentDate
            FROM tblpayments
            WHERE {payment_filter}
            GROUP BY OrderID
        ) p_grouped ON o.OrderID = p_grouped.OrderID
        WHERE {' AND '.join(filters)}
        ORDER BY o.OrderDate, o.OrderID
    """

@reports_bp.route('/customer/<customer_id>/statement/<statement_year>/<statement_month>')
def statement(customer_id, statement_year, statement_month):
    try:
        customer_id = int(customer_id)
        statement_year = int(statement_year)
        statement_month = int(statement_month)
        selected_customer = execute("""
                SELECT
                    *
                FROM tblcustomers
                WHERE CustomerID = %(CustomerID)s
            """, {'CustomerID': customer_id})[0]
        # A bad month or year (e.g. /2024/13) raises ValueError here
        statement_start_date = date(statement_year, statement_month, 1)
        _, statement_end_day = calendar.monthrange(statement_year, statement_month)
        statement_end_date = date(statement_year, statement_month, statement_end_day)
    except (ValueError, IndexError, calendar.IllegalMonthError):
        abort(404)

    payment_due_date = statement_end_date + timedelta(days=15)

    # First day of the next month. Using "< next month" instead of "<= last day"
    # also catches payments made late on the last day if the column holds a time.
    next_month_start = statement_end_date + timedelta(days=1)

    args = {
        'customer_id': customer_id,
        'statement_start_date': statement_start_date,
        'statement_end_date': statement_end_date,
        'next_month_start': next_month_start,
    }

    # Beginning balance: earlier orders, minus only the payments made before this month
    beginning_balance_query = f"""
        SELECT
            COALESCE(SUM(AmountDue), 0) AS BeginningBalance
        FROM ({get_orders_base_query(['o.OrderDate < %(statement_start_date)s'], 'PaymentDate < %(statement_start_date)s')}) orders
    """
    beginning_balance = execute(beginning_balance_query, args)[0]['BeginningBalance']

    # This month's orders, showing payments made up to the end of this month
    customer_orders = execute(get_orders_base_query(
        ['o.OrderDate >= %(statement_start_date)s', 'o.OrderDate <= %(statement_end_date)s'],
        'PaymentDate < %(next_month_start)s'
    ), args)

    new_charges = sum([o['OrderTotal'] for o in customer_orders])

    # Payments made during this month, on any of the customer's orders (old or new)
    total_payments = execute("""
        SELECT
            COALESCE(SUM(p.Amount), 0) AS TotalPayments
        FROM tblpayments p
        JOIN tblorders o ON p.OrderID = o.OrderID
        WHERE o.CustomerID = %(customer_id)s
          AND o.OrderStatus <> 'Cancelled'
          AND p.PaymentDate >= %(statement_start_date)s
          AND p.PaymentDate < %(next_month_start)s
    """, args)[0]['TotalPayments']
    balance_due = beginning_balance + new_charges - total_payments

    return render_template(
        'reports/statement.html',
        customer=selected_customer,
        statement_date=statement_end_date,
        beginning_balance=beginning_balance,
        new_charges=new_charges,
        total_payments=total_payments,
        balance_due=balance_due,
        payment_due_date=payment_due_date,
        customer_orders=customer_orders
    )