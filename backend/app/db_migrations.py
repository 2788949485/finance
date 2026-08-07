"""数据库索引迁移。

在各表创建后补充索引，加速高频查询。
CREATE INDEX IF NOT EXISTS 保证可重复执行不报错。
"""
from __future__ import annotations

import logging

from .config import _connect

logger = logging.getLogger(__name__)

_INDEXES = [
    # analyses: 按user_id查历史、按ticker查同股票分析
    "CREATE INDEX IF NOT EXISTS idx_analyses_user ON analyses(user_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_analyses_ticker ON analyses(ticker, status)",

    # chat_sessions: 按user_id列表
    "CREATE INDEX IF NOT EXISTS idx_chat_sessions_user ON chat_sessions(user_id, id)",

    # chat_messages: 按session_id查消息(最高频查询)
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id)",

    # chat_messages: 搜索消息内容
    "CREATE INDEX IF NOT EXISTS idx_chat_messages_search ON chat_messages(content)",

    # user_memories: 按user_id查记忆
    "CREATE INDEX IF NOT EXISTS idx_user_memories_user ON user_memories(user_id, memory_type)",

    # portfolio: 按user_id查持仓
    "CREATE INDEX IF NOT EXISTS idx_portfolio_user ON portfolio(user_id)",

    # transactions: 按user_id查交易记录
    "CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id, id)",

    # alerts: 按user_id+status查活跃预警
    "CREATE INDEX IF NOT EXISTS idx_alerts_user_status ON alerts(user_id, status)",

    # audit_log: 按时间查日志
    "CREATE INDEX IF NOT EXISTS idx_audit_log_time ON audit_log(created_at)",

    # scheduled_results: 已有索引(scheduler.py),这里不重复

    # thesis_checks: 已有索引(thesis_tracker.py),这里不重复
]


def run_migrations() -> None:
    """执行所有索引迁移（幂等，可安全重复调用）。"""
    try:
        with _connect() as conn:
            for sql in _INDEXES:
                try:
                    conn.execute(sql)
                except Exception as e:
                    logger.debug("索引跳过(可能表不存在): %s -> %s", sql[:50], e)
        logger.info("数据库索引迁移完成: %d 条", len(_INDEXES))
    except Exception as e:
        logger.warning("索引迁移失败(不阻塞启动): %s", e)
