"""Cliente HTTP da API do Asaas (cobranças, assinaturas e clientes).

Segurança:
- A API key NUNCA sai do backend (lida do .env via Settings).
- Nenhum segredo é logado; respostas de erro são truncadas e sanitizadas.
- Timeout em todas as requisições (anti-DoS).
- User-Agent OBRIGATÓRIO pela API do Asaas (contas criadas após 13/06/2024).
- Reconciliação por CPF/CNPJ: consulta antes de criar (evita duplicidade).
"""
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger("asaas")

HTTP_TIMEOUT = 30.0

# Mapeamento de status de cobrança do Asaas -> nosso BillingStatus.
ASAAS_PAYMENT_STATUS_MAP = {
    "PENDING": "pending",
    "RECEIVED": "paid",
    "CONFIRMED": "paid",
    "OVERDUE": "overdue",
    "REFUNDED": "refunded",
    "CANCELLED": "cancelled",
}


class AsaasError(Exception):
    """Erro de comunicação ou de negócio com a API do Asaas."""


class AsaasClient:
    """Wrapper fino e seguro sobre a API do Asaas."""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.asaas_base_url
        self.api_key = settings.ASAAS_API_KEY.strip()
        self.user_agent = settings.ASAAS_USER_AGENT
        if not self.api_key:
            raise AsaasError("ASAAS_API_KEY não configurada no .env.")

    def _headers(self) -> dict[str, str]:
        return {
            "access_token": self.api_key,
            "Content-Type": "application/json",
            # OBRIGATÓRIO pela API do Asaas (contas criadas após 13/06/2024).
            "User-Agent": self.user_agent,
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Executa uma requisição ao Asaas de forma segura.

        - Nunca loga a API key.
        - Trata timeout e falhas de conexão.
        - Valida que a resposta é JSON objeto.
        """
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
                resp = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            raise AsaasError("Tempo esgotado ao comunicar com o Asaas.") from exc
        except httpx.RequestError as exc:
            raise AsaasError("Falha de conexão com o Asaas.") from exc

        if resp.status_code >= 400:
            # Sanitiza: nunca loga a API key nem o corpo completo.
            snippet = resp.text[:300].replace("\n", " ")
            logger.error("Asaas %s %s -> %s: %s", method, path, resp.status_code, snippet)
            raise AsaasError(self._extract_error(resp))

        try:
            data = resp.json()
        except ValueError as exc:
            raise AsaasError("Resposta do Asaas não é JSON válido.") from exc

        if not isinstance(data, dict):
            raise AsaasError("Resposta do Asaas em formato inesperado.")
        return data

    @staticmethod
    def _extract_error(resp: httpx.Response) -> str:
        """Extrai a mensagem de erro do payload do Asaas de forma segura."""
        try:
            data = resp.json()
        except ValueError:
            return f"Asaas respondeu {resp.status_code}."
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict):
                desc = first.get("description") or first.get("code") or ""
                return f"Asaas: {desc}".strip()
        return f"Asaas respondeu {resp.status_code}."

    # ---------- Clientes (reconciliação por CPF/CNPJ) ----------
    async def find_customer_by_document(self, cpf_cnpj: str) -> str | None:
        """Busca cliente no Asaas por CPF/CNPJ. Retorna o id ou None.

        Percorre a paginação de forma defensiva (limite de 10 páginas).
        """
        for page in range(1, 11):
            data = await self._request(
                "GET",
                "/v3/customers",
                params={"cpfCnpj": cpf_cnpj, "limit": 100, "offset": (page - 1) * 100},
            )
            items = data.get("data") or []
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    return item["id"]
            total = data.get("totalCount") or 0
            if page * 100 >= total:
                break
        return None

    async def create_customer(
        self, name: str, cpf_cnpj: str, email: str | None = None
    ) -> str:
        """Cria cliente no Asaas. Retorna o id criado."""
        payload: dict[str, Any] = {"name": name, "cpfCnpj": cpf_cnpj}
        if email:
            payload["email"] = email
        data = await self._request("POST", "/v3/customers", json_body=payload)
        customer_id = data.get("id")
        if not customer_id:
            raise AsaasError("Asaas não retornou o id do cliente.")
        return customer_id

    async def get_or_create_customer(
        self, name: str, cpf_cnpj: str, email: str | None = None
    ) -> str:
        """Reconcilia: consulta por CNPJ; se não existir, cria."""
        existing = await self.find_customer_by_document(cpf_cnpj)
        if existing:
            return existing
        return await self.create_customer(name, cpf_cnpj, email)

    # ---------- Cobranças ----------
    async def create_payment(
        self,
        *,
        customer_id: str,
        value: float,
        due_date: str,
        billing_type: str,
        description: str,
        external_reference: str,
    ) -> dict[str, Any]:
        """Cria cobrança avulsa. Retorna o payload do Asaas (com invoiceUrl)."""
        payload = {
            "customer": customer_id,
            "billingType": billing_type,
            "value": value,
            "dueDate": due_date,
            "description": description,
            "externalReference": external_reference,
        }
        return await self._request("POST", "/v3/payments", json_body=payload)

    async def get_payment(self, payment_id: str) -> dict[str, Any]:
        """Busca uma cobrança no Asaas (para atualizar status/URL)."""
        return await self._request("GET", f"/v3/payments/{payment_id}")

    # ---------- Assinaturas (recorrência mensal) ----------
    async def create_subscription(
        self,
        *,
        customer_id: str,
        value: float,
        next_due_date: str,
        billing_type: str,
        description: str,
        external_reference: str,
    ) -> dict[str, Any]:
        """Cria assinatura mensal. Retorna o payload do Asaas."""
        payload = {
            "customer": customer_id,
            "billingType": billing_type,
            "value": value,
            "nextDueDate": next_due_date,
            "cycle": "MONTHLY",
            "description": description,
            "externalReference": external_reference,
        }
        return await self._request("POST", "/v3/subscriptions", json_body=payload)

    async def get_subscription(self, subscription_id: str) -> dict[str, Any]:
        """Busca uma assinatura no Asaas (para validar se ainda está ativa)."""
        return await self._request("GET", f"/v3/subscriptions/{subscription_id}")

    async def list_subscription_payments(
        self, subscription_id: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Lista as cobranças geradas por uma assinatura (para sincronizar)."""
        data = await self._request(
            "GET",
            "/v3/payments",
            params={"subscription": subscription_id, "limit": limit},
        )
        items = data.get("data") or []
        return [item for item in items if isinstance(item, dict)]

    # ---------- Helpers ----------
    @staticmethod
    def payment_checkout_url(payment: dict[str, Any]) -> str | None:
        """URL da página de pagamento HOSPEDADA do Asaas (Fatura).

        Campo correto: `invoiceUrl`. A Fatura é a tela onde o pagador
        conclui o pagamento na interface do Asaas — prioridade do projeto.
        """
        url = payment.get("invoiceUrl")
        return url if isinstance(url, str) and url else None

    @staticmethod
    def map_payment_status(asaas_status: str) -> str | None:
        """Mapeia o status do Asaas para o nosso BillingStatus."""
        return ASAAS_PAYMENT_STATUS_MAP.get(asaas_status)