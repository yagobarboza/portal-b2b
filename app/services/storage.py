"""Serviço de armazenamento no Cloudflare R2 (seções 18 e 20)."""
import uuid

import boto3
from botocore.client import Config

from app.core.config import get_settings

settings = get_settings()

class StorageService:
    def __init__(self) -> None:
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint_url,
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name="auto",
            config=Config(signature_version="s3v4"),
        )
        self.bucket = settings.r2_bucket_name

    def _build_key(self, tenant_id: uuid.UUID, owner_type: str, ext: str) -> str:
        # Nunca usa o nome original como chave (seção 18)
        return f"tenant/{tenant_id}/{owner_type}/{uuid.uuid4().hex}{ext}"

    def upload_bytes(
        self,
        *,
        tenant_id: uuid.UUID,
        owner_type: str,
        content: bytes,
        ext: str,
        content_type: str,
    ) -> str:
        key = self._build_key(tenant_id, owner_type, ext)
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
        )
        return key

    def generate_signed_url(self, storage_key: str, expires_in: int | None = None) -> str:
        expiry = expires_in or settings.r2_signed_url_expiry
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": storage_key},
            ExpiresIn=expiry,
        )

    def delete_object(self, storage_key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=storage_key)