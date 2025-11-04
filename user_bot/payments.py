import httpx
from config import PUSHINPAY_API_KEY

# URLs CORRIGIDAS com base na documentação
BASE_API_URL = "https://api.pushinpay.com.br"
CREATE_PIX_URL = f"{BASE_API_URL}/api/pix/cashIn"
CONSULT_PIX_URL = f"{BASE_API_URL}/api/transactions" # O ID será adicionado depois

HEADERS = {
    "Authorization": f"Bearer {PUSHINPAY_API_KEY}",
    "Accept": "application/json",
    "Content-Type": "application/json"
}

# 1. Função agora é 'async def'
async def create_pix_payment(user_id: int, amount: float) -> dict | None:
    """
    Cria uma cobrança PIX no PushinPay e retorna os dados.
    'amount' deve ser em Reais (ex: 10.00).
    """
    # CONVERSÃO PARA CENTAVOS
    value_in_cents = int(amount * 100)
    
    payload = {
        "value": value_in_cents,
        # A API não parece ter um campo 'description' ou 'external_reference' no corpo da requisição,
        # vamos confiar no webhook ou na consulta para o gerenciamento.
        # Se houver esses campos na documentação, podemos adicioná-los aqui.
    }
    
    try:
        # 2. Cliente agora é 'AsyncClient'
        async with httpx.AsyncClient() as client:
            # 3. A chamada de rede agora usa 'await'
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

# 1. Função agora é 'async def'
async def check_payment_status(payment_id: str) -> str | None:
    """
    Verifica o status de um pagamento no PushinPay.
    Retorna a string do status ('created', 'paid', 'expired') ou None se der erro.
    """
    url = f"{CONSULT_PIX_URL}/{payment_id}"
    try:
        # 2. Cliente agora é 'AsyncClient'
        async with httpx.AsyncClient() as client:
            # 3. A chamada de rede agora usa 'await'
            response = await client.get(url, headers=HEADERS)
            response.raise_for_status()
            data = response.json()
            return data.get("status")
    except Exception as e:
        print(f"Erro ao consultar PIX: {e}")
        return None
    