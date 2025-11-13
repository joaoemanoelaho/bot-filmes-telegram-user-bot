#
# NOME DO ARQUIVO: handlers_user.py (VERSÃO 5.14 - WEBHOOK FINAL)
#
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    InlineQueryResultArticle, InputTextMessageContent,
    InlineQueryResultPhoto, InputMediaPhoto
)
from telegram.ext import CommandHandler, ContextTypes, CallbackQueryHandler, InlineQueryHandler, MessageHandler, filters
from telegram.error import NetworkError, Forbidden, RetryAfter, BadRequest
import database as db
from config import ADMIN_IDS, STORAGE_CHANNEL_ID, STORAGE_CHANNEL_ID_SERIES
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
import asyncio


current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
sys.path.insert(0, parent_dir)

# =================================================================
# === GERENCIADOR DE FILA (SEMAPHORE) ===
# =================================================================
DB_SEMAPHORE = asyncio.Semaphore(20)

async def safe_call(obj, method_name, *args, **kwargs):
    """Evita crash caso o objeto ou método estejam ausentes."""
    if not obj:
        # print(f"[WARN] safe_call ignorado: {method_name} chamado com None")
        return None
    method = getattr(obj, method_name, None)
    if not method:
        # print(f"[WARN] safe_call ignorado: {method_name} inexistente em {type(obj)}")
        return None
    try:
        # Verifica se é um método assíncrono
        if asyncio.iscoroutinefunction(method):
            # Se for, retorna a corrotina (deve ser chamada com await)
            return await method(*args, **kwargs)
        else:
            # Se for síncrono, chama diretamente
            return method(*args, **kwargs)
    except Exception as e:
        print(f"[WARN] Erro em safe_call({method_name}): {e}")

# =================================================================
# === FUNÇÃO HELPER (PARA NAVEGAÇÃO) ===
# =================================================================
# (Helper da v5.11 - Otimizado)
async def _get_episode_details_message(episode_id: int, bot_username: str, delete_msg_id: int = None) -> tuple[str, InlineKeyboardMarkup]:
    """Prepara a mensagem e os botões de áudio (com URL) para um episódio."""
    try:
        # OTIMIZAÇÃO: 3 chamadas ao DB -> 1 chamada
        details = await db.get_full_episode_details(episode_id)
        if not details:
            return ("Erro: Episódio não encontrado.", None)

        episode = details
        season = details.get('seasons')
        series = season.get('series') if season else None

        if not season or not series:
             return ("Erro: Dados da temporada ou série ausentes.", None)

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

        if episode.get('dubbed_file_id'):
            payload = f"watch_ep_{episode['id']}_dub"
            if delete_msg_id:
                payload += f"_del_{delete_msg_id}"
            url = f"https://t.me/{bot_username}?start={payload}"
            audio_row.append(
                InlineKeyboardButton("Dublado 🇧🇷", url=url)
            )
        if episode.get('subtitled_file_id'):
            payload = f"watch_ep_{episode['id']}_sub"
            if delete_msg_id:
                payload += f"_del_{delete_msg_id}"
            url = f"https://t.me/{bot_username}?start={payload}"
            audio_row.append(
                InlineKeyboardButton("Legendado 🇺🇸", url=url)
            )

        if audio_row:
             keyboard.append(audio_row)

        return (message_text, InlineKeyboardMarkup(keyboard))

    except Exception as e:
        print(f"Erro em _get_episode_details_message: {e}")
        import traceback
        traceback.print_exc()
        return (f"Erro ao carregar detalhes do episódio: {e}", None)

# =================================================================
# === HANDLERS PRINCIPAIS (COM PROTEÇÃO) ===
# =================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Função da v5.10, mas com a lógica de navegação otimizada)
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

            if not is_query:
                try:
                    await message_to_reply.delete()
                except Exception as e:
                    print(f"[WARN] Não foi possível deletar a msg /start do usuário: {e}")

            if payload.startswith("watch_ep_"):
                try:
                    parts = payload.split('_')
                    episode_id = int(parts[2])
                    audio_type = parts[3]
                    msg_to_delete_id = None
                    if len(parts) > 5 and parts[4] == 'del':
                        msg_to_delete_id = int(parts[5])

                    if msg_to_delete_id:
                        try:
                            await context.bot.delete_message(chat_id=user.id, message_id=msg_to_delete_id)
                        except Exception as e:
                            print(f"[WARN] Não foi possível deletar msg de áudio {msg_to_delete_id}: {e}")

                    await db.get_or_create_user(user_id=user.id, first_name=user.first_name)

                    if not await db.is_user_vip(user.id):
                        await context.bot.send_message(
                            chat_id=user.id,
                            text=(
                                f"Opa, {user.first_name}! 👋\n\n"
                                "Para continuar assistindo, você precisa do 🍿 **Acesso Pipoca Premium**!\n\n"
                                "Clique no botão abaixo para ver as opções."
                            ),
                            parse_mode="Markdown"
                        )
                        class FakeQuery:
                            def __init__(self, usr, msg):
                                self.from_user = usr
                                self.message = msg
                                self.data = "main_vip"
                            async def answer(self): pass
                            async def edit_message_text(self, *a, **kw): await context.bot.send_message(user.id, *a, **kw)
                            async def delete_message(self): pass
                            async def reply_photo(self, *a, **kw): await context.bot.send_photo(user.id, *a, **kw)
                        class FakeUpdate:
                            def __init__(self, usr, msg):
                                self.effective_user = usr
                                self.callback_query = FakeQuery(usr, msg)
                                self.message = msg
                        await button_handler(FakeUpdate(user, message_to_reply), context)
                        return

                    status_msg = await context.bot.send_message(chat_id=user.id, text="⏳ Carregando seu episódio...")

                    full_details = await db.get_full_episode_details(episode_id)
                    if not full_details:
                        await status_msg.edit_text("Erro ao carregar dados do episódio.")
                        return

                    episode_data = full_details
                    season_data = full_details.get('seasons')
                    series_data = season_data.get('series') if season_data else None

                    if not season_data or not series_data:
                        await status_msg.edit_text("Erro ao carregar dados da série.")
                        return

                    file_id_to_send = None
                    audio_text = "N/A"
                    msg_id_to_copy = None
                    if audio_type == 'dub' and episode_data.get('dubbed_file_id'):
                        file_id_to_send = episode_data['dubbed_file_id']
                        msg_id_to_copy = episode_data.get('dubbed_msg_id')
                        audio_text = "(Dublado)"
                    elif audio_type == 'sub' and episode_data.get('subtitled_file_id'):
                        file_id_to_send = episode_data['subtitled_file_id']
                        msg_id_to_copy = episode_data.get('subtitled_msg_id')
                        audio_text = "(Legendado)"

                    if file_id_to_send:
                        bot_username = context.bot.username
                        series_title = series_data.get('title', 'Série')
                        season_number = season_data.get('season_number', 0)
                        series_id_for_related = series_data.get('id', 0)

                        video_caption = (
                            f"📺 *{series_title}*\n"
                            f"S{season_number:02d}E{episode_data.get('episode_number', 0):02d}: *{episode_data.get('title', 'Episódio')}* {audio_text}\n\n"
                            f"---\n"
                            f"🍿 Assistido com @{bot_username}"
                        )

                        # --- CORREÇÃO (v5.13): Lógica de navegação otimizada ---
                        season_id = season_data.get('id')
                        current_ep_num = episode_data.get('episode_number', 0)

                        # Faz 2 buscas rápidas em paralelo, em vez de 1 lenta
                        prev_ep, next_ep = await asyncio.gather(
                            db.get_neighbor_episode(season_id, current_ep_num, 'previous'),
                            db.get_neighbor_episode(season_id, current_ep_num, 'next')
                        )

                        nav_row = []
                        if prev_ep:
                            nav_row.append(
                                InlineKeyboardButton("⏪ Ep. Anterior", callback_data=f"ep_nav_{prev_ep['id']}")
                            )
                        if next_ep:
                            nav_row.append(
                                InlineKeyboardButton("Próximo Ep. ⏩", callback_data=f"ep_nav_{next_ep['id']}")
                            )
                        # --- FIM DA CORREÇÃO ---

                        keyboard = [
                            [ InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{series_id_for_related}_series") ],
                            [ InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"ep_card:{episode_id}") ]
                        ]
                        if nav_row:
                            keyboard.append(nav_row)
                        video_reply_markup = InlineKeyboardMarkup(keyboard)

                        try:
                            # PLANO A: Tenta enviar o file_id salvo
                            print(f"[Plano A] Tentando enviar Ep {episode_id} com file_id: {file_id_to_send[:20]}...")
                            await context.bot.send_video(
                                chat_id=user.id,
                                video=file_id_to_send,
                                caption=video_caption,
                                parse_mode="Markdown",
                                reply_markup=video_reply_markup,
                                protect_content=True
                            )
                        except BadRequest as e:
                            error_text = str(e).lower() # Normaliza o erro
                            print(f"--- DEBUG PLANO B (EPISÓDEO) ---")
                            print(f"1. Erro recebido: {error_text}")
                            print(f"2. Condição 1 ('wrong file'): {'wrong file id' in error_text or 'wrong file identifier' in error_text}")
                            print(f"3. Condição 2 (msg_id): {msg_id_to_copy}")
                            print(f"4. Condição 3 (channel_id): {STORAGE_CHANNEL_ID_SERIES}")

                            # Agora verifica as duas mensagens de erro:
                            if ("wrong file id" in error_text or "wrong file identifier" in error_text) and msg_id_to_copy and STORAGE_CHANNEL_ID_SERIES:
                                # PLANO B: O file_id está quebrado, mas temos o msg_id
                                print(f"🚨 [Plano B] File ID quebrado para Ep {episode_id}. Iniciando Auto-Cura.")
                                print(f"   Copiando msg {msg_id_to_copy} do canal {STORAGE_CHANNEL_ID_SERIES}")
                                
                                try:
                                    # 1. Copia a mensagem do canal de armazenamento (o bot PRECISA ser admin lá)
                                    copied_message = await context.bot.copy_message(
                                        chat_id=user.id,
                                        from_chat_id=STORAGE_CHANNEL_ID_SERIES,
                                        message_id=msg_id_to_copy,
                                        protect_content=True # Protege a cópia
                                    )
                                    
                                    # 2. Pega o NOVO file_id válido
                                    new_file_id = copied_message.video.file_id
                                    print(f"   ✅ Sucesso! Novo file_id: {new_file_id[:20]}...")
                                    
                                    # 3. Salva o novo file_id no banco para o futuro
                                    await db.update_episode_file_id_only(episode_id, new_file_id, audio_type)
                                    
                                    # 4. Adiciona o caption e botões à mensagem copiada
                                    await copied_message.edit_caption(
                                        caption=video_caption,
                                        parse_mode="Markdown",
                                        reply_markup=video_reply_markup
                                    )
                                    
                                except Exception as e_inner:
                                    print(f"   ❌ FALHA no Plano B: {e_inner}")
                                    await status_msg.edit_text("😔 Desculpe, o `file_id` deste episódio quebrou e não consegui repará-lo automaticamente. Avise um admin.")
                            else:
                                # O erro não é "Wrong file_id" ou não temos msg_id para copiar
                                print(f"Erro (sem Plano B): {e}")
                                await status_msg.edit_text(f"😔 Ocorreu um erro inesperado ao enviar o vídeo: {e}")
                        except Exception as e:
                             print(f"Erro geral ao enviar vídeo: {e}")
                             await status_msg.edit_text(f"😔 Ocorreu um erro geral ao enviar o vídeo: {e}")
                        # --- FIM DA MUDANÇA (v6.0) ---

                        await status_msg.delete()
                    else:
                        await status_msg.edit_text("😔 Desculpe, esta versão do áudio não está disponível.")
                except Exception as e:
                    print(f"Erro ao processar payload watch_ep_: {e}")
                    import traceback
                    traceback.print_exc() # Adiciona mais detalhes do erro
                    await context.bot.send_message(chat_id=user.id, text="Erro ao carregar episódio.")
                return
            
            elif payload.startswith("show_ep_"):
                try:
                    episode_id = int(payload.split('_')[2])

                    await db.get_or_create_user(user_id=user.id, first_name=user.first_name)

                    # --- VERIFICAÇÃO DE VIP ---
                    if not await db.is_user_vip(user.id):
                        await context.bot.send_message(
                            chat_id=user.id,
                            text=(
                                f"Opa, {user.first_name}! 👋\n\n"
                                "Para continuar assistindo, você precisa do 🍿 **Acesso Pipoca Premium**!\n\n"
                                "Clique no botão abaixo para ver as opções."
                            ),
                            parse_mode="Markdown"
                        )
                        # Simula um clique no botão main_vip para mostrar o menu de pagamento
                        class FakeQuery:
                            def __init__(self, usr, msg):
                                self.from_user = usr
                                self.message = msg
                                self.data = "main_vip"
                            async def answer(self): pass
                            async def edit_message_text(self, *a, **kw): await context.bot.send_message(user.id, *a, **kw)
                            async def delete_message(self): pass
                            async def reply_photo(self, *a, **kw): await context.bot.send_photo(user.id, *a, **kw)
                        class FakeUpdate:
                            def __init__(self, usr, msg):
                                self.effective_user = usr
                                self.callback_query = FakeQuery(usr, msg)
                                self.message = msg
                        await button_handler(FakeUpdate(user, message_to_reply), context)
                        return
                    # --- FIM DA VERIFICAÇÃO ---

                    status_msg = await context.bot.send_message(chat_id=user.id, text="⏳ Carregando seu episódio...")
                    bot_username = context.bot.username

                    # Chama a função helper que gera a seleção de áudio
                    message_text, reply_markup = await _get_episode_details_message(
                        episode_id,
                        bot_username,
                        delete_msg_id=status_msg.message_id
                    )

                    if reply_markup:
                        await status_msg.edit_text(
                            text=message_text,
                            reply_markup=reply_markup,
                            parse_mode="Markdown"
                        )
                    else:
                        await status_msg.edit_text(text=message_text.replace("Selecione o áudio (o bot irá te chamar no privado):", "Erro: Nenhum áudio encontrado para este episódio."))

                except Exception as e:
                    print(f"Erro ao processar payload show_ep_: {e}")
                    import traceback
                    traceback.print_exc()
                    await context.bot.send_message(chat_id=user.id, text="Erro ao carregar detalhes do episódio.")
                return

            elif payload.startswith("watch_"):
                context.args = [payload.split('_')[1]]
                await watch_command_handler(update, context, message_deletada=True)
                return

        await db.get_or_create_user(user_id=user.id, first_name=user.first_name)

        keyboard = [
            [InlineKeyboardButton("Buscar Mídia 🔎", switch_inline_query_current_chat=""),],
            [InlineKeyboardButton("Adquirir VIP 🚀", callback_data="main_vip")],
            [InlineKeyboardButton("Pedir Filme/Série 💡", callback_data="main_request"),
             InlineKeyboardButton("Top Mídia 🏆", callback_data="main_top")]
        ]
        main_menu = InlineKeyboardMarkup(keyboard)
        community_link = "https://t.me/+-v5nIbZ93J43MmQ5"
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
                    try:
                        await message_to_reply.delete()
                    except: pass
        else:
            await message_to_reply.reply_html(welcome_text, reply_markup=main_menu)

async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Função sem alteração)
    async with DB_SEMAPHORE:
        user_id = update.effective_user.id

        await db.get_or_create_user(user_id=update.effective_user.id, first_name=update.effective_user.first_name)

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

        context.user_data['state'] = 'awaiting_request'
        await update.message.reply_text(
            "Qual filme ou série você gostaria de ver no catálogo?\n\n"
            "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Função da v5.10 - COM MUDANÇAS)
    query = update.callback_query

    if not query:
        if isinstance(update, object) and hasattr(update, 'callback_query'):
             query = update.callback_query
        else:
             print("[ERRO] button_handler recebeu um update inválido.")
             return

    callback_data = query.data
    user_id = query.from_user.id
    print(f"Usuário {user_id} clicou no botão: {callback_data}")

    if callback_data.startswith("play_"):
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10

        if now - last_request < cooldown:
            # --- MUDANÇA ---
            await safe_call(query, "answer",
                text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s antes de outra ação.",
                show_alert=True
            )
            # --- FIM DA MUDANÇA ---
            return

        async with DB_SEMAPHORE:
            # --- MUDANÇA ---
            await safe_call(query, "answer")
            _, movie_id_str, audio_choice = callback_data.split('_')
            movie_id = int(movie_id_str)
            await safe_call(query, "edit_message_caption", caption="⏳ Carregando seu filme, por favor aguarde...")
            # --- FIM DA MUDANÇA ---

            movie = await db.get_movie_by_id(movie_id)

            if not movie:
                # --- MUDANÇA ---
                await safe_call(query, "edit_message_caption", caption="Erro: Filme não encontrado.")
                # --- FIM DA MUDANÇA ---
                return
            
            file_id_to_send = None
            msg_id_to_copy = None
            if audio_choice == "dub":
                file_id_to_send = movie.get('dubbed_file_id')
                msg_id_to_copy = movie.get('dubbed_msg_id')
            else:
                file_id_to_send = movie.get('subtitled_file_id')
                msg_id_to_copy = movie.get('subtitled_msg_id')

            if file_id_to_send:
                # --- MUDANÇA ---
                await safe_call(query, "delete_message")
                # --- FIM DA MUDANÇA ---
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

                try:
                    # PLANO A: Tenta enviar o file_id salvo
                    print(f"[Plano A] Tentando enviar Filme {movie_id} com file_id: {file_id_to_send[:20]}...")
                    await context.bot.send_video(
                        chat_id=query.message.chat.id,
                        video=file_id_to_send,
                        caption=video_caption,
                        parse_mode="Markdown",
                        reply_markup=video_reply_markup,
                        protect_content=True
                    )
                    
                except BadRequest as e:
                    error_text = str(e).lower()
                    print(f"--- DEBUG PLANO B (FILME) ---")
                    print(f"1. Erro recebido: {error_text}")
                    print(f"2. Condição 1 ('wrong file'): {'wrong file id' in error_text or 'wrong file identifier' in error_text}")
                    print(f"3. Condição 2 (msg_id): {msg_id_to_copy}")
                    print(f"4. Condição 3 (channel_id): {STORAGE_CHANNEL_ID}")
                    
                    # Agora verifica as duas mensagens de erro:
                    if ("wrong file id" in error_text or "wrong file identifier" in error_text) and msg_id_to_copy and STORAGE_CHANNEL_ID:
                        # PLANO B: O file_id está quebrado, mas temos o msg_id
                        print(f"🚨 [Plano B] File ID quebrado para Filme {movie_id}. Iniciando Auto-Cura.")
                        print(f"   Copiando msg {msg_id_to_copy} do canal {STORAGE_CHANNEL_ID}")
                        
                        try:
                            # 1. Copia a mensagem do canal de armazenamento
                            copied_message = await context.bot.copy_message(
                                chat_id=query.message.chat.id,
                                from_chat_id=STORAGE_CHANNEL_ID,
                                message_id=msg_id_to_copy,
                                protect_content=True
                            )
                            
                            # 2. Pega o NOVO file_id válido
                            new_file_id = copied_message.video.file_id
                            print(f"   ✅ Sucesso! Novo file_id: {new_file_id[:20]}...")
                            
                            # 3. Salva o novo file_id no banco
                            await db.update_movie_file_id_only(movie_id, new_file_id, audio_choice)
                            
                            # 4. Adiciona o caption e botões
                            await copied_message.edit_caption(
                                caption=video_caption,
                                parse_mode="Markdown",
                                reply_markup=video_reply_markup
                            )
                            
                        except Exception as e_inner:
                            print(f"   ❌ FALHA no Plano B: {e_inner}")
                            # Manda uma msg de erro temporária
                            await context.bot.send_message(chat_id=query.message.chat.id, text="😔 Desculpe, o `file_id` deste filme quebrou e não consegui repará-lo automaticamente. Avise um admin.")
                    else:
                        print(f"Erro (sem Plano B): {e}")
                        await context.bot.send_message(chat_id=query.message.chat.id, text=f"😔 Ocorreu um erro inesperado ao enviar o vídeo: {e}")
                except Exception as e:
                    print(f"Erro geral ao enviar vídeo: {e}")
                    await context.bot.send_message(chat_id=query.message.chat.id, text=f"😔 Ocorreu um erro geral ao enviar o vídeo: {e}")

                await db.log_movie_view(movie_id=movie_id, user_id=user_id)
                context.user_data['last_action_time'] = time.time()
            else:
                # --- MUDANÇA ---
                await safe_call(query, "edit_message_caption", caption="😔 Desculpe, esta versão do filme não está disponível.")
                # --- FIM DA MUDANÇA ---

    elif callback_data.startswith("related_"):
        now = time.time()
        last_request = context.user_data.get('last_action_time', 0)
        cooldown = 10

        if now - last_request < cooldown:
            # --- MUDANÇA ---
            await safe_call(query, "answer",
                text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s antes de outra ação.",
                show_alert=True
            )
            # --- FIM DA MUDANÇA ---
            return

        async with DB_SEMAPHORE:
            # --- MUDANÇA ---
            await safe_call(query, "answer")
            # --- FIM DA MUDANÇA ---
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

            context.user_data['last_action_time'] = time.time()

    elif callback_data == "main_request":
        async with DB_SEMAPHORE:
            user_id = query.from_user.id

            await db.get_or_create_user(user_id=update.effective_user.id, first_name=update.effective_user.first_name)

            if not await db.is_user_vip(user_id):
                # --- MUDANÇA ---
                await safe_call(query, "answer")
                # --- FIM DA MUDANÇA ---
                keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")],
                            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                message_text = (
                    f"Opa, {query.from_user.first_name}! 👋\n\n"
                    "Esta é uma função exclusiva do 🍿 **Acesso Pipoca Premium**!\n\n"
                    "Assine para poder pedir filmes/séries e ver o que está em alta."
                )
                try:
                    # --- MUDANÇA ---
                    await safe_call(query, "edit_message_text",
                        text=message_text,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                    # --- FIM DA MUDANÇA ---
                except Exception: pass
                return

            # --- MUDANÇA ---
            await safe_call(query, "answer")
            context.user_data['state'] = 'awaiting_request'
            await safe_call(query, "edit_message_text",
                text="Qual filme ou série você gostaria de ver no catálogo?\n\n"
                     "Por favor, envie o nome completo. Para cancelar, digite /cancelar."
            )
            # --- FIM DA MUDANÇA ---

    elif callback_data == "main_top":
        async with DB_SEMAPHORE:
            user_id = query.from_user.id

            await db.get_or_create_user(user_id=update.effective_user.id, first_name=update.effective_user.first_name)

            if not await db.is_user_vip(user_id):
                # --- MUDANÇA ---
                await safe_call(query, "answer")
                # --- FIM DA MUDANÇA ---
                keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")],
                            [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                message_text = (
                    f"Opa, {query.from_user.first_name}! 👋\n\n"
                    "Esta é uma função exclusiva do 🍿 **Acesso Pipoca Premium**!\n\n"
                    "Assine para poder pedir filmes/séries e ver o que está em alta."
                )
                try:
                    # --- MUDANÇA ---
                    await safe_call(query, "edit_message_text",
                        text=message_text,
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                    # --- FIM DA MUDANÇA ---
                except Exception: pass
                return

            # --- MUDANÇA ---
            await safe_call(query, "answer")
            # --- FIM DA MUDANÇA ---
            keyboard = [
                [InlineKeyboardButton("🏆 Top Semana", callback_data="top_7")],
                [InlineKeyboardButton("🗓️ Top Mês", callback_data="top_30")],
                [InlineKeyboardButton("🌎 Top Geral", callback_data="top_0")],
                [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            # --- MUDANÇA ---
            await safe_call(query, "edit_message_text", text="Selecione o período do ranking que deseja visualizar:", reply_markup=reply_markup)
            # --- FIM DA MUDANÇA ---

    elif callback_data.startswith("top_"):
        async with DB_SEMAPHORE:
            # --- MUDANÇA ---
            await safe_call(query, "answer")
            # --- FIM DA MUDANÇA ---
            period_days = int(callback_data.split('_')[1])
            period_text = "Geral (Todos os Tempos)"
            if period_days == 7: period_text = "da Semana"
            if period_days == 30: period_text = "do Mês"
            # --- MUDANÇA ---
            await safe_call(query, "edit_message_text", text=f"🏆 Buscando o Top 10 {period_text}, aguarde...")
            # --- FIM DA MUDANÇA ---

            trending_movies = await db.get_trending(period_days=period_days)

            if not trending_movies:
                # --- MUDANÇA ---
                await safe_call(query, "edit_message_text", text="Ainda não há dados suficientes para gerar um ranking.")
                # --- FIM DA MUDANÇA ---
                return

            keyboard = []
            for movie in trending_movies:
                button = [InlineKeyboardButton(f"{movie['title']} ({movie['year']})", callback_data=f"show_card_{movie['movie_id']}")]
                keyboard.append(button)
            keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="main_top")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            message_text = f"🏆 **Top 10 {period_text}** 🏆\n\nClique em um filme abaixo para ver mais detalhes:"
            # --- MUDANÇA ---
            await safe_call(query, "edit_message_text", text=message_text, parse_mode="Markdown", reply_markup=reply_markup)
            # --- FIM DA MUDANÇA ---

    elif callback_data.startswith("show_card_"):
        async with DB_SEMAPHORE:
            # --- MUDANÇA ---
            await safe_call(query, "answer")
            # --- FIM DA MUDANÇA ---
            movie_id = int(callback_data.split('_')[2])

            movie = await db.get_movie_by_id(movie_id)

            if not movie:
                try:
                    # --- MUDANÇA ---
                    await safe_call(query, "edit_message_text", text="Desculpe, este filme não foi encontrado.")
                    # --- FIM DA MUDANÇA ---
                except Exception:
                    pass
                return

            try:
                # --- MUDANÇA ---
                await safe_call(query, "delete_message")
                # --- FIM DA MUDANÇA ---
            except Exception:
                pass

            bot_username = context.bot.username
            watch_url = f"https://t.me/{bot_username}?start=watch_{movie['movie_id']}"

            keyboard = [[
                InlineKeyboardButton("Assistir ⏯️", url=watch_url),
                InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=movie['title'])
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            photo_caption = (
                f"🎬 *{movie['title']}* ({movie['year']})\n"
                f"🎭 *Gênero:* {movie.get('genre', 'N/A')}"
            )

            await context.bot.send_photo(
                chat_id=user_id,
                photo=movie['poster_url'],
                caption=photo_caption,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )

    elif callback_data == "back_to_main":
        # --- MUDANÇA ---
        await safe_call(query, "answer")
        # --- FIM DA MUDANÇA ---
        await start(update, context)

    elif callback_data == "main_vip":
        now = time.time()
        last_request = context.user_data.get('last_pix_request', 0)
        cooldown = 60

        if now - last_request < cooldown:
            # --- MUDANÇA ---
            await safe_call(query, "answer",
                text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s para gerar um novo PIX.",
                show_alert=True
            )
            # --- FIM DA MUDANÇA ---
            return

        async with DB_SEMAPHORE:
            await safe_call(query, "answer")

            await db.get_or_create_user(user_id=user_id, first_name=query.from_user.first_name)

            if await db.is_user_vip(user_id):
                try:
                    # --- MUDANÇA ---
                    await safe_call(query, "edit_message_text", text="✨ Você já é um membro Premium! Aproveite todo o catálogo do Cine Pipoca.")
                    # --- FIM DA MUDANÇA ---
                except Exception: pass
                return

            user_details = await db.get_user_details(user_id)
            active_payment_id = user_details.get('active_payment_id') if user_details else None

            if active_payment_id:
                # Um ID está preso. Vamos verificar o status UMA VEZ.
                await safe_call(query, "edit_message_text", text="⏳ Verificando pagamento pendente...")
                status = await payments.check_payment_status(active_payment_id)

                if status == "paid":
                    # O Webhook falhou, mas o pagamento está OK. Libera manualmente.
                    await db.set_user_as_vip(user_id, duration_days=7) # VIP por 7 dias
                    await db.clear_user_active_payment_id(user_id)
                    print(f"✅ VIP ATIVADO (via verificação manual) para UserID: {user_id}")
                    await safe_call(query, "edit_message_text", text="🎉 Pagamento confirmado! Seu acesso Premium está ativo.")
                    return
                
                elif status == "created":
                    # O PIX ainda está pendente. Agora sim, mostramos o erro da sua imagem.
                    await safe_call(query, "edit_message_text", text="⚠️ Você já possui uma cobrança PIX pendente.\n\nPor favor, realize o pagamento ou aguarde alguns minutos até que ela expire para gerar uma nova.")
                    return

                elif status in ["expired", "canceled", "not_found"]:
                    # O PIX expirou ou falhou. Limpa o ID e deixa gerar um novo.
                    print(f"PIX {active_payment_id} expirado/cancelado. Limpando...")
                    await db.clear_user_active_payment_id(user_id)
                    # Deixa o código continuar para gerar um novo PIX
                
                else:
                    # Erro na API ou status desconhecido
                    await safe_call(query, "edit_message_text", text="😕 Erro ao verificar seu pagamento anterior. Tente novamente em 1 minuto.")
                    return

            context.user_data['last_pix_request'] = time.time()

            # --- MUDANÇA ---
            await safe_call(query, "edit_message_text", text="⏳ Gerando sua cobrança PIX, aguarde...")
            # --- FIM DA MUDANÇA ---
            vip_price = 0.50  # Preço do VIP

            payment_data = await payments.create_pix_payment(user_id=user_id, amount=vip_price)

            if payment_data and payment_data.get("qr_code_base64"):
                payment_id = payment_data['payment_id']
                base64_string = payment_data['qr_code_base64']
                if ',' in base64_string:
                    base64_string = base64_string.split(',')[1]
                qr_image_data = base64.b64decode(base64_string)
                qr_image_file = io.BytesIO(qr_image_data)
                pix_code = payment_data['qr_code_text']
                
                # --- NOVO TEXTO PARA WEBHOOK ---
                caption = (
                    f"🍿 **Seu Acesso Pipoca Premium está quase pronto!** ✨\n\n"
                    f"Para concluir, faça o pagamento de R${vip_price:.2f} via PIX.\n\n"
                    f"**1.** Escaneie o QR Code acima.\n"
                    f"**2.** Ou use o PIX Copia e Cola abaixo:\n"
                    f"`{pix_code}`\n\n"
                    "✅ **Seu acesso será liberado AUTOMATICAMENTE** assim que o pagamento for confirmado pelo banco.\n\n"
                    "⚠️ *Este código expira em alguns minutos.*"
                )
                # --- FIM DO NOVO TEXTO ---

                # --- MUDANÇA ---
                # Esta lógica é mais segura do que a sua anterior
                await safe_call(query.message, "delete")
                # --- FIM DA MUDANÇA ---

                msg_qrcode = await context.bot.send_photo(
                    chat_id=user_id, photo=qr_image_file, caption=caption,
                    parse_mode="Markdown", reply_markup=None
                )
                

                qr_message_id = msg_qrcode.message_id
                await db.set_user_active_payment_id(user_id, payment_id, qr_message_id)
            else:
                # --- MUDANÇA ---
                await safe_call(query, "edit_message_text", text="😕 Desculpe, não foi possível gerar a cobrança PIX. Tente novamente mais tarde.")
                # --- FIM DA MUDANÇA ---

    # --- REMOVIDO O BLOCO 'check_payment_' POIS AGORA É WEBHOOK ---

    elif callback_data.startswith("ep_nav_"):
        # (Função da v5.10 - COM MUDANÇAS)
        async with DB_SEMAPHORE:
            try:
                # --- MUDANÇA ---
                await safe_call(query, "answer")
                # --- FIM DA MUDANÇA ---
            except Exception as e:
                print(f"Ignorando erro de timeout no query.answer(): {e}")

            new_episode_id = int(callback_data.split('_')[2])

            try:
                # --- MUDANÇA ---
                await safe_call(query, "delete_message")
                # --- FIM DA MUDANÇA ---
            except Exception as e:
                print(f"Erro ao deletar msg de vídeo na navegação: {e}")

            placeholder_msg = await context.bot.send_message(chat_id=user_id, text="Carregando opções...")

            bot_username = context.bot.username
            message_text, reply_markup = await _get_episode_details_message(
                new_episode_id,
                bot_username,
                delete_msg_id=placeholder_msg.message_id
            )

            if reply_markup:
                await placeholder_msg.edit_text(
                    text=message_text,
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            else:
                await placeholder_msg.edit_text(message_text)

# =================================================================
# === INLINE QUERY HANDLER (COM CORREÇÃO v5.13) ===
# =================================================================
async def inline_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    results = []
    cache_time = 30 # Padrão
    is_personal = False
    next_offset = None # Inicializa o next_offset

    try:
        async with DB_SEMAPHORE:
            query_text = update.inline_query.query
            bot_username = context.bot.username

            # --- CORREÇÃO (v5.13): Lê o offset atual ---
            # O offset é uma string, convertemos para int.
            # Se for vazio (''), usamos 0.
            current_offset = int(update.inline_query.offset) if update.inline_query.offset else 0

            #
            # === ROTA 0: COMPARTILHAMENTO DE EPISÓDIO (EP_CARD) ===
            #
            if query_text.startswith("ep_card:"):
                try:
                    episode_id = int(query_text.split(':')[1])
                    
                    # Usamos a função de details para pegar tudo
                    details = await db.get_full_episode_details(episode_id)

                    if details:
                        episode = details
                        season = details.get('seasons')
                        series = await db.get_series_by_id(season['series_id']) if season else None

                        if season and series:
                            series_title = series.get('title', 'Série')
                            ep_number = episode.get('episode_number', 0)
                            season_number = season.get('season_number', 0)
                            ep_title = episode.get('title', f"Episódio {ep_number}")
                            poster_url_grande = series.get('poster_url', 'https://via.placeholder.com/500x750.png?text=Sem+Pôster')
                            poster_url_pequeno = poster_url_grande.replace('/w500/', '/w92/')                        
                            # Este é o caption da foto (igual da imagem de referência)
                            photo_caption = (
                                f"📽️ *{series_title}*\n"
                                f"🎬 *Temporada:* {season_number}\n"
                                f"🎯 *Episódio:* {ep_number}"
                            )
                            
                            # Este é o deeplink para o start
                            watch_url = f"https://t.me/{bot_username}?start=show_ep_{episode_id}"
                            
                            keyboard = [[
                                InlineKeyboardButton("Assistir - ⏩", url=watch_url)
                            ]]
                            reply_markup = InlineKeyboardMarkup(keyboard)

                            results.append(
                                InlineQueryResultPhoto(
                                    id=f"share_ep_{episode_id}",
                                    title=f"SÉRIE: {series_title}",
                                    description=f"S{season_number:02d}E{ep_number:02d} - {ep_title}",
                                    photo_url=poster_url_grande,
                                    thumbnail_url=poster_url_pequeno,
                                    caption=photo_caption,
                                    parse_mode="Markdown",
                                    reply_markup=reply_markup
                                )
                            )
                            is_personal = True # É um card específico
                            cache_time = 10 # Pode cachear por um tempinho
                
                except Exception as e:
                    print(f"Erro ao gerar ep_card: {e}")
                
                # Responde e encerra a função
                # (A lógica de retry será pega no final da função)
                
                # --- Usamos o safe_call no final da função ---
                pass # Deixa o código fluir para o safe_call no final
            
            ### FIM DA MUDANÇA 3 ###

            #
            # === ROTA 1: BUSCA DE EPISÓDIOS ===
            #

            # --- CORREÇÃO (v5.13): Novo formato de query: season:<id>:<offset> ---
            # (O formato antigo 'season:<id>' também funciona para a página 1)
            season_id = None
            if query_text.startswith("season:"):
                parts = query_text.split(':')
                try:
                    season_id = int(parts[1])
                    if len(parts) > 2:
                        current_offset = int(parts[2]) # Pega o offset do *botão*
                except (IndexError, ValueError):
                    season_id = None

            if season_id:
            # --- FIM DA CORREÇÃO ---
                try:
                    user_id = update.inline_query.from_user.id
                    await db.get_or_create_user(user_id=user_id, first_name=update.inline_query.from_user.first_name)
                    is_vip = await db.is_user_vip(user_id)

                    # --- CORREÇÃO (v5.13): Define o limite e chama com offset ---
                    # Usamos 48 para deixar espaço para os botões de nav
                    page_limit = 48

                    episodes, season = await db.get_episodes_for_season(
                        season_id,
                        limit=page_limit,
                        offset=current_offset
                    )

                    if not is_vip:
                        # (Lógica do VIP sem alteração)
                        results.append(
                            InlineQueryResultArticle(
                                id="vip_required_series",
                                title="🍿 Acesso Pipoca Premium Necessário!",
                                description="Clique aqui para liberar todas as séries do catálogo.",
                                thumbnail_url="https://i.imgur.com/L3Ew4wt.png",
                                reply_markup=InlineKeyboardMarkup([[
                                        InlineKeyboardButton(
                                            "Quero meu Acesso Premium! 🚀",
                                            url=f"https://t.me/{bot_username}?start=premium"
                                        )
                                    ]]),
                                input_message_content=InputTextMessageContent(
                                    message_text=(
                                        f"Ei {update.inline_query.from_user.first_name}! 👋\n\n"
                                        "Para maratonar esta e **todas as outras séries**, você precisa do 🍿 **Acesso Pipoca Premium**!\n\n"
                                        "Com ele, você libera todo o catálogo e ajuda nosso cinema a ficar sempre online."
                                    ),
                                    parse_mode="Markdown",
                                )
                            )
                        )
                        cache_time = 5
                        is_personal = True

                    elif not episodes and current_offset == 0:
                        # (Lógica de 'sem episódios' sem alteração)
                        results.append(InlineQueryResultArticle(
                            id="no_eps_found",
                            title="Nenhum episódio encontrado",
                            description="Esta temporada parece não ter episódios cadastrados.",
                            input_message_content=InputTextMessageContent("Nenhum episódio encontrado.")
                        ))
                        cache_time = 10

                    else: # Usuário é VIP e existem episódios

                        series = await db.get_series_by_id(season['series_id'])
                        series_title = series.get('title', 'Série') if series else 'Série'

                        poster_url_grande = series.get('poster_url', 'https://via.placeholder.com/500x750.png?text=Sem+Pôster')
                        poster_url_pequeno = poster_url_grande.replace('/w500/', '/w92/')

                        # (Loop otimizado da v5.11 - sem alteração)
                        for i, ep in enumerate(episodes):
                            ep_title = ep.get('title', f"Episódio {ep['episode_number']}")

                            message_text = (
                                f"📽️ *{series_title}*\n"
                                f"🎬 *Temporada:* {season['season_number']}\n"
                                f"🎯 *Episódio:* {ep['episode_number']} - {ep_title}\n"
                                f"--------------------\n"
                                f"Selecione o áudio (o bot irá te chamar no privado):"
                            )
                            keyboard = []
                            audio_row = []
                            if ep.get('dubbed_file_id'):
                                payload = f"watch_ep_{ep['id']}_dub"
                                url = f"https://t.me/{bot_username}?start={payload}"
                                audio_row.append(InlineKeyboardButton("Dublado 🇧🇷", url=url))
                            if ep.get('subtitled_file_id'):
                                payload = f"watch_ep_{ep['id']}_sub"
                                url = f"https://t.me/{bot_username}?start={payload}"
                                audio_row.append(InlineKeyboardButton("Legendado 🇺🇸", url=url))

                            if audio_row:
                                reply_markup = InlineKeyboardMarkup([audio_row])
                                results.append(
                                    InlineQueryResultArticle(
                                        id=f"ep_{ep['id']}",
                                        title=f"Episódio : {ep['episode_number']}",
                                        description=f"🎬 {series_title} | {ep_title}",
                                        thumbnail_url=poster_url_pequeno,
                                        reply_markup=reply_markup,
                                        input_message_content=InputTextMessageContent(
                                            message_text=message_text,
                                            parse_mode="Markdown"
                                        )
                                    )
                                )

                        # --- CORREÇÃO (v5.13): Botões Manuais de Paginação ---
                        nav_buttons = []
                        if current_offset > 0:
                            # Se não estamos na página 1, adiciona botão "Anterior"
                            prev_offset = max(0, current_offset - page_limit)
                            nav_buttons.append(
                                InlineQueryResultArticle(
                                    id=f"page_prev_{prev_offset}",
                                    title="⬅️ Página Anterior",
                                    description=f"Voltar para Eps {prev_offset + 1}-{current_offset}",
                                    thumbnail_url="https://i.imgur.com/b6PZt7H.png", # Seta para esquerda
                                    input_message_content=InputTextMessageContent(
                                        f"Carregando página anterior..."
                                    ),
                                    reply_markup=InlineKeyboardMarkup([[
                                        InlineKeyboardButton(
                                            "Clique para carregar ⬅️",
                                            switch_inline_query_current_chat=f"season:{season_id}:{prev_offset}"
                                        )
                                    ]])
                                )
                            )

                        if len(episodes) == page_limit:
                            # Se a busca retornou o NÚMERO MÁXIMO (48),
                            # assumimos que há uma próxima página.
                            next_offset_manual = current_offset + page_limit
                            nav_buttons.append(
                                InlineQueryResultArticle(
                                    id=f"page_next_{next_offset_manual}",
                                    title="Próxima Página ➡️",
                                    description=f"Carregar Eps {next_offset_manual + 1}-{next_offset_manual + page_limit}",
                                    thumbnail_url="https://i.imgur.com/FwOxDqO.png", # Seta para direita
                                    input_message_content=InputTextMessageContent(
                                        f"Carregando próxima página..."
                                    ),
                                    reply_markup=InlineKeyboardMarkup([[
                                        InlineKeyboardButton(
                                            "Clique para carregar ➡️",
                                            switch_inline_query_current_chat=f"season:{season_id}:{next_offset_manual}"
                                        )
                                    ]])
                                )
                            )

                        # Adiciona os botões de navegação DEPOIS dos resultados
                        results.extend(nav_buttons)
                        # --- FIM DA CORREÇÃO ---

                        cache_time = 10
                        is_personal = True

                except Exception as e:
                    print(f"❌ Erro na busca inline de episódios: {e}")
                    results = [InlineQueryResultArticle(
                        id="error_eps",
                        title="Erro ao buscar episódios",
                        input_message_content=InputTextMessageContent("Ocorreu um erro ao processar sua solicitação.")
                    )]

            #
            # === ROTA 2: BUSCA NORMAL (Filme/Série) ===
            #
            elif not query_text:
                results = [
                    InlineQueryResultArticle(
                        id="help_bubble",
                        title="Digite o nome do Filme ou Série",
                        description="Comece a digitar para que os resultados da busca apareçam aqui.",
                        thumbnail_url="https://cdn-icons-png.flaticon.com/512/3931/3931294.png",
                        input_message_content=InputTextMessageContent("👍")
                    )
                ]
                is_personal = True
                cache_time = 5

            else: # Usuário está digitando uma busca normal
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

                movies_from_db = await db.search_movies(query_text, limit=5)
                series_from_db = await db.search_series_by_title(query_text, limit=5)
                bot_username = context.bot.username

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

                for series in series_from_db:
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
                            # --- CORREÇÃO (v5.13): Inicia a paginação com offset 0 ---
                            keyboard.append([
                                InlineKeyboardButton(
                                    f"▶️ Temporada {season['season_number']}",
                                    switch_inline_query_current_chat=f"season:{season['id']}:0"
                                )
                            ])
                            # --- FIM DA CORREÇÃO ---
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

                cache_time = 30 # Padrão para busca normal

    except Exception as e:
        print(f"Erro ao processar lógica inline: {e}")
        return

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # --- CORREÇÃO (v5.13): Removemos o next_offset daqui ---
            # A paginação agora é 100% manual pelos botões
            
            # --- MUDANÇA (v5.14) ---
            # Aplicando safe_call aqui também para segurança, embora o
            # erro 'NoneType' seja mais comum em callback_query.
            await safe_call(update.inline_query, "answer",
                results,
                cache_time=cache_time,
                is_personal=is_personal,
                next_offset=None # <-- IMPORTANTE: Desliga o scroll infinito
            )
            # --- FIM DA MUDANÇA ---
            break
        except NetworkError as e:
            print(f"Erro de rede ao enviar inline_query (tentativa {attempt + 1}/{max_retries}): {e}")
            if attempt + 1 == max_retries:
                print("Falha ao enviar inline_query após 3 tentativas.")
                return
            await asyncio.sleep(1)

async def watch_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, message_deletada: bool = False) -> None:
    # (Função da v5.10 - Sem alterações)
    async with DB_SEMAPHORE:
        if update.message and not message_deletada:
            try: await update.message.delete()
            except Exception: pass

        if not context.args: return
        movie_id = context.args[0]
        user_id = update.effective_user.id

        chat_id_to_reply = update.effective_chat.id

        await db.get_or_create_user(user_id=update.effective_user.id, first_name=update.effective_user.first_name)

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
                chat_id=chat_id_to_reply,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode="Markdown"
            )
            return

        movie = await db.get_movie_by_id(movie_id)

        if movie and movie.get('poster_url'):
            audio_buttons = []
            if movie.get('dubbed_file_id'):
                audio_buttons.append(
                    InlineKeyboardButton("Dublado 🇧🇷", callback_data=f"play_{movie_id}_dub")
                )
            if movie.get('subtitled_file_id'):
                audio_buttons.append(
                    InlineKeyboardButton("Legendado 🇺🇸", callback_data=f"play_{movie_id}_sub")
                )

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

async def broadcast_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handler para o comando /transmissao (APENAS ADMINS).
    Prepara o bot para receber a mensagem de transmissão.
    """
    user_id = update.effective_user.id
    
    # 1. VERIFICA SE É ADMIN (usando sua lista importada de config.py)
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("Você não tem permissão para usar este comando.")
        return

    # 2. DEFINE O ESTADO
    context.user_data['state'] = 'awaiting_broadcast_message'
    await update.message.reply_text(
        "📣 **Modo de Transmissão** 📣\n\n"
        "Envie a mensagem que você deseja enviar para TODOS os usuários ativos.\n\n"
        "A mensagem pode conter formatação Markdown (ex: *negrito*, `código`).\n\n"
        "Para cancelar, digite /cancelar."
    )

async def iniciar_broadcast_real(context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """
    Esta é a função que realmente faz o trabalho pesado,
    com pausas e tratamento de erros.
    """
    bot = context.bot
    admin_id = ADMIN_IDS[0] # Pega o primeiro admin da sua lista
    
    # 1. Pega APENAS usuários ativos
    active_users = await db.get_active_users()
    if not active_users:
        print("Broadcast cancelado: Nenhum usuário ativo encontrado.")
        await bot.send_message(chat_id=admin_id, text="📣 Transmissão cancelada: Nenhum usuário ativo encontrado.")
        return

    print(f"Iniciando broadcast de '{message_text[:20]}...' para {len(active_users)} usuários.")
    
    sucesso = 0
    falha_bloqueio = 0
    falha_outros = 0

    for user in active_users:
        user_id = user['user_id']
        
        try:
            # Tenta enviar a mensagem
            await bot.send_message(chat_id=user_id, text=message_text, parse_mode="Markdown")
            sucesso += 1
            
            # A PAUSA SEGURA (6 segundos = 10 usuários/min)
            await asyncio.sleep(6)

        except Forbidden as e:
            # Usuário bloqueou o bot ou desativou a conta
            if "bot was blocked" in str(e) or "user is deactivated" in str(e):
                await db.set_user_inactive(user_id) # Desativa no banco
                falha_bloqueio += 1
            else:
                print(f"Erro Forbidden (não-bloqueio) para {user_id}: {e}")
                falha_outros += 1

        except RetryAfter as e:
            # O Telegram pediu para esperar
            print(f"RetryAfter... esperando {e.retry_after} segundos.")
            await asyncio.sleep(e.retry_after + 1) # Espera o tempo pedido + 1s

        except Exception as e:
            # Outro erro qualquer
            print(f"Erro genérico ao enviar para {user_id}: {e}")
            falha_outros += 1
    
    # Avisa o Admin que terminou
    print("Broadcast concluído!")
    await bot.send_message(
        chat_id=admin_id, 
        text=(
            f"📣 **Transmissão Concluída!**\n\n"
            f"✅ Enviado com sucesso: {sucesso}\n"
            f"🚫 Bloqueios/Desativados: {falha_bloqueio}\n"
            f"❌ Falhas (outras): {falha_outros}\n\n"
            f"Total de usuários (início): {len(active_users)}"
        ),
        parse_mode="Markdown"
    )

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Função sem alteração)
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

        elif user_state == 'awaiting_broadcast_message':
            del context.user_data['state']
            user_id = update.effective_user.id
            
            # Checagem dupla de admin, por segurança
            if user_id not in ADMIN_IDS:
                return

            # Pega a mensagem exata que o admin enviou
            message_to_send = update.message.text
            await update.message.reply_text(
                f"✅ Mensagem recebida. Iniciando a transmissão em segundo plano...\n\n"
                "Você será notificado quando terminar. Isso pode demorar bastante."
            )
            asyncio.create_task(iniciar_broadcast_real(context, message_to_send))

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Função sem alteração)
    if 'state' in context.user_data:
        del context.user_data['state']
        await update.message.reply_text("Operação cancelada.")
    else:
        await update.message.reply_text("Não há nenhuma operação para cancelar.")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Função sem alteração)
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
broadcast_handler = CommandHandler("transmissao", broadcast_command_handler)
