"""Definição dos perfis RBAC (seção 13 do doc).

Perfis iniciais:
- super_admin: administração global da plataforma (tenant_id NULL)
- admin: administração do tenant
- vendedor: clientes, pedidos e atendimento comercial
- financeiro: dados financeiros autorizados
- suporte: tickets e atendimento
- cliente: somente recursos pertencentes a ele
"""
from app.core.permissions import (
    ADMIN_MANAGE, CART_MANAGE, CATALOG_MANAGE, CATALOG_READ,
    CHAT_READ, CHAT_SEND, COMPANY_READ, CUSTOMER_CREATE, CUSTOMER_READ,
    CUSTOMER_UPDATE, FILE_READ, FILE_UPLOAD, FINANCIAL_READ,
    NOTIFICATION_READ, ORDER_CREATE, ORDER_MANAGE, ORDER_READ,
    ORDER_UPDATE, PRODUCT_CREATE, PRODUCT_DELETE, PRODUCT_READ,
    PRODUCT_UPDATE, SUPER_ADMIN, TICKET_CREATE, TICKET_READ,
    TICKET_UPDATE, USER_CREATE, USER_DELETE, USER_READ, USER_UPDATE,
)

ROLE_SUPER_ADMIN = "super_admin"
ROLE_ADMIN = "admin"
ROLE_VENDEDOR = "vendedor"
ROLE_FINANCEIRO = "financeiro"
ROLE_SUPORTE = "suporte"
ROLE_CLIENTE = "cliente"

ROLE_DEFINITIONS: dict[str, dict] = {
    ROLE_SUPER_ADMIN: {
        "name": "Super Admin",
        "is_system": True,
        "global": True,  # tenant_id NULL
        "permissions": [SUPER_ADMIN],
    },
    ROLE_ADMIN: {
        "name": "Admin da Empresa",
        "is_system": True,
        "global": False,
        "permissions": [
            COMPANY_READ, USER_READ, USER_CREATE, USER_UPDATE, USER_DELETE,
            CUSTOMER_READ, CUSTOMER_CREATE, CUSTOMER_UPDATE,
            PRODUCT_READ, PRODUCT_CREATE, PRODUCT_UPDATE, PRODUCT_DELETE,
            CATALOG_READ, CATALOG_MANAGE,
            ORDER_READ, ORDER_MANAGE,
            TICKET_READ, TICKET_UPDATE,
            CHAT_READ, CHAT_SEND,
            FINANCIAL_READ,
            FILE_UPLOAD, FILE_READ,
            NOTIFICATION_READ,
            ADMIN_MANAGE,
        ],
    },
    ROLE_VENDEDOR: {
        "name": "Vendedor",
        "is_system": True,
        "global": False,
        "permissions": [
            CUSTOMER_READ, CUSTOMER_CREATE, CUSTOMER_UPDATE,
            PRODUCT_READ, CATALOG_READ,
            ORDER_READ, ORDER_CREATE, ORDER_UPDATE,
            CHAT_READ, CHAT_SEND,
            NOTIFICATION_READ,
        ],
    },
    ROLE_FINANCEIRO: {
        "name": "Financeiro",
        "is_system": True,
        "global": False,
        "permissions": [
            FINANCIAL_READ, ORDER_READ, CUSTOMER_READ, NOTIFICATION_READ,
        ],
    },
    ROLE_SUPORTE: {
        "name": "Suporte",
        "is_system": True,
        "global": False,
        "permissions": [
            TICKET_READ, TICKET_CREATE, TICKET_UPDATE,
            CHAT_READ, CHAT_SEND, CUSTOMER_READ, NOTIFICATION_READ,
        ],
    },
    ROLE_CLIENTE: {
        "name": "Cliente",
        "is_system": True,
        "global": False,
        "permissions": [
            PRODUCT_READ, CATALOG_READ, CART_MANAGE,
            ORDER_READ, ORDER_CREATE,
            TICKET_READ, TICKET_CREATE,
            CHAT_READ, CHAT_SEND,
            FINANCIAL_READ,
            NOTIFICATION_READ,
        ],
    },
}