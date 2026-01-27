from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from .. import database as db
from handlers.common import DB_SEMAPHORE, safe_call, get_vip_sales_message
# Importa handlers específicos apenas quando necessário dentro da função ou via dispatcher
# (Para evitar erros, a lógica de 'watch_' e 'show_ep_' será roteada no main ou aqui)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    async with DB_SEMAPHORE:
        is_query = update.callback_query is not None
        user = update.callback_query.from_user if is_query else update.effective_user
        message_to_reply = update.callback_query.message if is_query else update.message

        # Se tiver payload (ex: /start watch_123), ignoramos o menu
        if context.args and not is_query:
            # A lógica de payloads complexos será tratada no main.py ou num router
            return 

        await db.get_or_create_user(user_id=user.id, first_name=user.first_name)
        
        # Verifica Configs de VIP para exibir ou não o botão
        config = await db.get_bot_config()
        price = config.get('vip_price', 4.99)
        is_free = price <= 0
        user_is_vip = await db.is_user_vip(user.id)

        keyboard = [[InlineKeyboardButton("Buscar Mídia 🔎", switch_inline_query_current_chat="")]]
        
        if not is_free and not user_is_vip:
            keyboard.append([InlineKeyboardButton("Adquirir VIP 🚀", callback_data="main_vip")])

        keyboard.append([InlineKeyboardButton("🔖 Minha Lista", callback_data="fav_menu")])
        keyboard.extend([[
            InlineKeyboardButton("Pedir Filme/Série 💡", callback_data="main_request"),
            InlineKeyboardButton("Top Mídia 🏆", callback_data="main_top")
        ]])

        main_menu = InlineKeyboardMarkup(keyboard)
        
        welcome_text = (
            f"Olá {user.mention_html()}! 👋\n\n"
            "🍿 **BEM-VINDO AO CINE PIPOCA!** 🍿\n\n"
            "Clique no botão \"Buscar Mídia 🔎\" para começar.\n"
            "Dúvidas? /help\n\n"
            "👇 **Escolha uma opção:**"
        )

        if is_query:
            try:
                # Tenta editar (se for foto, edita caption, se texto, edita texto)
                if message_to_reply.photo:
                     await message_to_reply.edit_caption(caption=welcome_text, reply_markup=main_menu, parse_mode='HTML')
                else:
                    await message_to_reply.edit_text(welcome_text, reply_markup=main_menu, parse_mode='HTML')
            except Exception:
                # Se falhar a edição (ex: mensagem antiga demais), envia nova
                await context.bot.send_message(chat_id=user.id, text=welcome_text, reply_markup=main_menu, parse_mode='HTML')
        else:
            await message_to_reply.reply_html(welcome_text, reply_markup=main_menu)

async def back_to_main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_call(query, "answer")
    await start(update, context)

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🔍 **Como usar:**\n"
        "Digite `@SeuBot nome do filme` em qualquer chat para buscar.\n\n"
        "🚀 **VIP:** Clique em 'Adquirir VIP' no menu para liberar tudo."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'state' in context.user_data:
        del context.user_data['state']
        await update.message.reply_text("🚫 Operação cancelada.")
    else:
        await update.message.reply_text("Nada para cancelar.")