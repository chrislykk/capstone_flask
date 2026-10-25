from dotenv import load_dotenv
load_dotenv()

from getpass import getpass
from werkzeug.security import generate_password_hash
from app import get_connection

username = input("Username: ").strip()
password = getpass("Password: ")

conn = get_connection()
try:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tblstaffusers (Username, PasswordHash) VALUES (%s, %s)",
            (username, generate_password_hash(password)),
        )
    conn.commit()
finally:
    conn.close()
print("Created", username)