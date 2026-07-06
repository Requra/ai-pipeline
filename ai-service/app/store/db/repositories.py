"""
PostgreSQL-backed store implementations (satisfy the protocols in app.store.base).

Each repository translates between the storage-agnostic domain records
(``app.store.models``) and the ORM rows (``app.store.db.models``). The
``PgResultStore`` additionally decomposes a JobResult into the normalized
requirement/story/quality tables for queryability, while keeping the full
contract payload in ``ai_job_results.result_json`` as the source of truth for
``get_result``.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.store.db import models as m
from app.store.db.session import Database
from app.store.models import (
    AiJobRecord,
    ChunkEmbeddingRecord,
    JobAttemptRecord,
    JobEventRecord,
    JobOptions,
    JobResultRecord,
    JobStatus,
    SourceChunkRecord,
    SourceDocumentRecord,
    TERMINAL_JOB_STATUSES,
    utcnow,
)


# ---------------------------------------------------------------------------
# ORM <-> domain conversion
# ---------------------------------------------------------------------------

def _job_to_domain(row: m.AiJob) -> AiJobRecord:
    return AiJobRecord(
        job_id=row.job_id,
        tenant_id=row.tenant_id,
        project_id=row.project_id,
        requested_by=row.requested_by,
        input_type=row.input_type,
        status=JobStatus(row.status),
        current_node=row.current_node,
        progress_pct=row.progress_pct,
        attempt_number=row.attempt_number,
        options=JobOptions(**(row.options_json or {})),
        contract_version=row.contract_version,
        error_code=row.error_code,
        error_message=row.error_message,
        callback_url=row.callback_url,
        cancel_requested=row.cancel_requested,
        request_fingerprint=row.request_fingerprint,
        request_fingerprint_version=row.request_fingerprint_version,
        idempotency_key=row.idempotency_key,
        last_duplicate_request_at=row.last_duplicate_request_at,
        duplicate_request_count=row.duplicate_request_count or 0,
        created_at=row.created_at,
        queued_at=row.queued_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        failed_at=row.failed_at,
        cancelled_at=row.cancelled_at,
        updated_at=row.updated_at,
    )


class PgJobStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_job(self, record: AiJobRecord) -> AiJobRecord:
        async with self._db.session() as s:
            row = m.AiJob(
                job_id=record.job_id,
                tenant_id=record.tenant_id,
                project_id=record.project_id,
                requested_by=record.requested_by,
                input_type=record.input_type,
                status=record.status.value,
                current_node=record.current_node,
                progress_pct=record.progress_pct,
                attempt_number=record.attempt_number,
                options_json=record.options.model_dump(),
                contract_version=record.contract_version,
                error_code=record.error_code,
                error_message=record.error_message,
                callback_url=record.callback_url,
                cancel_requested=record.cancel_requested,
                queued_at=record.queued_at or record.created_at,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
            return _job_to_domain(row)

    async def create_or_get(self, record: AiJobRecord) -> Tuple[AiJobRecord, bool]:
        # Atomic insert-or-fetch: `INSERT ... ON CONFLICT (job_id) DO NOTHING`
        # is a single statement the database serializes against concurrent
        # inserts of the same job_id — exactly one concurrent caller's insert
        # actually lands a row. The loser then re-reads the winner's row with
        # `SELECT ... FOR UPDATE` for a consistent, lock-held view before the
        # caller makes any idempotency decision on it (the lock is released at
        # commit, at the end of this `async with` block).
        async with self._db.session() as s:
            stmt = (
                pg_insert(m.AiJob)
                .values(
                    job_id=record.job_id,
                    tenant_id=record.tenant_id,
                    project_id=record.project_id,
                    requested_by=record.requested_by,
                    input_type=record.input_type,
                    status=record.status.value,
                    current_node=record.current_node,
                    progress_pct=record.progress_pct,
                    attempt_number=record.attempt_number,
                    options_json=record.options.model_dump(),
                    contract_version=record.contract_version,
                    error_code=record.error_code,
                    error_message=record.error_message,
                    callback_url=record.callback_url,
                    cancel_requested=record.cancel_requested,
                    request_fingerprint=record.request_fingerprint,
                    request_fingerprint_version=record.request_fingerprint_version,
                    idempotency_key=record.idempotency_key,
                    queued_at=record.queued_at or record.created_at,
                )
                .on_conflict_do_nothing(index_elements=["job_id"])
            )
            result = await s.execute(stmt)
            await s.commit()

            if result.rowcount and result.rowcount > 0:
                row = await s.get(m.AiJob, record.job_id)
                return _job_to_domain(row), True

            existing = (
                await s.execute(
                    select(m.AiJob).where(m.AiJob.job_id == record.job_id).with_for_update()
                )
            ).scalar_one()
            return _job_to_domain(existing), False

    async def mark_duplicate(self, job_id: str) -> Optional[AiJobRecord]:
        async with self._db.session() as s:
            row = await s.get(m.AiJob, job_id)
            if row is None:
                return None
            row.duplicate_request_count = (row.duplicate_request_count or 0) + 1
            row.last_duplicate_request_at = utcnow()
            row.updated_at = utcnow()
            await s.commit()
            await s.refresh(row)
            return _job_to_domain(row)

    async def try_requeue_for_retry(
        self,
        job_id: str,
        *,
        allowed_statuses: FrozenSet[JobStatus],
        fingerprint: str,
        options: JobOptions,
        callback_url: Optional[str],
    ) -> Optional[AiJobRecord]:
        # Row-locked check-and-set within one transaction: a concurrent second
        # retry call blocks on FOR UPDATE until this transaction commits, then
        # observes the now-QUEUED status (no longer retryable) and correctly
        # no-ops — two concurrent retries can never both succeed.
        allowed_values = {st.value for st in allowed_statuses}
        async with self._db.session() as s:
            row = (
                await s.execute(
                    select(m.AiJob).where(m.AiJob.job_id == job_id).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.status not in allowed_values:
                return None
            if row.request_fingerprint != fingerprint:
                return None

            now = utcnow()
            row.attempt_number += 1
            row.status = JobStatus.QUEUED.value
            row.current_node = "queued"
            row.progress_pct = 0
            row.cancel_requested = False
            row.error_code = None
            row.error_message = None
            row.options_json = options.model_dump()
            row.callback_url = callback_url or options.callback_url
            row.queued_at = now
            row.started_at = None
            row.completed_at = None
            row.failed_at = None
            row.cancelled_at = None
            row.updated_at = now
            await s.commit()
            await s.refresh(row)
            return _job_to_domain(row)

    async def get_job(self, job_id: str) -> Optional[AiJobRecord]:
        async with self._db.session() as s:
            row = await s.get(m.AiJob, job_id)
            return _job_to_domain(row) if row else None

    async def update_job(self, job_id: str, **fields: Any) -> Optional[AiJobRecord]:
        async with self._db.session() as s:
            row = await s.get(m.AiJob, job_id)
            if row is None:
                return None
            for key, value in fields.items():
                if key == "status" and isinstance(value, JobStatus):
                    value = value.value
                if key == "options" and isinstance(value, JobOptions):
                    row.options_json = value.model_dump()
                    continue
                if hasattr(row, key):
                    setattr(row, key, value)
            row.updated_at = utcnow()
            await s.commit()
            await s.refresh(row)
            return _job_to_domain(row)

    async def set_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        current_node: Optional[str] = None,
        progress_pct: Optional[int] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Optional[AiJobRecord]:
        async with self._db.session() as s:
            row = await s.get(m.AiJob, job_id)
            if row is None:
                return None
            row.status = status.value
            now = utcnow()
            if current_node is not None:
                row.current_node = current_node
            if progress_pct is not None:
                row.progress_pct = progress_pct
            if error_code is not None:
                row.error_code = error_code
            if error_message is not None:
                row.error_message = error_message
            if status == JobStatus.PROCESSING and row.started_at is None:
                row.started_at = now
            if status == JobStatus.FAILED:
                row.failed_at = now
            elif status == JobStatus.CANCELLED:
                row.cancelled_at = now
            elif status in TERMINAL_JOB_STATUSES:
                row.completed_at = now
            row.updated_at = now
            await s.commit()
            await s.refresh(row)
            return _job_to_domain(row)

    async def request_cancel(self, job_id: str) -> Optional[AiJobRecord]:
        return await self.update_job(job_id, cancel_requested=True)

    async def is_cancel_requested(self, job_id: str) -> bool:
        async with self._db.session() as s:
            row = await s.get(m.AiJob, job_id)
            return bool(row and row.cancel_requested)

    async def add_event(self, event: JobEventRecord) -> None:
        async with self._db.session() as s:
            s.add(
                m.AiJobEvent(
                    job_id=event.job_id,
                    event_type=event.event_type,
                    node_name=event.node_name,
                    message=event.message,
                    severity=event.severity,
                    metadata_json=event.metadata,
                )
            )
            await s.commit()

    async def list_events(self, job_id: str) -> List[JobEventRecord]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(m.AiJobEvent)
                    .where(m.AiJobEvent.job_id == job_id)
                    .order_by(m.AiJobEvent.created_at)
                )
            ).scalars().all()
            return [
                JobEventRecord(
                    job_id=r.job_id,
                    event_type=r.event_type,
                    node_name=r.node_name,
                    message=r.message,
                    severity=r.severity,
                    metadata=r.metadata_json or {},
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def add_attempt(self, attempt: JobAttemptRecord) -> None:
        async with self._db.session() as s:
            stmt = pg_insert(m.AiJobAttempt).values(
                job_id=attempt.job_id,
                attempt_number=attempt.attempt_number,
                status=attempt.status.value,
                started_at=attempt.started_at,
                completed_at=attempt.completed_at,
                error_code=attempt.error_code,
                error_message=attempt.error_message,
            )
            update_dict = {
                "status": stmt.excluded.status,
            }
            if attempt.completed_at is not None:
                update_dict["completed_at"] = stmt.excluded.completed_at
            if attempt.error_code is not None:
                update_dict["error_code"] = stmt.excluded.error_code
            if attempt.error_message is not None:
                update_dict["error_message"] = stmt.excluded.error_message

            stmt = stmt.on_conflict_do_update(
                constraint="uq_ai_job_attempts_job_attempt",
                set_=update_dict,
            )
            await s.execute(stmt)
            await s.commit()

    async def list_attempts(self, job_id: str) -> List[JobAttemptRecord]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(m.AiJobAttempt)
                    .where(m.AiJobAttempt.job_id == job_id)
                    .order_by(m.AiJobAttempt.attempt_number)
                )
            ).scalars().all()
            return [
                JobAttemptRecord(
                    job_id=r.job_id,
                    attempt_number=r.attempt_number,
                    status=JobStatus(r.status),
                    started_at=r.started_at,
                    completed_at=r.completed_at,
                    error_code=r.error_code,
                    error_message=r.error_message,
                    created_at=r.created_at,
                )
                for r in rows
            ]

    async def cleanup_expired(self, ttl_seconds: int) -> int:
        if ttl_seconds <= 0:
            return 0
        from datetime import timedelta

        cutoff = utcnow() - timedelta(seconds=ttl_seconds)
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(m.AiJob).where(
                        m.AiJob.status.in_([st.value for st in TERMINAL_JOB_STATUSES]),
                        m.AiJob.updated_at < cutoff,
                    )
                )
            ).scalars().all()
            for row in rows:
                await s.delete(row)  # cascades to child rows
            await s.commit()
            return len(rows)


class PgResultStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_result(
        self,
        job_id: str,
        result: Dict[str, Any],
        *,
        contract_version: str = "1.0",
        status: str = "completed",
        processing_time_ms: int = 0,
    ) -> JobResultRecord:
        async with self._db.session() as s:
            # Idempotent per attempt: clear prior decomposed rows + result.
            for table in (
                m.AiRequirementEvidence,
                m.AiAcceptanceCriterion,
                m.AiRequirement,
                m.AiUserStory,
                m.AiRequirementCoverage,
                m.AiQualityIssue,
                m.AiPipelineWarning,
            ):
                await s.execute(delete(table).where(table.job_id == job_id))
            await s.execute(delete(m.AiQualityReport).where(m.AiQualityReport.job_id == job_id))
            await s.execute(delete(m.AiJobResult).where(m.AiJobResult.job_id == job_id))

            s.add(
                m.AiJobResult(
                    job_id=job_id,
                    contract_version=contract_version,
                    status=status,
                    result_json=result,
                    exports_json=result.get("exports", {}) if isinstance(result, dict) else {},
                    artifacts_json=result.get("artifacts", {}) if isinstance(result, dict) else {},
                    processing_time_ms=processing_time_ms,
                )
            )
            await self._decompose(s, job_id, result)
            await s.commit()

        return JobResultRecord(
            job_id=job_id,
            contract_version=contract_version,
            status=status,
            result_json=result,
            processing_time_ms=processing_time_ms,
        )

    async def _decompose(self, s, job_id: str, result: Dict[str, Any]) -> None:
        """Best-effort projection of the contract payload into normalized tables."""
        if not isinstance(result, dict):
            return
        tenant = result.get("tenant_id")
        project = result.get("project_id")

        requirements_to_add = []
        evidence_to_add = []
        for req in result.get("requirements", []) or []:
            requirement_db_id = m._uuid()
            row = m.AiRequirement(
                id=requirement_db_id,
                job_id=job_id,
                tenant_id=tenant,
                project_id=project,
                requirement_key=str(req.get("id", "")),
                text=req.get("description", ""),
                actor=req.get("actor"),
                type=req.get("type"),
                labels_json=req.get("labels", []) or [],
                priority=req.get("priority", "Medium"),
                confidence_score=float(req.get("confidence_score", 0.0) or 0.0),
                deduplication_key=req.get("deduplication_key", ""),
            )
            requirements_to_add.append(row)
            for ref in req.get("source_refs", []) or []:
                evidence_to_add.append(
                    m.AiRequirementEvidence(
                        requirement_id=requirement_db_id,
                        job_id=job_id,
                        chunk_id=ref.get("chunk_id"),
                        quote=ref.get("quote", ""),
                        page_number=ref.get("page"),
                        confidence_score=float(ref.get("confidence_score", 0.0) or 0.0),
                    )
                )

        for r in requirements_to_add:
            s.add(r)
        await s.flush()

        for e in evidence_to_add:
            s.add(e)

        stories_to_add = []
        ac_to_add = []
        for story in result.get("user_stories", []) or []:
            story_db_id = m._uuid()
            row = m.AiUserStory(
                id=story_db_id,
                job_id=job_id,
                tenant_id=tenant,
                project_id=project,
                story_key=str(story.get("id", "")),
                requirement_id=story.get("requirement_id"),
                title=story.get("title", ""),
                description=story.get("user_story", ""),
                priority=story.get("priority", "Medium"),
                type=story.get("type"),
                labels_json=(story.get("jira_fields", {}) or {}).get("labels", []),
                deduplication_key=story.get("deduplication_key", ""),
                quality_json=story.get("quality", {}) or {},
                jira_fields_json=story.get("jira_fields", {}) or {},
            )
            stories_to_add.append(row)
            for ac in story.get("acceptance_criteria", []) or []:
                ac_to_add.append(
                    m.AiAcceptanceCriterion(
                        user_story_id=story_db_id,
                        job_id=job_id,
                        criterion_key=ac.get("id", ""),
                        text=ac.get("text", ""),
                        criterion_type=ac.get("criterion_type", "plain"),
                    )
                )

        for st in stories_to_add:
            s.add(st)
        await s.flush()

        for ac_row in ac_to_add:
            s.add(ac_row)

        for cov in result.get("requirement_coverages", []) or []:
            s.add(
                m.AiRequirementCoverage(
                    job_id=job_id,
                    requirement_id=cov.get("requirement_id", ""),
                    coverage_type=cov.get("coverage_type", ""),
                    story_ids_json=cov.get("story_ids", []) or [],
                    acceptance_criteria_ids_json=cov.get("acceptance_criteria_ids", []) or [],
                    reason=cov.get("reason"),
                )
            )

        for issue in result.get("quality_issues", []) or []:
            s.add(
                m.AiQualityIssue(
                    job_id=job_id,
                    item_id=str(issue.get("item_id", "")),
                    item_type=issue.get("item_type"),
                    severity=issue.get("severity", "low"),
                    rule_violated=issue.get("rule_violated", ""),
                    details=issue.get("details", ""),
                )
            )

        for warn in result.get("warnings", []) or []:
            s.add(
                m.AiPipelineWarning(
                    job_id=job_id,
                    node_name=warn.get("node_name"),
                    code=warn.get("code", ""),
                    message=warn.get("message", ""),
                )
            )

        report = result.get("quality_report")
        if isinstance(report, dict):
            s.add(
                m.AiQualityReport(
                    job_id=job_id,
                    overall_score=report.get("overall_score", 1.0),
                    traceability_coverage=report.get("traceability_coverage", 1.0),
                    groundedness_score=report.get("groundedness_score", 1.0),
                    story_completeness=report.get("story_completeness", 1.0),
                    acceptance_criteria_quality=report.get("acceptance_criteria_quality", 1.0),
                    duplicate_risk=report.get("duplicate_risk", 0.0),
                    requirement_count=report.get("requirement_count", 0),
                    story_count=report.get("story_count", 0),
                    high_severity_issue_count=report.get("high_severity_issue_count", 0),
                    report_json=report,
                )
            )

    async def get_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self._db.session() as s:
            row = (
                await s.execute(select(m.AiJobResult).where(m.AiJobResult.job_id == job_id))
            ).scalar_one_or_none()
            return dict(row.result_json) if row else None

    async def get_result_record(self, job_id: str) -> Optional[JobResultRecord]:
        async with self._db.session() as s:
            row = (
                await s.execute(select(m.AiJobResult).where(m.AiJobResult.job_id == job_id))
            ).scalar_one_or_none()
            if row is None:
                return None
            return JobResultRecord(
                job_id=row.job_id,
                contract_version=row.contract_version,
                status=row.status,
                result_json=row.result_json or {},
                exports_json=row.exports_json or {},
                artifacts_json=row.artifacts_json or {},
                processing_time_ms=row.processing_time_ms,
                created_at=row.created_at,
            )


class PgChunkStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_documents(
        self, documents: List[SourceDocumentRecord]
    ) -> List[SourceDocumentRecord]:
        if not documents:
            return []
        saved: List[SourceDocumentRecord] = []
        async with self._db.session() as s:
            job_ids = {doc.job_id for doc in documents}
            for jid in job_ids:
                await s.execute(delete(m.AiSourceDocument).where(m.AiSourceDocument.job_id == jid))
            for doc in documents:
                row = m.AiSourceDocument(
                    job_id=doc.job_id,
                    tenant_id=doc.tenant_id,
                    project_id=doc.project_id,
                    backend_document_id=doc.backend_document_id,
                    source_type=doc.source_type,
                    file_name=doc.file_name,
                    mime_type=doc.mime_type,
                    storage_key=doc.storage_key,
                    file_url=doc.file_url,
                    sha256_hash=doc.sha256_hash,
                    language=doc.language,
                    page_count=doc.page_count,
                )
                s.add(row)
                await s.flush()
                saved.append(doc.model_copy(update={"id": row.id}))
            await s.commit()
        return saved

    async def save_chunks(self, chunks: List[SourceChunkRecord]) -> None:
        if not chunks:
            return
        async with self._db.session() as s:
            job_ids = {c.job_id for c in chunks}
            for jid in job_ids:
                await s.execute(delete(m.AiSourceChunk).where(m.AiSourceChunk.job_id == jid))
            for c in chunks:
                s.add(
                    m.AiSourceChunk(
                        job_id=c.job_id,
                        tenant_id=c.tenant_id,
                        project_id=c.project_id,
                        source_document_id=c.source_document_id,
                        chunk_id=c.chunk_id,
                        chunk_index=c.chunk_index,
                        text=c.text,
                        page_number=c.page_number,
                        speaker=c.speaker,
                        start_time_sec=c.start_time_sec,
                        end_time_sec=c.end_time_sec,
                        start_char=c.start_char,
                        end_char=c.end_char,
                        token_count=c.token_count,
                    )
                )
            await s.commit()

    @staticmethod
    def _chunk_to_domain(row: m.AiSourceChunk) -> SourceChunkRecord:
        return SourceChunkRecord(
            job_id=row.job_id,
            tenant_id=row.tenant_id,
            project_id=row.project_id,
            source_document_id=row.source_document_id,
            chunk_id=row.chunk_id,
            chunk_index=row.chunk_index,
            text=row.text,
            page_number=row.page_number,
            speaker=row.speaker,
            start_time_sec=row.start_time_sec,
            end_time_sec=row.end_time_sec,
            start_char=row.start_char,
            end_char=row.end_char,
            token_count=row.token_count,
            created_at=row.created_at,
        )

    async def get_chunks(self, job_id: str) -> List[SourceChunkRecord]:
        async with self._db.session() as s:
            rows = (
                await s.execute(
                    select(m.AiSourceChunk)
                    .where(m.AiSourceChunk.job_id == job_id)
                    .order_by(m.AiSourceChunk.chunk_index)
                )
            ).scalars().all()
            return [self._chunk_to_domain(r) for r in rows]

    async def find_chunks(
        self,
        *,
        tenant_id: Optional[str] = None,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
        source_document_ids: Optional[List[str]] = None,
        chunk_ids: Optional[List[str]] = None,
    ) -> List[SourceChunkRecord]:
        async with self._db.session() as s:
            stmt = select(m.AiSourceChunk)
            if tenant_id is not None:
                stmt = stmt.where(m.AiSourceChunk.tenant_id == tenant_id)
            if project_id is not None:
                stmt = stmt.where(m.AiSourceChunk.project_id == project_id)
            if job_id is not None:
                stmt = stmt.where(m.AiSourceChunk.job_id == job_id)
            if source_document_ids:
                stmt = stmt.where(m.AiSourceChunk.source_document_id.in_(source_document_ids))
            if chunk_ids:
                stmt = stmt.where(m.AiSourceChunk.chunk_id.in_(chunk_ids))
            rows = (await s.execute(stmt)).scalars().all()
            return [self._chunk_to_domain(r) for r in rows]


class PgEmbeddingStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def save_embeddings(self, embeddings: List[ChunkEmbeddingRecord]) -> None:
        if not embeddings:
            return
        async with self._db.session() as s:
            job_ids = {e.job_id for e in embeddings}
            for jid in job_ids:
                await s.execute(delete(m.AiSourceChunkEmbedding).where(m.AiSourceChunkEmbedding.job_id == jid))
            for e in embeddings:
                s.add(
                    m.AiSourceChunkEmbedding(
                        chunk_id=e.chunk_id,
                        job_id=e.job_id,
                        tenant_id=e.tenant_id,
                        project_id=e.project_id,
                        embedding_model=e.embedding_model,
                        embedding=e.embedding,
                    )
                )
            await s.commit()

    async def count_for_job(self, job_id: str) -> int:
        from sqlalchemy import func

        async with self._db.session() as s:
            return int(
                (
                    await s.execute(
                        select(func.count())
                        .select_from(m.AiSourceChunkEmbedding)
                        .where(m.AiSourceChunkEmbedding.job_id == job_id)
                    )
                ).scalar_one()
            )

    async def vector_search(
        self,
        query_embedding: List[float],
        *,
        tenant_id: Optional[str] = None,
        project_id: Optional[str] = None,
        job_id: Optional[str] = None,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        if job_id is None and (tenant_id is None or project_id is None):
            raise ValueError(
                "vector_search requires job_id or both tenant_id and project_id"
            )
        col = m.AiSourceChunkEmbedding
        # pgvector cosine distance operator; similarity score = 1 - distance.
        distance = col.embedding.cosine_distance(query_embedding)
        async with self._db.session() as s:
            stmt = select(col.chunk_id, col.job_id, distance.label("distance"))
            if tenant_id is not None:
                stmt = stmt.where(col.tenant_id == tenant_id)
            if project_id is not None:
                stmt = stmt.where(col.project_id == project_id)
            if job_id is not None:
                stmt = stmt.where(col.job_id == job_id)
            stmt = stmt.order_by(distance).limit(max(0, top_k))
            rows = (await s.execute(stmt)).all()
            return [
                {
                    "chunk_id": r.chunk_id,
                    "job_id": r.job_id,
                    "score": round(1.0 - float(r.distance), 6),
                }
                for r in rows
            ]
