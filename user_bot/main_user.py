import sys
import os
import logging
import asyncio
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response
from telegram import Update
from telegram.ext import Application
from ptbcontrib.aiohttp_request import AiohttpRequest
import aiohttp
from telegram.request import HTTPXRequest
from telegram.error import NetworkError

import handlers_user as handlers
from config import BOT_TOKEN

# --- DEBUG PRINT ---
print("[DEBUG] Versão do código: 1.4 (com shutdown seguro e fallback HTTPX)")
# ---------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

application: Application = None
APP_INITIALIZED = asyncio.Event()
session: aiohttp.ClientSession | None = None  # sessão global aiohttp


# ==========================================================
# 🔁 ERROR HANDLER COM RETRY AUTOMÁTICO
# ==========================================================
async def error_handler(update: object, context):
    print(f"[DEBUG] ERROR_HANDLER ATIVADO! Erro: {context.error}")

    try:
        raise context.error
    except NetworkError:
        print("⚠️ NetworkError detectado — tentando novamente em 3s...")
        await asyncio.sleep(3)
        try:
            if update and hasattr(update, "callback_query"):
                await update.callback_query.answer()
        except Exception as e:
            print(f"❌ Falha no retry automático: {e}")
    except Exception:
        import traceback
        traceback.print_exc()


# ==========================================================
# 🚀 STARTUP DO BOT
# ==========================================================
async def startup():
    """Inicializa o bot ao iniciar o servidor"""
    global application, session

    print("[DEBUG] Função startup() iniciada.")

    try:
        # ---------------------------
        # Criação da sessão AIOHTTP
        # ---------------------------
        timeout = aiohttp.ClientTimeout(
            total=600,
            connect=60,
            sock_read=600,
            sock_connect=60,
        )

        session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=256)
        )

        request_motor = AiohttpRequest(
            client_session=session,
            client_timeout=timeout,
            connection_pool_size=256,
        )

        try:
            # Tenta construir com AIOHTTP
            application = (
                Application.builder()
                .token(BOT_TOKEN)
                .request(request_motor)
                .get_updates_request(request_motor)
                .build()
            )
            print("✅ Application criada com AiohttpRequest")
        except Exception as e:
            print(f"⚠️ Falha ao criar com AiohttpRequest: {e}")
            print("➡️ Tentando fallback para HTTPXRequest...")

            # Fallback automático
            request_motor = HTTPXRequest(
                connect_timeout=30.0,
                read_timeout=300.0,
                write_timeout=300.0,
                pool_timeout=30.0,
                connection_pool_size=256,
            )
            application = (
                Application.builder()
                .token(BOT_TOKEN)
                .request(request_motor)
                .get_updates_request(request_motor)
                .build()
            )
            print("✅ Application criada com HTTPXRequest (fallback).")

        # Handlers
        application.add_handler(handlers.start_handler)
        application.add_handler(handlers.button_click_handler)
        application.add_handler(handlers.inline_search_handler)
        application.add_handler(handlers.watch_handler)
        application.add_handler(handlers.text_handler)
        application.add_handler(handlers.cancel_command_handler)
        application.add_handler(handlers.help_command_handler)
        application.add_handler(handlers.request_command_handler)

        # Error handler
        application.add_error_handler(error_handler)

        await application.initialize()
        print("✅ Bot inicializado com sucesso (webhook pronto).")

        APP_INITIALIZED.set()
        print("[DEBUG] APP_INITIALIZED.set() concluído.")

    except Exception as e:
        print(f"❌ ERRO NO STARTUP: {e}")
        import traceback
        traceback.print_exc()
        raise


# ==========================================================
# 🧹 SHUTDOWN DO BOT
# ==========================================================
async def shutdown():
    """Fecha conexões e libera recursos ao encerrar o servidor"""
    global session, application

    print("[DEBUG] Função shutdown() iniciada — limpando recursos...")

    try:
        if application:
            await application.shutdown()
            await application.stop()
            print("✅ Application encerrada com sucesso.")

        if session and not session.closed:
            await session.close()
            print("✅ Sessão aiohttp fechada com sucesso.")

    except Exception as e:
        print(f"⚠️ Erro durante shutdown: {e}")
        import traceback
        traceback.print_exc()

    print("[DEBUG] Shutdown concluído.")


# ==========================================================
# 📩 TELEGRAM WEBHOOK
# ==========================================================
async def telegram_webhook(request: Request) -> Response:
    print("[DEBUG] /webhook recebido. Aguardando APP_INITIALIZED...")
    await APP_INITIALIZED.wait()
    print("[DEBUG] APP_INITIALIZED set. Processando update...")

    try:
        data = await request.json()
        print(f"📨 Update recebido: {data}")

        if not isinstance(data, dict) or "update_id" not in data:
            print("⚠️ Dados inválidos recebidos no webhook.")
            return Response("ok", status_code=200)

        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        print(f"✅ Update processado: {data.get('update_id')}")

    except Exception as e:
        print(f"❌ Erro ao processar webhook do Telegram: {e}")
        import traceback
        traceback.print_exc()

    return Response("ok", status_code=200)


# ==========================================================
# 🔔 SUPABASE WEBHOOK
# ==========================================================
async def supabase_webhook(request: Request) -> Response:
    print("[DEBUG] /webhook/supabase recebido. Aguardando APP_INITIALIZED...")
    await APP_INITIALIZED.wait()
    print("[DEBUG] APP_INITIALIZED set. Processando Supabase...")

    try:
        data = await request.json()
        print(f"--- WEBHOOK DO SUPABASE ---\n{data}\n---------------------------")

        user_id = data.get("user_id")
        title = data.get("title")
        new_status = data.get("new_status")

        if user_id and new_status:
            message = ""
            if new_status == "added":
                message = f"🎉 O título '{title}' que você pediu já está disponível!"
            elif new_status == "denied":
                message = f"😔 O pedido '{title}' não pôde ser adicionado no momento."

            if message:
                await application.bot.send_message(chat_id=user_id, text=message)

        return Response(status_code=200)
    except Exception as e:
        print(f"Erro ao processar webhook do Supabase: {e}")
        return Response(status_code=500)


# ==========================================================
# 🩺 HEALTH CHECK
# ==========================================================
async def health_check(request: Request) -> Response:
    return Response("Servidor e Bot estão online!", status_code=200)


# ==========================================================
# 🛠️ ROTAS E APP
# ==========================================================
routes = [
    Route("/webhook", endpoint=telegram_webhook, methods=["POST"]),
    Route("/webhook/supabase", endpoint=supabase_webhook, methods=["POST"]),
    Route("/health", endpoint=health_check, methods=["GET"]),
]

app = Starlette(routes=routes, on_startup=[startup], on_shutdown=[shutdown])


# ==========================================================
# ▶️ MAIN (LOCAL)
# ==========================================================
if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    print(f"[WEB] Servidor iniciando em http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)
