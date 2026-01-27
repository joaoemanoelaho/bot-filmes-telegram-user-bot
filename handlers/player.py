import time
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
import asyncio
from .. import database as db
from .. import tastedive_api
from config import STORAGE_CHANNEL_ID, STORAGE_CHANNEL_ID_SERIES
from handlers.common import DB_SEMAPHORE, safe_call, delete_message_job, get_vip_sales_message

# --- HELPER INTERNO: Detalhes do Episódio ---
async def _get_episode_details_message(episode_id: int, bot_username: str, delete_msg_id: int = None) -> tuple[str, InlineKeyboardMarkup]:
    try:
        details = await db.get_full_episode_details(episode_id)
        if not details: return ("Erro: Episódio não encontrado.", None)

        episode = details
        season = details.get('seasons')
        series = season.get('series') if season else None

        if not season or not series: return ("Erro: Dados da temporada ou série ausentes.", None)

        series_title = series.get('title', 'Série')
        ep_title = episode.get('title', f"Episódio {episode['episode_number']}")
        
        message_text = (
            f"📽️ *{series_title}*\n"
            f"🎬 *Temporada:* {season['season_number']}\n"
            f"🎯 *Episódio:* {episode['episode_number']} - {ep_title}\n"
            f"--------------------\n"
            f"Selecione o áudio (o bot irá te chamar no privado):"
        )

        keyboard = []
        audio_row = []
        
        # Botões de Áudio (Deeplink para /start)
        if episode.get('dubbed_file_id'):
            payload = f"watch_ep_{episode['id']}_dub"
            if delete_msg_id: payload += f"_del_{delete_msg_id}"
            url = f"https://t.me/{bot_username}?start={payload}"
            audio_row.append(InlineKeyboardButton("Dublado 🇧🇷", url=url))
            
        if episode.get('subtitled_file_id'):
            payload = f"watch_ep_{episode['id']}_sub"
            if delete_msg_id: payload += f"_del_{delete_msg_id}"
            url = f"https://t.me/{bot_username}?start={payload}"
            audio_row.append(InlineKeyboardButton("Legendado 🇺🇸", url=url))

        if audio_row: keyboard.append(audio_row)
        return (message_text, InlineKeyboardMarkup(keyboard))
    except Exception as e:
        print(f"Erro helper episodio: {e}")
        return ("Erro ao carregar.", None)

# --- COMANDO /watch ---
async def watch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with DB_SEMAPHORE:
        try: await update.message.delete()
        except: pass
        
        if not context.args: return
        movie_id = context.args[0]
        user_id = update.effective_user.id

        await db.get_or_create_user(user_id, update.effective_user.first_name)

        # VIP Check
        if not await db.is_user_vip(user_id):
            config = await db.get_bot_config()
            if config.get('vip_price', 0) > 0:
                sales_text, markup = await get_vip_sales_message()
                await context.bot.send_message(user_id, text=f"🔒 **VIP Necessário**\n\n{sales_text}", reply_markup=markup, parse_mode="Markdown")
                return

        movie = await db.get_movie_by_id(movie_id)
        if movie and movie.get('poster_url'):
            kb = []
            if movie.get('dubbed_file_id'):
                kb.append(InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"play_{movie_id}_dub"))
            if movie.get('subtitled_file_id'):
                kb.append(InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"play_{movie_id}_sub"))

            caption = f"🎬 *{movie['title']}* ({movie['year']})\n🎭 {movie.get('genre','N/A')}\n📝 {movie.get('description','N/A')[:200]}..."
            markup = InlineKeyboardMarkup([kb]) if kb else None
            
            await context.bot.send_photo(user_id, movie['poster_url'], caption=caption, parse_mode="Markdown", reply_markup=markup)
        else:
            await context.bot.send_message(user_id, "Filme não encontrado.")

# --- CALLBACKS DO PLAYER (play_, ep_nav_, related_) ---
async def player_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user_id = query.from_user.id
    
    # Cooldown simples
    now = time.time()
    last = context.user_data.get('last_play', 0)
    if now - last < 3:
        await safe_call(query, "answer", text="⏳ Aguarde...", show_alert=False)
        return
    context.user_data['last_play'] = now

    async with DB_SEMAPHORE:
        # 1. PLAY FILME
        if data.startswith("play_"):
            await safe_call(query, "answer")
            _, movie_id, audio = data.split('_')
            movie = await db.get_movie_by_id(int(movie_id))
            
            if not movie:
                await safe_call(query, "edit_message_caption", caption="❌ Filme não encontrado.")
                return

            file_id = movie.get('dubbed_file_id') if audio == 'dub' else movie.get('subtitled_file_id')
            msg_id = movie.get('dubbed_msg_id') if audio == 'dub' else movie.get('subtitled_msg_id')

            if file_id:
                await safe_call(query, "delete_message")
                caption = f"🎬 *{movie['title']}*\n🍿 Assistido com @{context.bot.username}\n⚠️ *Apaga em 4h*"
                
                # Fav Toggle
                fav_code = f"mov_{movie_id}_{audio}"
                is_fav = await db.is_favorite(user_id, fav_code)
                fav_txt = "❌ Remover" if is_fav else "🔖 Salvar"
                
                kb = [
                    [InlineKeyboardButton("❤️ Share", switch_inline_query=movie['title']), 
                     InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{movie_id}")],
                    [InlineKeyboardButton(fav_txt, callback_data=f"fav_toggle_{fav_code}")]
                ]
                
                try:
                    # Plano A
                    msg = await context.bot.send_video(user_id, file_id, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown", protect_content=True)
                    # Agenda Deleção
                    job_data = {'chat_id': user_id, 'message_id': msg.message_id}
                    context.job_queue.run_once(delete_message_job, 14400, data=job_data, name=f"del_{user_id}_{msg.message_id}")
                except BadRequest:
                    # Plano B (Copy)
                    if msg_id and STORAGE_CHANNEL_ID:
                        try:
                            cp_msg = await context.bot.copy_message(user_id, STORAGE_CHANNEL_ID, msg_id, protect_content=True)
                            await context.bot.edit_message_caption(user_id, cp_msg.message_id, caption=caption, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                            
                            job_data = {'chat_id': user_id, 'message_id': cp_msg.message_id}
                            context.job_queue.run_once(delete_message_job, 14400, data=job_data, name=f"del_{user_id}_{cp_msg.message_id}")
                        except:
                            await context.bot.send_message(user_id, "❌ Erro: Arquivo original perdido.")
            else:
                await safe_call(query, "answer", text="Áudio indisponível.", show_alert=True)

        # 2. NAVEGAÇÃO DE EPISÓDIO
        elif data.startswith("ep_nav_"):
            await safe_call(query, "answer")
            ep_id = int(data.split('_')[2])
            
            try: await query.message.delete()
            except: pass
            
            msg = await context.bot.send_message(user_id, "⏳ Carregando...")
            text, markup = await _get_episode_details_message(ep_id, context.bot.username, msg.message_id)
            
            if markup: await msg.edit_text(text, reply_markup=markup, parse_mode="Markdown")
            else: await msg.edit_text(text)

        # 3. RELACIONADOS
        elif data.startswith("related_"):
            await safe_call(query, "answer", text="🔍 Buscando similares...")
            parts = data.split('_')
            media_id = int(parts[1])
            is_series = len(parts) > 2 and parts[2] == 'series'
            
            title = None
            if is_series:
                obj = await db.get_series_by_id(media_id)
                if obj: title = obj['title']
            else:
                obj = await db.get_movie_by_id(media_id)
                if obj: title = obj['title']
            
            if title:
                recs = await tastedive_api.get_recommendations(title)
                valid_recs = await db.filter_existing_titles(recs) if recs else []
                
                if valid_recs:
                    kb = [[InlineKeyboardButton(f"🔎 {t}", switch_inline_query_current_chat=t)] for t in valid_recs]
                    await query.message.reply_text(f"Se curtiu '{title}', tente estes:", reply_markup=InlineKeyboardMarkup(kb))
                else:
                    await query.message.reply_text("Sem recomendações no catálogo por enquanto.")
            else:
                await query.message.reply_text("Mídia original não encontrada.")

        # 4. SHOW CARD (Info do Top Mídia)
        elif data.startswith("show_card_"):
            await safe_call(query, "answer")
            mid = int(data.split('_')[2])
            movie = await db.get_movie_by_id(mid)
            
            if movie:
                try: await query.message.delete()
                except: pass
                
                url = f"https://t.me/{context.bot.username}?start=watch_{mid}"
                kb = [[InlineKeyboardButton("Assistir ⏯️", url=url)]]
                cap = f"🎬 *{movie['title']}*\n🎭 {movie.get('genre','N/A')}"
                await context.bot.send_photo(user_id, movie['poster_url'], caption=cap, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
