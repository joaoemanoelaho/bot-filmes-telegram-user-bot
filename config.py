import os
from dotenv import load_dotenv

# Carrega variáveis do .env
load_dotenv()

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

# TMDb
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Pyrogram
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
STORAGE_CHANNEL_ID = int(os.getenv("STORAGE_CHANNEL_ID"))
PUSHINPAY_API_KEY = os.getenv("PUSHINPAY_API_KEY")
TASTEDIVE_API_KEY = os.getenv("TASTEDIVE_API_KEY")

M3U_FILE_PATH = os.getenv("M3U_FILE_PATH")
DOWNLOAD_FOLDER = os.getenv("DOWNLOAD_FOLDER")
LOG_FILE = os.getenv("LOG_FILE")
REFERER_URL = os.getenv("REFERER_URL")

USER_AGENT = os.getenv("USER_AGENT") 

PROXY_URL = os.getenv("PROXY_URL")
SESSION_STRING = os.environ.get('PYROGRAM_SESSION_STRING')

WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN")

# Validações
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN não configurado!")
if not TMDB_API_KEY:
    raise ValueError("❌ TMDB_API_KEY não configurado!")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("❌ Credenciais Supabase não configuradas!")
if not API_ID or not API_HASH:
    raise ValueError("❌ Credenciais Pyrogram não configuradas!")
if not STORAGE_CHANNEL_ID:
    raise ValueError("❌ STORAGE_CHANNEL_ID não configurado!")
if not ADMIN_IDS:
    raise ValueError("❌ ADMIN_IDS não configurado!")
if not PUSHINPAY_API_KEY:
    raise ValueError("❌ PUSHINPAY_API_KEY não configurado!")
if not TASTEDIVE_API_KEY:
    raise ValueError("❌ TASTEDIVE_API_KEY não configurado!")
print("✅ Configurações carregadas com sucesso!")