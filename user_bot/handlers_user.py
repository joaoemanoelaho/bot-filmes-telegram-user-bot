#
# NOME DO ARQUIVO: handlers_user.py (VERSÃO 3.3 - CORREÇÃO DE URLs)
#
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton, 
    InlineQueryResultArticle, InputTextMessageContent, 
    InlineQueryResultPhoto, InputMediaPhoto
)
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
import asyncio
from thefuzz import fuzz
from starlette.requests import Request
from starlette.responses import Response
import uuid
import tastedive_api

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

# =================================================================
# === GERENCIADOR DE FILA (SEMAPHORE) ===
# =================================================================
# Este é o "porteiro". Ele só permite que 20 tarefas pesadas (que usam DB)
# rodem ao mesmo tempo. Isso evita travar seu bot e o Supabase.
DB_SEMAPHORE = asyncio.Semaphore(20)

# =================================================================
# === NOVA FUNÇÃO HELPER (PARA NAVEGAÇÃO) ===
# =================================================================

async def _get_episode_details_message(episode_id: int) -> (str, InlineKeyboardMarkup):
    """
    (NOVO v3.2) Gera o texto e os botões para a mensagem "Selecione o áudio".
    OS BOTÕES DE NAVEGAÇÃO FORAM REMOVIDOS DAQUI.
    """
    try:
        # 1. Pega o episódio, temporada e série
        episode = await db.get_episode_by_id(episode_id)
        if not episode:
            return ("Erro: Episódio não encontrado.", None)
            
        # Usamos a função do DB que já retorna a lista de eps E os dados da temporada
        all_episodes, season = await db.get_episodes_for_season(episode['season_id'])
        if not season:
             return ("Erro: Temporada não encontrada.", None)

        series = await db.get_series_by_id(season['series_id'])
        series_title = series.get('title', 'Série')
        ep_title = episode.get('title', f"Episódio {episode['episode_number']}")

        # 2. Monta o Texto da Mensagem
        message_text = (
            f"📽️ *{series_title}*\n"
            f"🎬 *Temporada:* {season['season_number']}\n"
            f"🎯 *Episódio:* {episode['episode_number']} - {ep_title}\n"
            f"--------------------\n"
            f"Selecione o áudio:"
        )

        # 3. Monta os Botões de Áudio (Dub/Leg)
        keyboard = []
        audio_row = []
        if episode.get('dubbed_file_id'):
            audio_row.append(
                InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"series_send_{episode['id']}_dub")
            )
        if episode.get('subtitled_file_id'):
            audio_row.append(
                InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"series_send_{episode['id']}_sub")
            )
        
        if audio_row:
             keyboard.append(audio_row)

        # 4. (REMOVIDO) Botões de Navegação
        # A navegação agora é adicionada na mensagem do VÍDEO.
        
        return (message_text, InlineKeyboardMarkup(keyboard))
        
    except Exception as e:
        print(f"Erro em _get_episode_details_message: {e}")
        import traceback
        traceback.print_exc() # Imprime o traceback completo para debug
        return (f"Erro ao carregar detalhes do episódio: {e}", None)


# =================================================================
# === HANDLERS EXISTENTES (ATUALIZADOS) ===
# =================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código v3.0 - Sem mudanças)
    async with DB_SEMAPHORE:
        is_query = update.callback_query is not None
        if is_query:
            user = update.callback_query.from_user
            message_to_reply = update.callback_query.message
        else:
            user = update.effective_user
            message_to_reply = update.message
        if context.args:
            payload = context.args[0]
            if payload.startswith("watch_"):
                movie_id = payload.split('_')[1]
                context.args = [movie_id]
                await watch_command_handler(update, context)
                return
            
        await db.get_or_create_user(user_id=user.id, first_name=user.first_name)
        keyboard = [
            [InlineKeyboardButton("Buscar Mídia 🔎", switch_inline_query_current_chat=""),],
            [InlineKeyboardButton("Adquirir VIP 🚀", callback_data="main_vip")],
            [InlineKeyboardButton("Pedir Filme/Série 💡", callback_data="main_request"),
            InlineKeyboardButton("Top Mídia 🏆", callback_data="main_top")]
        ]
        main_menu = InlineKeyboardMarkup(keyboard)
        
        community_link = "https://t.me/meucinepipocacanal" 
        
        community_text = (
            "Psst! 🤫 Quer debater sobre filmes, pedir séries, ou dar ideias para o bot?\n"
            f"➡️ <a href=\"{community_link}\"><b>Junte-se à nossa comunidade!</b></a>"
        )

        welcome_text = (
            f"Olá {user.mention_html()}! 👋\n\n"
            "Gosta de maratonar? Esse bot é perfeito para isso 😉.\n\n"
            "Clique no botão \"Buscar Mídia 🔎\" para começar.\n\n"
            "Ficou com dúvidas? Envie o comando /help\n\n"
            "--------------------\n\n"
            f"{community_text}"
        )
        if is_query:
            try:
                if message_to_reply.photo:
                    await message_to_reply.edit_caption(caption=welcome_text, reply_markup=main_menu, parse_mode='HTML')
                else:
                    await message_to_reply.edit_text(welcome_text, reply_markup=main_menu, parse_mode='HTML')
            except Exception as e:
                print(f"Erro ao editar de volta ao menu: {e}.")
                await context.bot.send_message(chat_id=user.id, text=welcome_text, reply_markup=main_menu, parse_mode='HTML')
                if message_to_reply:
                    await message_to_reply.delete()
        else:
            await message_to_reply.reply_html(welcome_text, reply_markup=main_menu)


async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (v3.3) Inicia o fluxo de pedido.
    AGORA COM VERIFICAÇÃO VIP.
    """
    async with DB_SEMAPHORE:
        # --- INÍCIO DA VERIFICAÇÃO VIP ---
        user_id = update.effective_user.id
        if not await db.is_user_vip(user_id):
            keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            message_text = (
                f"Opa, {update.effective_user.first_name}! 👋\n\n"
                "Esta é uma função exclusiva do 🍿 **Acesso Pipoca Premium**!\n\n"
                "Assine para poder pedir filmes/séries e ver o que está em alta."
            )
            await update.message.reply_text(
                text=message_text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            return
        # --- FIM DA VERIFICAÇÃO VIP ---

        # (Usuário é VIP, o código continua)
        context.user_data['state'] = 'awaiting_request'
        await update.message.reply_text(
            "Qual filme ou série você gostaria de ver no catálogo?\n\n"
            "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (v3.2) Processa cliques em botões.
    LÓGICA 'series_send_' ATUALIZADA para incluir navegação no vídeo.
    """
    query = update.callback_query
    callback_data = query.data
    user_id = query.from_user.id
    print(f"Usuário {user_id} clicou no botão: {callback_data}")

    # --- LÓGICA PARA ENVIAR O FILME (Sem mudança) ---
    if callback_data.startswith("play_"):
        # (Seu código v3.0 - Sem mudanças)

        # --- BLOQUEIO ANTI-SPAM (THROTTLING) ---
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10 # 10 segundos
        
        if now - last_request < cooldown:
            await query.answer(
                text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s antes de outra ação.",
                show_alert=True
            )
            return
        context.user_data['last_action_time'] = now
        # --- FIM DO BLOQUEIO ---

        async with DB_SEMAPHORE:
            await query.answer()
            _, movie_id_str, audio_choice = callback_data.split('_')
            movie_id = int(movie_id_str)
            await query.edit_message_caption(caption="⏳ Carregando seu filme, por favor aguarde...")
            movie = await db.get_movie_by_id(movie_id)
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
                keyboard = [[
                    InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"{movie['title']}"), 
                    InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{movie_id}")
                ]]
                video_reply_markup = InlineKeyboardMarkup(keyboard)
                await context.bot.send_video(
                    chat_id=query.message.chat.id,
                    video=file_id_to_send,
                    caption=video_caption,
                    parse_mode="Markdown",
                    reply_markup=video_reply_markup,
                    protect_content=True
                )
                await db.log_movie_view(movie_id=movie_id, user_id=user_id)
            else:
                await query.edit_message_caption(caption="😔 Desculpe, esta versão do filme não está disponível.")

    # --- LÓGICA DE RELACIONADOS (Sem mudança) ---
    elif callback_data.startswith("related_"):
        # (Seu código v3.0 - Sem mudanças)

        # --- BLOQUEIO ANTI-SPAM (THROTTLING) ---
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10 # 10 segundos
        
        if now - last_request < cooldown:
            await query.answer(
                text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s antes de outra ação.",
                show_alert=True
            )
            return
        context.user_data['last_action_time'] = now
        # --- FIM DO BLOQUEIO ---

        async with DB_SEMAPHORE:
            await query.answer()
            parts = callback_data.split('_')
            media_id = int(parts[1])
            media_type = 'movie' 
            if len(parts) > 2 and parts[2] == 'series':
                media_type = 'series'
            title_to_search = None
            if media_type == 'movie':
                media_obj = await db.get_movie_by_id(media_id)
                if media_obj: title_to_search = media_obj['title']
            else: 
                media_obj = await db.get_series_by_id(media_id)
                if media_obj: title_to_search = media_obj['title']
            if not title_to_search:
                await context.bot.send_message(chat_id=user_id, text="Não consegui encontrar a mídia original.")
                return
            status_msg = await context.bot.send_message(chat_id=user_id, text=f"⏳ Buscando mídias relacionadas a '{title_to_search}'...")
            recommendations_from_api = await tastedive_api.get_recommendations(title_to_search)
            if recommendations_from_api:
                existing_recommendations = await db.filter_existing_titles(recommendations_from_api)
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
        # (Seu código v3.0 - Sem mudanças)
        # --- INÍCIO DA VERIFICAÇÃO VIP ---
        async with DB_SEMAPHORE:
            user_id = query.from_user.id
            if not await db.is_user_vip(user_id):
                await query.answer() 
                keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")],
                            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                message_text = (
                    f"Opa, {query.from_user.first_name}! 👋\n\n"
                    "Esta é uma função exclusiva do 🍿 **Acesso Pipoca Premium**!\n\n"
                    "Assine para poder pedir filmes/séries e ver o que está em alta."
                )
                try:
                    await query.edit_message_text( 
                        text=message_text,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                except Exception: pass
                return
            # --- FIM DA VERIFICAÇÃO VIP ---
            await query.answer()
            context.user_data['state'] = 'awaiting_request'
            await query.edit_message_text(
                text="Qual filme ou série você gostaria de ver no catálogo?\n\n"
                    "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
            )
    
    # --- LÓGICA TOP FILMES (Sem mudança) ---
    elif callback_data == "main_top":
        # (Seu código v3.0 - Sem mudanças)
        # --- INÍCIO DA VERIFICAÇÃO VIP ---
        async with DB_SEMAPHORE:
            user_id = query.from_user.id
            if not await db.is_user_vip(user_id):
                await query.answer() 
                keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")],
                            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                message_text = (
                    f"Opa, {query.from_user.first_name}! 👋\n\n"
                    "Esta é uma função exclusiva do 🍿 **Acesso Pipoca Premium**!\n\n"
                    "Assine para poder pedir filmes/séries e ver o que está em alta."
                )
                try:
                    await query.edit_message_text( 
                        text=message_text,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                except Exception: pass
                return
            # --- FIM DA VERIFICAÇÃO VIP ---
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
        # (Seu código v3.0 - Sem mudanças)
        async with DB_SEMAPHORE:
            await query.answer()
            period_days = int(callback_data.split('_')[1])
            period_text = "Geral (Todos os Tempos)"
            if period_days == 7: period_text = "da Semana"
            if period_days == 30: period_text = "do Mês"
            await query.edit_message_text(f"🏆 Buscando o Top 10 {period_text}, aguarde...")
            trending_movies = await db.get_trending(period_days=period_days)
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
        async with DB_SEMAPHORE:
            await query.answer()
            movie_id = int(callback_data.split('_')[2])
            movie = await db.get_movie_by_id(movie_id)
            
            if not movie:
                try:
                    # Tenta editar a mensagem de erro
                    await query.edit_message_text("Desculpe, este filme não foi encontrado.")
                except Exception:
                    pass # Ignora se não puder editar
                return
            
            # Deleta a mensagem de ranking (a lista de botões)
            try:
                await query.delete_message() 
            except Exception:
                pass # Ignora se a msg for muito antiga

            bot_username = context.bot.username
            watch_url = f"https://t.me/{bot_username}?start=watch_{movie['movie_id']}"
            
            keyboard = [[
                InlineKeyboardButton("Assistir ⏯️", url=watch_url),
                InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=movie['title'])
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # --- CORREÇÃO DA LEGENDA ---
            # Remove o link invisível e usa .get() para segurança
            photo_caption = (
                f"🎬 *{movie['title']}* ({movie['year']})\n"
                f"🎭 *Gênero:* {movie.get('genre', 'N/A')}"
            )
            
            # --- CORREÇÃO DO ENVIO ---
            # Troca 'send_message' por 'send_photo'
            await context.bot.send_photo(
                chat_id=user_id,
                photo=movie['poster_url'], # URL do pôster vai aqui
                caption=photo_caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
        
    # --- LÓGICA DE VOLTAR AO MENU (Sem mudança) ---
    elif callback_data == "back_to_main":
        await query.answer()
        await start(update, context)
        
    # --- LÓGICA DE PAGAMENTO VIP (v3.1 - COM MENSAGENS NOVAS) ---
    elif callback_data == "main_vip":
        # --- BLOQUEIO ANTI-SPAM (THROTTLING) ---
        now = time.time()
        # Usamos uma chave diferente para PIX
        last_request = context.user_data.get('last_pix_request', 0)
        cooldown = 60 # 1 minuto para gerar outro PIX
        
        if now - last_request < cooldown:
            await query.answer(
                text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s para gerar um novo PIX.",
                show_alert=True
            )
            return
        # Não salvamos o 'now' aqui, só depois de passar no 'check_payment_status'
        # --- FIM DO BLOQUEIO ---

        async with DB_SEMAPHORE:
            await query.answer()
            if await db.is_user_vip(user_id):
                await query.edit_message_text("✨ Você já é um membro Premium! Aproveite todo o catálogo do Cine Pipoca.")
                return
            user_details = await db.get_user_details(user_id)
            active_payment_id = user_details.get('active_payment_id') if user_details else None
            if active_payment_id:
                await query.edit_message_text("⏳ Verificando seu pagamento anterior, aguarde...")
                status = await payments.check_payment_status(active_payment_id)
                if status == 'created':
                    await query.edit_message_text("Você já possui uma cobrança PIX pendente. Por favor, realize o pagamento ou aguarde expirar.")
                    return 
            await query.edit_message_text("⏳ Gerando sua cobrança PIX, aguarde...")
            vip_price = 4.00 # (Você pode mover isso para o config.py)
            payment_data = payments.create_pix_payment(user_id=user_id, amount=vip_price)
            if payment_data and payment_data.get("qr_code_base64"):
                payment_id = payment_data['payment_id']
                await db.set_user_active_payment_id(user_id, payment_id)
                base64_string = payment_data['qr_code_base64']
                if ',' in base64_string:
                    base64_string = base64_string.split(',')[1]
                qr_image_data = base64.b64decode(base64_string)
                qr_image_file = io.BytesIO(qr_image_data)
                pix_code = payment_data['qr_code_text']
                caption = (
                    f"🍿 **Seu Acesso Pipoca Premium está quase pronto!** ✨\n\n"
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
        # (Seu código v3.0 - Sem mudanças)
        async with DB_SEMAPHORE:
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
            status = await payments.check_payment_status(payment_id)
            if status == 'paid':
                await query.answer()
                await db.set_user_as_vip(user_id, duration_days=30)
                await db.clear_user_active_payment_id(user_id)
                await query.message.delete()
                await context.bot.send_message(
                    chat_id=user_id,
                    text="🎉 **Pagamento confirmado!** 🎉\n\n"
                        "Você agora é um membro Premium! Aproveite todo o nosso catálogo.",
                    parse_mode="Markdown"
                )
            else:
                await query.answer(
                    text=" Pagamento ainda não confirmado.\n\nA confirmação pode levar alguns instantes.",
                    show_alert=True
                )
            
    # --- v3.2: LÓGICA DE NAVEGAÇÃO DE EPISÓDIOS ---
    elif callback_data.startswith("ep_nav_"):
        async with DB_SEMAPHORE:
            try:
                await query.answer() # Responde ao clique
            except Exception as e:
                print(f"Ignorando erro de timeout no query.answer(): {e}")

            new_episode_id = int(callback_data.split('_')[2])
            
            # Gera o novo texto e botões para o episódio selecionado
            message_text, reply_markup = await _get_episode_details_message(new_episode_id)
            
            # =======================================================
            # === INÍCIO DA CORREÇÃO (DELETAR E ENVIAR NOVO) =======
            # =======================================================
            try:
                # 1. Deleta a mensagem do vídeo anterior (que continha o botão)
                await query.delete_message()
            except Exception as e:
                print(f"Erro ao deletar msg de vídeo na navegação: {e}")

            # 2. Envia uma NOVA mensagem com a seleção de áudio do próximo ep
            #    (Como você sugeriu, trocamos 'edit' por 'create'/'send')
            if reply_markup:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            else:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="Markdown"
                )
            # =======================================================
            # === FIM DA CORREÇÃO ===================================
            # =======================================================
            
    # --- LÓGICA FINAL (v3.2) - Enviar o vídeo da série ---
    elif callback_data.startswith("series_send_"):
        # --- BLOQUEIO ANTI-SPAM (THROTTLING) ---
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10 # 10 segundos
        
        if now - last_request < cooldown:
            await query.answer(
                text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s antes de outra ação.",
                show_alert=True
            )
            return
        context.user_data['last_action_time'] = now
        # --- FIM DO BLOQUEIO ---
        
        async with DB_SEMAPHORE:
            try:
                await query.answer() 
            except Exception as e:
                print(f"Ignorando erro de timeout no query.answer(): {e}")

            # --- INÍCIO DA VERIFICAÇÃO VIP 2 (COM NOVA MENSAGEM) ---
            user_id = query.from_user.id
            if not await db.is_user_vip(user_id):
                keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                message_text = (
                    f"Opa, {query.from_user.first_name}! 👋\n\n"
                    "Para continuar assistindo, você precisa do 🍿 **Acesso Pipoca Premium**!\n\n"
                    "✅ Libere **TODAS** as séries do catálogo.\n"
                    "✅ Assista filmes e séries sem interrupções.\n"
                    "✅ Ajude a manter o bot online com novos lançamentos!"
                )
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                return # <-- BLOQUEIA O RESTO DA FUNÇÃO
            # --- FIM DA VERIFICAÇÃO VIP 2 ---

            parts = callback_data.split('_')
            episode_id = int(parts[2])
            audio_type = parts[3]
            
            # ===============================================
            # === INÍCIO DA OTIMIZAÇÃO (v3.1) =====
            # ===============================================
            
            # 1. FAZ A NOVA CHAMADA ÚNICA
            full_details = await db.get_full_episode_details(episode_id)
            
            if not full_details:
                print(f"Erro: get_full_episode_details não encontrou dados para ep {episode_id}")
                await context.bot.send_message(chat_id=user_id, text="Erro ao carregar dados do episódio.")
                return

            # 2. EXTRAI OS DADOS ANINHADOS (com segurança)
            episode_data = full_details
            season_data = full_details.get('seasons')
            series_data = season_data.get('series') if season_data else None

            if not season_data or not series_data:
                print(f"Erro: Dados de temporada ou série ausentes no JOIN para ep {episode_id}")
                await context.bot.send_message(chat_id=user_id, text="Erro ao carregar dados da série.")
                return

            # 3. PEGA OS DADOS QUE PRECISAMOS
            series_title = series_data.get('title', 'Série')
            season_number = season_data.get('season_number', 0)
            series_id_for_related = series_data.get('id', 0) # Para o botão "Relacionados"
                
            # ===============================================
            # === FIM DA OTIMIZAÇÃO =========================
            # ===============================================

            file_id_to_send = None
            audio_text = "N/A"
            if audio_type == 'dub' and episode_data.get('dubbed_file_id'):
                file_id_to_send = episode_data['dubbed_file_id']
                audio_text = "(Dublado)"
            elif audio_type == 'sub' and episode_data.get('subtitled_file_id'):
                file_id_to_send = episode_data['subtitled_file_id']
                audio_text = "(Legendado)"
            
            if file_id_to_send:
                bot_username = context.bot.username
                
                # (Pegamos o 'episode_number' do objeto 'episode' que já temos)
                video_caption = (
                    f"📺 *{series_title}*\n"
                    f"S{season_number:02d}E{episode_data.get('episode_number', 0):02d}: *{episode_data.get('title', 'Episódio')}* {audio_text}\n\n"
                    f"---\n"
                    f"🍿 Assistido com @{bot_username}"
                )
                
                # ==============================================================
                # === INÍCIO DA MUDANÇA v3.2 (ADICIONA BOTÕES DE NAVEGAÇÃO) =====
                # ==============================================================

                # 1. Pega o ID da temporada (graças à correção no database.py)
                season_id = season_data.get('id')
                
                # 2. Busca todos os episódios da temporada
                all_episodes = []
                if season_id:
                    all_episodes, _ = await db.get_episodes_for_season(season_id)
                    
                # 3. Encontra o ep atual, anterior e próximo
                nav_row = []
                current_index = -1
                for i, ep in enumerate(all_episodes):
                    if ep['id'] == episode_id:
                        current_index = i
                        break
                
                if current_index != -1:
                    # Verifica se tem episódio anterior
                    if current_index > 0:
                        prev_episode_id = all_episodes[current_index - 1]['id']
                        nav_row.append(
                            InlineKeyboardButton("⏪ Ep. Anterior", callback_data=f"ep_nav_{prev_episode_id}")
                        )
                    
                    # Verifica se tem próximo episódio
                    if current_index < len(all_episodes) - 1:
                        next_episode_id = all_episodes[current_index + 1]['id']
                        nav_row.append(
                            InlineKeyboardButton("Próximo Ep. ⏩", callback_data=f"ep_nav_{next_episode_id}")
                        )

                # 4. Monta o teclado final com os botões de navegação
                keyboard = [
                    [ # Linha 1: Compartilhar e Relacionados
                        InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{series_id_for_related}_series")
                    ], [
                        InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=series_title)
                    ]
                ]
                
                if nav_row:
                    keyboard.append(nav_row) # Linha 2: Navegação (se houver)

                video_reply_markup = InlineKeyboardMarkup(keyboard)
                
                # ==============================================================
                # === FIM DA MUDANÇA v3.2 ======================================
                # ==============================================================
                
                # Deleta a mensagem de "Selecionar Áudio"
                try:
                    await query.delete_message()
                except Exception as e:
                    print(f"Não foi possível deletar a msg de áudio: {e}")

                await context.bot.send_video(
                    chat_id=query.from_user.id, 
                    video=file_id_to_send,
                    caption=video_caption,
                    parse_mode="Markdown",
                    reply_markup=video_reply_markup, # (Agora com os botões de navegação)
                    protect_content=True
                )
            else:
                await context.bot.send_message(
                    chat_id=query.from_user.id,
                    text="😔 Desculpe, esta versão do áudio não está disponível."
                )
        # --- FIM DO 'series_send_' ---

async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (v3.3) Lida com as buscas inline.
    CORRIGIDO o erro 'httpsa://' em todas as URLs.
    """
    async with DB_SEMAPHORE:
        query_text = update.inline_query.query
        results = []
        
        #
        # === ROTA 1: BUSCA DE EPISÓDIOS (ex: "season:1") ===
        #
        if query_text.startswith("season:"):
            try:
                # --- VERIFICAÇÃO VIP 1 ---
                user_id = update.inline_query.from_user.id
                if not await db.is_user_vip(user_id):
                    results.append(
                        InlineQueryResultArticle(
                            id="vip_required_series",
                            title="🍿 Acesso Pipoca Premium Necessário!",
                            description="Clique aqui para liberar todas as séries do catálogo.",
                            thumbnail_url="https://i.imgur.com/L3Ew4wt.png", # (Já estava correto aqui)
                            input_message_content=InputTextMessageContent(
                                message_text=(
                                    f"Ei {update.inline_query.from_user.first_name}! 👋\n\n"
                                    "Para maratonar esta e **todas as outras séries**, você precisa do 🍿 **Acesso Pipoca Premium**!\n\n"
                                    "Com ele, você libera todo o catálogo e ajuda nosso cinema a ficar sempre online."
                                ),
                                parse_mode="Markdown",
                                reply_markup=InlineKeyboardMarkup([[
                                    InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")
                                ]])
                            )
                        )
                    )
                    await update.inline_query.answer(results, cache_time=5, is_personal=True)
                    return
                # --- FIM DA VERIFICAÇÃO VIP 1 ---

                season_id = int(query_text.split(':')[1])
                
                # (episodes é a lista de todos os episódios da temporada)
                episodes, season = await db.get_episodes_for_season(season_id)
                
                if not episodes:
                    # (código de "nenhum episódio")
                    results.append(InlineQueryResultArticle(
                        id="no_eps_found",
                        title="Nenhum episódio encontrado",
                        description="Esta temporada parece não ter episódios cadastrados.",
                        input_message_content=InputTextMessageContent("Nenhum episódio encontrado.")
                    ))
                    await update.inline_query.answer(results, cache_time=10)
                    return

                series = await db.get_series_by_id(season['series_id'])
                series_title = series.get('title', 'Série') if series else 'Série'

                # --- CORREÇÃO v3.2: Gerar os botões AQUI ---
                for i, ep in enumerate(episodes): # (Usamos 'i' para saber o índice)
                    ep_title = ep.get('title', f"Episódio {ep['episode_number']}")
                    
                    # 1. Monta o Texto da Mensagem
                    message_text = (
                        f"📽️ *{series_title}*\n"
                        f"🎬 *Temporada:* {season['season_number']}\n"
                        f"🎯 *Episódio:* {ep['episode_number']} - {ep_title}\n"
                        f"--------------------\n"
                        f"Selecione o áudio:"
                    )

                    # 2. Monta os Botões de Áudio (Dub/Leg)
                    keyboard = []
                    audio_row = []
                    if ep.get('dubbed_file_id'):
                        audio_row.append(
                            InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"series_send_{ep['id']}_dub")
                        )
                    if ep.get('subtitled_file_id'):
                        audio_row.append(
                            InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"series_send_{ep['id']}_sub")
                        )
                    
                    if audio_row:
                        keyboard.append(audio_row)

                    # 3. (REMOVIDO) Botões de Navegação
                    # A navegação agora é adicionada na mensagem do VÍDEO.
                    
                    # 4. Adiciona o resultado
                    if audio_row: # Só mostra se tiver áudio
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        results.append(
                            InlineQueryResultArticle(
                                id=f"ep_{ep['id']}",
                                title=f"Episódio : {ep['episode_number']}",
                                description=f"🎬 {series_title} | {ep_title}",
                                # ===============================================
                                # === CORREÇÃO 1/5 ==============================
                                # ===============================================
                                thumbnail_url="https://i.imgur.com/TqA8sE8.png", 
                                reply_markup=reply_markup, # Botões de Áudio (sem nav)
                                input_message_content=InputTextMessageContent(
                                    message_text=message_text,
                                    parse_mode="Markdown"
                                )
                            )
                        )
                # --- FIM DA CORREÇÃO v3.2 ---
            
            except Exception as e:
                print(f"❌ Erro na busca inline de episódios: {e}")
                results.append(InlineQueryResultArticle(
                    id="error_eps",
                    title="Erro ao buscar episódios",
                    input_message_content=InputTextMessageContent("Ocorreu um erro ao processar sua solicitação.")
                ))
            
            await update.inline_query.answer(results, cache_time=10, is_personal=True)
            return
        
        #
        # === ROTA 2: BUSCA NORMAL (Filme/Série) ===
        #

        if not query_text:
            # (código do help_bubble)
            help_result = [
                InlineQueryResultArticle(
                    id="help_bubble",
                    title="Digite o nome do Filme ou Série",
                    description="Comece a digitar para que os resultados da busca apareçam aqui.",
                    # ===============================================
                    # === CORREÇÃO 2/5 ==============================
                    # ===============================================
                    thumbnail_url="https://cdn-icons-png.flaticon.com/512/3931/3931294.png",
                    input_message_content=InputTextMessageContent("👍")
                )
            ]
            await update.inline_query.answer(help_result, is_personal=True, cache_time=5)
            return
        
        results.append(
            InlineQueryResultArticle(
                # (código do static_help)
                id="static_help",
                title="Ajuda",
                description="Como usar o bot de busca",
                # ===============================================
                # === CORREÇÃO 3/5 ==============================
                # ===============================================
                thumbnail_url="https://cdn-icons-png.flaticon.com/512/189/189665.png", 
                input_message_content=InputTextMessageContent(
                    f"Para buscar, digite @{context.bot.username} e o nome do filme.\n\n"
                    "Para ver o menu principal, envie o comando /start."
                )
            )
        )
        
        movies_from_db = await db.search_movies(query_text, limit=5)
        series_from_db = await db.search_series_by_title(query_text, limit=5)
        bot_username = context.bot.username

        # 3. Processa os resultados de FILMES (Sem mudança)
        for movie in movies_from_db:
            # (código dos filmes)
            if movie.get('poster_url'):
                # ===============================================
                # === CORREÇÃO 4/5 ==============================
                # ===============================================
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

        # 4. Processa os resultados de SÉRIES (v3.0 - Sem mudança)
        for series in series_from_db:
            # (código das séries)
            # ===============================================
            # === CORREÇÃO 5/5 ==============================
            # ===============================================
            poster_url_grande = series.get('poster_url', 'https://via.placeholder.com/500x750.png?text=Sem+Pôster')
            poster_url_pequeno = poster_url_grande.replace('/w500/', '/w92/')
            seasons = await db.get_seasons_for_series(series['series_id'])
            photo_caption = (
                f"📺 *{series['title']}*\n\n"
                f"🗓️ *Ano:* {series['year']}\n"
                f"🎭 *Gênero:* {series.get('genre', 'N/A')}\n\n"
                f"📝 *Sinopse:* {series.get('description', 'N/A')}\n\n"
                "---\n"
                "Selecione a temporada desejada abaixo:"
            )
            keyboard = []
            if seasons:
                for season in seasons:
                    keyboard.append([
                        InlineKeyboardButton(
                            f"▶️ Temporada {season['season_number']}",
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
                    reply_markup=reply_markup
                )
            )

        await update.inline_query.answer(results, cache_time=30)

async def watch_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    (v3.3) Lida com o comando /watch OU é chamada pela função start.
    AGORA COM BOTÕES DE ÁUDIO DINÂMICOS.
    """
    async with DB_SEMAPHORE:
        if update.message:
            try: await update.message.delete()
            except Exception: pass
        if not context.args: return
        movie_id = context.args[0]
        user_id = update.effective_user.id
        
        # (Verificação VIP - já está correta)
        if not await db.is_user_vip(user_id):
            keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            message_text = (
                f"Opa, {update.effective_user.first_name}! 👋\n\n"
                "Para assistir filmes, você precisa do 🍿 **Acesso Pipoca Premium**!\n\n"
                "✅ Libere **TODOS** os filmes e séries do catálogo.\n"
                "✅ Ajude a manter o bot online com novos lançamentos!"
            )
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return

        # (Usuário é VIP, continua)
        movie = await db.get_movie_by_id(movie_id)
        
        if movie and movie.get('poster_url'):
            
            # --- INÍCIO DA MUDANÇA (BOTÕES DINÂMICOS) ---
            
            # 1. Cria uma lista vazia para os botões de áudio
            audio_buttons = []
            
            # 2. Verifica se o 'dubbed_file_id' existe (não é None, não é "")
            if movie.get('dubbed_file_id'):
                audio_buttons.append(
                    InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"play_{movie_id}_dub")
                )
            
            # 3. Verifica se o 'subtitled_file_id' existe
            if movie.get('subtitled_file_id'):
                audio_buttons.append(
                    InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"play_{movie_id}_sub")
                )

            # 4. Monta a legenda principal
            caption = (
                f"🎬 *{movie['title']}* ({movie['year']})\n\n"
                f"🎭 *Gênero:* {movie.get('genre', 'N/A')}\n\n"
                f"📝 *Sinopse:* {movie.get('description', 'N/A')}\n\n"
                "---\n"
            )

            # 5. Monta o teclado (reply_markup)
            keyboard = []
            reply_markup = None # Começa como Nulo
            
            if audio_buttons:
                # Se encontrou botões (Dub ou Leg), adiciona-os ao teclado
                caption += "Selecione o áudio desejado abaixo:"
                keyboard.append(audio_buttons) # Adiciona a linha de botões
                reply_markup = InlineKeyboardMarkup(keyboard)
            else:
                # Se não encontrou NENHUMA versão, avisa o usuário
                caption += "😔 *Este filme está no catálogo, mas ainda estamos aguardando os arquivos de vídeo.*"
                # reply_markup continua Nulo (sem botões)

            # --- FIM DA MUDANÇA ---
                
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=movie['poster_url'],
                caption=caption,
                parse_mode="Markdown",
                reply_markup=reply_markup # (Agora é dinâmico)
            )
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text="Filme não encontrado ou sem pôster disponível.")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código v3.0 - Sem mudanças)
    async with DB_SEMAPHORE:
        user_state = context.user_data.get('state')
        if user_state == 'awaiting_request':
            del context.user_data['state']
            requested_title = update.message.text
            user_id = update.effective_user.id
            if await db.add_request(user_id=user_id, title=requested_title):
                await update.message.reply_text(
                    f"✅ Obrigado! Sua sugestão \"{requested_title}\" foi registrada e será analisada.\n\n"
                    "Se aprovada, estará disponível em nosso catálogo em até 24 horas!"
                )
            else:
                await update.message.reply_text("😕 Desculpe, ocorreu um erro ao salvar seu pedido. Tente novamente mais tarde.")

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código v3.0 - Sem mudanças)
    if 'state' in context.user_data:
        del context.user_data['state']
        await update.message.reply_text("Operação cancelada.")
    else:
        await update.message.reply_text("Não há nenhuma operação para cancelar.")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Seu código v3.0 - Sem mudanças, mas com texto customizado)
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

# --- Definição dos Handlers (Sem mudança) ---
start_handler = CommandHandler("start", start)
button_click_handler = CallbackQueryHandler(button_handler)
inline_search_handler = InlineQueryHandler(inline_query_handler)
watch_handler = CommandHandler("watch", watch_command_handler)
text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler)
cancel_command_handler = CommandHandler("cancelar", cancel_handler)
help_command_handler = CommandHandler("help", help_handler)
request_command_handler = CommandHandler("pedir", request_command_handler)
