import aiohttp
import logging

# Importando do seu config.py
# ⚠️ ATENÇÃO: Você precisará adicionar EVOPAY_TOKEN no seu config.py e .env
from config import (
    EVOPAY_TOKEN,
    WEBHOOK_DOMAIN,
    WEBHOOK_SECRET
)

logger = logging.getLogger(__name__)

class EvoPayAPI:
    def __init__(self):
        self.base_url = "https://pix.evopay.cash/v1"
        self.headers = {
            "API-Key": EVOPAY_TOKEN,
            "Content-Type": "application/json"
        }

    def _traduzir_status(self, status_raw):
        """Traduz o status da EvoPay para o padrão interno do bot (SyncPay legado)"""
        if not status_raw:
            return None
        
        status_upper = status_raw.upper()
        if status_upper == "COMPLETED":
            return "paid"
        elif status_upper == "PENDING":
            return "created"
        elif status_upper in ["EXPIRED", "CANCELED"]:
            return "expired"
        
        return status_upper.lower()

    async def create_pix_payment(self, user_id, amount):
        """
        Cria cobrança PIX na EvoPay.
        """
        if not EVOPAY_TOKEN:
            logger.error("❌ EVOPAY_TOKEN não configurado!")
            return None

        url = f"{self.base_url}/pix"
        
        # URL Dinâmica para identificar o usuário no Webhook
        user_webhook_url = f"{WEBHOOK_DOMAIN}/webhook/evopay/{user_id}?secret={WEBHOOK_SECRET}"

        payload = {
            "amount": float(amount),
            "callbackUrl": user_webhook_url
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload, headers=self.headers) as response:
                    resp_json = await response.json()
                    
                    if response.status == 200:
                        # A EvoPay já entrega o Base64 pronto, não precisamos gerar localmente!
                        return {
                            "payment_id": resp_json.get("id"),
                            "qr_code_text": resp_json.get("qrCodeText"),
                            "qr_code_base64": resp_json.get("qrCodeBase64")
                        }
                    else:
                        logger.error(f"❌ Erro Criação PIX EvoPay: {resp_json}")
                        return None
            except Exception as e:
                logger.error(f"❌ Erro Conexão EvoPay: {e}")
                return None

    async def check_payment_status(self, payment_id):
        """
        Verifica status manualmente (Backup do Webhook).
        """
        url = f"{self.base_url}/pix?id={payment_id}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        status_raw = data.get("status")
                        return self._traduzir_status(status_raw)
                    elif response.status == 404:
                        return "not_found"
                    return None
            except Exception as e:
                logger.error(f"❌ Erro Check Status EvoPay: {e}")
                return None
            
    async def get_pix_details(self, payment_id):
        """
        Recupera todos os dados do PIX (inclusive o Copia e Cola) se ele estiver pendente.
        """
        url = f"{self.base_url}/pix?id={payment_id}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=self.headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        status_raw = data.get("status")
                        
                        return {
                            "status": self._traduzir_status(status_raw),
                            "qr_code_text": data.get("qrCodeText"),
                            "qr_code_base64": data.get("qrCodeBase64")
                        }
                    return None
            except Exception as e:
                logger.error(f"❌ Erro Pix Details EvoPay: {e}")
                return None

# --- INSTÂNCIA E WRAPPERS ---
# Isso garante que as funções chamadas no vip.py continuem funcionando igual
api = EvoPayAPI()

async def create_pix_payment(user_id: int, amount: float) -> dict | None:
    return await api.create_pix_payment(user_id, amount)

async def check_payment_status(payment_id: str) -> str | None:
    return await api.check_payment_status(payment_id)

async def get_pix_details(payment_id: str) -> dict | None:
    return await api.get_pix_details(payment_id)