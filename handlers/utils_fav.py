from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
import database as db
from config import STORAGE_CHANNEL_ID, STORAGE_CHANNEL_ID_SERIES
from handlers.common import DB_SEMAPHORE, safe_call, delete_message_job

async def fav_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    
    async with DB_SEMAPHORE:
        await safe_call(query, "answer")
        favorites = await db.get_user_favorites(user_id)
        
        if not favorites:
            txt = "📭 **Sua lista está vazia!**\nAdicione itens usando o botão '🔖 Salvar' abaixo dos vídeos."
            kb = [[InlineKeyboardButton("⬅️ Voltar", callback_data="back_to_main")]]
            await safe_call(query, "edit_message_text", text=txt, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))
            return

        kb = []
        for fav in favorites:
            icon = '🎬' if fav['media_type'] == 'movie' else '📺'
            kb.append([InlineKeyboardButton(f"{icon} {fav['title'][:30]}", callback_data=f"fav_watch_{fav['unique_code']}")])
        
        kb.append([InlineKeyboardButton("⬅️ Voltar", callback_data="back_to_main")])
        await safe_call(query, "edit_message_text", text=f"🔖 **Minha Lista ({len(favorites)})**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(kb))

async def fav_toggle_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    code = query.data.replace("fav_toggle_", "")
    
    async with DB_SEMAPHORE:
        if await db.is_favorite(user_id, code):
            await db.remove_favorite(user_id, code)
            txt = "🗑️ Removido."
        else:
            # Lógica para salvar
            parts = code.split('_')
            m_type = 'movie' if parts[0] == 'mov' else 'episode'
            m_id = int(parts[1])
            audio = parts[2]
            
            data = {'unique_code': code, 'media_type': m_type, 'title': '?', 'file_id': None, 'message_id': None, 'channel_id': None}
            
            if m_type == 'movie':
                obj = await db.get_movie_by_id(m_id)
                data['channel_id'] = STORAGE_CHANNEL_ID
            else:
                obj = await db.get_full_episode_details(m_id)
                data['channel_id'] = STORAGE_CHANNEL_ID_SERIES
                if obj: 
                    s = obj['seasons']['series']
                    obj['title'] = f"{s['title']} S{obj['seasons']['season_number']}E{obj['episode_number']}"

            if obj:
                data['title'] = obj.get('title')
                data['file_id'] = obj.get(f"{'dubbed' if audio=='dub' else 'subtitled'}_file_id")
                data['message_id'] = obj.get(f"{'dubbed' if audio=='dub' else 'subtitled'}_msg_id")
                
                res = await db.add_favorite(user_id, data)
                if res == 'limit_reached':
                    await safe_call(query, "answer", text="⚠️ Lista cheia (Máx 10).", show_alert=True)
                    return
                txt = "✅ Salvo!"
            else:
                txt = "Erro: Mídia não encontrada."

        # Atualiza botão
        kb = query.message.reply_markup.inline_keyboard
        new_kb = []
        is_fav_now = await db.is_favorite(user_id, code)
        btn_txt = "❌ Remover" if is_fav_now else "🔖 Salvar"
        
        for row in kb:
            new_row = []
            for btn in row:
                if btn.callback_data == query.data:
                    new_row.append(InlineKeyboardButton(btn_txt, callback_data=query.data))
                else:
                    new_row.append(btn)
            new_kb.append(new_row)
            
        await safe_call(query, "answer", text=txt)
        await safe_call(query, "edit_message_reply_markup", reply_markup=InlineKeyboardMarkup(new_kb))

async def fav_watch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    code = query.data.replace("fav_watch_", "")
    
    async with DB_SEMAPHORE:
        fav = await db.get_favorite_item(user_id, code)
        if not fav:
            await safe_call(query, "answer", text="Item removido.", show_alert=True)
            return

        await safe_call(query, "delete_message")
        
        # Recria botões básicos (Remover + Share)
        kb = [[
            InlineKeyboardButton("❌ Remover", callback_data=f"fav_toggle_{code}"),
            InlineKeyboardButton("❤️ Share", switch_inline_query=fav['title'])
        ]]
        
        cap = f"🍿 **{fav['title']}**\n🔖 Da sua lista.\n⚠️ *Apaga em 4h*"
        
        try:
            msg = await context.bot.send_video(user_id, fav['file_id'], caption=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown", protect_content=True)
            context.job_queue.run_once(delete_message_job, 14400, data={'chat_id': user_id, 'message_id': msg.message_id}, name=f"del_fav_{msg.message_id}")
        except BadRequest:
            # Tentativa de cópia se file_id falhar
            if fav['message_id'] and fav['channel_id']:
                try:
                    cp = await context.bot.copy_message(user_id, fav['channel_id'], fav['message_id'], protect_content=True)
                    await context.bot.edit_message_caption(user_id, cp.message_id, caption=cap, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
                    context.job_queue.run_once(delete_message_job, 14400, data={'chat_id': user_id, 'message_id': cp.message_id}, name=f"del_fav_{cp.message_id}")
                except:
                    await context.bot.send_message(user_id, "❌ Erro ao recuperar vídeo.")
