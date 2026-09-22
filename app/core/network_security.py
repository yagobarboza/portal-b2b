"""Validação de destinos HTTP externos usados por integrações.

Esta camada é uma defesa de aplicação. Em produção ela deve ser combinada
com política de egress da infraestrutura para impedir acesso a redes internas.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


class UnsafeOutboundUrlError(ValueError):
    pass


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    # `is_global` também exclui faixas especiais que não são classificadas
    # apenas como private/loopback (por exemplo CGNAT 100.64.0.0/10).
    return ip.is_global


async def validate_public_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https":
        raise UnsafeOutboundUrlError("A integração deve usar HTTPS.")
    if parsed.username or parsed.password:
        raise UnsafeOutboundUrlError("Credenciais não são permitidas na URL.")
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if not hostname:
        raise UnsafeOutboundUrlError("Host da integração inválido.")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise UnsafeOutboundUrlError("Destino de rede não permitido.")

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public_ip(str(literal)):
            raise UnsafeOutboundUrlError("Destino de rede não permitido.")
        return

    loop = asyncio.get_running_loop()
    try:
        addresses = await loop.getaddrinfo(
            hostname,
            parsed.port or 443,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeOutboundUrlError("Não foi possível resolver o host da integração.") from exc
    if not addresses:
        raise UnsafeOutboundUrlError("Não foi possível resolver o host da integração.")

    resolved = {item[4][0] for item in addresses}
    if any(not _is_public_ip(address) for address in resolved):
        raise UnsafeOutboundUrlError("Destino de rede não permitido.")
