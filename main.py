from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from database import get_db
import models
from pydantic import BaseModel
from typing import Optional
import time
from datetime import datetime
import os

app = FastAPI()
app.mount("/static", StaticFiles(directory="."), name="static")

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class MessageSchema(BaseModel):
    user_id: str
    text: str
    user_name: Optional[str] = "Usuario WhatsApp" # Valor por defecto si falla
    phone: Optional[str] = None
    timestamp: Optional[float] = None

@app.post("/webhook/n8n")
def receive_user_msg(msg: MessageSchema, db: Session = Depends(get_db)):
    try:
        # LIMPIEZA TOTAL: Quitamos comillas, espacios y aseguramos que sea string
        u_id = str(msg.user_id).replace('"', '').replace("'", "").strip()
        
        # 1. Buscar o crear usuario con el ID limpio
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

        # 2. Guardar mensaje vinculado al ID limpio
        new_msg = models.Message(
            user_id=u_id,
            sender="user",
            text=str(msg.text),
            timestamp_ms=int(time.time() * 1000)
        )
        db.add(new_msg)
        db.commit()
        return {"status": "ok"}
    except Exception as e:
        db.rollback()
        print(f"Error detallado: {e}")
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

@app.get("/api/conversations")
def get_conversations(db: Session = Depends(get_db)):
    users = db.query(models.User).all()
    return [{"id": u.id, "user": u.full_name, "phone": u.phone, "tags": u.tags} for u in users]

@app.get("/api/messages/{user_id}")
def get_messages(user_id: str, db: Session = Depends(get_db)):
    msgs = db.query(models.Message).filter(models.Message.user_id == user_id).order_by(models.Message.timestamp_ms).all()
    return [{"from": m.sender, "text": m.text, "time": m.timestamp_ms} for m in msgs]

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    total_msgs = db.query(models.Message).count()
    total_users = db.query(models.User).count()
    return {"total_messages": total_msgs, "total_users": total_users}

class TagUpdateSchema(BaseModel):
    tags: str

@app.post("/api/users/{user_id}/tags")
def update_user_tags(user_id: str, tag_data: TagUpdateSchema, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
         return {"status": "error", "detail": "User not found"}
    user.tags = tag_data.tags
    db.commit()
    return {"status": "ok"}

import httpx

class SendMessageSchema(BaseModel):
    user_id: str
    text: str
    phone: Optional[str] = None
    
@app.post("/api/messages/send")
async def send_message(msg: SendMessageSchema, db: Session = Depends(get_db)):
    try:
        # 1. Guardar el mensaje en la base de datos (sender = "human")
        new_msg = models.Message(
            user_id=msg.user_id,
            sender="human",
            text=msg.text,
            timestamp_ms=int(time.time() * 1000)
        )
        db.add(new_msg)
        db.commit()

        # 2. Enviar el mensaje al Webhook de N8N
        n8n_webhook_url = os.getenv("N8N_WEBHOOK_URL", "URL_PENDIENTE") # TODO: Cargar por .env
        if n8n_webhook_url != "URL_PENDIENTE":
            async with httpx.AsyncClient() as client:
                await client.post(n8n_webhook_url, json={
                   "action": "send_message",
                   "phone": msg.phone,
                   "user_id": msg.user_id,
                   "text": msg.text
                })

        return {"status": "ok"}
    except Exception as e:
        db.rollback()
        return {"status": "error", "detail": str(e)}

@app.get("/", response_class=HTMLResponse)
def root():
    return """
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <title>Module-M IA Dashboard</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
        <style>
            .crm-shadow { box-shadow: 0 4px 20px -5px rgba(0,0,0,0.1); }
            .user-card:hover { transform: translateX(5px); background-color: #f8fafc; }
            ::-webkit-scrollbar { width: 6px; }
            ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 10px; }
        </style>
    </head>
    <body class="bg-[#f0f2f5] h-screen flex flex-col font-sans text-slate-700 p-6">
        
        <header class="bg-white rounded-2xl p-6 mb-6 flex justify-between items-center crm-shadow border border-white/50">
            <div class="flex items-center gap-6">
                <div class="w-16 h-16 bg-white rounded-xl flex items-center justify-center p-1">
                    <img src="/static/logo-mmarketing%20iso.png" alt="Logo" class="w-full h-full object-contain">
                </div>
                <h1 class="text-2xl font-bold text-slate-800 tracking-tight">Module-M IA Assistant Dashboard</h1>
            </div>
            <div class="flex gap-12 mr-8">
                <div class="text-right border-r pr-12 border-slate-100">
                    <p id="stat-users" class="text-3xl font-black text-slate-900">0</p>
                    <p class="text-[10px] text-slate-400 uppercase font-black">Leads</p>
                </div>
                <div class="text-right">
                    <p id="stat-msgs" class="text-3xl font-black text-slate-900">0</p>
                    <p class="text-[10px] text-slate-400 uppercase font-black">Mensajes</p>
                </div>
            </div>
        </header>

        <div class="flex flex-1 overflow-hidden gap-6">
            <div class="w-96 bg-white rounded-2xl border border-white/50 crm-shadow flex flex-col overflow-hidden">
                <div class="p-5 border-b flex justify-between items-center bg-slate-50/50">
                    <span class="text-[11px] font-black text-slate-400 uppercase tracking-widest">Tabla de Leads</span>
                </div>
                <div id="user-list" class="overflow-y-auto flex-1"></div>
            </div>

            <div class="flex-1 flex flex-col bg-white rounded-2xl border border-white/50 crm-shadow relative overflow-hidden">
                <div id="chat-header" class="p-5 border-b flex flex-col gap-3 bg-white z-10 transition-all">
                    <div class="flex justify-between items-center w-full">
                        <div>
                            <h2 id="chat-title" class="text-lg font-bold text-slate-800">Seleccione un Lead</h2>
                            <p id="chat-phone" class="text-xs text-blue-600 font-medium"></p>
                        </div>
                        <button onclick="clearView()" class="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-xl text-xs font-bold transition shadow-lg shadow-blue-200">
                            <i class="fas fa-broom mr-2"></i>Limpiar Vista
                        </button>
                    </div>
                    <!-- Zona de Etiquetas -->
                    <div id="chat-tags-container" class="hidden flex-wrap items-center gap-2 mt-1">
                        <div id="tags-list" class="flex flex-wrap gap-2"></div>
                        <button onclick="showAddTagInput()" class="w-6 h-6 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-500 flex items-center justify-center text-xs transition">
                            <i class="fas fa-plus"></i>
                        </button>
                        <div id="add-tag-container" class="hidden items-center gap-2">
                            <input type="text" id="new-tag-input" class="text-xs border border-slate-200 rounded px-2 py-1 outline-none focus:border-blue-500 w-24" placeholder="Nueva tag..." onkeypress="if(event.key === 'Enter') addTag()">
                            <button onclick="addTag()" class="text-xs bg-slate-800 text-white px-2 py-1 rounded hover:bg-slate-700">Add</button>
                        </div>
                    </div>
                </div>

                <div id="chat-window" class="flex-1 p-8 overflow-y-auto space-y-6 bg-[#f8fafc]"></div>
                
                <!-- Caja de Input para responder -->
                <div id="chat-input-container" class="hidden p-4 bg-white border-t border-slate-100 flex items-center gap-3">
                    <input type="text" id="message-input" class="flex-1 bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500/50 focus:border-blue-500 transition" placeholder="Escribe un mensaje al cliente..." onkeypress="if(event.key === 'Enter') sendMessage()">
                    <button onclick="sendMessage()" class="bg-blue-600 hover:bg-blue-700 text-white w-12 h-12 rounded-xl flex items-center justify-center shadow-lg shadow-blue-200 transition">
                        <i class="fas fa-paper-plane"></i>
                    </button>
                </div>
            </div>
        </div>

        <script>
            let currentUserId = null;
            let currentUserPhone = null;
            let currentTags = [];
            let lastCount = 0;
            let isViewCleared = false;

            function formatTime(ts) {
                const d = new Date(ts);
                return d.toLocaleString('es-AR', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' });
            }

            async function updateStats() {
                try {
                    const res = await fetch('/api/stats');
                    const s = await res.json();
                    document.getElementById('stat-users').innerText = s.total_users;
                    document.getElementById('stat-msgs').innerText = s.total_messages;
                } catch(e) { console.error("Error stats:", e); }
            }

            function renderTags(tagsString) {
                if (!tagsString) return '';
                const tags = tagsString.split(',').filter(t => t.trim() !== '');
                return tags.map(t => `<span class="bg-indigo-50 border border-indigo-100 text-indigo-600 px-2 py-0.5 rounded-md text-[9px] font-bold uppercase tracking-wider">${t.trim()}</span>`).join('');
            }

            async function loadConversations() {
                try {
                    const res = await fetch('/api/conversations');
                    const data = await res.json();
                    document.getElementById('user-list').innerHTML = data.map(c => `
                        <div onclick="selectChat('${c.id}', '${c.user}', '${c.phone}', '${c.tags || ''}')" class="user-card p-5 border-b border-slate-50 cursor-pointer flex items-center gap-4 border-l-4 transition-all ${currentUserId === c.id ? 'border-blue-600 bg-blue-50' : 'border-transparent'}">
                            <div class="w-12 h-12 rounded-full border-2 border-white shadow-sm flex items-center justify-center overflow-hidden bg-slate-200 shrink-0">
                               <img src="https://ui-avatars.com/api/?name=${encodeURIComponent(c.user)}&background=random" class="w-full h-full">
                            </div>
                            <div class="flex-1 min-w-0">
                                <p class="font-bold text-sm text-slate-800 truncate">${c.user}</p>
                                <p class="text-[10px] text-blue-500 font-medium">${c.phone || 'Sin número'}</p>
                                <div class="flex gap-1 mt-1 flex-wrap">
                                    ${renderTags(c.tags)}
                                </div>
                            </div>
                        </div>
                    `).join('');
                    
                    // Update current tags if we are viewing a user
                    if (currentUserId && !isViewCleared) {
                         const me = data.find(u => u.id === currentUserId);
                         if (me && me.tags !== currentTags.join(',')) {
                             currentTags = me.tags ? me.tags.split(',').filter(t => t.trim()) : [];
                             renderCurrentTags();
                         }
                    }
                } catch(e) {}
            }

            function renderCurrentTags() {
                const container = document.getElementById('tags-list');
                container.innerHTML = currentTags.map(tag => `
                    <span class="bg-indigo-100 text-indigo-700 px-2 py-1 rounded text-[10px] font-bold flex items-center gap-1">
                        ${tag}
                        <i class="fas fa-times cursor-pointer hover:text-red-500 ml-1" onclick="removeTag('${tag}')"></i>
                    </span>
                `).join('');
            }

            function showAddTagInput() {
                document.getElementById('add-tag-container').style.display = 'flex';
                document.getElementById('new-tag-input').focus();
            }

            async function addTag() {
                const input = document.getElementById('new-tag-input');
                const tag = input.value.trim();
                
                if (tag && !currentTags.includes(tag) && currentUserId) {
                    currentTags.push(tag);
                    renderCurrentTags();
                    input.value = '';
                    document.getElementById('add-tag-container').style.display = 'none';
                    await saveTagsToBackend();
                }
            }

            async function removeTag(tagToRemove) {
                if (!currentUserId) return;
                currentTags = currentTags.filter(t => t !== tagToRemove);
                renderCurrentTags();
                await saveTagsToBackend();
            }

            async function saveTagsToBackend() {
                try {
                    await fetch('/api/users/' + currentUserId + '/tags', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ tags: currentTags.join(',') })
                    });
                    loadConversations();
                } catch (e) { console.error("Error saving tags", e); }
            }

            function selectChat(id, name, phone, tagsStr) {
                currentUserId = id;
                currentUserPhone = phone;
                isViewCleared = false;
                lastCount = 0;
                
                currentTags = tagsStr ? tagsStr.split(',').filter(t => t.trim()) : [];
                
                document.getElementById('chat-title').innerText = name;
                document.getElementById('chat-phone').innerText = "Teléfono: " + (phone || 'No disponible');
                
                document.getElementById('chat-input-container').style.display = 'flex';
                document.getElementById('chat-tags-container').style.display = 'flex';
                document.getElementById('add-tag-container').style.display = 'none';
                
                renderCurrentTags();
                loadMessages();
            }

            function clearView() {
                document.getElementById('chat-window').innerHTML = "<div class='text-center text-slate-400 text-xs mt-10'>Vista despejada</div>";
                document.getElementById('chat-input-container').style.display = 'none';
                document.getElementById('chat-tags-container').style.display = 'none';
                isViewCleared = true;
            }

            async function sendMessage() {
                if (!currentUserId) return;
                const input = document.getElementById('message-input');
                const text = input.value.trim();
                if (!text) return;

                // Optimistic UI update
                input.value = '';
                const windowDiv = document.getElementById('chat-window');
                windowDiv.innerHTML += `
                    <div class="flex flex-col items-end mb-4">
                        <div class="bg-emerald-500 text-white shadow-sm shadow-emerald-100 p-4 rounded-2xl max-w-lg text-sm border border-emerald-600">
                            ${text}
                        </div>
                        <span class="text-[9px] text-slate-400 mt-1 mx-2">Ahora (Tú)</span>
                    </div>
                `;
                windowDiv.scrollTop = windowDiv.scrollHeight;

                try {
                    await fetch('/api/messages/send', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            user_id: currentUserId,
                            text: text,
                            phone: currentUserPhone !== 'null' ? currentUserPhone : null
                        })
                    });
                    // Reload forcefully after sending to sync
                    lastCount = 0; 
                    loadMessages();
                } catch(e) {
                    console.error("Error sending message", e);
                }
            }

            async function loadMessages() {
                if (!currentUserId || isViewCleared) return;
                try {
                    const res = await fetch('/api/messages/' + currentUserId);
                    const messages = await res.json();
                    const windowDiv = document.getElementById('chat-window');
                    
                    windowDiv.innerHTML = messages.map(m => {
                        let alignment = m.from === 'bot' || m.from === 'human' ? 'items-end' : 'items-start';
                        let style = '';
                        let senderLabel = '';

                        if (m.from === 'user') {
                            style = 'bg-white border border-slate-100 text-slate-700';
                            senderLabel = m.time ? formatTime(m.time) : '';
                        } else if (m.from === 'human') {
                            style = 'bg-emerald-500 text-white shadow-sm shadow-emerald-100 border border-emerald-600';
                            senderLabel = (m.time ? formatTime(m.time) : '') + ' • Humano';
                        } else {
                            style = 'bg-blue-600 text-white shadow-sm shadow-blue-100';
                            senderLabel = (m.time ? formatTime(m.time) : '') + ' • Bot';
                        }

                        return `
                        <div class="flex flex-col ${alignment} mb-4">
                            <div class="${style} p-4 rounded-2xl max-w-lg text-sm">
                                ${m.text}
                            </div>
                            <span class="text-[9px] text-slate-400 mt-1 mx-2">${senderLabel}</span>
                        </div>
                        `;
                    }).join('');

                    if (messages.length > lastCount) {
                        windowDiv.scrollTop = windowDiv.scrollHeight;
                        lastCount = messages.length;
                    }
                } catch(e) {}
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

  
