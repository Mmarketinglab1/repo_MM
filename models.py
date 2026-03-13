from sqlalchemy import Column, String, Text, BigInteger, ForeignKey, TIMESTAMP
from database import Base
from sqlalchemy.sql import func

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True) # WhatsApp ID
    full_name = Column(String)
    phone = Column(String)
    tags = Column(String, default="") # Etiquetas de CRM
    crm_status = Column(String, default="No Contactado") # Estado de CRM
    email = Column(String) # Nuevo
    address = Column(String) # Nuevo
    observations = Column(Text) # Nuevo
    created_at = Column(TIMESTAMP, server_default=func.now())

class Operator(Base):
    __tablename__ = "operators"
    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    role = Column(String, default="operador") # admin, operador
    created_at = Column(TIMESTAMP, server_default=func.now())

class Message(Base):
    __tablename__ = "messages"
    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"))
    sender = Column(String) # bot, user, human
    text = Column(Text)
    timestamp_ms = Column(BigInteger)
