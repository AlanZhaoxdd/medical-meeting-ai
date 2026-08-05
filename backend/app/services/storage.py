from __future__ import annotations

from io import BytesIO

import anyio
from minio import Minio

from app.core.config import get_settings


class ObjectStorage:
    def __init__(self) -> None:
        settings = get_settings()
        self.bucket = settings.minio_bucket
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    async def ensure_bucket(self) -> None:
        exists = await anyio.to_thread.run_sync(self.client.bucket_exists, self.bucket)
        if not exists:
            await anyio.to_thread.run_sync(self.client.make_bucket, self.bucket)

    async def put(self, object_key: str, content: bytes, content_type: str) -> None:
        await self.ensure_bucket()

        def upload() -> None:
            self.client.put_object(
                self.bucket,
                object_key,
                BytesIO(content),
                len(content),
                content_type=content_type,
            )

        await anyio.to_thread.run_sync(upload)

    async def get(self, object_key: str) -> bytes:
        def download() -> bytes:
            response = self.client.get_object(self.bucket, object_key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        return await anyio.to_thread.run_sync(download)

    async def delete(self, object_key: str) -> None:
        await anyio.to_thread.run_sync(self.client.remove_object, self.bucket, object_key)

