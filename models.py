from sqlalchemy import Column, String, Text, BigInteger, ForeignKey, TIMESTAMP
from database import Base
from sqlalchemy.sql import func

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True)
    full_name = Column(String)
    phone = Column(String) # <-- Nueva columna
    tags = Column(String, default="") # Etiquetas separadas por comas
    created_at = Column(TIMESTAMP, server_default=func.now())

class Message(Base):
    __tablename__ = "messages"
    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"))
    sender = Column(String) 
    text = Column(Text)
    timestamp_ms = Column(BigInteger)
