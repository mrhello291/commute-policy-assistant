"""The question endpoint employees use.

The body carries the question and nothing else. Unknown fields are rejected,
so there is no way to pass a company, version or filter: those come from the
token and the registry.
"""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.answering.service import AnswerResult, AnswerStatus
from app.api.deps import get_container, get_principal
from app.api.error_handlers import request_id
from app.auth import Principal
from app.container import Container

router = APIRouter(prefix="/v1", tags=["ask"])

MAX_QUESTION_CHARS = 500


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=3, max_length=MAX_QUESTION_CHARS)


class CitationOut(BaseModel):
    document: str
    version: int
    section: str
    title: str
    excerpt: str
    page_start: int | None = Field(None, description="First PDF page of the section (null for Markdown policies)")
    page_end: int | None = Field(None, description="Last PDF page of the section; equals page_start on one page")


class PolicyOut(BaseModel):
    document: str
    version: int


class MetaOut(BaseModel):
    llm_calls: int
    latency_ms: float


class AskResponse(BaseModel):
    status: AnswerStatus
    answer: str
    citations: list[CitationOut]
    policy: PolicyOut | None
    meta: MetaOut
    request_id: str


def to_response(result: AnswerResult, req_id: str) -> AskResponse:
    return AskResponse(
        status=result.status,
        answer=result.answer,
        citations=[
            CitationOut(
                document=c.document,
                version=c.version,
                section=c.section,
                title=c.title,
                excerpt=c.excerpt,
                page_start=c.page_start,
                page_end=c.page_end,
            )
            for c in result.citations
        ],
        policy=PolicyOut(document=result.policy.title, version=result.policy.version) if result.policy else None,
        meta=MetaOut(llm_calls=result.diagnostics.llm_calls, latency_ms=result.diagnostics.total_ms),
        request_id=req_id,
    )


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={503: {"model": AskResponse, "description": "Temporary problem; the answer says to try again."}},
)
def ask(
    body: AskRequest,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_principal),
    container: Container = Depends(get_container),
) -> AskResponse:
    result = container.answering.ask(principal, body.question)
    if result.status is AnswerStatus.TRY_AGAIN:
        response.status_code = 503
        response.headers["Retry-After"] = "30"
    return to_response(result, request_id(request))
