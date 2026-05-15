import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_PORT = os.getenv("DB_PORT", "5432")

def check_foreign_keys():
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            host=DB_HOST,
            port=DB_PORT
        )
        cur = conn.cursor()
        
        # Consultar todas las llaves foráneas que apuntan a la tabla 'users'
        cur.execute("""
            SELECT
                tc.table_name, 
                kcu.column_name, 
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name 
            FROM 
                information_schema.table_constraints AS tc 
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND ccu.table_name='users';
        """)
        
        fks = cur.fetchall()
        if not fks:
            print("No se encontraron llaves foráneas apuntando a 'users'.")
        else:
            print("Tablas que dependen de 'users':")
            for fk in fks:
                print(f"- Tabla: {fk[0]}, Columna: {fk[1]}, Apunta a: {fk[2]}.{fk[3]}")
        
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_foreign_keys()
