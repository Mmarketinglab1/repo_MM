import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def check_tables():
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        # Listar tablas
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';")
        tables = cur.fetchall()
        print("Tablas encontradas:", [t[0] for t in tables])
        
        # Para cada tabla, ver si tiene una columna user_id o similar que apunte a users
        for table in [t[0] for t in tables]:
            cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}';")
            cols = [c[0] for c in cur.fetchall()]
            if 'user_id' in cols:
                print(f"La tabla '{table}' tiene una columna 'user_id'")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_tables()
