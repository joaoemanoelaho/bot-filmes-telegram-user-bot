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
        "Envie a mensagem agora ou digite /cancelar.",
        parse_mode="Markdown"
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
                await db.set_user_inactive(user_id) 
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

    if update.message and update.message.chat.type != 'private':
        return
    
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

async def fake_pay_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Comando secreto para simular um pagamento e testar os pontos."""
    user_id = update.effective_user.id
    
    # Só você (Admin) pode usar esse comando
    if user_id not in ADMIN_IDS: 
        return

    try:
        target_user_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.message.reply_text("❌ Uso correto: `/fakepay ID_DO_USUARIO`\nExemplo: `/fakepay 123456789`", parse_mode="Markdown")
        return

    await update.message.reply_text(f"⏳ Simulando o pagamento PIX para o usuário {target_user_id}...")

    try:
        # 1. Dá o VIP para o usuário alvo (O amigo)
        config = await db.get_bot_config()
        duration = config.get('vip_duration_days', 30)
        await db.set_user_as_vip(target_user_id, duration_days=duration)
        await db.clear_user_active_payment_id(target_user_id)
        
        # 2. A MÁGICA DOS PONTOS! (Puxa quem indicou ele)
        user_info = await db.get_user_details(target_user_id)
        referrer_id = user_info.get('referred_by') if user_info else None
        
        if referrer_id:
            referrer_info = await db.get_user_details(referrer_id)
            if referrer_info:
                pontos_atuais = referrer_info.get('points', 0)
                novos_pontos = pontos_atuais + 1
                
                if novos_pontos >= 5:
                    # Bateu a meta
                    await db.set_user_as_vip(referrer_id, duration_days=30)
                    await asyncio.to_thread(db.supabase.table('users').update({'points': 0}).eq('user_id', referrer_id).execute)
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id, 
                            text="🎉 <b>VOCÊ BATEU 5 PONTOS!</b> 🏆\n\nUm amigo que você indicou acabou de assinar o VIP. Você ganhou <b>1 MÊS DE VIP TOTALMENTE GRÁTIS!</b> 🎁🚀", 
                            parse_mode="HTML"
                        )
                    except: pass
                else:
                    # Só soma 1 ponto
                    await asyncio.to_thread(db.supabase.table('users').update({'points': novos_pontos}).eq('user_id', referrer_id).execute)
                    try:
                        await context.bot.send_message(
                            chat_id=referrer_id, 
                            text=f"🪙 <b>VOCÊ GANHOU 1 PONTO!</b>\n\nUm amigo que você indicou (Simulação) assinou o VIP! Você agora tem <b>{novos_pontos}/5 pontos</b>. Junte 5 e ganhe 1 Mês Grátis! 🎁", 
                            parse_mode="HTML"
                        )
                    except: pass
            
            await update.message.reply_text(f"✅ Pagamento simulado com sucesso!\nO Padrinho (`{referrer_id}`) recebeu a notificação e os pontos.", parse_mode="Markdown")
        else:
            await update.message.reply_text("✅ Pagamento simulado!\n⚠️ Mas atenção: Esse usuário NÃO tinha padrinho cadastrado. Ninguém ganhou pontos.")

        # Avisa o "amigo" que o VIP dele caiu
        try:
            await context.bot.send_message(
                chat_id=target_user_id, 
                text="✅ **Pagamento Confirmado!** 🚀\n\nSeu acesso VIP foi liberado com sucesso. (Teste de Simulação) 🍿", 
                parse_mode="Markdown"
            )
        except: pass

    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao simular pagamento: {e}")
        
async def set_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    try:
        menu_text = update.message.text.split(' ', 1)[1]
        
        await db.set_bot_config_value('main_menu_text', menu_text)
        await update.message.reply_text(
            f"✅ Texto do menu principal salvo com sucesso!\n\n"
            f"💡 **Lembre-se das tags disponíveis:**\n"
            f"`{{USER_NAME}}` - Mostra o nome do usuário\n"
            f"`{{POINTS}}` - Mostra os pontos atuais (ex: 2)\n"
            f"`{{COMMUNITY_LINK}}` - Mostra o link do grupo"
        )
    except IndexError:
        await update.message.reply_text("❌ Erro: Envie o texto logo após o comando.\n\nExemplo:\n`/setmenu Olá {USER_NAME}! Você tem {POINTS} pontos.`", parse_mode="Markdown")