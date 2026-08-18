"""Catálogo central de permissões do sistema (seção 13 do doc).

Formato: {modulo}:{acao}
- modulo: companies, users, customers, products, catalogs, cart,
  orders, tickets, chat, financial, files, notifications, admin
- acao: read, create, update, delete, manage
"""

# ===== Companies =====
COMPANY_READ = "companies:read"
COMPANY_MANAGE = "companies:manage"

# ===== Users =====
USER_READ = "users:read"
USER_CREATE = "users:create"
USER_UPDATE = "users:update"
USER_DELETE = "users:delete"

# ===== Customers =====
CUSTOMER_READ = "customers:read"
CUSTOMER_CREATE = "customers:create"
CUSTOMER_UPDATE = "customers:update"

# ===== Products =====
PRODUCT_READ = "products:read"
PRODUCT_CREATE = "products:create"
PRODUCT_UPDATE = "products:update"
PRODUCT_DELETE = "products:delete"

# ===== Catalogs =====
CATALOG_READ = "catalogs:read"
CATALOG_MANAGE = "catalogs:manage"

# ===== Cart =====
CART_MANAGE = "cart:manage"

# ===== Orders =====
ORDER_READ = "orders:read"
ORDER_CREATE = "orders:create"
ORDER_UPDATE = "orders:update"
ORDER_MANAGE = "orders:manage"

# ===== Tickets =====
TICKET_READ = "tickets:read"
TICKET_CREATE = "tickets:create"
TICKET_UPDATE = "tickets:update"

# ===== Chat =====
CHAT_READ = "chat:read"
CHAT_SEND = "chat:send"

# ===== Financial =====
FINANCIAL_READ = "financial:read"

# ===== Files =====
FILE_UPLOAD = "files:upload"
FILE_READ = "files:read"

# ===== Notifications =====
NOTIFICATION_READ = "notifications:read"

# ===== Admin =====
ADMIN_MANAGE = "admin:manage"

# ===== Super admin (acesso total) =====
SUPER_ADMIN = "super_admin:all"

# Catálogo completo (usado no seed)
PERMISSION_CATALOG: list[dict] = [
    {"code": COMPANY_READ, "name": "Ver empresas", "module": "companies", "description": "Visualizar dados da empresa"},
    {"code": COMPANY_MANAGE, "name": "Gerenciar empresas", "module": "companies", "description": "Criar/editar dados da empresa"},
    {"code": USER_READ, "name": "Ver usuários", "module": "users", "description": "Listar usuários do tenant"},
    {"code": USER_CREATE, "name": "Criar usuários", "module": "users", "description": "Criar usuários do tenant"},
    {"code": USER_UPDATE, "name": "Editar usuários", "module": "users", "description": "Editar usuários do tenant"},
    {"code": USER_DELETE, "name": "Excluir usuários", "module": "users", "description": "Excluir usuários do tenant"},
    {"code": CUSTOMER_READ, "name": "Ver clientes", "module": "customers", "description": "Listar clientes do tenant"},
    {"code": CUSTOMER_CREATE, "name": "Criar clientes", "module": "customers", "description": "Criar clientes do tenant"},
    {"code": CUSTOMER_UPDATE, "name": "Editar clientes", "module": "customers", "description": "Editar clientes do tenant"},
    {"code": PRODUCT_READ, "name": "Ver produtos", "module": "products", "description": "Listar produtos do tenant"},
    {"code": PRODUCT_CREATE, "name": "Criar produtos", "module": "products", "description": "Criar produtos do tenant"},
    {"code": PRODUCT_UPDATE, "name": "Editar produtos", "module": "products", "description": "Editar produtos do tenant"},
    {"code": PRODUCT_DELETE, "name": "Excluir produtos", "module": "products", "description": "Excluir produtos do tenant"},
    {"code": CATALOG_READ, "name": "Ver catálogos", "module": "catalogs", "description": "Listar catálogos do tenant"},
    {"code": CATALOG_MANAGE, "name": "Gerenciar catálogos", "module": "catalogs", "description": "Criar/editar catálogos do tenant"},
    {"code": CART_MANAGE, "name": "Gerenciar carrinho", "module": "cart", "description": "Gerenciar o próprio carrinho"},
    {"code": ORDER_READ, "name": "Ver pedidos", "module": "orders", "description": "Listar pedidos do tenant"},
    {"code": ORDER_CREATE, "name": "Criar pedidos", "module": "orders", "description": "Criar pedidos"},
    {"code": ORDER_UPDATE, "name": "Editar pedidos", "module": "orders", "description": "Editar pedidos"},
    {"code": ORDER_MANAGE, "name": "Gerenciar pedidos", "module": "orders", "description": "Aprovar/processar pedidos"},
    {"code": TICKET_READ, "name": "Ver tickets", "module": "tickets", "description": "Listar tickets do tenant"},
    {"code": TICKET_CREATE, "name": "Criar tickets", "module": "tickets", "description": "Abrir tickets"},
    {"code": TICKET_UPDATE, "name": "Editar tickets", "module": "tickets", "description": "Atualizar tickets"},
    {"code": CHAT_READ, "name": "Ver chat", "module": "chat", "description": "Ler mensagens do chat"},
    {"code": CHAT_SEND, "name": "Enviar mensagens", "module": "chat", "description": "Enviar mensagens no chat"},
    {"code": FINANCIAL_READ, "name": "Ver financeiro", "module": "financial", "description": "Consultar dados financeiros"},
    {"code": FILE_UPLOAD, "name": "Enviar arquivos", "module": "files", "description": "Fazer upload de arquivos"},
    {"code": FILE_READ, "name": "Ver arquivos", "module": "files", "description": "Acessar arquivos"},
    {"code": NOTIFICATION_READ, "name": "Ver notificações", "module": "notifications", "description": "Ler notificações"},
    {"code": ADMIN_MANAGE, "name": "Administração", "module": "admin", "description": "Funções administrativas do tenant"},
    {"code": SUPER_ADMIN, "name": "Super Admin", "module": "admin", "description": "Acesso global à plataforma"},
]

def effective_permissions(user) -> set[str]:
    """Permissões efetivas do usuário (RBAC — seção 13).

    - Super admin: acesso total.
    - Demais: união das permissões de todas as roles do usuário.

    Reutilizada por app/api/deps.py e pelo endpoint GET /auth/me
    (evita duplicar a lógica de cálculo em dois lugares).
    """
    if user.is_super_admin:
        return {SUPER_ADMIN}
    perms: set[str] = set()
    for role in user.roles:
        for p in role.permissions:
            perms.add(p.code)
    return perms