import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def apply_cascade_migration():
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        conn.autocommit = True
        cur = conn.cursor()
        
        print("Iniciando migracion maestra de integridad referencial (CASCADE)...")
        
        # 1. Ajustar 'lead_analysis'
        print("Actualizando tabla 'lead_analysis'...")
        cur.execute("ALTER TABLE lead_analysis DROP CONSTRAINT IF EXISTS lead_analysis_user_id_fkey;")
        cur.execute("ALTER TABLE lead_analysis ADD CONSTRAINT lead_analysis_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;")
        
        # 2. Ajustar 'messages'
        print("Actualizando tabla 'messages'...")
        cur.execute("ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_user_id_fkey;")
        cur.execute("ALTER TABLE messages ADD CONSTRAINT messages_user_id_fkey FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;")
        
        print("Migracion completada exitosamente. Las tablas ahora soportan borrado en cascada.")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error durante la migracion: {e}")

if __name__ == "__main__":
    apply_cascade_migration()
