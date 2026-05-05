import os
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
import models

load_dotenv()

db_host = os.getenv("DB_HOST", "db.vcnrvohzedxpknbggckb.supabase.co")
db_name = os.getenv("DB_NAME", "postgres")
db_user = os.getenv("DB_USER", "postgres")
db_pass = os.getenv("DB_PASS")
db_port = os.getenv("DB_PORT", "6543")

DATABASE_URL = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def check_data():
    db = SessionLocal()
    try:
        print("--- TOP 5 USERS ---")
        users = db.query(models.User).limit(5).all()
        for u in users:
            print(f"User ID: '{u.id}', Name: '{u.full_name}', Company: {u.company_id}")
            
            # Check messages for this specific user
            msg_count = db.query(models.Message).filter(models.Message.user_id == u.id).count()
            print(f"  -> Messages in DB: {msg_count}")
            
            # Check if there's a company mismatch
            msg_with_company = db.query(models.Message).filter(
                models.Message.user_id == u.id, 
                models.Message.company_id == u.company_id
            ).count()
            print(f"  -> Messages with matching Company ID: {msg_with_company}")
            
        print("\n--- TOP 5 MESSAGES ---")
        msgs = db.query(models.Message).limit(5).all()
        for m in msgs:
            print(f"Msg ID: {m.id}, UserID: '{m.user_id}', Sender: {m.sender}, Company: {m.company_id}")
            
    finally:
        db.close()

if __name__ == "__main__":
    check_data()
