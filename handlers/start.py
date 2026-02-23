from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
import asyncio
from datetime import datetime, timedelta
import database as db
from config import STORAGE_CHANNEL_ID, STORAGE_CHANNEL_ID_SERIES, FSUB_GROUP_LINK, FSUB_CHANNEL_LINK, FSUB_CHANNEL_ID, FSUB_GROUP_ID
from handlers.common import (
    DB_SEMAPHORE, safe_call, _get_vip_sales_message, 
    _get_episode_details_message, delete_message_job
)

async def adicionar_horas_vip(user_id: int, horas: int):
    """Soma horas ao tempo VIP existente, ou cria um novo se não tiver."""
    is_vip = await db.is_user_vip(user_id)
    agora = datetime.utcnow()
    
    if is_vip:
        # Se já é VIP, puxa a data atual e soma
        try:
            response = await asyncio.to_thread(
                db.supabase.table('users').select('vip_until').eq('user_id', user_id).single().execute
            )
            if response.data and response.data.get('vip_until'):
                data_atual = datetime.fromisoformat(response.data['vip_until'].replace('Z', '+00:00')).replace(tzinfo=None)
                if data_atual > agora:
                    nova_data = data_atual + timedelta(hours=horas)
                else:
                    nova_data = agora + timedelta(hours=horas)
            else:
                nova_data = agora + timedelta(hours=horas)
        except:
            nova_data = agora + timedelta(hours=horas)
    else:
        # Se não é VIP, soma a partir de agora
        nova_data = agora + timedelta(hours=horas)
    
    # Atualiza o banco
    await asyncio.to_thread(
        db.supabase.table('users').update({
            'is_vip': True,
            'vip_until': nova_data.isoformat()
        }).eq('user_id', user_id).execute
    )

async def verificar_inscricao(bot, user_id):
    status_aceitos = ['member', 'administrator', 'creator']
    # O Bot checa usando IDs, não Links!
    chats_para_verificar = [FSUB_CHANNEL_ID, FSUB_GROUP_ID] 
    
    for chat_id in chats_para_verificar:
        try:
            membro = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if membro.status not in status_aceitos:
                return False
        except BadRequest:
            pass 
        except Exception:
            pass
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # --- CORREÇÃO AQUI: Importamos apenas o watch_command_handler ---
    # Removemos 'button_handler', pois ele mudou de nome e não precisamos dele aqui
    from handlers.player import watch_command_handler

    async with DB_SEMAPHORE:
        is_query = update.callback_query is not None
        if is_query:
            user = update.callback_query.from_user
            message_to_reply = update.callback_query.message
        else:
            user = update.effective_user
            message_to_reply = update.message

        try:
            user_info = await db.get_or_create_user(user_id=user.id, first_name=user.first_name)
            if isinstance(user_info, tuple):
                _, is_new_user = user_info
            else:
                is_new_user = False
        except Exception:
            is_new_user = False

        # === TRATAMENTO DE DEEP LINKS (start=...) ===
        if context.args:
            payload = context.args[0]

            if not is_query:
                try:
                    await message_to_reply.delete()
                except Exception:
                    pass

            # =========================================================
            # ROTA 5: SISTEMA DE INDICAÇÃO (NOVO)
            # =========================================================
            if payload.startswith("ref_"):
                try:
                    referrer_id = int(payload.split('_')[1])
                    
                    if is_new_user and referrer_id != user.id:
                        # 1. Salva quem indicou
                        await asyncio.to_thread(
                            db.supabase.table('users').update({'referred_by': referrer_id}).eq('user_id', user.id).execute
                        )
                        
                        # 2. Dá 4 horas pro padrinho
                        await adicionar_horas_vip(referrer_id, 4)
                        
                        # Avisa o padrinho
                        try:
                            await context.bot.send_message(
                                chat_id=referrer_id, 
                                text=f"🎉 **Indicação de Sucesso!**\n\nO usuário {user.first_name} entrou pelo seu link! Você acaba de ganhar **+4 HORAS** de VIP grátis! 🍿",
                                parse_mode="Markdown"
                            )
                        except: pass
                        
                        # 3. Dá as 8 horas (4 trial + 4 bonus) pro novato!
                        await adicionar_horas_vip(user.id, 8)
                        
                        trial_text = (
                            f"🎉 <b>BEM-VINDO, {user.first_name}!</b>\n\n"
                            f"🎁 Como você foi convidado por um amigo, você acaba de ganhar <b>8 HORAS DE VIP GRÁTIS!</b> (O dobro do normal!)\n\n"
                            "✅ Filmes e Séries sem limites.\n"
                            "✅ Alta velocidade.\n\n"
                            "⏳ <i>Seu tempo já está contando... Corra para maratonar!</i>\n\n"
                            "👇 <b>Clique abaixo para buscar seu filme:</b>"
                        )
                        await context.bot.send_message(
                            chat_id=user.id, text=trial_text, parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔎 Buscar Filme Agora", switch_inline_query_current_chat="")]])
                        )
                        return # Encerra aqui pra não mandar o menu padrão junto
                    
                    elif not is_new_user:
                        await context.bot.send_message(chat_id=user.id, text="ℹ️ Você já tem uma conta conosco! O link de convite é apenas para novos usuários.")
                        # Continua pro menu normal abaixo...
                        
                except Exception as e:
                    print(f"Erro no sistema de indicação: {e}")

            # ROTA 1: Assistir Episódio (watch_ep_)
            if payload.startswith("watch_ep_"):
                esta_inscrito = await verificar_inscricao(context.bot, user.id)

                if not esta_inscrito:
                    # Link genérico para recarregar o start com o mesmo payload
                    link_recarregar = f"https://t.me/{context.bot.username}?start={payload}"
                    
                    keyboard_fsub = [
                        [InlineKeyboardButton("📢 Entrar no Canal", url=FSUB_CHANNEL_LINK)],
                        # ⚠️ TROQUE ESTE LINK PELO LINK DE CONVITE DO SEU GRUPO
                        [InlineKeyboardButton("💬 Entrar no Grupo", url=FSUB_GROUP_LINK)], 
                        [InlineKeyboardButton("🔄 Já entrei! Tentar Novamente", url=link_recarregar)]
                    ]
                    
                    await context.bot.send_message(
                        chat_id=user.id,
                        text="🚫 <b>Acesso Restrito!</b>\n\nPara assistir a este episódio, você precisa entrar nos nossos canais oficiais.",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(keyboard_fsub)
                    )
                    return
                try:
                    parts = payload.split('_')
                    episode_id = int(parts[2])
                    audio_type = parts[3]
                    msg_to_delete_id = None
                    if len(parts) > 5 and parts[4] == 'del':
                        msg_to_delete_id = int(parts[5])

                    if msg_to_delete_id:
                        try:
                            await context.bot.delete_message(chat_id=user.id, message_id=msg_to_delete_id)
                        except Exception:
                            pass

                    await db.get_or_create_user(user_id=user.id, first_name=user.first_name)

                    if not await db.is_user_vip(user.id):
                        config = await db.get_bot_config()
                        price = config.get('vip_price', 4.99)
                        duration_days = config.get('vip_duration_days', 7)

                        if price <= 0:
                            # É GRÁTIS!
                            await db.set_user_as_vip(user.id, duration_days=duration_days if duration_days > 0 else 9999)
                            try:
                                await context.bot.send_message(chat_id=user.id, text="🎉 Bem-vindo! O acesso está gratuito no momento. Carregando...")
                            except Exception: pass
                        else:
                            # É PAGO!
                            context.user_data['update'] = update
                            sales_text, reply_markup = await _get_vip_sales_message(context)
                            await context.bot.send_message( 
                                chat_id=user.id,
                                text=f"Opa, {user.first_name}! 👋\n\n{sales_text}",
                                parse_mode="HTML",
                                reply_markup=reply_markup
                            )
                            return

                    status_msg = await context.bot.send_message(chat_id=user.id, text="⏳ Carregando seu episódio...")

                    full_details = await db.get_full_episode_details(episode_id)
                    if not full_details:
                        await status_msg.edit_text("Erro ao carregar dados do episódio.")
                        return

                    episode_data = full_details
                    season_data = full_details.get('seasons')
                    series_data = season_data.get('series') if season_data else None

                    if not season_data or not series_data:
                        await status_msg.edit_text("Erro ao carregar dados da série.")
                        return

                    file_id_to_send = None
                    audio_text = "N/A"
                    msg_id_to_copy = None
                    
                    if audio_type == 'dub' and episode_data.get('dubbed_file_id'):
                        file_id_to_send = episode_data['dubbed_file_id']
                        msg_id_to_copy = episode_data.get('dubbed_msg_id')
                        audio_text = "(Dublado)"
                    elif audio_type == 'sub' and episode_data.get('subtitled_file_id'):
                        file_id_to_send = episode_data['subtitled_file_id']
                        msg_id_to_copy = episode_data.get('subtitled_msg_id')
                        audio_text = "(Legendado)"

                    if file_id_to_send:
                        bot_username = context.bot.username
                        series_title = series_data.get('title', 'Série')
                        season_number = season_data.get('season_number', 0)
                        series_id_for_related = series_data.get('id', 0)

                        video_caption = (
                            f"📺 <b>{series_title}</b>\n"
                            f"S{season_number:02d}E{episode_data.get('episode_number', 0):02d}: <b>{episode_data.get('title', 'Episódio')}</b> {audio_text}\n\n"
                            f"---\n"
                            f"🍿 Assistido com @{bot_username}\n"
                            f"⚠️ <b>Este vídeo será apagado em 4 horas.</b>"
                        )

                        # Lógica de navegação otimizada
                        season_id = season_data.get('id')
                        current_ep_num = episode_data.get('episode_number', 0)

                        prev_ep, next_ep = await asyncio.gather(
                            db.get_neighbor_episode(season_id, current_ep_num, 'previous'),
                            db.get_neighbor_episode(season_id, current_ep_num, 'next')
                        )

                        # Lógica de pular temporada
                        if not next_ep:
                            all_seasons = await db.get_seasons_for_series(series_data['id'])
                            if all_seasons:
                                current_season_num = season_data['season_number']
                                next_season_obj = next((s for s in all_seasons if s['season_number'] == current_season_num + 1), None)
                                if next_season_obj:
                                    eps_next_season, _ = await db.get_episodes_for_season(next_season_obj['id'], limit=1, offset=0)
                                    if eps_next_season:
                                        next_ep = eps_next_season[0]

                        if not prev_ep and season_data['season_number'] > 1:
                            all_seasons = await db.get_seasons_for_series(series_data['id'])
                            if all_seasons:
                                prev_season_obj = next((s for s in all_seasons if s['season_number'] == season_data['season_number'] - 1), None)
                                if prev_season_obj:
                                    eps_prev, _ = await db.get_episodes_for_season(prev_season_obj['id'], limit=100, offset=0)
                                    if eps_prev:
                                        prev_ep = eps_prev[-1]

                        nav_row = []
                        if prev_ep:
                            nav_row.append(InlineKeyboardButton("⏪ Ep. Anterior", callback_data=f"ep_nav_{prev_ep['id']}"))
                        if next_ep:
                            nav_row.append(InlineKeyboardButton("Próximo Ep. ⏩", callback_data=f"ep_nav_{next_ep['id']}"))

                        fav_unique_code = f"ep_{episode_id}_{audio_type}"
                        is_fav = await db.is_favorite(user.id, fav_unique_code)
                        fav_btn_text = "❌ Remover da Lista" if is_fav else "🔖 Salvar na Lista"

                        keyboard = []
                        if nav_row: keyboard.append(nav_row)
                        
                        keyboard.append([
                            InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{series_id_for_related}_series"),
                            InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"ep_card:{episode_id}")
                        ])
                        keyboard.append([InlineKeyboardButton(fav_btn_text, callback_data=f"fav_toggle_{fav_unique_code}")])
                        
                        video_reply_markup = InlineKeyboardMarkup(keyboard)

                        try:
                            # Plano A: Enviar
                            sent_message = await context.bot.send_video(
                                chat_id=user.id,
                                video=file_id_to_send,
                                caption=video_caption,
                                parse_mode="HTML",
                                reply_markup=video_reply_markup,
                                protect_content=True
                            )
                            # Agenda Deleção
                            job_data = {'chat_id': sent_message.chat_id, 'message_id': sent_message.message_id}
                            context.job_queue.run_once(
                                delete_message_job, 
                                when=14400, 
                                data=job_data,
                                name=f"del_{sent_message.chat_id}_{sent_message.message_id}"
                            )
                            await db.log_movie_view(user_id=user.id, series_id=series_id_for_related)
                        except BadRequest as e:
                            # Plano B: Copiar (se falhar o file_id)
                            # Aqui assumimos que você tem as constantes STORAGE_CHANNEL_ID_SERIES no config.py
                            from config import STORAGE_CHANNEL_ID_SERIES
                            if ("wrong file id" in str(e).lower()) and msg_id_to_copy and STORAGE_CHANNEL_ID_SERIES:
                                try:
                                    copied_message = await context.bot.copy_message(
                                        chat_id=user.id,
                                        from_chat_id=STORAGE_CHANNEL_ID_SERIES,
                                        message_id=msg_id_to_copy,
                                        protect_content=True
                                    )
                                    await context.bot.edit_message_caption(
                                        chat_id=user.id,
                                        message_id=copied_message.message_id,
                                        caption=video_caption,
                                        parse_mode="HTML",
                                        reply_markup=video_reply_markup
                                    )
                                    job_data = {'chat_id': user.id, 'message_id': copied_message.message_id}
                                    context.job_queue.run_once(
                                        delete_message_job, 
                                        when=14400, 
                                        data=job_data,
                                        name=f"del_{user.id}_{copied_message.message_id}"
                                    )
                                except Exception:
                                    await status_msg.edit_text("😔 Erro ao recuperar vídeo.")
                            else:
                                await status_msg.edit_text(f"😔 Erro ao enviar vídeo: {e}")
                        except Exception as e:
                             await status_msg.edit_text(f"😔 Erro geral: {e}")

                        await status_msg.delete()
                    else:
                        await status_msg.edit_text("😔 Desculpe, esta versão do áudio não está disponível.")
                except Exception as e:
                    print(f"Erro watch_ep: {e}")
                    await context.bot.send_message(chat_id=user.id, text="Erro ao carregar episódio.")
                return
            
            # ROTA 2: Mostrar Episódio (show_ep_)
            elif payload.startswith("show_ep_"):
                try:
                    episode_id = int(payload.split('_')[2])
                    await db.get_or_create_user(user_id=user.id, first_name=user.first_name)

                    if not await db.is_user_vip(user.id):
                        config = await db.get_bot_config()
                        price = config.get('vip_price', 4.99)
                        duration_days = config.get('vip_duration_days', 7)

                        if price <= 0:
                            await db.set_user_as_vip(user.id, duration_days=duration_days if duration_days > 0 else 9999)
                            try: await context.bot.send_message(chat_id=user.id, text="🎉 Bem-vindo! Acesso gratuito.")
                            except: pass
                        else:
                            context.user_data['update'] = update
                            sales_text, reply_markup = await _get_vip_sales_message(context)
                            await context.bot.send_message(chat_id=user.id, text=f"Opa, {user.first_name}! 👋\n\n{sales_text}", parse_mode="HTML", reply_markup=reply_markup)
                            return

                    status_msg = await context.bot.send_message(chat_id=user.id, text="⏳ Carregando seu episódio...")
                    
                    message_text, reply_markup = await _get_episode_details_message(
                        episode_id,
                        context.bot.username,
                        delete_msg_id=status_msg.message_id
                    )

                    if reply_markup:
                        try:
                            await status_msg.edit_text(text=message_text, reply_markup=reply_markup, parse_mode="HTML")
                        except:
                            await status_msg.edit_text(text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
                    else:
                        await status_msg.edit_text(text=message_text)
                except Exception as e:
                    print(f"Erro show_ep: {e}")
                    await context.bot.send_message(chat_id=user.id, text="Erro ao carregar episódio.")
                return

            # ROTA 3: Assistir Filme (watch_)
            elif payload.startswith("watch_"):
                esta_inscrito = await verificar_inscricao(context.bot, user.id)
                if not esta_inscrito:
                    link_recarregar = f"https://t.me/{context.bot.username}?start={payload}"
                    keyboard_fsub = [
                        [InlineKeyboardButton("📢 Entrar no Canal", url=FSUB_CHANNEL_LINK)],
                        [InlineKeyboardButton("💬 Entrar no Grupo", url=FSUB_GROUP_LINK)], 
                        [InlineKeyboardButton("🔄 Já entrei! Tentar Novamente", url=link_recarregar)]
                    ]
                    await context.bot.send_message(chat_id=user.id, text="🚫 <b>Acesso Restrito!</b>\nEntre nos canais para assistir.", parse_mode="HTML",reply_markup=InlineKeyboardMarkup(keyboard_fsub))
                    return
                
                context.args = [payload.split('_')[1]]
                # Chama o handler que importamos de player.py
                await watch_command_handler(update, context, message_deletada=True)
                return

            # ROTA 4: Atalho VIP
            elif payload == "vip":
                # --- CORREÇÃO: Chamada direta sem simular botão ---
                # Isso evita importar handlers que não existem mais ou mudaram de nome.
                sales_text, reply_markup = await _get_vip_sales_message(context)

                direct_keyboard = [
                    [InlineKeyboardButton("✅ Sim, Gerar PIX para Pagar!", callback_data="confirm_pay")],
                    [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]
                ]

                await context.bot.send_message(
                    chat_id=user.id, 
                    text=f"Opa, {user.first_name}! 👋\n\n{sales_text}", 
                    parse_mode="HTML", 
                    reply_markup=InlineKeyboardMarkup(direct_keyboard)
                )
                return
            
            elif payload.startswith("serie_"):
                # 1. Trava do FSub (Inscrição Obrigatória)
                esta_inscrito = await verificar_inscricao(context.bot, user.id)
                if not esta_inscrito:
                    link_recarregar = f"https://t.me/{context.bot.username}?start={payload}"
                    keyboard_fsub = [
                        [InlineKeyboardButton("📢 Entrar no Canal", url=FSUB_CHANNEL_LINK)],
                        [InlineKeyboardButton("💬 Entrar no Grupo", url=FSUB_GROUP_LINK)], 
                        [InlineKeyboardButton("🔄 Já entrei! Tentar Novamente", url=link_recarregar)]
                    ]
                    await context.bot.send_message(
                        chat_id=user.id, 
                        text="🚫 *Acesso Restrito!*\nEntre nos canais para assistir.", 
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard_fsub)
                    )
                    return
                
                # 2. Carregar e mostrar a Série
                try:
                    serie_id = int(payload.split('_')[1])
                    status_msg = await context.bot.send_message(chat_id=user.id, text="⏳ Carregando sua série...")

                    # Puxa os dados do DB (Igual você faz na busca inline)
                    series = await db.get_series_by_id(serie_id)
                    if not series:
                        await status_msg.edit_text("❌ Série não encontrada no catálogo.")
                        return

                    seasons = await db.get_seasons_for_series(serie_id)
                    
                    poster = series.get('poster_url')
                    if not poster: 
                        poster = 'https://via.placeholder.com/500x750.png?text=Sem+Pôster'
                    
                    # Legenda idêntica à que você já usa na busca!
                    photo_caption = (
                        f"📺 *{series.get('title', 'Série')}*\n\n"
                        f"🗓️ *Ano:* {series.get('year', 'N/A')}\n"
                        f"🎭 *Gênero:* {series.get('genre', 'N/A')}\n\n"
                        f"📝 *Sinopse:* {series.get('description', series.get('overview', 'N/A'))[:400]}...\n\n"
                        f"---\nSelecione a temporada desejada abaixo:"
                    )
                    
                    # Botões de Temporada (com a sua mágica do inline)
                    keyboard = []
                    if seasons:
                        for season in seasons:
                            keyboard.append([InlineKeyboardButton(f"▶️ Temporada {season['season_number']}", switch_inline_query_current_chat=f"season:{season['id']}:0")])
                    
                    keyboard.append([InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=series.get('title', ''))])
                    
                    # Envia a foto com tudo pronto
                    await context.bot.send_photo(
                        chat_id=user.id,
                        photo=poster,
                        caption=photo_caption,
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    await status_msg.delete()
                    
                except Exception as e:
                    print(f"Erro na Rota de Série (start): {e}")
                    await context.bot.send_message(chat_id=user.id, text="Erro ao processar a série.")
                return

        # === MENU PRINCIPAL ===
        try:
            user_info = await db.get_or_create_user(user_id=user.id, first_name=user.first_name)
            
            # Verifica se retornou uma tupla (user_data, is_new)
            if isinstance(user_info, tuple):
                _, is_new_user = user_info
            else:
                # Caso o database.py ainda esteja na versão antiga
                is_new_user = False
        except Exception:
            # Fallback de segurança
            is_new_user = False

        # 🎉 SE FOR NOVO, MANDA A MENSAGEM DO TESTE GRÁTIS 🎉
        if is_new_user:
            trial_text = (
                f"🎉 <b>PARABÉNS, {user.first_name}! VOCÊ GANHOU UM PRESENTE!</b> 🎁\n\n"
                "Como boas-vindas, liberamos <b>4 HORAS DE ACESSO VIP TOTAL!</b> 🚀\n\n"
                "✅ Filmes e Séries sem limites.\n"
                "✅ Alta velocidade.\n"
                "✅ Catálogo completo liberado.\n\n"
                "⏳ <i>Seu tempo já está contando... Corra para maratonar!</i>\n\n"
                "👇 <b>Gostou? Garanta 30 DIAS por preço de banana aqui:</b>"
            )
            
            # Botão de venda imediata
            keyboard_trial = [
                [InlineKeyboardButton("💎 Quero garantir 30 Dias de VIP!", callback_data="main_vip")],
                [InlineKeyboardButton("🔎 Ir para o Catálogo (Teste Grátis)", switch_inline_query_current_chat="")]
            ]
            
            await context.bot.send_message(
                chat_id=user.id,
                text=trial_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard_trial)
            )
            
        config = await db.get_bot_config()
        price = config.get('vip_price', 4.99)
        is_free = price <= 0
        user_is_vip = await db.is_user_vip(user.id)

        keyboard = [[InlineKeyboardButton("Buscar Mídia 🔎", switch_inline_query_current_chat="")]]
        
        if not is_free and not user_is_vip:
            keyboard.append([InlineKeyboardButton("Adquirir VIP 🚀", callback_data="main_vip")])

        keyboard.append([InlineKeyboardButton("🔖 Minha Lista", callback_data="fav_menu")])

        user_dict = user_info[0] if isinstance(user_info, tuple) else user_info
        meus_pontos = user_dict.get('points', 0) if user_dict else 0
        custom_menu_text = config.get('main_menu_text')

        texto_convite = (
            "Ganhe +4 HORAS de VIP grátis!\n"
            "Envie esse bot para seus amigos clicarem no link abaixo:\n\n"
            f"https://t.me/{context.bot.username}?start=ref_{user.id}\n\n"
            "✨ Bônus: Se o seu amigo assinar o VIP, você ganha 1 Ponto. Junte 5 Pontos e troque por 1 MÊS GRÁTIS!"
        )

        keyboard.extend([[InlineKeyboardButton("Pedir Filme/Série 💡", callback_data="main_request"), InlineKeyboardButton("Top Mídia 🏆", callback_data="main_top")], [InlineKeyboardButton("🎁 Convidar Amigos (Ganhe VIP)", switch_inline_query=texto_convite)]])

        main_menu = InlineKeyboardMarkup(keyboard)
        community_link = "https://t.me/+-v5nIbZ93J43MmQ5"
        if custom_menu_text:
            # Se o admin configurou um texto, substitui as variáveis mágicas
            welcome_text = custom_menu_text.replace('{USER_NAME}', user.mention_html()).replace('{POINTS}', str(meus_pontos)).replace('{COMMUNITY_LINK}', community_link)
        else:
            # Texto Padrão (Caso o banco esteja vazio ou seja a primeira vez)
            welcome_text = (
                f"Olá {user.mention_html()}! 👋\n\n"
                "🍿 <b>O MELHOR BOT DE FILMES E SÉRIES!</b>\n"
                "Clique em \"Buscar Mídia 🔎\" abaixo para começar.\n\n"
                "_________________\n\n"
                "🎁 <b>INDIQUE E GANHE:</b>\n"
                f"🏆 <b>Seus Pontos: {meus_pontos}/5</b>\n\n"
                "• Indique um amigo e <b>ganhe +4h VIP</b> na hora!\n"
                "• Se ele assinar o VIP Mensal, você <b>ganha 1 Ponto</b>.\n"
                "• Junte 5 Pontos e ganhe <b>1 MÊS GRÁTIS!</b>\n"
                "_________________\n\n"
                "💎 <b>Quer liberar tudo sem limites agora?</b>\n"
                "Clique em \"Adquirir VIP\" no menu abaixo!\n\n"
                f"💬 <a href=\"{community_link}\">Junte-se à nossa comunidade!</a>"
            )

        if is_query:
            try:
                if message_to_reply.photo:
                    # Fotos não geram preview de link, então não precisa adicionar aqui
                    await message_to_reply.edit_caption(caption=welcome_text, reply_markup=main_menu, parse_mode='HTML')
                else:
                    await message_to_reply.edit_text(
                        welcome_text, 
                        reply_markup=main_menu, 
                        parse_mode='HTML',
                        disable_web_page_preview=True  # <--- ADICIONADO AQUI
                    )
            except Exception:
                await context.bot.send_message(
                    chat_id=user.id, 
                    text=welcome_text, 
                    reply_markup=main_menu, 
                    parse_mode='HTML',
                    disable_web_page_preview=True  # <--- ADICIONADO AQUI
                )
                if message_to_reply:
                    try: await message_to_reply.delete()
                    except: pass
        else:
            await message_to_reply.reply_html(
                welcome_text, 
                reply_markup=main_menu,
                disable_web_page_preview=True  # <--- ADICIONADO AQUI
            )

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if 'state' in context.user_data:
        del context.user_data['state']
        await update.message.reply_text("Operação cancelada.")
    else:
        await update.message.reply_text("Não há nenhuma operação para cancelar.")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "Olá! Eu sou o Cine Pipoca, seu assistente de filmes e séries. Veja como me usar:\n\n"
        "🔎 <b>Para Buscar:</b>\n"
        "Vá em qualquer chat, digite o <code>@username</code> do bot e comece a escrever o nome do filme ou série. Uma lista de resultados aparecerá!\n\n"
        "💡 <b>Pedir um Filme/Série:</b>\n"
        "Use o botão 'Pedir Filme/Série' no menu principal para sugerir um título que você não encontrou.\n\n"
        "🏆 <b>Top Mídia:</b>\n"
        "Quer saber o que está em alta? Clique no botão 'Top Mídia' no menu e escolha o período.\n\n"
        "🚀 <b>Acesso Pipoca Premium:</b>\n"
        "O acesso Premium te dá direito a assistir todo o catálogo. Você pode adquirir o seu através do botão no menu principal."
    )
    help_text = help_text.replace("@username", f"@{context.bot.username}")
    await update.message.reply_text(help_text, parse_mode="HTML")

async def back_to_main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_call(query, "answer")
    await start(update, context)
    