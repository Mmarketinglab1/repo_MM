import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def alter_table():
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        cur.execute("UPDATE users SET last_user_msg_at = last_activity WHERE last_user_msg_at IS NULL;")
        print("Column last_user_msg_at backfilled successfully.")
        conn.commit()
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    alter_table()
