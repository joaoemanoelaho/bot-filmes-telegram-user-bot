#
# NOME DO ARQUIVO: handlers_user.py (VERSÃO 2.1 - FLUXO INLINE DE SÉRIES)
#
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    InlineQueryResultArticle, InputTextMessageContent, 
    InlineQueryResultPhoto, InputMediaPhoto,
    InlineQueryResultCachedVideo  # <--- MUDANÇA 1: Importado para episódios
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
    (ATUALIZADO) Helper para o deep link /start series_... ou botão 'Voltar'.
    Verifica o VIP e mostra o card com botões INLINE QUERY para as temporadas.
    """
    user_id = update.effective_user.id
    
    # Se for um comando /start, deleta a mensagem
    if update.message:
        try:
            await update.message.delete()
        except Exception as e:
            print(f"Não foi possível deletar a msg /start: {e}") # Evita o crash de ReadError
        
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
    # <--- MUDANÇA 2: Botões agora usam 'switch_inline_query_current_chat' ---
    # Cria botões para cada temporada
    for season in seasons:
        # Este será o texto da busca: @SeuBot ep:123 (onde 123 é o ID da temporada)
        inline_episode_query = f"season:{season['id']}"
        
        keyboard.append([
            InlineKeyboardButton(
                f"▶️ Temporada {season['season_number']}",
                switch_inline_query_current_chat=inline_episode_query
            )
        ])
    # <--- FIM DA MUDANÇA 2 ---
    
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

# <--- MUDANÇA 3: As funções 'send_season_details' e 'send_episode_options' foram REMOVIDAS
# Elas não são mais necessárias, pois o fluxo agora é feito pelo 'inline_query_handler'.
# ---

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
        # --- Roteador de Deep Link ---
        if payload.startswith("watch_"):
            movie_id = payload.split('_')[1]
            context.args = [movie_id]
            return await watch_command_handler(update, context) # Chama o handler de filmes
        elif payload.startswith("series_"):
            series_id = int(payload.split('_')[1])
            # Chama o NOVO helper de séries
            return await show_series_details_handler(update, context, series_id)

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
            # Tenta editar a foto (se o menu anterior era uma foto, como o de séries)
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
            # Se falhar (ex: editando texto para foto), envia nova msg
            print(f"Erro ao editar de volta ao menu: {e}. Enviando nova mensagem.")
            await context.bot.send_message(
                chat_id=user.id, 
                text=welcome_text, 
                reply_markup=main_menu, 
                parse_mode='HTML'
            )
            if message_to_reply:
                await message_to_reply.delete() # Deleta a msg anterior
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
        
        parts = callback_data.split('_')
        media_id = int(parts[1])
        media_type = 'movie' # Padrão é filme
        
        # Verifica se é uma série (com base no que definimos em 'inline_query_handler')
        if len(parts) > 2 and parts[2] == 'series':
            media_type = 'series'
        
        title_to_search = None
        
        if media_type == 'movie':
            media_obj = db.get_movie_by_id(media_id)
            if media_obj:
                title_to_search = media_obj['title']
        else: # media_type == 'series'
            media_obj = db.get_series_by_id(media_id)
            if media_obj:
                title_to_search = media_obj['title']

        if not title_to_search:
            await context.bot.send_message(chat_id=user_id, text="Não consegui encontrar a mídia original.")
            return

        status_msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ Buscando mídias relacionadas a '{title_to_search}'...")
        
        # O resto do código é IDÊNTICO, pois a API de recomendação aceita nome de série
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
    # <--- MUDANÇA 4: A antiga "MUDANÇA 3" (lógica de botões de série) foi REMOVIDA
    # Ela não é mais necessária, pois o 'inline_query_handler' cuida disso.
    #
    


async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (ATUALIZADO) Lida com as buscas em modo inline para FILMES, SÉRIES e EPISÓDIOS.
    """
    query_text = update.inline_query.query
    results = []
    
    #
    # <--- MUDANÇA 5: NOVA LÓGICA PARA BUSCAR EPISÓDIOS ---
    #
    if query_text.startswith("season:"):
        try:
            season_id = int(query_text.split(':')[1])
            
            # Sua função de DB que retorna uma tupla (lista_de_episodios, dados_da_temporada)
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

            # Pega o título da série (precisa que a 'season' tenha o 'series_title')
            # Se sua função `get_episodes_for_season` não retorna o título da série
            # junto com os dados da 'season', você pode precisar de outra consulta ao DB aqui.
            # Vou assumir que 'season' tem um campo 'series_title' para o 'description'
            
            # (Se 'season' não tiver o título da série, descomente a linha abaixo)
            # series = db.get_series_by_id(season['series_id'])
            # series_title = series['title']

            # ▼▼▼ AQUI ESTÁ O CONSERTO DO BUG ▼▼▼
            # O objeto 'season' NÃO tem o título da série, apenas o 'series_id'.
            # Precisamos buscar a série para pegar o nome dela.
            
            series = db.get_series_by_id(season['series_id'])
            if series:
                series_title = series.get('title', 'Série')
            else:
                series_title = 'Série' # Fallback
            # ▲▲▲ FIM DO CONSERTO DO BUG ▲▲▲

            bot_username = context.bot.username

            keyboard = [
                [
                    # Botão para compartilhar a SÉRIE (não o episódio)
                    InlineKeyboardButton(
                        "Compartilhar ❤️", 
                        switch_inline_query=series_title
                    ),
                    # Novo botão de relacionados (vamos fazê-lo funcionar na MUDANÇA 2)
                    InlineKeyboardButton(
                        "🍿 Relacionados", 
                        callback_data=f"related_{series['id']}_series" # Novo formato
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            for ep in episodes:
                ep_title = ep.get('title', f"Episódio {ep['episode_number']}")
                
                video_caption_dub = (
                    f"📺 *{series_title}*\n"
                    f"S{season['season_number']:02d}E{ep['episode_number']:02d}: *{ep_title}* (Dublado)\n\n"
                    f"---\n"
                    f"🍿 Assistido com @{bot_username}"
                )

                # Adiciona resultado para Dublado
                if ep.get('dubbed_file_id'):
                    results.append(
                        InlineQueryResultCachedVideo(
                            id=f"ep_dub_{ep['id']}",
                            video_file_id=ep['dubbed_file_id'],
                            title=f"Ep. {ep['episode_number']} (Dublado) 🇧🇷",
                            description=f"{series_title} | {ep_title}",
                            caption=f"📺 {series_title}\nS{season['season_number']:02d}E{ep['episode_number']:02d} (Dublado)"
                        )
                    )
                
                video_caption_sub = (
                    f"📺 *{series_title}*\n"
                    f"S{season['season_number']:02d}E{ep['episode_number']:02d}: *{ep_title}* (Legendado)\n\n"
                    f"---\n"
                    f"🍿 Assistido com @{bot_username}"
                )

                # Adiciona resultado para Legendado
                if ep.get('subtitled_file_id'):
                    results.append(
                        InlineQueryResultCachedVideo(
                            id=f"ep_sub_{ep['id']}",
                            video_file_id=ep['subtitled_file_id'],
                            title=f"Ep. {ep['episode_number']} (Legendado) 🇺🇸",
                            description=f"{series_title} | {ep_title}",
                            caption=f"📺 {series_title}\nS{season['season_number']:02d}E{ep['episode_number']:02d} (Legendado)"
                        )
                    )
        
        except Exception as e:
            print(f"❌ Erro na busca inline de episódios: {e}")
            results.append(InlineQueryResultArticle(
                id="error_eps",
                title="Erro ao buscar episódios",
                input_message_content=InputTextMessageContent("Ocorreu um erro ao processar sua solicitação.")
            ))
        
        await update.inline_query.answer(results, cache_time=10, is_personal=True)
        return # <-- IMPORTANTE: Termina a função aqui
    # --- FIM DA MUDANÇA 5 ---


    # Se a busca estiver vazia, mostra o balão de ajuda normal
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
    
    # V--- INÍCIO DA CORREÇÃO (A "MÁGICA") ---V
    # 1. Adiciona um "Artigo" de Ajuda Fixo no TOPO da lista.
    #    Isso força o Telegram a usar a lista vertical.
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
    # ^--- FIM DA MÁGICA ---^

    
    # --- Busca Híbrida (Filmes e Séries) ---
    
    # 2. Busca Filmes e Séries no banco de dados
    movies_from_db = db.search_movies(query_text, limit=5)
    series_from_db = db.search_series_by_title(query_text, limit=5)
    
    bot_username = context.bot.username

    # 3. Processa os resultados de FILMES (Convertido para Photo)
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

    # 4. Processa os resultados de SÉRIES (Convertido para Photo)
    for series in series_from_db:
        poster_url_grande = series.get('poster_url', 'https://via.placeholder.com/500x750.png?text=Sem+Pôster')
        poster_url_pequeno = poster_url_grande.replace('/w500/', '/w92/')
        
        # O deep link AGORA aponta para o handler que mostra o card de temporadas
        watch_url = f"https://t.me/{bot_username}?start=series_{series['series_id']}"
        
        keyboard = [[
            InlineKeyboardButton("Ver Temporadas 📺", url=watch_url),
        ],
        [InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=series['title'])]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        photo_caption = (
            f"📺 *{series['title']}* ({series['year']})\n"
            f"🎭 *Gênero:* {series.get('genre', 'Série')}"
        )
        
        results.append(
            InlineQueryResultPhoto(
                id=f"series_{series['series_id']}",
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
        try:
            await update.message.delete()
        except Exception as e:
            print(f"Não foi possível deletar a msg /watch: {e}")
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
