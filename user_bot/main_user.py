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
from telegram.ext import Application
from ptbcontrib.aiohttp_request import AiohttpRequest
import aiohttp
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
# 🔁 ERROR HANDLER
# ==========================================================
async def error_handler(update: object, context):
    e = context.error
    print(f"[DEBUG] ERROR_HANDLER ATIVADO! Erro: {e}")
    if isinstance(e, (NetworkError, aiohttp.ClientError)):
        print(f"⚠️ NetworkError detectado ({type(e).__name__}) — tentando novamente em 3s...")
        await asyncio.sleep(3)
        if update and isinstance(update, Update):
            try:
                # print(f"🔄 Tentando re-processar update: {update.update_id}")
                await context.application.process_update(update)
            except Exception as retry_err:
                print(f"❌ Falha no retry automático: {retry_err}")
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
    if final_proxy_url:
        print(f"✅ [PROXY] Usando proxy configurado.")
    else:
        print("⚠️ [PROXY] Rodando sem proxy.")

    try:
        timeout = aiohttp.ClientTimeout(total=600, connect=60, sock_read=600, sock_connect=60)
        request_motor = AiohttpRequest(client_timeout=timeout, connection_pool_size=256, proxy=final_proxy_url)
        try:
            application = Application.builder().token(BOT_TOKEN).request(request_motor).get_updates_request(request_motor).build()
            print("✅ Application criada com AiohttpRequest")
        except Exception as e:
            print(f"⚠️ Falha com AiohttpRequest: {e}. Tentando fallback para HTTPX...")
            request_motor = HTTPXRequest(connect_timeout=30.0, read_timeout=300.0, write_timeout=300.0, pool_timeout=30.0, proxy_url=final_proxy_url)
            application = Application.builder().token(BOT_TOKEN).request(request_motor).get_updates_request(request_motor).build()
            print("✅ Application criada com HTTPXRequest (fallback).")

        application.add_handler(handlers.start_handler)
        application.add_handler(handlers.button_click_handler)
        application.add_handler(handlers.inline_search_handler)
        application.add_handler(handlers.watch_handler)
        application.add_handler(handlers.text_handler)
        application.add_handler(handlers.cancel_command_handler)
        application.add_handler(handlers.help_command_handler)
        application.add_handler(handlers.request_command_handler)
        application.add_error_handler(error_handler)

        await application.initialize()
        
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
    
    # 1. Captura e validação do User ID (mais robusta)
    # Aceita qualquer coisa da URL e tenta limpar antes de converter
    raw_user_id = request.path_params.get('user_id')
    try:
        # Converte para string primeiro, remove espaços em branco e então para int
        user_id = int(str(raw_user_id).strip())
    except (ValueError, TypeError):
        print(f"[Webhook PushinPay] ❌ ERRO FATAL: ID inválido recebido na URL: '{raw_user_id}'")
        return JSONResponse({"status": "error", "message": "Invalid user_id format"}, status_code=400)

    # 2. Leitura do corpo da requisição (JSON)
    try:
        data = await request.json()
    except json.JSONDecodeError:
        print(f"[Webhook PushinPay] ❌ ERRO: Corpo da requisição não é um JSON válido (UserID: {user_id})")
        return JSONResponse({"status": "received_but_invalid_json"})

    # 3. Processamento do status
    payment_status = data.get("status")
    print(f"[Webhook PushinPay] 🔔 Recebido para UserID: {user_id} | Status: {payment_status}")
    
    if payment_status == "paid":
        try:
            # Verifica se já é VIP para evitar duplicidade
            if not await db.is_user_vip(user_id):
                await db.set_user_as_vip(user_id, duration_days=30)
                await db.clear_user_active_payment_id(user_id)
                print(f"✅ VIP ATIVADO para UserID: {user_id}")
                
                # Tenta notificar o usuário (não quebra se falhar)
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text="🎉 **Pagamento confirmado!** 🎉\n\nSeu **Acesso Pipoca Premium** foi ativado!\nAproveite o catálogo sem limites. 🍿",
                        parse_mode="Markdown"
                    )
                except Exception as e_msg:
                     print(f"⚠️ Não foi possível enviar msg de confirmação para {user_id}: {e_msg}")
            else:
                 print(f"ℹ️ UserID {user_id} já era VIP. Apenas limpando pendências.")
                 await db.clear_user_active_payment_id(user_id)
                 
        except Exception as e_db:
            print(f"❌ ERRO AO SALVAR VIP NO BANCO para {user_id}: {e_db}")
            return JSONResponse({"status": "error", "message": "Database error"}, status_code=500)

    return JSONResponse({"status": "received"})

# ==========================================================
# 🔔 WEBHOOK SUPABASE
# ==========================================================
async def supabase_webhook(request: Request) -> Response:
    await APP_INITIALIZED.wait()
    try:
        data = await request.json()
        uid, title, status = data.get("user_id"), data.get("title"), data.get("new_status")
        if uid and status:
            msg = f"🎉 '{title}' disponível!" if status == "added" else (f"😔 Pedido '{title}' negado." if status == "denied" else "")
            if msg:
                try: await application.bot.send_message(uid, msg)
                except: pass
        return Response(status_code=200)
    except Exception: return Response(status_code=500)

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
    