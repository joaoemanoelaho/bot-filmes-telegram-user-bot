#
# NOME DO ARQUIVO: handlers_user.py (VERSÃO 2.0 - VIP BOT + SÉRIES)
#
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    InlineQueryResultArticle, InputTextMessageContent, 
    InlineQueryResultPhoto, InputMediaPhoto # <-- MUDANÇA: Importar InputMediaPhoto
)
from telegram.ext import CommandHandler, ContextTypes, CallbackQueryHandler, InlineQueryHandler, MessageHandler, filters
import database as db
# import tmdb_api (Este arquivo não está sendo usado)
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
# === NOVOS HELPERS DE NAVEGAÇÃO (SÉRIES) ===
# =================================================================

async def show_series_details_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, series_id: int):
    """
    (NOVO) Helper para o deep link /start series_... ou botão 'Voltar'.
    Verifica o VIP e mostra o card de temporadas.
    """
    user_id = update.effective_user.id
    
    # Se for um comando /start, deleta a mensagem
    if update.message:
        await update.message.delete()
        
    # 1. Verifica o VIP
    if not db.is_user_vip(user_id):
        keyboard = [[InlineKeyboardButton("Adquirir Acesso VIP 🚀", callback_data="main_vip")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        message_text = (
            "✨ *Você precisa do Passe Premium para assistir séries!* ✨\n\n"
            "Clique no botão abaixo para se tornar VIP!"
        )
        await context.bot.send_message(
            chat_id=user_id, text=message_text,
            reply_markup=reply_markup, parse_mode="Markdown"
        )
        return
        
    # 2. (VIP) Busca a série e temporadas
    series = db.get_series_by_id(series_id)
    if not series:
        await context.bot.send_message(chat_id=user_id, text="Erro: Série não encontrada.")
        return

    seasons = db.get_seasons_for_series(series_id)
    if not seasons:
        await context.bot.send_message(chat_id=user_id, text="Esta série ainda não tem temporadas cadastradas.")
        return

    # 3. Monta o card da série
    caption = (
        f"📺 *{series['title']}*\n\n"
        f"🗓️ *Ano:* {series['year']}\n"
        f"🎭 *Gênero:* {series.get('genre', 'N/A')}\n\n"
        f"📝 *Sinopse:* {series.get('description', 'N/A')}\n\n"
        "---\n"
        "Selecione a temporada desejada abaixo:"
    )
    
    keyboard = []
    # Cria botões para cada temporada
    for season in seasons:
        keyboard.append([
            InlineKeyboardButton(
                f"▶️ Temporada {season['season_number']}",
                callback_data=f"series_season_{season['id']}" # <-- Novo Callback
            )
        ])
    
    # Botão de voltar ao menu principal
    keyboard.append([InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    poster_url = series.get('poster_url', 'https://via.placeholder.com/500x750.png?text=Sem+Pôster')

    # 4. Envia ou Edita
    if update.callback_query:
        # Se veio de um botão "Voltar", edita a mensagem
        try:
            await update.callback_query.edit_message_media(
                media=InputMediaPhoto(media=poster_url, caption=caption, parse_mode='Markdown'),
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Erro ao editar para show_series_details_handler: {e}")
    else:
        # Se veio de um deep link /start, envia uma nova foto
        await context.bot.send_photo(
            chat_id=user_id,
            photo=poster_url,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=reply_markup
        )

async def send_season_details(query: Update, context: ContextTypes.DEFAULT_TYPE, season_id: int):
    """
    (NOVO) Mostra os episódios de uma temporada.
    """
    try:
        episodes, season = db.get_episodes_for_season(season_id)
        if not episodes:
            await query.answer("Esta temporada ainda não tem episódios.", show_alert=True)
            return

        keyboard = []
        for ep in episodes:
            audio_tags = ""
            if ep.get('dubbed_file_id'): audio_tags += "[DUB] "
            if ep.get('subtitled_file_id'): audio_tags += "[LEG]"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"Ep. {ep['episode_number']}: {ep['title']} {audio_tags}",
                    callback_data=f"series_episode_{ep['id']}" # <-- Novo Callback
                )
            ])
        
        # Botão de Voltar para a lista de temporadas
        keyboard.append([InlineKeyboardButton("🔙 Voltar (Info da Série)", callback_data=f"series_view_{season['series_id']}")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Apenas edita a mensagem anterior
        await query.callback_query.edit_message_caption(
            caption=f"Selecione um episódio para a **Temporada {season['season_number']}**:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
    except Exception as e:
        print(f"❌ Erro em send_season_details: {e}")

async def send_episode_options(query: Update, context: ContextTypes.DEFAULT_TYPE, episode_id: int):
    """
    (NOVO) Mostra as opções de áudio (DUB/LEG) para um episódio.
    """
    try:
        episode = db.get_episode_by_id(episode_id)
        if not episode:
            await query.answer("Episódio não encontrado.", show_alert=True)
            return

        keyboard = []
        if episode.get('dubbed_file_id'):
            keyboard.append([InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"series_send_{episode['id']}_dub")])
        if episode.get('subtitled_file_id'):
            keyboard.append([InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"series_send_{episode['id']}_sub")])
        
        # Botão de Voltar para a lista de episódios
        keyboard.append([InlineKeyboardButton("🔙 Voltar (Episódios)", callback_data=f"series_season_{episode['season_id']}")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.callback_query.edit_message_caption(
            caption="Selecione o áudio desejado abaixo:",
            reply_markup=reply_markup
        )

    except Exception as e:
        print(f"❌ Erro em send_episode_options: {e}")

# =================================================================
# === HANDLERS EXISTENTES (ATUALIZADOS) ===
# =================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (ATUALIZADO) Função /start.
    Com lógica de Deep Linking para FILMES e SÉRIES.
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
        # --- MUDANÇA 1: Roteador de Deep Link ---
        if payload.startswith("watch_"):
            movie_id = payload.split('_')[1]
            context.args = [movie_id]
            return await watch_command_handler(update, context) # Chama o handler de filmes
        elif payload.startswith("series_"):
            series_id = int(payload.split('_')[1])
            # Chama o NOVO helper de séries
            return await show_series_details_handler(update, context, series_id)
        # --- FIM DA MUDANÇA 1 ---

    db.get_or_create_user(user_id=user.id, first_name=user.first_name)
    
    # --- MUDANÇA 2: Texto dos botões ---
    keyboard = [
        [
            InlineKeyboardButton("Buscar Mídia 🔎", switch_inline_query_current_chat=""),
        ],
        [
            InlineKeyboardButton("Adquirir VIP 🚀", callback_data="main_vip")
        ],
        [
            InlineKeyboardButton("Pedir Filme/Série 💡", callback_data="main_request"),
            InlineKeyboardButton("Top Mídia 🏆", callback_data="main_top") # Texto atualizado
        ]
    ]
    # --- FIM DA MUDANÇA 2 ---
    
    main_menu = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        f"Olá {user.mention_html()}! 👋\n\n"
        "Gosta de maratonar? Esse bot é perfeito para isso 😉.\n\n"
        "Clique no botão \"Buscar Mídia 🔎\" para começar.\n\n"
        "Ficou com dúvidas? Envie o comando /help"
    )
    
    if is_query:
        try:
            # (Seu código original usa edit_text, então vamos manter)
            await message_to_reply.edit_text(welcome_text, reply_markup=main_menu, parse_mode='HTML')
        except Exception as e:
            print(f"Erro ao editar mensagem de volta ao menu: {e}")
            await context.bot.send_message(chat_id=user.id, text=welcome_text, reply_markup=main_menu, parse_mode='HTML')
    else:
        await message_to_reply.reply_html(welcome_text, reply_markup=main_menu)

async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Sem mudança) Inicia o fluxo de pedido."""
    context.user_data['state'] = 'awaiting_request'
    await update.message.reply_text(
        "Qual filme ou série você gostaria de ver no catálogo?\n\n"
        "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (ATUALIZADO) Processa TODOS os cliques em botões inline.
    """
    query = update.callback_query
    # (Seu código moveu o answer() para dentro dos blocos, vamos manter)
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

    elif callback_data.startswith("related_"):
        await query.answer()
        movie_id = int(callback_data.split('_')[1])
        movie = db.get_movie_by_id(movie_id)
        if not movie:
            await context.bot.send_message(chat_id=user_id, text="Não consegui encontrar o filme original.")
            return
        status_msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ Buscando filmes relacionados a '{movie['title']}'...")
        recommendations_from_api = tastedive_api.get_recommendations(movie['title'])
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
        message_text = f"Se você gostou de '{movie['title']}', talvez também goste destes:\n\nClique em um título para buscar:"
        await status_msg.edit_text(text=message_text, reply_markup=InlineKeyboardMarkup(keyboard))

    # --- LÓGICA PARA O BOTÃO DE PEDIDO (Sem mudança) ---
    elif callback_data == "main_request":
        await query.answer()
        context.user_data['state'] = 'awaiting_request'
        await query.edit_message_text(
            text="Qual filme ou série você gostaria de ver no catálogo?\n\n"
                 "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
        )
    
    # --- LÓGICA PARA O BOTÃO TOP FILMES (Sem mudança) ---
    elif callback_data == "main_top":
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

    # --- LÓGICA PARA O BOTÃO DE VOLTAR AO MENU (Sem mudança) ---
    elif callback_data == "back_to_main":
        await query.answer()
        await start(update, context)
        
    # --- LÓGICA DE PAGAMENTO VIP (Sem mudança) ---
    elif callback_data == "main_vip":
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
    # --- INÍCIO DA MUDANÇA 3: LÓGICA DE BOTÕES DE SÉRIES ---
    #
    elif callback_data.startswith("series_view_"):
        await query.answer()
        series_id = int(callback_data.split('_')[2])
        # Requer que o helper possa lidar com um 'update.callback_query'
        await show_series_details_handler(update, context, series_id)
        
    elif callback_data.startswith("series_season_"):
        await query.answer()
        season_id = int(callback_data.split('_')[2])
        await send_season_details(query, context, season_id)
        
    elif callback_data.startswith("series_episode_"):
        await query.answer()
        episode_id = int(callback_data.split('_')[2])
        await send_episode_options(query, context, episode_id)
        
    elif callback_data.startswith("series_send_"):
        await query.answer()
        parts = callback_data.split('_')
        episode_id = int(parts[2])
        audio_type = parts[3]
        
        # Deleta a mensagem de botões
        await query.delete_message()
        
        # Pega o episódio
        episode = db.get_episode_by_id(episode_id)
        if not episode:
            await context.bot.send_message(chat_id=user_id, text="Erro: Episódio não encontrado.")
            return

        file_id_to_send = None
        if audio_type == 'dub' and episode.get('dubbed_file_id'):
            file_id_to_send = episode['dubbed_file_id']
        elif audio_type == 'sub' and episode.get('subtitled_file_id'):
            file_id_to_send = episode['subtitled_file_id']
        
        if file_id_to_send:
            await context.bot.send_video(
                chat_id=query.message.chat.id,
                video=file_id_to_send,
                caption=f"▶️ Episódio {audio_type.upper()}",
                protect_content=True
            )
        else:
            await context.bot.send_message(chat_id=user_id, text="😔 Desculpe, esta versão do áudio não está disponível.")
    # --- FIM DA MUDANÇA 3 ---


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (ATUALIZADO) Lida com as buscas em modo inline para FILMES, SÉRIES e EPISÓDIOS.
    Usa o "Hack" de misturar Article + Photo para forçar a lista vertical.
    """
    query_text = update.inline_query.query
    results = []
    
    # --- LÓGICA DE BUSCA DE EPISÓDIOS ---
    if '|' in query_text:
        try:
            series_name, season_query = query_text.split('|', 1)
            series_name = series_name.strip()
            
            season_number_match = re.search(r'(\d+)', season_query)
            if not season_number_match:
                await update.inline_query.answer([], cache_time=10)
                return

            season_number = int(season_number_match.group(1))
            
            series_list = db.search_series_by_title(series_name, limit=1)
            if not series_list:
                await update.inline_query.answer([], cache_time=10)
                return
                
            # V--- CORREÇÃO DE KEYERROR AQUI TAMBÉM ---V
            series_id = series_list[0].get('id')
            if not series_id:
                 await update.inline_query.answer([], cache_time=10)
                 return
            # ^--- FIM DA CORREÇÃO ---^
            
            all_seasons = db.get_seasons_for_series(series_id)
            target_season = next((s for s in all_seasons if s['season_number'] == season_number), None)
            
            if target_season:
                episodes, _ = db.get_episodes_for_season(target_season['id'])
                for ep in episodes:
                    episode_db_id = ep.get('id') # <-- Usar .get() por segurança
                    if not episode_db_id:
                        continue
                        
                    results.append(
                        InlineQueryResultArticle(
                            id=f"ep_{episode_db_id}",
                            title=f"Episódio {ep['episode_number']}: {ep['title']}",
                            description=f"{series_name} - T{season_number}:E{ep['episode_number']}",
                            thumbnail_url="https://cdn-icons-png.flaticon.com/512/1042/1042340.png",
                            input_message_content=InputTextMessageContent(
                                f"Para assistir o Episódio {ep['episode_number']} de {series_name}, "
                                f"por favor, use o comando /start e navegue até a série."
                            )
                        )
                    )
            
            await update.inline_query.answer(results, cache_time=30)
            return
            
        except Exception as e:
            print(f"Erro ao buscar episódios inline: {e}")
            await update.inline_query.answer([], cache_time=10)
            return

    # --- FIM DA LÓGICA DE EPISÓDIOS ---

    if not query_text:
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
    
    # "HACK" DA LISTA VERTICAL
    results.append(
        InlineQueryResultArticle(
            id="static_help",
            title="Ajuda",
            description="Como usar o bot de busca",
            input_message_content=InputTextMessageContent(
                "Para buscar, digite @MeuCinePipocaBot e o nome do filme.\n\n"
                "Para ver o menu principal, envie o comando /start."
            )
        )
    )
    
    bot_username = context.bot.username
    
    # --- Busca Híbrida (Filmes) ---
    movies_from_db = db.search_movies(query_text, limit=5)
    for movie in movies_from_db:
        movie_id = movie.get('movie_id') # <--- CORREÇÃO DE SEGURANÇA
        if not movie_id or not movie.get('poster_url'):
            continue
            
        watch_url = f"https://t.me/{bot_username}?start=watch_{movie_id}"
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
                id=f"movie_{movie_id}",
                title=f"FILME: {movie['title']}",
                description=f"{movie['year']} - {movie.get('genre', 'N/A')}",
                photo_url=poster_url_grande,
                thumbnail_url=poster_url_pequeno,
                caption=photo_caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        )

    # --- Busca Híbrida (Séries) ---
    series_from_db = db.search_series_by_title(query_text, limit=5)
    for series in series_from_db:
        # V--- ESTA É A CORREÇÃO DO 'KeyError: id' ---V
        series_id = series.get('id')
        if not series_id or not series.get('poster_url'):
            continue  # Pula esta série se ela não tiver 'id' ou 'poster'
        # ^--- FIM DA CORREÇÃO ---^

        poster_url_grande = series.get('poster_url', 'https://via.placeholder.com/500x750.png?text=Sem+Pôster')
        poster_url_pequeno = poster_url_grande.replace('/w500/', '/w92/')
        
        watch_url = f"https://t.me/{bot_username}?start=series_{series_id}"
        
        seasons = db.get_seasons_for_series(series_id)
        keyboard = []
        for season in seasons[:3]: 
            keyboard.append([
                InlineKeyboardButton(
                    f"Temporada {season['season_number']}",
                    switch_inline_query_current_chat=f"{series['title']} | {season['season_number']}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=series['title'])])
        if len(seasons) > 3:
             keyboard.append([InlineKeyboardButton("Ver todas temporadas...", url=watch_url)])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        photo_caption = (
            f"📺 *{series['title']}* ({series['year']})\n"
            f"🎭 *Gênero:* {series.get('genre', 'Série')}"
        )
        
        results.append(
            InlineQueryResultPhoto(
                id=f"series_{series_id}",
                title=f"SÉRIE: {series['title']}",
                description=f"{series['year']} - {series.get('genre', 'Série')}",
                photo_url=poster_url_grande,
                thumbnail_url=poster_url_pequeno,
                caption=photo_caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        )

    await update.inline_query.answer(results, cache_time=30)

async def watch_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Sem mudança) Lida com o comando /watch OU é chamada pela função start."""
    if update.message:
        await update.message.delete()
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
    """(Sem mudança) Lida com mensagens de texto para capturar respostas (pedidos)."""
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
    """(Sem mudança) Cancela a conversa atual."""
    if 'state' in context.user_data:
        del context.user_data['state']
        await update.message.reply_text("Operação cancelada.")
    else:
        await update.message.reply_text("Não há nenhuma operação para cancelar.")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Sem mudança) Envia uma mensagem de ajuda."""
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
