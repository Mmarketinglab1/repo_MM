from fastapi import FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, BackgroundTasks, File, UploadFile, Query, Body, Request, Form
import shutil
import asyncio
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text
from database import get_db, engine, SessionLocal
import models
from pydantic import BaseModel
from typing import Optional, List, Any
import httpx
import time
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from jose import JWTError, jwt
import bcrypt
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi import WebSocket, WebSocketDisconnect
import httpx
from sqlalchemy.sql import func
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import google.generativeai as genai
import json
import uuid

load_dotenv()

# models.Base.metadata.create_all(bind=engine)

app = FastAPI()

# Asegurar que exista directorio para logos
OS_LOGOS_DIR = "static/logos"
if not os.path.exists(OS_LOGOS_DIR):
    os.makedirs(OS_LOGOS_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- AUTOMATIZACION DE ESTADOS ---
async def lead_status_automation():
    """
    Proceso en segundo plano que revisa la inactividad de los leads y actualiza su estado.
    - No Contactado -> Informar Estado (después de 3 días)
    - Contactado -> En Proceso (después de 8 días)
    """
    while True:
        try:
            print("[AUTOMATION] Ejecutando revisión de estados de leads...", flush=True)
            db = SessionLocal()
            try:
                now = datetime.utcnow()
                
                # 1. Contactado o En Proceso -> Informar Estado (3 días = 72 horas)
                three_days_ago = now - timedelta(days=3)
                leads_to_inform = db.query(models.User).filter(
                    models.User.crm_status.in_(["Contactado", "En Proceso"]),
                    models.User.last_activity <= three_days_ago
                ).all()
                
                for lead in leads_to_inform:
                    print(f"[AUTOMATION] Lead {lead.id} ({lead.full_name}): {lead.crm_status} -> Informar Estado", flush=True)
                    lead.crm_status = "Informar Estado"
                
                if leads_to_inform:
                    db.commit()
                    print(f"[AUTOMATION] {len(leads_to_inform)} estados actualizados.", flush=True)
                else:
                    print("[AUTOMATION] No se detectaron leads para actualizar.", flush=True)
                    
            except Exception as e:
                print(f"[AUTOMATION] Error en proceso de actualización: {e}", flush=True)
                db.rollback()
            finally:
                db.close()
                
        except Exception as e:
            print(f"[AUTOMATION] Error crítico en loop: {e}", flush=True)
            
        # Ejecutar cada 1 hora
        await asyncio.sleep(3600)

# --- AUTO MIGRACIÓN ---
@app.on_event("startup")
async def startup_event():
    from sqlalchemy import text
    from database import engine
    print("Verificando esquema de base de datos...")
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS logo_url VARCHAR;"))
            conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS whatsapp_token VARCHAR;"))
            conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS whatsapp_phone_id VARCHAR;"))
            conn.execute(text("ALTER TABLE companies ADD COLUMN IF NOT EXISTS whatsapp_waba_id VARCHAR;"))
            conn.commit()
            print("Esquema verificado/actualizado.")
            
            # MIGRACION AUTOMATICA: Si existen variables de entorno, moverlas a la DB para la empresa principal
            wa_t = os.environ.get("WHATSAPP_TOKEN")
            wa_p = os.environ.get("WHATSAPP_PHONE_ID")
            if wa_t and wa_p:
                print(f"[MIGRACION] Detectadas variables de entorno. Poblando base de datos...", flush=True)
                conn.execute(text("UPDATE companies SET whatsapp_token = :t, whatsapp_phone_id = :p WHERE whatsapp_token IS NULL"), {"t": wa_t, "p": wa_p})
                conn.commit()
                
            # INICIAR AUTOMATIZACION DE ESTADOS
            asyncio.create_task(lead_status_automation())
            print("Automatización de estados iniciada.")
            
    except Exception as e:
        print(f"Nota de migración: {e}")

# --- SEGURIDAD ---
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 1 día

if not SECRET_KEY:
    raise ValueError("FATAL ERROR: SECRET_KEY is missing in environment variables or .env file.")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

def get_password_hash(password):
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def verify_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except:
        return None

async def get_current_operator(request: Request, db: Session = Depends(get_db)):
    auth_header = request.headers.get("Authorization")
    if not auth_header: raise HTTPException(status_code=401, detail="No token")
    token = auth_header.replace("Bearer ", "")
    payload = verify_token(token)
    if not payload: raise HTTPException(status_code=401, detail="Invalid token")
    
    op = db.query(models.Operator).filter(models.Operator.email == payload["sub"]).first()
    if not op: raise HTTPException(status_code=401, detail="User not found")
    
    # LÓGICA DE IMPERSONACIÓN REFORZADA
    target_company_id = request.headers.get("X-Tenant-ID") or request.headers.get("X-Company-ID") or request.query_params.get("company_id")
    
    if op.role == "super_admin" and target_company_id and target_company_id not in ["undefined", "null", ""]:
        class VirtualOperator:
            def __init__(self, original_op, target_id):
                self.id = original_op.id
                self.email = original_op.email
                self.username = original_op.username
                self.full_name = original_op.full_name
                self.role = "super_admin"
                self.company_id = str(target_id)
                self.is_virtual = True
        
        v_op = VirtualOperator(op, target_company_id)
        print(f"!!! [IMPERSONATION] {v_op.email} -> TENANT: {v_op.company_id}")
        return v_op
            
    if op.role != "super_admin":
        if not op.company_id: raise HTTPException(status_code=401, detail="No company assigned")
        company = db.query(models.Company).filter(models.Company.id == op.company_id).first()
        if not company or not company.is_active:
            raise HTTPException(status_code=403, detail="Empresa suspendida o no encontrada")
            
    return op

@app.get("/api/debug/env-vars")
def debug_env_vars(current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Solo admin/super_admin")
    
    import os as _os
    return {
        "WHATSAPP_TOKEN_RAW": _os.environ.get("WHATSAPP_TOKEN", "NOT_FOUND"),
        "WHATSAPP_PHONE_ID_RAW": _os.environ.get("WHATSAPP_PHONE_ID", "NOT_FOUND"),
        "PROCESS_ID": _os.getpid(),
        "ENV_KEYS": list(_os.environ.keys())
    }

@app.get("/api/debug/env-keys")
def debug_env_keys(current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Solo admin/super_admin")
    
    import os as _os
    res = {}
    for k in _os.environ.keys():
        val = _os.environ.get(k, "")
        res[k] = {
            "exists": True,
            "len": len(val),
            "preview": val[:3] + "..." if len(val) > 5 else "***"
        }
    return res

@app.get("/api/public-config")
def get_public_config():
    return {
        "SUPABASE_URL": os.getenv("SUPABASE_URL", "https://nsgiyxybvgnpwxgpkkvz.supabase.co"),
        "SUPABASE_ANON_KEY": os.getenv("SUPABASE_ANON_KEY")
    }

# --- SMTP CONFIG ---
SMTP_HOST = os.getenv("SMTP_HOST", "mail.mmarketing.dev")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "famiglia_crm@mmarketing.dev")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", "famiglia_crm@mmarketing.dev")

# Meta WhatsApp Cloud API Config
# WhatsApp Cloud API Config (Evaluated at start, but endpoint uses direct os.environ)
WHATSAPP_VERSION = "v21.0"

def send_email_smtp(to_email: str, subject: str, html_body: str):
    """Envía un email usando SMTP directo."""
    SMTP_HOST = os.getenv("SMTP_HOST")
    SMTP_PORT = os.getenv("SMTP_PORT", "587")
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASS = os.getenv("SMTP_PASS")
    SMTP_FROM = os.getenv("SMTP_FROM")

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        print("[EMAIL] ⚠️ No hay configuración SMTP completa.", flush=True)
        return {"ok": False, "error": "Falta configuración SMTP"}

    try:
        print(f"[EMAIL] [DEBUG] Intentando enviar a {to_email} via {SMTP_HOST}:{SMTP_PORT} como {SMTP_USER}", flush=True)
        msg = MIMEMultipart("alternative")
        msg["From"] = f"LiveChatPro <{SMTP_FROM}>"
        msg["To"] = to_email
        msg["Bcc"] = "cosciamaximiliano@gmail.com"
        msg["Subject"] = subject
        msg.attach(MIMEText(html_body, "html", "utf-8"))
        
        recipients = [to_email, "cosciamaximiliano@gmail.com"]
        
        try:
            # Si el puerto es 465, usamos SMTP_SSL
            if SMTP_PORT == "465":
                server = smtplib.SMTP_SSL(SMTP_HOST, int(SMTP_PORT), timeout=15)
            else:
                server = smtplib.SMTP(SMTP_HOST, int(SMTP_PORT), timeout=15)
                server.starttls()
            
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, recipients, msg.as_string())
            server.quit()
            print(f"[EMAIL] ✅ Email enviado correctamente a {to_email}", flush=True)
            return {"ok": True, "error": None}
        except Exception as e:
            err_msg = f"{type(e).__name__}: {e}"
            print(f"[EMAIL] ❌ SMTP Error: {err_msg}", flush=True)
            return {"ok": False, "error": err_msg}
    except Exception as e:
        print(f"[EMAIL] ❌ Error general en send_email_smtp: {e}", flush=True)
        return {"ok": False, "error": str(e)}

@app.get("/api/test-email")
async def test_email(to: str = "test@test.com", db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    """Endpoint de prueba para verificar que SMTP funciona."""
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Solo admin puede probar emails")
    
    result = send_email_smtp(
        to_email=to,
        subject="🧪 Test LiveChatPro - Email de prueba",
        html_body=f"""
        <div style="font-family: Arial; padding: 20px; text-align: center;">
            <h2>✅ Email de prueba exitoso</h2>
            <p>Si estás leyendo esto, el sistema SMTP funciona correctamente.</p>
            <p style="color: #64748b;">Host: {SMTP_HOST}:{SMTP_PORT}</p>
            <p style="color: #64748b;">From: {SMTP_FROM}</p>
        </div>
        """
    )
    return {
        "status": "ok" if result["ok"] else "error",
        "smtp_host": f"{SMTP_HOST}:{SMTP_PORT}",
        "smtp_user": SMTP_USER,
        "to": to,
        "error_detail": result["error"]
    }

# La función send_whatsapp_cloud_api vieja ha sido eliminada para evitar duplicidad.
# El módulo de Remarketing usa la versión de la línea 1160+ que es compatible con múltiples empresas.

# --- CONFIGURACION GEMINI ---
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
if GEMINI_KEY:
    genai.configure(api_key=GEMINI_KEY)
    model_ai = genai.GenerativeModel('gemini-flash-latest')
else:
    model_ai = None

async def analyze_lead_with_ai(user_id: str, company_id: str, db: Session):
    """Realiza el análisis profundo de un lead usando el historial de mensajes."""
    if not model_ai:
        return {"error": "Gemini API Key no configurada"}
    
    # Obtener mensajes con búsqueda flexible
    print(f"[DEBUG AI] Buscando mensajes para UserID: '{user_id}' en Company: '{company_id}'")
    
    # Intentamos primero con coincidencia exacta pero limpia
    msgs = db.query(models.Message).filter(
        models.Message.user_id == user_id, 
        models.Message.company_id == company_id
    ).order_by(models.Message.timestamp_ms).all()
    
    if not msgs:
        print(f"[DEBUG AI] No se encontraron mensajes con ID exacto '{user_id}'. Probando búsqueda parcial/limpia...")
        # Búsqueda más agresiva: ignorando espacios y comillas en la DB
        clean_uid = user_id.replace('"', '').replace("'", "").strip()
        msgs = db.query(models.Message).filter(
            func.trim(models.Message.user_id) == clean_uid,
            models.Message.company_id == company_id
        ).all()

    if not msgs:
        print(f"[DEBUG AI] ❌ Fallo final: No hay mensajes para UserID '{user_id}' en Company '{company_id}'")
        return {"error": f"Sin mensajes para analizar (ID buscado: {user_id})"}
    
    history_text = "\n".join([f"{m.sender}: {m.text}" for m in msgs])
    
    prompt = f"""
    Analiza la siguiente conversación de un cliente con un CRM de viajes/ventas.
    Historial:
    {history_text}
    
    Genera un JSON con este formato exacto:
    {{
      "summary": "Resumen corto de 2 frases sobre qué busca el cliente y su situación actual.",
      "sentiment": "Positivo|Neutral|Negativo",
      "intents": ["Intencion1", "Intencion2"],
      "temperature": 85
    }}
    Donde 'temperature' es un score del 0 al 100 indicando qué tan cerca está de comprar (Lead Score).
    Responde SOLO el JSON.
    """
    
    try:
        response = model_ai.generate_content(prompt)
        # Limpiar posible markdown del JSON si Gemini lo incluye
        raw_text = response.text.strip().replace("```json", "").replace("```", "")
        data = json.loads(raw_text)
        
        # Guardar o actualizar en DB
        analysis = db.query(models.LeadAnalysis).filter(
            models.LeadAnalysis.user_id == user_id,
            models.LeadAnalysis.company_id == company_id
        ).first()
        
        if not analysis:
            analysis = models.LeadAnalysis(user_id=user_id, company_id=company_id)
            db.add(analysis)
        
        analysis.summary = data.get("summary", "")
        analysis.sentiment_score = data.get("sentiment", "Neutral")
        analysis.top_intents = ", ".join(data.get("intents", []))
        analysis.temperature = data.get("temperature", 50)
        
        db.commit()
        return data
    except Exception as e:
        print(f"Error AI Analysis: {e}")
        return {"error": str(e)}

async def get_current_superadmin(current: models.Operator = Depends(get_current_operator)):
    if current.role != "super_admin":
        raise HTTPException(status_code=403, detail="Acceso denegado: Se requiere rol Super Admin")
    return current

def clean_user_id(uid: Any) -> str:
    if uid is None: return ""
    # Eliminar símbolos comunes y espacios para que el ID sea consistente
    s = str(uid).replace('"', '').replace("'", "").replace("+", "").replace("-", "").replace(" ", "").strip()
    # Si viene con @s.whatsapp.net lo quitamos (aunque n8n ya debería hacerlo)
    if "@" in s:
        s = s.split("@")[0]
    return s

# --- SCHEMAS ---

class OperatorUpdateSchema(BaseModel):
    full_name: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = "operador"
    email: Optional[str] = None
    is_active_round_robin: Optional[bool] = True

class MessageSchema(BaseModel):
    user_id: Any
    text: Any
    user_name: Optional[str] = "Usuario WhatsApp"
    phone: Optional[str] = None
    timestamp: Optional[Any] = None
    sender: Optional[str] = "user"

class HandoffSchema(BaseModel):
    user_id: str
    resumen: str

class Token(BaseModel):
    access_token: str
    token_type: str

class LeadCreateSchema(BaseModel):
    id: str # Phone usually
    full_name: str
    phone: str
    tags: Optional[str] = ""
    crm_status: Optional[str] = "No Contactado"
    email: Optional[str] = None
    address: Optional[str] = None
    observations: Optional[str] = None

class LeadUpdateSchema(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    tags: Optional[str] = None
    crm_status: Optional[str] = None
    email: Optional[str] = None
    address: Optional[str] = None
    observations: Optional[str] = None

class TagUpdateSchema(BaseModel):
    tags: str

class StatusUpdateSchema(BaseModel):
    status: str

class OperatorCreateSchema(BaseModel):
    username: str
    password: str
    full_name: str
    role: str = "operador"
    email: str
    is_active_round_robin: bool = True
    target_company_id: Optional[str] = None # Solo para Super Admin

class CompanyRegisterSchema(BaseModel):
    company_name: str
    admin_email: str
    admin_password: str
    admin_fullname: str

def get_next_operator_id(company_id: str, db: Session) -> Optional[int]:
    """
    Calcula el siguiente operador en la rotación Round Robin de forma atómica.
    Utiliza with_for_update() para bloquear la fila de la empresa y evitar condiciones de carrera.
    """
    # Bloquear la fila de la empresa para asegurar atomicidad en la consulta/actualización
    company = db.query(models.Company).filter(models.Company.id == company_id).with_for_update().first()
    if not company:
        return None
    
    # Si el modo no es round_robin, respetamos la configuración específica
    if company.assignment_mode == "manual":
        return None
    
    if company.assignment_mode.startswith("op_"):
        try:
            return int(company.assignment_mode.replace("op_", ""))
        except:
            return None

    if company.assignment_mode == "round_robin":
        # Obtener lista de operadores activos para RR
        ops = db.query(models.Operator).filter(
            models.Operator.company_id == company_id,
            models.Operator.is_active_round_robin == True
        ).order_by(models.Operator.id).all()
        
        if not ops:
            return None
        
        if not company.last_assigned_operator_id:
            # Si es la primera vez, asignamos al primero
            next_op = ops[0]
        else:
            # Buscar el índice del último operador asignado en la lista actual de activos
            idx = next((i for i, op in enumerate(ops) if op.id == company.last_assigned_operator_id), -1)
            # Si no se encuentra (fue desactivado), idx es -1, así que next_idx = 0.
            # Si se encuentra, pasamos al siguiente.
            next_idx = (idx + 1) % len(ops)
            next_op = ops[next_idx]
        
        # Actualizar el rastro del último asignado
        company.last_assigned_operator_id = next_op.id
        return next_op.id
        
    return None

async def notify_n8n(webhook_token: str, data: dict):
    """Notifica a n8n sobre actividad del operador o cambios de estado."""
    url = os.getenv("N8N_WEBHOOK_URL")
    if not url:
        # Fallback predeterminado basado en la configuración del cliente
        url = f"https://n8n.mmarketing.com.ar/webhook/yTRaMK39VgktEKcx"
    
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=data, timeout=10.0)
    except Exception as e:
        print(f"Error notificando a n8n: {e}")

# --- WEBSOCKET MANAGER ---
from collections import defaultdict
class ConnectionManager:
    def __init__(self):
        self.active_connections = defaultdict(list)

    async def connect(self, websocket: WebSocket, company_id: str):
        await websocket.accept()
        self.active_connections[company_id].append(websocket)

    def disconnect(self, websocket: WebSocket, company_id: str):
        if company_id in self.active_connections and websocket in self.active_connections[company_id]:
            self.active_connections[company_id].remove(websocket)

    async def broadcast(self, message: dict, company_id: str):
        if company_id not in self.active_connections: return
        for connection in list(self.active_connections[company_id]):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection, company_id)

manager = ConnectionManager()

# --- REGISTRO Y AUTH ---

@app.post("/api/register")
async def register_company(data: CompanyRegisterSchema, db: Session = Depends(get_db)):
    # Verificar si el email ya existe
    db_op = db.query(models.Operator).filter(models.Operator.email == data.admin_email).first()
    if db_op:
        raise HTTPException(status_code=400, detail="Este email ya está registrado")
    
    new_company = models.Company(name=data.company_name)
    db.add(new_company)
    db.commit()
    db.refresh(new_company)
    
    new_op = models.Operator(
        company_id=new_company.id,
        username=data.admin_email.split('@')[0], # Username por defecto basado en email
        email=data.admin_email,
        hashed_password=get_password_hash(data.admin_password),
        full_name=data.admin_fullname,
        role="admin"
    )
    db.add(new_op)
    db.commit()
    return {"status": "ok", "company_id": new_company.id, "webhook_token": new_company.webhook_token}

@app.post("/token", response_model=Token)
async def login_for_access_token(
    username: str = Form(...), # Este campo recibirá el email
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    # 1. Buscar al operador por email globalmente
    operator = db.query(models.Operator).filter(models.Operator.email == username).first()
    
    if not operator or not verify_password(password, operator.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
        
    # 2. Validar si la empresa está activa (excepto para Súper Admin)
    if operator.role != "super_admin":
        company = db.query(models.Company).filter(models.Company.id == operator.company_id).first()
        if not company or not company.is_active:
            raise HTTPException(status_code=403, detail="La cuenta de esta empresa ha sido suspendida")
    
    access_token = create_access_token(data={"sub": operator.email, "company_id": operator.company_id})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/me")
async def read_users_me(current_user: models.Operator = Depends(get_current_operator), db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(models.Company.id == current_user.company_id).first()

    return {
        "username": current_user.username, 
        "full_name": current_user.full_name, 
        "role": current_user.role, 
        "company_id": current_user.company_id,
        "company_name": company.name if company else "Empresa",
        "logo_url": company.logo_url if company else None,
        "logo_data": company.logo_data if company else None,
        "webhook_token": company.webhook_token if company else None
    }

# --- OPERADORES ---

@app.post("/api/operators")
async def create_operator(op: OperatorCreateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="No tienes permisos para crear operadores")
    
    # Determinar a qué empresa pertenece el nuevo operador
    target_company = current.company_id
    # Lógica de Súper Admin: Usar la empresa destino proporcionada
    target_company = current.company_id
    if current.role == "super_admin" and op.target_company_id:
        target_company = op.target_company_id
    
    # Debug log
    print(f"!!! [OP_CREATE] SuperAdmin {current.email} creating op for company: {target_company}")
    
    # Validar email único global
    db_op = db.query(models.Operator).filter(models.Operator.email == op.email).first()
    if db_op:
        raise HTTPException(status_code=400, detail="Este email ya está en uso por otro operador")
    
    new_op = models.Operator(
        company_id=target_company,
        username=op.username,
        hashed_password=get_password_hash(op.password),
        full_name=op.full_name,
        role=op.role,
        email=op.email,
        is_active_round_robin=op.is_active_round_robin
    )
    db.add(new_op)
    db.commit()
    return {"status": "ok"}

@app.get("/api/operators")
async def get_operators(company_id: Optional[str] = None, db: Session = Depends(get_db), current: Any = Depends(get_current_operator)):
    cid = company_id or current.company_id
    
    # REGLA DE ORO: Si es Super Admin y no hay CID, forzamos FAMIGLIA
    if current.role == "super_admin":
        if not cid or cid in ["undefined", "null", "", "None"]:
            cid = "00000000-0000-0000-0000-000000000000"
            
    print(f"!!! [OPS_LIST] User: {current.email}, Using CID: {cid}")
    
    if current.role not in ["admin", "super_admin"]:
        return db.query(models.Operator).filter(models.Operator.id == current.id).all()
        
    return db.query(models.Operator).filter(models.Operator.company_id == cid).all()

@app.get("/api/super/all-operators")
async def super_get_all_operators(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role != "super_admin":
        raise HTTPException(status_code=403, detail="Solo Súper Admin")
    
    operators = db.query(models.Operator, models.Company.name.label("company_name"))\
                  .join(models.Company, models.Operator.company_id == models.Company.id, isouter=True)\
                  .all()
    
    return [
        {
            "id": op.Operator.id,
            "full_name": op.Operator.full_name,
            "email": op.Operator.email,
            "role": op.Operator.role,
            "company_name": op.company_name or "Súper Admin"
        } for op in operators
    ]

@app.get("/api/super/companies/{company_id}/operators")
async def super_get_company_operators(company_id: str, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_superadmin)):
    operators = db.query(models.Operator).filter(models.Operator.company_id == company_id).all()
    return operators

@app.delete("/api/super/operators/{operator_id}")
async def super_delete_operator(operator_id: int, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_superadmin)):
    op = db.query(models.Operator).filter(models.Operator.id == operator_id).first()
    if not op: raise HTTPException(status_code=404, detail="Operador no encontrado")
    
    # No permitir que el superadmin se borre a sí mismo por error
    if op.id == current.id:
        raise HTTPException(status_code=400, detail="No puedes eliminarte a ti mismo")
        
    db.delete(op)
    db.commit()
    return {"status": "ok"}

@app.get("/api/leads")
async def get_leads(company_id: Optional[str] = None, db: Session = Depends(get_db), current_operator: Any = Depends(get_current_operator)):
    cid = company_id or current_operator.company_id
    
    # REGLA DE ORO
    if current_operator.role == "super_admin":
        if not cid or cid in ["undefined", "null", "", "None"]:
            cid = "00000000-0000-0000-0000-000000000000"
            
    users = db.query(models.User).filter(models.User.company_id == cid).order_by(models.User.created_at.desc()).all()
    
    # Lógica de ventana 24hs sincronizada con get_conversations
    user_ids = [u.id for u in users]
    last_msg_map = {}
    if user_ids:
        from sqlalchemy import func
        stmt = db.query(models.Message.user_id, func.max(models.Message.timestamp_ms).label('max_ts'))\
                 .filter(models.Message.user_id.in_(user_ids), models.Message.sender == 'user')\
                 .group_by(models.Message.user_id).all()
        last_msg_map = {row.user_id: row.max_ts for row in stmt}
    
    current_time_ms = int(time.time() * 1000)
    window_24h_ms = 24 * 60 * 60 * 1000
    
    return [{
        "id": u.id, 
        "user": u.full_name, 
        "phone": u.phone, 
        "tags": u.tags, 
        "status": u.crm_status,
        "email": u.email,
        "address": u.address,
        "observations": u.observations,
        "assigned_to": u.assigned_to,
        "assigned_name": u.assigned_operator.full_name if u.assigned_operator else "Sin Asignar",
        "is_bot_active": u.is_bot_active,
        "is_24h_window_closed": (current_time_ms - last_msg_map.get(u.id, 0)) > window_24h_ms if last_msg_map.get(u.id) else True,
        "created_at": (u.created_at.isoformat() + "Z") if u.created_at else None
    } for u in users]

@app.put("/api/operators/{op_id}")
async def update_operator(op_id: int, op: OperatorUpdateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="No tienes permisos")
    
    db_op = db.query(models.Operator).filter(models.Operator.id == op_id, models.Operator.company_id == current.company_id).first()
    if not db_op:
        raise HTTPException(status_code=404, detail="Operador no encontrado")
    
    if op.full_name: db_op.full_name = op.full_name
    if op.password: db_op.hashed_password = get_password_hash(op.password)
    if op.role: db_op.role = op.role
    if op.email is not None: db_op.email = op.email
    if op.is_active_round_robin is not None: db_op.is_active_round_robin = op.is_active_round_robin
    
    db.commit()
    return {"status": "ok"}

@app.delete("/api/operators/{op_id}")
async def delete_operator(op_id: int, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="No tienes permisos")
        
    db_op = db.query(models.Operator).filter(models.Operator.id == op_id, models.Operator.company_id == current.company_id).first()
    if not db_op:
        raise HTTPException(status_code=404, detail="Operador no encontrado")
        
    # Reasignar leads a "Sin Asignar" antes de borrar
    db.query(models.User).filter(models.User.assigned_to == op_id).update({models.User.assigned_to: None})
    
    db.delete(db_op)
    db.commit()
    return {"status": "ok"}

# --- SUPER ADMIN (Gesti-n Global) ---

@app.get("/api/super/companies")
async def super_list_companies(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_superadmin)):
    companies = db.query(models.Company).all()
    result = []
    for c in companies:
        leads_count = db.query(models.User).filter(models.User.company_id == c.id).count()
        msg_count = db.query(models.Message).join(models.User).filter(models.User.company_id == c.id).count()
        result.append({
            "id": c.id,
            "name": c.name,
            "webhook_token": c.webhook_token,
            "is_active": c.is_active,
            "logo_url": c.logo_url,
            "leads": leads_count,
            "messages": msg_count
        })
    return result

# --- BACKUP / CONFIGURACION ---

@app.get("/api/backup/leads")
async def backup_leads(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="No tienes permisos para exportar datos")
        
    leads = db.query(models.User).filter(models.User.company_id == current.company_id).all()
    output = []
    for l in leads:
        output.append({
            "phone_id": l.id,
            "name": l.user,
            "status": l.status,
            "email": l.email,
            "address": l.address,
            "observations": l.observations,
            "assigned_to": l.assigned_to,
            "is_bot_active": l.is_bot_active,
            "created_at": l.created_at.isoformat() if hasattr(l, 'created_at') and l.created_at else None
        })
    return output

@app.post("/api/super/companies/{company_id}/logo")
@app.post("/api/companies/{company_id}/logo")
async def upload_company_logo(company_id: str, file: UploadFile = File(...), db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    # Permisos: SuperAdmin o Admin de la misma empresa
    if current.role != "super_admin":
        if current.role != "admin" or current.company_id != company_id:
            raise HTTPException(status_code=403, detail="No autorizado")
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    
    # Cloud Run has an ephemeral file system. To prevent logos from disappearing on redeploys,
    # we convert it to Base64 and store it directly in the db logo_url field.
    import base64
    file_bytes = await file.read()
    b64_string = base64.b64encode(file_bytes).decode('utf-8')
    mime_type = file.content_type or "image/png"
    data_uri = f"data:{mime_type};base64,{b64_string}"
    
    company.logo_url = data_uri
    db.commit()
    return {"ok": True, "logo_url": company.logo_url}

class CompanyUpdateSchema(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    whatsapp_waba_id: Optional[str] = None

@app.patch("/api/companies/{company_id}")
async def update_company(company_id: str, data: CompanyUpdateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role != "super_admin":
        if current.role != "admin" or current.company_id != company_id:
            raise HTTPException(status_code=403, detail="No autorizado")
            
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company: raise HTTPException(status_code=404, detail="Empresa no encontrada")
    
    if data.name is not None: company.name = data.name
    if data.whatsapp_waba_id is not None: company.whatsapp_waba_id = data.whatsapp_waba_id
    
    # is_active solo lo cambia el SuperAdmin
    if data.is_active is not None and current.role == "super_admin":
        company.is_active = data.is_active
        
    db.commit()
    return {"status": "ok", "name": company.name, "whatsapp_waba_id": company.whatsapp_waba_id}

@app.patch("/api/super/companies/{company_id}")
async def super_update_company(company_id: str, data: CompanyUpdateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_superadmin)):
    company = db.query(models.Company).filter(models.Company.id == company_id).first()
    if not company: raise HTTPException(status_code=404, detail="Empresa no encontrada")
    
    if data.name is not None: company.name = data.name
    if data.is_active is not None: company.is_active = data.is_active
    
    db.commit()
    return {"status": "ok"}

# --- CONFIGURACION DE EMPRESA (ASIGNACION) ---
class CompanySettingsSchema(BaseModel):
    assignment_mode: Optional[str] = None
    logo_data: Optional[str] = None

@app.put("/api/company/settings")
async def update_company_settings(data: CompanySettingsSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    
    company = db.query(models.Company).filter(models.Company.id == current.company_id).first()
    if not company: raise HTTPException(status_code=404, detail="Empresa no encontrada")
    
    if data.assignment_mode is not None:
        if data.assignment_mode not in ["round_robin", "manual"] and not data.assignment_mode.startswith("op_"):
            raise HTTPException(status_code=400, detail="Modo de asignación inválido")
        company.assignment_mode = data.assignment_mode
        
    if data.logo_data is not None:
        company.logo_data = data.logo_data

    db.commit()
    return {"status": "ok"}

@app.get("/api/company/settings")
async def get_company_settings(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    company = db.query(models.Company).filter(models.Company.id == current.company_id).first()
    if not company: raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return {
        "assignment_mode": company.assignment_mode,
        "logo_data": company.logo_data
    }

# --- WEBHOOKS (Públicos o con validación simple si se desea) ---

@app.post("/wh/{token}")
@app.post("/webhook/n8n/{token}")
async def receive_user_msg(token: str, msg: MessageSchema, db: Session = Depends(get_db)):
    try:
        company = db.query(models.Company).filter(models.Company.webhook_token == token).first()
        if not company: return {"status": "error", "detail": "Token inválido"}

        u_id = clean_user_id(msg.user_id)
        user = db.query(models.User).filter(models.User.id == u_id, models.User.company_id == company.id).first()
        if not user:
            assigned_op_id = get_next_operator_id(company.id, db)

            user = models.User(
                id=u_id, 
                company_id=company.id,
                full_name=msg.user_name if msg.user_name else "Cliente WhatsApp",
                phone=u_id,
                assigned_to=assigned_op_id
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            # Notificar al operador asignado por email si corresponde
            print(f"[DEBUG] Lead creado. assigned_op_id: {assigned_op_id}", flush=True)
            if assigned_op_id:
                op = db.query(models.Operator).filter(models.Operator.id == assigned_op_id).first()
                if op and op.email:
                    print(f"[DEBUG] Notificando a operador: {op.full_name} ({op.email})", flush=True)
                    subject = f"🔔 Nuevo Lead Asignado (Ingreso) - {user.full_name}"
                    html_body = f"""
                    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                        <div style="background: linear-gradient(135deg, #1E2235 0%, #2D3250 100%); color: white; padding: 25px; border-radius: 12px 12px 0 0; text-align: center;">
                            <h1 style="margin: 0; font-size: 22px;">🔔 Nuevo Lead Ingresado</h1>
                            <p style="margin: 5px 0 0; opacity: 0.8;">LiveChatPro - Notificación Inicial</p>
                        </div>
                        <div style="background: #f8fafc; padding: 25px; border: 1px solid #e2e8f0; border-top: none;">
                            <p style="color: #334155; font-size: 16px;">Hola <strong>{op.full_name}</strong>,</p>
                            <p style="color: #64748b;">Un nuevo lead acaba de conversar con tu bot y te fue asignado para tu cartera automáticamente.</p>
                            <div style="background: white; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0; margin: 15px 0;">
                                <p style="margin: 5px 0;"><strong>👤 Nombre:</strong> {user.full_name}</p>
                                <p style="margin: 5px 0;"><strong>📱 Teléfono:</strong> {user.phone or 'No disponible'}</p>
                            </div>
                        </div>
                        <div style="background: #1E2235; color: #94a3b8; padding: 15px; border-radius: 0 0 12px 12px; text-align: center; font-size: 12px;">
                            <p style="margin: 0;">LiveChatPro - Asignación Automática</p>
                        </div>
                    </div>
                    """
                    try:
                        res = send_email_smtp(op.email, subject, html_body)
                        print(f"[DEBUG] send_email_smtp result: {res}", flush=True)
                    except Exception as email_err:
                        print(f"[DEBUG] CRITICAL error calling send_email_smtp: {email_err}", flush=True)
                else:
                    print(f"[DEBUG] No hay email para operador {assigned_op_id} o no existe.", flush=True)
            else:
                print(f"[DEBUG] No assigned_op_id for new lead.", flush=True)

        # Inteligencia: si n8n manda un sender lo usamos, sino detectamos por contenido
        final_sender = msg.sender
        if "asistente virtual" in str(msg.text).lower() or "soy el bot" in str(msg.text).lower():
            final_sender = "bot"
        elif not final_sender:
            final_sender = "user"

        new_msg = models.Message(
            company_id=company.id, user_id=u_id, sender=final_sender, text=str(msg.text),
            timestamp_ms=int(time.time() * 1000)
        )
        db.add(new_msg)
        
        # Actualizar última actividad del usuario (PARA ORDEN CRONOLOGICO)
        user.last_activity = func.now()
        db.commit() # Guardamos actividad inmediatamente
        
        await manager.broadcast({"event": "new_message", "user_id": u_id, "text": new_msg.text, "sender": final_sender}, company.id)
        # Forzar actualización de posición en la lista (orden cronológico)
        await manager.broadcast({"event": "bot_status_change", "user_id": u_id, "is_bot_active": user.is_bot_active}, company.id)
        
        return {"status": "ok", "is_bot_active": user.is_bot_active}
    except Exception as e:
        db.rollback()
        return {"status": "error", "detail": str(e)}

@app.post("/webhook/n8n/handoff/{token}")
async def n8n_handoff(token: str, data: HandoffSchema, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(models.Company.webhook_token == token).first()
    if not company: return {"status": "error"}
    
    u_id = str(data.user_id).replace('"', '').replace("'", "").strip()
    user = db.query(models.User).filter(models.User.id == u_id, models.User.company_id == company.id).first()
    if not user: return {"status": "error", "detail": "Lead no encontrado"}
    
    # 1. Cambiar estado
    user.crm_status = "Esperando Operador"
    user.observations = data.resumen
    db.commit()

    # 2. Notificar por WS a toda la empresa (el frontend filtrará por assigned_to)
    await manager.broadcast({
        "event": "new_lead_assigned",
        "user_id": user.id,
        "assigned_to": user.assigned_to,
        "text": data.resumen,
        "lead_name": user.full_name
    }, company.id)

    # 3. Notificar por Email SMTP al operador asignado
    print(f"[DEBUG] Handoff para user {user.id}. assigned_to: {user.assigned_to}", flush=True)
    if user.assigned_to:
        op = db.query(models.Operator).filter(models.Operator.id == user.assigned_to).first()
        if op and op.email:
            print(f"[DEBUG] Notificando handoff a: {op.full_name} ({op.email})", flush=True)
            subject = f"🔔 Nuevo Lead Asignado - {user.full_name}"
            html_body = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #1E2235 0%, #2D3250 100%); color: white; padding: 25px; border-radius: 12px 12px 0 0; text-align: center;">
                    <h1 style="margin: 0; font-size: 22px;">🔔 Nuevo Lead Asignado</h1>
                    <p style="margin: 5px 0 0; opacity: 0.8;">LiveChatPro - Notificación</p>
                </div>
                <div style="background: #f8fafc; padding: 25px; border: 1px solid #e2e8f0; border-top: none;">
                    <p style="color: #334155; font-size: 16px;">Hola <strong>{op.full_name}</strong>,</p>
                    <p style="color: #64748b;">Se te ha asignado un nuevo lead:</p>
                    <div style="background: white; padding: 20px; border-radius: 8px; border: 1px solid #e2e8f0; margin: 15px 0;">
                        <p style="margin: 5px 0;"><strong>👤 Nombre:</strong> {user.full_name}</p>
                        <p style="margin: 5px 0;"><strong>📱 Teléfono:</strong> {user.phone or 'No disponible'}</p>
                    </div>
                    <div style="background: #eff6ff; padding: 15px; border-radius: 8px; border-left: 4px solid #3b82f6;">
                        <p style="margin: 0 0 5px; font-weight: bold; color: #1e40af;">📋 Resumen de la conversación:</p>
                        <p style="margin: 0; color: #334155; line-height: 1.6;">{data.resumen}</p>
                    </div>
                </div>
                <div style="background: #1E2235; color: #94a3b8; padding: 15px; border-radius: 0 0 12px 12px; text-align: center; font-size: 12px;">
                    <p style="margin: 0;">LiveChatPro &copy; {datetime.now().year} - Notificación automática</p>
                </div>
            </div>
            """
            try:
                res = send_email_smtp(op.email, subject, html_body)
                print(f"[DEBUG] send_email_smtp (handoff) result: {res}", flush=True)
            except Exception as email_err:
                print(f"[DEBUG] CRITICAL error calling send_email_smtp (handoff): {email_err}", flush=True)
        else:
            print(f"[DEBUG] No hay email para operador {user.assigned_to}", flush=True)
    else:
        print(f"[DEBUG] User NOT assigned. skipping email.", flush=True)
                
    return {"status": "ok"}

@app.post("/webhook/bot/{token}")
async def receive_bot_msg(token: str, msg: MessageSchema, db: Session = Depends(get_db)):
    company = db.query(models.Company).filter(models.Company.webhook_token == token).first()
    if not company: return {"status": "error", "detail": "Token inválido"}
    u_id = clean_user_id(msg.user_id)
    new_msg = models.Message(
        company_id=company.id, user_id=u_id, sender="bot", text=msg.text,
        timestamp_ms=int(msg.timestamp) if (msg.timestamp and str(msg.timestamp).isdigit()) else int(time.time() * 1000)
    )
    db.add(new_msg)
    
    # Actualizar última actividad del usuario
    db_user = db.query(models.User).filter(models.User.id == u_id, models.User.company_id == company.id).first()
    if db_user:
        db_user.last_activity = func.now()
        
    db.commit()
    await manager.broadcast({"event": "new_message", "user_id": u_id, "text": msg.text, "sender": "bot"}, company.id)
    return {"status": "ok"}

# --- CONVERSACIONES & CRM ---

@app.get("/api/conversations")
def get_conversations(
    company_id: Optional[str] = None, 
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    operator_id: Optional[str] = None,
    db: Session = Depends(get_db), 
    current: Any = Depends(get_current_operator)
):
    # PRIORIDAD 1: Parámetro en URL (?company_id=...)
    # PRIORIDAD 2: Atributo company_id del operador (que es None para el Súper Admin)
    cid = company_id or current.company_id
    
    # REGLA DE ORO: Si es Super Admin y no hay CID, o el CID es vacío, forzamos FAMIGLIA 
    # para que nunca vea el panel vacío al entrar.
    if current.role == "super_admin":
        if not cid or cid in ["undefined", "null", "", "None"]:
            cid = "00000000-0000-0000-0000-000000000000"
        
    print(f"!!! [CONV_LIST] User: {current.email}, Final CID: {cid}")
    query = db.query(models.User).filter(models.User.company_id == cid)
    
    # Regla: Los operadores convencionales solo ven sus leads
    if current.role == "operador":
        query = query.filter(models.User.assigned_to == current.id)
    else:
        # Administradores pueden filtrar por operador
        if operator_id and operator_id != "all":
            if operator_id == "manual" or operator_id == "unassigned":
                query = query.filter(models.User.assigned_to == None)
            else:
                try:
                    op_id_int = int(operator_id)
                    query = query.filter(models.User.assigned_to == op_id_int)
                except ValueError:
                    pass

    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from.replace("Z", "+00:00"))
            query = query.filter(models.User.created_at >= dt_from)
        except Exception:
            pass

    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to.replace("Z", "+00:00"))
            if "T" not in date_to:
                dt_to = dt_to + timedelta(hours=23, minutes=59, seconds=59)
            query = query.filter(models.User.created_at <= dt_to)
        except Exception:
            pass

    users = query.order_by(models.User.last_activity.desc()).all()
    
    # Obtener el último mensaje del usuario para la ventana de 24hs
    user_ids = [u.id for u in users]
    last_msg_map = {}
    if user_ids:
        stmt = db.query(models.Message.user_id, func.max(models.Message.timestamp_ms).label('max_ts'))\
                 .filter(models.Message.user_id.in_(user_ids), models.Message.sender == 'user')\
                 .group_by(models.Message.user_id).all()
        last_msg_map = {row.user_id: row.max_ts for row in stmt}
    
    current_time_ms = int(time.time() * 1000)
    window_24h_ms = 24 * 60 * 60 * 1000
    
    # Mapear info extra del operador asignado para la grilla admin
    op_ids = [u.assigned_to for u in users if u.assigned_to]
    ops = db.query(models.Operator).filter(models.Operator.id.in_(op_ids)).all()
    op_map = {op.id: op.full_name for op in ops}
    return [{
        "id": u.id, 
        "user": u.full_name, 
        "phone": u.phone, 
        "tags": u.tags, 
        "status": u.crm_status,
        "email": u.email,
        "address": u.address,
        "observations": u.observations,
        "assigned_to": u.assigned_to,
        "assigned_name": op_map.get(u.assigned_to, "Sin Asignar"),
        "is_bot_active": u.is_bot_active,
        "is_24h_window_closed": (current_time_ms - last_msg_map.get(u.id, 0)) > window_24h_ms if last_msg_map.get(u.id) else True,
        "created_at": (u.created_at.isoformat() + "Z") if hasattr(u, 'created_at') and u.created_at else None
    } for u in users]

@app.get("/api/messages/{user_id}")
def get_messages(user_id: str, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    
    # Validar que el operador tenga acceso a este lead antes de ver mensajes
    if current.role == "operador":
        user = db.query(models.User).filter(models.User.id == user_id, models.User.company_id == cid).first()
        if not user or user.assigned_to != current.id:
            raise HTTPException(status_code=403, detail="No tienes acceso a los mensajes de este lead")

    u_id = clean_user_id(user_id)
    msgs = db.query(models.Message).filter(models.Message.user_id == u_id, models.Message.company_id == cid).order_by(models.Message.timestamp_ms).all()
    return [{"from": m.sender, "text": m.text, "time": m.timestamp_ms} for m in msgs]

@app.post("/api/leads")
async def create_lead(lead: LeadCreateSchema, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    
    # Normalizar el ID antes de chequear
    u_id = clean_user_id(lead.id)

    # 1. Chequeo Global: El ID (teléfono) debe ser único en TODA la base de datos (por ser PK)
    global_lead = db.query(models.User).filter(models.User.id == u_id).first()
    if global_lead:
        if global_lead.company_id == cid:
            raise HTTPException(status_code=400, detail="Este Lead ya existe en tu empresa")
        else:
            raise HTTPException(status_code=400, detail="Este número de teléfono ya está registrado en el sistema global (otra empresa)")
    
    company = db.query(models.Company).filter(models.Company.id == cid).first()
    assigned_op_id = get_next_operator_id(cid, db)

    new_lead = models.User(
        id=lead.id,
        company_id=cid,
        full_name=lead.full_name,
        phone=lead.phone,
        tags=lead.tags,
        crm_status=lead.crm_status,
        email=lead.email,
        address=lead.address,
        observations=lead.observations,
        assigned_to=assigned_op_id
    )
    db.add(new_lead)
    db.commit()
    db.refresh(new_lead)
    return new_lead

@app.put("/api/leads/{user_id}")
async def update_lead(user_id: str, lead: LeadUpdateSchema, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    db_lead = db.query(models.User).filter(models.User.id == user_id, models.User.company_id == cid).first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")
    
    update_data = lead.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_lead, key, value)
    
    db.commit()
    db.refresh(db_lead)
    return db_lead

@app.put("/api/users/{user_id}/status")
def update_user_status(user_id: str, status_data: StatusUpdateSchema, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    user = db.query(models.User).filter(models.User.id == user_id, models.User.company_id == cid).first()
    if not user: return {"status": "error", "detail": "Lead not found"}
    if current.role == "operador" and user.assigned_to != current.id:
        return {"status": "error", "detail": "No autorizado"}
    
    user.crm_status = status_data.status
    db.commit()
    return {"status": "ok"}

@app.put("/api/users/{user_id}/tags")
def update_user_tags(user_id: str, tag_data: TagUpdateSchema, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    user = db.query(models.User).filter(models.User.id == user_id, models.User.company_id == cid).first()
    if not user: return {"status": "error", "detail": "Lead not found"}
    if current.role == "operador" and user.assigned_to != current.id:
        return {"status": "error", "detail": "No autorizado"}
        
    user.tags = tag_data.tags
    db.commit()
    return {"status": "ok"}

class AssignUpdateSchema(BaseModel):
    operator_id: Optional[int]

@app.delete("/api/leads/{user_id}")
async def delete_lead(user_id: str, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Solo administradores pueden borrar leads")
        
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    db_lead = db.query(models.User).filter(models.User.id == user_id, models.User.company_id == cid).first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")
        
    # Borrar primero todos sus mensajes
    db.query(models.Message).filter(models.Message.user_id == user_id, models.Message.company_id == cid).delete()
    
    # Borrar al lead
    db.delete(db_lead)
    db.commit()
    return {"status": "ok"}

@app.put("/api/leads/{user_id}/assign")
async def update_lead_assignment(user_id: str, data: AssignUpdateSchema, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Solo administradores pueden reasignar leads")
        
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    db_lead = db.query(models.User).filter(models.User.id == user_id, models.User.company_id == cid).first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")
        
    db_lead.assigned_to = data.operator_id
    db.commit()
    return {"status": "ok"}

@app.delete("/api/leads/{user_id}")
async def delete_lead(user_id: str, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="No tienes permisos para borrar leads")
    
    u_id = clean_user_id(user_id)
    
    # Después de la migración a ON DELETE CASCADE, el borrado es atómico y automático
    db_lead = db.query(models.User).filter(
        models.User.id == u_id, 
        models.User.company_id == current.company_id
    ).first()
    
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")
        
    db.delete(db_lead)
    db.commit()
    return {"status": "ok"}

class BotStatusUpdateSchema(BaseModel):
    is_bot_active: bool

@app.post("/api/users/{user_id}/bot_status")
@app.put("/api/users/{user_id}/bot_status")
async def update_bot_status(user_id: str, data: BotStatusUpdateSchema, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    user = db.query(models.User).filter(models.User.id == user_id, models.User.company_id == cid).first()
    if not user: return {"status": "error", "detail": "Lead not found"}
    
    user.is_bot_active = data.is_bot_active
    db.commit()
    await manager.broadcast({"event": "bot_status_change", "user_id": user_id, "is_bot_active": user.is_bot_active}, cid)
    return {"status": "ok", "is_bot_active": user.is_bot_active}


@app.get("/api/stats")
def get_stats(company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    cid = company_id if (company_id and current.role == "super_admin") else current.company_id
    total_msgs = db.query(models.Message).filter(models.Message.company_id == cid).count()
    total_users = db.query(models.User).filter(models.User.company_id == cid).count()
    return {"total_messages": total_msgs, "total_users": total_users}

@app.get("/api/stats/summary")
async def get_stats_summary(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    
    cid = current.company_id
    
    # KPIs Básicos
    total_leads = db.query(models.User).filter(models.User.company_id == cid).count()
    start_of_today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    leads_today = db.query(models.User).filter(models.User.company_id == cid, models.User.created_at >= start_of_today).count()
    total_messages = db.query(models.Message).filter(models.Message.company_id == cid).count()
    total_vendidos = db.query(models.User).filter(models.User.company_id == cid, models.User.crm_status == 'Vendido').count()
    
    # 1. Leads por día (últimos 15 días)
    fifteen_days_ago = datetime.now() - timedelta(days=15)
    leads_over_time = db.query(func.date(models.User.created_at), func.count(models.User.id))\
        .filter(models.User.company_id == cid, models.User.created_at >= fifteen_days_ago)\
        .group_by(func.date(models.User.created_at))\
        .order_by(func.date(models.User.created_at)).all()
    
    # 2. Leads por estado
    status_counts = db.query(models.User.crm_status, func.count(models.User.id))\
        .filter(models.User.company_id == cid)\
        .group_by(models.User.crm_status).all()
    
    # 3. Rendimiento por Operador (Simplificado sin CASE para evitar fallos de startup)
    op_perf_list = []
    ops = db.query(models.Operator).filter(models.Operator.company_id == cid).all()
    for op in ops:
        assigned = db.query(models.User).filter(models.User.assigned_to == op.id).count()
        won = db.query(models.User).filter(models.User.assigned_to == op.id, models.User.crm_status == 'Vendido').count()
        if assigned > 0:
            op_perf_list.append({"name": op.full_name, "assigned": assigned, "won": won})

    # 4. Promedio de Temperatura y Sentimiento
    sentiment_counts = db.query(models.LeadAnalysis.sentiment_score, func.count(models.LeadAnalysis.id))\
        .filter(models.LeadAnalysis.company_id == cid)\
        .group_by(models.LeadAnalysis.sentiment_score).all()
    
    avg_temp = db.query(func.avg(models.LeadAnalysis.temperature))\
        .filter(models.LeadAnalysis.company_id == cid).scalar() or 0

    return {
        "kpis": {
            "total_leads": total_leads,
            "leads_today": leads_today,
            "conversion_rate": round((total_vendidos / total_leads * 100), 2) if total_leads > 0 else 0,
            "total_messages": total_messages
        },
        "leads_over_time": {str(day): count for day, count in leads_over_time},
        "status_distribution": {s[0]: s[1] for s in status_counts},
        "operator_performance": op_perf_list,
        "sentiment_distribution": {s[0]: s[1] for s in sentiment_counts},
        "average_temperature": round(float(avg_temp), 2)
    }

@app.post("/api/leads/{user_id}/analyze")
async def trigger_ai_analysis(user_id: str, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    cid = current.company_id
    u_id = clean_user_id(user_id)
    res = await analyze_lead_with_ai(u_id, cid, db)
    if "error" in res:
        raise HTTPException(status_code=500, detail=res["error"])
    return res

@app.get("/api/leads/{user_id}/analysis")
def get_lead_analysis(user_id: str, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    analysis = db.query(models.LeadAnalysis).filter(
        models.LeadAnalysis.user_id == user_id,
        models.LeadAnalysis.company_id == current.company_id
    ).first()
    if not analysis:
        return {"summary": "Aún no se ha analizado este lead", "sentiment_score": "---", "top_intents": "", "temperature": 0}
    return {
        "summary": analysis.summary,
        "sentiment_score": analysis.sentiment_score,
        "top_intents": analysis.top_intents,
        "temperature": analysis.temperature,
        "last_analyzed": analysis.last_analyzed
    }

import httpx

class SendMessageSchema(BaseModel):
    user_id: str
    text: str
    phone: Optional[str] = None

async def send_n8n_webhook(url: str, payload: dict):
    try:
        async with httpx.AsyncClient() as client:
            await client.post(url, json=payload, timeout=10.0)
    except Exception as e:
        print(f"Error enviando a n8n: {e}")

@app.post("/api/messages/send")
async def send_message(msg: SendMessageSchema, background_tasks: BackgroundTasks, company_id: Optional[str] = None, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    try:
        cid = company_id if (company_id and current.role == "super_admin") else current.company_id
        u_id = clean_user_id(msg.user_id)
        
        new_msg = models.Message(
            company_id=cid, user_id=u_id, sender="human", text=msg.text,
            timestamp_ms=int(time.time() * 1000)
        )
        db.add(new_msg)
        
        # Actualizar última actividad y desactivar bot si interviene humano
        db_user = db.query(models.User).filter(models.User.id == u_id, models.User.company_id == cid).first()
        if db_user:
            db_user.last_activity = func.now()
            db_user.is_bot_active = False # Deactivación automática
            db.commit()
            # Broadcast del cambio de estado del bot
            await manager.broadcast({"event": "bot_status_change", "user_id": u_id, "is_bot_active": False}, cid)

        db.commit()

        # BROADCAST INMEDIATO para respuesta instantánea en el panel
        await manager.broadcast({
            "event": "new_message", 
            "user_id": u_id, 
            "text": msg.text, 
            "sender": "human"
        }, cid)

        # Forzar reordenamiento en la lista
        await manager.broadcast({"event": "bot_status_change", "user_id": u_id, "is_bot_active": False}, cid)

        # Obtener datos de la empresa para tokens y n8n
        company = db.query(models.Company).filter(models.Company.id == cid).first()
        if not company:
            return {"status": "error", "detail": "Empresa no encontrada"}

        # Enviar mensaje a WhatsApp (Outbound) en el caso de operador humano
        if company.whatsapp_token and company.whatsapp_phone_id:
            background_tasks.add_task(
                send_whatsapp_text,
                db_user.phone if db_user else u_id,
                msg.text,
                company.whatsapp_token,
                company.whatsapp_phone_id,
                cid
            )

        # Enviar a n8n en segundo plano (Background Task) para no bloquear al operador
        background_tasks.add_task(notify_n8n, company.webhook_token, {
            "user_id": u_id,
            "text": msg.text,
            "sender": "human",
            "is_bot_active": False,
            "phone": db_user.phone if db_user else u_id
        })

        return {"status": "ok"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "detail": str(e)}

async def send_whatsapp_text(phone: str, text: str, company_token: str, phone_id: str, company_id: str):
    """Envía un mensaje de texto libre (no plantilla) vía WhatsApp Cloud API."""
    url = f"https://graph.facebook.com/{WHATSAPP_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {company_token}",
        "Content-Type": "application/json"
    }
    
    clean_phone = "".join(filter(str.isdigit, phone))
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_phone,
        "type": "text",
        "text": { 
            "preview_url": False, 
            "body": text 
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, headers=headers, timeout=15)
            print(f"[OUTBOUND] [{company_id}] Sent to {phone}: {res.status_code}", flush=True)
            if not res.is_success:
                print(f"[OUTBOUND] [{company_id}] Error: {res.text}", flush=True)
            return res.is_success
    except Exception as e:
        print(f"[OUTBOUND] [{company_id}] Exception sending to {phone}: {e}", flush=True)
        return False

# --- REMARKETING (WHATSAPP BROADCAST) ---

async def send_whatsapp_cloud_api(phone: str, template_name: str, company_token: str, phone_id: str, company_id: str, language_code: str = "es_AR", components: list = None):
    """Envía una plantilla de WhatsApp usando la API de Meta Business Manager."""
    url = f"https://graph.facebook.com/{WHATSAPP_VERSION}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {company_token}",
        "Content-Type": "application/json"
    }
    
    # Limpiamos el teléfono (solo números)
    clean_phone = "".join(filter(str.isdigit, phone))
    
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": clean_phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": { "code": language_code }
        }
    }

    if components:
        payload["template"]["components"] = components
    
    # ULTIMATE DEBUG: Log the final lowercase payload
    print(f"[REMARKETING] [{company_id}] FINAL PAYLOAD: {json.dumps(payload)}", flush=True)

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(url, json=payload, headers=headers, timeout=15)
            # LOG PROFUNDO: Para ver qué dice Meta exactamente
            print(f"[REMARKETING] [{company_id}] Sent to {phone}: {res.status_code}", flush=True)
            print(f"[REMARKETING] [{company_id}] Response: {res.text}", flush=True)
            return res.is_success
    except Exception as e:
        print(f"[REMARKETING] [{company_id}] Exception sending to {phone}: {e}", flush=True)
        return False

@app.get("/api/remarketing/leads")
async def get_remarketing_leads(
    status: str = Query("all"),
    tag: str = Query(""),
    db: Session = Depends(get_db),
    current: models.Operator = Depends(get_current_operator)
):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
        
    query = db.query(models.User).filter(models.User.company_id == current.company_id)
    
    if status != "all":
        query = query.filter(models.User.crm_status.ilike(status))
    
    if tag:
        query = query.filter(models.User.tags.like(f"%{tag}%"))
        
    leads = query.order_by(models.User.last_activity.desc()).all()
    return [{
        "id": l.id,
        "name": l.full_name or l.id,
        "phone": l.phone,
        "status": l.crm_status,
        "tags": l.tags
    } for l in leads]

@app.get("/api/remarketing/templates")
async def get_whatsapp_templates(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    company = db.query(models.Company).filter(models.Company.id == current.company_id).first()
    if not company or not company.whatsapp_token or not company.whatsapp_phone_id:
        return {"templates": [], "error": "WhatsApp no configurado"}

    token = company.whatsapp_token
    phone_id = company.whatsapp_phone_id
    waba_id = company.whatsapp_waba_id

    async with httpx.AsyncClient() as client:
        # Descubrimiento automático si falta el WABA ID
        if not waba_id:
            url_discovery = f"https://graph.facebook.com/v20.0/{phone_id}?fields=whatsapp_business_account"
            headers = {"Authorization": f"Bearer {token}"}
            try:
                resp = await client.get(url_discovery, headers=headers)
                data = resp.json()
                if "whatsapp_business_account" in data:
                    waba_id = data["whatsapp_business_account"]["id"]
                    company.whatsapp_waba_id = waba_id
                    db.commit()
                else:
                    return {"templates": [], "error": "No se pudo detectar el WABA ID automáticamente"}
            except Exception as e:
                return {"templates": [], "error": f"Error de conexión: {str(e)}"}

        # Obtener plantillas de Meta
        url_templates = f"https://graph.facebook.com/v20.0/{waba_id}/message_templates"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp_t = await client.get(url_templates, headers=headers)
            templates_data = resp_t.json()
            if "error" in templates_data:
                return {"templates": [], "error": templates_data["error"]["message"]}
            
            # Solo plantillas aprobadas
            valid_templates = [
                {
                    "name": t["name"],
                    "status": t["status"],
                    "language": t["language"],
                    "category": t["category"],
                    "components": t.get("components", [])
                }
                for t in templates_data.get("data", []) if t["status"] == "APPROVED"
            ]
            return {"templates": valid_templates, "waba_id": waba_id}
        except Exception as e:
            return {"templates": [], "error": f"Error al consultar plantillas: {str(e)}"}

@app.post("/api/remarketing/send")
async def send_remarketing_campaign(
    background_tasks: BackgroundTasks,
    template_name: str = Body(..., embed=True),
    user_ids: List[str] = Body(..., embed=True),
    language_code: str = Body("es_AR", embed=True),
    components: List[dict] = Body(None, embed=True),
    db: Session = Depends(get_db),
    current: models.Operator = Depends(get_current_operator)
):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
        
    company = db.query(models.Company).filter(models.Company.id == current.company_id).first()
    if not company:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    
    # Prioridad: 1. DB (Seguro), 2. Environment (Legacy)
    wa_token = company.whatsapp_token or os.environ.get("WHATSAPP_TOKEN")
    wa_phone_id = company.whatsapp_phone_id or os.environ.get("WHATSAPP_PHONE_ID")
    
    if not wa_token or len(wa_token) < 10:
        raise HTTPException(status_code=500, detail="Configuración de WhatsApp Cloud API pendiente.")
        
    if not wa_phone_id or len(wa_phone_id) < 5:
        raise HTTPException(status_code=500, detail="Configuración de WhatsApp Cloud ID pendiente.")

    leads = db.query(models.User).filter(
        models.User.id.in_(user_ids), 
        models.User.company_id == current.company_id
    ).all()
    
    print(f"[REMARKETING] Dispatching campaign '{template_name}' ({language_code}) to {len(leads)} leads", flush=True)
    
    count = 0
    for lead in leads:
        if lead.phone:
            background_tasks.add_task(
                send_whatsapp_cloud_api, 
                lead.phone, template_name, wa_token, wa_phone_id, current.company_id, language_code, components
            )
            count += 1
            
    return {"status": "ok", "dispatched": count}

@app.post("/api/remarketing/upload")
async def upload_remarketing_media(
    request: Request,
    file: UploadFile = File(...),
    current: models.Operator = Depends(get_current_operator)
):
    if current.role not in ["admin", "super_admin"]:
        raise HTTPException(status_code=403, detail="Permisos insuficientes")
    
    # Validar extensión
    ext = file.filename.split(".")[-1].lower()
    if ext not in ["jpg", "jpeg", "png", "mp4", "pdf"]:
        raise HTTPException(status_code=400, detail="Formato no soportado")
    
    filename = f"{uuid.uuid4()}.{ext}"
    os.makedirs("static/uploads", exist_ok=True)
    file_path = os.path.join("static/uploads", filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # IMPORTANTE: WhatsApp requiere URLs absolutas para las plantillas multimedia
    public_url = f"{str(request.base_url).rstrip('/')}/static/uploads/{filename}"
    return {"url": public_url}
    return {"url": public_url}

@app.websocket("/ws/{username}")
async def websocket_endpoint(websocket: WebSocket, username: str, db: Session = Depends(get_db)):
    operator = db.query(models.Operator).filter(models.Operator.username == username).first()
    if not operator: 
        await websocket.close()
        return
    await manager.connect(websocket, operator.company_id)
    try:
        while True:
            data = await websocket.receive_text()
            # No actions from operator yet needed
    except WebSocketDisconnect:
        manager.disconnect(websocket, operator.company_id)

# --- FRONTEND (HTML/JS) ---

@app.get("/", response_class=HTMLResponse)
def root():
    with open("templates/livechat.html", "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

  
