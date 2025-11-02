#
# NOME DO ARQUIVO: handlers_user.py (VERSÃO 3.0 - FLUXO HÍBRIDO CORRETO)
#
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    InlineQueryResultArticle, InputTextMessageContent, 
    InlineQueryResultPhoto, InputMediaPhoto
)
# (Não precisamos de CachedVideo no inline_handler)
from telegram.ext import CommandHandler, ContextTypes, CallbackQueryHandler, InlineQueryHandler, MessageHandler, filters
import database as db
from config import ADMIN_IDS, STORAGE_CHANNEL_ID
import payments
import base64
import io
import time
import re
import os
import sys
from thefuzz import fuzz
from starlette.requests import Request
from starlette.responses import Response
import uuid
import tastedive_api

    
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

# =================================================================
# === HELPERS REMOVIDOS ===
# As funções 'show_series_details_handler', 'send_season_details'
# e 'send_episode_options' NÃO SÃO MAIS NECESSÁRIAS.
# O 'inline_query_handler' vai fazer 90% do trabalho.
# =================================================================

# =================================================================
# === HANDLERS EXISTENTES (ATUALIZADOS) ===
# =================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (v3.0) Função /start simplificada.
    Removemos o deep link de 'series_', pois não é mais usado.
    """
    is_query = update.callback_query is not None
    
    if is_query:
        user = update.callback_query.from_user
        message_to_reply = update.callback_query.message
    else:
        user = update.effective_user
        message_to_reply = update.message
        
    if context.args:
        payload = context.args[0]
        # --- Roteador de Deep Link (APENAS FILMES) ---
        if payload.startswith("watch_"):
            movie_id = payload.split('_')[1]
            context.args = [movie_id]
            return await watch_command_handler(update, context) # Chama o handler de filmes
        
        # O 'elif payload.startswith("series_")' FOI REMOVIDO.

    db.get_or_create_user(user_id=user.id, first_name=user.first_name)
    
    keyboard = [
        [
            InlineKeyboardButton("Buscar Mídia 🔎", switch_inline_query_current_chat=""),
        ],
        [
            InlineKeyboardButton("Adquirir VIP 🚀", callback_data="main_vip")
        ],
        [
            InlineKeyboardButton("Pedir Filme/Série 💡", callback_data="main_request"),
            InlineKeyboardButton("Top Mídia 🏆", callback_data="main_top")
        ]
    ]
    
    main_menu = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        f"Olá {user.mention_html()}! 👋\n\n"
        "Gosta de maratonar? Esse bot é perfeito para isso 😉.\n\n"
        "Clique no botão \"Buscar Mídia 🔎\" para começar.\n\n"
        "Ficou com dúvidas? Envie o comando /help"
    )
    
    if is_query:
        try:
            # Lógica para editar texto ou foto
            if message_to_reply.photo:
                 await message_to_reply.edit_caption(
                    caption=welcome_text, 
                    reply_markup=main_menu, 
                    parse_mode='HTML'
                )
            else:
                await message_to_reply.edit_text(
                    welcome_text, 
                    reply_markup=main_menu, 
                    parse_mode='HTML'
                )
        except Exception as e:
            print(f"Erro ao editar de volta ao menu: {e}.")
            await context.bot.send_message(
                chat_id=user.id, 
                text=welcome_text, 
                reply_markup=main_menu, 
                parse_mode='HTML'
            )
            if message_to_reply:
                await message_to_reply.delete()
    else:
        await message_to_reply.reply_html(welcome_text, reply_markup=main_menu)


async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código original - sem mudanças)
    context.user_data['state'] = 'awaiting_request'
    await update.message.reply_text(
        "Qual filme ou série você gostaria de ver no catálogo?\n\n"
        "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (v3.0) Processa cliques em botões.
    LOGICA DE SÉRIES FOI REMOVIDA DAQUI, exceto o 'series_send_'
    """
    query = update.callback_query
    callback_data = query.data
    user_id = query.from_user.id
    print(f"Usuário {user_id} clicou no botão: {callback_data}")

    # --- LÓGICA PARA ENVIAR O FILME (Sem mudança) ---
    if callback_data.startswith("play_"):
        await query.answer()
        _, movie_id_str, audio_choice = callback_data.split('_')
        movie_id = int(movie_id_str)
        await query.edit_message_caption(caption="⏳ Carregando seu filme, por favor aguarde...")
        movie = db.get_movie_by_id(movie_id)
        if not movie:
            await query.edit_message_caption(caption="Erro: Filme não encontrado.")
            return
        file_id_to_send = movie.get('dubbed_file_id') if audio_choice == "dub" else movie.get('subtitled_file_id')
        if file_id_to_send:
            await query.delete_message()
            bot_username = context.bot.username
            video_caption = (
                f"🎬 *{movie['title']}* ({movie['year']})\n\n"
                f"🎭 *Gênero:* {movie['genre']}\n\n"
                f"---\n"
                f"🍿 Assistido com @{bot_username}"
            )
            keyboard = [[InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"{movie['title']}"), InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{movie_id}")]]
            video_reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_video(
                chat_id=query.message.chat.id,
                video=file_id_to_send,
                caption=video_caption,
                parse_mode="Markdown",
                reply_markup=video_reply_markup,
                protect_content=True
            )
            db.log_movie_view(movie_id=movie_id, user_id=user_id)
        else:
            await query.edit_message_caption(caption="😔 Desculpe, esta versão do filme não está disponível.")

    # --- LÓGICA DE RELACIONADOS (v2.2 - Já está correta) ---
    elif callback_data.startswith("related_"):
        await query.answer()
        
        parts = callback_data.split('_')
        media_id = int(parts[1])
        media_type = 'movie' 
        
        if len(parts) > 2 and parts[2] == 'series':
            media_type = 'series'
        
        title_to_search = None
        
        if media_type == 'movie':
            media_obj = db.get_movie_by_id(media_id)
            if media_obj:
                title_to_search = media_obj['title']
        else: 
            media_obj = db.get_series_by_id(media_id)
            if media_obj:
                title_to_search = media_obj['title']

        if not title_to_search:
            await context.bot.send_message(chat_id=user_id, text="Não consegui encontrar a mídia original.")
            return
        # (Resto do seu código de 'related_' está correto)
        status_msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ Buscando mídias relacionadas a '{title_to_search}'...")
        recommendations_from_api = tastedive_api.get_recommendations(title_to_search)
        if recommendations_from_api:
            existing_recommendations = db.filter_existing_titles(recommendations_from_api)
        else:
            existing_recommendations = []
        if not existing_recommendations:
            await status_msg.edit_text("Não encontrei nenhuma recomendação que já esteja em nosso catálogo.")
            return
        keyboard = []
        for title in existing_recommendations:
            keyboard.append([InlineKeyboardButton(f"🔎 {title}", switch_inline_query_current_chat=title)])
        message_text = f"Se você gostou de '{title_to_search}', talvez também goste destes:\n\nClique em um título para buscar:"
        await status_msg.edit_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard))


    # --- LÓGICA DE PEDIDO (Sem mudança) ---
    elif callback_data == "main_request":
        # (Seu código original)
        await query.answer()
        context.user_data['state'] = 'awaiting_request'
        await query.edit_message_text(
            text="Qual filme ou série você gostaria de ver no catálogo?\n\n"
                 "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
        )
    
    # --- LÓGICA TOP FILMES (Sem mudança) ---
    elif callback_data == "main_top":
        # (Seu código original)
        await query.answer()
        keyboard = [
            [InlineKeyboardButton("🏆 Top Semana", callback_data="top_7")],
            [InlineKeyboardButton("🗓️ Top Mês", callback_data="top_30")],
            [InlineKeyboardButton("🌎 Top Geral", callback_data="top_0")],
            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Selecione o período do ranking que deseja visualizar:", reply_markup=reply_markup)
    elif callback_data.startswith("top_"):
        # (Seu código original)
        await query.answer()
        period_days = int(callback_data.split('_')[1])
        period_text = "Geral (Todos os Tempos)"
        if period_days == 7: period_text = "da Semana"
        if period_days == 30: period_text = "do Mês"
        await query.edit_message_text(f"🏆 Buscando o Top 10 {period_text}, aguarde...")
        trending_movies = db.get_trending(period_days=period_days)
        if not trending_movies:
            await query.edit_message_text("Ainda não há dados suficientes para gerar um ranking.")
            return
        keyboard = []
        for movie in trending_movies:
            button = [InlineKeyboardButton(f"{movie['title']} ({movie['year']})", callback_data=f"show_card_{movie['movie_id']}")]
            keyboard.append(button)
        keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="main_top")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = f"🏆 **Top 10 {period_text}** 🏆\n\nClique em um filme abaixo para ver mais detalhes:"
        await query.edit_message_text(message_text, parse_mode="Markdown", reply_markup=reply_markup)
    elif callback_data.startswith("show_card_"):
        # (Seu código original)
        await query.answer()
        movie_id = int(callback_data.split('_')[2])
        movie = db.get_movie_by_id(movie_id)
        if not movie:
            await query.edit_message_text("Desculpe, este filme não foi encontrado.")
            return
        await query.delete_message()
        bot_username = context.bot.username
        watch_url = f"https://t.me/{bot_username}?start=watch_{movie['movie_id']}"
        keyboard = [[
            InlineKeyboardButton("Assistir ⏯️", url=watch_url),
            InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=movie['title'])
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        invisible_char = "\u200b"
        card_text_content = (
            f"[{invisible_char}]({movie['poster_url']})"
            f"🎬 *{movie['title']}* ({movie['year']})\n"
            f"🎭 *Gênero:* {movie['genre']}"
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=card_text_content,
            parse_mode="Markdown",
            reply_markup=reply_markup,
            disable_web_page_preview=False
        )

    # --- LÓGICA DE VOLTAR AO MENU (Sem mudança) ---
    elif callback_data == "back_to_main":
        await query.answer()
        await start(update, context)
        
    # --- LÓGICA DE PAGAMENTO VIP (Sem mudança) ---
    # (Todo o seu código 'main_vip' e 'check_payment_' vai aqui...)
    elif callback_data == "main_vip":
        # (Seu código original)
        await query.answer()
        if db.is_user_vip(user_id):
            await query.edit_message_text("✨ Você já é um membro VIP! Aproveite o catálogo.")
            return
        user_details = db.get_user_details(user_id)
        active_payment_id = user_details.get('active_payment_id') if user_details else None
        if active_payment_id:
            await query.edit_message_text("⏳ Verificando seu pagamento anterior, aguarde...")
            status = payments.check_payment_status(active_payment_id)
            if status == 'created':
                await query.edit_message_text("Você já possui uma cobrança PIX pendente. Por favor, realize o pagamento ou aguarde expirar.")
                return 
        await query.edit_message_text("⏳ Gerando sua cobrança PIX, aguarde...")
        vip_price = 2.00 # (Você pode mover isso para o config.py)
        payment_data = payments.create_pix_payment(user_id=user_id, amount=vip_price)
        if payment_data and payment_data.get("qr_code_base64"):
            payment_id = payment_data['payment_id']
            db.set_user_active_payment_id(user_id, payment_id)
            base64_string = payment_data['qr_code_base64']
            if ',' in base64_string:
                base64_string = base64_string.split(',')[1]
            qr_image_data = base64.b64decode(base64_string)
            qr_image_file = io.BytesIO(qr_image_data)
            pix_code = payment_data['qr_code_text']
            caption = (
                f"✨ **Seu Acesso VIP está quase pronto!** ✨\n\n"
                f"Para concluir, faça o pagamento de R${vip_price:.2f} via PIX.\n\n"
                f"**1.** Escaneie o QR Code acima.\n"
                f"**2.** Ou use o PIX Copia e Cola abaixo:\n"
                f"`{pix_code}`\n\n"
                "Após pagar, clique no botão 'Já Paguei' para verificar.\n\n"
                "⚠️ *Este código expira em alguns minutos.*"
            )
            keyboard = [[InlineKeyboardButton("✅ Já Paguei", callback_data=f"check_payment_{payment_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.delete_message()
            await context.bot.send_photo(
                chat_id=user_id, photo=qr_image_file, caption=caption,
                parse_mode="Markdown", reply_markup=reply_markup
            )
        else:
            await query.edit_message_text("😕 Desculpe, não foi possível gerar a cobrança PIX. Tente novamente mais tarde.")

    elif callback_data.startswith("check_payment_"):
        # (Seu código original)
        payment_id = callback_data.split('_')[2]
        now = time.time()
        last_check = context.user_data.get('last_payment_check', 0)
        if now - last_check < 60:
            await query.answer(
                text=f"✋ Por favor, aguarde {int(60 - (now - last_check))} segundos antes de verificar novamente.",
                show_alert=True
            )
            return
        context.user_data['last_payment_check'] = now
        status = payments.check_payment_status(payment_id)
        if status == 'paid':
            await query.answer()
            db.set_user_as_vip(user_id, duration_days=30)
            db.clear_user_active_payment_id(user_id)
            await query.message.delete()
            await context.bot.send_message(
                chat_id=user_id,
                text="🎉 **Pagamento confirmado!** 🎉\n\n"
                     "Você agora é um membro VIP! Aproveite todo o nosso catálogo.",
                parse_mode="Markdown"
            )
        else:
            await query.answer(
                text=" Pagamento ainda não confirmado.\n\nA confirmação pode levar alguns instantes.",
                show_alert=True
            )
            
    #
    # --- MUDANÇA: 'series_season_', 'series_episode_', 'series_view_' FORAM REMOVIDOS ---
    #
        
    # === LÓGICA FINAL (v3.0) - Enviar o vídeo da série ===
    elif callback_data.startswith("series_send_"):
        try:
            await query.answer() 
        except Exception as e:
            print(f"Ignorando erro de timeout no query.answer(): {e}")

        parts = callback_data.split('_')
        episode_id = int(parts[2])
        audio_type = parts[3]
        
        # Pega o episódio
        episode = db.get_episode_by_id(episode_id)
        if not episode:
            print(f"Erro: Episódio {episode_id} não encontrado no DB.")
            return

        # --- INÍCIO DA CORREÇÃO ---
        # A função 'get_episodes_for_season' retorna uma TUPLA: (lista_de_eps, dados_da_temporada)
        # Precisamos desempacotar ela corretamente:
        
        lista_de_eps, dados_da_temporada = db.get_episodes_for_season(episode['season_id'])
        
        # Agora usamos 'dados_da_temporada' (que é um dicionário)
        series = db.get_series_by_id(dados_da_temporada['series_id'])
        series_title = series.get('title', 'Série')
        season_number = dados_da_temporada.get('season_number', 0) # Pega o número da temporada
        # --- FIM DA CORREÇÃO ---

        file_id_to_send = None
        audio_text = "N/A"
        if audio_type == 'dub' and episode.get('dubbed_file_id'):
            file_id_to_send = episode['dubbed_file_id']
            audio_text = "(Dublado)"
        elif audio_type == 'sub' and episode.get('subtitled_file_id'):
            file_id_to_send = episode['subtitled_file_id']
            audio_text = "(Legendado)"
        
        if file_id_to_send:
            bot_username = context.bot.username
            
            # (Ajuste na legenda para usar a variável correta 'season_number')
            video_caption = (
                f"📺 *{series_title}*\n"
                f"S{season_number:02d}E{episode.get('episode_number', 0):02d}: *{episode.get('title', 'Episódio')}* {audio_text}\n\n"
                f"---\n"
                f"🍿 Assistido com @{bot_username}"
            )
            
            keyboard = [[
                InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=series_title), 
                InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{series['id']}_series")
            ]]
            video_reply_markup = InlineKeyboardMarkup(keyboard)
            
            await context.bot.send_video(
                chat_id=query.from_user.id, 
                video=file_id_to_send,
                caption=video_caption,
                parse_mode="Markdown",
                reply_markup=video_reply_markup,
                protect_content=True
            )
        else:
             await context.bot.send_message(
                chat_id=query.from_user.id,
                text="😔 Desculpe, esta versão do áudio não está disponível."
            )
    # --- FIM DA LÓGICA DE SÉRIES ---


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (v3.0) Lida com as buscas em modo inline (Híbrido).
    """
    query_text = update.inline_query.query
    results = []
    
    #
    # === ROTA 1: BUSCA DE EPISÓDIOS (ex: "season:1") ===
    #
    if query_text.startswith("season:"):
        try:
            season_id = int(query_text.split(':')[1])
            
            episodes, season = db.get_episodes_for_season(season_id)
            
            if not episodes:
                results.append(InlineQueryResultArticle(
                    id="no_eps_found",
                    title="Nenhum episódio encontrado",
                    description="Esta temporada parece não ter episódios cadastrados.",
                    input_message_content=InputTextMessageContent("Nenhum episódio encontrado.")
                ))
                await update.inline_query.answer(results, cache_time=10)
                return

            # Buscar a série para pegar o nome
            series = db.get_series_by_id(season['series_id'])
            series_title = series.get('title', 'Série') if series else 'Série'

            # Criar os resultados (Artigos)
            for ep in episodes:
                ep_title = ep.get('title', f"Episódio {ep['episode_number']}")
                
                # --- A MENSAGEM DE ÁUDIO (imagem 2666a4.png) ---
                # 1. A Legenda da mensagem
                message_text = (
                    f"📽️ *{series_title}*\n"
                    f"🎬 *Temporada:* {season['season_number']}\n"
                    f"🎯 *Episódio:* {ep['episode_number']} - {ep_title}\n"
                    f"--------------------\n"
                    f"Selecione o áudio:"
                )
                
                # 2. Os Botões de áudio (Dub/Leg)
                keyboard = []
                options_row = []
                if ep.get('dubbed_file_id'):
                    options_row.append(
                        InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"series_send_{ep['id']}_dub")
                    )
                if ep.get('subtitled_file_id'):
                    options_row.append(
                        InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"series_send_{ep['id']}_sub")
                    )
                
                # 3. Criar o resultado (Artigo)
                if options_row: # Só mostra o episódio se tiver Dub ou Leg
                    keyboard.append(options_row)
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    results.append(
                        InlineQueryResultArticle(
                            id=f"ep_{ep['id']}",
                            title=f"Episódio : {ep['episode_number']}",
                            description=f"🎬 {series_title} | {ep_title}",
                            # (Use uma thumbnail genérica, como o bot de exemplo)
                            thumbnail_url="https://i.imgur.com/TqA8sE8.png", 
                            reply_markup=reply_markup, # <--- ESTA É A POSIÇÃO CORRETA
                            input_message_content=InputTextMessageContent(
                                message_text=message_text,
                                parse_mode="Markdown"
                                # (Removido daqui)
                            )
                        )
                    )
            # --- FIM DO LOOP DE EPISÓDIOS ---
        
        except Exception as e:
            print(f"❌ Erro na busca inline de episódios: {e}")
            results.append(InlineQueryResultArticle(
                id="error_eps",
                title="Erro ao buscar episódios",
                input_message_content=InputTextMessageContent("Ocorreu um erro ao processar sua solicitação.")
            ))
        
        await update.inline_query.answer(results, cache_time=10, is_personal=True)
        return # <-- Termina a função aqui
    
    #
    # === ROTA 2: BUSCA NORMAL (Filme/Série) ===
    #

    # Se a busca estiver vazia, mostra o balão de ajuda
    if not query_text:
        # (Seu código original)
        help_result = [
            InlineQueryResultArticle(
                id="help_bubble",
                title="Digite o nome do Filme ou Série",
                description="Comece a digitar para que os resultados da busca apareçam aqui.",
                thumbnail_url="https://cdn-icons-png.flaticon.com/512/3931/3931294.png",
                input_message_content=InputTextMessageContent("👍")
            )
        ]
        await update.inline_query.answer(help_result, is_personal=True, cache_time=5)
        return
    
    # "Mágica" do Artigo Fixo (Sem mudança)
    results.append(
        InlineQueryResultArticle(
            id="static_help",
            title="Ajuda",
            description="Como usar o bot de busca",
            thumbnail_url="https://cdn-icons-png.flaticon.com/512/189/189665.png", 
            input_message_content=InputTextMessageContent(
                f"Para buscar, digite @{context.bot.username} e o nome do filme.\n\n"
                "Para ver o menu principal, envie o comando /start."
            )
        )
    )
    
    # --- Busca Híbrida ---
    movies_from_db = db.search_movies(query_text, limit=5)
    series_from_db = db.search_series_by_title(query_text, limit=5)
    bot_username = context.bot.username

    # 3. Processa os resultados de FILMES (Sem mudança)
    for movie in movies_from_db:
        if movie.get('poster_url'):
            watch_url = f"https://t.me/{bot_username}?start=watch_{movie['movie_id']}"
            keyboard = [[
                InlineKeyboardButton("Assistir ⏯️", url=watch_url),
            ],
            [InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=movie['title'])]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            photo_caption = (
                f"🎬 *{movie['title']}* ({movie['year']})\n"
                f"🎭 *Gênero:* {movie.get('genre', 'N/A')}"
            )
            
            poster_url_grande = movie.get('poster_url')
            poster_url_pequeno = poster_url_grande.replace('/w500/', '/w92/')
            
            results.append(
                InlineQueryResultPhoto(
                    id=f"movie_{movie['movie_id']}",
                    title=f"FILME: {movie['title']}", 
                    description=f"{movie['year']} - {movie.get('genre', 'N/A')}", 
                    photo_url=poster_url_grande,
                    thumbnail_url=poster_url_pequeno,
                    caption=photo_caption,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            )

    # 4. Processa os resultados de SÉRIES (FLUXO HÍBRIDO v3.0)
    for series in series_from_db:
        poster_url_grande = series.get('poster_url', 'https://via.placeholder.com/500x750.png?text=Sem+Pôster')
        poster_url_pequeno = poster_url_grande.replace('/w500/', '/w92/')
        
        # 1. Buscar as temporadas (NOVA CONSULTA AO DB)
        seasons = db.get_seasons_for_series(series['series_id'])
        
        # 2. Montar a legenda completa
        photo_caption = (
            f"📺 *{series['title']}*\n\n"
            f"🗓️ *Ano:* {series['year']}\n"
            f"🎭 *Gênero:* {series.get('genre', 'N/A')}\n\n"
            f"📝 *Sinopse:* {series.get('description', 'N/A')}\n\n"
            "---\n"
            "Selecione a temporada desejada abaixo:"
        )
        
        # 3. Montar o teclado com 'switch_inline_query_current_chat'
        keyboard = []
        if seasons:
            for season in seasons:
                keyboard.append([
                    InlineKeyboardButton(
                        f"▶️ Temporada {season['season_number']}",
                        # Dispara a nova busca inline: @SeuBot season:1
                        switch_inline_query_current_chat=f"season:{season['id']}" 
                    )
                ])
        
        keyboard.append([
            InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=series['title'])
        ])
        reply_markup = InlineKeyboardMarkup(keyboard)

        results.append(
            InlineQueryResultPhoto(
                id=f"series_{series['series_id']}",
                title=f"SÉRIE: {series['title']}",
                description=f"{series['year']} - {series.get('genre', 'Série')}",
                photo_url=poster_url_grande,
                thumbnail_url=poster_url_pequeno,
                caption=photo_caption,
                parse_mode="Markdown",
                reply_markup=reply_markup # <-- Teclado com botões REAIS
            )
        )

    await update.inline_query.answer(results, cache_time=30)


async def watch_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código original - sem mudanças)
    if update.message:
        try:
            await update.message.delete()
        except Exception:
            pass
    if not context.args:
        return
    movie_id = context.args[0]
    user_id = update.effective_user.id
    
    if not db.is_user_vip(user_id):
        keyboard = [[InlineKeyboardButton("Adquirir Acesso VIP 🚀", callback_data="main_vip")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = (
            "✨ *Você precisa do Passe Premium para assistir!* ✨\n\n"
            "✅ Acesse TODOS os filmes e séries disponíveis.\n"
            "✅ Ajude a manter o bot online e sempre melhorando.\n\n"
            "Clique no botão abaixo para se tornar VIP!"
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=message_text,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )
        return

    # (Usuário é VIP, continua)
    movie = db.get_movie_by_id(movie_id)
    if movie and movie.get('poster_url'):
        caption = (
            f"🎬 *{movie['title']}*\n\n"
            f"🗓️ *Ano:* {movie['year']}\n"
            f"🎭 *Gênero:* {movie['genre']}\n\n"
            f"📝 *Sinopse:* {movie.get('description', 'N/A')}\n\n"
            "---\n"
            "Selecione o áudio desejado abaixo:"
        )
        keyboard = [[
            InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"play_{movie_id}_dub"),
            InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"play_{movie_id}_sub")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await context.bot.send_photo(
            chat_id=update.effective_chat.id,
            photo=movie['poster_url'],
            caption=caption,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Filme não encontrado ou sem pôster disponível.")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código original - sem mudanças)
    user_state = context.user_data.get('state')
    if user_state == 'awaiting_request':
        del context.user_data['state']
        requested_title = update.message.text
        user_id = update.effective_user.id
        if db.add_request(user_id=user_id, title=requested_title):
            await update.message.reply_text(
                f"✅ Obrigado! Sua sugestão \"{requested_title}\" foi registrada e será analisada.\n\n"
                "Se aprovada, estará disponível em nosso catálogo em até 24 horas!"
            )
        else:
            await update.message.reply_text("😕 Desculpe, ocorreu um erro ao salvar seu pedido. Tente novamente mais tarde.")

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código original - sem mudanças)
    if 'state' in context.user_data:
        del context.user_data['state']
        await update.message.reply_text("Operação cancelada.")
    else:
        await update.message.reply_text("Não há nenhuma operação para cancelar.")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código original - sem mudanças)
    help_text = (
        "Olá! Eu sou o Cine Pipoca, seu assistente de filmes. Veja como me usar:\n\n"
        "🔎 **Para Buscar:**\n"
        "Vá em qualquer chat, digite o `@username` do bot e comece a escrever o nome do filme ou série. Uma lista de resultados aparecerá!\n\n"
        "💡 **Pedir um Filme/Série:**\n"
        "Use o botão 'Pedir Filme/Série' no menu principal para sugerir um título que você não encontrou.\n\n"
        "🏆 **Top Mídia:**\n"
        "Quer saber o que está em alta? Clique no botão 'Top Mídia' no menu e escolha o período.\n\n"
        "🚀 **Acesso VIP:**\n"
        "O acesso VIP te dá direito a assistir todo o catálogo. Você pode adquirir o seu através do botão no menu principal."
    )
    help_text = help_text.replace("@username", f"@{context.bot.username}")
    await update.message.reply_text(help_text, parse_mode="Markdown")

# --- Definição dos Handlers (Sem mudança) ---
start_handler = CommandHandler("start", start)
button_click_handler = CallbackQueryHandler(button_handler)
inline_search_handler = InlineQueryHandler(inline_query_handler)
watch_handler = CommandHandler("watch", watch_command_handler)
text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler)
cancel_command_handler = CommandHandler("cancelar", cancel_handler)
help_command_handler = CommandHandler("help", help_handler)
request_command_handler = CommandHandler("pedir", request_command_handler)
