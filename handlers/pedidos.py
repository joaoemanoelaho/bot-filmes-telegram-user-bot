from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .. import database as db
from handlers.common import DB_SEMAPHORE, safe_call, get_vip_sales_message

async def request_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia o fluxo de pedido (Callback: main_request)."""
    query = update.callback_query
    user_id = query.from_user.id
    
    async with DB_SEMAPHORE:
        # Verifica VIP
        if not await db.is_user_vip(user_id):
            config = await db.get_bot_config()
            if config.get('vip_price', 0) > 0:
                sales_text, markup = await get_vip_sales_message()
                await safe_call(query, "edit_message_text", text=f"🔒 **Exclusivo VIP**\n\n{sales_text}", reply_markup=markup, parse_mode="Markdown")
                return

        await safe_call(query, "answer")
        context.user_data['state'] = 'awaiting_request'
        
        # --- AQUI ENTRARÁ SUA LÓGICA TMDB FUTURAMENTE ---
        await safe_call(query, "edit_message_text",
            text="💡 **Pedir Filme/Série**\n\n"
                 "Digite o nome do título que você quer:\n"
                 "_(Para cancelar, digite /cancelar)_"
        )

async def handle_request_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa o texto enviado pelo usuário quando state='awaiting_request'."""
    async with DB_SEMAPHORE:
        title = update.message.text
        user_id = update.effective_user.id
        
        if await db.add_request(user_id=user_id, title=title):
            del context.user_data['state']
            await update.message.reply_text(f"✅ Pedido recebido: **{title}**\nVocê será notificado se for adicionado!", parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Erro ao salvar pedido.")
