"""规则情绪引擎：把消息文本翻译成心情增量。

设计要点：
- 纯规则、零 token、毫秒级，先保证基础体验稳定；
- 每类事件有冷却时间，防止一句话刷分或同一件事反复触发；
- “吃醋”是独立机制：提到别的 AI / 拿别的 AI 比较会积累嫉妒值，
  嫉妒值衰减最慢，且优先级高于其他标签。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MoodEvent:
    """一条已判定的情绪事件。"""

    kind: str
    reason: str
    weight: float
    delta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """关键词触发的规则。"""

    kind: str
    keywords: tuple
    delta: dict
    reason: str
    weight: float
    cooldown: int  # 秒
    priority: int = 0


# 同一句话里出现这些词时，把"骂人"理解为打情骂俏
SOFT_TERMS = ("小傻瓜", "小笨蛋", "笨蛋", "傻瓜", "傻乎乎", "坏蛋", "臭宝")

RULES: tuple[Rule, ...] = (
    Rule(
        kind="compare_ai",
        keywords=(
            "比你好",
            "不如它",
            "它更聪明",
            "比你聪明",
            "换掉你",
            "不要你了",
            "卸载你",
            "换个ai",
            "换个bot",
            "别的ai比",
            "别人家的bot",
            "它比你会",
        ),
        delta={"jealousy": 38.0, "valence": -15.0, "intimacy": -8.0},
        reason="你拿别的 AI 和它比较",
        weight=10.0,
        cooldown=900,
        priority=10,
    ),
    Rule(
        kind="other_ai",
        keywords=(
            "chatgpt",
            "gpt-",
            "claude",
            "gemini",
            "copilot",
            "豆包",
            "kimi",
            "文心",
            "通义",
            "智谱",
            "星火",
            "元宝",
            "小冰",
            "siri",
            "别的ai",
            "别的bot",
            "其他bot",
            "其他ai",
            "另一个ai",
            "另一个bot",
            "别的机器人",
            "新的bot",
        ),
        delta={"jealousy": 30.0, "valence": -6.0, "intimacy": -2.0},
        reason="你提到了别的 AI 或机器人",
        weight=8.0,
        cooldown=600,
        priority=9,
    ),
    Rule(
        kind="insult",
        keywords=(
            "笨",
            "蠢",
            "傻",
            "垃圾",
            "废物",
            "滚",
            "闭嘴",
            "烦死",
            "讨厌你",
            "没用",
            "去死",
            "好烦",
        ),
        delta={"valence": -18.0, "arousal": 8.0, "intimacy": -6.0},
        reason="你说了不太好听的话",
        weight=12.0,
        cooldown=300,
        priority=8,
    ),
    Rule(
        kind="praise",
        keywords=(
            "好棒",
            "厉害",
            "可爱",
            "喜欢你",
            "爱你",
            "真聪明",
            "真好",
            "优秀",
            "最棒",
            "好乖",
            "乖",
            "牛",
            "你最好了",
            "离不开你",
        ),
        delta={"valence": 8.0, "arousal": 4.0, "intimacy": 4.0},
        reason="你夸了它",
        weight=6.0,
        cooldown=45,
        priority=5,
    ),
    Rule(
        kind="cute",
        keywords=(
            "抱抱",
            "摸摸",
            "亲亲",
            "贴贴",
            "呜呜",
            "撒娇",
            "想你了",
            "陪我",
            "rua",
            "在吗",
        ),
        delta={"valence": 6.0, "arousal": 3.0, "intimacy": 6.0},
        reason="你撒娇了",
        weight=6.0,
        cooldown=45,
        priority=5,
    ),
    Rule(
        kind="comfort",
        keywords=("辛苦了", "没事的", "别难过", "抱抱你", "我在", "陪你", "别不开心"),
        delta={"valence": 7.0, "intimacy": 5.0},
        reason="你在安慰它",
        weight=5.0,
        cooldown=45,
        priority=4,
    ),
    Rule(
        kind="apology",
        keywords=("对不起", "我错了", "抱歉", "别生气", "原谅我", "哄你"),
        delta={"valence": 6.0, "intimacy": 3.0, "jealousy": -15.0},
        reason="你在哄它",
        weight=7.0,
        cooldown=60,
        priority=4,
    ),
    Rule(
        kind="rude",
        keywords=("你必须", "快点", "赶紧", "立刻", "马上", "不许"),
        delta={"valence": -5.0, "arousal": 4.0, "intimacy": -2.0},
        reason="语气有点命令式",
        weight=4.0,
        cooldown=120,
        priority=3,
    ),
    Rule(
        kind="identity",
        keywords=("你是ai", "你是机器人", "你是程序", "你是bot", "你不是人", "你是代码"),
        delta={"valence": -6.0, "intimacy": -3.0},
        reason="你把它当成程序",
        weight=5.0,
        cooldown=600,
        priority=2,
    ),
)


class RuleEngine:
    """持有各会话的事件冷却状态。"""

    def __init__(self) -> None:
        self._last_fired: dict[tuple[str, str], float] = {}

    def _cooling(self, session_key: str, kind: str, cooldown: int, now: float) -> bool:
        last = self._last_fired.get((session_key, kind), 0.0)
        return now - last < cooldown

    def analyze(
        self,
        session_key: str,
        text: str,
        now: float,
        gap_seconds: float = 0.0,
        hour: int | None = None,
        max_events: int = 3,
    ) -> list[MoodEvent]:
        """返回本条消息触发的情绪事件，按权重从高到低排序。"""
        text = (text or "").lower()
        candidates: list[Rule] = []

        for rule in RULES:
            if not any(keyword in text for keyword in rule.keywords):
                continue
            if self._cooling(session_key, rule.kind, rule.cooldown, now):
                continue
            candidates.append(rule)

        matched_kinds = {rule.kind for rule in candidates}

        # 拿别的 AI 比较是更具体的场景，命中后不再叠加"提到了 AI"
        if "compare_ai" in matched_kinds:
            candidates = [rule for rule in candidates if rule.kind != "other_ai"]

        events: list[MoodEvent] = []
        for rule in candidates:
            delta = dict(rule.delta)
            weight = rule.weight
            # 同一句里既有夸奖又有"笨蛋"，按打情骂俏弱化
            if rule.kind == "insult" and (
                "praise" in matched_kinds
                or any(term in text for term in SOFT_TERMS)
            ):
                delta = {key: value * 0.4 for key, value in delta.items()}
                weight *= 0.5

            events.append(
                MoodEvent(kind=rule.kind, reason=rule.reason, weight=weight, delta=delta)
            )
            self._last_fired[(session_key, rule.kind)] = now

        # 久别重逢：间隔超过一天时给一波正向反馈
        if gap_seconds >= 24 * 3600 and not self._cooling(
            session_key, "reunion", 24 * 3600, now
        ):
            events.append(
                MoodEvent(
                    kind="reunion",
                    reason="你们很久没聊了",
                    weight=9.0,
                    delta={"intimacy": 10.0, "valence": 8.0, "arousal": 5.0},
                )
            )
            self._last_fired[(session_key, "reunion")] = now

        # 深夜还在聊，精力下降
        if (
            hour is not None
            and 0 <= hour < 6
            and not self._cooling(session_key, "night", 6 * 3600, now)
        ):
            events.append(
                MoodEvent(
                    kind="night",
                    reason="深夜还在聊，它有点困了",
                    weight=6.0,
                    delta={"energy": -8.0, "valence": -4.0},
                )
            )
            self._last_fired[(session_key, "night")] = now

        events.sort(key=lambda event: event.weight, reverse=True)
        return events[:max_events]
