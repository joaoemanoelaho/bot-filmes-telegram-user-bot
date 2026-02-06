import aiohttp
import asyncio
import logging
import qrcode
import io
import base64
from datetime import datetime, timedelta

# Importando do seu config.py
from config import (
    SYNCPAY_CLIENT_ID, 
    SYNCPAY_CLIENT_SECRET,
    WEBHOOK_DOMAIN,     # Ex: https://seu-site.com
    DEFAULT_PAYER       # O dicionário com o CPF padrão
)

# Configuração
BASE_API_URL = "https://api.syncpay.com.br" # Confirme se é .com.br ou .net no suporte
logger = logging.getLogger(__name__)

class SyncPayAPI:
    def __init__(self):
        self.access_token = None
        self.token_expires_at = datetime.now()

    async def _get_auth_token(self):
        """
        Realiza o login na Syncpay e gerencia a renovação do Token (validade de 1h).
        """
        # Se o token existe e ainda não venceu (com margem de 5 min)
        if self.access_token and datetime.now() < (self.token_expires_at - timedelta(minutes=5)):
            return self.access_token

        url = f"{BASE_API_URL}/api/partner/v1/auth-token"
        
        payload = {
            "client_id": SYNCPAY_CLIENT_ID,
            "client_secret": SYNCPAY_CLIENT_SECRET
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.access_token = data.get("access_token")
                        # Define validade (padrão 1h)
                        expires_in = data.get("expires_in", 3600)
                        self.token_expires_at = datetime.now() + timedelta(seconds=expires_in)
                        return self.access_token
                    else:
                        logger.error(f"❌ Erro Login Syncpay: {await response.text()}")
                        return None
            except Exception as e:
                logger.error(f"❌ Erro Conexão Auth: {e}")
                return None

    def _generate_qr_base64(self, text):
        """
        Gera a imagem do QR Code localmente (Syncpay só manda o texto).
        """
        if not text: return None
        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(text)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            
            buffered = io.BytesIO()
            img.save(buffered, format="PNG")
            return base64.b64encode(buffered.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error(f"Erro ao gerar imagem QR: {e}")
            return None

    async def create_pix_payment(self, user_id, amount):
        """
        Cria cobrança PIX na Syncpay usando o 'Cliente Padrão' para não pedir dados no chat.
        """
        token = await self._get_auth_token()
        if not token: return None

        url = f"{BASE_API_URL}/api/partner/v1/cash-in"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        # URL Dinâmica para identificar o usuário no Webhook
        user_webhook_url = f"{WEBHOOK_DOMAIN}/webhook/syncpay/{user_id}"

        payload = {
            "amount": float(amount),
            "description": f"VIP-{user_id}",
            "webhook_url": user_webhook_url, # Syncpay avisará aqui
            "client": DEFAULT_PAYER # Dados padrão para agilizar a venda
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=headers) as response:
                    resp_json = await response.json()
                    
                    if response.status == 200:
                        pix_code = resp_json.get("pix_code")
                        payment_id = resp_json.get("identifier")

                        # Gera a imagem Base64 aqui mesmo
                        qr_base64 = self._generate_qr_base64(pix_code)

                        return {
                            "payment_id": payment_id,
                            "qr_code_text": pix_code,
                            "qr_code_base64": qr_base64
                        }
                    else:
                        logger.error(f"❌ Erro Cash-in Syncpay: {resp_json}")
                        return None
            except Exception as e:
                logger.error(f"❌ Erro Conexão Cash-in: {e}")
                return None

    async def check_payment_status(self, payment_id):
        """
        Verifica status manualmente (Backup do Webhook).
        """
        token = await self._get_auth_token()
        if not token: return None

        url = f"{BASE_API_URL}/api/partner/v1/transaction/{payment_id}"
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        # A Syncpay retorna dentro de data -> status
                        # Status possíveis: pending, completed, failed, refunded
                        status_raw = data.get("data", {}).get("status")
                        
                        # Tradução para o padrão do seu bot antigo
                        if status_raw == "completed": return "paid"
                        if status_raw == "pending": return "created" # ou waiting
                        if status_raw == "failed": return "expired"
                        return status_raw
                    elif response.status == 404:
                        return "not_found"
                    return None
            except Exception:
                return None

# --- INSTÂNCIA E WRAPPERS ---
# Isso garante que as funções chamadas no vip.py continuem funcionando igual
api = SyncPayAPI()

async def create_pix_payment(user_id: int, amount: float) -> dict | None:
    return await api.create_pix_payment(user_id, amount)

async def check_payment_status(payment_id: str) -> str | None:
    return await api.check_payment_status(payment_id)