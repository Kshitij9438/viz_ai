"""Restart-safe async background worker.

Continuously polls a Redis queue for job IDs, claims jobs atomically from
the database, executes the pipeline, and stores results back in PostgreSQL.

Key resilience features:
- Atomic job claim: UPDATE ... WHERE status='pending' prevents multi-replica races
- Terminal states (done/failed) are never overwritten by error handlers
- Failure handlers reload job row from DB before mutating (stale ORM safe)
- Orphan recovery on startup: re-enqueues stuck pending/running jobs
- Reconnect with exponential backoff on Redis disconnect
- Per-job timeout via asyncio.wait_for
- Graceful shutdown via asyncio.Event
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.queue import dequeue_job, enqueue_job
from app.models.models import Job
from app.services.storage import public_asset_url

logger = logging.getLogger("vizzy.worker")

_TERMINAL = frozenset({"done", "failed"})


class JobWorker:
    """In-process async worker with restart-safe guarantees."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._shutdown = asyncio.Event()

    async def start(self) -> None:
        """Start the worker: recover orphans, then begin polling."""
        self._shutdown.clear()
        await self._recover_orphaned_jobs()
        self._task = asyncio.create_task(self._loop(), name="vizzy-worker")
        logger.info("worker_started", extra={"event": "worker_started"})

    async def stop(self) -> None:
        """Graceful shutdown: signal stop, wait for current job to finish."""
        self._shutdown.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._task), timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        logger.info("worker_stopped", extra={"event": "worker_stopped"})

    # ------------------------------------------------------------------
    # Orphan recovery
    # ------------------------------------------------------------------

    async def _recover_orphaned_jobs(self) -> None:
        """On startup: find stuck pending/running jobs → re-enqueue to Redis.

        This handles:
        - Container restart mid-job (status=running, never completed)
        - Redis lost the queue entry (job in DB but not in queue)
        - Worker crash (job stuck in running)

        If ``result`` is already populated but status is not ``done``,
        reconcile to ``done`` (idempotent repair — never downgrade).
        """
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Job).where(Job.status.in_(["pending", "running"]))
                )
                orphans = result.scalars().all()

                for job in orphans:
                    # Repair: success payload persisted but status not terminal
                    if job.result is not None and job.status in ("pending", "running"):
                        status_before = job.status
                        job.status = "done"
                        if job.completed_at is None:
                            job.completed_at = datetime.utcnow()
                        await db.commit()
                        logger.info(
                            "job_reconciled",
                            extra={
                                "event": "job_reconciled",
                                "job_id": job.id,
                                "status_before": status_before,
                                "status_after": "done",
                            },
                        )
                        continue

                    # Reset running jobs back to pending for re-claim
                    if job.status == "running":
                        status_before = job.status
                        job.status = "pending"
                        await db.commit()
                    else:
                        status_before = job.status

                    enqueued = await enqueue_job(job.id)
                    logger.info(
                        "job_recovered",
                        extra={
                            "event": "job_recovered",
                            "job_id": job.id,
                            "original_status": status_before,
                            "re_enqueued": enqueued,
                        },
                    )

                if orphans:
                    logger.info(
                        "orphan_recovery_complete",
                        extra={"event": "orphan_recovery_complete", "count": len(orphans)},
                    )
        except Exception as exc:
            logger.error(
                "orphan_recovery_failed",
                extra={"event": "orphan_recovery_failed", "error": str(exc)},
            )

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        """Continuously poll Redis queue with reconnect resilience."""
        consecutive_failures = 0

        while not self._shutdown.is_set():
            try:
                job_id = await dequeue_job(timeout=5)
                if job_id is None:
                    consecutive_failures = 0
                    await asyncio.sleep(2)
                    continue

                consecutive_failures = 0
                await self._process_job(job_id)

            except ConnectionError:
                consecutive_failures += 1
                delay = min(2 ** consecutive_failures, 60)
                logger.warning(
                    "worker_redis_disconnect",
                    extra={
                        "event": "worker_redis_disconnect",
                        "consecutive_failures": consecutive_failures,
                        "backoff_seconds": delay,
                    },
                )
                from app.core.queue import reset_redis_state

                await reset_redis_state()
                await asyncio.sleep(delay)

            except asyncio.CancelledError:
                break

            except Exception as exc:
                consecutive_failures += 1
                delay = min(2 ** consecutive_failures, 60)
                logger.exception(
                    "worker_loop_error",
                    extra={
                        "event": "worker_loop_error",
                        "error": str(exc),
                        "consecutive_failures": consecutive_failures,
                        "backoff_seconds": delay,
                    },
                )
                await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Job processing
    # ------------------------------------------------------------------

    @staticmethod
    def _pipeline_result_is_valid(result_data: Any) -> bool:
        """True if the worker should mark the job done (never fail after real generation)."""
        if not result_data or not isinstance(result_data, dict):
            return False
        if result_data.get("reply"):
            return True
        if result_data.get("asset_bundle"):
            return True
        return False

    async def _finalize_success(
        self, job_id: str, result_data: dict[str, Any], *, started_at_perf: float
    ) -> None:
        """Write terminal success using a dedicated DB session.

        The pipeline shares the claim session and commits many times; persisting
        ``Job.status``/``Job.result`` here avoids a stale or inconsistent session
        leaving the row stuck in ``running`` after ``image_success``.
        """
        status_before_ok = "running"
        async with AsyncSessionLocal() as db:
            job = (
                await db.execute(select(Job).where(Job.id == job_id))
            ).scalar_one_or_none()
            if job is None:
                try:
                    logger.warning(
                        "job_finalize_success_job_missing",
                        extra={
                            "event": "job_finalize_success_job_missing",
                            "job_id": job_id,
                        },
                    )
                except Exception:
                    pass
                return
            if job.status in _TERMINAL:
                try:
                    logger.info(
                        "job_skipped",
                        extra={
                            "event": "job_skipped",
                            "job_id": job_id,
                            "reason": "already_terminal_before_success_persist",
                            "status_before": job.status,
                            "status_after": job.status,
                        },
                    )
                except Exception:
                    pass
                return
            status_before_ok = job.status
            job.status = "done"
            job.result = result_data
            job.completed_at = datetime.utcnow()
            job.error = None
            await db.commit()

        try:
            logger.info(
                "job_finalized",
                extra={
                    "event": "job_finalized",
                    "job_id": job_id,
                    "status": "done",
                    "has_result": True,
                },
            )
        except Exception:
            pass

        try:
            duration_ms = round((time.perf_counter() - started_at_perf) * 1000, 2)
            logger.info(
                "job_succeeded",
                extra={
                    "event": "job_succeeded",
                    "job_id": job_id,
                    "status_before": status_before_ok,
                    "status_after": "done",
                    "duration_ms": duration_ms,
                },
            )
        except Exception:
            pass

    async def _process_job(self, job_id: str) -> None:
        """Execute a single job with atomic claim and timeout."""
        started_at = time.perf_counter()

        async with AsyncSessionLocal() as db:
            snapshot = (
                await db.execute(select(Job).where(Job.id == job_id))
            ).scalar_one_or_none()

            if snapshot is None:
                logger.warning(
                    "job_not_found",
                    extra={"event": "job_not_found", "job_id": job_id},
                )
                return

            # Terminal guard — never reprocess finished jobs (prevents duplicate runs / retries)
            if snapshot.status in _TERMINAL:
                logger.info(
                    "job_skipped",
                    extra={
                        "event": "job_skipped",
                        "job_id": job_id,
                        "reason": "already_terminal",
                        "status_before": snapshot.status,
                        "status_after": snapshot.status,
                    },
                )
                return

            if snapshot.result is not None and snapshot.status in ("pending", "running"):
                # Inconsistent row: treat as success (repair on the fly)
                logger.info(
                    "job_skipped",
                    extra={
                        "event": "job_skipped",
                        "job_id": job_id,
                        "reason": "has_result_reconcile",
                        "status_before": snapshot.status,
                        "status_after": "done",
                    },
                )
                snapshot.status = "done"
                if snapshot.completed_at is None:
                    snapshot.completed_at = datetime.utcnow()
                await db.commit()
                return

            # ---- ATOMIC CLAIM ----
            upd = await db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "pending")
                .values(
                    status="running",
                    started_at=datetime.utcnow(),
                    attempts=Job.attempts + 1,
                )
                .returning(Job.id)
            )
            claimed_id = upd.scalar_one_or_none()
            await db.commit()

            if claimed_id is None:
                lost = (
                    await db.execute(select(Job.status).where(Job.id == job_id))
                ).scalar_one_or_none()
                logger.info(
                    "job_claim_skipped",
                    extra={
                        "event": "job_claim_skipped",
                        "job_id": job_id,
                        "status_before": "pending",
                        "status_after": lost or "unknown",
                    },
                )
                return

            job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()

            logger.info(
                "job_claimed",
                extra={
                    "event": "job_claimed",
                    "job_id": job.id,
                    "status_before": "pending",
                    "status_after": "running",
                    "attempt": job.attempts,
                },
            )
            logger.info(
                "job_started",
                extra={
                    "event": "job_started",
                    "job_id": job.id,
                    "status_before": "pending",
                    "status_after": "running",
                    "attempt": job.attempts,
                    "type": job.type,
                },
            )

            try:
                result_data = await asyncio.wait_for(
                    self._execute_pipeline(db, job),
                    timeout=settings.QUEUE_JOB_TIMEOUT_SECONDS,
                )

                if not self._pipeline_result_is_valid(result_data):
                    await self._finalize_failure(job_id, "Pipeline returned empty or invalid result")
                    return

                await self._finalize_success(job_id, result_data, started_at_perf=started_at)

            except asyncio.TimeoutError:
                try:
                    await db.rollback()
                except Exception:
                    pass
                await self._finalize_failure(job_id, "Job timed out")

            except Exception as exc:
                try:
                    await db.rollback()
                except Exception:
                    pass
                await self._finalize_failure(job_id, str(exc))

    async def _finalize_failure(self, job_id: str, error_msg: str) -> None:
        """Handle failure using a fresh DB session; never overwrite ``done`` or valid ``result``."""
        async with AsyncSessionLocal() as db:
            job = (
                await db.execute(select(Job).where(Job.id == job_id))
            ).scalar_one_or_none()
            if job is None:
                return

            status_before = job.status

            # Never retry / mark failed once success is persisted (or already terminal).
            if job.status == "done" or job.result is not None:
                if job.result is not None and job.status != "done":
                    job.status = "done"
                    job.error = None
                    if job.completed_at is None:
                        job.completed_at = datetime.utcnow()
                    await db.commit()
                    try:
                        logger.warning(
                            "job_recovered_result_on_failure_path",
                            extra={
                                "event": "job_recovered_result_on_failure_path",
                                "job_id": job_id,
                                "status_before": status_before,
                                "status_after": "done",
                            },
                        )
                    except Exception:
                        pass
                else:
                    try:
                        logger.info(
                            "job_skipped",
                            extra={
                                "event": "job_skipped",
                                "job_id": job_id,
                                "reason": "failure_after_done_or_has_result",
                                "status_before": status_before,
                                "status_after": job.status,
                            },
                        )
                    except Exception:
                        pass
                return

            if job.status not in ("running", "pending"):
                logger.info(
                    "job_skipped",
                    extra={
                        "event": "job_skipped",
                        "job_id": job_id,
                        "reason": "unexpected_status_on_failure",
                        "status_before": status_before,
                        "status_after": job.status,
                    },
                )
                return

            await self._handle_failure(db, job, error_msg)

    async def _execute_pipeline(self, db, job: Job) -> dict:
        """Rebuild full PipelineContext from DB and execute the pipeline."""
        from app.memory.memory import get_or_create_taste, get_business, get_recent_messages
        from app.models.models import Session as SessionModel, Message
        from app.services.intent_engine import IntentResult, PIPELINE_STEPS
        from app.services.pipeline_engine import PipelineContext, execute_pipeline
        from sqlalchemy import func, select as sa_select

        session = (
            await db.execute(select(SessionModel).where(SessionModel.id == job.session_id))
        ).scalar_one()

        taste = await get_or_create_taste(db, job.user_id)
        business = await get_business(db, job.user_id)
        recent_msgs = await get_recent_messages(db, job.session_id)

        max_seq_result = await db.execute(
            sa_select(func.max(Message.sequence)).where(Message.session_id == job.session_id)
        )
        current_max = max_seq_result.scalar() or 0

        user_msg = Message(
            session_id=job.session_id,
            role="user",
            content=job.message,
            sequence=current_max + 1,
            attachments=job.attachments_json,
        )
        db.add(user_msg)
        session.message_count += 1
        await db.commit()

        intent_data = job.intent_data or {}
        pipeline_name = intent_data.get("pipeline", "image_pipeline")
        if pipeline_name not in PIPELINE_STEPS:
            pipeline_name = "image_pipeline"

        intent = IntentResult(
            intent=intent_data.get("intent", "image"),
            pipeline=pipeline_name,
            steps=intent_data.get("steps", PIPELINE_STEPS[pipeline_name]),
            confidence=float(intent_data.get("confidence", 0.7)),
            execute=bool(intent_data.get("execute", True)),
            parameters=intent_data.get("parameters", {}),
        )

        ctx = PipelineContext(
            db=db,
            user_id=job.user_id,
            session_id=job.session_id,
            message=job.message,
            attachments=job.attachments_json,
            recent_messages=recent_msgs,
            taste=taste,
            business=business,
            session_last_prompt=session.last_prompt,
        )

        pipeline_result = await execute_pipeline(ctx, intent)

        if pipeline_result.primary_bundle:
            session.last_prompt = (
                pipeline_result.primary_bundle.get("prompt_used")
                or pipeline_result.memory_signal
            )

        asst_seq = current_max + 2
        asst_msg = Message(
            session_id=job.session_id,
            role="assistant",
            content=pipeline_result.reply,
            tool_calls={
                "creative_output": pipeline_result.creative_output,
                "intent": intent.model_dump(),
                "bundle_ids": pipeline_result.bundle_ids,
            },
            asset_bundle_id=(
                pipeline_result.primary_bundle.get("bundle_id")
                if pipeline_result.primary_bundle
                else None
            ),
            sequence=asst_seq,
        )
        db.add(asst_msg)
        await db.commit()

        bundle = pipeline_result.primary_bundle
        if bundle:
            bundle = dict(bundle)
            bundle["assets"] = [
                {**a, "url": public_asset_url(a.get("url"))}
                for a in bundle.get("assets", [])
                if a.get("url")
            ]

        return {
            "reply": pipeline_result.reply,
            "asset_bundle": bundle,
            "creative_output": pipeline_result.creative_output,
            "intent": intent.model_dump(),
            "tool_call": pipeline_result.tool_call,
        }

    async def _handle_failure(self, db, job: Job, error_msg: str) -> None:
        """Retry or mark failed. Caller must ensure job is not terminal."""
        await db.refresh(job)

        status_before = job.status

        if job.status == "done":
            logger.info(
                "job_skipped",
                extra={
                    "event": "job_skipped",
                    "job_id": job.id,
                    "reason": "handle_failure_done_guard",
                    "status_before": status_before,
                    "status_after": "done",
                },
            )
            return

        if job.result is not None:
            job.status = "done"
            job.error = None
            if job.completed_at is None:
                job.completed_at = datetime.utcnow()
            await db.commit()
            logger.warning(
                "job_recovered_result_in_handle_failure",
                extra={
                    "event": "job_recovered_result_in_handle_failure",
                    "job_id": job.id,
                    "status_before": status_before,
                    "status_after": "done",
                },
            )
            return

        # Never retry after success payload or terminal success
        will_retry = (
            job.status != "done"
            and job.result is None
            and job.attempts < settings.QUEUE_MAX_TRIES
        )

        if will_retry:
            job.status = "pending"
            job.error = error_msg
            await db.commit()

            await enqueue_job(job.id)

            logger.warning(
                "job_retry_scheduled",
                extra={
                    "event": "job_retry_scheduled",
                    "job_id": job.id,
                    "error": error_msg[:300],
                    "attempt": job.attempts,
                    "status_before": status_before,
                    "status_after": "pending",
                    "will_retry": True,
                },
            )
            return

        job.status = "failed"
        job.error = error_msg
        job.completed_at = datetime.utcnow()
        await db.commit()

        logger.error(
            "job_failed",
            extra={
                "event": "job_failed",
                "job_id": job.id,
                "error": error_msg[:300],
                "attempt": job.attempts,
                "status_before": status_before,
                "status_after": "failed",
                "will_retry": False,
            },
        )
