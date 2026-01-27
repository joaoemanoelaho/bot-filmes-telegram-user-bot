from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database as db
from handlers.common import DB_SEMAPHORE, safe_call, get_vip_sales_message

async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Função sem alteração)
    async with DB_SEMAPHORE:
        user_id = update.effective_user.id

        await db.get_or_create_user(user_id=update.effective_user.id, first_name=update.effective_user.first_name)

        if not await db.is_user_vip(user_id):
            config = await db.get_bot_config()
            price = config.get('vip_price', 4.99)
            duration_days = config.get('vip_duration_days', 7)

            if price <= 0:
                # É GRÁTIS!
                await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
                try:
                    # Tenta enviar uma mensagem nova
                    await context.bot.send_message(chat_id=user_id, text="🎉 Bem-vindo! O acesso está gratuito no momento. Carregando...")
                except Exception:
                    pass # Se falhar, não importa, o código continua
                
                # NÃO damos 'return', o código continua e libera o acesso
            else:
                # É PAGO! (Esta é a lógica antiga)
                context.user_data['update'] = update
                sales_text, reply_markup = await _get_vip_sales_message(context)
                
                # A LINHA CORRETA PARA ESTE LUGAR
                await context.bot.send_message( 
                    chat_id=user_id,
                    text=f"Opa, {update.effective_user.first_name}! 👋\n\n{sales_text}",
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                return # <-- IMPORTANTE: Bloqueia o usuário

        context.user_data['state'] = 'awaiting_request'
        await update.message.reply_text(
            "Qual filme ou série você gostaria de ver no catálogo?\n\n"
            "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
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
