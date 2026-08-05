import asyncio

from app.services.vector_store import VectorStore


async def main() -> None:
    store = VectorStore()
    store.ensure_collection()


if __name__ == "__main__":
    asyncio.run(main())
