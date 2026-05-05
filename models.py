from sqlalchemy import Column, String, Text, BigInteger, ForeignKey, TIMESTAMP, Boolean, UniqueConstraint
from database import Base
from sqlalchemy.sql import func
import uuid

def generate_uuid():
    return str(uuid.uuid4())

class Company(Base):
    __tablename__ = "companies"
    id = Column(String, primary_key=True, default=generate_uuid)
    name = Column(String, unique=True)
    is_active = Column(Boolean, default=True)
    webhook_token = Column(String, unique=True, index=True, default=generate_uuid)
    assignment_mode = Column(String, default="round_robin") # "round_robin" o "manual"
    logo_url = Column(String, nullable=True) # URL del logo de la empresa
    whatsapp_token = Column(String, nullable=True) # Token de WhatsApp Cloud API
    whatsapp_phone_id = Column(String, nullable=True) # Phone ID de WhatsApp Cloud API
    whatsapp_waba_id = Column(String, nullable=True) # WhatsApp Business Account ID
    last_assigned_operator_id = Column(BigInteger, ForeignKey("operators.id"), nullable=True)
    logo_data = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, server_default=func.now())

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True) # ID de contacto/telefono/UUID
    company_id = Column(String, ForeignKey("companies.id"))
    full_name = Column(String)
    phone = Column(String)
    tags = Column(String, default="") # Etiquetas de CRM
    crm_status = Column(String, default="No Contactado") # Estado de CRM
    email = Column(String) # Nuevo
    address = Column(String) # Nuevo
    observations = Column(Text) # Nuevo
    assigned_to = Column(BigInteger, ForeignKey("operators.id"), nullable=True) # Operador asignado
    last_activity = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
    is_bot_active = Column(Boolean, default=True) # Control de bot IA
    created_at = Column(TIMESTAMP, server_default=func.now())

class Operator(Base):
    __tablename__ = "operators"
    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    company_id = Column(String, ForeignKey("companies.id"))
    username = Column(String, index=True)
    hashed_password = Column(String)
    full_name = Column(String)
    role = Column(String, default="operador") # admin, operador
    email = Column(String, unique=True, index=True) # Email único para login (global)
    is_active_round_robin = Column(Boolean, default=True) # Participa en round robin
    created_at = Column(TIMESTAMP, server_default=func.now())

    __table_args__ = (UniqueConstraint('company_id', 'username', name='_company_username_uc'),)

class Message(Base):
    __tablename__ = "messages"
    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    company_id = Column(String, ForeignKey("companies.id"))
    user_id = Column(String, ForeignKey("users.id"))
    sender = Column(String) # bot, user, human
    text = Column(Text)
    timestamp_ms = Column(BigInteger)

class LeadAnalysis(Base):
    __tablename__ = "lead_analysis"
    id = Column(BigInteger, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.id"))
    company_id = Column(String, ForeignKey("companies.id"))
    summary = Column(Text)
    sentiment_score = Column(String) # "Positivo", "Neutral", "Negativo"
    top_intents = Column(String) # Comas separados
    temperature = Column(BigInteger) # 0-100
    last_analyzed = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now())
