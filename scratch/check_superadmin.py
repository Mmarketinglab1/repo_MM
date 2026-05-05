import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "6543")

try:
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST, port=DB_PORT
    )
    cur = conn.cursor()
    
    cur.execute("SELECT id, username, email, role FROM operators WHERE role = 'super_admin';")
    rows = cur.fetchall()
    
    if rows:
        print("Usuarios Super Admin encontrados:")
        for row in rows:
            print(f"ID: {row[0]}, Username: {row[1]}, Email: {row[2]}, Role: {row[3]}")
    else:
        print("No se encontraron usuarios Super Admin.")
        
    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
