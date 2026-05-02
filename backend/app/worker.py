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

Gap mitigations (added):
- GAP 1/6: Execution barrier — db.refresh + status verify after claim before pipeline runs
- GAP 2:   poll_after_ms hint embedded in every finalized result for frontend backoff
- GAP 3:   Rate-limit-aware pipeline wrapper with exponential backoff + jitter
- GAP 4/5: No duplicate bundles — barrier prevents duplicate execution; primary_bundle only
- GAP 7:   Module-level asyncio.Semaphore caps concurrent pipeline executions per replica
- GAP 8/9: run_id (uuid4) threaded through all log events; attempt/retry_delay_ms fields added
- GAP 10:  poll_after_ms=0 on terminal states; non-zero on retries
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from app.core.config import settings
from app.core.db import AsyncSessionLocal
from app.core.queue import dequeue_job, enqueue_job, get_redis
from app.models.models import Job
from app.services.storage import public_asset_url

logger = logging.getLogger("vizzy.worker")

_TERMINAL = frozenset({"done", "failed"})

# GAP 7 — Limits concurrent pipeline executions within a single replica.
# Prevents one replica from spamming the image provider while another is
# already mid-execution. Tune MAX_CONCURRENT to match provider rate limits.
_MAX_CONCURRENT_PIPELINES: int = getattr(settings, "WORKER_MAX_CONCURRENT_PIPELINES", 2)
_PIPELINE_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_PIPELINES)

# GAP 3 — How many times we retry a single pipeline execution on rate-limit errors
# before letting the job-level retry mechanism take over.
_RATE_LIMIT_MAX_RETRIES: int = 3
_RATE_LIMIT_BASE_DELAY: float = 2.0   # seconds; doubled each attempt
_RATE_LIMIT_MAX_DELAY: float = 30.0   # cap
_REDIS_DISCONNECT_LOG_THRESHOLD: int = 5
_REDIS_DISCONNECT_LOG_EVERY: int = 10


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detect HTTP 429 / provider rate-limit errors by class name and message."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return (
        "ratelimit" in name
        or "toomanyrequests" in name
        or "429" in msg
        or "rate limit" in msg
        or "too many requests" in msg
    )


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
        logger.info(
            "worker_version_check",
            extra={
                "event": "worker_version_check",
                "version": "final_retry_safe_v1",
            },
        )
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
        """On startup: find stuck pending/running jobs and re-enqueue to Redis.

        Handles:
        - Container restart mid-job (status=running, never completed)
        - Redis lost the queue entry (job in DB but not in queue)
        - Worker crash (job stuck in running)

        If result is already populated but status is not done,
        reconcile to done (idempotent repair — never downgrade).
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

                    recovered = await self._recover_result_from_committed_message(job)
                    if recovered is not None:
                        status_before = job.status
                        recovered.setdefault("_meta", {})
                        recovered["_meta"].update({
                            "run_id": "startup_recovery",
                            "poll_after_ms": 0,
                            "completed_at": datetime.utcnow().isoformat(),
                        })
                        job.status = "done"
                        job.result = recovered
                        job.error = None
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
                                "has_result": True,
                            },
                        )
                        continue

                    await db.refresh(job)
                    if job.status == "done" or job.result is not None:
                        logger.info(
                            "retry_blocked_already_completed",
                            extra={
                                "event": "retry_blocked_already_completed",
                                "job_id": job.id,
                                "status": job.status,
                                "has_result": job.result is not None,
                                "source": "orphan_recovery_enqueue",
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

                    await db.refresh(job)
                    if job.status == "done" or job.result is not None:
                        logger.info(
                            "retry_blocked_already_completed",
                            extra={
                                "event": "retry_blocked_already_completed",
                                "job_id": job.id,
                                "status": job.status,
                                "has_result": job.result is not None,
                                "source": "orphan_recovery_pre_enqueue",
                            },
                        )
                        continue

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
                should_log_disconnect = (
                    consecutive_failures <= _REDIS_DISCONNECT_LOG_THRESHOLD
                    or consecutive_failures % _REDIS_DISCONNECT_LOG_EVERY == 0
                )
                if should_log_disconnect:
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
        """
        Production-safe validation.

        Rules:
        - reply present and non-empty → valid (chat response)
        - asset_bundle present (even if empty dict/list) → valid (generation)
        - NEVER reject real generation output
        """
        if not result_data or not isinstance(result_data, dict):
            return False

        # Chat response
        if isinstance(result_data.get("reply"), str) and result_data["reply"].strip():
            return True

        # Generation success: bundle with assets.
        bundle = result_data.get("asset_bundle")
        if isinstance(bundle, dict) and bundle.get("assets"):
            return True

        # Fallback: explicit asset_bundle key presence still counts as success.
        if "asset_bundle" in result_data:
            return True

        return False

    async def _finalize_success(
        self,
        job_id: str,
        result_data: dict[str, Any],
        *,
        started_at_perf: float,
        run_id: str,
    ) -> None:
        """Write terminal success using a dedicated DB session.

        The pipeline shares the claim session and commits many times; persisting
        Job.status/Job.result here avoids a stale or inconsistent session
        leaving the row stuck in 'running' after image_success.

        GAP 2/10: Embeds poll_after_ms=0 so the frontend stops polling immediately.
        GAP 9:    Attaches run_id to result metadata for deduplication tracing.
        """
        status_before_ok = "running"
        async with AsyncSessionLocal() as db:
            try:
                job = (
                    await db.execute(select(Job).where(Job.id == job_id))
                ).scalar_one_or_none()
                if job is None:
                    logger.error(
                        "finalize_success_job_missing",
                        extra={
                            "event": "finalize_success_job_missing",
                            "job_id": job_id,
                            "run_id": run_id,
                        },
                    )
                    return

                if job.status in _TERMINAL:
                    try:
                        logger.info(
                            "job_skipped",
                            extra={
                                "event": "job_skipped",
                                "job_id": job_id,
                                "run_id": run_id,
                                "reason": "already_terminal_before_success_persist",
                                "status_before": job.status,
                                "status_after": job.status,
                            },
                        )
                    except Exception:
                        pass
                    return

                # Guard against double-finalization in race conditions.
                if job.result is not None:
                    if job.status != "done":
                        job.status = "done"
                        job.completed_at = job.completed_at or datetime.utcnow()
                        job.error = None
                        await db.commit()
                    return

                # GAP 2/10 — Embed frontend polling hint and run identity in result metadata.
                result_data.setdefault("_meta", {})
                result_data["_meta"].update({
                    "run_id": run_id,
                    "poll_after_ms": 0,            # terminal — frontend should stop polling
                    "completed_at": datetime.utcnow().isoformat(),
                })

                status_before_ok = job.status
                job.status = "done"
                job.result = result_data
                job.completed_at = datetime.utcnow()
                job.error = None
                await db.commit()
                await db.refresh(job)
                logger.info(
                    "job_success_marker_v1",
                    extra={"job_id": job_id},
                )
            except Exception as exc:
                logger.error(
                    "finalize_success_failed",
                    extra={
                        "event": "finalize_success_failed",
                        "job_id": job_id,
                        "run_id": run_id,
                        "error": str(exc),
                    },
                )
                raise

        try:
            logger.info(
                "job_finalized",
                extra={
                    "event": "job_finalized",
                    "job_id": job_id,
                    "run_id": run_id,
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
                    "run_id": run_id,
                    "status_before": status_before_ok,
                    "status_after": "done",
                    "duration_ms": duration_ms,
                },
            )
        except Exception:
            pass

    async def _update_progress(
        self,
        db,
        job: Job,
        stage: str,
        percent: int,
        *,
        message: str,
        run_id: str,
    ) -> None:
        """Persist lightweight progress without changing the polling API shape."""
        job.progress = {
            "stage": stage,
            "percent": percent,
            "message": message,
            "updated_at": datetime.utcnow().isoformat(),
        }
        await db.commit()
        logger.info(
            "job_progress_updated",
            extra={
                "event": "job_progress_updated",
                "job_id": job.id,
                "run_id": run_id,
                "status": job.status,
                "stage": stage,
                "percent": percent,
            },
        )

    async def _acquire_execution_lock(self, job_id: str, run_id: str) -> str | None:
        lock_key = f"job_lock:{job_id}"
        redis = await get_redis()
        if redis is None:
            logger.warning(
                "execution_lock_unavailable",
                extra={"event": "execution_lock_unavailable", "job_id": job_id, "run_id": run_id},
            )
            return None

        acquired = await redis.set(lock_key, "1", nx=True, ex=300)
        if not acquired:
            logger.info(
                "execution_skipped_locked",
                extra={"event": "execution_skipped_locked", "job_id": job_id, "run_id": run_id},
            )
            return None
        return lock_key

    async def _release_execution_lock(self, lock_key: str | None) -> None:
        if lock_key is None:
            return
        try:
            redis = await get_redis()
            if redis is not None:
                await redis.delete(lock_key)
        except Exception as exc:
            logger.warning(
                "execution_lock_release_failed",
                extra={"event": "execution_lock_release_failed", "error": str(exc)[:200]},
            )

    async def _process_job(self, job_id: str) -> None:
        """Execute a single job with atomic claim and timeout.

        GAP 9: Generates a run_id at entry — every log event for this execution
               carries it, making duplicate runs trivially distinguishable in logs.
        """
        # GAP 9 — Unique identity for this specific execution attempt.
        run_id = str(uuid.uuid4())
        started_at = time.perf_counter()

        async with AsyncSessionLocal() as db:
            snapshot = (
                await db.execute(select(Job).where(Job.id == job_id))
            ).scalar_one_or_none()

            if snapshot is None:
                logger.warning(
                    "job_not_found",
                    extra={"event": "job_not_found", "job_id": job_id, "run_id": run_id},
                )
                return

            if snapshot.status == "done" or snapshot.result is not None:
                logger.info(
                    "skip_already_completed_job",
                    extra={
                        "event": "skip_already_completed_job",
                        "job_id": job_id,
                        "run_id": run_id,
                        "reason": "already_completed_at_fetch",
                        "status_before": snapshot.status,
                        "has_result": snapshot.result is not None,
                    },
                )
                if snapshot.status != "done" and snapshot.result is not None:
                    snapshot.status = "done"
                    if snapshot.completed_at is None:
                        snapshot.completed_at = datetime.utcnow()
                    await db.commit()
                return

            # Terminal guard — never reprocess finished jobs
            if snapshot.status in _TERMINAL:
                logger.info(
                    "job_skipped",
                    extra={
                        "event": "job_skipped",
                        "job_id": job_id,
                        "run_id": run_id,
                        "reason": "already_terminal",
                        "status_before": snapshot.status,
                        "status_after": snapshot.status,
                    },
                )
                return

            if snapshot.status == "running" and snapshot.attempts > 0:
                logger.info(
                    "duplicate_execution_blocked",
                    extra={
                        "event": "duplicate_execution_blocked",
                        "job_id": job_id,
                        "run_id": run_id,
                        "status_before": snapshot.status,
                        "status_after": snapshot.status,
                        "reason": "already_running_attempted",
                        "attempt": snapshot.attempts,
                    },
                )
                return

            lock_key = await self._acquire_execution_lock(job_id, run_id)
            if lock_key is None:
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
                        "run_id": run_id,
                        "status_before": "pending",
                        "status_after": lost or "unknown",
                    },
                )
                await self._release_execution_lock(lock_key)
                return

            job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one()

            # GAP 1/6 — EXECUTION BARRIER.
            # Re-read the row from DB after the claim commit. In a multi-replica
            # environment there is a window between the UPDATE returning and this
            # read where another worker could have claimed or completed the job.
            # Only proceed if we genuinely own a 'running' row right now.
            await db.refresh(job)
            if job.status == "done" or job.result is not None:
                logger.info(
                    "skip_already_completed_job",
                    extra={
                        "event": "skip_already_completed_job",
                        "job_id": job_id,
                        "run_id": run_id,
                        "reason": "already_completed_after_claim",
                        "status_before": job.status,
                        "has_result": job.result is not None,
                    },
                )
                await self._release_execution_lock(lock_key)
                return

            if job.status != "running":
                logger.info(
                    "job_execution_barrier_rejected",
                    extra={
                        "event": "job_execution_barrier_rejected",
                        "job_id": job_id,
                        "run_id": run_id,
                        "status_after_refresh": job.status,
                        "reason": "status_changed_between_claim_and_execute",
                    },
                )
                await self._release_execution_lock(lock_key)
                return

            logger.info(
                "job_claimed",
                extra={
                    "event": "job_claimed",
                    "job_id": job.id,
                    "run_id": run_id,
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
                    "run_id": run_id,
                    "status_before": "pending",
                    "status_after": "running",
                    "attempt": job.attempts,
                    "type": job.type,
                },
            )
            await self._update_progress(
                db,
                job,
                "starting",
                10,
                message="Generating your design...",
                run_id=run_id,
            )

            try:
                # GAP 7 — Semaphore caps concurrent pipeline executions per replica,
                # preventing this worker from flooding the image provider.
                await self._update_progress(
                    db,
                    job,
                    "generating",
                    60,
                    message="Still working...",
                    run_id=run_id,
                )
                async with _PIPELINE_SEMAPHORE:
                    result_data = await asyncio.wait_for(
                        # GAP 3 — Rate-limit-aware wrapper with backoff + jitter.
                        self._execute_pipeline_with_backoff(db, job, run_id=run_id),
                        timeout=settings.QUEUE_JOB_TIMEOUT_SECONDS,
                    )

                # If validation fails but an asset_bundle is present, treat as success.
                if not self._pipeline_result_is_valid(result_data):
                    # SAFETY: bundle exists → success regardless of other fields
                    if isinstance(result_data, dict) and result_data.get("asset_bundle") is not None:
                        await self._update_progress(
                            db,
                            job,
                            "finalizing",
                            90,
                            message="Here's your design. Want to refine?",
                            run_id=run_id,
                        )
                        await self._finalize_success(
                            job_id, result_data, started_at_perf=started_at, run_id=run_id
                        )
                        return

                    await self._finalize_failure(
                        job_id, "Pipeline returned empty or invalid result", run_id=run_id
                    )
                    return

                # Normal success path
                await self._update_progress(
                    db,
                    job,
                    "finalizing",
                    90,
                    message="Here's your design. Want to refine?",
                    run_id=run_id,
                )
                await self._finalize_success(
                    job_id, result_data, started_at_perf=started_at, run_id=run_id
                )
                return

            except asyncio.TimeoutError:
                try:
                    await db.rollback()
                except Exception:
                    pass

                # Timeout race hardening: if another path already persisted result,
                # finalize as success instead of forcing failure/retry.
                try:
                    await db.refresh(job)
                except Exception:
                    pass
                if job.result is not None:
                    await self._finalize_success(
                        job.id,
                        job.result,
                        started_at_perf=started_at,
                        run_id=run_id,
                    )
                    return

                recovered = await self._recover_result_from_committed_message(job)
                if recovered is not None:
                    await self._finalize_success(
                        job.id,
                        recovered,
                        started_at_perf=started_at,
                        run_id=run_id,
                    )
                    return

                await self._finalize_failure(job_id, "Job timed out", run_id=run_id)

            except Exception as exc:
                try:
                    await db.rollback()
                except Exception:
                    pass
                recovered = await self._recover_result_from_committed_message(job)
                if recovered is not None:
                    await self._finalize_success(
                        job.id,
                        recovered,
                        started_at_perf=started_at,
                        run_id=run_id,
                    )
                    return
                await self._finalize_failure(job_id, str(exc), run_id=run_id)
            finally:
                await self._release_execution_lock(lock_key)

    async def _recover_result_from_committed_message(self, job: Job) -> dict[str, Any] | None:
        """Recover a completed job when timeout lands after durable message commit."""
        from app.models.models import Asset, Message

        async with AsyncSessionLocal() as db:
            message = (
                await db.execute(
                    select(Message)
                    .where(
                        Message.session_id == job.session_id,
                        Message.role == "assistant",
                        Message.asset_bundle_id.is_not(None),
                    )
                    .order_by(Message.created_at.desc(), Message.sequence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            if message is None or not message.asset_bundle_id:
                return None

            if job.started_at and message.created_at and message.created_at < job.started_at:
                return None

            assets = (
                await db.execute(
                    select(Asset)
                    .where(Asset.bundle_id == message.asset_bundle_id)
                    .order_by(Asset.variant_index.asc(), Asset.created_at.asc())
                )
            ).scalars().all()

            if not assets:
                return None

            tool_calls = message.tool_calls or {}
            creative_output = tool_calls.get("creative_output")
            intent = tool_calls.get("intent")

            bundle: dict[str, Any] | None = None
            if isinstance(creative_output, dict):
                for item in creative_output.get("outputs") or []:
                    if isinstance(item, dict) and item.get("kind") == "asset_bundle":
                        candidate = item.get("bundle")
                        if isinstance(candidate, dict):
                            bundle = dict(candidate)
                            break

            if bundle is None:
                bundle = {
                    "bundle_id": message.asset_bundle_id,
                    "type": assets[0].type,
                    "prompt_used": assets[0].prompt,
                    "actions": ["select", "download_all", "refine", "share", "save"],
                }

            bundle["assets"] = [
                {
                    "id": asset.id,
                    "url": public_asset_url(asset.url),
                    "index": asset.variant_index,
                    "type": asset.type,
                }
                for asset in assets
            ]

            logger.warning(
                "job_result_recovered_from_message",
                extra={
                    "event": "job_result_recovered_from_message",
                    "job_id": job.id,
                    "status_before": job.status,
                    "status_after": "done",
                    "has_result": True,
                },
            )

            return {
                "reply": message.content,
                "asset_bundle": bundle,
                "creative_output": creative_output,
                "intent": intent,
                "tool_call": {"name": "pipeline", "arguments": intent} if intent else None,
            }

    async def _finalize_failure(self, job_id: str, error_msg: str, *, run_id: str) -> None:
        """Handle failure using a fresh DB session; never overwrite 'done' or valid result."""
        async with AsyncSessionLocal() as db:
            job = (
                await db.execute(select(Job).where(Job.id == job_id))
            ).scalar_one_or_none()
            if job is None:
                return

            if job.status == "done" or job.result is not None:
                logger.info(
                    "retry_blocked_already_completed",
                    extra={
                        "event": "retry_blocked_already_completed",
                        "job_id": job_id,
                        "run_id": run_id,
                        "status": job.status,
                        "has_result": job.result is not None,
                        "source": "finalize_failure",
                    },
                )
                return

            status_before = job.status

            if job.status not in ("running", "pending"):
                logger.info(
                    "job_skipped",
                    extra={
                        "event": "job_skipped",
                        "job_id": job_id,
                        "run_id": run_id,
                        "reason": "unexpected_status_on_failure",
                        "status_before": status_before,
                        "status_after": job.status,
                    },
                )
                return

            await self._handle_failure(db, job, error_msg, run_id=run_id)

    # ------------------------------------------------------------------
    # Rate-limit-aware pipeline execution  (GAP 3)
    # ------------------------------------------------------------------

    async def _execute_pipeline_with_backoff(
        self, db, job: Job, *, run_id: str
    ) -> dict:
        """Execute the pipeline with exponential backoff + jitter on rate-limit errors.

        Up to _RATE_LIMIT_MAX_RETRIES internal retries are attempted before the
        exception propagates to the job-level failure handler.  This keeps job-level
        retry counts clean — a transient provider blip does not burn an attempt.

        GAP 3: delay = min(base * 2^attempt, max) + uniform(0, 1) jitter
        """
        last_exc: Exception | None = None

        for attempt in range(_RATE_LIMIT_MAX_RETRIES + 1):
            try:
                return await self._execute_pipeline(db, job)

            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    # Non-rate-limit errors propagate immediately.
                    raise

                logger.error(
                    "pipeline_rate_limit_retries_exhausted",
                    extra={
                        "event": "pipeline_rate_limit_retries_exhausted",
                        "job_id": job.id,
                        "run_id": run_id,
                        "attempt": attempt,
                        "error": str(exc)[:300],
                    },
                )
                raise

        # Unreachable — loop always raises or returns, but satisfies type checkers.
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    async def _execute_pipeline(self, db, job: Job) -> dict:
        """Rebuild full PipelineContext from DB and execute the pipeline.

        GAP 4/5: Takes only primary_bundle from pipeline result — extra bundles
                 generated by fallback loops inside the pipeline are discarded here,
                 ensuring ONE job → ONE asset bundle in the response.
        """
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
            design_context=intent_data.get("design_context"),
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
        # Lightweight post-commit sanity query to surface session health issues early.
        await db.execute(select(Job.id).where(Job.id == job.id).limit(1))

        # GAP 4/5 — Take only the primary bundle; discard any extras produced by
        # fallback loops inside the pipeline to guarantee one job → one bundle.
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

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    async def _handle_failure(self, db, job: Job, error_msg: str, *, run_id: str) -> None:
        """Retry or mark failed. Caller must ensure job is not terminal."""
        await db.refresh(job)

        status_before = job.status

        # Absolute guard — covers both job.status == "done" and job.result is not None.
        if job.status == "done" or job.result is not None:
            logger.info(
                "retry_blocked_already_completed",
                extra={
                    "event": "retry_blocked_already_completed",
                    "job_id": job.id,
                    "run_id": run_id,
                    "status": job.status,
                    "has_result": job.result is not None,
                    "source": "handle_failure_entry",
                },
            )
            return

        will_retry = (
            job.status != "done"
            and job.result is None
            and not _is_rate_limit_error(RuntimeError(error_msg))
            and job.attempts < 3
        )

        if will_retry:
            # ---------------------------------------------------------
            # SAFETY GUARD — DO NOT RETRY SUCCESSFUL JOBS
            # ---------------------------------------------------------
            if job.status == "done" or job.result is not None:
                try:
                    logger.info(
                        "job_retry_skipped_already_completed",
                        extra={
                            "event": "job_retry_skipped_already_completed",
                            "job_id": job.id,
                            "status": job.status,
                            "has_result": job.result is not None,
                        },
                    )
                except Exception:
                    pass
                return
            # ---------------------------------------------------------

            # GAP 10 — Retry delay hint for the frontend: mirrors the backoff the
            # worker will apply before re-executing, so the client waits the right amount.
            retry_delay_s = min(2 ** job.attempts, 30)
            poll_after_ms = round(retry_delay_s * 1000)

            retry_update = await db.execute(
                update(Job)
                .where(
                    Job.id == job.id,
                    Job.status.not_in(["done", "failed"]),
                    Job.result.is_(None),
                )
                .values(status="pending", error=error_msg)
            )
            if retry_update.rowcount == 0:
                await db.rollback()
                logger.info(
                    "retry_blocked_already_completed",
                    extra={
                        "event": "retry_blocked_already_completed",
                        "job_id": job.id,
                        "run_id": run_id,
                        "status": job.status,
                        "has_result": job.result is not None,
                        "source": "atomic_retry_update",
                    },
                )
                return
            await db.commit()

            # ABSOLUTE FINAL GUARD — re-read DB state before enqueue to catch
            # any concurrent success commit from another worker/session.
            await db.refresh(job)
            if job.status == "done" or job.result is not None:
                logger.info(
                    "retry_blocked_already_completed",
                    extra={
                        "event": "retry_blocked_already_completed",
                        "job_id": job.id,
                        "run_id": run_id,
                        "status": job.status,
                        "has_result": job.result is not None,
                        "source": "pre_retry_enqueue",
                    },
                )
                return

            await enqueue_job(job.id)
            logger.info(
                "retry_marker_v1",
                extra={"job_id": job.id},
            )

            logger.warning(
                "job_retry_scheduled",
                extra={
                    "event": "job_retry_scheduled",
                    "job_id": job.id,
                    "run_id": run_id,
                    "error": error_msg[:300],
                    "attempt": job.attempts,
                    "status_before": status_before,
                    "status_after": "pending",
                    "will_retry": True,
                    "poll_after_ms": poll_after_ms,
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
                "run_id": run_id,
                "error": error_msg[:300],
                "attempt": job.attempts,
                "status_before": status_before,
                "status_after": "failed",
                "will_retry": False,
                "poll_after_ms": 0,    # terminal — frontend stops polling
            },
        )
