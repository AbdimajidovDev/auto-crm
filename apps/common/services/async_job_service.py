import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
import io
import os

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import connections, transaction
from django.utils import timezone

from apps.common.models import AsyncJob

logger = logging.getLogger(__name__)

# Controlled background worker pool (default 2 workers to keep server light)
_EXECUTOR = ThreadPoolExecutor(max_workers=getattr(settings, "ASYNC_JOB_WORKERS", 2), thread_name_prefix="async_job_worker")


class AsyncJobService:
    """
    Central background job orchestration service.
    Dual-mode:
      - Default: Managed ThreadPoolExecutor with safe Django DB connection handling.
      - Celery-ready: If settings.CELERY_ENABLED is True, dispatches to celery tasks.
    """

    @classmethod
    def create_and_submit(
        cls,
        *,
        job_type: str,
        user,
        store=None,
        payload: dict | None = None,
        input_file=None,
        task_func=None,
        task_args: tuple = (),
        task_kwargs: dict | None = None,
    ) -> AsyncJob:
        payload = payload or {}
        task_kwargs = task_kwargs or {}

        job = AsyncJob.objects.create(
            job_type=job_type,
            status=AsyncJob.JobStatus.PENDING,
            store=store,
            created_by=user,
            payload=payload,
            progress=0,
        )

        if input_file:
            input_file.seek(0)
            file_bytes = input_file.read()
            job.input_file.save(f"{job.id}_{input_file.name}", ContentFile(file_bytes), save=True)

        if getattr(settings, "ASYNC_JOB_ALWAYS_SYNC", False):
            cls._execute_job_in_worker(job.id, task_func, is_sync_call=True, *task_args, **task_kwargs)
            return job

        if getattr(settings, "CELERY_ENABLED", False):
            # Celery dispatch fallback
            try:
                from apps.common.tasks import run_async_job_task
                run_async_job_task.delay(str(job.id))
                return job
            except ImportError:
                pass

        # ThreadPool dispatch
        _EXECUTOR.submit(cls._execute_job_in_worker, job.id, task_func, is_sync_call=False, *task_args, **task_kwargs)
        return job

    @classmethod
    def _execute_job_in_worker(cls, job_id, task_func, *args, is_sync_call=False, **kwargs):
        # Only isolate database connections when running in an asynchronous worker thread
        if not is_sync_call:
            connections.close_all()
        try:
            job = AsyncJob.objects.filter(id=job_id).first()
            if not job:
                logger.error("AsyncJob %s not found for worker execution.", job_id)
                return

            if job.status == AsyncJob.JobStatus.CANCELLED:
                logger.info("AsyncJob %s was cancelled before start.", job_id)
                return

            job.status = AsyncJob.JobStatus.PROCESSING
            job.started_at = timezone.now()
            job.progress = 10
            job.save(update_fields=["status", "started_at", "progress"])

            result = task_func(job, *args, **kwargs)

            job.refresh_from_db()
            if job.status != AsyncJob.JobStatus.CANCELLED:
                job.status = AsyncJob.JobStatus.COMPLETED
                job.progress = 100
                job.completed_at = timezone.now()
                job.result = cls._serialize_result(result)
                job.save(update_fields=["status", "progress", "completed_at", "result"])
                logger.info("AsyncJob %s completed successfully.", job_id)
        except Exception as exc:
            logger.exception("AsyncJob %s failed with error: %s", job_id, exc)
            try:
                job = AsyncJob.objects.filter(id=job_id).first()
                if job and job.status != AsyncJob.JobStatus.CANCELLED:
                    job.status = AsyncJob.JobStatus.FAILED
                    job.completed_at = timezone.now()
                    job.error_message = str(exc)
                    job.save(update_fields=["status", "completed_at", "error_message"])
            except Exception as inner_e:
                logger.error("Failed to update status for failed job %s: %s", job_id, inner_e)
        finally:
            if not is_sync_call:
                connections.close_all()

    @classmethod
    def cancel_job(cls, job_id, user) -> AsyncJob:
        job = cls.get_job(job_id, user)
        if job.status in (AsyncJob.JobStatus.COMPLETED, AsyncJob.JobStatus.FAILED):
            raise ValidationError("Yakunlangan vazifani bekor qilib bo'lmaydi.")
        job.status = AsyncJob.JobStatus.CANCELLED
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at"])
        return job

    @classmethod
    def get_job(cls, job_id, user) -> AsyncJob:
        try:
            job = AsyncJob.objects.select_related("store", "created_by").get(id=job_id)
        except AsyncJob.DoesNotExist:
            raise ValidationError("Vazifa topilmadi.")

        if not user.is_superuser:
            if job.created_by_id != user.id:
                from apps.common.store_scope import allowed_store_ids
                allowed = allowed_store_ids(user)
                if allowed is None or (job.store_id and job.store_id in allowed):
                    pass
                else:
                    raise PermissionDenied("Ushbu vazifaga ruxsat yo'q.")
        return job

    @classmethod
    def cleanup_old_jobs(cls, hours: int = 24) -> int:
        cutoff = timezone.now() - timedelta(hours=hours)
        old_jobs = AsyncJob.objects.filter(created_at__lt=cutoff)
        count = 0
        for job in old_jobs:
            if job.input_file:
                job.input_file.delete(save=False)
            if job.output_file:
                job.output_file.delete(save=False)
            job.delete()
            count += 1
        return count

    @staticmethod
    def _serialize_result(val):
        if val is None:
            return {}
        if isinstance(val, dict):
            clean = {}
            for k, v in val.items():
                if isinstance(v, Decimal):
                    clean[k] = str(v)
                elif isinstance(v, (list, tuple)):
                    clean[k] = [str(x) if isinstance(x, Decimal) else x for x in v]
                else:
                    clean[k] = v
            return clean
        return {"data": str(val)}
