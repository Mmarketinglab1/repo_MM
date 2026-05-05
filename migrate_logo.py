import os
from sqlalchemy import text
from database import engine
from dotenv import load_dotenv

load_dotenv()

def migrate():
    with engine.connect() as conn:
        print("Agregando columna logo_url a la tabla companies...")
        try:
            conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_url VARCHAR;"))
            conn.commit()
            print("Columna agregada exitosamente.")
        except Exception as e:
            print(f"Error o ya existe: {e}")

if __name__ == "__main__":
    migrate()
