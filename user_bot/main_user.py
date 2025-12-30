import sys
import os
import logging
import asyncio
import json
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, JSONResponse
from telegram import Update
from telegram.ext import Application, PicklePersistence
from ptbcontrib.aiohttp_request import AiohttpRequest
import aiohttp
from aiohttp_socks import ProxyConnector
from telegram.request import HTTPXRequest
from telegram.error import NetworkError

import handlers_user as handlers
import database as db
# CERTIFIQUE-SE QUE ESTAS VARIÁVEIS ESTÃO NO SEU CONFIG.PY
from config import BOT_TOKEN, PROXY_URL, WEBHOOK_DOMAIN, TELEGRAM_WEBHOOK_PATH

# --- DEBUG PRINT ---
print("[DEBUG] Versão do código: 1.9 (Webhook PushinPay BLINDADO)")
# ---------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

application: Application = None
APP_INITIALIZED = asyncio.Event()
session: aiohttp.ClientSession | None = None

# ==========================================================
# 🔁 ERROR HANDLER (CORRIGIDO - SEM LOOP INFINITO)
# ==========================================================
async def error_handler(update: object, context):
    e = context.error
    print(f"[DEBUG] ERROR_HANDLER ATIVADO! Erro: {e}")

    # --- INÍCIO DA CORREÇÃO ---
    # Agora, tratamos erros de rede da mesma forma que outros erros:
    # apenas registramos.
    if isinstance(e, (NetworkError, aiohttp.ClientError)):
        print(f"❌ ERRO DE REDE (Captura Global): {e}")
        print("O bot tentou 3 vezes (dentro do handler) e falhou. Desistindo deste update.")
        # NÃO vamos re-processar o update, para evitar o loop infinito.
        return
    # --- FIM DA CORREÇÃO ---

    # Esta parte (para erros que não são de rede) continua igual
    print(f"❌ Erro não-rede no handler: {e}")
    import traceback
    traceback.print_exc()

# ==========================================================
# 🚀 STARTUP DO BOT
# ==========================================================
async def startup():
    global application, session
    print("[DEBUG] Função startup() iniciada.")
    final_proxy_url: str | None = PROXY_URL
    if final_proxy_url:
        print(f"✅ [PROXY] Usando proxy configurado.")
    else:
        print("⚠️ [PROXY] Rodando sem proxy.")

    try:
        print("🔵 Usando AiohttpRequest (ptb-contrib)...")
        
        timeout = aiohttp.ClientTimeout(
            total=600, 
            connect=60, 
            sock_read=600, 
            sock_connect=60
        )
    
        request_motor = AiohttpRequest(
            client_timeout=timeout, 
            connection_pool_size=256, 
            socks_url=final_proxy_url  # <--- O argumento correto para Aiohttp
        )

        persistence = PicklePersistence(filepath="bot_persistence.pkl")

        print("🔵 Criando Application do Bot com Persistência...")
        application = Application.builder().token(BOT_TOKEN).request(request_motor).get_updates_request(request_motor).persistence(persistence).build()
        print("✅ Application criada com AiohttpRequest.")

        application.add_handler(handlers.start_handler)
        application.add_handler(handlers.button_click_handler)
        application.add_handler(handlers.inline_search_handler)
        application.add_handler(handlers.watch_handler)
        application.add_handler(handlers.text_handler)
        application.add_handler(handlers.cancel_command_handler)
        application.add_handler(handlers.help_command_handler)
        application.add_handler(handlers.request_command_handler)
        application.add_handler(handlers.broadcast_handler)
        application.add_handler(handlers.set_config_handler)
        application.add_handler(handlers.set_text_handler)
        application.add_handler(handlers.show_config_handler)
        application.add_error_handler(error_handler)

        await application.initialize()
        await application.start()
        
        # Configura Webhook Telegram (garante sincronia com config.py)
        webhook_url = f"{WEBHOOK_DOMAIN}{TELEGRAM_WEBHOOK_PATH}"
        print(f"ℹ️ Configurando webhook do Telegram para: {webhook_url}")
        await application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)

        print("✅ Bot inicializado com sucesso.")
        APP_INITIALIZED.set()
    except Exception as e:
        print(f"❌ ERRO CRÍTICO NO STARTUP: {e}")
        import traceback
        traceback.print_exc()
        raise

# ==========================================================
# 🧹 SHUTDOWN
# ==========================================================
async def shutdown():
    global session, application
    print("[DEBUG] Encerrando...")
    try:
        if application:
            await application.shutdown()
            await application.stop()
            print("✅ Application encerrada.")
        if session and not session.closed:
            await session.close()
            print("✅ Sessão aiohttp fechada.")
    except Exception as e:
        print(f"⚠️ Erro durante shutdown: {e}")

# ==========================================================
# 📩 WEBHOOK TELEGRAM
# ==========================================================
async def telegram_webhook(request: Request) -> Response:
    await APP_INITIALIZED.wait()
    try:
        data = await request.json()
        if not isinstance(data, dict) or "update_id" not in data:
            return Response("ok", status_code=200)
        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
    except Exception as e:
        print(f"❌ Erro webhook Telegram: {e}")
    return Response("ok", status_code=200)

# ==========================================================
# 💸 WEBHOOK PUSHINPAY (CORRIGIDO E BLINDADO)
# ==========================================================
async def pushinpay_webhook(request: Request) -> Response:
    await APP_INITIALIZED.wait()
    
    # --- DEBUG: Print do corpo bruto da requisição ---
    try:
        raw_body = await request.body()
        print(f"[Webhook PushinPay DEBUG] Raw Body: {raw_body.decode('utf-8', errors='ignore')}")
    except Exception as e_debug:
        print(f"[Webhook PushinPay DEBUG] Falha ao ler raw body: {e_debug}")
    # -------------------------------------------------

    # 1. Captura e validação do User ID
    raw_user_id = request.path_params.get('user_id')
    try:
        user_id = int(str(raw_user_id).strip())
    except (ValueError, TypeError):
        print(f"[Webhook PushinPay] ❌ ERRO FATAL: ID inválido recebido na URL: '{raw_user_id}'")
        return JSONResponse({"status": "error", "message": "Invalid user_id format"}, status_code=400)

    # 2. Leitura do corpo da requisição (TENTA JSON, DEPOIS FORM)
    data = None
    try:
        # Tenta ler como JSON (vai falhar neste caso)
        data = await request.json()
    except json.JSONDecodeError:
        # Se falhar, tenta ler como FORM (o formato correto que recebemos)
        print(f"[Webhook PushinPay] ℹ️ JSON falhou, tentando ler como FORM... (UserID: {user_id})")
        try:
            form_data = await request.form()
            data = dict(form_data) # Converte para um dicionário normal
        except Exception as e_form:
            print(f"[Webhook PushinPay] ❌ ERRO CRÍTICO: Nem JSON nem FORM. Erro: {e_form}")
            return JSONResponse({"status": "received_but_invalid_format"})

    if not data:
         print(f"[Webhook PushinPay] ❌ ERRO: Corpo vazio.")
         return JSONResponse({"status": "received_but_empty"})

    # 3. Processamento do status (agora 'data' deve ter os dados corretos)
    payment_status = data.get("status")
    print(f"[Webhook PushinPay] 🔔 UserID: {user_id} | Status: {payment_status}")
    
    if payment_status == "paid":
        try:
            try:
                # 1. Busca os detalhes do usuário para achar o ID da mensagem
                user_data = await db.get_user_details(user_id)
                qr_msg_id = user_data.get('qr_message_id') if user_data else None

                # 2. Se tiver um ID salvo, tenta apagar
                if qr_msg_id:
                    await application.bot.delete_message(chat_id=user_id, message_id=qr_msg_id)
                    print(f"🗑️ QR Code antigo (msg {qr_msg_id}) apagado para UserID {user_id}")
            except Exception as e_del:
                # Se falhar ao apagar (msg muito antiga, já apagada, etc), só ignora e segue.
                print(f"⚠️ Não foi possível apagar QR code antigo: {e_del}")
                
            if not await db.is_user_vip(user_id):
                await db.set_user_as_vip(user_id, duration_days=7) # Ativa VIP por 7 dias
                await db.clear_user_active_payment_id(user_id)
                print(f"✅ VIP ATIVADO para UserID: {user_id}")
                
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text="🎉 **Pagamento confirmado!** 🎉\n\nSeu **Acesso Pipoca Premium** foi ativado!\nAproveite o catálogo sem limites. 🍿",
                        parse_mode="Markdown"
                    )
                except Exception as e_msg:
                     print(f"⚠️ Erro ao notificar UserID {user_id}: {e_msg}")
            else:
                 print(f"ℹ️ UserID {user_id} já era VIP.")
                 await db.clear_user_active_payment_id(user_id)
        except Exception as e_db:
            print(f"❌ ERRO AO SALVAR VIP: {e_db}")
            return JSONResponse({"status": "error", "message": "Database error"}, status_code=500)

    return JSONResponse({"status": "received"})

# ==========================================================
# 🔔 WEBHOOK SUPABASE (ATUALIZADO)
# ==========================================================
async def supabase_webhook(request: Request) -> Response:
    # Espera o bot iniciar antes de tentar enviar mensagem
    await APP_INITIALIZED.wait()
    
    try:
        data = await request.json()
        print(f"--- 🔔 WEBHOOK SUPABASE RECEBIDO ---\n{data}")

        # Pega os dados que o Supabase enviou
        user_id = data.get('user_id')
        title = data.get('title')
        new_status = data.get('new_status') # 'added' ou 'denied'
        
        if user_id and new_status:
            message = ""
            
            # Lógica da mensagem personalizada
            if new_status == 'added':
                message = f"🎉 **Boas notícias!**\n\nO título que você pediu, **'{title}'**, acabou de ser adicionado ao nosso catálogo!\n\nUse a busca para assistir agora. 🍿"
            elif new_status == 'denied':
                message = f"😔 **Olá!**\n\nSobre o seu pedido **'{title}'**: infelizmente não conseguimos adicioná-lo ao catálogo no momento."
            
            if message:
                try:
                    # Usa o 'application.bot' que já existe no main.py
                    await application.bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                    print(f"✅ Notificação enviada para {user_id}")
                except Exception as e_msg:
                    print(f"⚠️ Erro ao enviar mensagem para o usuário {user_id}: {e_msg}")
        
        return Response(status_code=200)

    except Exception as e:
        print(f"❌ Erro ao processar webhook do Supabase: {e}")
        return Response(status_code=500)

# ==========================================================
# 🩺 HEALTH CHECK
# ==========================================================
async def health_check(request: Request) -> Response:
    return Response("Online!", status_code=200)

# ==========================================================
# 🛠️ ROTAS
# ==========================================================
routes = [
    Route(TELEGRAM_WEBHOOK_PATH, endpoint=telegram_webhook, methods=["POST"]),
    # Rota flexível: {user_id} sem :int para permitir nossa validação manual blindada
    Route("/webhook/pushinpay/{user_id}", endpoint=pushinpay_webhook, methods=["POST"]),
    Route("/webhook/supabase", endpoint=supabase_webhook, methods=["POST"]),
    Route("/health", endpoint=health_check, methods=["GET"]),
]

app = Starlette(routes=routes, on_startup=[startup], on_shutdown=[shutdown])

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"[WEB] Iniciando na porta {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)
