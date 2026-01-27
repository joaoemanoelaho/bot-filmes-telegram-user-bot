import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
import asyncio
import database as db
import tastedive_api
from config import STORAGE_CHANNEL_ID, STORAGE_CHANNEL_ID_SERIES
from handlers.common import (
    DB_SEMAPHORE, safe_call, delete_message_job, 
    _get_episode_details_message, _get_vip_sales_message
)

async def watch_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, message_deletada: bool = False) -> None:
    async with DB_SEMAPHORE:
        if update.message and not message_deletada:
            try: await update.message.delete()
            except Exception: pass

        if not context.args: return
        movie_id = context.args[0]
        user_id = update.effective_user.id
        chat_id_to_reply = update.effective_chat.id

        await db.get_or_create_user(user_id=user_id, first_name=update.effective_user.first_name)

        # VIP Check
        if not await db.is_user_vip(user_id):
            config = await db.get_bot_config()
            price = config.get('vip_price', 4.99)
            duration_days = config.get('vip_duration_days', 7)

            if price <= 0:
                await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
                try: await context.bot.send_message(chat_id=user_id, text="🎉 Bem-vindo! O acesso está gratuito no momento.")
                except: pass
            else:
                context.user_data['update'] = update
                sales_text, reply_markup = await _get_vip_sales_message(context)
                await context.bot.send_message( 
                    chat_id=user_id,
                    text=f"Opa, {update.effective_user.first_name}! 👋\n\n{sales_text}",
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                return

        movie = await db.get_movie_by_id(movie_id)

        if movie and movie.get('poster_url'):
            audio_buttons = []
            if movie.get('dubbed_file_id'):
                audio_buttons.append(InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"play_{movie_id}_dub"))
            if movie.get('subtitled_file_id'):
                audio_buttons.append(InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"play_{movie_id}_sub"))

            caption = (
                f"🎬 *{movie['title']}* ({movie['year']})\n\n"
                f"🎭 *Gênero:* {movie.get('genre', 'N/A')}\n\n"
                f"📝 *Sinopse:* {movie.get('description', 'N/A')}\n\n"
                "---\n"
            )

            keyboard = []
            reply_markup = None
            if audio_buttons:
                caption += "Selecione o áudio desejado abaixo:"
                keyboard.append(audio_buttons)
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                caption += "😔 *Este filme está no catálogo, mas ainda estamos aguardando os arquivos de vídeo.*"

            await context.bot.send_photo(
                chat_id=chat_id_to_reply,
                photo=movie['poster_url'],
                caption=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        else:
            await context.bot.send_message(chat_id=chat_id_to_reply, text="Filme não encontrado ou sem pôster disponível.")

async def player_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Centraliza os callbacks de mídia: play_, ep_nav_, related_, top_, main_top, show_card_
    """
    query = update.callback_query
    callback_data = query.data
    user_id = query.from_user.id
    
    # 1. PLAY MOVIE (play_)
    if callback_data.startswith("play_"):
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10

        if now - last_request < cooldown:
            await safe_call(query, "answer", text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s.", show_alert=True)
            return

        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            _, movie_id_str, audio_choice = callback_data.split('_')
            movie_id = int(movie_id_str)
            await safe_call(query, "edit_message_caption", caption="⏳ Carregando seu filme, por favor aguarde...")

            movie = await db.get_movie_by_id(movie_id)

            if not movie:
                await safe_call(query, "edit_message_caption", caption="Erro: Filme não encontrado.")
                return
            
            file_id_to_send = movie.get('dubbed_file_id') if audio_choice == "dub" else movie.get('subtitled_file_id')
            msg_id_to_copy = movie.get('dubbed_msg_id') if audio_choice == "dub" else movie.get('subtitled_msg_id')

            if file_id_to_send:
                await safe_call(query, "delete_message")
                bot_username = context.bot.username
                video_caption = (
                    f"🎬 *{movie['title']}* ({movie['year']})\n\n"
                    f"🎭 *Gênero:* {movie['genre']}\n\n"
                    f"---\n"
                    f"🍿 Assistido com @{bot_username}\n"
                    f"⚠️ *Este vídeo será apagado em 4 horas.*"
                )

                fav_unique_code = f"mov_{movie_id}_{audio_choice}"
                is_fav = await db.is_favorite(user_id, fav_unique_code)
                fav_btn_text = "❌ Remover da Lista" if is_fav else "🔖 Salvar na Lista"

                keyboard = [
                    [InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"{movie['title']}"),
                     InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{movie_id}")],
                    [InlineKeyboardButton(fav_btn_text, callback_data=f"fav_toggle_{fav_unique_code}")]
                ]
                video_reply_markup = InlineKeyboardMarkup(keyboard)

                try:
                    # Plano A
                    sent_message = await context.bot.send_video(
                        chat_id=query.message.chat.id,
                        video=file_id_to_send,
                        caption=video_caption,
                        parse_mode="Markdown",
                        reply_markup=video_reply_markup,
                        protect_content=True
                    )
                    job_data = {'chat_id': sent_message.chat_id, 'message_id': sent_message.message_id}
                    context.job_queue.run_once(delete_message_job, 14400, data=job_data, name=f"del_{sent_message.chat_id}_{sent_message.message_id}")
                except BadRequest as e:
                    # Plano B
                    if ("wrong file id" in str(e).lower()) and msg_id_to_copy and STORAGE_CHANNEL_ID:
                        try:
                            copied_message = await context.bot.copy_message(
                                chat_id=query.message.chat.id,
                                from_chat_id=STORAGE_CHANNEL_ID,
                                message_id=msg_id_to_copy,
                                protect_content=True
                            )
                            await context.bot.edit_message_caption(
                                chat_id=query.message.chat.id,
                                message_id=copied_message.message_id,
                                caption=video_caption,
                                parse_mode="Markdown",
                                reply_markup=video_reply_markup
                            )
                            job_data = {'chat_id': query.message.chat.id, 'message_id': copied_message.message_id}
                            context.job_queue.run_once(delete_message_job, 14400, data=job_data, name=f"del_{query.message.chat.id}_{copied_message.message_id}")
                        except Exception:
                            await context.bot.send_message(chat_id=query.message.chat.id, text="😔 Erro ao recuperar arquivo.")
                    else:
                        await context.bot.send_message(chat_id=query.message.chat.id, text=f"😔 Erro ao enviar: {e}")

                await db.log_movie_view(movie_id=movie_id, user_id=user_id)
                context.user_data['last_action_time'] = time.time()
            else:
                await safe_call(query, "edit_message_caption", caption="😔 Versão indisponível.")

    # 2. RELACIONADOS (related_)
    elif callback_data.startswith("related_"):
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10
        if now - last_request < cooldown:
            await safe_call(query, "answer", text=f"✋ Aguarde {int(cooldown - (now - last_request))}s.", show_alert=True)
            return

        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            parts = callback_data.split('_')
            media_id = int(parts[1])
            media_type = 'series' if len(parts) > 2 and parts[2] == 'series' else 'movie'
            
            title_to_search = None
            if media_type == 'movie':
                media_obj = await db.get_movie_by_id(media_id)
                if media_obj: title_to_search = media_obj['title']
            else:
                media_obj = await db.get_series_by_id(media_id)
                if media_obj: title_to_search = media_obj['title']
            
            if not title_to_search:
                await context.bot.send_message(chat_id=user_id, text="Mídia original não encontrada.")
                return
            
            status_msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ Buscando similares a '{title_to_search}'...")
            recommendations = await tastedive_api.get_recommendations(title_to_search)
            valid_recs = await db.filter_existing_titles(recommendations) if recommendations else []

            if not valid_recs:
                await status_msg.edit_text("Sem recomendações no catálogo.")
                return
            
            keyboard = [[InlineKeyboardButton(f"🔎 {t}", switch_inline_query_current_chat=t)] for t in valid_recs]
            await status_msg.edit_text(text=f"Sugestões baseadas em '{title_to_search}':", reply_markup=InlineKeyboardMarkup(keyboard))
            context.user_data['last_action_time'] = time.time()

    # 3. NAVEGAÇÃO EPISÓDIO (ep_nav_)
    elif callback_data.startswith("ep_nav_"):
        async with DB_SEMAPHORE:
            try: await safe_call(query, "answer")
            except: pass

            new_episode_id = int(callback_data.split('_')[2])
            try: await safe_call(query, "delete_message")
            except: pass

            placeholder_msg = await context.bot.send_message(chat_id=user_id, text="Carregando...")
            message_text, reply_markup = await _get_episode_details_message(
                new_episode_id,
                context.bot.username,
                delete_msg_id=placeholder_msg.message_id
            )

            if reply_markup:
                await placeholder_msg.edit_text(text=message_text, reply_markup=reply_markup, parse_mode="Markdown")
            else:
                await placeholder_msg.edit_text(message_text)

    # 4. RANKING TOP (main_top, top_, show_card_)
    elif callback_data == "main_top":
        async with DB_SEMAPHORE:
            # VIP check (resumido para caber)
            if not await db.is_user_vip(user_id):
                config = await db.get_bot_config()
                if config.get('vip_price', 0) > 0:
                     context.user_data['update'] = update
                     sales_text, markup = await _get_vip_sales_message(context)
                     await safe_call(query, "edit_message_text", text=sales_text, reply_markup=markup, parse_mode="Markdown")
                     return
            
            await safe_call(query, "answer")
            keyboard = [
                [InlineKeyboardButton("🏆 Top Semana", callback_data="top_7")],
                [InlineKeyboardButton("🗓️ Top Mês", callback_data="top_30")],
                [InlineKeyboardButton("🌎 Top Geral", callback_data="top_0")],
                [InlineKeyboardButton("⬅️ Voltar", callback_data="back_to_main")]
            ]
            await safe_call(query, "edit_message_text", text="Selecione o período:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif callback_data.startswith("top_"):
        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            period_days = int(callback_data.split('_')[1])
            period_text = "Geral"
            if period_days == 7: period_text = "da Semana"
            if period_days == 30: period_text = "do Mês"
            
            await safe_call(query, "edit_message_text", text=f"🏆 Buscando Top 10 {period_text}...")
            trending = await db.get_trending(period_days=period_days)

            if not trending:
                await safe_call(query, "edit_message_text", text="Dados insuficientes.")
                return

            keyboard = []
            for m in trending:
                keyboard.append([InlineKeyboardButton(f"{m['title']} ({m['year']})", callback_data=f"show_card_{m['movie_id']}")])
            keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="main_top")])
            
            await safe_call(query, "edit_message_text", text=f"🏆 **Top 10 {period_text}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif callback_data.startswith("show_card_"):
        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            movie_id = int(callback_data.split('_')[2])
            movie = await db.get_movie_by_id(movie_id)

            if not movie:
                await safe_call(query, "edit_message_text", text="Filme não encontrado.")
                return

            try: await safe_call(query, "delete_message")
            except: pass

            url = f"https://t.me/{context.bot.username}?start=watch_{movie['movie_id']}"
            keyboard = [[
                InlineKeyboardButton("Assistir ⏯️", url=url),
                InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=movie['title'])
            ]]
            caption = f"🎬 *{movie['title']}* ({movie['year']})\n🎭 {movie.get('genre', 'N/A')}"
            
            await context.bot.send_photo(chat_id=user_id, photo=movie['poster_url'], caption=caption, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
