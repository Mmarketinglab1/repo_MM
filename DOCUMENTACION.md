# Documentación Oficial - LiveChat & CRM SaaS

Bienvenido a la documentación oficial del sistema **LiveChat & CRM Multi-Tenant**. Este documento está diseñado para guiar tanto al equipo técnico (desarrolladores, DevOps) como a los usuarios finales (Administradores y Operadores).

---

## 1. Visión General del Sistema (Overview)
El sistema es una plataforma **Software as a Service (SaaS)** que permite a múltiples empresas (Tenants) gestionar de manera centralizada sus conversaciones de WhatsApp, utilizando inteligencia artificial (vía flujos de n8n) y permitiendo intervención humana fluida.

**Flujo Principal:**
1. El cliente se comunica al WhatsApp de la empresa.
2. Un Bot de IA responde automáticamente (procesado por n8n).
3. Los mensajes se sincronizan en tiempo real en esta plataforma web.
4. El operador humano puede supervisar el chat y, si lo desea, pausar al Bot para tomar el control de la conversación.

---

## 2. Pila Tecnológica (Tech Stack)
- **Backend**: FastAPI (Python 3.10+).
- **Base de Datos**: PostgreSQL alojada en Supabase, procesada y vinculada usando SQLAlchemy ORM.
- **Frontend**: Single Page Application (SPA) construida en HTML5 Vanilla, JavaScript Vanilla, y estilizada con **TailwindCSS** (100% Mobile Responsive).
- **Notificaciones**: Servidor SMTP Privado alojado en servidores cPanel.
- **Infraestructura Cloud**: Google Cloud Run (Contenedores Docker serverless).

---

## 3. Modelo de Base de Datos y Arquitectura
- `Company`: Entidad raíz que representa a un cliente del SaaS (ej. Famiglia Viajes). Contiene datos de asignación de leads (`assignment_mode`).
- `Operator`: Usuarios de acceso al sistema, atados a una `Company`. Tienen dos roles: `admin` o `operador`.
- `User` (Leads): Los clientes reales que envían WhatsApps. Contienen campos de calificación como Tags, Email, Dirección, Observaciones y a qué operador fueron `assigned_to` (asignados).
- `Message`: Historial del chat, enlazado a un `User`.

### Lógica de Webhooks (n8n a FastAPI)
N8n gestiona el flujo de IA. Cuando n8n manda un mensaje o el cliente responde, envía eventos por POST a `/webhooks/n8n`.
- Si se detecta un lead nuevo, el sistema usa el **Algoritmo de Asignación** (Round-Robin, Operador Fijo, o Manual) y notifica al operador por correo (SMTP directo a su email).

---

## 4. Guía de Usuario (Manual de Operaciones)

### A. Súper Administrador
Es el dueño del sistema SaaS.
- Al acceder al Panel Principal, puede dar de alta (Registrar) nuevas empresas cliente.
- Puede visualizar cuántos leads y mensajes consumió cada empresa en tiempo real para temas de facturación o control.
- Puede **Inpersonar** (Ver como) cualquier empresa para configurar su entorno base sin necesidad de pedirles su clave de acceso.

### B. Funciones de Administrador (Tenant)
El administrador de la empresa se encarga de configurar las bases del sistema para su equipo.
- **Sección Operadores:** Puede crear credenciales para sus empleados, asignándoles rol de `admin` u `operador`.
- **Sección CRM Leads -> Rutas de Asignación:** En la vista CRM puede definir cómo entran los nuevos clientes de WhatsApp:
  - **Rotativo (Round Robin):** Los nuevos chats se reparten de a uno por vez equitativamente entre los operadores disponibles.
  - **Manual (Sin asignar):** Los chats caen sin dueño, listos para que los operadores los reclamen.
  - **Operadores Específicos:** Dirigir TODO el tráfico a una única persona (ej. un vendedor en particular).

### C. Funciones del Operador
Es el usuario del día a día del sistema.
1. **Vista Conversaciones (El Chat):**
   - En la columna izquierda (o pantalla completa en móviles) se encuentran los clientes asignados.
   - Dentro del chat, el botón de **"Bot"** permite apagar la inteligencia artificial. A partir de allí (cuando el botón se pone verde agua y dice "Bot Pausado"), lo que escriba el operador por la plataforma es lo único que responde al cliente de WhatsApp.
   - Se pueden agregar **Etiquetas (Tags)** coloreadas para clasificar la charla (ej. `PRESUPUESTO ENVIADO`, `URGENTE`).
2. **Vista CRM Leads:**
   - Tabla general para ver los datos de contacto: WhatsApp, Email, Estado actual y Dirección.
   - **Scroll Horizontal en Celulares**: Si el sistema se usa desde el celular, desliza la tabla hacia los lados.
   - Se pueden usar los buscadores en tiempo real (lupa) o filtrar unívocamente por estados (Vendido, Interesado, etc.).

---

## 5. Accesibilidad Móvil
La plataforma LIVECHAT es 100% responsiva (Mobile-First approach adaptado).
Si intentas acceder desde iOS Safari o Android Chrome:
- El Menú se convierte en una barra colapsable (botón hamburguesa).
- El sistema de Chats se divide correctamente en lista y ventana para aprovechar pantallas pequeñas (con botón Atrás).
- Las tablas gigantes ahora permiten desplazamiento, evitando roturas en el diseño.

## 6. Comandos de Despliegue Técnico (Deploy)
Si realizas un cambio en el código fuente desde el repositorio local instalado en la Mac, los comandos de despliegue son de una sola línea:

1. Guardar cambios (Commit):
```bash
git add .
git commit -m "Descripción de lo que modificaste"
git push origin main
```
2. Empujar a la nube Serverless (Google Cloud):
```bash
gcloud run deploy livechat-final --source . --region us-central1 --allow-unauthenticated
```
El nuevo código estará online (en la misma URL proporcionada por gcloud) en menos de 2 minutos.
