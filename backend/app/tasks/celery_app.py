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
        "app.tasks.validation",
        "app.tasks.archive",
        "app.tasks.auto_loop",
        "app.tasks.reconcile",
        "app.tasks.retrain",
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
        # 完整验证是长耗时纯计算，单独排队避免把数据回填的队列堵死
        "app.tasks.validation.*": {"queue": "compute"},
        "app.tasks.archive.*":    {"queue": "data"},
        # 自动因子循环同属长耗时纯计算，与完整验证共用 compute 队列
        "app.tasks.auto_loop.*":  {"queue": "compute"},
        # 对账是只读的券商查询，走默认队列即可（不与数据回填抢 IO）
        "app.tasks.reconcile.*":  {"queue": "default"},
        # 再训练 / 漂移检测同属长耗时纯计算，与完整验证共用 compute 队列
        "app.tasks.retrain.*":    {"queue": "compute"},
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
        # 每天 20:00 实盘对账（V3 G6）。只读：拉券商持仓/资金与本地 OMS 比对，
        # 有差异或券商不可达才发通知，一致时静默。
        # ⚠️ worker 进程的 OMS 订单簿与交易进程不共享，见 app/tasks/reconcile.py 的
        # 模块 docstring；要拿到有意义的结果请走 POST /api/v1/reconcile/{market}。
        "reconcile-live-positions": {
            "task": "app.tasks.reconcile.reconcile_all_markets",
            "schedule": crontab(hour=20, minute=0),
            "options": {"queue": "default"},
        },
    },
)

# ── V4 M6：自适应再训练的周期调度 ─────────────────────────────────
#
# **默认关闭**，由 `settings.retrain_schedule_enabled` 显式开启。
# 关闭时连条目都不注册 —— 注册一个进去就立刻 return 的任务，只会在 beat 日志里
# 每周留下一条看起来像在工作的记录。任务体内还有第二道开关检查（防御性），
# 见 `app/tasks/retrain.py::scheduled_retrain_task`。
#
# ⚠️ 即使开启，重训产出也**只入库不上线**。
if settings.retrain_schedule_enabled:
    celery_app.conf.beat_schedule["adaptive-retrain"] = {
        "task": "app.tasks.retrain.scheduled_retrain_task",
        "schedule": crontab(
            day_of_week=settings.retrain_schedule_day_of_week,
            hour=settings.retrain_schedule_hour,
            minute=0,
        ),
        "options": {"queue": "compute"},
    }
