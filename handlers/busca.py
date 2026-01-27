from telegram import Update, InlineQueryResultArticle, InlineQueryResultPhoto, InputTextMessageContent, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import NetworkError
import asyncio
import database as db
from handlers.common import DB_SEMAPHORE, safe_call

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    user = update.inline_query.from_user
    offset = int(update.inline_query.offset) if update.inline_query.offset else 0
    results = []
    
    async with DB_SEMAPHORE:
        # ROTA 1: Card de Compartilhamento (ep_card:ID)
        if query.startswith("ep_card:"):
            try:
                ep_id = int(query.split(':')[1])
                dt = await db.get_full_episode_details(ep_id)
                if dt:
                    s = dt['seasons']['series']
                    cap = f"📽️ *{s['title']}*\nS{dt['seasons']['season_number']:02d}E{dt['episode_number']:02d}"
                    url = f"https://t.me/{context.bot.username}?start=show_ep_{ep_id}"
                    
                    results.append(InlineQueryResultPhoto(
                        id=f"share_{ep_id}", title=s['title'], photo_url=s['poster_url'], thumbnail_url=s['poster_url'],
                        caption=cap, parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Assistir ⏯️", url=url)]])
                    ))
                    await safe_call(update.inline_query, "answer", results, cache_time=10, is_personal=True)
                    return
            except: pass

        # ROTA 2: Temporadas (season:ID:OFFSET)
        season_id = None
        if query.startswith("season:"):
            try:
                parts = query.split(':')
                season_id = int(parts[1])
                if len(parts) > 2: offset = int(parts[2]) # Offset manual
            except: pass

        if season_id:
            await db.get_or_create_user(user.id, user.first_name)
            is_vip = await db.is_user_vip(user.id)
            config = await db.get_bot_config()
            
            # Bloqueio VIP na busca
            if not is_vip and config.get('vip_price', 0) > 0:
                results.append(InlineQueryResultArticle(
                    id="vip_lock", title="🔒 Acesso Premium Necessário",
                    description="Clique para desbloquear as séries.",
                    thumbnail_url="https://i.imgur.com/L3Ew4wt.png",
                    input_message_content=InputTextMessageContent("🔐 *Conteúdo Exclusivo VIP*", parse_mode="Markdown"),
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Virar VIP 🚀", url=f"https://t.me/{context.bot.username}?start=vip")]])
                ))
            else:
                eps, season = await db.get_episodes_for_season(season_id, limit=48, offset=offset)
                if eps:
                    series = await db.get_series_by_id(season['series_id'])
                    thumb = series['poster_url'].replace('/w500/', '/w92/')
                    
                    for ep in eps:
                        title = f"E{ep['episode_number']} - {ep['title']}"
                        desc = f"S{season['season_number']} | {series['title']}"
                        
                        # Monta botões de áudio
                        row = []
                        if ep.get('dubbed_file_id'):
                            url = f"https://t.me/{context.bot.username}?start=watch_ep_{ep['id']}_dub"
                            row.append(InlineKeyboardButton("Dub 🇧🇷", url=url))
                        if ep.get('subtitled_file_id'):
                            url = f"https://t.me/{context.bot.username}?start=watch_ep_{ep['id']}_sub"
                            row.append(InlineKeyboardButton("Leg 🇺🇸", url=url))
                        
                        if row:
                            results.append(InlineQueryResultArticle(
                                id=f"ep_{ep['id']}", title=title, description=desc, thumbnail_url=thumb,
                                input_message_content=InputTextMessageContent(f"📺 *{series['title']}*\n{title}\n\nSelecione o áudio:", parse_mode="Markdown"),
                                reply_markup=InlineKeyboardMarkup([row])
                            ))
                    
                    # Paginação Manual
                    if offset > 0:
                        prev_off = max(0, offset - 48)
                        results.append(InlineQueryResultArticle(
                            id=f"prev_{prev_off}", title="⬅️ Página Anterior",
                            input_message_content=InputTextMessageContent("Carregando..."),
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Carregar", switch_inline_query_current_chat=f"season:{season_id}:{prev_off}")]])
                        ))
                    if len(eps) == 48:
                        next_off = offset + 48
                        results.append(InlineQueryResultArticle(
                            id=f"next_{next_off}", title="Próxima Página ➡️",
                            input_message_content=InputTextMessageContent("Carregando..."),
                            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Carregar", switch_inline_query_current_chat=f"season:{season_id}:{next_off}")]])
                        ))

            await safe_call(update.inline_query, "answer", results, cache_time=10, is_personal=True, next_offset=None)
            return

        # ROTA 3: Busca Geral (Filme/Série)
        if not query:
            # Placeholder vazio
            results.append(InlineQueryResultArticle(id="typ", title="Digite o nome...", input_message_content=InputTextMessageContent("🔎")))
        else:
            movies = await db.search_movies(query, limit=5)
            series = await db.search_series_by_title(query, limit=5)
            
            for m in movies:
                url = f"https://t.me/{context.bot.username}?start=watch_{m['movie_id']}"
                kb = [[InlineKeyboardButton("Assistir ⏯️", url=url)]]
                thumb = m['poster_url'].replace('/w500/','/w92/')
                results.append(InlineQueryResultPhoto(
                    id=f"mov_{m['movie_id']}", photo_url=m['poster_url'], thumbnail_url=thumb,
                    title=m['title'], description=str(m['year']),
                    caption=f"🎬 *{m['title']}*\n{m.get('genre','')}", parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(kb)
                ))
            
            for s in series:
                seasons = await db.get_seasons_for_series(s['series_id'])
                kb = []
                for sea in seasons:
                    kb.append([InlineKeyboardButton(f"Temporada {sea['season_number']}", switch_inline_query_current_chat=f"season:{sea['id']}:0")])
                
                thumb = s['poster_url'].replace('/w500/','/w92/')
                results.append(InlineQueryResultPhoto(
                    id=f"ser_{s['series_id']}", photo_url=s['poster_url'], thumbnail_url=thumb,
                    title=s['title'], caption=f"📺 *{s['title']}*\nEscolha a temporada:", parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(kb)
                ))

        await safe_call(update.inline_query, "answer", results, cache_time=30, is_personal=False)
