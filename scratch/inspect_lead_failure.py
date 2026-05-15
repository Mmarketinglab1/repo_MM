import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def check_user_data(uid):
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        print(f"Buscando datos para el ID: {uid}")
        
        # 1. Comprobar en 'users'
        cur.execute("SELECT id, company_id, full_name FROM users WHERE id LIKE %s", (f'%{uid}%',))
        users = cur.fetchall()
        print(f"Usuarios encontrados ({len(users)}):", users)
        
        # 2. Comprobar en 'lead_analysis'
        cur.execute("SELECT id, user_id, company_id FROM lead_analysis WHERE user_id LIKE %s", (f'%{uid}%',))
        analyses = cur.fetchall()
        print(f"Análisis encontrados ({len(analyses)}):", analyses)
        
        # 3. Comprobar en 'messages'
        cur.execute("SELECT id, user_id, company_id FROM messages WHERE user_id LIKE %s", (f'%{uid}%',))
        msgs = cur.fetchall()
        print(f"Mensajes encontrados ({len(msgs)}):", len(msgs))
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_user_data("534532")
