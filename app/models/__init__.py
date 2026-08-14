"""Registro central de modelos.

Importar TODOS os modelos aqui é obrigatório:
é o que permite ao Alembic detectar as tabelas automaticamente.
"""

from app.models.audit import AuditLog
from app.models.cart import Cart, CartItem
from app.models.catalog import (
    Catalog,
    Category,
    CustomerPrice,
    PriceList,
    Product,
    catalog_products,
)
from app.models.chat import ChatMessage, ChatParticipant, ChatRoom
from app.models.company import Company
from app.models.customer import Customer
from app.models.file import File
from app.models.financial import FinancialAccount, FinancialPayment
from app.models.integration import ERPIntegration, SyncExecution, WebhookEvent
from app.models.notification import Notification
from app.models.order import Order, OrderItem, OrderStatusHistory
from app.models.rbac import Permission, Role, role_permissions, user_roles
from app.models.ticket import Ticket, TicketMessage
from app.models.user import User
from app.models.catalog import Category, CustomerPrice, PriceList, Product

__all__ = [
    "AuditLog",
    "Cart",
    "CartItem",
    "Catalog",
    "Category",
    "ChatMessage",
    "ChatParticipant",
    "ChatRoom",
    "Company",
    "Customer",
    "CustomerPrice",
    "ERPIntegration",
    "File",
    "FinancialAccount",
    "FinancialPayment",
    "Notification",
    "Order",
    "OrderItem",
    "OrderStatusHistory",
    "Permission",
    "PriceList",
    "Product",
    "Role",
    "SyncExecution",
    "Ticket",
    "TicketMessage",
    "User",
    "WebhookEvent",
    "catalog_products",
    "role_permissions",
    "user_roles",
]