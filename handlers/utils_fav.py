from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest
import asyncio
import database as db
from config import STORAGE_CHANNEL_ID, STORAGE_CHANNEL_ID_SERIES
from handlers.common import (
    DB_SEMAPHORE, safe_call, delete_message_job, get_fav_keyboard_markup, _get_vip_sales_message
)

async def fav_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para 'fav_menu'."""
    query = update.callback_query
    user_id = query.from_user.id
    
    async with DB_SEMAPHORE:
        await safe_call(query, "answer")
        favorites = await db.get_user_favorites(user_id)
        
        if not favorites:
            empty_text = (
                "📭 **Sua lista está vazia! 🗑️**\n\n"
                "Para adicionar itens, navegue pelo bot e clique no botão **'🔖 Salvar na Lista'** "
                "que aparece embaixo dos vídeos."
            )
            keyboard_empty = [[InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]]
        
            await safe_call(query, "edit_message_text", text=empty_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_empty))
            return

        keyboard = []
        for fav in favorites:
            title_display = fav['title'][:30]
            icon = '🎬' if fav['media_type'] == 'movie' else '📺'
            keyboard.append([InlineKeyboardButton(f"{icon} {title_display}", callback_data=f"fav_watch_{fav['unique_code']}")])
        
        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="back_to_main")])
        
        await safe_call(query, "edit_message_text", text=f"🔖 **Minha Lista ({len(favorites)}/10)**\n\nToque para assistir:", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def fav_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para 'fav_toggle_'."""
    query = update.callback_query
    user_id = query.from_user.id
    callback_data = query.data
    
    async with DB_SEMAPHORE:
        unique_code = callback_data.replace("fav_toggle_", "")
        
        if await db.is_favorite(user_id, unique_code):
            await db.remove_favorite(user_id, unique_code)
            msg_text = "🗑️ Removido da lista."
        else:
            parts = unique_code.split('_') # ex: mov_123_dub
            media_type_prefix = parts[0]
            media_id = int(parts[1])
            audio = parts[2]
            
            data_to_save = {
                'unique_code': unique_code,
                'media_type': 'movie' if media_type_prefix == 'mov' else 'episode',
                'title': 'Desconhecido',
                'file_id': None,
                'message_id': None,
                'channel_id': None
            }

            if media_type_prefix == 'mov':
                movie = await db.get_movie_by_id(media_id)
                if movie:
                    data_to_save['title'] = movie['title']
                    if audio == 'dub':
                        data_to_save['file_id'] = movie['dubbed_file_id']
                        data_to_save['message_id'] = movie['dubbed_msg_id']
                    else:
                        data_to_save['file_id'] = movie['subtitled_file_id']
                        data_to_save['message_id'] = movie['subtitled_msg_id']
                    data_to_save['channel_id'] = STORAGE_CHANNEL_ID 
            else:
                ep_details = await db.get_full_episode_details(media_id)
                if ep_details:
                    season = ep_details.get('seasons', {})
                    series = season.get('series', {})
                    s_num = season.get('season_number', 0)
                    e_num = ep_details.get('episode_number', 0)
                    data_to_save['title'] = f"{series.get('title')} S{s_num:02d}E{e_num:02d}"
                    
                    if audio == 'dub':
                        data_to_save['file_id'] = ep_details['dubbed_file_id']
                        data_to_save['message_id'] = ep_details['dubbed_msg_id']
                    else:
                        data_to_save['file_id'] = ep_details['subtitled_file_id']
                        data_to_save['message_id'] = ep_details['subtitled_msg_id']
                    data_to_save['channel_id'] = STORAGE_CHANNEL_ID_SERIES

            if data_to_save['file_id']:
                res = await db.add_favorite(user_id, data_to_save)
                if res == 'limit_reached':
                    await safe_call(query, "answer", text="⚠️ Limite de 10 itens atingido!", show_alert=True)
                    return
                elif res == 'error':
                    await safe_call(query, "answer", text="Erro ao salvar.", show_alert=True)
                    return
                msg_text = "✅ Salvo na lista!"
            else:
                await safe_call(query, "answer", text="Erro: Mídia não encontrada.", show_alert=True)
                return

        # Atualiza o botão visualmente (Toggle)
        current_markup = query.message.reply_markup.inline_keyboard if query.message.reply_markup else None
        new_markup = await get_fav_keyboard_markup(user_id, unique_code, current_markup)
        
        await safe_call(query, "answer", text=msg_text)
        await safe_call(query, "edit_message_reply_markup", reply_markup=new_markup)

async def fav_watch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback para 'fav_watch_'."""
    query = update.callback_query
    user_id = query.from_user.id
    callback_data = query.data
    
    async with DB_SEMAPHORE:
        if not await db.is_user_vip(user_id):
            config = await db.get_bot_config()
            price = config.get('vip_price', 4.99)
            if price > 0:
                # Mostra a tela de venda com botão de PIX direto
                sales_text, _ = await _get_vip_sales_message(context)
                
                keyboard = [
                    [InlineKeyboardButton("✅ Sim, Gerar PIX para Pagar!", callback_data="confirm_pay")],
                    [InlineKeyboardButton("⬅️ Voltar", callback_data="fav_menu")] # Volta pra lista em vez do menu principal
                ]
                
                # Edita a mensagem para mostrar o aviso de VIP
                await safe_call(query, "edit_message_text", 
                    text=f"🔒 <b>Conteúdo Exclusivo VIP<b>\n\n{sales_text}", 
                    parse_mode="HTML", 
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
            
        unique_code = callback_data.replace("fav_watch_", "")
        fav_item = await db.get_favorite_item(user_id, unique_code)
        
        if not fav_item:
            await safe_call(query, "answer", text="Erro: Item não encontrado.", show_alert=True)
            return

        await safe_call(query, "delete_message")
        status_msg = await context.bot.send_message(chat_id=user_id, text=f"🔄 Recuperando **{fav_item['title']}**...")
        
        # =========================================================
        # 🛡️ BLINDAGEM CONTRA DEAD LINKS (Atualizador Dinâmico)
        # =========================================================
        file_id_atualizado = None
        
        try:
            parts = unique_code.split('_')
            tipo = parts[0]      # 'movie' ou 'ep'
            item_id = int(parts[1])
            audio = parts[2] if len(parts) > 2 else 'dub'

            if tipo == 'movie':
                filme = await db.get_movie_by_id(item_id)
                if filme:
                    file_id_atualizado = filme.get(f"{'dubbed' if audio == 'dub' else 'subtitled'}_file_id")
            elif tipo == 'ep':
                ep = await db.get_episode_by_id(item_id)
                if ep:
                    file_id_atualizado = ep.get(f"{'dubbed' if audio == 'dub' else 'subtitled'}_file_id")
        except Exception as e:
            print(f"[FAV] Erro ao buscar ID atualizado: {e}")

        # Se encontrou um ID novo no banco principal, usa ele! Se não, tenta o velho do favorito.
        final_file_id = file_id_atualizado or fav_item['file_id']
        # =========================================================

        # --- CONSTRUÇÃO DOS BOTÕES DE NAVEGAÇÃO ---
        base_keyboard = []
        
        if unique_code.startswith("ep_"):
            try:
                parts = unique_code.split('_')
                episode_id = int(parts[1])
                ep_details = await db.get_full_episode_details(episode_id)
                
                if ep_details:
                    season = ep_details.get('seasons')
                    if season:
                        season_id = season.get('id')
                        current_ep_num = ep_details.get('episode_number', 0)
                        series_id = season.get('series_id')
                        current_season_num = season.get('season_number', 1)

                        prev_ep, next_ep = await asyncio.gather(
                            db.get_neighbor_episode(season_id, current_ep_num, 'previous'),
                            db.get_neighbor_episode(season_id, current_ep_num, 'next')
                        )
                        
                        # Lógica de pular temporada
                        if not next_ep:
                            all_seasons = await db.get_seasons_for_series(series_id)
                            if all_seasons:
                                next_season_obj = next((s for s in all_seasons if s['season_number'] == current_season_num + 1), None)
                                if next_season_obj:
                                    eps_next_season, _ = await db.get_episodes_for_season(next_season_obj['id'], limit=1, offset=0)
                                    if eps_next_season: next_ep = eps_next_season[0]

                        if not prev_ep and current_season_num > 1:
                            all_seasons = await db.get_seasons_for_series(series_id)
                            if all_seasons:
                                prev_season_obj = next((s for s in all_seasons if s['season_number'] == current_season_num - 1), None)
                                if prev_season_obj:
                                    eps_prev, _ = await db.get_episodes_for_season(prev_season_obj['id'], limit=100, offset=0)
                                    if eps_prev: prev_ep = eps_prev[-1]

                        nav_row = []
                        if prev_ep: nav_row.append(InlineKeyboardButton("⏪ Ep. Anterior", callback_data=f"ep_nav_{prev_ep['id']}"))
                        if next_ep:
                            is_new_season = next_ep.get('season_id') != season_id
                            btn_text = "Próxima Temp. ⏩" if is_new_season else "Próximo Ep. ⏩"
                            nav_row.append(InlineKeyboardButton(btn_text, callback_data=f"ep_nav_{next_ep['id']}"))
                        
                        if nav_row: base_keyboard.append(nav_row)
                        base_keyboard.append([
                            InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{series_id}_series"),
                            InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"ep_card:{episode_id}")
                        ])
            except Exception as e:
                print(f"[FAV] Erro nav série: {e}")
        else:
            try:
                parts = unique_code.split('_')
                movie_id = int(parts[1])
                base_keyboard.append([
                    InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{movie_id}"),
                    InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=fav_item['title'])
                ])
            except: pass

        video_markup = await get_fav_keyboard_markup(user_id, unique_code, base_keyboard)
        caption_text = f"🍿 **{fav_item['title']}**\n\n🔖 Recuperado da Minha Lista."

        try:
            # PLANO A: Usando o File ID blindado
            sent_message = await context.bot.send_video(
                chat_id=user_id, video=final_file_id, caption=caption_text,
                parse_mode="Markdown", reply_markup=video_markup, protect_content=True
            )
            
        except BadRequest as e:
            error_text = str(e).lower()
            if ("wrong file id" in error_text) and fav_item['message_id'] and fav_item['channel_id']:
                try:
                    # PLANO B: Cópia
                    copied_message = await context.bot.copy_message(
                        chat_id=user_id, from_chat_id=fav_item['channel_id'], message_id=fav_item['message_id'], protect_content=True
                    )
                    await context.bot.edit_message_caption(
                        chat_id=user_id, message_id=copied_message.message_id, caption=caption_text, parse_mode="Markdown", reply_markup=video_markup
                    )
                except Exception:
                    await status_msg.edit_text("❌ Erro fatal: O arquivo original foi apagado e não foi substituído no Acervo. Remova dos favoritos!")
                    return
            else:
                await status_msg.edit_text("❌ Erro ao enviar vídeo.")
                return

        await status_msg.delete()
        