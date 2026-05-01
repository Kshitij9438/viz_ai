"""Restart-safe async background worker.

Continuously polls a Redis queue for job IDs, claims jobs atomically from
the database, executes the pipeline, and stores results back in PostgreSQL.

Key resilience features:
- Atomic job claim: UPDATE ... WHERE status='pending' prevents multi-replica races
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

from sqlalchemy import select, update

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.queue import dequeue_job, enqueue_job, get_redis
from app.models.models import Job
from app.services.storage import public_asset_url

logger = logging.getLogger("vizzy.worker")


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
        """
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Job).where(Job.status.in_(["pending", "running"]))
                )
                orphans = result.scalars().all()

                for job in orphans:
                    # Reset running jobs back to pending
                    if job.status == "running":
                        job.status = "pending"
                        await db.commit()

                    # Re-enqueue to Redis
                    enqueued = await enqueue_job(job.id)
                    logger.info(
                        "job_recovered",
                        extra={
                            "event": "job_recovered",
                            "job_id": job.id,
                            "original_status": "running" if job.status == "pending" else job.status,
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
                    # No jobs in queue — sleep before polling again
                    # (RPOP is non-blocking, so we must throttle ourselves)
                    consecutive_failures = 0
                    await asyncio.sleep(2)
                    continue

                consecutive_failures = 0
                await self._process_job(job_id)

            except ConnectionError:
                # Redis disconnected — backoff and retry
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
                # Reset Redis state so get_redis() retries the connection
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

    async def _process_job(self, job_id: str) -> None:
        """Execute a single job with atomic claim and timeout."""
        started_at = time.perf_counter()

        async with AsyncSessionLocal() as db:
            # ---- ATOMIC CLAIM ----
            # Only ONE worker can claim a job. If another worker already
            # changed the status, this UPDATE affects 0 rows → we skip.
            result = await db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "pending")
                .values(
                    status="running",
                    started_at=datetime.utcnow(),
                    attempts=Job.attempts + 1,
                )
                .returning(Job.id)
            )
            claimed = result.scalar_one_or_none()
            await db.commit()

            if claimed is None:
                logger.info(
                    "job_claim_skipped",
                    extra={"event": "job_claim_skipped", "job_id": job_id},
                )
                return

            # Reload the full job record
            job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()

            logger.info(
                "job_started",
                extra={
                    "event": "job_started",
                    "job_id": job.id,
                    "attempt": job.attempts,
                    "type": job.type,
                },
            )

            try:
                # ---- REBUILD CONTEXT & EXECUTE ----
                result_data = await asyncio.wait_for(
                    self._execute_pipeline(db, job),
                    timeout=settings.QUEUE_JOB_TIMEOUT_SECONDS,
                )

                # ---- STORE RESULT ----
                job.status = "done"
                job.result = result_data
                job.completed_at = datetime.utcnow()
                await db.commit()

                duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
                logger.info(
                    "job_completed",
                    extra={
                        "event": "job_completed",
                        "job_id": job.id,
                        "duration_ms": duration_ms,
                    },
                )

            except asyncio.TimeoutError:
                await self._handle_failure(db, job, "Job timed out")

            except Exception as exc:
                await self._handle_failure(db, job, str(exc))

    async def _execute_pipeline(self, db, job: Job) -> dict:
        """Rebuild full PipelineContext from DB and execute the pipeline."""
        from app.memory.memory import get_or_create_taste, get_business, get_recent_messages
        from app.models.models import Session as SessionModel, Message
        from app.services.intent_engine import IntentResult, PIPELINE_STEPS
        from app.services.pipeline_engine import PipelineContext, execute_pipeline
        from sqlalchemy import func, select as sa_select

        # Load fresh context from DB
        session = (await db.execute(
            select(SessionModel).where(SessionModel.id == job.session_id)
        )).scalar_one()

        taste = await get_or_create_taste(db, job.user_id)
        business = await get_business(db, job.user_id)
        recent_msgs = await get_recent_messages(db, job.session_id)

        # Store the user message in conversation history
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

        # Reconstruct intent from stored data
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

        # Build context and execute
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

        # Update session state
        if pipeline_result.primary_bundle:
            session.last_prompt = (
                pipeline_result.primary_bundle.get("prompt_used")
                or pipeline_result.memory_signal
            )

        # Store assistant message
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

        # Normalize asset URLs for the result
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
        """Handle job failure: retry or mark as permanently failed."""
        will_retry = job.attempts < settings.QUEUE_MAX_TRIES

        if will_retry:
            job.status = "pending"
            job.error = error_msg
            await db.commit()

            # Re-enqueue for retry
            await enqueue_job(job.id)

            logger.warning(
                "job_failed",
                extra={
                    "event": "job_failed",
                    "job_id": job.id,
                    "error": error_msg[:300],
                    "attempt": job.attempts,
                    "will_retry": True,
                },
            )
        else:
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
                    "will_retry": False,
                },
            )
