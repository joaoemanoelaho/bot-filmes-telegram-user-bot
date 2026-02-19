import sys
import os
import logging
import asyncio
import json
import secrets
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
from handlers import (
    manutencao, 
    start, 
    vip, 
    pedidos, 
    admin, 
    player, 
    busca, 
    utils_fav
)
from telegram.ext import CommandHandler, CallbackQueryHandler, MessageHandler, filters, InlineQueryHandler
# CERTIFIQUE-SE QUE ESTAS VARIÁVEIS ESTÃO NO SEU CONFIG.PY
from config import BOT_TOKEN, PROXY_URL, WEBHOOK_DOMAIN, TELEGRAM_WEBHOOK_PATH, WEBHOOK_SECRET

# --- DEBUG PRINT ---
print("[DEBUG] Versão do código: 2.0 (SyncPay Integration)")
# ---------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

application: Application = None
APP_INITIALIZED = asyncio.Event()
session: aiohttp.ClientSession | None = None

# ==========================================================
# 🔁 ERROR HANDLER
# ==========================================================
async def error_handler(update: object, context):
    e = context.error
    print(f"[DEBUG] ERROR_HANDLER ATIVADO! Erro: {e}")

    if isinstance(e, (NetworkError, aiohttp.ClientError)):
        print(f"❌ ERRO DE REDE (Captura Global): {e}")
        return

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

    try:
        timeout = aiohttp.ClientTimeout(total=600, connect=60, sock_read=600, sock_connect=60)
    
        request_motor = AiohttpRequest(
            client_timeout=timeout, 
            connection_pool_size=256, 
            socks_url=final_proxy_url
        )

        persistence = PicklePersistence(filepath="bot_persistence.pkl")

        print("🔵 Criando Application do Bot com Persistência...")
        application = Application.builder().token(BOT_TOKEN).request(request_motor).get_updates_request(request_motor).persistence(persistence).build()
        
        # 1. START & MENU
        application.add_handler(CommandHandler("start", start.start))
        application.add_handler(CommandHandler("help", start.help_handler))
        application.add_handler(CommandHandler("cancelar", start.cancel_handler))
        application.add_handler(CallbackQueryHandler(start.back_to_main_handler, pattern="^back_to_main$"))

        # 2. VIP & PIX
        application.add_handler(CallbackQueryHandler(vip.vip_menu_callback, pattern="^main_vip$"))
        application.add_handler(CallbackQueryHandler(vip.confirm_pay_callback, pattern="^confirm_pay$"))

        # 3. PLAYER & NAVEGAÇÃO
        application.add_handler(CommandHandler("watch", player.watch_command_handler))
        application.add_handler(CallbackQueryHandler(player.player_callback, pattern="^(play_|ep_nav_|related_|show_card_|main_top|top_)"))

        # 4. FAVORITOS
        application.add_handler(CallbackQueryHandler(utils_fav.fav_menu_callback, pattern="^fav_menu$"))
        application.add_handler(CallbackQueryHandler(utils_fav.fav_toggle_callback, pattern="^fav_toggle_"))
        application.add_handler(CallbackQueryHandler(utils_fav.fav_watch_callback, pattern="^fav_watch_"))

        # 5. ADMIN
        application.add_handler(CommandHandler("setconfig", admin.set_config_command))
        application.add_handler(CommandHandler("settext", admin.set_text_command))
        application.add_handler(CommandHandler("showconfig", admin.show_config_command))
        application.add_handler(CommandHandler("pedidos", admin.pedidos_command_handler))
        application.add_handler(CommandHandler("transmissao", admin.broadcast_command_handler))
        application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, admin.text_message_handler))
        application.add_handler(CallbackQueryHandler(admin.admin_callback, pattern="^adm_"))

        # 6. PEDIDOS
        application.add_handler(CommandHandler("pedir", pedidos.request_command_handler))
        application.add_handler(CallbackQueryHandler(pedidos.request_start_callback, pattern="^main_request$"))
        
        # 7. BUSCA
        application.add_handler(InlineQueryHandler(busca.inline_query_handler))
        application.add_error_handler(error_handler)

        await application.initialize()
        await application.start()
        
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
# 💸 WEBHOOK SYNCPAY (NOVO)
# ==========================================================
async def syncpay_webhook(request: Request) -> Response:
    await APP_INITIALIZED.wait()

    # === 🔒 BLINDAGEM DE SEGURANÇA ===
    # Pega a senha que veio na URL
    secret_token = request.query_params.get('secret')
    
    # Verifica se a senha é EXATAMENTE igual a que você colocou no payments.py
    if not secret_token or not secrets.compare_digest(secret_token, WEBHOOK_SECRET):
        print(f"[ALERTA DE SEGURANÇA] 🚨 Tentativa de invasão bloqueada! IP tentou acessar sem senha.")
        return JSONResponse({"status": "error", "message": "Acesso Negado. Senha incorreta."}, status_code=403)
    # =================================
    
    # 1. Captura e validação do User ID na URL
    raw_user_id = request.path_params.get('user_id')
    try:
        user_id = int(str(raw_user_id).strip())
    except (ValueError, TypeError):
        print(f"[Webhook SyncPay] ❌ ID inválido recebido: '{raw_user_id}'")
        return JSONResponse({"status": "error", "message": "Invalid user_id"}, status_code=400)

    # 2. Leitura do JSON
    try:
        payload = await request.json()
        # A SyncPay manda tudo dentro de um objeto 'data'
        data = payload.get('data', {}) 
    except Exception as e:
        print(f"[Webhook SyncPay] ❌ Erro ao ler JSON: {e}")
        return JSONResponse({"status": "error"}, status_code=400)

    if not data:
         print(f"[Webhook SyncPay] ❌ Payload vazio ou sem 'data'.")
         return JSONResponse({"status": "ignored"})

    # 3. Processamento do status
    # Status possíveis: 'completed', 'pending', 'failed'
    payment_status = data.get("status")
    print(f"[Webhook SyncPay] 🔔 UserID: {user_id} | Status: {payment_status}")
    
    if payment_status in ["completed", "PAID_OUT", "APPROVED", "paid", "SETTLED"]:
        try:
            # Apaga a mensagem do QR Code antigo se existir
            try:
                user_data = await db.get_user_details(user_id)
                qr_msg_id = user_data.get('qr_message_id') if user_data else None
                if qr_msg_id:
                    await application.bot.delete_message(chat_id=user_id, message_id=qr_msg_id)
            except Exception: pass
            
            # ATIVA O VIP
            if not await db.is_user_vip(user_id):
                # Pega configuração de dias ou usa padrão 30
                config = await db.get_bot_config()
                duration = config.get('vip_duration_days', 30)
                
                await db.set_user_as_vip(user_id, duration_days=duration)
                await db.clear_user_active_payment_id(user_id)
                print(f"✅ VIP ATIVADO (SyncPay) para UserID: {user_id}")
                
                # Manda mensagem de sucesso
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text="✅ **Pagamento Confirmado!** 🚀\n\nSeu acesso VIP foi liberado com sucesso.\nObrigado por apoiar o Cine Pipoca! 🍿",
                        parse_mode="Markdown"
                    )
                except Exception: pass
            else:
                print(f"ℹ️ UserID {user_id} já era VIP.")
                await db.clear_user_active_payment_id(user_id)
                
        except Exception as e_db:
            print(f"❌ ERRO AO SALVAR VIP (SyncPay): {e_db}")
            return JSONResponse({"status": "error"}, status_code=500)

    return JSONResponse({"status": "received"})

# ==========================================================
# 🔔 WEBHOOK SUPABASE
# ==========================================================
async def supabase_webhook(request: Request) -> Response:
    await APP_INITIALIZED.wait()
    try:
        data = await request.json()
        user_id = data.get('user_id')
        title = data.get('title')
        new_status = data.get('new_status')
        
        if user_id and new_status:
            message = ""
            if new_status == 'added':
                message = f"🎉 **Boas notícias!**\n\nO título **'{title}'** foi adicionado ao catálogo! 🍿"
            elif new_status == 'denied':
                message = f"😔 **Olá!**\n\nSobre o pedido **'{title}'**: infelizmente não conseguimos adicionar no momento."
            
            if message:
                try:
                    await application.bot.send_message(chat_id=user_id, text=message, parse_mode="Markdown")
                except Exception: pass
        return Response(status_code=200)
    except Exception:
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
    # ROTA NOVA SYNCPAY
    Route("/webhook/syncpay/{user_id}", endpoint=syncpay_webhook, methods=["POST"]),
    Route("/webhook/supabase", endpoint=supabase_webhook, methods=["POST"]),
    Route("/health", endpoint=health_check, methods=["GET"]),
]

app = Starlette(routes=routes, on_startup=[startup], on_shutdown=[shutdown])

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    print(f"[WEB] Iniciando na porta {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)