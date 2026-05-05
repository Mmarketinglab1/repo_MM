# LiveChatPro SaaS & CRM

Un sistema unificado de gestión de prospectos (Leads) y comunicaciones (LiveChat) multi-tenant, potenciado por Inteligencia Artificial, webhooks externos y funcionalidades en tiempo real para operadores humanos.

---

## 🏗️ Arquitectura del Sistema

El proyecto opera bajo una arquitectura monolítica ágil pero dividida conceptualmente en tres capas modernas:

1. **Backend y WebSockets (FastAPI):**
   - Utiliza **Python 3.x + FastAPI** como motor de altísimo rendimiento gracias a su núcleo asíncrono.
   - Sirve Endpoints HTTPS para la API (RESTful), así como webhooks externos `/webhook/n8n/` que actúan de puente.
   - Orquesta canales **WebSocket** (`/ws/{username}`) que aseguran un refresco instantáneo de mensajes, estados y asignaciones sin necesidad de F5 (long-polling/AJAX tradicional).

2. **Capa de Persistencia (PostgreSQL / Supabase + SQLAlchemy):**
   - Integrado directamente con **Supabase**, utilizando el puerto seguro del *connection pooler* (6543) vía conexión **TCP**.
   - **SQLAlchemy (ORM):** Abstrae y securiza todas las transacciones SQL (evitando Inyecciones SQL), manteniendo la integridad referencial para una estructura Multi-tenant robusta.

3. **Frontend (HTML/Vanilla JS + TailwindCSS):**
   - Toda la interfaz recae en un sistema de una sola página (`livechat.html`) o SPA.
   - Carece de compiladores pesados; utiliza Vanilla JavaScript asíncrono para gestionar las vistas y enrutamientos DOM.
   - Los estilos provienen de **Tailwind CSS** vía CDN, facilitando un diseño premium a medida.

---

## 📂 Archivos Centrales y Directorios

El núcleo del ecosistema está en el servidor local/nublado. Sus archivos más importantes son:

| Archivo | Responsabilidad / Naturaleza |
| :--- | :--- |
| `main.py` | Es el corazón de la aplicación FastAPI. Define seguridad (JWT), endpoints completos para entidades (Leads, Mensajes, Operadores, Súper-Admin), controla la máquina de websockets, y enruta los webhooks de n8n. |
| `models.py` | Configura las tablas en la Base de Datos utilizando el mapeo relacional de SQLAlchemy (Companies, Users, Operators, Messages). Contiene las llaves foráneas para sostener el esquema *Multi-tenant*. |
| `database.py` | Configura el motor (Engine) y el conector global a PostgreSQL. Extrae de `.env` configuraciones críticas de credenciales para levantar de forma segura la sesión (`SessionLocal`). |
| `templates/livechat.html` | Front-end y render visual oficial del proyecto. Contempla desde el Login hasta la Consola CRM, la interfaz de chats en tiempo real, modales pop-up de CRUD y lógicas de red (sockets). |
| Scripts de Migración | `migrate_*.py` y `run_migration.py` son puentes que alteraron la arquitectura nativa en producción (por ejemplo, para agregar soporte de asignación, actividad cronológica e inhibidor de bots). |

---

## 👥 Flujos de Sistema y Jerarquías de Rol

El proyecto fue diseñado nativamente como Software-as-a-Service (SaaS), permitiendo escalar horizontalmente diferentes inquilinos de empresa, bajo este esquema de jerárquias de acceso:

### 1. El Rol "Súper Admin"
Poseedor del sistema general. Inicia sesión para tener vista macro.
- Posee acceso a un panel exclusivo (**Dashboard SaaS Globa**l).
- Supervisa los "Tenants" (empresas clientes), cuenta volumetría (Leads totales, Mensajes totales de todos).
- Puede deshabilitar / suspender empresas a voluntad.

### 2. El Rol "Admin" (Dueño de un Tenant/Empresa)
Es quien contrata el SaaS.
- **Micro-gestión de Equipo:** En su configuración general puede crear/leer/actualizar/borrar (CRUD) credenciales para **Operadores**.
- **Reglas de Asignación:** Define si los nuevos chats llegarán a un listado global manual, o si el bot los asignará a los operadores mediante **Round Robin** automático.
- **Acceso Irrestricto:** Puede ver los mensajes de toda la consola CRM, intervenir cualquier ticket de cualquier operador y **Borrar leads** globalmente borrando chats en cascada de la DB.

### 3. El Rol "Operador"
Atención final al cliente de WhatsApp.
- Vista restringida en la interfaz: Únicamente ven en el panel a los leads y chats de prospectos que les han sido *asignados* o aquellos tomados manualmente.

---

## 🤖 Operativa del "Takeover Humano" vs Asistente IA (n8n Webhook)

La automatización no desplaza al servicio humano, ambos funcionan en harmonía:

1. El usuario WhatsApp interactúa con un número vinculado nativamente a n8n.
2. Cada mensaje entrante o respuesta generada por n8n (el Bot IA) percute el webhook `/webhook/n8n/{token}` o `/webhook/bot/{token}` del **LiveChatPro**. Los mensajes son renderizados y las burbujas actualizadas instantáneamente en azul.
3. El frontend ordena automáticamente a priori el lead que tuvo la "actividad más reciente" cronológicamente a lo largo de la columna.
4. **Interrupción Directa (Takeover):** Si un Operador humano desde el panel ve un conflicto y envía un mensaje mediante LiveChatPro, la aplicación **fuerza en DB que el bot se duerma** (`is_bot_active = False`). 
   *(Nota: N8n verifica este flag antes de escupir respuestas).*
5. Los mensajes del operador pintan un sombreado **Índigo Oscuro** para la clara diferencia visual.
6. Una vez la incidencia está solucionada, el operador desde la botonera superior pulsa en el ícono eléctrico verde, encendiendo nuevamente el `is_bot_active` y reviviendo la IA en n8n para futuros mensajes de ese prospecto particular.

---

## 🚀 Despliegue (En Google Cloud Run)

La plataforma utiliza ecosistemas contenerizados idealizados para GCP (Cloud Run), previniendo fricción tecnológica:

1. Validar entorno `.env`: 
   `DB_PORT`, `DB_HOST`, `DB_USER`, `DB_PASS`, `DB_NAME`, `SECRET_KEY`.
2. Compilar imagen o desplegar desde source:
   ```bash
   gcloud run deploy livechat-final --source . --region us-central1 --allow-unauthenticated
   ```
3. Todo requerimiento externo usa un puerto HTTPS 443 convencional. No se requieren puertos oscuros. Todo el tráfico, al ser Cloud Run, escala de a 0 instancias abaratando costos de mantenimiento en momentos muertos.
