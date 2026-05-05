import os
import bcrypt
import psycopg2

from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "6543")

NEW_PASSWORD = os.getenv("RESET_SUPERADMIN_PASS")

def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

try:
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST, port=DB_PORT
    )
    cur = conn.cursor()
    
    hashed = get_password_hash(NEW_PASSWORD)
    
    print(f"Buscando usuario superadmin...")
    cur.execute("UPDATE operators SET hashed_password = %s WHERE role = 'super_admin';", (hashed,))
    
    if cur.rowcount > 0:
        conn.commit()
        print(f"✓ Contraseña de superadmin actualizada correctamente a: {NEW_PASSWORD}")
    else:
        print("✗ No se encontró ningún usuario con el rol 'super_admin'.")
        
    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
