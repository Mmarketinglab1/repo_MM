from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
import models
from pydantic import BaseModel
from typing import Optional
import time

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class MessageSchema(BaseModel):
    user_id: str
    text: str
    user_name: Optional[str] = "Usuario Nuevo"
    timestamp: Optional[float] = None

# --- ENDPOINTS DE WEBHOOK (N8N) ---

@app.post("/webhook/n8n")
def receive_user_msg(msg: MessageSchema, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == msg.user_id).first()
    if not user:
        user = models.User(id=msg.user_id, full_name=msg.user_name)
        db.add(user)
    
    new_msg = models.Message(
        user_id=msg.user_id,
        sender="user",
        text=msg.text,
        timestamp_ms=int(msg.timestamp) if msg.timestamp else int(time.time() * 1000)
    )
    db.add(new_msg)
    db.commit()
    return {"status": "ok"}

@app.post("/webhook/bot")
def receive_bot_msg(msg: MessageSchema, db: Session = Depends(get_db)):
    new_msg = models.Message(
        user_id=msg.user_id,
        sender="bot",
        text=msg.text,
        timestamp_ms=int(msg.timestamp) if msg.timestamp else int(time.time() * 1000)
    )
    db.add(new_msg)
    db.commit()
    return {"status": "ok"}

# --- ENDPOINTS DE API PARA EL PANEL ---

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    total_msgs = db.query(models.Message).count()
    total_users = db.query(models.User).count()
    bot_msgs = db.query(models.Message).filter(models.Message.sender == "bot").count()
    return {
        "total_messages": total_msgs,
        "total_users": total_users,
        "bot_efficiency": f"{round((bot_msgs/total_msgs)*100)}%" if total_msgs > 0 else "0%"
    }

@app.get("/api/conversations")
def get_conversations(db: Session = Depends(get_db)):
    users = db.query(models.User).all()
    return [{"id": u.id, "user": u.full_name} for u in users]

@app.get("/api/messages/{user_id}")
def get_messages(user_id: str, db: Session = Depends(get_db)):
    msgs = db.query(models.Message).filter(models.Message.user_id == user_id).order_by(models.Message.timestamp_ms).all()
    return [{"from": m.sender, "text": m.text, "time": m.timestamp_ms} for m in msgs]

# --- EL PANEL VISUAL (FRONTEND) ---


@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>AI CRM & LiveChat</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    </head>
    <body class="bg-slate-100 h-screen flex flex-col font-sans text-gray-800">
        
        <nav class="bg-white border-b p-4 flex justify-between items-center shadow-sm">
            <div class="flex items-center gap-2">
                <div class="bg-blue-600 p-2 rounded-lg text-white">
                    <i class="fas fa-robot text-xl"></i>
                </div>
                <h1 class="text-xl font-bold tracking-tight">AI CRM <span class="text-blue-600">Pro</span></h1>
            </div>
            <div class="flex gap-6">
                <div class="text-center"><p class="text-[10px] text-gray-400 uppercase">Leads</p><p id="stat-users" class="font-bold">0</p></div>
                <div class="text-center"><p class="text-[10px] text-gray-400 uppercase">Interacciones</p><p id="stat-msgs" class="font-bold">0</p></div>
            </div>
        </nav>

        <div class="flex flex-1 overflow-hidden">
            <div class="w-96 bg-white border-r flex flex-col shadow-inner">
                <div class="p-4 border-b bg-gray-50 flex justify-between items-center">
                    <span class="text-xs font-bold text-gray-500 uppercase">Lista de Contactos</span>
                    <i class="fas fa-filter text-gray-400 cursor-pointer hover:text-blue-500"></i>
                </div>
                <div id="user-list" class="overflow-y-auto flex-1">
                    </div>
            </div>

            <div class="flex-1 flex flex-col bg-white">
                <div id="chat-header" class="p-4 border-b flex justify-between items-center bg-white">
                    <div class="flex items-center gap-3">
                        <div id="user-avatar" class="w-10 h-10 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold hidden"></div>
                        <div>
                            <p id="chat-user-name" class="font-bold text-gray-700">Selecciona un prospecto</p>
                            <p id="chat-user-id" class="text-[10px] text-gray-400"></p>
                        </div>
                    </div>
                    <button onclick="clearView()" class="text-gray-400 hover:text-orange-500 transition-colors flex items-center gap-2 text-sm border px-3 py-1 rounded-md hover:bg-orange-50">
                        <i class="fas fa-broom"></i> Limpiar Vista
                    </button>
                </div>

                <div id="chat-window" class="flex-1 p-6 overflow-y-auto space-y-4 bg-slate-50 shadow-inner">
                    </div>
            </div>
        </div>

        <script>
            let currentUserId = null;
            let lastCount = 0;
            let isViewCleared = false;

            async function updateStats() {
                const res = await fetch('/api/stats');
                const s = await res.json();
                document.getElementById('stat-users').innerText = s.total_users;
                document.getElementById('stat-msgs').innerText = s.total_messages;
            }

            async function loadConversations() {
                const res = await fetch('/api/conversations');
                const data = await res.json();
                const list = document.getElementById('user-list');
                list.innerHTML = data.map(c => `
                    <div onclick="selectChat('${c.id}', '${c.user}')" class="p-4 border-b hover:bg-blue-50 cursor-pointer transition-all flex items-center gap-3 ${currentUserId === c.id ? 'bg-blue-50 border-r-4 border-r-blue-600' : ''}">
                        <div class="w-10 h-10 rounded-full bg-gray-200 flex items-center justify-center text-gray-500 font-bold">
                            ${c.user.charAt(0)}
                        </div>
                        <div class="flex-1 min-w-0">
                            <p class="font-bold text-sm text-gray-900 truncate">${c.user}</p>
                            <p class="text-xs text-gray-500 truncate">ID: ${c.id}</p>
                        </div>
                    </div>
                `).join('');
            }

            async function selectChat(id, name) {
                currentUserId = id;
                lastCount = 0;
                isViewCleared = false; // Resetear el estado de limpieza al cambiar de chat
                document.getElementById('chat-user-name').innerText = name;
                document.getElementById('chat-user-id').innerText = "ID: " + id;
                document.getElementById('user-avatar').innerText = name.charAt(0);
                document.getElementById('user-avatar').classList.remove('hidden');
                loadMessages();
            }

            function clearView() {
                document.getElementById('chat-window').innerHTML = "";
                isViewCleared = true;
            }

            async function loadMessages() {
                if (!currentUserId || isViewCleared) return;
                const res = await fetch('/api/messages/' + currentUserId);
                const messages = await res.json();
                messages.sort((a, b) => a.time - b.time);
                
                const window = document.getElementById('chat-window');
                window.innerHTML = messages.map(m => `
                    <div class="flex ${m.from === 'user' ? 'justify-start' : 'justify-end'}">
                        <div class="${m.from === 'user' ? 'bg-white border border-gray-200 text-gray-800' : 'bg-blue-600 text-white shadow-sm'} p-3 rounded-2xl max-w-md text-sm">
                            ${m.text}
                        </div>
                    </div>
                `).join('');
                
                if (messages.length > lastCount) {
                    window.scrollTop = window.scrollHeight;
                    lastCount = messages.length;
                }
            }

            setInterval(() => { 
                loadConversations(); 
                updateStats();
                if(currentUserId && !isViewCleared) loadMessages(); 
            }, 3000);
            
            updateStats();
            loadConversations();
        </script>
    </body>
    </html>
    """
