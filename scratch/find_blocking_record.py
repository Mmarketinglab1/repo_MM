import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def find_blocking_record(uid):
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        print(f"Buscando qué registro en 'lead_analysis' apunta al usuario '{uid}'")
        
        # Consultar la tabla lead_analysis para ver el valor EXACTO de user_id
        # que dispara la FK
        cur.execute("SELECT id, user_id, length(user_id) as len FROM lead_analysis WHERE user_id LIKE %s", (f'%{uid}%',))
        rows = cur.fetchall()
        
        if not rows:
            print("No se encontraron registros en 'lead_analysis' con ese patrón.")
        else:
            for row in rows:
                print(f"Encontrado: id={row[0]}, user_id='{row[1]}' (Longitud: {row[2]})")
                # Ver si hay caracteres no imprimibles
                print(f"Bytes: {row[1].encode('utf-8')}")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    find_blocking_record("8743136546")
