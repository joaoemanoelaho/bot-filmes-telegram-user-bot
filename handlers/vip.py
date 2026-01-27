import time
import io
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database as db
import payments
from handlers.common import DB_SEMAPHORE, safe_call, get_vip_sales_message

async def vip_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra o texto de venda (Callback: main_vip)."""
    query = update.callback_query
    async with DB_SEMAPHORE:
        await safe_call(query, "answer")
        sales_text, _ = await get_vip_sales_message()
        
        keyboard = [
            [InlineKeyboardButton("✅ Sim, Gerar PIX!", callback_data="confirm_pay")],
            [InlineKeyboardButton("⬅️ Voltar", callback_data="back_to_main")]
        ]
        await safe_call(query, "edit_message_text", text=sales_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def confirm_pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gera o PIX (Callback: confirm_pay)."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # Cooldown
    now = time.time()
    last = context.user_data.get('last_pix', 0)
    if now - last < 60:
        await safe_call(query, "answer", text=f"Aguarde {int(60-(now-last))}s.", show_alert=True)
        return
    context.user_data['last_pix'] = now

    async with DB_SEMAPHORE:
        await safe_call(query, "answer")
        
        if await db.is_user_vip(user_id):
            await safe_call(query, "edit_message_text", text="✅ Você já é VIP!")
            return

        config = await db.get_bot_config()
        if config.get('vip_price', 0) <= 0:
            await db.set_user_as_vip(user_id, duration_days=999)
            await safe_call(query, "edit_message_text", text="🎉 Modo Gratuito Ativado! Aproveite.")
            return

        await safe_call(query, "edit_message_text", text="⏳ Gerando PIX...")
        
        # Cria pagamento
        price = config.get('vip_price', 4.99)
        payment = await payments.create_pix_payment(user_id, price)
        
        if payment and payment.get("qr_code_base64"):
            # Decodifica Imagem
            b64 = payment['qr_code_base64'].split(',')[-1]
            img = io.BytesIO(base64.b64decode(b64))
            
            txt = (
                f"💰 **Valor:** R$ {price:,.2f}\n"
                f"💠 **Copia e Cola:**\n`{payment['qr_code_text']}`\n\n"
                "⏳ Pague em até 5 minutos. A liberação é automática!"
            )
            
            await safe_call(query.message, "delete")
            msg = await context.bot.send_photo(chat_id=user_id, photo=img, caption=txt, parse_mode="Markdown")
            
            # Salva ID para deletar depois se precisar
            await db.set_user_active_payment_id(user_id, payment['payment_id'], msg.message_id)
        else:
            await safe_call(query, "edit_message_text", text="❌ Erro ao gerar PIX. Tente mais tarde.")
