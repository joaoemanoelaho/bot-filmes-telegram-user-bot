import sys
import os
import logging
import asyncio
import json # <-- IMPORT NOVO PARA O WEBHOOK
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, JSONResponse # <-- JSONResponse ADICIONADO
from telegram import Update
from telegram.ext import Application
from ptbcontrib.aiohttp_request import AiohttpRequest
import aiohttp
from telegram.request import HTTPXRequest
from telegram.error import NetworkError

import handlers_user as handlers
import database as db # <-- IMPORT DO BANCO DE DADOS PARA O VIP
# 1. Importa a nova variável PROXY_URL do config
from config import BOT_TOKEN, PROXY_URL, WEBHOOK_DOMAIN, TELEGRAM_WEBHOOK_PATH

# --- DEBUG PRINT ---
print("[DEBUG] Versão do código: 1.7 (Com Webhook PushinPay Final)")
# ---------------------

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

application: Application = None
APP_INITIALIZED = asyncio.Event()
session: aiohttp.ClientSession | None = None  # sessão global aiohttp


# ==========================================================
# 🔁 ERROR HANDLER COM RETRY AUTOMÁTICO (CORRIGIDO)
# ==========================================================
async def error_handler(update: object, context):
    """Loga os erros e tenta re-processar updates em caso de erro de rede."""
    
    e = context.error
    print(f"[DEBUG] ERROR_HANDLER ATIVADO! Erro: {e}")

    # Verifica se é um erro de rede (NetworkError ou erros do aiohttp)
    # Adicionamos aiohttp.ClientError para pegar erros como ClientOSError
    if isinstance(e, (NetworkError, aiohttp.ClientError)):
        print(f"⚠️ NetworkError detectado ({type(e).__name__}) — tentando novamente em 3s...")
        await asyncio.sleep(3)
        
        # Evita retry em updates nulos ou que não sejam Updates
        if update and isinstance(update, Update):
            try:
                # A forma correta de retry é re-processar o update inteiro,
                # não chamar .answer()
                print(f"🔄 Tentando re-processar update: {update.update_id}")
                await context.application.process_update(update)
            except Exception as retry_err:
                # Se o retry falhar (ex: a conexão ainda está ruim), apenas logamos
                print(f"❌ Falha no retry automático após erro de rede: {retry_err}")
        else:
            print("⚠️ Erro de rede sem 'update' associado ou 'update' não é um objeto Update. Retry não é possível.")
        return  # Sai do handler após tratar o erro de rede

    # Se for qualquer outro erro (como 'NoneType')
    print(f"❌ Erro não-rede no handler: {e}")
    import traceback
    traceback.print_exc()


# ==========================================================
# 🚀 STARTUP DO BOT
# ==========================================================
async def startup():
    """Inicializa o bot ao iniciar o servidor"""
    global application, session

    print("[DEBUG] Função startup() iniciada.")

    # --- INÍCIO DA LÓGICA DO PROXY (Simplificado) ---
    # 2. Usa a variável PROXY_FULL_URL importada
    final_proxy_url: str | None = PROXY_URL

    if final_proxy_url:
        print(f"✅ [PROXY] Usando proxy com a URL completa.")
    else:
        print("⚠️ [PROXY] Nenhuma PROXY_URL encontrada em config.py. Rodando sem proxy.")
    # --- FIM DA LÓGICA DO PROXY ---

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

        request_motor = AiohttpRequest(
            client_timeout=timeout,
            connection_pool_size=256,
            proxy=final_proxy_url  # <-- ADICIONADO PROXY AQUI
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
                proxy_url=final_proxy_url  # <-- ADICIONADO PROXY AQUI
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
        
        # --- CONFIGURAÇÃO DO WEBHOOK DO TELEGRAM ---
        # Garante que o webhook do Telegram esteja setado corretamente na inicialização
        webhook_url = f"{WEBHOOK_DOMAIN}{TELEGRAM_WEBHOOK_PATH}"
        print(f"ℹ️ Configurando webhook do Telegram para: {webhook_url}")
        await application.bot.set_webhook(url=webhook_url, allowed_updates=Update.ALL_TYPES)
        
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
            # Tenta deletar o webhook ao desligar (opcional, mas boa prática)
            # await application.bot.delete_webhook()
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
    # print("[DEBUG] /webhook recebido. Aguardando APP_INITIALIZED...")
    await APP_INITIALIZED.wait()
    # print("[DEBUG] APP_INITIALIZED set. Processando update...")

    try:
        data = await request.json()
        # print(f"📨 Update recebido: {data}")

        if not isinstance(data, dict) or "update_id" not in data:
            print("⚠️ Dados inválidos recebidos no webhook do Telegram.")
            return Response("ok", status_code=200)

        update = Update.de_json(data, application.bot)
        asyncio.create_task(application.process_update(update))
        # print(f"✅ Update processado: {data.get('update_id')}")

    except Exception as e:
        print(f"❌ Erro ao processar webhook do Telegram: {e}")
        import traceback
        traceback.print_exc()

    return Response("ok", status_code=200)


# ==========================================================
# 🔔 SUPABASE WEBHOOK
# ==========================================================
async def supabase_webhook(request: Request) -> Response:
    # print("[DEBUG] /webhook/supabase recebido. Aguardando APP_INITIALIZED...")
    await APP_INITIALIZED.wait()
    # print("[DEBUG] APP_INITIALIZED set. Processando Supabase...")

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
                # Usa safe_call ou try/except para evitar crash se o usuário bloqueou o bot
                try:
                    await application.bot.send_message(chat_id=user_id, text=message)
                except Exception as e:
                    print(f"Erro ao notificar usuário {user_id} sobre pedido: {e}")

        return Response(status_code=200)
    except Exception as e:
        print(f"Erro ao processar webhook do Supabase: {e}")
        return Response(status_code=500)

# =================================================================
# === 💸 NOVO: WEBHOOK HANDLER DO PUSHINPAY ===
# =================================================================
async def pushinpay_webhook(request: Request) -> Response:
    """
    Este endpoint recebe a notificação de pagamento da PushinPay.
    URL esperada: /webhook/pushinpay/{user_id}
    """
    # Aguarda o bot estar pronto para podermos usar 'application.bot'
    await APP_INITIALIZED.wait()
    
    try:
        # 1. Pega o ID do usuário da URL (vem como string, convertemos para int)
        user_id_str = request.path_params.get('user_id')
        if not user_id_str:
             print("[Webhook PushinPay] ERRO: user_id não encontrado na URL.")
             return JSONResponse({"status": "error", "message": "Missing user_id"}, status_code=400)
        user_id = int(user_id_str)
        
        # 2. Pega os dados do pagamento do corpo da requisição
        data = await request.json()
        payment_status = data.get("status")
        
        print(f"[Webhook PushinPay] Recebido para UserID: {user_id}. Status: {payment_status}")
        
        # 3. Se o status for 'paid', libera o VIP
        if payment_status == "paid":
            # Verifica se já é VIP para não enviar mensagem duplicada ou estender sem querer
            # (Opcional: você pode querer permitir estender. Se sim, remova este 'if')
            if not await db.is_user_vip(user_id):
                # Ativa VIP por 30 dias
                await db.set_user_as_vip(user_id, duration_days=30)
                # Limpa o ID de pagamento pendente para permitir gerar outro no futuro
                await db.clear_user_active_payment_id(user_id)
                
                print(f"✅ [Webhook PushinPay] VIP ATIVADO para UserID: {user_id}")
                
                # Notifica o usuário pelo Telegram
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text="🎉 **Pagamento confirmado!** 🎉\n\n"
                             "Seu **Acesso Pipoca Premium** foi ativado com sucesso!\n"
                             "Aproveite todo o nosso catálogo sem limites. 🍿",
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    print(f"⚠️ [Webhook PushinPay] Erro ao notificar UserID {user_id} (pode ter bloqueado o bot): {e}")
            else:
                 print(f"ℹ️ [Webhook PushinPay] UserID {user_id} já era VIP. Ignorando ativação duplicada.")
                 # Mesmo já sendo VIP, limpa o pagamento pendente para não travar
                 await db.clear_user_active_payment_id(user_id)
        
        # Responde 200 OK para a PushinPay saber que recebemos corretamente
        return JSONResponse({"status": "received"})
    
    except ValueError:
        print(f"[Webhook PushinPay] ERRO: user_id inválido na URL. Valor recebido: {request.path_params.get('user_id')}")
        return JSONResponse({"status": "error", "message": "Invalid user_id format"}, status_code=400)
    except json.JSONDecodeError:
        print("[Webhook PushinPay] ERRO: Corpo da requisição não é um JSON válido.")
        return JSONResponse({"status": "error", "message": "Invalid JSON body"}, status_code=400)
    except Exception as e:
        print(f"❌ ERRO GRAVE NO WEBHOOK PUSHINPAY: {e}")
        import traceback
        traceback.print_exc()
        # Retorna erro 500 para a PushinPay tentar enviar novamente depois (se eles tiverem mecanismo de retry)
        return JSONResponse({"status": "error", "message": "Internal server error"}, status_code=500)


# ==========================================================
# 🩺 HEALTH CHECK
# ==========================================================
async def health_check(request: Request) -> Response:
    return Response("Servidor e Bot estão online!", status_code=200)


# ==========================================================
# 🛠️ ROTAS E APP
# ==========================================================
# Define as rotas. IMPORTANTE: certifique-se que TELEGRAM_WEBHOOK_PATH
# em config.py corresponde a "/webhook" (ou o que você usar aqui).
routes = [
    Route("/webhook", endpoint=telegram_webhook, methods=["POST"]),
    Route("/webhook/supabase", endpoint=supabase_webhook, methods=["POST"]),
    # --- ROTA NOVA PUSHINPAY ---
    # O trecho {user_id:int} diz ao Starlette que essa parte da URL é uma variável numérica
    Route("/webhook/pushinpay/{user_id:int}", endpoint=pushinpay_webhook, methods=["POST"]),
    # ---------------------------
    Route("/health", endpoint=health_check, methods=["GET"]),
]

app = Starlette(routes=routes, on_startup=[startup], on_shutdown=[shutdown])


# ==========================================================
# ▶️ MAIN (LOCAL & CLOUD)
# ==========================================================
if __name__ == "__main__":
    import uvicorn

    # Pega a porta da variável de ambiente PORT (padrão em nuvens como Render/Heroku/SquareCloud)
    # Se não tiver, usa 8000 como padrão para testes locais.
    port = int(os.environ.get("PORT", 8000))
    print(f"[WEB] Servidor iniciando na porta {port}...")
    
    # Inicia o servidor Uvicorn
    # host="0.0.0.0" é essencial para que o servidor seja acessível externamente na nuvem
    uvicorn.run(app, host="0.0.0.0", port=port)
    