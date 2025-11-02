import sys
import os
import logging
import asyncio # 1. Importe asyncio
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response
from telegram import Update, Bot
from telegram.ext import Application
from telegram.request import HTTPXRequest
import handlers_user as handlers
from config import BOT_TOKEN

# --- DEBUG PRINT ---
print("[DEBUG] Versão do código: 1.1 (com asyncio.Event e Logs)")
# ---------------------

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

application: Application = None
APP_INITIALIZED = asyncio.Event() # 2. Crie um Evento global

async def error_handler(update: object, context):
    """Loga os erros causados pelos handlers."""
    # --- DEBUG PRINT ---
    print(f"[DEBUG] ERROR_HANDLER ATIVADO! Erro: {context.error}")
    # ---------------------
    print(f"❌ Erro no handler: {context.error}")
    import traceback
    traceback.print_exc()

async def startup():
    """Inicializa o bot ao iniciar o servidor"""
    global application
    
    # --- DEBUG PRINT ---
    print("[DEBUG] Função startup() iniciada.")
    # ---------------------
    
    try:
        request = HTTPXRequest(read_timeout=60.0, connect_timeout=10.0)

        application = Application.builder().token(BOT_TOKEN).request(request).build()
        
        application.add_handler(handlers.start_handler)
        application.add_handler(handlers.button_click_handler)
        application.add_handler(handlers.inline_search_handler)
        application.add_handler(handlers.watch_handler)
        application.add_handler(handlers.text_handler)
        application.add_handler(handlers.cancel_command_handler)
        application.add_handler(handlers.help_command_handler)
        application.add_handler(handlers.request_command_handler)
        
        # 3. Adicione o error handler (MUITO IMPORTANTE)
        print("[DEBUG] Adicionando error_handler...")
        application.add_error_handler(error_handler)
        
        await application.initialize()
        print("✅ Bot de USUÁRIO (webhook) inicializado!")
        
        # 4. Sinalize para os webhooks que o bot está pronto
        print("[DEBUG] Sinalizando APP_INITIALIZED.set()")
        APP_INITIALIZED.set() 
        print("[DEBUG] Startup concluído.")
        
    except Exception as e:
        print(f"❌ ERRO NO STARTUP: {e}")
        import traceback
        traceback.print_exc()
        raise

async def telegram_webhook(request: Request) -> Response:
    """Recebe updates do Telegram via webhook"""
    
    # --- DEBUG PRINT ---
    print("[DEBUG] /webhook recebido. Aguardando APP_INITIALIZED...")
    # ---------------------
    
    # 5. Espere o startup terminar ANTES de fazer qualquer coisa
    await APP_INITIALIZED.wait() 
    
    # --- DEBUG PRINT ---
    print("[DEBUG] APP_INITIALIZED está 'set'. Processando webhook do Telegram.")
    # ---------------------
    
    try:
        data = await request.json()
        print(f"📨 Dados recebidos no webhook: {data}")
        
        # Valida se é um update válido do Telegram
        if not isinstance(data, dict):
            print(f"⚠️ Dados não são dicionário: {type(data)}")
            return Response("ok", status_code=200)
        
        if 'update_id' not in data:
            print(f"⚠️ Sem update_id. Chaves: {data.keys()}")
            return Response("ok", status_code=200)
        
        # Agora é seguro usar application.bot porque esperamos o Event
        update = Update.de_json(data, application.bot) 
        await application.process_update(update)
        print(f"✅ Update processado: {data.get('update_id')}")
        
    except Exception as e:
        print(f"❌ Erro ao processar webhook do Telegram: {e}")
        import traceback
        traceback.print_exc()
    
    return Response("ok", status_code=200)

async def supabase_webhook(request: Request) -> Response:
    """Recebe notificações do Supabase"""
    
    # --- DEBUG PRINT ---
    print("[DEBUG] /webhook/supabase recebido. Aguardando APP_INITIALIZED...")
    # ---------------------
    
    # 6. Espere o startup terminar aqui também
    await APP_INITIALIZED.wait()
    
    # --- DEBUG PRINT ---
    print("[DEBUG] APP_INITIALIZED está 'set'. Processando webhook do Supabase.")
    # ---------------------
    
    try:
        data = await request.json()
        print(f"--- WEBHOOK DO SUPABASE RECEBIDO ---\n{data}\n---------------------------------")

        user_id = data.get('user_id')
        title = data.get('title')
        new_status = data.get('new_status')
        
        if user_id and new_status:
            message = ""
            if new_status == 'added':
                message = f"🎉 Boas notícias! O título que você pediu, '{title}', já está disponível no nosso catálogo!"
            elif new_status == 'denied':
                message = f"😔 Olha! Sobre o seu pedido '{title}', infelizmente não conseguimos adicioná-lo ao catálogo no momento."
            
            if message:
                await application.bot.send_message(chat_id=user_id, text=message)
        
        return Response(status_code=200)
    except Exception as e:
        print(f"Erro ao processar webhook do Supabase: {e}")
        return Response(status_code=500)

async def health_check(request: Request) -> Response:
    """Verificação de saúde do servidor"""
    return Response("Servidor e Bot estão online!", status_code=200)

# Define as rotas
routes = [
    Route("/webhook", endpoint=telegram_webhook, methods=["POST"]),
    Route("/webhook/supabase", endpoint=supabase_webhook, methods=["POST"]),
    Route("/health", endpoint=health_check, methods=["GET"]),
]

app = Starlette(routes=routes, on_startup=[startup])

if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("PORT", 8000))
    print(f"[WEB] Servidor iniciando em http://0.0.0.0:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port)