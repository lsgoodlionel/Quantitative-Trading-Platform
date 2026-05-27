"""
Celery 应用配置

队列设计:
  data      — 数据回填/更新任务（低优先级）
  strategy  — 策略定时执行任务（高优先级）
  default   — 其他任务

运行方式:
  # Worker（处理任务）
  celery -A app.tasks.celery_app worker -Q data,strategy,default -c 4 --loglevel=info

  # Beat（定时调度）
  celery -A app.tasks.celery_app beat --loglevel=info

  # Flower 监控面板
  celery -A app.tasks.celery_app flower --port=5555
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "quantbot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "app.tasks.data",
        "app.tasks.notify",
    ],
)

celery_app.conf.update(
    # 序列化格式
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,

    # 任务路由
    task_routes={
        "app.tasks.data.*":     {"queue": "data"},
        "app.tasks.notify.*":   {"queue": "default"},
    },

    # 结果保留时间
    result_expires=3600,  # 1 小时

    # 重试配置
    task_max_retries=3,
    task_default_retry_delay=60,  # 秒

    # 任务超时
    task_soft_time_limit=300,   # 5 分钟软超时
    task_time_limit=600,        # 10 分钟硬超时

    # Beat 定时任务
    beat_schedule={
        # 每天 18:00 回填美股日线数据（美股 4:00 PM ET 收盘）
        "backfill-us-daily": {
            "task": "app.tasks.data.backfill_market",
            "schedule": crontab(hour=18, minute=0),
            "args": ("US", "1d", 2),
            "options": {"queue": "data"},
        },
        # 每天 16:30 回填港股日线数据（港股 4:00 PM HKT 收盘）
        "backfill-hk-daily": {
            "task": "app.tasks.data.backfill_market",
            "schedule": crontab(hour=16, minute=30),
            "args": ("HK", "1d", 2),
            "options": {"queue": "data"},
        },
        # 每天 15:30 回填 A 股日线数据（A股 3:00 PM CST 收盘）
        "backfill-a-daily": {
            "task": "app.tasks.data.backfill_market",
            "schedule": crontab(hour=15, minute=30),
            "args": ("A", "1d", 2),
            "options": {"queue": "data"},
        },
        # 每 5 分钟清理过期 Redis 数据
        "cleanup-redis-cache": {
            "task": "app.tasks.data.cleanup_cache",
            "schedule": crontab(minute="*/5"),
            "options": {"queue": "default"},
        },
    },
)
