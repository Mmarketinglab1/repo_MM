import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def migrate():
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        print("Adding column 'logo_data' to 'companies' table...")
        cur.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_data TEXT;")
        
        conn.commit()
        cur.close()
        conn.close()
        print("Migration completed successfully.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    migrate()
