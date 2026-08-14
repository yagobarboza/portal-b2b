import enum

from sqlalchemy import Enum

class UserStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"

class CustomerStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    BLOCKED = "blocked"

class CompanyStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class ProductStatus(str, enum.Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class CartStatus(str, enum.Enum):
    OPEN = "open"
    CHECKED_OUT = "checked_out"
    ABANDONED = "abandoned"

class OrderStatus(str, enum.Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    RECEIVED = "received"
    UNDER_REVIEW = "under_review"
    AWAITING_CUSTOMER = "awaiting_customer"
    APPROVED = "approved"
    PROCESSING = "processing"
    INVOICED = "invoiced"
    SHIPPED = "shipped"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class TicketPriority(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"

class TicketStatus(str, enum.Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    AWAITING_CUSTOMER = "awaiting_customer"
    AWAITING_COMPANY = "awaiting_company"
    RESOLVED = "resolved"
    CLOSED = "closed"

class ChatSector(str, enum.Enum):
    SALES = "sales"
    COMMERCIAL = "commercial"
    FINANCIAL = "financial"
    SUPPORT = "support"
    SERVICE = "service"

class ChatRoomStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"

class FinancialAccountStatus(str, enum.Enum):
    OPEN = "open"
    PAID = "paid"
    OVERDUE = "overdue"

class FileOwnerType(str, enum.Enum):
    PRODUCT = "product"
    CATALOG = "catalog"
    TICKET = "ticket"
    CHAT = "chat"
    DOCUMENT = "document"
    USER = "user"

class SyncStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"

class WebhookStatus(str, enum.Enum):
    RECEIVED = "received"
    PROCESSED = "processed"
    FAILED = "failed"

class NotificationType(str, enum.Enum):
    ORDER = "order"
    TICKET = "ticket"
    CHAT = "chat"
    FINANCIAL = "financial"
    SYSTEM = "system"

def pg_enum(enum_cls: type[enum.Enum], name: str):
    """Enum do PostgreSQL usando os VALORES dos membros (minúsculos).

    Sem values_callable, o SQLAlchemy usa os NOMES dos membros
    (MAIÚSCULAS) ao criar o tipo no banco — descasando com os
    server_default (ex.: 'active' vs 'ACTIVE'). Este helper garante
    que autogenerate e runtime usem sempre os valores.
    """
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda e: [member.value for member in e],
    )