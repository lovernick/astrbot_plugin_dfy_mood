"""WebUI 数据接口。

页面位于 pages/心情面板/，通过 AstrBotPluginPage.apiGet / apiPost 调用这里的路由。
所有路由都以插件名做前缀，返回 JSON。
"""

from __future__ import annotations

import json
import time

from astrbot.api import logger
from quart import jsonify, request

try:
    from .core.state import (
        BASELINE_AROUSAL,
        BASELINE_ENERGY,
        BASELINE_INTIMACY,
        BASELINE_VALENCE,
        derive_label,
    )
except ImportError:  # 兼容以顶层模块方式加载
    from core.state import (
        BASELINE_AROUSAL,
        BASELINE_ENERGY,
        BASELINE_INTIMACY,
        BASELINE_VALENCE,
        derive_label,
    )

RANGES = {
    "24h": 86400,
    "7d": 7 * 86400,
    "30d": 30 * 86400,
}

FIELD_LABEL = {
    "valence": "愉悦",
    "arousal": "活跃",
    "energy": "精力",
    "intimacy": "亲密",
    "jealousy": "吃醋",
}

KIND_LABEL = {
    "praise": "被夸奖",
    "insult": "被嫌弃",
    "tease": "打情骂俏",
    "comfort": "被安慰",
    "apology": "被哄",
    "compare_ai": "被拿来比较",
    "other_ai": "提到别的 AI",
    "cute": "被撒娇",
    "missing": "想念",
    "reunion": "久别重逢",
    "late_night": "深夜陪聊",
    "neglect": "被冷落",
    "neutral": "没什么特别",
}

# 设置页允许修改的配置项
EDITABLE_CONFIG = [
    ("enabled", "总开关", "bool", 1),
    ("private_only", "只记私聊", "bool", 1),
    ("inject_prompt", "注入心情提示", "bool", 1),
    ("tool_enable", "模型自评工具", "bool", 0),
    ("tool_hint", "只在疑似情绪消息里提示", "bool", 0),
    ("tool_cooldown_seconds", "同类事件冷却（秒）", "int", 2),
    ("tool_max_intensity", "单次影响上限", "float", 0),
    ("max_events_per_message", "每轮最多事件", "int", 1),
    ("half_life_valence", "愉悦半衰期（小时）", "float", 0),
    ("half_life_arousal", "活跃半衰期（小时）", "float", 0),
    ("half_life_energy", "精力半衰期（小时）", "float", 0),
    ("half_life_jealousy", "吃醋半衰期（小时）", "float", 1),
    ("half_life_intimacy", "亲密半衰期（小时）", "float", 0),
    ("snapshot_retention_days", "快照保留天数", "int", 1),
    ("owner_ids", "主人 QQ（逗号分隔）", "str", 1),
]


def _range_seconds(raw: str) -> int:
    return RANGES.get(str(raw or "").lower(), RANGES["24h"])


def _split_session(session_key: str) -> tuple[str, str]:
    platform, _, uid = str(session_key or "").partition(":")
    return platform or "unknown", uid or str(session_key or "")


def _delta_text(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: round(float(value), 1) for key, value in data.items() if value}


def _state_payload(state) -> dict:
    return {
        "session_key": state.session_key,
        "valence": round(state.valence, 1),
        "arousal": round(state.arousal, 1),
        "energy": round(state.energy, 1),
        "intimacy": round(state.intimacy, 1),
        "jealousy": round(state.jealousy, 1),
        "label": state.label,
        "enabled": bool(state.enabled),
        "updated_at": state.updated_at,
        "last_interact_at": state.last_interact_at,
    }


def _point_payload(row) -> dict:
    ts, v, a, e, i, j, label = row
    return {
        "ts": ts,
        "valence": round(v, 1),
        "arousal": round(a, 1),
        "energy": round(e, 1),
        "intimacy": round(i, 1),
        "jealousy": round(j, 1),
        "label": label,
    }


async def _resolve_key(plugin, raw: str) -> str:
    """未指定会话时退回最近互动的那个。"""
    key = str(raw or "").strip()
    if key:
        return key
    rows = plugin.store.sessions(time.time() - 30 * 86400)
    return rows[0]["session_key"] if rows else ""


def register_web_apis(plugin) -> None:
    base = f"/{plugin.name}"

    def add(route: str, handler, methods, desc: str) -> None:
        plugin.context.register_web_api(f"{base}/{route}", handler, methods, desc)

    async def overview():
        key = await _resolve_key(plugin, request.args.get("session", ""))
        if not key:
            return jsonify(
                {
                    "state": None,
                    "events": [],
                    "sessions": [],
                    "points": [],
                    "server_time": time.time(),
                }
            )
        since = time.time() - _range_seconds(request.args.get("range", "24h"))
        state = await plugin._peek(key)
        points = [_point_payload(row) for row in plugin.store.history(key, since)]
        points.append(
            _point_payload(
                (
                    time.time(),
                    state.valence,
                    state.arousal,
                    state.energy,
                    state.intimacy,
                    state.jealousy,
                    state.label,
                )
            )
        )
        events = [
            {
                "ts": ts,
                "kind": kind,
                "label": KIND_LABEL.get(kind, kind),
                "reason": reason,
                "delta": _delta_text(delta),
            }
            for ts, kind, reason, delta in plugin.store.recent_events(key, 8)
        ]
        sessions = [
            {
                "session_key": row["session_key"],
                "platform": _split_session(row["session_key"])[0],
                "uid": _split_session(row["session_key"])[1],
                "label": row["label"],
                "intimacy": round(row["intimacy"], 1),
                "last_interact_at": row["last_interact_at"],
            }
            for row in plugin.store.sessions(time.time() - 30 * 86400)[:20]
        ]
        return jsonify(
            {
                "state": _state_payload(state),
                "events": events,
                "sessions": sessions,
                "points": points,
                "range": str(request.args.get("range", "24h")),
                "server_time": time.time(),
            }
        )

    async def sessions():
        since = time.time() - 30 * 86400
        rows = plugin.store.sessions(since)
        return jsonify(
            {
                "sessions": [
                    {
                        **{k: v for k, v in row.items() if k != "session_key"},
                        "session_key": row["session_key"],
                        "platform": _split_session(row["session_key"])[0],
                        "uid": _split_session(row["session_key"])[1],
                    }
                    for row in rows
                ]
            }
        )

    async def history():
        key = await _resolve_key(plugin, request.args.get("session", ""))
        if not key:
            return jsonify({"points": [], "events": []})
        since = time.time() - _range_seconds(request.args.get("range", "24h"))
        points = [_point_payload(row) for row in plugin.store.history(key, since)]
        # 末尾补一个当前值，避免曲线停在很久以前
        current = await plugin._peek(key)
        points.append(
            _point_payload(
                (
                    time.time(),
                    current.valence,
                    current.arousal,
                    current.energy,
                    current.intimacy,
                    current.jealousy,
                    current.label,
                )
            )
        )
        events = [
            {
                "ts": ts,
                "kind": kind,
                "label": KIND_LABEL.get(kind, kind),
                "reason": reason,
                "delta": _delta_text(delta),
            }
            for ts, kind, reason, delta in plugin.store.events(key, since, 500)
        ]
        return jsonify({"points": points, "events": events})

    async def events():
        key = await _resolve_key(plugin, request.args.get("session", ""))
        if not key:
            return jsonify({"events": []})
        since = time.time() - _range_seconds(request.args.get("range", "7d"))
        try:
            limit = int(request.args.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(500, limit))
        return jsonify(
            {
                "events": [
                    {
                        "ts": ts,
                        "kind": kind,
                        "label": KIND_LABEL.get(kind, kind),
                        "reason": reason,
                        "delta": _delta_text(delta),
                    }
                    for ts, kind, reason, delta in plugin.store.events(
                        key, since, limit
                    )
                ]
            }
        )

    async def stats():
        key = await _resolve_key(plugin, request.args.get("session", ""))
        if not key:
            return jsonify({"kinds": [], "hours": [], "labels": []})
        since = time.time() - _range_seconds(request.args.get("range", "7d"))
        data = plugin.store.stats(key, since)
        data["kinds"] = [
            {**item, "label": KIND_LABEL.get(item["kind"], item["kind"])}
            for item in data["kinds"]
        ]
        return jsonify(data)

    async def adjust():
        body = await request.get_json(silent=True) or {}
        key = await _resolve_key(plugin, body.get("session", ""))
        field = str(body.get("field", ""))
        if not key or field not in FIELD_LABEL:
            return jsonify({"message": "参数不正确"}), 400
        try:
            value = float(body.get("value"))
        except (TypeError, ValueError):
            return jsonify({"message": "数值需要是 0-100 之间的数字"}), 400
        value = max(0.0, min(100.0, value))
        async with plugin._lock(key):
            state = plugin.store.get(key)
            setattr(state, field, value)
            state.label = derive_label(state)
            state.updated_at = time.time()
            plugin.store.save(state)
            plugin.store.record_snapshot(
                state, time.time(), f"手动调整 {FIELD_LABEL[field]}"
            )
        return jsonify({"state": _state_payload(state)})

    async def reset():
        body = await request.get_json(silent=True) or {}
        key = await _resolve_key(plugin, body.get("session", ""))
        if not key:
            return jsonify({"message": "没有可重置的会话"}), 400
        async with plugin._lock(key):
            state = plugin.store.get(key)
            state.valence = BASELINE_VALENCE
            state.arousal = BASELINE_AROUSAL
            state.energy = BASELINE_ENERGY
            state.intimacy = BASELINE_INTIMACY
            state.jealousy = 0.0
            state.label = derive_label(state)
            state.updated_at = time.time()
            plugin.store.save(state)
            plugin.store.record_snapshot(state, time.time(), "手动重置")
        return jsonify({"state": _state_payload(state)})

    def _coerce(kind: str, raw, current):
        if kind == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "on")
        if kind == "int":
            return max(1, min(100000, int(float(raw))))
        if kind == "float":
            return max(0.0, min(100000.0, float(raw)))
        return str(raw)

    def _config_fields() -> list[dict]:
        items = []
        for key, label, kind, decimals in EDITABLE_CONFIG:
            items.append(
                {
                    "key": key,
                    "label": label,
                    "type": kind,
                    "value": plugin._cfg(key, ""),
                }
            )
        return items

    async def config():
        if request.method == "GET":
            return jsonify({"fields": _config_fields()})

        body = await request.get_json(silent=True) or {}
        changes = body.get("changes")
        if not isinstance(changes, dict):
            return jsonify({"message": "请求格式不正确"}), 400

        kinds = {key: kind for key, _label, kind, _d in EDITABLE_CONFIG}
        merged = dict(plugin.config)
        try:
            for key, raw in changes.items():
                if key not in kinds:
                    continue
                merged[key] = _coerce(kinds[key], raw, merged.get(key))
        except (TypeError, ValueError):
            return jsonify({"message": "存在不合法的配置值"}), 400

        try:
            plugin.config.save_config(merged)
        except Exception:
            logger.exception("保存心情插件配置失败")
            return jsonify({"message": "保存配置失败，请查看服务日志"}), 500

        manager = getattr(plugin.context, "_star_manager", None)
        applied = False
        if manager is not None:
            try:
                success, _ = await manager.reload(plugin.name)
                applied = bool(success)
            except Exception:
                logger.exception("配置已保存，但插件重载失败")
        return jsonify(
            {
                "fields": _config_fields(),
                "applied": applied,
                "message": "设置已保存并生效。" if applied else "设置已保存，重载插件后生效。",
            }
        )

    add("overview", overview, ["GET"], "心情面板总览")
    add("sessions", sessions, ["GET"], "会话列表")
    add("history", history, ["GET"], "心情时序")
    add("events", events, ["GET"], "情绪事件流")
    add("stats", stats, ["GET"], "情绪统计")
    add("adjust", adjust, ["POST"], "手动调整维度")
    add("reset", reset, ["POST"], "重置会话心情")
    add("config", config, ["GET", "POST"], "读取与修改配置")
