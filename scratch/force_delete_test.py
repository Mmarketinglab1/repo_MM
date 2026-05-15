import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def force_delete_test(uid):
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        conn.autocommit = False
        cur = conn.cursor()
        
        print(f"Buscando qué registro en 'lead_analysis' apunta al usuario '{uid}'")
        cur.execute("SELECT id FROM lead_analysis WHERE user_id = %s", (uid,))
        rows = cur.fetchall()
        print("Antes del borrado:", rows)

        # Intentar borrar
        cur.execute("DELETE FROM lead_analysis WHERE user_id = %s", (uid,))
        print(f"Filas borradas en lead_analysis: {cur.rowcount}")
        
        cur.execute("DELETE FROM messages WHERE user_id = %s", (uid,))
        print(f"Filas borradas en messages: {cur.rowcount}")
        
        conn.commit()
        print("Commit realizado.")
        
        # Intentar borrar el usuario
        cur.execute("DELETE FROM users WHERE id = %s", (uid,))
        print(f"Filas borradas en users: {cur.rowcount}")
        
        conn.commit()
        print("Borrado final exitoso.")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error durante el borrado forzado: {e}")

if __name__ == "__main__":
    force_delete_test("8743136546")
