import time
import io
import base64
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database as db
import payments
from handlers.common import DB_SEMAPHORE, safe_call, _get_vip_sales_message

async def vip_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para o botão 'main_vip'."""
    query = update.callback_query
    
    # ETAPA 1: Mostrar o Texto de Venda (Marketing)
    async with DB_SEMAPHORE:
        await safe_call(query, "answer")
        
        # Pega o texto de venda formatado (o helper já faz isso)
        context.user_data['update'] = update
        sales_text, _ = await _get_vip_sales_message(context) # Ignoramos o markup antigo do helper

        # Criamos o NOVO markup com o botão de confirmar
        keyboard = [
            [InlineKeyboardButton("✅ Sim, Gerar PIX para Pagar!", callback_data="confirm_pay")],
            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            # Mostra o texto de venda com o botão "Gerar PIX"
            await safe_call(query, "edit_message_text",
                text=sales_text,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Erro ao mostrar sales_text em main_vip: {e}")

async def confirm_pay_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para o botão 'confirm_pay'."""
    query = update.callback_query
    user_id = query.from_user.id
    
    # ETAPA 2: Gerar o PIX
    now = time.time()
    last_request = context.user_data.get('last_pix_request', 0)
    cooldown = 60 # 60 segundos de cooldown

    if now - last_request < cooldown:
        await safe_call(query, "answer",
            text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s para gerar um novo PIX.",
            show_alert=True
        )
        return

    async with DB_SEMAPHORE:
        await safe_call(query, "answer")

        config = await db.get_bot_config()
        price = config.get('vip_price', 4.99)
        duration_days = config.get('vip_duration_days', 7)

        # Verificação de segurança
        if await db.is_user_vip(user_id):
            try:
                await safe_call(query, "edit_message_text", text="✨ Você já é um membro Premium! Aproveite todo o catálogo do Cine Pipoca.")
            except Exception: pass
            return
        
        if price <= 0:
            # O admin mudou para 'gratis' enquanto o usuário olhava o menu
            await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
            await safe_call(query, "edit_message_text", text="🎉 Boas notícias! O acesso agora é gratuito. Seu VIP foi ativado!")
            return
        
        # Lógica de verificação de pagamento pendente
        user_details = await db.get_user_details(user_id)
        active_payment_id = user_details.get('active_payment_id') if user_details else None

        if active_payment_id:
            await safe_call(query, "edit_message_text", text="⏳ Verificando pagamento pendente...")
            status = await payments.check_payment_status(active_payment_id)

            if status == "paid":
                await db.set_user_as_vip(user_id, duration_days=duration_days) 
                await db.clear_user_active_payment_id(user_id)
                await safe_call(query, "edit_message_text", text="🎉 Pagamento confirmado! Seu acesso Premium está ativo.")
                return
            
            elif status == "created":
                # TENTA RECUPERAR O PIX VÁLIDO!
                pix_data = await payments.get_pix_details(active_payment_id)
                
                if pix_data and pix_data.get("qr_code_text"):
                    # Conseguimos recuperar! Vamos mostrar o PIX de novo.
                    base64_string = pix_data['qr_code_base64']
                    if ',' in base64_string:
                        base64_string = base64_string.split(',')[1]
                    qr_image_data = base64.b64decode(base64_string)
                    qr_image_file = io.BytesIO(qr_image_data)
                    pix_code = pix_data['qr_code_text']
                    
                    caption = (
                        f"⚠️ **Você já tem um PIX gerado e ainda válido!**\n\n"
                        f"<b>1.</b> Escaneie o QR Code acima.\n"
                        f"<b>2.</b> Ou use o PIX Copia e Cola abaixo:\n"
                        f"<code>{pix_code}</code>\n\n"
                        "✅ Seu acesso Premium é <b>liberado automaticamente</b> na mesma hora."
                    )
                    
                    await safe_call(query.message, "delete")
                    msg_qrcode = await context.bot.send_photo(
                        chat_id=user_id, photo=qr_image_file, caption=caption, parse_mode="HTML"
                    )
                    
                    # Atualiza o ID da mensagem com o novo QR Code
                    await db.set_user_active_payment_id(user_id, active_payment_id, msg_qrcode.message_id)
                    return
                else:
                    # Se por algum motivo a SyncPay não devolver o código
                    await safe_call(query, "edit_message_text", text="⚠️ Você possui uma cobrança pendente. Por favor, aguarde alguns minutos até que ela expire para gerar uma nova.")
                    return

            elif status in ["expired", "canceled", "not_found"]:
                await db.clear_user_active_payment_id(user_id)
            
            else:
                await safe_call(query, "edit_message_text", text="😕 Erro ao verificar seu pagamento anterior. Tente novamente em 1 minuto.")
                return
        
        context.user_data['last_pix_request'] = time.time()
        
        # Puxa os preços do DB
        vip_price = config.get('vip_price', 4.99)
        vip_anchor_price = config.get('vip_anchor_price', 14.99)
        
        await safe_call(query, "edit_message_text", text="⏳ Gerando sua cobrança PIX, aguarde...")

        payment_data = await payments.create_pix_payment(user_id=user_id, amount=vip_price)

        if payment_data and payment_data.get("qr_code_base64"):
            payment_id = payment_data['payment_id']
            base64_string = payment_data['qr_code_base64']
            if ',' in base64_string:
                base64_string = base64_string.split(',')[1]
            qr_image_data = base64.b64decode(base64_string)
            qr_image_file = io.BytesIO(qr_image_data)
            pix_code = payment_data['qr_code_text']
            
            caption = (
                f"✨ <b>Seu PIX Promocional está pronto!</b>\n\n"
                f"Preço normal: <s>R$ {vip_anchor_price:,.2f}</s>\n"  
                f"Preço HOJE: <b>R$ {vip_price:,.2f}</b>\n\n"       
                f"<b>1.</b> Escaneie o QR Code acima.\n"
                f"<b>2.</b> Ou use o PIX Copia e Cola abaixo:\n"
                f"<code>{pix_code}</code>\n\n"              
                "✅ Seu acesso Premium é <b>liberado automaticamente</b> segundos após o pagamento.\n\n"
                "⚠️ <b>ATENÇÃO: Este código expira em 5 minutos!</b>\n"
                "Pague agora para travar o preço promocional."
            )

            await safe_call(query.message, "delete") # Deleta a mensagem de "venda"

            msg_qrcode = await context.bot.send_photo(
                chat_id=user_id, photo=qr_image_file, caption=caption,
                parse_mode="HTML", reply_markup=None
            )
            
            qr_message_id = msg_qrcode.message_id
            await db.set_user_active_payment_id(user_id, payment_id, qr_message_id) 
        else:
            await safe_call(query, "edit_message_text", text="😕 Desculpe, não foi possível gerar a cobrança PIX. Tente novamente mais tarde.")
