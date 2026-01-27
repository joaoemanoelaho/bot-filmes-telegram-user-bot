from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import Forbidden, RetryAfter
import asyncio
from .. import database as db
from config import ADMIN_IDS
from handlers.common import DB_SEMAPHORE, safe_call

# --- COMANDOS DE CONFIGURAÇÃO ---
async def set_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    try:
        price = 0.0 if context.args[0].lower() == 'gratis' else float(context.args[0])
        anchor = float(context.args[1])
        days = int(context.args[2])
        
        await db.set_bot_config_value('vip_price', price)
        await db.set_bot_config_value('vip_anchor_price', anchor)
        await db.set_bot_config_value('vip_duration_days', days)
        await update.message.reply_text("✅ Configuração salva!")
    except:
        await update.message.reply_text("Uso: /setconfig [preço/gratis] [ancora] [dias]")

# --- GESTÃO DE PEDIDOS ---
async def manage_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    
    pending = await db.get_pending_requests()
    if not pending:
        await update.message.reply_text("✅ Sem pedidos pendentes.")
        return
        
    await update.message.reply_text(f"📋 **{len(pending)} Pedidos Pendentes:**")
    for req in pending:
        kb = [[
            InlineKeyboardButton("✅ Aprovar", callback_data=f"adm_approve_{req['request_id']}"),
            InlineKeyboardButton("❌ Negar", callback_data=f"adm_deny_{req['request_id']}")
        ]]
        await update.message.reply_text(
            f"🆔 `{req['request_id']}` | 👤 `{req['user_id']}`\n🎬 {req['requested_title']}",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
        )

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trata cliques nos botões de adm (Aprovar/Negar)."""
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS: return
    
    parts = query.data.split('_') # adm_approve_ID
    action, req_id = parts[1], parts[2]
    
    req = await db.get_request_by_id(req_id)
    if not req:
        await safe_call(query, "edit_message_text", text="❌ Pedido não existe mais.")
        return

    # Notifica usuário (Simulação simples)
    status_msg = "Aprovado! ✅" if action == "approve" else "Negado. ❌"
    try:
        await context.bot.send_message(chat_id=req['user_id'], text=f"🔔 Seu pedido '{req['requested_title']}' foi: **{status_msg}**", parse_mode="Markdown")
    except: pass
    
    await db.delete_request(req_id)
    await safe_call(query, "edit_message_text", text=f"pedido {req['requested_title']}: {status_msg}")

# --- BROADCAST ---
async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    context.user_data['state'] = 'awaiting_broadcast'
    await update.message.reply_text("📣 Envie a mensagem para transmissão ou /cancelar.")

async def handle_broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    users = await db.get_active_users()
    await update.message.reply_text(f"🚀 Iniciando envio para {len(users)} usuários...")
    
    count = 0
    for u in users:
        try:
            await context.bot.send_message(u['user_id'], msg, parse_mode="Markdown")
            count += 1
            await asyncio.sleep(0.5) # Anti-flood
        except Exception: pass
    
    del context.user_data['state']
    await update.message.reply_text(f"✅ Transmissão finalizada! Enviado para {count} usuários.")