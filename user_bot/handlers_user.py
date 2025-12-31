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

async def _get_episode_details_message(episode_id: int, bot_username: str, delete_msg_id: int = None) -> tuple[str, InlineKeyboardMarkup]:
    """Prepara a mensagem e os botões de áudio (com URL) para um episódio."""
    try:
        # 1. Busca dados básicos
        details = await db.get_full_episode_details(episode_id)
        if not details: 
            return ("Erro: Episódio não encontrado.", None)

        episode = details
        season = details.get('seasons')
        series = season.get('series') if season else None

        if not season or not series: 
            return ("Erro: Dados da temporada ou série ausentes.", None)

        # --- DEBUG LOG ---
        print(f"\n--- 🕵️ DEBUG NAVEGAÇÃO ---")
        print(f"Série ID: {series['id']} | Temp Atual: {season['season_number']} | Ep Atual: {episode['episode_number']}")

        # Monta o texto (Padrão)
        series_title = series.get('title', 'Série')
        ep_title = episode.get('title', f"Episódio {episode['episode_number']}")
        
        message_text = (
            f"📽️ *{series_title}*\n"
            f"🎬 *Temporada:* {season['season_number']}\n"
            f"🎯 *Episódio:* {episode['episode_number']} - {ep_title}\n"
            f"--------------------\n"
            f"Selecione o áudio (o bot irá te chamar no privado):"
        )

        # -------------------------------------------------------------
        # 2. LÓGICA DE NAVEGAÇÃO
        # -------------------------------------------------------------
        season_id = season['id']
        current_ep_num = int(episode['episode_number'])
        current_season_num = int(season['season_number'])
        series_id = series['id']

        # Busca vizinhos normais (mesma temporada)
        prev_ep, next_ep = await asyncio.gather(
            db.get_neighbor_episode(season_id, current_ep_num, 'previous'),
            db.get_neighbor_episode(season_id, current_ep_num, 'next')
        )

        print(f"Vizinho Próximo na mesma temporada? {'SIM' if next_ep else 'NÃO'}")

        # === LÓGICA "PRÓXIMO": Se não tem ep seguinte, tenta a próxima temporada ===
        if not next_ep:
            print(">>> Fim da temporada detectado. Buscando próxima temporada...")
            
            all_seasons = await db.get_seasons_for_series(series_id)
            
            if all_seasons:
                target_season = current_season_num + 1
                # Procura a temporada X + 1
                next_season_obj = next((s for s in all_seasons if int(s['season_number']) == target_season), None)
                
                if next_season_obj:
                    print(f">>> ✅ Temporada {target_season} encontrada (ID: {next_season_obj['id']})")
                    # Pega o primeiro episódio da nova temporada
                    eps_next_season, _ = await db.get_episodes_for_season(next_season_obj['id'], limit=1, offset=0)
                    
                    if eps_next_season:
                        next_ep = eps_next_season[0]
                        print(f">>> ✅ Episódio 1 da nova temporada definido como 'Próximo'.")
                    else:
                        print(f">>> ❌ Temporada existe mas está vazia.")
                else:
                    print(f">>> ❌ Temporada {target_season} não encontrada. Fim da série.")

        # === LÓGICA "ANTERIOR": Se não tem ep anterior, tenta a temporada anterior ===
        if not prev_ep and current_season_num > 1:
            all_seasons = await db.get_seasons_for_series(series_id)
            if all_seasons:
                target_prev = current_season_num - 1
                prev_season_obj = next((s for s in all_seasons if int(s['season_number']) == target_prev), None)
                
                if prev_season_obj:
                    # Pega o último episódio da temporada anterior
                    eps_prev, _ = await db.get_episodes_for_season(prev_season_obj['id'], limit=100, offset=0)
                    if eps_prev:
                        prev_ep = eps_prev[-1] # Pega o último da lista

        # -------------------------------------------------------------
        # 3. MONTAGEM DOS BOTÕES
        # -------------------------------------------------------------
        keyboard = []
        nav_row = []
        
        if prev_ep:
            nav_row.append(InlineKeyboardButton("⏪ Ep. Anterior", callback_data=f"ep_nav_{prev_ep['id']}"))
        
        if next_ep:
            # Verifica se mudou de temporada para mudar o texto
            # next_ep['season_id'] pode virar int ou str, garante comparação segura
            is_new_season = int(next_ep.get('season_id', 0)) != int(season_id)
            
            btn_text = "Próxima Temp. ⏩" if is_new_season else "Próximo Ep. ⏩"
            nav_row.append(InlineKeyboardButton(btn_text, callback_data=f"ep_nav_{next_ep['id']}"))
            print(f">>> Botão criado: '{btn_text}' -> ID {next_ep['id']}")
        
        if nav_row:
            keyboard.append(nav_row)

        # Botões de Áudio
        audio_row = []
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

        print("--- FIM DEBUG ---\n")
        return (message_text, InlineKeyboardMarkup(keyboard))

    except Exception as e:
        print(f"❌ CRITICAL ERROR helper: {e}")
        import traceback
        traceback.print_exc()
        return (f"Erro ao processar episódio: {e}", None)
    
async def _get_vip_sales_message(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, InlineKeyboardMarkup]:
    """
    Busca a configuração de venda do DB e formata a mensagem e os botões.
    """
    config = await db.get_bot_config()
    
    # Pega os valores do DB ou usa defaults
    price = config.get('vip_price', 4.99)
    anchor_price = config.get('vip_anchor_price', 14.99)
    sales_text = config.get('vip_sales_text', 'Para continuar, assine o VIP!')
    
    # Formata o texto de venda substituindo os placeholders
    formatted_text = sales_text.format(
        PRICE=f"R$ {price:,.2f}",
        ANCHOR_PRICE=f"R$ {anchor_price:,.2f}"
    )

    
    keyboard = [[InlineKeyboardButton("Quero meu Acesso Premium! 🚀", callback_data="main_vip")]]
    
    # Se for um callback_query (botão), adiciona o botão "Voltar"
    if 'update' in context.user_data and isinstance(context.user_data['update'], Update) and context.user_data['update'].callback_query:
         keyboard.append([InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")])
         
    return (formatted_text, InlineKeyboardMarkup(keyboard))

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
                        # --- MUDANÇA (v7.0): Usa o helper ---
                        config = await db.get_bot_config()
                        price = config.get('vip_price', 4.99)
                        duration_days = config.get('vip_duration_days', 7)

                        if price <= 0:
                            # É GRÁTIS!
                            await db.set_user_as_vip(user.id, duration_days=duration_days if duration_days > 0 else 9999)
                            try:
                                # Tenta enviar uma mensagem nova
                                await context.bot.send_message(chat_id=user.id, text="🎉 Bem-vindo! O acesso está gratuito no momento. Carregando...")
                            except Exception:
                                pass # Se falhar, não importa, o código continua
                            
                            # NÃO damos 'return', o código continua e libera o acesso
                        else:
                            # É PAGO! (Esta é a lógica antiga)
                            context.user_data['update'] = update
                            sales_text, reply_markup = await _get_vip_sales_message(context)
                            
                            # A LINHA CORRETA PARA ESTE LUGAR
                            await context.bot.send_message( 
                                chat_id=user.id,
                                text=f"Opa, {user.first_name}! 👋\n\n{sales_text}",
                                parse_mode="Markdown",
                                reply_markup=reply_markup
                            )
                            return # <-- IMPORTANTE: Bloqueia o usuário

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
                            f"🍿 Assistido com @{bot_username}\n"
                            f"⚠️ *Este vídeo será apagado em 4 horas.*"
                        )

                        # --- CORREÇÃO (v5.13): Lógica de navegação otimizada ---
                        season_id = season_data.get('id')
                        current_ep_num = episode_data.get('episode_number', 0)

                        # Faz 2 buscas rápidas em paralelo, em vez de 1 lenta
                        prev_ep, next_ep = await asyncio.gather(
                            db.get_neighbor_episode(season_id, current_ep_num, 'previous'),
                            db.get_neighbor_episode(season_id, current_ep_num, 'next')
                        )

                        # --- ADICIONE ISSO PARA DESCOBRIR A VERDADE ---
                        if next_ep:
                            print(f"👻 O BOT ACHOU UM PRÓXIMO EPISÓDIO! ID: {next_ep.get('id')} | Número: {next_ep.get('episode_number')}")
                        else:
                            print("✅ O bot NÃO achou próximo episódio. Deveria pular a temporada.")
                        # -----------------------------------------------

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

                        # Cria o código único: ep_{id}_{audio}
                        fav_unique_code = f"ep_{episode_id}_{audio_type}"
                        
                        # Verifica se já é favorito para decidir o texto do botão
                        is_fav = await db.is_favorite(user.id, fav_unique_code)
                        fav_btn_text = "❌ Remover da Lista" if is_fav else "🔖 Salvar na Lista"
                        # ---------------------------------

                        keyboard = []

                        if nav_row:
                            keyboard.append(nav_row)

                        keyboard.append([
                            InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{series_id_for_related}_series"),
                            InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"ep_card:{episode_id}")
                        ])

                        # 3. Adiciona o Botão Salvar (Embaixo)
                        keyboard.append([
                            InlineKeyboardButton(fav_btn_text, callback_data=f"fav_toggle_{fav_unique_code}")
                        ])
                        
                        video_reply_markup = InlineKeyboardMarkup(keyboard)

                        try:
                            # PLANO A: Tenta enviar o file_id salvo
                            print(f"[Plano A] Tentando enviar Ep {episode_id} com file_id: {file_id_to_send[:20]}...")
                            
                            # --- MUDANÇA 1: Salva a mensagem enviada ---
                            sent_message = await context.bot.send_video(
                                chat_id=user.id,
                                video=file_id_to_send,
                                caption=video_caption,
                                parse_mode="Markdown",
                                reply_markup=video_reply_markup,
                                protect_content=True
                            )

                            # --- MUDANÇA 2: Adiciona o agendamento ---
                            job_data = {
                                'chat_id': sent_message.chat_id,
                                'message_id': sent_message.message_id
                            }
                            # Agenda a deleção para 4 horas (14400 segundos)
                            context.job_queue.run_once(
                                delete_message_job, 
                                when=14400,  # 4 horas * 60 min * 60 seg
                                data=job_data,
                                name=f"del_{sent_message.chat_id}_{sent_message.message_id}"
                            )
                            print(f"[JOB] Agendada deleção da msg {sent_message.message_id} em 4h.")
                            # --- FIM DA MUDANÇA ---
                        except BadRequest as e:
                            error_text = str(e).lower()
                        
                            if ("wrong file id" in error_text or "wrong file identifier" in error_text) and msg_id_to_copy and STORAGE_CHANNEL_ID_SERIES:
                                # PLANO B: O file_id está quebrado, mas temos o msg_id
                                print(f"🚨 [Plano B] File ID quebrado para Ep {episode_id}. Copiando...")
                                print(f"    Copiando msg {msg_id_to_copy} do canal {STORAGE_CHANNEL_ID_SERIES}")
                                
                                try:
                                    # 1. Copia a mensagem (o vídeo)
                                    copied_message = await context.bot.copy_message(
                                        chat_id=user.id,
                                        from_chat_id=STORAGE_CHANNEL_ID_SERIES,
                                        message_id=msg_id_to_copy,
                                        protect_content=True
                                    )
                                    
                                    # 3. Editamos o caption da mensagem que acabamos de copiar.
                                    await context.bot.edit_message_caption(
                                        chat_id=user.id,
                                        message_id=copied_message.message_id, # Usamos o ID da msg copiada
                                        caption=video_caption,
                                        parse_mode="Markdown",
                                        reply_markup=video_reply_markup
                                    )

                                    # --- MUDANÇA 3: Agendamento do Plano B ---
                                    job_data = {
                                        'chat_id': user.id,  # <-- Corrigido
                                        'message_id': copied_message.message_id
                                    }
                                    context.job_queue.run_once(
                                        delete_message_job, 
                                        when=14400, # Teste de 4 horas 
                                        data=job_data,
                                        name=f"del_{user.id}_{copied_message.message_id}" # <-- Corrigido
                                    )
                                    print(f"[JOB] Agendada deleção da msg {copied_message.message_id} (Plano B) em 4h.")
                                    # --- FIM DA MUDANÇA ---
                                    
                                except Exception as e_inner:
                                    print(f"   ❌ FALHA no Plano B: {e_inner}")
                                    await status_msg.edit_text("😔 Desculpe, o `file_id` deste episódio quebrou e não consegui repará-lo automaticamente. Avise um admin.")
                            else:
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
                        # --- MUDANÇA (v7.0): Usa o helper ---
                        config = await db.get_bot_config()
                        price = config.get('vip_price', 4.99)
                        duration_days = config.get('vip_duration_days', 7)

                        if price <= 0:
                            # É GRÁTIS!
                            await db.set_user_as_vip(user.id, duration_days=duration_days if duration_days > 0 else 9999)
                            try:
                                # Tenta enviar uma mensagem nova
                                await context.bot.send_message(chat_id=user.id, text="🎉 Bem-vindo! O acesso está gratuito no momento. Carregando...")
                            except Exception:
                                pass # Se falhar, não importa, o código continua
                            
                            # NÃO damos 'return', o código continua e libera o acesso
                        else:
                            # É PAGO! (Esta é a lógica antiga)
                            context.user_data['update'] = update
                            sales_text, reply_markup = await _get_vip_sales_message(context)
                            
                            # A LINHA CORRETA PARA ESTE LUGAR
                            await context.bot.send_message( 
                                chat_id=user.id,
                                text=f"Opa, {user.first_name}! 👋\n\n{sales_text}",
                                parse_mode="Markdown",
                                reply_markup=reply_markup
                            )
                            return # <-- IMPORTANTE: Bloqueia o usuário
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

            elif payload == "vip":
                # Simula um clique no botão main_vip
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

        await db.get_or_create_user(user_id=user.id, first_name=user.first_name)

        config = await db.get_bot_config()
        price = config.get('vip_price', 4.99)
        is_free = price <= 0
        user_is_vip = await db.is_user_vip(user.id)

        # Começa o teclado SÓ com o botão de busca
        keyboard = [
            [InlineKeyboardButton("Buscar Mídia 🔎", switch_inline_query_current_chat=""),],
        ]
        
        # SÓ adiciona o botão "Adquirir VIP" se...
        # 1. O bot NÃO estiver no modo "grátis" E
        # 2. O usuário AINDA NÃO for VIP
        if not is_free and not user_is_vip:
            keyboard.append([InlineKeyboardButton("Adquirir VIP 🚀", callback_data="main_vip")])

        keyboard.append([InlineKeyboardButton("🔖 Minha Lista", callback_data="fav_menu")])
        
        # Adiciona os outros botões
        keyboard.extend([
            [InlineKeyboardButton("Pedir Filme/Série 💡", callback_data="main_request"),
            InlineKeyboardButton("Top Mídia 🏆", callback_data="main_top")]
        ])

        main_menu = InlineKeyboardMarkup(keyboard)
        community_link = "https://t.me/+-v5nIbZ93J43MmQ5"
        ads_channel_link = "https://t.me/meucinepipocacanal"

        ads_channel_text = (
            "Para manter o bot 100% gratuito, preciso da sua ajuda!\n"
            f"➡️ <a href=\"{ads_channel_link}\"><b>Entre no nosso Canal de Avisos</b></a> ⬅️\n"
            "É lá que posto os anúncios que pagam o servidor."
        )
        
        community_text = (
            "Psst! 🤫 Quer debater sobre filmes, pedir séries, ou dar ideias para o bot?\n"
            f"➡️ <a href=\"{community_link}\"><b>Junte-se à nossa comunidade!</b></a>"
        )
        welcome_text = (
            f"Olá {user.mention_html()}! 👋\n\n"
            "🍿 **ÓTIMA NOTÍCIA: O BOT AGORA É 100% GRATUITO!** 🍿\n\n"
            "Gosta de maratonar? Esse bot é perfeito para isso 😉.\n\n"
            "Clique no botão \"Buscar Mídia 🔎\" para começar.\n\n"
            "Ficou com dúvidas? Envie o comando /help\n\n"
            "--------------------\n\n"
            f"{ads_channel_text}\n\n"
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

async def set_text_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /settext [texto de venda]
    Usa {PRICE} e {ANCHOR_PRICE} como placeholders.
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return await update.message.reply_text("Você não tem permissão.")
        
    try:
        # Pega o texto completo depois do comando
        sales_text = update.message.text.split(' ', 1)[1]
        
        if '{PRICE}' not in sales_text or '{ANCHOR_PRICE}' not in sales_text:
            await update.message.reply_text("⚠️ Atenção: O seu texto não contém os placeholders {PRICE} e {ANCHOR_PRICE}. Salvo mesmo assim.")
            
        await db.set_bot_config_value('vip_sales_text', sales_text)
        
        await update.message.reply_text(
            "✅ Novo texto de venda salvo!\n\n"
            f"**Preview:**\n{sales_text.format(PRICE='R$ X.XX', ANCHOR_PRICE='R$ Y.YY')}",
            parse_mode="Markdown"
        )
    except IndexError:
        await update.message.reply_text(
            "Erro: Você precisa enviar o texto.\n"
            "Ex: /settext Novo texto de venda com {PRICE}!"
        )
    except Exception as e:
        await update.message.reply_text(f"Erro ao salvar: {e}")

async def set_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /setconfig [preço] [âncora] [dias]
    Ex: /setconfig 4.99 14.99 7
    Ex: /setconfig gratis 0 9999
    """
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return await update.message.reply_text("Você não tem permissão.")
        
    try:
        # --- INÍCIO DA MUDANÇA ---
        price_str = context.args[0].lower()
        price = 0.0

        if price_str == "gratis":
            price = 0.0
        else:
            price = float(price_str)
        # --- FIM DA MUDANÇA ---

        anchor_price = float(context.args[1])
        duration = int(context.args[2])
        
        await db.set_bot_config_value('vip_price', price)
        await db.set_bot_config_value('vip_anchor_price', anchor_price)
        await db.set_bot_config_value('vip_duration_days', duration)
        
        price_text = f"R$ {price:,.2f}"
        if price == 0:
            price_text = "Gratuito (R$ 0,00)"
        
        await update.message.reply_text(
            "✅ Configuração de VIP atualizada!\n\n"
            f"Preço: **{price_text}**\n"
            f"Âncora (De): R$ {anchor_price:,.2f}\n"
            f"Duração: {duration} dias"
        )
    except (IndexError, ValueError):
        await update.message.reply_text(
            "Erro: Use o formato correto.\n"
            "/setconfig [preço ou 'gratis'] [âncora] [dias]\n"
            "Ex: /setconfig 4.99 14.99 7\n"
            "Ex: /setconfig gratis 0 9999"
        )
    except Exception as e:
        await update.message.reply_text(f"Erro ao salvar: {e}")

async def show_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra a configuração atual."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        return await update.message.reply_text("Você não tem permissão.")
        
    config = await db.get_bot_config()
    
    await update.message.reply_text(
        "⚙️ **Configuração Atual do Bot** ⚙️\n\n"
        f"**Preço:** R$ {config.get('vip_price'):,.2f}\n"
        f"**Âncora:** R$ {config.get('vip_anchor_price'):,.2f}\n"
        f"**Duração:** {config.get('vip_duration_days')} dias\n\n"
        f"**Texto de Venda:**\n{config.get('vip_sales_text')}"
    )

async def delete_message_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Função chamada pelo JobQueue para deletar uma mensagem.
    """
    try:
        # Pega os dados que salvamos quando agendamos
        chat_id = context.job.data['chat_id']
        message_id = context.job.data['message_id']
        
        print(f"[JOB] Deletando msg {message_id} no chat {chat_id} (após 4h).")
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    
    except BadRequest as e:
        # Usuário provavelmente já deletou a msg. Ignoramos.
        print(f"[JOB-WARN] Não foi possível deletar msg: {e}")
    except Exception as e:
        # Outro erro
        print(f"[JOB-ERROR] Erro ao deletar msg: {e}")

async def request_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # (Função sem alteração)
    async with DB_SEMAPHORE:
        user_id = update.effective_user.id

        await db.get_or_create_user(user_id=update.effective_user.id, first_name=update.effective_user.first_name)

        if not await db.is_user_vip(user_id):
            config = await db.get_bot_config()
            price = config.get('vip_price', 4.99)
            duration_days = config.get('vip_duration_days', 7)

            if price <= 0:
                # É GRÁTIS!
                await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
                try:
                    # Tenta enviar uma mensagem nova
                    await context.bot.send_message(chat_id=user_id, text="🎉 Bem-vindo! O acesso está gratuito no momento. Carregando...")
                except Exception:
                    pass # Se falhar, não importa, o código continua
                
                # NÃO damos 'return', o código continua e libera o acesso
            else:
                # É PAGO! (Esta é a lógica antiga)
                context.user_data['update'] = update
                sales_text, reply_markup = await _get_vip_sales_message(context)
                
                # A LINHA CORRETA PARA ESTE LUGAR
                await context.bot.send_message( 
                    chat_id=user_id,
                    text=f"Opa, {update.effective_user.first_name}! 👋\n\n{sales_text}",
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                return # <-- IMPORTANTE: Bloqueia o usuário

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
                    f"🍿 Assistido com @{bot_username}\n"
                    f"⚠️ *Este vídeo será apagado em 4 horas.*"
                )

                # 1. Gera o código único: mov_{id}_{audio}
                fav_unique_code = f"mov_{movie_id}_{audio_choice}"

                # 2. Verifica se já é favorito para decidir o texto do botão
                is_fav = await db.is_favorite(user_id, fav_unique_code)
                fav_btn_text = "❌ Remover da Lista" if is_fav else "🔖 Salvar na Lista"

                keyboard = [[
                    InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"{movie['title']}"),
                    InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{movie_id}")
                    ],
                    [InlineKeyboardButton(fav_btn_text, callback_data=f"fav_toggle_{fav_unique_code}")]
                ]
                
                video_reply_markup = InlineKeyboardMarkup(keyboard)

                try:
                    # PLANO A: Tenta enviar o file_id salvo
                    print(f"[Plano A] Tentando enviar Filme {movie_id} com file_id: {file_id_to_send[:20]}...")
                    
                    # --- MUDANÇA 1: Salva a mensagem enviada ---
                    sent_message = await context.bot.send_video(
                        chat_id=query.message.chat.id,
                        video=file_id_to_send,
                        caption=video_caption,
                        parse_mode="Markdown",
                        reply_markup=video_reply_markup,
                        protect_content=True
                    )
                    
                    # --- MUDANÇA 2: Adiciona o agendamento ---
                    job_data = {
                        'chat_id': sent_message.chat_id,
                        'message_id': sent_message.message_id
                    }
                    context.job_queue.run_once(
                        delete_message_job, 
                        when=14400,  # 4 horas
                        data=job_data,
                        name=f"del_{sent_message.chat_id}_{sent_message.message_id}"
                    )
                    print(f"[JOB] Agendada deleção da msg {sent_message.message_id} em 4h.")
                    # --- FIM DA MUDANÇA ---
                    
                except BadRequest as e:
                    error_text = str(e).lower()
                        
                    if ("wrong file id" in error_text or "wrong file identifier" in error_text) and msg_id_to_copy and STORAGE_CHANNEL_ID:
                        # PLANO B: O file_id está quebrado, mas temos o msg_id
                        print(f"🚨 [Plano B] File ID quebrado para Filme {movie_id}. Copiando...")
                        print(f"    Copiando msg {msg_id_to_copy} do canal {STORAGE_CHANNEL_ID}")
                        
                        try:
                            # 1. Copia a mensagem (o vídeo)
                            copied_message = await context.bot.copy_message(
                                chat_id=query.message.chat.id,
                                from_chat_id=STORAGE_CHANNEL_ID,
                                message_id=msg_id_to_copy,
                                protect_content=True
                            )
                            
                            # 3. Editamos o caption da mensagem que acabamos de copiar.
                            await context.bot.edit_message_caption(
                                chat_id=query.message.chat.id,
                                message_id=copied_message.message_id, # Usamos o ID da msg copiada
                                caption=video_caption,
                                parse_mode="Markdown",
                                reply_markup=video_reply_markup
                            )

                            # --- MUDANÇA 3: Agendamento do Plano B ---
                            job_data = {
                                'chat_id': query.message.chat.id,  # <-- Corrigido
                                'message_id': copied_message.message_id
                            }
                            context.job_queue.run_once(
                                delete_message_job, 
                                when=14400, # Agendamento para 4 horas
                                data=job_data,
                                name=f"del_{query.message.chat.id}_{copied_message.message_id}" # <-- Corrigido
                            )
                            print(f"[JOB] Agendada deleção da msg {copied_message.message_id} (Plano B) em 4h.")
                            # --- FIM DA MUDANÇA ---
                            
                        except Exception as e_inner:
                            print(f"   ❌ FALHA no Plano B: {e_inner}")
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
                config = await db.get_bot_config()
                price = config.get('vip_price', 4.99)
                duration_days = config.get('vip_duration_days', 7)

                if price <= 0:
                    # É GRÁTIS!
                    await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
                    await safe_call(query, "answer", text="🎉 Acesso gratuito ativado! Carregando...")
                    
                    # NÃO damos 'return', o código continua
                else:
                    # É PAGO! (Esta é a lógica antiga)
                    context.user_data['update'] = update
                    sales_text, reply_markup = await _get_vip_sales_message(context)
                    
                    # A LINHA CORRETA
                    await safe_call(query, "edit_message_text", 
                        text=f"Opa, {query.from_user.first_name}! 👋\n\n{sales_text}",
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                    return # <-- IMPORTANTE: Bloqueia o usuário

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
                # --- MUDANÇA (v7.0): Usa o helper ---
                config = await db.get_bot_config()
                price = config.get('vip_price', 4.99)
                duration_days = config.get('vip_duration_days', 7)

                if price <= 0:
                    # É GRÁTIS!
                    await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
                    await safe_call(query, "answer", text="🎉 Acesso gratuito ativado! Carregando...")
                    
                    # NÃO damos 'return', o código continua
                else:
                    # É PAGO! (Esta é a lógica antiga)
                    context.user_data['update'] = update
                    sales_text, reply_markup = await _get_vip_sales_message(context)
                    
                    # A LINHA CORRETA
                    await safe_call(query, "edit_message_text", 
                        text=f"Opa, {query.from_user.first_name}! 👋\n\n{sales_text}",
                        parse_mode="Markdown",
                        reply_markup=reply_markup
                    )
                    return # <-- IMPORTANTE: Bloqueia o usuário

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
        # ETAPA 1: Mostrar o Texto de Venda (Marketing)
        async with DB_SEMAPHORE:
            await safe_call(query, "answer")
            
            # Pega o texto de venda formatado (o helper já faz isso)
            context.user_data['update'] = update
            sales_text, _ = await _get_vip_sales_message(context) # Ignoramos o markup antigo

            # Criamos o NOVO markup com o botão de confirmar
            keyboard = [
                [InlineKeyboardButton("✅ Sim, Gerar PIX para Pagar!", callback_data="confirm_pay")],
                [InlineKeyboardButton("⬅️ Voltar ao Menu", callback_data="back_to_main")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            try:
                # Mostra o texto de venda com o botão "Gerar PIX"
                await safe_call(query, "edit_message_text",
                    text=sales_text,
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
            except Exception as e:
                print(f"Erro ao mostrar sales_text em main_vip: {e}")

    elif callback_data == "confirm_pay":
        # ETAPA 2: Gerar o PIX (Lógica que estava em "main_vip")
        now = time.time()
        last_request = context.user_data.get('last_pix_request', 0)
        cooldown = 60 # 60 segundos de cooldown

        if now - last_request < cooldown:
            await safe_call(query, "answer",
                text=f"✋ Calma! Aguarde {int(cooldown - (now - last_request))}s para gerar um novo PIX.",
                show_alert=True
            )
            return

        async with DB_SEMAPHORE:
            await safe_call(query, "answer")

            
            config = await db.get_bot_config()
            price = config.get('vip_price', 4.99)
            duration_days = config.get('vip_duration_days', 7)

            # (Verificação de VIP de novo, por segurança)
            if await db.is_user_vip(user_id):
                try:
                    await safe_call(query, "edit_message_text", text="✨ Você já é um membro Premium! Aproveite todo o catálogo do Cine Pipoca.")
                except Exception: pass
                return
            
            if price <= 0:
                # O admin mudou para 'gratis' enquanto o usuário olhava o menu
                await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
                await safe_call(query, "edit_message_text", text="🎉 Boas notícias! O acesso agora é gratuito. Seu VIP foi ativado!")
                return
            
            # Lógica de verificação de pagamento pendente
            user_details = await db.get_user_details(user_id)
            active_payment_id = user_details.get('active_payment_id') if user_details else None

            config = await db.get_bot_config()
            duration_days = config.get('vip_duration_days', 7)

            if active_payment_id:
                await safe_call(query, "edit_message_text", text="⏳ Verificando pagamento pendente...")
                status = await payments.check_payment_status(active_payment_id)

                if status == "paid":
                    await db.set_user_as_vip(user_id, duration_days=duration_days) 
                    await db.clear_user_active_payment_id(user_id)
                    print(f"✅ VIP ATIVADO (via verificação manual) para UserID: {user_id}")
                    await safe_call(query, "edit_message_text", text="🎉 Pagamento confirmado! Seu acesso Premium está ativo.")
                    return
                
                elif status == "created":
                    await safe_call(query, "edit_message_text", text="⚠️ Você já possui uma cobrança PIX pendente.\n\nPor favor, realize o pagamento ou aguarde alguns minutos até que ela expire para gerar uma nova.")
                    return

                elif status in ["expired", "canceled", "not_found"]:
                    print(f"PIX {active_payment_id} expirado/cancelado. Limpando...")
                    await db.clear_user_active_payment_id(user_id)
                
                else:
                    await safe_call(query, "edit_message_text", text="😕 Erro ao verificar seu pagamento anterior. Tente novamente em 1 minuto.")
                    return
            
            context.user_data['last_pix_request'] = time.time()
            
            # Puxa os preços do DB
            vip_price = config.get('vip_price', 4.99)
            vip_anchor_price = config.get('vip_anchor_price', 14.99)
            
            await safe_call(query, "edit_message_text", text="⏳ Gerando sua cobrança PIX, aguarde...")

            payment_data = await payments.create_pix_payment(user_id=user_id, amount=vip_price)

            if payment_data and payment_data.get("qr_code_base64"):
                payment_id = payment_data['payment_id']
                base64_string = payment_data['qr_code_base64']
                if ',' in base64_string:
                    base64_string = base64_string.split(',')[1]
                qr_image_data = base64.b64decode(base64_string)
                qr_image_file = io.BytesIO(qr_image_data)
                pix_code = payment_data['qr_code_text']
                
                caption = (
                    f"✨ **Seu PIX Promocional está pronto!**\n\n"
                    f"Preço normal: ~~R$ {vip_anchor_price:,.2f}~~\n"
                    f"Preço HOJE: **R$ {vip_price:,.2f}**\n\n"
                    f"**1.** Escaneie o QR Code acima.\n"
                    f"**2.** Ou use o PIX Copia e Cola abaixo:\n"
                    f"`{pix_code}`\n\n"
                    "✅ Seu acesso Premium é **liberado automaticamente** segundos após o pagamento.\n\n"
                    "⚠️ **ATENÇÃO: Este código expira em 5 minutos!**\n"
                    "Pague agora para travar o preço promocional."
                )

                await safe_call(query.message, "delete") # Deleta a mensagem de "venda"

                msg_qrcode = await context.bot.send_photo(
                    chat_id=user_id, photo=qr_image_file, caption=caption,
                    parse_mode="Markdown", reply_markup=None
                )
                
                qr_message_id = msg_qrcode.message_id
                await db.set_user_active_payment_id(user_id, payment_id, qr_message_id) 
            else:
                await safe_call(query, "edit_message_text", text="😕 Desculpe, não foi possível gerar a cobrança PIX. Tente novamente mais tarde.")

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

    elif callback_data == "fav_menu":
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
            
                await safe_call(query, "edit_message_text",
                    text=empty_text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard_empty)
                )
                return

            keyboard = []
            for fav in favorites:
                # Botão com nome cortado
                title_display = fav['title'][:30]
                icon = '🎬' if fav['media_type'] == 'movie' else '📺'
                keyboard.append([InlineKeyboardButton(f"{icon} {title_display}", callback_data=f"fav_watch_{fav['unique_code']}")])
            
            keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="back_to_main")])
            
            await safe_call(query, "edit_message_text",
                text=f"🔖 **Minha Lista ({len(favorites)}/10)**\n\nToque para assistir:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    elif callback_data.startswith("fav_toggle_"):
        async with DB_SEMAPHORE:
            unique_code = callback_data.replace("fav_toggle_", "")
            
            # Se já é favorito -> REMOVE
            if await db.is_favorite(user_id, unique_code):
                await db.remove_favorite(user_id, unique_code)
                msg_text = "🗑️ Removido da lista."
            
            # Se não é -> ADICIONA
            else:
                # Recupera dados para salvar
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

                # Busca dados frescos no DB
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

    elif callback_data.startswith("fav_watch_"):
        async with DB_SEMAPHORE:
            unique_code = callback_data.replace("fav_watch_", "")
            fav_item = await db.get_favorite_item(user_id, unique_code)
            
            if not fav_item:
                await safe_call(query, "answer", text="Erro: Item não encontrado.", show_alert=True)
                return

            await safe_call(query, "delete_message")
            status_msg = await context.bot.send_message(chat_id=user_id, text=f"🔄 Recuperando **{fav_item['title']}**...")
            
            # --- CONSTRUÇÃO DOS BOTÕES DE NAVEGAÇÃO ---
            base_keyboard = []
            
            # Identifica se é Filme ou Série pelo código (mov_... ou ep_...)
            if unique_code.startswith("ep_"):
                # É SÉRIE: Precisamos calcular Anterior/Próximo
                try:
                    parts = unique_code.split('_')
                    episode_id = int(parts[1])
                    
                    # Busca detalhes completos para saber a temporada
                    ep_details = await db.get_full_episode_details(episode_id)
                    
                    if ep_details:
                        season = ep_details.get('seasons')
                        if season:
                            season_id = season.get('id')
                            current_ep_num = ep_details.get('episode_number', 0)
                            series_id = season.get('series_id')
                            current_season_num = season.get('season_number', 1) # Importante pegar o número da temporada

                            # Busca vizinhos em paralelo (dentro da mesma temporada)
                            prev_ep, next_ep = await asyncio.gather(
                                db.get_neighbor_episode(season_id, current_ep_num, 'previous'),
                                db.get_neighbor_episode(season_id, current_ep_num, 'next')
                            )

                            # =========================================================
                            # CORREÇÃO: LÓGICA DE PULAR TEMPORADA
                            # =========================================================
                            
                            # Se NÃO achou próximo episódio (fim da temporada atual)
                            if not next_ep:
                                # Busca todas as temporadas para achar a próxima
                                all_seasons = await db.get_seasons_for_series(series_id)
                                
                                if all_seasons:
                                    # Procura a temporada X + 1
                                    next_season_obj = next((s for s in all_seasons if s['season_number'] == current_season_num + 1), None)
                                    
                                    if next_season_obj:
                                        # Pega o Ep 1 da nova temporada
                                        eps_next_season, _ = await db.get_episodes_for_season(next_season_obj['id'], limit=1, offset=0)
                                        if eps_next_season:
                                            next_ep = eps_next_season[0]
                                            print(f"✅ Próxima temporada encontrada! Botão vai apontar para S{next_season_obj['season_number']}E01")

                            # Se NÃO achou anterior (início da temporada) e não é a temp 1
                            if not prev_ep and current_season_num > 1:
                                all_seasons = await db.get_seasons_for_series(series_id) # (O python geralmente cacheia essa call se for seguida)
                                if all_seasons:
                                    prev_season_obj = next((s for s in all_seasons if s['season_number'] == current_season_num - 1), None)
                                    if prev_season_obj:
                                        # Pega o último ep da temporada anterior
                                        eps_prev, _ = await db.get_episodes_for_season(prev_season_obj['id'], limit=100, offset=0)
                                        if eps_prev:
                                            prev_ep = eps_prev[-1]

                            # =========================================================
                            
                            nav_row = []
                            if prev_ep:
                                nav_row.append(InlineKeyboardButton("⏪ Ep. Anterior", callback_data=f"ep_nav_{prev_ep['id']}"))
                            
                            if next_ep:
                                # Verifica se mudou de temporada para ajustar o texto do botão
                                # Se o season_id do próximo ep for diferente do atual, mudou de temporada.
                                is_new_season = next_ep.get('season_id') != season_id
                                btn_text = "Próxima Temp. ⏩" if is_new_season else "Próximo Ep. ⏩"
                                
                                nav_row.append(InlineKeyboardButton(btn_text, callback_data=f"ep_nav_{next_ep['id']}"))
                            
                            if nav_row:
                                base_keyboard.append(nav_row)
                            
                            # Botões extras de série
                            base_keyboard.append([
                                InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{series_id}_series"),
                                InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=f"ep_card:{episode_id}")
                            ])
                except Exception as e:
                    print(f"[FAV] Erro ao gerar navegação de série: {e}")
            
            else:
                # É FILME: Botões padrão de filme
                try:
                    parts = unique_code.split('_')
                    movie_id = int(parts[1])
                    base_keyboard.append([
                        InlineKeyboardButton("🍿 Relacionados", callback_data=f"related_{movie_id}"),
                        InlineKeyboardButton("Compartilhar ❤️", switch_inline_query=fav_item['title'])
                    ])
                except: pass

            # --- GERA O BOTÃO "REMOVER DA LISTA" E JUNTA TUDO ---
            # A função helper vai pegar nosso base_keyboard e adicionar o botão [X Remover]
            video_markup = await get_fav_keyboard_markup(user_id, unique_code, base_keyboard)
            
            caption_text = f"🍿 **{fav_item['title']}**\n\n🔖 Recuperado da Minha Lista.\n⚠️ *Apaga em 4 horas.*"

            # --- ENVIO COM SEGURANÇA (PLANO A + B) ---
            try:
                # PLANO A: File ID
                print(f"[FAV] Plano A: File ID {str(fav_item['file_id'])[:15]}...")
                sent_message = await context.bot.send_video(
                    chat_id=user_id,
                    video=fav_item['file_id'],
                    caption=caption_text,
                    parse_mode="Markdown",
                    reply_markup=video_markup,
                    protect_content=True
                )
                
                job_data = {'chat_id': sent_message.chat_id, 'message_id': sent_message.message_id}
                context.job_queue.run_once(delete_message_job, when=14400, data=job_data, name=f"del_{user_id}_{sent_message.message_id}")
                
            except BadRequest as e:
                error_text = str(e).lower()
                if ("wrong file id" in error_text or "wrong file identifier" in error_text) and fav_item['message_id'] and fav_item['channel_id']:
                    
                    # PLANO B: Cópia
                    print(f"[FAV] 🚨 Plano B: Copiando msg {fav_item['message_id']} do canal {fav_item['channel_id']}")
                    try:
                        copied_message = await context.bot.copy_message(
                            chat_id=user_id,
                            from_chat_id=fav_item['channel_id'],
                            message_id=fav_item['message_id'],
                            protect_content=True
                        )
                        
                        await context.bot.edit_message_caption(
                            chat_id=user_id,
                            message_id=copied_message.message_id,
                            caption=caption_text,
                            parse_mode="Markdown",
                            reply_markup=video_markup
                        )
                        
                        job_data = {'chat_id': user_id, 'message_id': copied_message.message_id}
                        context.job_queue.run_once(delete_message_job, when=14400, data=job_data, name=f"del_{user_id}_{copied_message.message_id}")
                        
                    except Exception as e_inner:
                        print(f"[FAV] ❌ Falha total: {e_inner}")
                        await status_msg.edit_text("❌ Erro fatal: O arquivo original foi apagado do canal.")
                        return
                else:
                    print(f"[FAV] Erro genérico: {e}")
                    await status_msg.edit_text("❌ Erro ao enviar vídeo.")
                    return

            await status_msg.delete()

    elif callback_data.startswith("adm_approve_") or callback_data.startswith("adm_deny_"):
        # 🔒 SEGURANÇA
        if user_id not in ADMIN_IDS:
            await safe_call(query, "answer", text="🚫 Acesso negado.", show_alert=True)
            return

        async with DB_SEMAPHORE:
            # CORREÇÃO CRÍTICA AQUI:
            # O callback é: adm_approve_69
            parts = callback_data.split('_')
            # parts[0] = "adm"
            # parts[1] = "approve" ou "deny"
            # parts[2] = "69" (ID)
            
            action = parts[1] 
            req_id = parts[2]
            
            # Agora a comparação funciona!
            new_status = "added" if action == "approve" else "denied"
            
            # 1. Atualiza no Banco
            request_data = await db.update_request_status(req_id, new_status)
            
            if request_data:
                target_user_id = request_data.get('user_id')
                title = request_data.get('requested_title', 'Filme/Série') # Valor padrão se vier None
                
                # 2. Notifica o Usuário
                if target_user_id:
                    try:
                        msg_user = ""
                        if new_status == "added":
                            msg_user = (
                                f"🎉 **Boas notícias!**\n\n"
                                f"O título que você pediu, **'{title}'**, foi aprovado e adicionado ao catálogo! 🍿\n"
                                f"Use a busca para assistir agora."
                            )
                        else:
                            msg_user = (
                                f"🔔 **Atualização sobre seu pedido**\n\n"
                                f"Infelizmente, seu pedido para **'{title}'** não pode ser atendido no momento."
                            )
                        
                        await context.bot.send_message(chat_id=target_user_id, text=msg_user, parse_mode="Markdown")
                        admin_feedback = "✅ Usuário notificado."
                    except Exception as e:
                        print(f"Erro ao notificar user {target_user_id}: {e}")
                        admin_feedback = "⚠️ Status salvo, mas falha ao notificar."
                else:
                    admin_feedback = "⚠️ User ID não achado."

                # 3. Atualiza a mensagem do Admin
                emoji_status = "✅ APROVADO" if new_status == "added" else "❌ NEGADO"
                original_text = query.message.text
                
                # Remove os botões para não clicar de novo
                await safe_call(query, "edit_message_text", 
                    text=f"{original_text}\n\n🏁 **Processado:** {emoji_status}\nℹ️ {admin_feedback}", 
                    parse_mode="Markdown", 
                    reply_markup=None
                )
            
            else:
                await safe_call(query, "answer", text="❌ Erro ao atualizar. Tente de novo.", show_alert=True)

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

                    config = await db.get_bot_config()
                    price = config.get('vip_price', 4.99)
                    is_free = price <= 0

                    if not is_vip and not is_free:
                        # --- MUDANÇA (v7.0): Aponta para o /start?vip ---
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
                                    message_text=(
                                        f"Ei {update.inline_query.from_user.first_name}! 👋\n\n"
                                        "Para maratonar esta e **todas as outras séries**, você precisa do 🍿 **Acesso Pipoca Premium**!"
                                    ),
                                    parse_mode="Markdown",
                                )
                            )
                        )
                        # --- FIM DA MUDANÇA ---
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
            # --- MUDANÇA (v7.0): Usa o helper ---
            config = await db.get_bot_config()
            price = config.get('vip_price', 4.99)
            duration_days = config.get('vip_duration_days', 7)

            if price <= 0:
                # É GRÁTIS!
                await db.set_user_as_vip(user_id, duration_days=duration_days if duration_days > 0 else 9999)
                try:
                    # Tenta enviar uma mensagem nova
                    await context.bot.send_message(chat_id=user_id, text="🎉 Bem-vindo! O acesso está gratuito no momento. Carregando...")
                except Exception:
                    pass # Se falhar, não importa, o código continua
                
                # NÃO damos 'return', o código continua e libera o acesso
            else:
                # É PAGO! (Esta é a lógica antiga)
                context.user_data['update'] = update
                sales_text, reply_markup = await _get_vip_sales_message(context)
                
                # A LINHA CORRETA PARA ESTE LUGAR
                await context.bot.send_message( 
                    chat_id=user_id,
                    text=f"Opa, {update.effective_user.first_name}! 👋\n\n{sales_text}",
                    parse_mode="Markdown",
                    reply_markup=reply_markup
                )
                return # <-- IMPORTANTE: Bloqueia o usuário

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

# =================================================================
# === GESTÃO DE PEDIDOS (ADMIN NO USER BOT) ===
# =================================================================
async def pedidos_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Lista os pedidos pendentes para o Admin aprovar ou negar.
    """
    user_id = update.effective_user.id
    
    # 🔒 SEGURANÇA: Só Admins podem usar
    if user_id not in ADMIN_IDS:
        return # Ignora silenciosamente

    async with DB_SEMAPHORE:
        pending = await db.get_pending_requests()
        
        if not pending:
            await update.message.reply_text("✅ **Zero Pendências!**\nNão há novos pedidos no momento.")
            return

        await update.message.reply_text(f"📋 **Gerenciamento de Pedidos**\nExistem {len(pending)} pedidos na fila:")

        for req in pending:
            req_id = req['request_id']
            title = req['requested_title']
            user_req_id = req['user_id']
            # Data formatada (opcional, se tiver o campo created_at)
            date_str = req.get('created_at', 'Data desc.')[:10] 
            
            # Botões de Ação
            keyboard = [
                [
                    # Callback data contém a ação e o ID do pedido
                    InlineKeyboardButton("✅ Aprovar", callback_data=f"adm_approve_{req_id}"),
                    InlineKeyboardButton("❌ Negar", callback_data=f"adm_deny_{req_id}")
                ]
            ]
            
            await update.message.reply_text(
                f"🆔 **Pedido #{req_id}**\n"
                f"👤 User ID: `{user_req_id}`\n"
                f"📅 Data: {date_str}\n\n"
                f"🎬 **Solicitação:**\n`{title}`",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
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

async def get_fav_keyboard_markup(user_id, unique_code, current_keyboard=None):
    """
    Adiciona ou atualiza o botão de Favoritos em um teclado existente.
    """
    is_fav = await db.is_favorite(user_id, unique_code)
    
    btn_text = "❌ Remover da Lista" if is_fav else "🔖 Salvar na Lista"
    callback = f"fav_toggle_{unique_code}"
    
    fav_button = [InlineKeyboardButton(btn_text, callback_data=callback)]
    
    if current_keyboard:
        new_keyboard = []
        replaced = False
        for row in current_keyboard:
            new_row = []
            for btn in row:
                # Se encontrar um botão antigo de fav, substitui
                if btn.callback_data and btn.callback_data.startswith("fav_toggle_"):
                    new_row.append(InlineKeyboardButton(btn_text, callback_data=callback))
                    replaced = True
                else:
                    new_row.append(btn)
            new_keyboard.append(new_row)
        
        if not replaced:
            new_keyboard.append(fav_button)
        return InlineKeyboardMarkup(new_keyboard)
    else:
        return InlineKeyboardMarkup([fav_button])

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
set_config_handler = CommandHandler("setconfig", set_config_command, filters=filters.User(user_id=ADMIN_IDS))
set_text_handler = CommandHandler("settext", set_text_command, filters=filters.User(user_id=ADMIN_IDS))
show_config_handler = CommandHandler("showconfig", show_config_command, filters=filters.User(user_id=ADMIN_IDS))
pedidos_handler = CommandHandler("pedidos", pedidos_command_handler)
