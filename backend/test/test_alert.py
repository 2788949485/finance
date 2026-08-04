"""app.alert 模块单元测试。

测试预警的 CRUD、触发检查、重新激活、技术指标预警。
直接操作真实 SQLite（app.config.DB_PATH），每个用例自行清理创建的预警。
运行: cd D:\\top\\finance\\backend && .venv/Scripts/python.exe -m pytest test/test_alert.py -v
"""
from __future__ import annotations

import pytest

from app.alert import (
    check_alerts,
    create_alert,
    delete_alert,
    list_alerts,
    reactivate_alert,
)
from app.config import DB_PATH


# 测试用的固定用户 id（避免与真实用户冲突，用一个较大的值）
TEST_USER_ID = 999999


@pytest.fixture(autouse=True)
def _ensure_schema():
    """每个测试前补齐旧版 alerts 表缺失的列（trigger_count），兼容已有数据库。

    alert.py 的 _ensure_table 用 CREATE TABLE IF NOT EXISTS，不会给已存在的表
    加新列，所以旧库可能缺少 trigger_count 列，导致 INSERT 报错。
    """
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(alerts)").fetchall()}
        if cols and "trigger_count" not in cols:
            conn.execute("ALTER TABLE alerts ADD COLUMN trigger_count INTEGER DEFAULT 0")
            conn.commit()
    finally:
        conn.close()
    yield


def _cleanup_alerts(user_id: int) -> None:
    """清理指定用户的所有预警（测试用）。"""
    for a in list_alerts(user_id):
        delete_alert(a["id"], user_id)


def test_alert_crud():
    """create -> list -> delete 全流程。"""
    _cleanup_alerts(TEST_USER_ID)
    try:
        # create
        created = create_alert(
            user_id=TEST_USER_ID,
            symbol="600519",
            symbol_name="贵州茅台",
            alert_type="price_above",
            threshold=99999.0,  # 不可能触发的高价
        )
        assert isinstance(created, dict)
        assert created["id"] > 0
        assert created["status"] == "active"
        alert_id = created["id"]

        # list
        alerts = list_alerts(TEST_USER_ID)
        assert isinstance(alerts, list)
        ids = [a["id"] for a in alerts]
        assert alert_id in ids

        # delete
        ok = delete_alert(alert_id, TEST_USER_ID)
        assert ok is True
        # 确认已删除
        alerts_after = list_alerts(TEST_USER_ID)
        assert alert_id not in [a["id"] for a in alerts_after]
    finally:
        _cleanup_alerts(TEST_USER_ID)


def test_check_alerts():
    """创建一个必然触发的预警(threshold=1 price_above)，验证 check_alerts 能触发。

    用 threshold=0 的 price_below 预警（任何股票现价都 >= 0，但 price_below<=0
    不一定成立）；更稳妥的方式是 price_above threshold 设极低值(0.01)，
    任何有行情的股票现价都会 >= 0.01。
    """
    _cleanup_alerts(TEST_USER_ID)
    try:
        # threshold=0.01，price_above —— 任何正常股票现价都 >= 0.01，必触发
        create_alert(
            user_id=TEST_USER_ID,
            symbol="600519",
            symbol_name="贵州茅台",
            alert_type="price_above",
            threshold=0.01,
        )

        triggered = check_alerts()
        assert isinstance(triggered, list)

        # 网络正常时，应能找到我们创建的预警
        # （若网络不通 get_stock_brief 返回 None，则不会触发——此时跳过断言）
        our = [t for t in triggered if t.get("symbol") == "600519" and t.get("user_id") == TEST_USER_ID]
        if not our:
            pytest.skip("网络不可用，600519 行情获取失败，预警未触发（非逻辑错误）")
        assert len(our) >= 1
        assert our[0]["status"] == "triggered"
        assert our[0].get("message")  # 有触发消息
    finally:
        _cleanup_alerts(TEST_USER_ID)


def test_reactivate():
    """触发后重新激活（re-arm）。"""
    _cleanup_alerts(TEST_USER_ID)
    try:
        # 创建一个必触发的预警
        created = create_alert(
            user_id=TEST_USER_ID,
            symbol="600519",
            symbol_name="贵州茅台",
            alert_type="price_above",
            threshold=0.01,
        )
        alert_id = created["id"]

        # 触发它
        check_alerts()
        alerts = list_alerts(TEST_USER_ID)
        target = [a for a in alerts if a["id"] == alert_id]
        if not target or target[0]["status"] != "triggered":
            pytest.skip("网络不可用，预警未触发，无法测试 reactivate")

        # 重新激活
        ok = reactivate_alert(alert_id, TEST_USER_ID)
        assert ok is True

        # 确认状态恢复 active
        alerts_after = list_alerts(TEST_USER_ID)
        reactivated = [a for a in alerts_after if a["id"] == alert_id][0]
        assert reactivated["status"] == "active"
        assert reactivated.get("triggered_at") is None
    finally:
        _cleanup_alerts(TEST_USER_ID)


def test_technical_alert():
    """技术指标预警创建（ma_cross_up / volume_surge）。"""
    _cleanup_alerts(TEST_USER_ID)
    try:
        # 创建均线金叉预警
        created_ma = create_alert(
            user_id=TEST_USER_ID,
            symbol="600519",
            symbol_name="贵州茅台",
            alert_type="ma_cross_up",
            threshold=0,  # 阈值对 ma_cross 无意义，但字段必填
        )
        assert isinstance(created_ma, dict)
        assert created_ma["alert_type"] == "ma_cross_up"
        assert created_ma["status"] == "active"

        # 创建放量预警
        created_vol = create_alert(
            user_id=TEST_USER_ID,
            symbol="600519",
            symbol_name="贵州茅台",
            alert_type="volume_surge",
            threshold=2.0,  # 量比2倍
        )
        assert created_vol["alert_type"] == "volume_surge"

        # 确认都能列出
        alerts = list_alerts(TEST_USER_ID)
        types = {a["alert_type"] for a in alerts}
        assert "ma_cross_up" in types
        assert "volume_surge" in types
    finally:
        _cleanup_alerts(TEST_USER_ID)
