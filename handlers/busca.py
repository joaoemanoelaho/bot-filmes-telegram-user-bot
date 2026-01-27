from telegram import (
    Update, InlineQueryResultArticle, InlineQueryResultPhoto, 
    InputTextMessageContent, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import ContextTypes
from telegram.error import NetworkError
import asyncio
import database as db
from handlers.common import DB_SEMAPHORE, safe_call

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    results = []
    cache_time = 0 # Cache 0 para garantir atualização nos testes
    is_personal = True
    
    try:
        async with DB_SEMAPHORE:
            query_text = update.inline_query.query
            bot_username = context.bot.username
            
            # Lê o offset atual
            current_offset = int(update.inline_query.offset) if update.inline_query.offset else 0

            # =========================================================
            # ROTA 0: COMPARTILHAMENTO DE EPISÓDIO (EP_CARD)
            # =========================================================
            if query_text.startswith("ep_card:"):
                try:
                    episode_id = int(query_text.split(':')[1])
                    details = await db.get_full_episode_details(episode_id)

                    if details:
                        episode = details
                        season = details.get('seasons')
                        series = await db.get_series_by_id(season['series_id']) if season else None

                        if season and series:
                            # 1. ADICIONA O "ABRE-ALAS" (AJUDA) PRIMEIRO
                            # Isso força o Telegram a abrir a lista corretamente
                            results.append(
                                InlineQueryResultArticle(
                                    id="help_header_ep", # ID único
                                    title="Confirme o envio 👇",
                                    description="Clique na imagem abaixo para compartilhar o episódio.",
                                    thumbnail_url="https://cdn-icons-png.flaticon.com/512/3931/3931294.png",
                                    input_message_content=InputTextMessageContent(
                                        f"Para compartilhar, clique na imagem do episódio na lista! 👇"
                                    )
                                )
                            )

                            # 2. PREPARA OS DADOS DO EPISÓDIO
                            series_title = series.get('title', 'Série')
                            ep_number = episode.get('episode_number', 0)
                            season_number = season.get('season_number', 0)
                            ep_title = episode.get('title', f"Episódio {ep_number}")
                            
                            # Tratamento de Pôster Blindado
                            poster = series.get('poster_url')
                            if not poster: 
                                poster = 'https://via.placeholder.com/500x750.png?text=Sem+Pôster'
                            
                            poster_url_grande = poster
                            poster_url_pequeno = poster.replace('/w500/', '/w92/') if '/w500/' in poster else poster
                            
                            desc_text = f"S{int(season_number):02d}E{int(ep_number):02d} - {ep_title}"
                            
                            photo_caption = (
                                f"📽️ *{series_title}*\n"
                                f"🎬 *Temporada:* {season_number}\n"
                                f"🎯 *Episódio:* {ep_number}"
                            )
                            
                            watch_url = f"https://t.me/{bot_username}?start=show_ep_{episode_id}"
                            keyboard = [[InlineKeyboardButton("Assistir - ⏩", url=watch_url)]]
                            reply_markup = InlineKeyboardMarkup(keyboard)

                            # 3. ADICIONA O CARD DO EPISÓDIO (SEGUNDO ITEM)
                            results.append(
                                InlineQueryResultPhoto(
                                    id=f"share_ep_v3_{episode_id}", # v3 para limpar cache
                                    title=f"SÉRIE: {series_title}",
                                    description=desc_text,
                                    photo_url=poster_url_grande,
                                    thumbnail_url=poster_url_pequeno,
                                    caption=photo_caption,
                                    parse_mode="Markdown",
                                    reply_markup=reply_markup
                                )
                            )
                            is_personal = True 
                            cache_time = 0
                except Exception as e:
                    print(f"Erro ao gerar ep_card: {e}")
                
                # Envia e sai
                await safe_call(update.inline_query, "answer", results, cache_time=cache_time, is_personal=is_personal, next_offset=None)
                return

            # =========================================================
            # ROTA 1: BUSCA DE EPISÓDIOS (PAGINAÇÃO)
            # =========================================================
            season_id = None
            if query_text.startswith("season:"):
                parts = query_text.split(':')
                try:
                    season_id = int(parts[1])
                    if len(parts) > 2:
                        current_offset = int(parts[2])
                except (IndexError, ValueError):
                    season_id = None

            if season_id:
                try:
                    user_id = update.inline_query.from_user.id
                    await db.get_or_create_user(user_id=user_id, first_name=update.inline_query.from_user.first_name)
                    is_vip = await db.is_user_vip(user_id)
                    page_limit = 48

                    episodes, season = await db.get_episodes_for_season(
                        season_id, limit=page_limit, offset=current_offset
                    )

                    config = await db.get_bot_config()
                    price = config.get('vip_price', 4.99)
                    is_free = price <= 0

                    if not is_vip and not is_free:
                        results.append(
                            InlineQueryResultArticle(
                                id="vip_required_series",
                                title="🍿 Acesso Premium Necessário!",
                                description="Clique aqui para liberar todas as séries do catálogo.",
                                thumbnail_url="https://i.imgur.com/L3Ew4wt.png",
                                reply_markup=InlineKeyboardMarkup([[
                                        InlineKeyboardButton(
                                            "Quero meu Acesso Premium! 🚀",
                                            url=f"https://t.me/{bot_username}?start=vip"
                                        )
                                    ]]),
                                input_message_content=InputTextMessageContent(
                                    message_text=(f"Ei {update.inline_query.from_user.first_name}! 👋\n\nPara maratonar esta e **todas as outras séries**, você precisa do 🍿 **Acesso Pipoca Premium**!"),
                                    parse_mode="Markdown",
                                )
                            )
                        )
                        cache_time = 5
                        is_personal = True

                    elif not episodes and current_offset == 0:
                        results.append(InlineQueryResultArticle(
                            id="no_eps_found",
                            title="Nenhum episódio encontrado",
                            description="Esta temporada parece não ter episódios cadastrados.",
                            input_message_content=InputTextMessageContent("Nenhum episódio encontrado.")
                        ))
                        cache_time = 10

                    else:
                        series = await db.get_series_by_id(season['series_id'])
                        series_title = series.get('title', 'Série') if series else 'Série'
                        
                        poster = series.get('poster_url')
                        if not poster: poster = 'https://via.placeholder.com/500x750.png?text=Sem+Pôster'
                        poster_url_pequeno = poster.replace('/w500/', '/w92/') if '/w500/' in poster else poster

                        for i, ep in enumerate(episodes):
                            ep_title = ep.get('title', f"Episódio {ep['episode_number']}")
                            message_text = (
                                f"📽️ *{series_title}*\n"
                                f"🎬 *Temporada:* {season['season_number']}\n"
                                f"🎯 *Episódio:* {ep['episode_number']} - {ep_title}\n"
                                f"--------------------\n"
                                f"Selecione o áudio (o bot irá te chamar no privado):"
                            )
                            audio_row = []
                            if ep.get('dubbed_file_id'):
                                payload = f"watch_ep_{ep['id']}_dub"
                                url = f"https://t.me/{bot_username}?start={payload}"
                                audio_row.append(InlineKeyboardButton("Dub 🇧🇷", url=url))
                            if ep.get('subtitled_file_id'):
                                payload = f"watch_ep_{ep['id']}_sub"
                                url = f"https://t.me/{bot_username}?start={payload}"
                                audio_row.append(InlineKeyboardButton("Leg 🇺🇸", url=url))

                            if audio_row:
                                results.append(
                                    InlineQueryResultArticle(
                                        id=f"ep_{ep['id']}",
                                        title=f"Episódio : {ep['episode_number']}",
                                        description=f"🎬 {series_title} | {ep_title}",
                                        thumbnail_url=poster_url_pequeno,
                                        reply_markup=InlineKeyboardMarkup([audio_row]),
                                        input_message_content=InputTextMessageContent(
                                            message_text=message_text,
                                            parse_mode="Markdown"
                                        )
                                    )
                                )

                        # Botões de Paginação
                        nav_buttons = []
                        if current_offset > 0:
                            prev_offset = max(0, current_offset - page_limit)
                            nav_buttons.append(
                                InlineQueryResultArticle(
                                    id=f"page_prev_{prev_offset}", title="⬅️ Página Anterior", description=f"Voltar para Eps {prev_offset + 1}-{current_offset}",
                                    thumbnail_url="https://i.imgur.com/b6PZt7H.png",
                                    input_message_content=InputTextMessageContent(f"Carregando página anterior..."),
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Clique para carregar ⬅️", switch_inline_query_current_chat=f"season:{season_id}:{prev_offset}")]])
                                )
                            )

                        if len(episodes) == page_limit:
                            next_offset_manual = current_offset + page_limit
                            nav_buttons.append(
                                InlineQueryResultArticle(
                                    id=f"page_next_{next_offset_manual}", title="Próxima Página ➡️", description=f"Carregar Eps {next_offset_manual + 1}-{next_offset_manual + page_limit}",
                                    thumbnail_url="https://i.imgur.com/FwOxDqO.png",
                                    input_message_content=InputTextMessageContent(f"Carregando próxima página..."),
                                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Clique para carregar ➡️", switch_inline_query_current_chat=f"season:{season_id}:{next_offset_manual}")]])
                                )
                            )
                        results.extend(nav_buttons)
                        cache_time = 10
                        is_personal = True

                except Exception as e:
                    print(f"❌ Erro na busca inline de episódios: {e}")
                    results = [InlineQueryResultArticle(id="error_eps", title="Erro ao buscar episódios", input_message_content=InputTextMessageContent("Ocorreu um erro ao processar sua solicitação."))]

            # =========================================================
            # ROTA 2: BUSCA NORMAL (Filme/Série)
            # =========================================================
            elif not query_text:
                results = [
                    InlineQueryResultArticle(
                        id="help_bubble", title="Digite o nome do Filme ou Série", description="Comece a digitar para que os resultados da busca apareçam aqui.",
                        thumbnail_url="https://cdn-icons-png.flaticon.com/512/3931/3931294.png", input_message_content=InputTextMessageContent("👍")
                    )
                ]
                is_personal = True
                cache_time = 5

            else:
                # AQUI ESTÁ O AJUDA QUE VOCÊ USA NA BUSCA NORMAL
                # Replicamos essa lógica lá em cima para o EP_CARD
                results.append(
                    InlineQueryResultArticle(
                        id="static_help", title="Ajuda", description="Como usar o bot de busca", thumbnail_url="https://cdn-icons-png.flaticon.com/512/189/189665.png",
                        input_message_content=InputTextMessageContent(f"Para buscar, digite @{context.bot.username} e o nome do filme.\n\nPara ver o menu principal, envie o comando /start.")
                    )
                )

                movies_from_db = await db.search_movies(query_text, limit=5)
                series_from_db = await db.search_series_by_title(query_text, limit=5)

                for movie in movies_from_db:
                    poster = movie.get('poster_url')
                    if not poster: poster = 'https://via.placeholder.com/500x750.png?text=Sem+Pôster'
                    
                    watch_url = f"https://t.me/{bot_username}?start=watch_{movie['movie_id']}"
                    keyboard = [[InlineKeyboardButton("Assistir ⏯️", url=watch_url)], [InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=movie['title'])]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    photo_caption = (f"🎬 *{movie['title']}* ({movie['year']})\n🎭 *Gênero:* {movie.get('genre', 'N/A')}")
                    
                    poster_url_pequeno = poster.replace('/w500/', '/w92/') if '/w500/' in poster else poster
                    results.append(
                        InlineQueryResultPhoto(
                            id=f"movie_{movie['movie_id']}", title=f"FILME: {movie['title']}", description=f"{movie['year']} - {movie.get('genre', 'N/A')}",
                            photo_url=poster, thumbnail_url=poster_url_pequeno, caption=photo_caption, parse_mode="Markdown", reply_markup=reply_markup
                        )
                    )

                for series in series_from_db:
                    poster = series.get('poster_url')
                    if not poster: poster = 'https://via.placeholder.com/500x750.png?text=Sem+Pôster'
                    poster_url_pequeno = poster.replace('/w500/', '/w92/') if '/w500/' in poster else poster
                    
                    seasons = await db.get_seasons_for_series(series['series_id'])
                    photo_caption = (f"📺 *{series['title']}*\n\n🗓️ *Ano:* {series['year']}\n🎭 *Gênero:* {series.get('genre', 'N/A')}\n\n📝 *Sinopse:* {series.get('description', 'N/A')}\n\n---\nSelecione a temporada desejada abaixo:")
                    keyboard = []
                    if seasons:
                        for season in seasons:
                            keyboard.append([InlineKeyboardButton(f"▶️ Temporada {season['season_number']}", switch_inline_query_current_chat=f"season:{season['id']}:0")])
                    keyboard.append([InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=series['title'])])
                    
                    results.append(
                        InlineQueryResultPhoto(
                            id=f"series_{series['series_id']}", title=f"SÉRIE: {series['title']}", description=f"{series['year']} - {series.get('genre', 'Série')}",
                            photo_url=poster, thumbnail_url=poster_url_pequeno, caption=photo_caption, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                    )
                cache_time = 30

    except Exception as e:
        print(f"Erro ao processar lógica inline: {e}")
        return

    max_retries = 3
    for attempt in range(max_retries):
        try:
            await safe_call(update.inline_query, "answer", results, cache_time=cache_time, is_personal=is_personal, next_offset=None)
            break
        except NetworkError as e:
            if attempt + 1 == max_retries: return
            await asyncio.sleep(1)
            