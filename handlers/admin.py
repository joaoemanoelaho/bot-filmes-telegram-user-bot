import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import Forbidden, RetryAfter
import database as db
from config import ADMIN_IDS, BROADCAST_SOURCE_CHAT_ID
from handlers.common import DB_SEMAPHORE, safe_call
import handlers.pedidos as pedidos
from datetime import datetime

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
# === BROADCAST (TRANSMISSÃO SEGMENTADA) ===
# =================================================================

async def broadcast_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inicia o processo de transmissão segmentada (Apenas Admins)."""
    if update.effective_user.id not in ADMIN_IDS: return
    
    keyboard = [
        [InlineKeyboardButton("📢 Todos os Usuários", callback_data="bc_all")],
        [InlineKeyboardButton("💎 Apenas VIPs", callback_data="bc_vip")],
        [InlineKeyboardButton("🆓 Apenas Gratuitos (Leads)", callback_data="bc_free")],
        [InlineKeyboardButton("🧪 Testar (Apenas para Mim)", callback_data="bc_test")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="bc_cancel")]
    ]
    
    await update.message.reply_text(
        "🎯 **Painel de Transmissão Inteligente**\n\n"
        "Escolha qual público deve receber a sua mensagem:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa a escolha do público no painel de transmissão."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS: return
    if not query.data.startswith("bc_"): return
        
    await safe_call(query, "answer")
    
    if query.data == "bc_cancel":
        await safe_call(query, "edit_message_text", text="❌ Transmissão cancelada.")
        return
        
    alvo = query.data.split('_')[1] # all, vip ou free
    
    context.user_data['broadcast_target'] = alvo
    context.user_data['state'] = 'awaiting_broadcast_message'
    
    nomes = {"all": "Todos os Usuários", "vip": "Apenas VIPs", "free": "Apenas Gratuitos", "test": "Teste (Apenas Admin)"}
    
    await safe_call(query, "edit_message_text", text=(
        f"🎯 Público selecionado: **{nomes[alvo]}**\n\n"
        f"⏳ Agora, **envie a mensagem** que você quer transmitir.\n"
        f"💡 *Dica: Pode ser texto, foto com legenda, vídeo ou até encaminhar uma mensagem pronta!*"
    ), parse_mode="Markdown")

async def iniciar_broadcast_real(context: ContextTypes.DEFAULT_TYPE, message_id: int, from_chat_id: int, alvo: str, botoes=None):
    """
    Usa copy_message, limpa o banco de quem bloqueou o bot e envia respeitando o Anti-Ban.
    """
    bot = context.bot
    admin_id = ADMIN_IDS[0]
    
    if alvo == "test":
        active_users_ids = [admin_id] # Coloca apenas VOCÊ na lista de disparo
    else:
        # Só vai no banco se não for teste
        active_users_ids = await db.obter_usuarios_broadcast(alvo)
    
    if not active_users_ids:
        await bot.send_message(chat_id=admin_id, text=f"📣 Cancelado: Nenhum usuário encontrado para o filtro '{alvo}'.")
        return

    await bot.send_message(chat_id=admin_id, text=f"🚀 Iniciando transmissão para {len(active_users_ids)} usuários em background...")
    
    sucesso = 0
    removidos = 0
    falha_outros = 0

    for user_id in active_users_ids:
        try:
            await bot.copy_message(
                chat_id=user_id, 
                from_chat_id=from_chat_id, 
                message_id=message_id,
                reply_markup=botoes
            )
            sucesso += 1
            await asyncio.sleep(0.05) # Pausa de segurança anti-ban

        except Forbidden as e:
            erro = str(e).lower()
            if "bot was blocked" in erro or "user is deactivated" in erro:
                await db.set_user_inactive(user_id) 
                removidos += 1
            else:
                falha_outros += 1

        except RetryAfter as e:
            print(f"⏳ FloodWait (Limite do Telegram): Pausando por {e.retry_after}s...")
            await asyncio.sleep(e.retry_after + 1)

        except Exception as e:
            print(f"❌ Erro genérico ao enviar para {user_id}: {e}")
            falha_outros += 1
    
    # Relatório Final
    relatorio = (
        f"📣 **Transmissão Finalizada!**\n\n"
        f"🎯 Alvo: {alvo.upper()}\n"
        f"✅ Entregues com sucesso: {sucesso}\n"
        f"🗑️ Usuários Removidos (Bloquearam o bot): {removidos}\n"
        f"❌ Falhas desconhecidas: {falha_outros}\n"
    )
    await bot.send_message(
        chat_id=admin_id,
        text=relatorio,
        parse_mode="Markdown"
    )

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captura mensagens privadas para Broadcast e Pedidos."""

    msg = update.effective_message
    user = update.effective_user

    if not msg or not user:
        return

    if msg.chat.type != "private":
        return

    if context.user_data is None:
        return

    async with DB_SEMAPHORE:
        state = context.user_data.get("state")
        
        # ROTA DE BROADCAST (ADMIN)
        if state == 'awaiting_broadcast_message':
            if update.effective_user.id not in ADMIN_IDS: return
            
            alvo = context.user_data.get('broadcast_target', 'all')
            del context.user_data['state']
            
            message_id = update.message.message_id
            from_chat_id = update.message.chat_id
            botoes = update.message.reply_markup
            
            await update.message.reply_text("⚙️ Mensagem capturada! Preparando os motores...")
            
            # Passa o 'alvo' para a função saber para quem enviar
            context.application.create_task(
                iniciar_broadcast_real(
                    context=context,
                    message_id=message_id,
                    from_chat_id=from_chat_id,
                    alvo=alvo,
                    botoes=botoes
                )
            )

        # ROTA DE PEDIDOS TMDB
        elif state == 'awaiting_tmdb_id':
            await pedidos.process_tmdb_message(update, context)
        
        # ROTA DE GERENCIAR VIP (ADMIN)
        elif state == 'awaiting_admin_vip_manage':
            if update.effective_user.id not in ADMIN_IDS: return
            
            try:
                partes = update.message.text.strip().split()
                alvo_id = int(partes[0])
                dias = int(partes[1])
                
                # 🚀 A MÁGICA: Importamos a sua função perfeita que já atualiza o Banco e o Cache!
                from handlers.start import adicionar_horas_vip
                
                if dias > 0:
                    # DAR VIP: Convertendo dias para horas e usando a sua estrutura oficial
                    await adicionar_horas_vip(alvo_id, dias * 24)
                    
                    await update.message.reply_text(f"✅ Sucesso Absoluto! O usuário `{alvo_id}` ganhou {dias} dias de VIP. Cache 100% sincronizado.", parse_mode="Markdown")
                    try:
                        await context.bot.send_message(chat_id=alvo_id, text=f"🎉 **PRESENTE DO ADMIN!**\nSua conta acaba de receber +{dias} dias de acesso VIP Premium! 🍿", parse_mode="Markdown")
                    except: pass
                    
                else:
                    # ❌ TIRAR VIP (Única parte manual, pois sua função só adiciona)
                    from datetime import datetime
                    agora_iso = datetime.utcnow().isoformat()
                    
                    # Salva no banco o vencimento para a hora atual (mata o VIP)
                    await asyncio.to_thread(
                        db.supabase.table('users').update({
                            'is_vip': False, 
                            'vip_until': agora_iso
                        }).eq('user_id', alvo_id).execute
                    )
                    
                    # Arranca da memória RAM na força
                    db.VIP_CACHE.pop(alvo_id, None)  
                    
                    await update.message.reply_text(f"❌ Sucesso! O VIP do usuário `{alvo_id}` foi cancelado e o cache foi detonado.", parse_mode="Markdown")
                    
            except Exception as e:
                print(f"Erro no painel VIP: {e}")
                await update.message.reply_text("⚠️ Formato inválido ou erro no banco! Envie: `ID DIAS` (Ex: `123456789 30`)", parse_mode="Markdown")
            
            del context.user_data['state']

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

async def broadcast_source_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Captura mensagens postadas no canal privado de transmissão."""
    
    msg = update.channel_post or update.message
    if not msg:
        return

    if msg.chat_id != BROADCAST_SOURCE_CHAT_ID:
        return

    context.bot_data["last_broadcast_source"] = {
        "from_chat_id": msg.chat_id,
        "message_id": msg.message_id,
        "media_group_id": msg.media_group_id,
        "reply_markup": msg.reply_markup
    }

    keyboard = [
        [InlineKeyboardButton("🧪 Testar comigo", callback_data="bcsrc_test")],
        [InlineKeyboardButton("📢 Enviar para Todos", callback_data="bcsrc_all")],
        [InlineKeyboardButton("💎 Enviar para VIPs", callback_data="bcsrc_vip")],
        [InlineKeyboardButton("🆓 Enviar para Gratuitos", callback_data="bcsrc_free")],
        [InlineKeyboardButton("❌ Cancelar", callback_data="bcsrc_cancel")]
    ]

    admin_id = ADMIN_IDS[0]

    await context.bot.send_message(
        chat_id=admin_id,
        text=(
            "📥 **Mensagem capturada do canal de transmissão.**\n\n"
            f"ID da mensagem: `{msg.message_id}`\n\n"
            "Confira o preview abaixo antes de disparar."
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    await context.bot.copy_message(
        chat_id=admin_id,
        from_chat_id=msg.chat_id,
        message_id=msg.message_id,
        reply_markup=msg.reply_markup
    )

async def broadcast_source_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id not in ADMIN_IDS:
        return

    if not query.data.startswith("bcsrc_"):
        return

    await query.answer()

    alvo = query.data.replace("bcsrc_", "")

    if alvo == "cancel":
        await query.edit_message_text("❌ Transmissão cancelada.")
        return

    source = context.bot_data.get("last_broadcast_source")

    if not source:
        await query.edit_message_text("❌ Nenhuma mensagem capturada do canal ainda.")
        return

    await query.edit_message_text(
        f"🚀 Disparo iniciado para: **{alvo.upper()}**",
        parse_mode="Markdown"
    )

    context.application.create_task(
        iniciar_broadcast_real(
            context=context,
            message_id=source["message_id"],
            from_chat_id=source["from_chat_id"],
            alvo=alvo,
            botoes=source.get("reply_markup")
        )
    )
        
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

# =================================================================
# === PAINEL CENTRAL DO ADMIN ===
# =================================================================

async def painel_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS: return

    msg = await update.message.reply_text("⏳ Puxando os relatórios do banco de dados...")
    await _renderizar_painel(user_id, msg)

async def _renderizar_painel(user_id, msg_to_edit):
    """Lê as métricas em tempo real e desenha o painel (Usado no comando e no botão voltar)"""
    async with DB_SEMAPHORE:
        # 1. Total de Usuários (Leads)
        try:
            res_users = await asyncio.to_thread(db.supabase.table('users').select('user_id', count='exact').execute())
            total_users = res_users.count if res_users else 0
        except: total_users = 0

        # 2. VIPs Ativos Hoje
        try:
            agora_iso = datetime.now().isoformat()
            res_vips = await asyncio.to_thread(db.supabase.table('users').select('user_id', count='exact').eq('is_vip', True).gte('vip_until', agora_iso).execute())
            total_vips = res_vips.count if res_vips else 0
        except: total_vips = 0

        # 3. Pedidos Pendentes
        try:
            pending = await db.get_pending_requests()
            total_pendentes = len(pending) if pending else 0
        except: total_pendentes = 0

        # Calcula a taxa de conversão
        conversao = (total_vips / total_users * 100) if total_users > 0 else 0.0

    texto_painel = (
        "🎛️ **PAINEL DE COMANDO - CINE PIPOCA** 🎛️\n\n"
        f"👥 **Total de Leads:** `{total_users}` usuários\n"
        f"💎 **VIPs Ativos:** `{total_vips}` assinantes\n"
        f"📈 **Conversão VIP:** `{conversao:.1f}%`\n"
        f"⏳ **Pedidos na Fila:** `{total_pendentes}` pendentes\n\n"
        "O que você deseja fazer agora?"
    )

    keyboard = [
        [InlineKeyboardButton("📢 Fazer Broadcast", callback_data="painel_broadcast")],
        [InlineKeyboardButton("📋 Fila de Pedidos", callback_data="painel_pedidos")],
        [InlineKeyboardButton("💎 Dar/Tirar VIP Manual", callback_data="painel_vip")],
        [InlineKeyboardButton("❌ Fechar Painel", callback_data="painel_fechar")]
    ]

    await safe_call(msg_to_edit, "edit_text", text=texto_painel, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def painel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa todos os cliques dentro do painel."""
    query = update.callback_query
    user_id = query.from_user.id
    
    if user_id not in ADMIN_IDS: return
    if not query.data.startswith("painel_"): return

    await safe_call(query, "answer")
    acao = query.data.replace("painel_", "")

    if acao == "fechar":
        await safe_call(query, "delete_message")
        
    elif acao == "voltar":
        await safe_call(query, "edit_message_text", text="⏳ Atualizando métricas...")
        await _renderizar_painel(user_id, query.message)

    elif acao == "broadcast":
        # Reaproveita a mesma interface que você já tinha no broadcast
        keyboard = [
            [InlineKeyboardButton("📢 Todos os Usuários", callback_data="bc_all")],
            [InlineKeyboardButton("💎 Apenas VIPs", callback_data="bc_vip")],
            [InlineKeyboardButton("🆓 Apenas Gratuitos (Leads)", callback_data="bc_free")],
            [InlineKeyboardButton("🧪 Testar (Apenas para Mim)", callback_data="bc_test")],
            [InlineKeyboardButton("⬅️ Voltar ao Painel", callback_data="painel_voltar")]
        ]
        await safe_call(query, "edit_message_text", text="🎯 **Painel de Transmissão Inteligente**\n\nEscolha qual público deve receber a sua mensagem:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif acao == "pedidos":
        # Exclui o menu e envia a fila de pedidos diretamente
        await safe_call(query, "delete_message")
        async with DB_SEMAPHORE:
            pending = await db.get_pending_requests()
            if not pending:
                await context.bot.send_message(chat_id=user_id, text="✅ Zero Pendências! O catálogo está em dia.")
                return

            await context.bot.send_message(chat_id=user_id, text=f"📋 **{len(pending)} Pedidos Pendentes:**", parse_mode="Markdown")
            for req in pending:
                kb = [[
                    InlineKeyboardButton("✅ Aprovar", callback_data=f"adm_approve_{req['request_id']}"),
                    InlineKeyboardButton("❌ Negar", callback_data=f"adm_deny_{req['request_id']}")
                ]]
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"🆔 `{req['request_id']}` | 👤 `{req['user_id']}`\n🎬 `{req['requested_title']}`",
                    reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
                )

    elif acao == "vip":
        context.user_data['state'] = 'awaiting_admin_vip_manage'
        texto = (
            "💎 **GERENCIADOR DE VIP MANUAL** 💎\n\n"
            "Para **DAR** VIP, digite o ID e os dias.\n"
            "👉 `123456789 30` (Dá 30 dias)\n\n"
            "Para **TIRAR** VIP, digite o ID e o número 0.\n"
            "👉 `123456789 0` (Remove o VIP e limpa o cache)\n\n"
            "Envie a mensagem agora ou clique em voltar."
        )
        keyboard = [[InlineKeyboardButton("⬅️ Voltar ao Painel", callback_data="painel_voltar")]]
        await safe_call(query, "edit_message_text", text=texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))