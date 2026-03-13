from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from database import get_db, engine
import models
from pydantic import BaseModel
from typing import Optional, List
import time
import os
from datetime import datetime, timedelta
from jose import JWTError, jwt
import bcrypt
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm

# Crear tablas si no existen
models.Base.metadata.create_all(bind=engine)

app = FastAPI()
app.mount("/static", StaticFiles(directory="."), name="static")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# --- SEGURIDAD ---
SECRET_KEY = os.getenv("SECRET_KEY", "mmarketing_secret_key_2024")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 # 1 día

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

async def get_current_operator(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudo validar el acceso",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    operator = db.query(models.Operator).filter(models.Operator.username == username).first()
    if operator is None:
        raise credentials_exception
    return operator

# --- SCHEMAS ---
class MessageSchema(BaseModel):
    user_id: str
    text: str
    user_name: Optional[str] = "Usuario WhatsApp"
    phone: Optional[str] = None
    timestamp: Optional[float] = None

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

# --- LOGIN & AUTH ---

@app.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    operator = db.query(models.Operator).filter(models.Operator.username == form_data.username).first()
    if not operator or not verify_password(form_data.password, operator.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(data={"sub": operator.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/me")
async def read_users_me(current_user: models.Operator = Depends(get_current_operator)):
    return {"username": current_user.username, "full_name": current_user.full_name, "role": current_user.role}

# --- OPERADORES ---

@app.post("/api/operators")
async def create_operator(op: OperatorCreateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role != "admin":
        raise HTTPException(status_code=403, detail="No tienes permisos para crear operadores")
    
    db_op = db.query(models.Operator).filter(models.Operator.username == op.username).first()
    if db_op:
        raise HTTPException(status_code=400, detail="El usuario ya existe")
    
    new_op = models.Operator(
        username=op.username,
        hashed_password=get_password_hash(op.password),
        full_name=op.full_name,
        role=op.role
    )
    db.add(new_op)
    db.commit()
    return {"status": "ok"}

@app.get("/api/operators")
async def list_operators(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    if current.role != "admin":
        return [current]
    return db.query(models.Operator).all()

# --- WEBHOOKS (Públicos o con validación simple si se desea) ---

@app.post("/webhook/n8n")
def receive_user_msg(msg: MessageSchema, db: Session = Depends(get_db)):
    try:
        u_id = str(msg.user_id).replace('"', '').replace("'", "").strip()
        user = db.query(models.User).filter(models.User.id == u_id).first()
        if not user:
            user = models.User(
                id=u_id, 
                full_name=msg.user_name if msg.user_name else "Cliente WhatsApp",
                phone=u_id
            )
            db.add(user)
            db.commit()
            db.refresh(user)

        new_msg = models.Message(
            user_id=u_id, sender="user", text=str(msg.text),
            timestamp_ms=int(time.time() * 1000)
        )
        db.add(new_msg)
        db.commit()
        return {"status": "ok"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "detail": str(e)}

@app.post("/webhook/bot")
def receive_bot_msg(msg: MessageSchema, db: Session = Depends(get_db)):
    new_msg = models.Message(
        user_id=msg.user_id, sender="bot", text=msg.text,
        timestamp_ms=int(msg.timestamp) if msg.timestamp else int(time.time() * 1000)
    )
    db.add(new_msg)
    db.commit()
    return {"status": "ok"}

# --- CONVERSACIONES & CRM ---

@app.get("/api/conversations")
def get_conversations(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    users = db.query(models.User).order_by(models.User.created_at.desc()).all()
    return [{
        "id": u.id, 
        "user": u.full_name, 
        "phone": u.phone, 
        "tags": u.tags, 
        "status": u.crm_status,
        "email": u.email,
        "address": u.address,
        "observations": u.observations
    } for u in users]

@app.get("/api/messages/{user_id}")
def get_messages(user_id: str, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    msgs = db.query(models.Message).filter(models.Message.user_id == user_id).order_by(models.Message.timestamp_ms).all()
    return [{"from": m.sender, "text": m.text, "time": m.timestamp_ms} for m in msgs]

@app.post("/api/leads")
async def create_lead(lead: LeadCreateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    db_lead = db.query(models.User).filter(models.User.id == lead.id).first()
    if db_lead:
        raise HTTPException(status_code=400, detail="El Lead (teléfono) ya existe")
    
    new_lead = models.User(
        id=lead.id,
        full_name=lead.full_name,
        phone=lead.phone,
        tags=lead.tags,
        crm_status=lead.crm_status,
        email=lead.email,
        address=lead.address,
        observations=lead.observations
    )
    db.add(new_lead)
    db.commit()
    db.refresh(new_lead)
    return new_lead

@app.put("/api/leads/{user_id}")
async def update_lead(user_id: str, lead: LeadUpdateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    db_lead = db.query(models.User).filter(models.User.id == user_id).first()
    if not db_lead:
        raise HTTPException(status_code=404, detail="Lead no encontrado")
    
    update_data = lead.dict(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_lead, key, value)
    
    db.commit()
    db.refresh(db_lead)
    return db_lead

@app.post("/api/users/{user_id}/status")
def update_user_status(user_id: str, status_data: StatusUpdateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user: return {"status": "error", "detail": "Lead not found"}
    user.crm_status = status_data.status
    db.commit()
    return {"status": "ok"}

@app.post("/api/users/{user_id}/tags")
def update_user_tags(user_id: str, tag_data: TagUpdateSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user: return {"status": "error", "detail": "Lead not found"}
    user.tags = tag_data.tags
    db.commit()
    return {"status": "ok"}

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    total_msgs = db.query(models.Message).count()
    total_users = db.query(models.User).count()
    return {"total_messages": total_msgs, "total_users": total_users}

import httpx

class SendMessageSchema(BaseModel):
    user_id: str
    text: str
    phone: Optional[str] = None

@app.post("/api/messages/send")
async def send_message(msg: SendMessageSchema, db: Session = Depends(get_db), current: models.Operator = Depends(get_current_operator)):
    try:
        new_msg = models.Message(
            user_id=msg.user_id, sender="human", text=msg.text,
            timestamp_ms=int(time.time() * 1000)
        )
        db.add(new_msg)
        db.commit()

        n8n_webhook_url = os.getenv("N8N_WEBHOOK_URL", "https://livechat-final-356139909399.us-central1.run.app/webhook/n8n")
        async with httpx.AsyncClient() as client:
            await client.post(n8n_webhook_url, json={
                "action": "send_message",
                "phone": msg.phone,
                "user_id": msg.user_id,
                "text": msg.text,
                "operator": current.username
            })
        return {"status": "ok"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "detail": str(e)}

# --- FRONTEND (HTML/JS) ---

@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Famiglia Viajes - LiveChat & CRM</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap');
            :root {
                --zoho-sidebar: #1E2235;
                --zoho-bg: #F4F7FB;
                --zoho-border: #E2E8F0;
                --zoho-blue: #1D4ED8;
                --zoho-text: #334155;
            }
            body { font-family: 'Inter', sans-serif; background-color: var(--zoho-bg); color: var(--zoho-text); }
            .sidebar-item { transition: all 0.2s; border-left: 4px solid transparent; }
            .sidebar-item.active { background: rgba(255,255,255,0.05); border-left-color: var(--zoho-blue); color: white !important; }
            .zoho-card { background: white; border: 1px solid var(--zoho-border); overflow: hidden; }
            .view-hidden { display: none !important; }
            ::-webkit-scrollbar { width: 5px; height: 5px; }
            ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
            .btn-zoho { font-weight: 600; transition: all 0.2s; display: inline-flex; items-center: center; gap: 0.5rem; }
            .btn-zoho:active { transform: scale(0.98); }
            table thead th { background: #F8FAFC; color: #64748B; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--zoho-border); }
            table tbody tr:last-child { border-bottom: none; }
        </style>
    </head>
    <body class="h-screen flex text-slate-700">
        
        <!-- PANTALLA DE LOGIN -->
        <div id="login-screen" class="flex-1 flex items-center justify-center p-6 bg-slate-50">
            <div class="w-full max-w-sm bg-white p-8 rounded border border-slate-200 shadow-sm">
                <div class="text-center mb-8">
                    <img src="/static/logo-mmarketing%20iso.png" alt="Logo" class="w-12 mx-auto mb-4 grayscale opacity-80">
                    <h2 class="text-xl font-bold text-slate-800">Acceso al Sistema</h2>
                    <p class="text-slate-400 text-xs mt-1">LiveChat & CRM Corporativo</p>
                </div>
                <div class="space-y-5">
                    <div>
                        <label class="text-[10px] font-bold uppercase text-slate-500 mb-1.5 block">Usuario</label>
                        <input type="text" id="login-user" class="w-full bg-white border border-slate-200 rounded px-4 py-3 text-sm outline-none focus:border-blue-500 transition-colors" placeholder="nombre.apellido">
                    </div>
                    <div>
                        <label class="text-[10px] font-bold uppercase text-slate-500 mb-1.5 block">Contraseña</label>
                        <input type="password" id="login-pass" class="w-full bg-white border border-slate-200 rounded px-4 py-3 text-sm outline-none focus:border-blue-500 transition-colors" placeholder="••••••••">
                    </div>
                    <button onclick="doLogin()" class="w-full bg-[#1D4ED8] hover:bg-blue-700 text-white font-bold py-3 rounded text-sm transition-all shadow-lg shadow-blue-500/20 active:scale-[0.98]">
                        Iniciar Sesión
                    </button>
                    <p id="login-error" class="text-red-500 text-[10px] text-center mt-4 hidden font-medium"></p>
                </div>
            </div>
        </div>

        <!-- APP PRINCIPAL -->
        <div id="app-screen" class="view-hidden flex-1 flex overflow-hidden">
            
            <!-- SIDEBAR BARRA LATERAL -->
            <aside class="w-64 bg-[#1E2235] flex flex-col shrink-0 z-50">
                <div class="p-6 border-b border-white/5 flex items-center gap-3">
                    <img src="/static/logo-mmarketing%20iso.png" alt="Logo" class="w-8 brightness-0 invert">
                    <span class="font-black text-white text-lg tracking-tight">LiveChat<span class="text-blue-400">Pro</span></span>
                </div>

                <nav class="flex-1 py-6">
                    <button onclick="showView('conversations')" id="nav-conversations" class="sidebar-item active w-full flex items-center gap-4 px-6 py-4 text-slate-400 font-semibold hover:text-white hover:bg-white/5 text-sm">
                        <i class="fas fa-comments w-5"></i> Conversaciones
                    </button>
                    <button onclick="showView('crm')" id="nav-crm" class="sidebar-item w-full flex items-center gap-4 px-6 py-4 text-slate-400 font-semibold hover:text-white hover:bg-white/5 text-sm">
                        <i class="fas fa-users w-5"></i> CRM Leads
                    </button>
                    <button onclick="showView('operators')" id="nav-operators" class="sidebar-item w-full flex items-center gap-4 px-6 py-4 text-slate-400 font-semibold hover:text-white hover:bg-white/5 text-sm">
                        <i class="fas fa-shield-alt w-5"></i> Operadores
                    </button>
                </nav>

                <div class="p-6 border-t border-white/5 bg-black/20">
                    <div class="flex items-center gap-3">
                        <div class="w-10 h-10 rounded-lg bg-blue-600 flex items-center justify-center text-white font-bold text-sm shadow-lg shadow-blue-900/40" id="me-avatar">A</div>
                        <div class="flex-1 min-w-0">
                            <p class="text-xs font-bold text-white truncate" id="me-name">---</p>
                            <p class="text-[9px] text-slate-500 font-black uppercase tracking-wider" id="me-role">---</p>
                        </div>
                        <button onclick="doLogout()" class="text-slate-500 hover:text-red-400 transition">
                            <i class="fas fa-sign-out-alt"></i>
                        </button>
                    </div>
                </div>
            </aside>

            <!-- CONTENIDO DERECHA -->
            <div class="flex-1 flex flex-col min-w-0">
                <header class="bg-white border-b border-slate-200 h-16 flex items-center px-8 shrink-0 relative">
                    <h2 id="view-title" class="text-sm font-black text-slate-400 uppercase tracking-widest">Conversaciones</h2>
                    <div class="ml-auto flex items-center gap-4">
                        <div class="w-8 h-8 rounded-full bg-slate-100 flex items-center justify-center text-slate-400">
                            <i class="fas fa-bell text-xs"></i>
                        </div>
                    </div>
                </header>

            <!-- VISTA CONVERSACIONES (CHAT) -->
            <main id="view-conversations" class="flex-1 flex overflow-hidden p-6 gap-6">
                <!-- Lista Leads Izquierda -->
                <div class="w-96 bg-white rounded-3xl border border-white crm-shadow flex flex-col overflow-hidden">
                    <div class="p-6 border-b flex justify-between items-center">
                        <h3 class="text-sm font-black text-slate-800 uppercase tracking-widest">Chat Activos</h3>
                        <span id="stat-total-users" class="bg-blue-100 text-blue-600 text-[10px] font-black px-2 py-1 rounded-full">0</span>
                    </div>
                    <div id="user-list" class="overflow-y-auto flex-1"></div>
                </div>

                <!-- Ventana Chat Derecha -->
                <div class="flex-1 flex flex-col bg-white rounded-3xl border border-white crm-shadow overflow-hidden relative">
                    <div id="chat-header-info" class="p-6 border-b flex justify-between items-center bg-white/50 backdrop-blur-md sticky top-0 z-10 hidden">
                        <div>
                            <h2 id="chat-title" class="text-xl font-black text-slate-800">Selecciona un Chat</h2>
                            <div id="chat-meta" class="flex items-center gap-3 mt-1">
                                <p id="chat-phone" class="text-xs text-blue-600 font-bold"></p>
                                <div id="chat-tags-display" class="flex gap-1"></div>
                            </div>
                        </div>
                        <div class="flex gap-2">
                             <button onclick="toggleTagInput()" class="w-8 h-8 rounded-lg bg-indigo-50 text-indigo-500 hover:bg-indigo-100 transition"><i class="fas fa-tag"></i></button>
                        </div>
                    </div>
                    
                    <div id="tag-edit-panel" class="hidden p-4 bg-indigo-50 border-b border-indigo-100 flex items-center gap-3">
                        <input type="text" id="new-tag-input" class="flex-1 bg-white border border-indigo-200 rounded-xl px-4 py-2 text-xs" placeholder="Nueva etiqueta...">
                        <button onclick="addCurrentTag()" class="bg-indigo-600 text-white text-xs px-4 py-2 rounded-xl font-bold">Agregar</button>
                    </div>

                    <div id="chat-window" class="flex-1 p-8 overflow-y-auto space-y-6 bg-[#fcfdff]">
                        <div class="flex flex-col items-center justify-center h-full text-slate-300">
                             <i class="fas fa-comments text-6xl mb-4"></i>
                             <p class="font-bold">Selecciona una conversación para empezar</p>
                        </div>
                    </div>

                    <div id="chat-input-area" class="p-5 bg-white border-t border-slate-50 flex items-center gap-4 hidden">
                        <input type="text" id="message-input" class="flex-1 bg-slate-50 border border-slate-100 rounded-2xl px-6 py-4 text-sm focus:ring-2 focus:ring-blue-500/20 transition outline-none" placeholder="Escribe un mensaje..." onkeypress="if(event.key === 'Enter') sendMessage()">
                        <button onclick="sendMessage()" class="bg-blue-600 hover:bg-blue-700 text-white w-14 h-14 rounded-2xl shadow-xl shadow-blue-200 flex items-center justify-center transition active:scale-95">
                            <i class="fas fa-paper-plane text-xl"></i>
                        </button>
                    </div>
                </div>
            </main>

            <!-- VISTA CRM (NUEVO) -->
            <main id="view-crm" class="flex-1 p-8 view-hidden overflow-y-auto">
                <div class="max-w-6xl mx-auto">
                    <div class="flex justify-between items-center mb-10">
                        <div>
                            <h1 class="text-3xl font-black text-slate-800">Gestión de Leads</h1>
                            <p class="text-slate-400">Base de datos centralizada de clientes</p>
                        </div>
                        <button onclick="openNewLeadModal()" class="bg-blue-600 hover:bg-blue-700 text-white px-8 py-4 rounded-2xl font-bold shadow-xl shadow-blue-100 transition flex items-center gap-3">
                            <i class="fas fa-user-plus"></i> Nuevo Lead
                        </button>
                    </div>

                    <div class="bg-white rounded-3xl crm-shadow border border-white overflow-hidden mb-8">
                        <div class="p-6 border-b border-slate-50 flex items-center gap-4 bg-slate-50/30">
                            <div class="relative flex-1">
                                <i class="fas fa-search absolute left-5 top-1/2 -translate-y-1/2 text-slate-300"></i>
                                <input type="text" id="crm-search" placeholder="Buscar por nombre o teléfono..." 
                                    class="w-full bg-white border border-slate-100 rounded-2xl pl-12 pr-6 py-4 text-sm focus:ring-4 focus:ring-blue-500/10 focus:border-blue-500 transition outline-none"
                                    oninput="filterCRMLeads()">
                            </div>
                            <select id="crm-filter-status" onchange="filterCRMLeads()" 
                                class="bg-white border border-slate-100 rounded-2xl px-4 py-4 text-sm font-bold text-slate-600 focus:ring-4 focus:ring-blue-500/10 focus:border-blue-500 transition outline-none shadow-sm">
                                <option value="all">TODOS LOS ESTADOS</option>
                                <option value="No Contactado">NO CONTACTADO</option>
                                <option value="Contactado">CONTACTADO</option>
                                <option value="Interesado">INTERESADO</option>
                                <option value="Vendido">VENDIDO</option>
                            </select>
                            <div class="text-slate-400 text-xs font-bold px-4">
                                <span id="crm-count-visible">0</span> / <span id="crm-count-total">0</span> leads
                            </div>
                        </div>
                        <table class="w-full text-left">
                            <thead class="bg-slate-50 border-b border-slate-100">
                                <tr>
                                    <th class="p-6 text-[10px] font-black uppercase text-slate-400">Cliente</th>
                                    <th class="p-6 text-[10px] font-black uppercase text-slate-400">Teléfono</th>
                                    <th class="p-6 text-[10px] font-black uppercase text-slate-400">Estado</th>
                                    <th class="p-6 text-[10px] font-black uppercase text-slate-400">Etiquetas</th>
                                    <th class="p-6 text-[10px] font-black uppercase text-slate-400 text-right">Acciones</th>
                                </tr>
                            </thead>
                            <tbody id="crm-table-body"></tbody>
                        </table>
                    </div>
                </div>
            </main>

            <!-- VISTA OPERADORES (NUEVO) -->
            <main id="view-operators" class="flex-1 p-8 view-hidden overflow-y-auto">
                <div class="max-w-4xl mx-auto">
                    <div class="flex justify-between items-center mb-10">
                        <div>
                            <h1 class="text-3xl font-black text-slate-800">Operadores</h1>
                            <p class="text-slate-400">Administra quién accede al sistema</p>
                        </div>
                        <button id="btn-new-op" onclick="openNewOpModal()" class="bg-slate-800 hover:bg-slate-900 text-white px-8 py-4 rounded-2xl font-bold shadow-xl shadow-slate-100 transition view-hidden">
                            Nuevo Operador
                        </button>
                    </div>

                    <div id="operators-list" class="grid grid-cols-1 md:grid-cols-2 gap-6"></div>
                </div>
            </main>
        </div>

        <!-- MODALES -->
        <div id="modal-container" class="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-[100] flex items-center justify-center view-hidden">
            <!-- Modal Lead -->
            <div id="modal-lead" class="w-full max-w-lg bg-white rounded-3xl p-10 crm-shadow view-hidden">
                <h2 id="modal-lead-title" class="text-2xl font-black mb-6">Crear Nuevo Lead</h2>
                <input type="hidden" id="edit-lead-id">
                <div class="space-y-4 max-h-[60vh] overflow-y-auto px-1">
                    <div>
                        <label class="text-[10px] font-black uppercase text-slate-400 ml-2">Nombre Completo</label>
                        <input type="text" id="lead-name" placeholder="Ej: Juan Pérez" class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100 focus:border-blue-500 transition">
                    </div>
                    <div>
                        <label class="text-[10px] font-black uppercase text-slate-400 ml-2">Teléfono (WhatsApp ID)</label>
                        <input type="text" id="lead-phone" placeholder="Ej: 549341123456" class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100 focus:border-blue-500 transition">
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="text-[10px] font-black uppercase text-slate-400 ml-2">Email</label>
                            <input type="email" id="lead-email" placeholder="mail@ejemplo.com" class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100 focus:border-blue-500 transition">
                        </div>
                        <div>
                            <label class="text-[10px] font-black uppercase text-slate-400 ml-2">Estado</label>
                            <select id="lead-status" class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100 focus:border-blue-500 transition">
                                <option value="No Contactado">No Contactado</option>
                                <option value="Contactado">Contactado</option>
                                <option value="Interesado">Interesado</option>
                                <option value="Vendido">Vendido</option>
                            </select>
                        </div>
                    </div>
                    <div>
                        <label class="text-[10px] font-black uppercase text-slate-400 ml-2">Dirección</label>
                        <input type="text" id="lead-address" placeholder="Ej: Calle Falsa 123" class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100 focus:border-blue-500 transition">
                    </div>
                    <div>
                        <label class="text-[10px] font-black uppercase text-slate-400 ml-2">Observaciones</label>
                        <textarea id="lead-observations" placeholder="Notas sobre el cliente..." class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100 focus:border-blue-500 transition h-24"></textarea>
                    </div>
                </div>
                <div class="flex gap-4 mt-8">
                    <button onclick="closeModal()" class="flex-1 bg-slate-100 py-4 rounded-2xl font-bold text-slate-600 hover:bg-slate-200 transition">Cancelar</button>
                    <button onclick="saveLead()" class="flex-1 bg-blue-600 py-4 rounded-2xl font-bold text-white shadow-lg shadow-blue-100 hover:bg-blue-700 transition">Guardar Lead</button>
                </div>
            </div>
            
            <!-- Modal Operador -->
            <div id="modal-operator" class="w-full max-w-lg bg-white rounded-3xl p-10 crm-shadow view-hidden">
                <h2 class="text-2xl font-black mb-6 text-slate-800">Nuevo Operador</h2>
                <div class="space-y-4">
                    <input type="text" id="op-name" placeholder="Nombre completo" class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100">
                    <input type="text" id="op-user" placeholder="Usuario" class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100">
                    <input type="password" id="op-pass" placeholder="Contraseña" class="w-full bg-slate-50 p-4 rounded-2xl outline-none border border-slate-100">
                </div>
                <div class="flex gap-4 mt-8">
                    <button onclick="closeModal()" class="flex-1 bg-slate-50 py-4 rounded-2xl font-bold text-slate-400">Cancelar</button>
                    <button onclick="saveOperator()" class="flex-1 bg-slate-800 py-4 rounded-2xl font-bold text-white">Crear Usuario</button>
                </div>
            </div>
        </div>

        <script>
            let auth_token = localStorage.getItem('token');
            let currentUser = null;
            let activeUserId = null;
            let activeUserPhone = null;
            let currentTags = [];
            let lastMsgCount = 0;
            let crmLeads = []; // Global storage for CRM leads

            // --- NAVEGACION ---
            function showView(viewId, skipLoad = false) {
                document.querySelectorAll('main').forEach(v => v.classList.add('view-hidden'));
                document.getElementById('view-' + viewId).classList.remove('view-hidden');
                
                document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
                document.getElementById('nav-' + viewId).classList.add('active');

                if (skipLoad) return;
                if (viewId === 'conversations') loadConversations();
                if (viewId === 'crm') loadCRMLeads();
                if (viewId === 'operators') loadOperators();
            }

            // --- AUTH ---
            async function checkAuth() {
                if (!auth_token) {
                    document.getElementById('login-screen').classList.remove('view-hidden');
                    document.getElementById('app-screen').classList.add('view-hidden');
                    return;
                }
                try {
                    const res = await fetch('/api/me', { headers: { 'Authorization': 'Bearer ' + auth_token }});
                    if (res.ok) {
                        currentUser = await res.json();
                        document.getElementById('login-screen').classList.add('view-hidden');
                        document.getElementById('app-screen').classList.remove('view-hidden');
                        document.getElementById('me-name').innerText = currentUser.full_name;
                        document.getElementById('me-role').innerText = currentUser.role;
                        if (currentUser.role === 'admin') document.getElementById('nav-operators').classList.remove('hidden');
                        if (currentUser.role === 'admin') document.getElementById('btn-new-op')?.classList.remove('view-hidden');
                        showView('conversations');
                    } else { throw new Error(); }
                } catch(e) { doLogout(); }
            }

            async function doLogin() {
                const user = document.getElementById('login-user').value;
                const pass = document.getElementById('login-pass').value;
                const body = new URLSearchParams();
                body.append('username', user);
                body.append('password', pass);

                try {
                    const res = await fetch('/token', { method: 'POST', body });
                    const data = await res.json();
                    if (res.ok) {
                        localStorage.setItem('token', data.access_token);
                        auth_token = data.access_token;
                        document.getElementById('login-error').classList.add('hidden');
                        checkAuth();
                    } else {
                        document.getElementById('login-error').innerText = data.detail || 'Error al entrar';
                        document.getElementById('login-error').classList.remove('hidden');
                    }
                } catch(e) { }
            }

            function doLogout() {
                localStorage.removeItem('token');
                auth_token = null;
                location.reload();
            }

            // --- CONVERSACIONES ---
            async function loadConversations() {
                const res = await fetch('/api/conversations', { headers: { 'Authorization': 'Bearer ' + auth_token }});
                const data = await res.json();
                document.getElementById('stat-total-users').innerText = data.length;
                document.getElementById('user-list').innerHTML = data.map(c => `
                    <div onclick="selectChat('${c.id}', '${c.user}', '${c.phone}', '${c.tags || ''}')" class="px-6 py-4 border-b border-slate-100 cursor-pointer hover:bg-slate-50 transition-all flex items-center gap-3 ${activeUserId === c.id ? 'bg-blue-50/50 border-r-2 border-r-blue-600' : ''}">
                        <div class="w-10 h-10 rounded-lg bg-slate-100 flex items-center justify-center text-slate-400 font-bold text-xs border border-slate-200 shrink-0 overflow-hidden">
                            <img src="https://ui-avatars.com/api/?name=${encodeURIComponent(c.user)}&background=F1F5F9&color=64748B&size=128" class="w-full h-full">
                        </div>
                        <div class="flex-1 min-w-0">
                            <p class="font-bold text-sm text-slate-800 truncate">${c.user}</p>
                            <p class="text-[10px] text-slate-500 font-medium">${c.phone}</p>
                            <div class="flex gap-1 mt-1 truncate">
                                ${c.tags ? c.tags.split(',').map(t => `<span class="bg-slate-100 text-slate-400 text-[8px] font-bold px-1.5 py-0.5 rounded border border-slate-200 uppercase">${t}</span>`).join('') : ''}
                            </div>
                        </div>
                    </div>
                `).join('');
            }

            async function selectChat(id, name, phone, tagsStr) {
                showView('conversations', true); // Cambia a vista chat sin recargar la lista de conversaciones
                activeUserId = id;
                activeUserPhone = phone;
                lastMsgCount = 0;
                currentTags = tagsStr ? tagsStr.split(',').filter(t => t.trim()) : [];
                
                document.getElementById('chat-header-info').classList.remove('hidden');
                document.getElementById('chat-input-area').classList.remove('hidden');
                document.getElementById('chat-title').innerText = name;
                document.getElementById('chat-phone').innerText = phone;
                
                renderTagsInChat();
                loadMessages();
                loadConversations(); // Para marcar el seleccionado
            }

            function renderTagsInChat() {
                document.getElementById('chat-tags-display').innerHTML = currentTags.map(t => `<span class="bg-indigo-600 text-white text-[9px] font-black px-2 py-0.5 rounded-full flex items-center gap-1">${t} <i class="fas fa-times cursor-pointer" onclick="removeTagRecord('${t}')"></i></span>`).join('');
            }

            function toggleTagInput() {
                document.getElementById('tag-edit-panel').classList.toggle('hidden');
            }

            async function addCurrentTag() {
                const input = document.getElementById('new-tag-input');
                const tag = input.value.trim();
                if (tag && !currentTags.includes(tag)) {
                    currentTags.push(tag);
                    renderTagsInChat();
                    input.value = '';
                    await fetch('/api/users/' + activeUserId + '/tags', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + auth_token },
                        body: JSON.stringify({ tags: currentTags.join(',') })
                    });
                }
            }

            async function removeTagRecord(tag) {
                currentTags = currentTags.filter(t => t !== tag);
                renderTagsInChat();
                await fetch('/api/users/' + activeUserId + '/tags', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + auth_token },
                    body: JSON.stringify({ tags: currentTags.join(',') })
                });
            }

            async function loadMessages() {
                if (!activeUserId) return;
                const res = await fetch('/api/messages/' + activeUserId, { headers: { 'Authorization': 'Bearer ' + auth_token }});
                const messages = await res.json();
                const window = document.getElementById('chat-window');
                
                window.innerHTML = messages.map(m => {
                    const isSelf = m.from === 'bot' || m.from === 'human';
                    const bubbleClass = isSelf ? 'bg-blue-600 text-white border-blue-700' : 'bg-white text-slate-700 border-slate-200';
                    const senderLabel = m.from === 'human' ? 'Operador' : (m.from === 'bot' ? 'Asistente IA' : 'Cliente');
                    
                    return `
                    <div class="flex flex-col ${isSelf ? 'items-end' : 'items-start'} mb-4">
                        <div class="${bubbleClass} border px-4 py-2.5 rounded-2xl max-w-[80%] text-sm leading-relaxed shadow-sm">
                            ${m.text}
                        </div>
                        <span class="text-[9px] text-slate-400 mt-1 mx-2 font-bold uppercase tracking-wider">${senderLabel}</span>
                    </div>
                    `;
                }).join('');
                if (messages.length > lastMsgCount) {
                    window.scrollTop = window.scrollHeight;
                    lastMsgCount = messages.length;
                }
            }

            async function sendMessage() {
                const input = document.getElementById('message-input');
                const text = input.value.trim();
                if (!activeUserId || !text) return;
                input.value = '';
                try {
                    await fetch('/api/messages/send', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + auth_token },
                        body: JSON.stringify({ user_id: activeUserId, text: text, phone: activeUserPhone })
                    });
                    loadMessages();
                } catch(e) {}
            }

            // --- CRM ---
            async function loadCRMLeads() {
                const res = await fetch('/api/conversations', { headers: { 'Authorization': 'Bearer ' + auth_token }});
                crmLeads = await res.json();
                renderCRMTable(crmLeads);
            }

            function filterCRMLeads() {
                const query = document.getElementById('crm-search').value.toLowerCase().trim();
                const statusFilter = document.getElementById('crm-filter-status').value;

                const filtered = crmLeads.filter(l => {
                    const name = (l.user || "").toLowerCase();
                    const phone = (l.phone || l.id || "").toLowerCase();
                    const status = l.status || "No Contactado";
                    
                    const matchesSearch = name.includes(query) || phone.includes(query);
                    const matchesStatus = statusFilter === 'all' || status === statusFilter;
                    
                    return matchesSearch && matchesStatus;
                });
                renderCRMTable(filtered);
            }

            function renderCRMTable(leads) {
                document.getElementById('crm-count-visible').innerText = leads.length;
                document.getElementById('crm-count-total').innerText = crmLeads.length;
                
                document.getElementById('crm-table-body').innerHTML = leads.map(l => {
                    const displayName = l.user || l.phone || 'Usuario Sin Nombre';
                    const displayPhone = l.phone || l.id || 'N/A';
                    const waLink = `https://wa.me/${displayPhone.replace(/[^0-9]/g, '')}`;
                    
                    // Status Badge Logic
                    const statusColors = {
                        'No Contactado': 'bg-slate-100 text-slate-500 border-slate-200',
                        'Contactado': 'bg-blue-50 text-blue-600 border-blue-100',
                        'Interesado': 'bg-indigo-50 text-indigo-600 border-indigo-100',
                        'Vendido': 'bg-emerald-50 text-emerald-600 border-emerald-100'
                    };
                    const statusClass = statusColors[l.status] || statusColors['No Contactado'];

                    return `
                    <tr class="border-b border-slate-100 hover:bg-slate-50/50 transition-colors group">
                        <td class="px-6 py-4">
                            <div class="flex items-center gap-3">
                                <div class="w-8 h-8 rounded bg-slate-100 text-slate-400 flex items-center justify-center font-bold text-[10px] border border-slate-200">${displayName.charAt(0).toUpperCase()}</div>
                                <div class="min-w-0">
                                    <span class="font-semibold text-slate-800 block text-sm truncate">${displayName}</span>
                                    <span class="text-[10px] text-slate-400 truncate block">${l.email || 'Sin email'}</span>
                                </div>
                            </div>
                        </td>
                        <td class="px-6 py-4">
                            <div class="flex flex-col">
                                <span class="text-xs text-slate-600 font-medium">${displayPhone}</span>
                                <span class="text-[10px] text-slate-400 truncate max-w-[120px]">${l.address || 'Sin dirección'}</span>
                            </div>
                        </td>
                        <td class="px-6 py-4">
                            <div class="relative inline-block text-[10px]">
                                <select onchange="updateLeadStatus('${l.id}', this.value)" 
                                    class="appearance-none font-bold px-3 py-1 rounded border outline-none bg-white transition-all cursor-pointer ${statusClass}">
                                    <option value="No Contactado" ${l.status === 'No Contactado' ? 'selected' : ''}>NO CONTACTADO</option>
                                    <option value="Contactado" ${l.status === 'Contactado' ? 'selected' : ''}>CONTACTADO</option>
                                    <option value="Interesado" ${l.status === 'Interesado' ? 'selected' : ''}>INTERESADO</option>
                                    <option value="Vendido" ${l.status === 'Vendido' ? 'selected' : ''}>VENDIDO</option>
                                </select>
                            </div>
                        </td>
                        <td class="px-6 py-4">
                            <div class="flex gap-1 flex-wrap max-w-[150px]">
                                ${l.tags ? l.tags.split(',').map(t => `<span class="bg-slate-50 text-slate-400 text-[9px] font-bold px-2 py-0.5 rounded border border-slate-100 uppercase">${t}</span>`).join('') : '<span class="text-slate-300 text-[10px]">Sin etiquetas</span>'}
                            </div>
                        </td>
                        <td class="px-6 py-4 text-right">
                             <div class="flex justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                <a href="${waLink}" target="_blank" class="w-8 h-8 flex items-center justify-center rounded text-emerald-500 hover:bg-emerald-50 border border-transparent hover:border-emerald-100 transition-all" title="WhatsApp">
                                    <i class="fab fa-whatsapp"></i>
                                </a>
                                <button onclick="openEditLeadModal('${l.id}')" class="w-8 h-8 flex items-center justify-center rounded text-slate-400 hover:text-blue-600 hover:bg-blue-50 border border-transparent hover:border-blue-100 transition-all" title="Editar">
                                    <i class="fas fa-pencil-alt text-xs"></i>
                                </button>
                                <button onclick="selectChat('${l.id}', '${displayName}', '${displayPhone}', '${l.tags || ''}')" class="w-8 h-8 flex items-center justify-center rounded text-blue-600 hover:bg-blue-600 hover:text-white border border-transparent transition-all" title="Chat">
                                    <i class="fas fa-comment-dots text-xs"></i>
                                </button>
                             </div>
                        </td>
                    </tr>
                `; }).join('');
            }

            async function updateLeadStatus(id, newStatus) {
                await fetch('/api/users/' + id + '/status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + auth_token },
                    body: JSON.stringify({ status: newStatus })
                });
            }

            // --- OPERADORES ---
            async function loadOperators() {
                const res = await fetch('/api/operators', { headers: { 'Authorization': 'Bearer ' + auth_token }});
                const data = await res.json();
                document.getElementById('operators-list').innerHTML = data.map(o => `
                    <div class="bg-white p-5 rounded border border-slate-200 flex items-center justify-between hover:border-slate-300 transition-colors">
                         <div class="flex items-center gap-3">
                            <div class="w-9 h-9 rounded bg-[#1E2235] text-white flex items-center justify-center font-bold text-xs">${o.username.charAt(0).toUpperCase()}</div>
                            <div>
                                <h4 class="font-bold text-slate-800 text-sm">${o.full_name}</h4>
                                <p class="text-[10px] text-slate-400 font-bold uppercase tracking-wider">${o.username} • ${o.role}</p>
                            </div>
                         </div>
                    </div>
                `).join('');
            }

            // --- MODALES OPS ---
              function openNewLeadModal() { 
                document.getElementById('modal-lead-title').innerText = "Crear Nuevo Lead";
                document.getElementById('edit-lead-id').value = "";
                document.getElementById('lead-name').value = "";
                document.getElementById('lead-phone').value = "";
                document.getElementById('lead-phone').disabled = false;
                document.getElementById('lead-email').value = "";
                document.getElementById('lead-address').value = "";
                document.getElementById('lead-observations').value = "";
                document.getElementById('lead-status').value = "No Contactado";
                
                document.getElementById('modal-container').classList.remove('view-hidden');
                document.getElementById('modal-lead').classList.remove('view-hidden');
            }

            function openEditLeadModal(leadId) {
                const lead = crmLeads.find(l => l.id === leadId);
                if (!lead) return;

                document.getElementById('modal-lead-title').innerText = "Editar Lead";
                document.getElementById('edit-lead-id').value = lead.id;
                document.getElementById('lead-name').value = lead.user || "";
                document.getElementById('lead-phone').value = lead.phone || lead.id || "";
                document.getElementById('lead-phone').disabled = true; 
                document.getElementById('lead-email').value = lead.email || "";
                document.getElementById('lead-address').value = lead.address || "";
                document.getElementById('lead-observations').value = lead.observations || "";
                document.getElementById('lead-status').value = lead.status;

                document.getElementById('modal-container').classList.remove('view-hidden');
                document.getElementById('modal-lead').classList.remove('view-hidden');
            }

            function closeModal() {
                document.getElementById('modal-container').classList.add('view-hidden');
                document.getElementById('modal-lead').classList.add('view-hidden');
                document.getElementById('modal-operator').classList.add('view-hidden');
            }

            async function saveLead() {
                const editId = document.getElementById('edit-lead-id').value;
                const name = document.getElementById('lead-name').value;
                const phone = document.getElementById('lead-phone').value;
                const email = document.getElementById('lead-email').value;
                const address = document.getElementById('lead-address').value;
                const observations = document.getElementById('lead-observations').value;
                const statusValue = document.getElementById('lead-status').value;

                if (!name || !phone) {
                    alert("Nombre y teléfono son obligatorios");
                    return;
                }

                const payload = {
                    full_name: name,
                    phone: phone,
                    email: email,
                    address: address,
                    observations: observations,
                    crm_status: statusValue
                };

                let res;
                if (editId) {
                    // Update
                    res = await fetch('/api/leads/' + editId, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + auth_token },
                        body: JSON.stringify(payload)
                    });
                } else {
                    // Create
                    payload.id = phone;
                    res = await fetch('/api/leads', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + auth_token },
                        body: JSON.stringify(payload)
                    });
                }

                if (res.ok) { 
                    closeModal(); 
                    loadCRMLeads(); 
                    if (activeUserId === editId) loadConversations();
                } else { 
                    const err = await res.json();
                    alert("Error: " + (err.detail || "No se pudo guardar el lead")); 
                }
            }

            async function saveOperator() {
                const name = document.getElementById('op-name').value;
                const user = document.getElementById('op-user').value;
                const pass = document.getElementById('op-pass').value;
                if (!name || !user || !pass) return;

                const res = await fetch('/api/operators', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + auth_token },
                    body: JSON.stringify({ username: user, password: pass, full_name: name })
                });
                if (res.ok) { closeModal(); loadOperators(); }
                else { alert("Error al crear operador."); }
            }

            // --- INIT ---
            setInterval(() => {
                if (activeUserId && document.getElementById('view-conversations').offsetParent !== null) loadMessages();
                if (document.getElementById('view-conversations').offsetParent !== null) loadConversations();
            }, 5000);

            checkAuth();
        </script>
    </body>
    </html>
    """

  
