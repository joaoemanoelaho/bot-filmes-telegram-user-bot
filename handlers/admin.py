import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import Forbidden, RetryAfter
import database as db
from config import ADMIN_IDS
from handlers.common import DB_SEMAPHORE, safe_call
import handlers.pedidos as pedidos

# =================================================================
# === COMANDOS DE CONFIGURAÇÃO ===
# =================================================================

async def set_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    try:
        sales_text = update.message.text.split(' ', 1)[1]
        if '{PRICE}' not in sales_text or '{ANCHOR_PRICE}' not in sales_text:
            await update.message.reply_text("⚠️ Atenção: Faltam os placeholders {PRICE} e {ANCHOR_PRICE}.")
            
        await db.set_bot_config_value('vip_sales_text', sales_text)
        await update.message.reply_text(f"✅ Texto salvo!\nPreview:\n{sales_text.format(PRICE='R$ X', ANCHOR_PRICE='R$ Y')}")
    except IndexError:
        await update.message.reply_text("Erro: Envie o texto. Ex: /settext Promoção {PRICE}!")

async def set_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    try:
        price_str = context.args[0].lower()
        price = 0.0 if price_str == "gratis" else float(price_str)
        anchor_price = float(context.args[1])
        duration = int(context.args[2])
        
        await db.set_bot_config_value('vip_price', price)
        await db.set_bot_config_value('vip_anchor_price', anchor_price)
        await db.set_bot_config_value('vip_duration_days', duration)
        
        p_txt = "Gratuito" if price == 0 else f"R$ {price:,.2f}"
        await update.message.reply_text(f"✅ Configuração salva!\nPreço: {p_txt}\nÂncora: R$ {anchor_price}\nDias: {duration}")
    except:
        await update.message.reply_text("Erro. Uso: /setconfig [preço/gratis] [âncora] [dias]")

async def show_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return
    
    config = await db.get_bot_config()
    await update.message.reply_text(
        f"⚙️ **Configuração**\n"
        f"Preço: R$ {config.get('vip_price')}\n"
        f"Âncora: R$ {config.get('vip_anchor_price')}\n"
        f"Dias: {config.get('vip_duration_days')}\n\n"
        f"Texto:\n{config.get('vip_sales_text')}"
    )

# =================================================================
# === GESTÃO DE PEDIDOS (ADMIN) ===
# =================================================================

async def pedidos_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lista pedidos pendentes."""
    if update.effective_user.id not in ADMIN_IDS: return

    async with DB_SEMAPHORE:
        pending = await db.get_pending_requests()
        if not pending:
            await update.message.reply_text("✅ Zero Pendências!")
            return

        await update.message.reply_text(f"📋 **{len(pending)} Pedidos Pendentes:**")
        for req in pending:
            kb = [[
                InlineKeyboardButton("✅ Aprovar", callback_data=f"adm_approve_{req['request_id']}"),
                InlineKeyboardButton("❌ Negar", callback_data=f"adm_deny_{req['request_id']}")
            ]]
            await update.message.reply_text(
                f"🆔 `{req['request_id']}` | 👤 `{req['user_id']}`\n🎬 `{req['requested_title']}`",
                reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
            )

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa botões de Aprovar/Negar."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS:
        await safe_call(query, "answer", text="🚫 Acesso negado.", show_alert=True)
        return

    async with DB_SEMAPHORE:
        parts = query.data.split('_')
        action, req_id = parts[1], parts[2]
        
        # Recupera dados antes de deletar
        try:
            request_data = await db.get_request_by_id(req_id)
        except AttributeError:
            request_data = await db.update_request_status(req_id, "processing")

        if request_data:
            target_user = request_data.get('user_id')
            title = request_data.get('requested_title')
            
            # Notifica usuário
            if target_user:
                msg = f"🎉 Pedido '{title}' aprovado!" if action == "approve" else f"🔔 Pedido '{title}' negado."
                try: await context.bot.send_message(chat_id=target_user, text=msg)
                except: pass

            # Deleta do banco
            try: await db.delete_request(req_id)
            except: pass

            status = "✅ APROVADO" if action == "approve" else "❌ NEGADO"
            await safe_call(query, "edit_message_text", text=f"{query.message.text}\n\n🏁 {status}")
        else:
            await safe_call(query, "edit_message_text", text="❌ Pedido não encontrado.")

# =================================================================
# === BROADCAST (TRANSMISSÃO) ===
# =================================================================

async def broadcast_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inicia o processo de transmissão (Apenas Admins)."""
    if update.effective_user.id not in ADMIN_IDS: return
    
    context.user_data['state'] = 'awaiting_broadcast_message'
    await update.message.reply_text(
        "📣 **Modo de Transmissão Ativado!**\n\n"
        "Envie a mensagem que deseja transmitir para todos os usuários.\n"
        "💡 *Dica:* Pode ser texto, foto com legenda, vídeo ou até GIF!\n\n"
        "Envie a mensagem agora ou digite /cancelar."
    )

async def iniciar_broadcast_real(context: ContextTypes.DEFAULT_TYPE, message_id: int, from_chat_id: int):
    """
    Versão Avançada: Usa copy_message (suporta fotos/vídeos), limpa o banco e envia mais rápido.
    """
    bot = context.bot
    admin_id = ADMIN_IDS[0]
    
    active_users = await db.get_active_users()
    if not active_users:
        await bot.send_message(chat_id=admin_id, text="📣 Cancelado: Nenhum usuário ativo encontrado.")
        return

    await bot.send_message(chat_id=admin_id, text=f"🚀 Iniciando transmissão para {len(active_users)} usuários em background...")
    
    sucesso = 0
    removidos = 0
    falha_outros = 0

    for user in active_users:
        user_id = user['user_id']
        
        try:
            # COPY_MESSAGE é o segredo! Ele copia a mensagem exata (foto, texto, formatação) e envia.
            await bot.copy_message(
                chat_id=user_id, 
                from_chat_id=from_chat_id, 
                message_id=message_id
            )
            sucesso += 1
            
            # Delay otimizado para não tomar block do Telegram. 
            # 0.1s permite enviar cerca de 10 mensagens por segundo (muito rápido e seguro).
            await asyncio.sleep(0.1) 

        except Forbidden as e:
            # Usuário bloqueou o bot ou excluiu a conta
            erro = str(e).lower()
            if "bot was blocked" in erro or "user is deactivated" in erro:
                await db.delete_user(user_id) 
                removidos += 1
            else:
                falha_outros += 1

        except RetryAfter as e:
            # O Telegram pediu para ir mais devagar
            print(f"⏳ FloodWait (Limite do Telegram): Pausando por {e.retry_after}s...")
            await asyncio.sleep(e.retry_after + 1)

        except Exception as e:
            print(f"❌ Erro genérico ao enviar para {user_id}: {e}")
            falha_outros += 1
    
    # Relatório Final
    relatorio = (
        f"📣 **Transmissão Finalizada!**\n\n"
        f"✅ Entregues com sucesso: {sucesso}\n"
        f"🗑️ Usuários Removidos (Bloquearam o bot): {removidos}\n"
        f"❌ Falhas desconhecidas: {falha_outros}\n\n"
        f"📊 Base atualizada: {len(active_users) - removidos} usuários ativos."
    )
    await bot.send_message(chat_id=admin_id, text=relatorio)

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura qualquer tipo de mensagem para Broadcast e Pedidos."""
    async with DB_SEMAPHORE:
        state = context.user_data.get('state')
        
        # ROTA DE BROADCAST (ADMIN)
        if state == 'awaiting_broadcast_message':
            if update.effective_user.id not in ADMIN_IDS: return
            del context.user_data['state']
            
            # Pegamos o ID da mensagem que o admin acabou de enviar e o ID do chat
            message_id = update.message.message_id
            from_chat_id = update.message.chat_id
            
            await update.message.reply_text("⚙️ Mensagem capturada! Preparando os motores...")
            
            # Chama a função passando a mensagem capturada (para copiar)
            asyncio.create_task(iniciar_broadcast_real(context, message_id, from_chat_id))

        # ROTA DE PEDIDOS TMDB
        elif state == 'awaiting_tmdb_id':
            await pedidos.process_tmdb_message(update, context)
