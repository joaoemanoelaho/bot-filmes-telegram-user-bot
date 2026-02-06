from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
import asyncio
import database as db
from handlers.common import (
    DB_SEMAPHORE, safe_call, _get_vip_sales_message, 
    _get_episode_details_message, delete_message_job
)

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

        # === TRATAMENTO DE DEEP LINKS (start=...) ===
        if context.args:
            payload = context.args[0]

            if not is_query:
                try:
                    await message_to_reply.delete()
                except Exception:
                    pass

            # ROTA 1: Assistir Episódio (watch_ep_)
            if payload.startswith("watch_ep_"):
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
                            f"📺 *{series_title}*\n"
                            f"S{season_number:02d}E{episode_data.get('episode_number', 0):02d}: *{episode_data.get('title', 'Episódio')}* {audio_text}\n\n"
                            f"---\n"
                            f"🍿 Assistido com @{bot_username}\n"
                            f"⚠️ *Este vídeo será apagado em 4 horas.*"
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
                                parse_mode="Markdown",
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
                                        parse_mode="Markdown",
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
                        await status_msg.edit_text(text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
                    else:
                        await status_msg.edit_text(text=message_text)
                except Exception as e:
                    print(f"Erro show_ep: {e}")
                    await context.bot.send_message(chat_id=user.id, text="Erro ao carregar episódio.")
                return

            # ROTA 3: Assistir Filme (watch_)
            elif payload.startswith("watch_"):
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

        # === MENU PRINCIPAL ===
        await db.get_or_create_user(user_id=user.id, first_name=user.first_name)
        config = await db.get_bot_config()
        price = config.get('vip_price', 4.99)
        is_free = price <= 0
        user_is_vip = await db.is_user_vip(user.id)

        keyboard = [[InlineKeyboardButton("Buscar Mídia 🔎", switch_inline_query_current_chat="")]]
        
        if not is_free and not user_is_vip:
            keyboard.append([InlineKeyboardButton("Adquirir VIP 🚀", callback_data="main_vip")])

        keyboard.append([InlineKeyboardButton("🔖 Minha Lista", callback_data="fav_menu")])
        keyboard.extend([[InlineKeyboardButton("Pedir Filme/Série 💡", callback_data="main_request"), InlineKeyboardButton("Top Mídia 🏆", callback_data="main_top")]])

        main_menu = InlineKeyboardMarkup(keyboard)
        community_link = "https://t.me/+-v5nIbZ93J43MmQ5"
        ads_channel_link = "https://t.me/meucinepipocacanal"

        ads_channel_text = (
            "Para manter o bot 100% gratuito, preciso da sua ajuda!\n"
            f"➡️ <a href=\"{ads_channel_link}\"><b>Entre no nosso Canal de Avisos</b></a> ⬅️\n"
            "É lá que posto os anúncios que pagam o servidor."
        )
        community_text = (
            "Psst! 🤫 Quer debater sobre filmes, pedir séries, ou dar ideias para o bot?\n"
            f"➡️ <a href=\"{community_link}\"><b>Junte-se à nossa comunidade!</b></a>"
        )
        welcome_text = (
            f"Olá {user.mention_html()}! 👋\n\n"
            "🍿 <b>O MELHOR BOT DE FILMES E SÉRIES!</b> 🍿\n\n"
            "Gosta de maratonar? Esse bot é perfeito para isso 😉.\n\n"
            "Clique no botão \"Buscar Mídia 🔎\" para começar.\n\n"
            "Ficou com dúvidas? Envie o comando /help\n\n"
            "_________________\n\n"
            "Para liberar acesso ilimitado e alta velocidade, torne-se VIP!\n"
            "💎 <b>Clique em \"Adquirir VIP\" no menu abaixo!</b>\n\n"
            "_________________\n\n"
            "Psst! 🤫 Quer debater sobre filmes, pedir séries, ou dar ideias para o bot?\n"
            f"➡️ <a href=\"{community_link}\"><b>Junte-se à nossa comunidade!</b></a>"
        )

        if is_query:
            try:
                if message_to_reply.photo:
                     await message_to_reply.edit_caption(caption=welcome_text, reply_markup=main_menu, parse_mode='HTML')
                else:
                    await message_to_reply.edit_text(welcome_text, reply_markup=main_menu, parse_mode='HTML')
            except Exception:
                await context.bot.send_message(chat_id=user.id, text=welcome_text, reply_markup=main_menu, parse_mode='HTML')
                if message_to_reply:
                    try: await message_to_reply.delete()
                    except: pass
        else:
            await message_to_reply.reply_html(welcome_text, reply_markup=main_menu)

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if 'state' in context.user_data:
        del context.user_data['state']
        await update.message.reply_text("Operação cancelada.")
    else:
        await update.message.reply_text("Não há nenhuma operação para cancelar.")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "Olá! Eu sou o Cine Pipoca, seu assistente de filmes e séries. Veja como me usar:\n\n"
        "🔎 **Para Buscar:**\n"
        "Vá em qualquer chat, digite o `@username` do bot e comece a escrever o nome do filme ou série. Uma lista de resultados aparecerá!\n\n"
        "💡 **Pedir um Filme/Série:**\n"
        "Use o botão 'Pedir Filme/Série' no menu principal para sugerir um título que você não encontrou.\n\n"
        "🏆 **Top Mídia:**\n"
        "Quer saber o que está em alta? Clique no botão 'Top Mídia' no menu e escolha o período.\n\n"
        "🚀 **Acesso Pipoca Premium:**\n"
        "O acesso Premium te dá direito a assistir todo o catálogo. Você pode adquirir o seu através do botão no menu principal."
    )
    help_text = help_text.replace("@username", f"@{context.bot.username}")
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def back_to_main_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await safe_call(query, "answer")
    await start(update, context)
    