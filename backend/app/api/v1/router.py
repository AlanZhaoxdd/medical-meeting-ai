from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.benchmarks import router as benchmarks_router
from app.api.v1.documents import router as documents_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.knowledge_bases import router as knowledge_bases_router
from app.api.v1.knowledge_items import router as knowledge_items_router
from app.api.v1.meeting_imports import router as meeting_imports_router
from app.api.v1.meeting_verification import router as meeting_verification_router
from app.api.v1.meetings import router as meetings_router
from app.api.v1.organizations import router as organizations_router
from app.api.v1.question_generation import router as question_generation_router
from app.api.v1.search import router as search_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(meetings_router)
router.include_router(meeting_verification_router)
router.include_router(question_generation_router)
router.include_router(meeting_imports_router)
router.include_router(knowledge_bases_router)
router.include_router(documents_router)
router.include_router(knowledge_items_router)
router.include_router(search_router)
router.include_router(jobs_router)
router.include_router(organizations_router)
router.include_router(benchmarks_router)
