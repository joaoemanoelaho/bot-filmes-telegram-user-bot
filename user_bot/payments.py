import httpx
# --- IMPORT NOVO: WEBHOOK_DOMAIN para montar a URL ---
from config import PUSHINPAY_API_KEY, WEBHOOK_DOMAIN

# URLs CORRIGIDAS com base na documentação
BASE_API_URL = "https://api.pushinpay.com.br"
CREATE_PIX_URL = f"{BASE_API_URL}/api/pix/cashIn"
CONSULT_PIX_URL = f"{BASE_API_URL}/api/transactions" 

HEADERS = {
    "Authorization": f"Bearer {PUSHINPAY_API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

async def create_pix_payment(user_id: int, amount: float) -> dict | None:
    """
    Cria uma cobrança PIX no PushinPay e retorna os dados,
    já configurando o Webhook para notificação automática.
    'amount' deve ser em Reais (ex: 10.00).
    """
    # CONVERSÃO PARA CENTAVOS
    value_in_cents = int(amount * 100)
    
    # Monta a URL de webhook específica para este usuário.
    # Quando a PushinPay notificar essa URL, saberemos de quem é o pagamento.
    user_webhook_url = f"{WEBHOOK_DOMAIN}/webhook/pushinpay/{user_id}"
    
    payload = {
        "value": value_in_cents,
        "webhook_url": user_webhook_url, # <-- O PULO DO GATO ESTÁ AQUI
        "description": f"Acesso Premium - {user_id}"
    }
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(CREATE_PIX_URL, headers=HEADERS, json=payload)
            response.raise_for_status()
            data = response.json()
            return {
                "payment_id": data.get("id"),
                "qr_code_base64": data.get("qr_code_base64"),
                "qr_code_text": data.get("qr_code")
            }
    except httpx.HTTPStatusError as e:
        print(f"\n--- ERRO HTTP NA API PUSHINPAY ---\nStatus Code: {e.response.status_code}\nResposta: {e.response.text}\n----------------------------------\n")
        return None
    except Exception as e:
        print(f"Erro inesperado ao criar PIX: {e}")
        return None

# OBS: A função 'check_payment_status' foi removida pois não é mais necessária com Webhooks.