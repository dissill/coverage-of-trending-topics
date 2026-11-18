# -*- coding: utf-8 -*-
"""astrbot_plugin_daily_news —— 每日新闻简报 + 每周新闻周报。

行为：
1. 每天到点（默认 23:00）抓取国内公开新闻源，去重后交给 AstrBot 当前模型，
   生成「今日要闻 + 反映什么 + 跟普通人有什么关系 + 政策与资金动向」，
   私聊推送给主人。
2. 每周一次（默认周日 23:10）读取最近 7 天日报，生成周报：本周主线、趋势判断、
   政策与资金方向、普通人该做/别做、下周关注。
3. 命令：/日报 /周报 /新闻源 /新闻设置 /新闻帮助。

数据落在 AstrBot 的 plugin_data/astrbot_plugin_daily_news/ 下（日报 JSON + 周报 JSON）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.message.components import Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from .news_sources import (
    SOURCE_LABELS,
    SOURCE_ORDER,
    dedupe_items,
    detect_location,
    fetch_all,
    fetch_market_snapshot,
    fetch_weather,
    format_materials,
)

PLUGIN_NAME = "astrbot_plugin_daily_news"
DEFAULT_OWNER_QQ = ""  # 留空表示未配置，安装后请在插件配置里填自己的 QQ
WEEKDAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
OWNER_ONLY_TEXT = "这个命令只有主人能用，请先在插件配置里填写 owner_qq。"
EXPLAIN_FOOTER = "这次汇报里有什么不懂的关键词吗？告诉我，我可以帮你解答。"

DAILY_SYSTEM = (
    "你是给普通中国人写每日新闻简报的编辑，读者是二十多岁的学生和打工人，时间少、不想读长文。"
    "你的活是：从候选新闻里挑出今天真正重要的 4-6 件事，按分类讲清楚事实和对普通人的影响。"
    "只依据给定材料，禁止编造；材料没说的不要补，材料不足就写“细节有限”。"
    "同一件事只写一条，一句话能说完就不写两句，禁止套话和口号。"
    "不要用 Markdown、表格、表情包，不要写“首先/其次/最后”。"
)

WEEKLY_SYSTEM = (
    "你是给普通人写新闻周报的编辑，擅长从一周的新闻里看出主线、政策方向和钱的流向。"
    "读者是二十多岁的普通打工人和学生，你要给的是判断和可执行建议，不是新闻复述。"
    "只依据给定材料，禁止编造；覆盖不全就直说覆盖不全。"
    "不要用 Markdown、表格、表情包，不要喊口号。语气冷静、具体。"
)

WATCH_SYSTEM = (
    "你是给普通读者写“关注领域”简讯的编辑。只依据给定候选材料，挑出与关注主题真正相关的 1-3 条，"
    "每条一句话讲事实、一句话讲影响；禁止编造，无关内容一律不要。"
    "候选里没有相关内容就只输出“无”。不要 Markdown、表格、表情，不要写“首先/其次”。"
)

TERM_SYSTEM = (
    "你是给普通人解释新闻里生僻词、行业黑话和新词的助手。"
    "先用一句话说清这个词本身是什么意思，再结合给定的最近新闻内容说明它在这条新闻里指什么、"
    "为什么值得注意。只依据材料和常识，不编造；材料里没有就只解释词义。"
    "总字数不超过 200 字，不要 Markdown、表格、表情。"
)

# 关注主题的关键词映射：命中别名就带上一组领域关键词，避免只靠主题字面匹配。
WATCH_HINTS: list[tuple[tuple[str, ...], list[str]]] = [
    (
        ("ai", "人工智能", "科技圈", "大模型", "智能体", "科技"),
        ["AI", "人工智能", "大模型", "算力", "芯片", "半导体", "机器人", "智能体",
         "OpenAI", "DeepSeek", "英伟达", "量子计算", "算法"],
    ),
    (
        ("股市", "a股", "股票", "大盘", "股指", "基金", "券商"),
        ["股市", "A股", "沪指", "深指", "上证", "深证", "创业板", "涨停", "跌停",
         "券商", "基金", "大盘", "北向", "股指", "成交额", "IPO"],
    ),
    (
        ("新能源", "电动车", "电车", "汽车", "车企"),
        ["新能源", "电动车", "电动汽车", "电池", "充电", "比亚迪", "特斯拉", "车企",
         "汽车", "续航", "混动", "智驾", "车企"],
    ),
    (
        ("房地产", "楼市", "房价"),
        ["房地产", "楼市", "房价", "房贷", "公积金", "商品房", "土拍", "二手房"],
    ),
    (
        ("就业", "找工作", "毕业", "考研", "考公"),
        ["就业", "招聘", "失业", "找工作", "毕业生", "考公", "考研", "岗位", "薪资"],
    ),
    (
        ("黄金", "金价"),
        ["黄金", "金价", "沪金", "避险", "贵金属"],
    ),
    (
        ("汇率", "人民币", "美元"),
        ["汇率", "人民币", "美元", "离岸", "中间价", "外汇"],
    ),
    (
        ("芯片", "半导体"),
        ["芯片", "半导体", "光刻", "晶圆", "算力", "英伟达", "国产替代"],
    ),
]

WATCH_STOPWORDS = (
    "方面", "的信息", "信息", "热点消息", "热点", "消息", "新闻", "相关", "动态",
    "报道", "一下", "每天", "额外", "帮我", "给我", "关注", "留意", "追踪",
)
WATCH_BANNED_TOPICS = {"列表", "设置", "帮助", "说明", "这个", "那个", "我", "什么", "怎么"}
STOCK_KEYWORDS = {"股市", "a股", "股票", "大盘", "股指", "基金", "券商", "上证", "深证", "创业板"}
TERM_STOPWORDS = {
    "这个", "那个", "今天", "现在", "什么", "怎么", "为什么", "新闻", "日报", "周报",
    "这个词语", "这里", "上面", "下面",
}

WATCH_ADD_RE = re.compile(
    r"^(?:(?:从)?(?:明天开始|明天|以后|每天|每日)[，,、\s]*){0,2}"
    r"(?:(?:额外|帮我|给我){0,2})"
    r"(?:报道|关注|追踪|留意)(?:一下|下)?[：:\s]*(?P<topic>.+)$"
)
WATCH_DEL_RE = re.compile(
    r"^(?:取消|停止|别再|不用再?)(?:关注|报道|追踪|留意)(?:一下)?[：:\s]*(?P<topic>.+)$"
)
TERM_PREFIX_RE = re.compile(
    r"^(?:请问|问一下|请教|解释一下|解释|什么是|啥是|啥叫)\s*(?P<term>[^\s，。？！,?!]{1,24})[？?。！!]*$"
)
TERM_SUFFIX_RE = re.compile(
    r"^(?P<term>[^\s，。？！,?!]{1,24}?)\s*"
    r"(?:是什么意思|啥意思|什么意思|是什么|怎么理解|有何含义)[？?。！!]*$"
)


def _clean_topic(raw: str) -> str:
    """把用户口语化的关注主题收拾成短名字。"""
    text = str(raw or "").strip()
    text = re.sub(r"^[：:，,、\s]+", "", text)
    text = re.sub(r"[。！!？?；;，,、\s]+$", "", text)
    text = text.strip("“”\"'《》【】[]()（）")
    text = re.sub(r"(?:了|吧|呀|啊|呢)$", "", text)
    lowered = text.lower()
    # 去掉口语后缀，但保留主题本身的字（例如“新能源汽车”不该变成“新能”）。
    while True:
        stripped = False
        lowered = text.lower()
        for word in sorted(WATCH_STOPWORDS, key=len, reverse=True):
            if lowered.endswith(word) and len(text) > len(word) + 1:
                text = text[: -len(word)]
                stripped = True
                break
        if not stripped:
            break
    text = re.sub(r"^(?:一下|下)", "", text)
    return text.strip(" 的")


def _clean_term(raw: str) -> str:
    """从“AI圈内的Protmat”这类说法里取出真正要解释的词。"""
    term = _clean_topic(raw)
    if "的" in term:
        tail = term.rsplit("的", 1)[-1].strip()
        if len(tail) >= 2:
            term = tail
    return term.strip("“”\"'《》【】[]()（）")


def derive_watch_keywords(name: str) -> list[str]:
    """从关注主题名派生用于匹配新闻的关键词。"""
    keywords: list[str] = []
    lowered = name.lower()
    for aliases, words in WATCH_HINTS:
        if any(alias in lowered or lowered in alias for alias in aliases):
            keywords.extend(words)
            break
    parts = [part for part in re.split(r"[、，,；;/]|和", name) if part.strip()]
    cleaned_parts = [_clean_topic(part) for part in parts]
    keywords.extend(part for part in cleaned_parts if len(part) >= 2)
    base = _clean_topic(name)
    if len(base) >= 2:
        keywords.append(base)
    result: list[str] = []
    seen: set[str] = set()
    for word in keywords:
        key = word.lower()
        if key and key not in seen:
            seen.add(key)
            result.append(word)
    return result[:16]


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log_label() -> str:
    return f"[{PLUGIN_NAME}]"


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    try:
        hour_text, minute_text = str(value).strip().split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (TypeError, ValueError):
        return None
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return hour, minute
    return None


def _reached(now: datetime, hhmm: str) -> bool:
    parsed = _parse_hhmm(hhmm)
    if parsed is None:
        return False
    return (now.hour, now.minute) >= parsed


def _weekday_index(value: str) -> int:
    text = str(value or "").strip().lower()
    if text in WEEKDAY_NAMES:
        return WEEKDAY_NAMES.index(text)
    mapping = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
               "周天": 6, "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
               "friday": 4, "saturday": 5, "sunday": 6}
    return mapping.get(text, 6)


def _split_chunks(text: str, limit: int) -> list[str]:
    """按行切分长文本，尽量不截断句子；单行超长则硬切。"""
    text = str(text or "").strip()
    if not text:
        return []
    limit = max(200, int(limit or 1200))
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > limit:
            chunks.append(line[:limit])
            line = line[limit:]
        current = line
    if current:
        chunks.append(current)
    return chunks


def _extract_completion_text(response: Any) -> str:
    try:
        text = response.completion_text
    except AttributeError:
        text = ""
    if not text:
        chain = getattr(response, "result_chain", None)
        if chain is not None and hasattr(chain, "get_plain_text"):
            try:
                text = chain.get_plain_text()
            except Exception:  # noqa: BLE001
                text = ""
    return str(text or "")


def _sanitize_llm_text(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)
    text = text.strip()
    return text


DAILY_ITEM_RE = re.compile(r"^\s*(\d{1,2})\s*[|｜]\s*([^|｜]{1,8})\s*[|｜]\s*(.+?)\s*$")
STOP_BIGRAMS = set(
    "我们 你们 他们 这个 那个 什么 怎么 为什么 可以 已经 就是 还是 因为 所以 但是 如果 "
    "没有 不是 这些 那些 以及 同时 进行 出现 发布 宣布 显示 认为 指出 介绍 记者 央视 "
    "媒体 时间 方面 情况 影响 发生 最新 重要 多个 一些 这样 今年 去年 同比 增长 下降 "
    "上涨 下跌 亿元 万元 美元 中国 美国 公司 市场 经济 人民 报道 表示 目前 今日 今天 "
    "昨天 消息 新闻 热点 相关 工作 问题 一个".split()
)


def _title_key(text: str) -> str:
    text = str(text or "").lower()
    return re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)


def _bigram_set(text: str) -> set[str]:
    key = _title_key(text)
    if not key:
        return set()
    if len(key) == 1:
        return {key}
    return {key[index:index + 2] for index in range(len(key) - 1)}


def _is_duplicate_title(title: str, recent_titles: list[str]) -> bool:
    """判断标题是否和最近推送过的标题重复（字符二元组相似度）。"""
    current = _bigram_set(title)
    if not current:
        return False
    for previous in recent_titles or []:
        other = _bigram_set(previous)
        if not other:
            continue
        shared = current & other
        if not shared:
            continue
        jaccard = len(shared) / len(current | other)
        rare = shared - STOP_BIGRAMS
        ratio = len(shared) / min(len(current), len(other))
        if jaccard >= 0.45 or len(rare) >= 4 or (len(rare) >= 3 and ratio >= 0.60):
            return True
    return False


def _digest_titles(digest: str) -> list[str]:
    """从旧版日报正文里提取编号标题（兼容没有 items 字段的历史档案）。"""
    titles: list[str] = []
    in_section = False
    for raw in str(digest or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        clean = line.replace("【", "").replace("】", "")
        if "要闻" in clean and len(clean) <= 8:
            in_section = True
            continue
        if in_section and clean.startswith(("政策", "一句话", "总结")):
            in_section = False
            continue
        if in_section:
            match = re.match(r"^\d{1,2}[.、]\s*(.+)$", line)
            if match:
                titles.append(match.group(1).strip())
    return titles


def parse_daily_digest(text: str) -> dict[str, Any]:
    """把模型输出解析成结构化要闻 + 总结；解析失败时返回空列表。"""
    items: list[dict[str, str]] = []
    policy_parts: list[str] = []
    one_parts: list[str] = []
    section = ""
    current: dict[str, str] | None = None

    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        stripped = line.lstrip("-·• ").strip()
        clean = stripped.replace("【", "").replace("】", "")

        if clean.startswith(("政策与资金：", "政策与资金:", "政策与资金动向：", "政策与资金动向:")):
            section = "policy"
            rest = re.sub(r"^政策与资金(动向)?[：:]", "", clean).strip()
            if rest:
                policy_parts.append(rest)
            continue
        if clean == "政策与资金" or clean == "政策与资金动向":
            section = "policy"
            continue
        if clean.startswith(("一句话：", "一句话:")):
            section = "one"
            rest = re.sub(r"^一句话[：:]", "", clean).strip()
            if rest:
                one_parts.append(rest)
            continue
        if clean == "一句话":
            section = "one"
            continue
        if "要闻" in clean and len(clean) <= 8:
            section = "items"
            current = None
            continue
        if "总结" in clean and len(clean) <= 8:
            section = "summary"
            continue

        if section == "items":
            match = DAILY_ITEM_RE.match(line)
            if match:
                current = {
                    "category": match.group(2).strip(),
                    "title": match.group(3).strip(" ，。；;"),
                    "fact": "",
                    "impact": "",
                }
                items.append(current)
                continue
            if current is not None:
                for prefix in ("事实：", "事实:", "发生了什么：", "发生了什么:"):
                    if line.startswith(prefix):
                        current["fact"] = line[len(prefix):].strip()
                        break
                else:
                    for prefix in ("影响：", "影响:", "意味着什么：", "意味着什么:",
                                   "跟你有什么关系：", "跟你有什么关系:"):
                        if line.startswith(prefix):
                            current["impact"] = line[len(prefix):].strip()
                            break
            continue

        if section == "policy":
            policy_parts.append(stripped)
            continue
        if section == "one":
            one_parts.append(stripped)
            continue

    return {
        "items": [item for item in items if item.get("title")],
        "policy": "\n".join(part for part in policy_parts if part).strip(),
        "one_liner": "".join(part for part in one_parts if part).strip(),
    }


def build_daily_digest(items: list[dict[str, str]], policy: str, one_liner: str) -> str:
    lines: list[str] = ["【今日要闻】"]
    for index, item in enumerate(items, 1):
        lines.append(f"{index}. [{item.get('category', '')}] {item.get('title', '')}")
        if item.get("fact"):
            lines.append(f"事实：{item['fact']}")
        if item.get("impact"):
            lines.append(f"影响：{item['impact']}")
    if policy:
        lines.extend(["", "【政策与资金动向】", policy])
    if one_liner:
        lines.extend(["", "【一句话】", one_liner])
    return "\n".join(lines).strip()


def build_daily_messages(
    date_text: str,
    items: list[dict[str, str]],
    policy: str,
    one_liner: str,
    watch_blocks: list[str] | None = None,
    weather_text: str = "",
) -> list[str]:
    try:
        day = datetime.strptime(date_text, "%Y-%m-%d")
        label = f"{day.month}月{day.day}日"
    except ValueError:
        label = date_text
    watch_blocks = [block for block in (watch_blocks or []) if str(block or "").strip()]
    header = f"【新闻简报】{label} · {len(items)} 条要闻"
    if watch_blocks:
        header += f" · 关注 {len(watch_blocks)} 个"
    if weather_text:
        header += "\n" + weather_text
    messages = [header]
    for index, item in enumerate(items, 1):
        parts = [f"{index}. {item.get('category', '')}｜{item.get('title', '')}".strip()]
        if item.get("fact"):
            parts.append(f"事实：{item['fact']}")
        if item.get("impact"):
            parts.append(f"影响：{item['impact']}")
        messages.append("\n".join(parts))
    for block in watch_blocks:
        messages.append(block)
    tail_parts: list[str] = []
    if policy:
        tail_parts.append(f"【政策与资金】\n{policy}")
    if one_liner:
        tail_parts.append(f"【一句话】\n{one_liner}")
    if tail_parts:
        messages.append("\n\n".join(tail_parts))
    if messages:
        messages[-1] = messages[-1] + "\n\n" + EXPLAIN_FOOTER
    return messages


def build_weather_text(weather: dict[str, Any] | None) -> str:
    """把天气数据压成一行：城市、天气、温度、冷暖和带伞提醒。"""
    if not weather:
        return ""
    city = str(weather.get("city") or "").strip() or "本地"
    desc = str(weather.get("desc") or "").strip()
    try:
        tmin = int(weather.get("tmin") or 0)
        tmax = int(weather.get("tmax") or 0)
    except (TypeError, ValueError):
        return ""
    rain = int(weather.get("rain") or 0)
    wind = int(weather.get("wind") or 0)
    diff = int(weather.get("diff") or 0)

    head = f"【明天天气】{city} {desc} {tmin}~{tmax}℃"
    tips: list[str] = []
    if diff <= -3:
        tips.append(f"比今天低{abs(diff)}℃")
    elif diff >= 4:
        tips.append(f"比今天高{diff}℃")
    if tmax <= 8:
        tips.append("气温偏冷，穿厚外套或羽绒服")
    elif tmax <= 15:
        tips.append("气温偏冷，加件厚外套")
    elif tmax <= 21:
        tips.append("有点凉，早晚加件外套")
    elif tmax <= 27:
        tips.append("温度舒服，穿件薄的就行")
    else:
        tips.append("偏热，穿轻薄点")
    if tmin <= 3 and tmax >= 15:
        tips.append("早晚温差大，带件可脱的外套")
    if rain >= 60:
        tips.append(f"降雨概率{rain}%，记得带伞")
    elif rain >= 30:
        tips.append(f"降雨概率{rain}%，可能下雨，带把折叠伞")
    elif rain >= 15:
        tips.append(f"降雨概率{rain}%，出门留意天色")
    if wind >= 35:
        tips.append(f"风有点大（{wind}km/h），注意保暖")
    return "；".join([head] + tips) + "。"


def build_watch_user_prompt(
    now: datetime,
    watch_name: str,
    candidates: list[Any],
    recent_titles: list[str] | None = None,
    max_items: int = 3,
) -> str:
    """生成“关注主题”简讯的提示词。"""
    lines: list[str] = []
    for index, item in enumerate(candidates, 1):
        summary = str(getattr(item, "summary", "") or "").strip()
        if len(summary) > 100:
            summary = summary[:100] + "…"
        line = f"[{index}] {getattr(item, 'source', '')}｜{getattr(item, 'title', '')}"
        if summary and summary != getattr(item, "title", ""):
            line += f"｜{summary}"
        lines.append(line)
    recent = [title for title in (recent_titles or []) if title]
    recent_block = "、".join(recent[:25]) if recent else "无"
    return (
        f"日期：{now:%Y-%m-%d}\n关注主题：{watch_name}\n\n"
        "与这个主题可能相关的候选（噪声较多，可能大部分不相关）：\n\n"
        f"{chr(10).join(lines)}\n\n"
        f"最近推过、不要重复的标题：{recent_block}\n\n"
        f"请只保留与“{watch_name}”真正相关的 1-{max_items} 条，"
        "每条格式（QQ 纯文本）：\n"
        "1|关注|标题（不超过 16 字）\n"
        "事实：一句话，不超过 40 字\n"
        "影响：一句话，不超过 40 字\n"
        "如果一条都不相关，只输出：无"
    )


def parse_watch_items(text: str) -> list[dict[str, str]]:
    """解析关注主题的模型输出；没有相关内容时返回空列表。"""
    text = str(text or "").strip()
    if not text or text.startswith("无"):
        return []
    parsed = parse_daily_digest("【要闻】\n" + text)
    return [
        dict(item, category="关注")
        for item in parsed["items"]
        if str(item.get("title") or "").strip()
    ]


def build_watch_blocks(watch_results: list[dict[str, Any]] | None) -> list[str]:
    """把关注主题结果渲染成单条 QQ 消息块。"""
    blocks: list[str] = []
    for result in watch_results or []:
        name = str(result.get("name") or "").strip()
        if not name:
            continue
        lines = [f"【关注 · {name}】"]
        snapshot = str(result.get("snapshot") or "").strip()
        if snapshot:
            lines.append(snapshot)
        items = result.get("items") or []
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            fact = str(item.get("fact") or "").strip()
            impact = str(item.get("impact") or "").strip()
            line = f"· {title}"
            if fact:
                line += f"｜{fact}"
            if impact:
                line += f"｜影响：{impact}"
            lines.append(line)
        if not items:
            lines.append("今天没有相关要闻。")
        blocks.append("\n".join(lines))
    return blocks


def build_watch_digest(watch_results: list[dict[str, Any]] | None) -> str:
    """关注主题写进日报档案的纯文本版本，供周报使用。"""
    parts: list[str] = []
    for result in watch_results or []:
        name = str(result.get("name") or "").strip()
        if not name:
            continue
        block = [f"【关注 · {name}】"]
        snapshot = str(result.get("snapshot") or "").strip()
        if snapshot:
            block.append(snapshot)
        for item in result.get("items") or []:
            block.append(f"{item.get('title', '')}：{item.get('fact', '')}（影响：{item.get('impact', '')}）")
        if not result.get("items"):
            block.append("今天没有相关要闻。")
        parts.append("\n".join(block))
    return "\n\n".join(parts)


def build_term_prompt(term: str, context: str) -> str:
    return (
        f"要解释的词：{term}\n\n"
        "最近的新闻内容（可能包含这个词，也可能没有）：\n"
        f"{(context or '（暂无）')[:3500]}\n\n"
        "请回答：①这个词本身是什么意思；②如果上面材料里有它，说明它在这条新闻里指什么、"
        "为什么值得注意；③给一个普通人听得懂的说法。总字数不超过 200 字。"
    )


def build_daily_user_prompt(
    now: datetime,
    materials: str,
    recent_titles: list[str] | None = None,
    item_count: int = 5,
) -> str:
    weekday = "一二三四五六日"[now.weekday()]
    recent = [title for title in (recent_titles or []) if title]
    recent_block = "、".join(recent[:40]) if recent else "无"
    count = max(3, min(6, int(item_count)))
    return (
        f"日期：{now:%Y-%m-%d} 星期{weekday}\n"
        f"下面是今天从国内公开新闻源抓到的候选条目（可能重复、嘈杂、含少量无关内容）：\n\n"
        f"{materials}\n\n"
        f"最近 5 天已经推送过的要闻（不要重复；同一件事今天如果没有重大新进展就直接跳过，"
        f"有新进展就只写新增变化，不要复述旧内容）：\n{recent_block}\n\n"
        f"请输出《今日新闻简报》，只保留最重要的 {count} 条（最多 6 条），总字数 600-900 字，"
        "格式严格如下（QQ 纯文本，不要多余符号）：\n\n"
        "【要闻】\n"
        "1|政策|标题（不超过 16 字）\n"
        "事实：一句话，不超过 45 字，讲清谁做了什么、关键数字\n"
        "影响：一句话，不超过 45 字，对普通人的钱包、工作或生活意味着什么\n"
        "2|经济|标题\n"
        "事实：…\n"
        "影响：…\n"
        "（分类只能从 政策、经济、民生、国际、科技、社会 里选一个；按重要程度排序，"
        "政策/经济/民生优先，娱乐体育最多 1 条）\n\n"
        "【总结】\n"
        "政策与资金：2-3 句，国家在鼓励或限制什么、钱在往哪个方向走；"
        "没有明显信号就写“今天没有明显信号”。\n"
        "一句话：给普通人的一条实在提醒。\n\n"
        "注意：同一事件出现在多个来源时合并成一条；不要写“根据材料”“综上所述”这类词；"
        "不要输出 Markdown 或表情。"
    )


def build_weekly_user_prompt(
    week_start: datetime,
    week_end: datetime,
    entries: list[tuple[str, str]],
) -> str:
    parts: list[str] = []
    for date_text, digest in entries:
        clipped = digest.strip()
        if len(clipped) > 850:
            clipped = clipped[:850] + "…"
        parts.append(f"[{date_text}]\n{clipped}")
    materials = "\n\n".join(parts)
    return (
        f"时间范围：{week_start:%Y-%m-%d} 至 {week_end:%Y-%m-%d}（覆盖 {len(entries)} 天，"
        f"部分日期可能没有日报，缺失就说明覆盖不全，不要编造）。\n\n"
        f"这一周的每日简报如下：\n\n{materials}\n\n"
        "请输出《本周新闻周报》，总字数 1300-1800 字，格式严格如下（QQ 纯文本）：\n\n"
        "【本周主线】\n"
        "2-3 句：这一周最核心的一条线是什么，为什么是它。\n\n"
        "【趋势判断】\n"
        "3-4 条，每条一行：趋势 + 一句依据（政策、资金、产业或社会情绪）。\n\n"
        "【政策与资金】\n"
        "国家在鼓励什么、限制什么；资金明显流向哪些行业或领域；普通人的体感是什么。"
        "3-5 句，能带具体数字就带。\n\n"
        "【普通人该做】\n"
        "2-3 条，具体、能落地的事，不喊口号。\n\n"
        "【普通人别做】\n"
        "2-3 条，具体的风险提醒。\n\n"
        "【下周关注】\n"
        "2-3 件事，说明为什么值得关注。\n\n"
        "注意：只基于上面的日报内容，不要补新闻；不要出现“根据材料”“综上所述”这类词。"
    )


@register(
    PLUGIN_NAME,
    "DSH Agent",
    "每日新闻简报 + 每周新闻周报 + 自定义关注主题 + 明天天气：每晚 23:00 抓取国内公开新闻源，"
    "按分类生成简短要闻、每条单独一条消息、最后汇总政策与资金，并自动跳过最近几天重复的新闻；"
    "日报开头附所在城市明天的天气和穿衣、带伞建议；"
    "可以在聊天里直接说“帮我关注 AI 科技圈”，机器人自己加关注；遇到生僻词直接问“XX 是什么意思”。"
    "命令：/日报 /天气 /周报 /新闻源 /关注 /关注列表 /取消关注 /新闻设置。",
    "1.3.0",
)
class DailyNewsPlugin(Star):
    """每日新闻简报插件。"""

    def __init__(self, context: Context, config: AstrBotConfig | dict | None = None) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        root = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.data_root = root
        self.daily_dir = root / "daily"
        self.weekly_dir = root / "weekly"
        self.state_path = root / "state.json"
        self.watch_path = root / "watches.json"
        self._watches: list[dict[str, Any]] = []
        self._weather_cache: dict[str, Any] | None = None
        self._weather_cache_date = ""
        self._tick_task: asyncio.Task | None = None
        self._tick_stop = False
        self._daily_running = False
        self._weekly_running = False
        self._state: dict[str, Any] = {}
        self._last_fail_at = 0.0
        self._last_fail_msg = ""
        self._last_notice_at = 0.0

    # ------------------------------------------------------------ 生命周期

    async def initialize(self) -> None:
        self.daily_dir.mkdir(parents=True, exist_ok=True)
        self.weekly_dir.mkdir(parents=True, exist_ok=True)
        self._state = self._load_json(self.state_path) or {}
        self._load_watches()
        self.logger.info(
            "%s 启动：日报=%s｜周报=%s(%s)｜源=%s｜关注=%s｜数据=%s",
            _log_label(),
            self._conf_str("daily_time", "23:00"),
            self._conf_str("weekly_time", "23:10"),
            self._conf_str("weekly_day", "sun"),
            ",".join(self._enabled_sources()) or "无",
            f"{len(self._active_watches())} 个",
            self.data_root,
        )
        self._start_tick_task()

    async def terminate(self) -> None:
        await self._stop_tick_task()
        self.logger.info("%s 已停止定时循环。", _log_label())

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self) -> None:
        self._start_tick_task()

    def _start_tick_task(self) -> None:
        if self._tick_task is not None and not self._tick_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tick_stop = False
        self._tick_task = loop.create_task(self._tick_loop(), name="daily_news_tick")

    async def _stop_tick_task(self) -> None:
        self._tick_stop = True
        task = self._tick_task
        self._tick_task = None
        if task is None:
            return
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task

    async def _tick_loop(self) -> None:
        tick = self._conf_int("tick_seconds", 30, 10, 600)
        self.logger.info("%s 定时循环启动，tick=%ss", _log_label(), tick)
        while not self._tick_stop:
            try:
                await self._tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - 单个 tick 出错不能杀死循环
                self.logger.exception("%s 定时 tick 异常", _log_label())
            try:
                await asyncio.sleep(tick)
            except asyncio.CancelledError:
                raise

    async def _tick_once(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        date_text = now.strftime("%Y-%m-%d")

        if (
            self._conf_bool("enable_daily", True)
            and not self._daily_running
            and _reached(now, self._conf_str("daily_time", "23:00"))
            and not self._daily_done(date_text)
            and self._fail_cooldown_ok()
        ):
            await self._scheduled_daily(date_text)

        if (
            self._conf_bool("enable_weekly", True)
            and not self._weekly_running
            and now.weekday() == _weekday_index(self._conf_str("weekly_day", "sun"))
            and _reached(now, self._conf_str("weekly_time", "23:10"))
            and not self._weekly_done(now)
            and self._fail_cooldown_ok()
        ):
            await self._scheduled_weekly(now)

    # ------------------------------------------------------------ 配置

    def _conf(self, key: str, default: Any) -> Any:
        try:
            value = self.config.get(key, default)
        except Exception:  # noqa: BLE001
            value = default
        return default if value is None else value

    def _conf_str(self, key: str, default: str) -> str:
        return str(self._conf(key, default)).strip() or default

    def _conf_bool(self, key: str, default: bool) -> bool:
        value = self._conf(key, default)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "是")
        return bool(value)

    def _conf_int(self, key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(self._conf(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def _owner_qq(self) -> str:
        return str(self._conf("owner_qq", DEFAULT_OWNER_QQ)).strip()

    async def _tomorrow_weather(self, force: bool = False) -> dict[str, Any] | None:
        """取明天的天气：有缓存先用，失败返回 None，不阻塞日报。"""
        if not self._conf_bool("enable_weather", True):
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        if not force and self._weather_cache and self._weather_cache_date == today:
            return self._weather_cache
        timeout = self._conf_int("weather_timeout", 12, 3, 30)
        city = str(self._conf("weather_city", "") or "").strip()
        try:
            if not city:
                loc = await detect_location(timeout=min(8, timeout))
                city = str(loc.get("city") or "").strip()
            weather = await fetch_weather(city=city, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - 天气失败不影响日报
            self.logger.warning("%s 天气获取失败：%s", _log_label(), exc)
            return None
        if weather:
            if city and not str(weather.get("city") or "").strip():
                weather["city"] = city
            self._weather_cache = weather
            self._weather_cache_date = today
            self.logger.info(
                "%s 明天天气：%s %s %s~%s℃ 降雨%s%%",
                _log_label(),
                weather.get("city"),
                weather.get("desc"),
                weather.get("tmin"),
                weather.get("tmax"),
                weather.get("rain"),
            )
        return weather

    def _enabled_sources(self) -> list[str]:
        result: list[str] = []
        for source in SOURCE_ORDER:
            if self._conf_bool(f"enable_{source}", True):
                result.append(source)
        return result

    def _per_source_limits(self) -> dict[str, int]:
        return {
            "chinanews": self._conf_int("chinanews_max", 20, 3, 30),
            "sina": self._conf_int("sina_max", 20, 3, 30),
            "qqhot": self._conf_int("qqhot_max", 15, 3, 30),
            "wscn": self._conf_int("wscn_max", 12, 3, 30),
            "baidu": self._conf_int("baidu_max", 8, 3, 30),
        }

    def _umos_to_try(self) -> list[str]:
        umos: list[str] = []
        working = str(self._state.get("working_umo") or "").strip()
        if working:
            umos.append(working)
        configured = self._conf("target_umos", [])
        if isinstance(configured, (list, tuple)):
            for value in configured:
                text = str(value or "").strip()
                if text and text not in umos:
                    umos.append(text)
        qq = self._owner_qq()
        if qq:
            for platform in ("aiocqhttp", "default"):
                umo = f"{platform}:FriendMessage:{qq}"
                if umo not in umos:
                    umos.append(umo)
        return umos

    def _remember_umo(self, umo: str) -> None:
        umo = str(umo or "").strip()
        if not umo or "FriendMessage" not in umo:
            return
        if self._state.get("working_umo") == umo:
            return
        self._state["working_umo"] = umo
        self._save_state()

    def _remember_umo_from_event(self, event: AstrMessageEvent) -> None:
        try:
            umo = str(event.unified_msg_origin or "").strip()
        except Exception:  # noqa: BLE001
            return
        if "FriendMessage" in umo:
            self._remember_umo(umo)

    # ------------------------------------------------------------ 关注主题

    def _load_watches(self) -> None:
        data = self._load_json(self.watch_path) or {}
        topics = data.get("topics") if isinstance(data.get("topics"), list) else []
        self._watches = [
            dict(topic)
            for topic in topics
            if isinstance(topic, dict) and str(topic.get("name") or "").strip()
        ]

    def _save_watches(self) -> None:
        with contextlib.suppress(Exception):
            self._write_json(
                self.watch_path,
                {"updated_at": _now_iso(), "topics": self._watches},
            )

    def _active_watches(self) -> list[dict[str, Any]]:
        return [topic for topic in self._watches if topic.get("active", True)]

    def _find_watch(self, name: str) -> dict[str, Any] | None:
        target = _clean_topic(name).lower()
        if not target:
            return None
        for topic in self._watches:
            topic_name = str(topic.get("name") or "")
            if topic_name.lower() == target or target in topic_name.lower():
                return topic
        return None

    def _add_watch(self, raw_name: str) -> tuple[dict[str, Any] | None, str]:
        name = _clean_topic(raw_name)[:20]
        if len(name) < 2:
            return None, "主题太短"
        if name in WATCH_BANNED_TOPICS:
            return None, "这个不算关注主题"
        existed = self._find_watch(name)
        if existed is not None:
            return existed, "已关注"
        topic = {
            "name": name,
            "keywords": derive_watch_keywords(name),
            "created_at": _now_iso(),
            "active": True,
        }
        self._watches.append(topic)
        self._save_watches()
        self.logger.info("%s 新增关注主题：%s｜关键词=%s", _log_label(), name, "、".join(topic["keywords"][:10]))
        return topic, ""

    def _remove_watch(self, arg: str) -> str:
        text = _clean_topic(arg)
        if not text:
            return ""
        active = self._active_watches()
        if text.isdigit():
            index = int(text)
            if 1 <= index <= len(active):
                topic = active[index - 1]
                self._watches.remove(topic)
                self._save_watches()
                return str(topic.get("name") or "")
        for topic in list(self._watches):
            if text.lower() in str(topic.get("name") or "").lower():
                self._watches.remove(topic)
                self._save_watches()
                return str(topic.get("name") or "")
        return ""

    def _parse_topics(self, arg: str) -> list[str]:
        """把“AI科技圈、新能源汽车”这类参数拆成多个主题。"""
        parts = [part for part in re.split(r"[、，,；;/]|和", str(arg or "")) if part.strip()]
        topics: list[str] = []
        for part in parts:
            cleaned = _clean_topic(part)
            if len(cleaned) >= 2 and cleaned not in topics:
                topics.append(cleaned)
        return topics

    def _watch_keywords(self, topic: dict[str, Any]) -> list[str]:
        keys = [str(key).strip() for key in (topic.get("keywords") or []) if str(key).strip()]
        if not keys:
            keys = derive_watch_keywords(str(topic.get("name") or ""))
        return keys

    def _watch_matches(self, topic: dict[str, Any], item: Any) -> bool:
        text = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}".lower()
        return any(key.lower() in text for key in self._watch_keywords(topic))

    def _is_stock_topic(self, topic: dict[str, Any]) -> bool:
        name = str(topic.get("name") or "").lower()
        if any(word in name for word in ("股", "大盘", "基金", "证券")):
            return True
        return any(str(key).lower() in STOCK_KEYWORDS for key in self._watch_keywords(topic))

    async def _fetch_watch_pool(self) -> tuple[list[Any], dict[str, dict[str, Any]]]:
        extras = ["ithome", "qbitai", "wscn_astock"]
        try:
            items, statuses = await fetch_all(
                extras,
                per_source_limit={"ithome": 30, "qbitai": 20, "wscn_astock": 12},
                timeout=float(self._conf_int("source_timeout", 18, 5, 60)),
            )
            self.logger.info(
                "%s 关注源抓取：%s",
                _log_label(),
                ",".join(
                    f"{SOURCE_LABELS.get(name, name)}{status.get('count', 0)}"
                    for name, status in statuses.items()
                ),
            )
            return items, statuses
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("%s 关注源抓取失败：%s", _log_label(), exc)
            return [], {}

    async def _market_snapshot_text(self) -> str:
        try:
            quotes = await fetch_market_snapshot(timeout=8.0)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("%s 行情快照失败：%s", _log_label(), exc)
            return ""
        parts = [
            f"{quote['name']} {quote['price']:.2f}（{quote['pct']:+.2f}%）"
            for quote in quotes
            if isinstance(quote, dict) and "name" in quote
        ]
        return "｜".join(parts)

    def _general_item_titles(self, items: list[dict[str, str]]) -> list[str]:
        return [str(item.get("title") or "").strip() for item in items if str(item.get("title") or "").strip()]

    async def _build_watch_results(
        self,
        recent_titles: list[str],
        base_items: list[Any],
        general_titles: list[str],
    ) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
        """为每个关注主题抓补充源、匹配候选、调用模型生成 1-3 条简讯。"""
        active = self._active_watches()
        if not active or not self._conf_bool("enable_watch", True):
            return [], {}
        pool, statuses = await self._fetch_watch_pool()
        all_items = dedupe_items(list(base_items) + list(pool))
        max_items = self._conf_int("watch_items", 3, 1, 5)
        candidates_cap = self._conf_int("watch_candidates", 8, 3, 20)
        timeout = float(self._conf_int("watch_llm_timeout", 90, 30, 300))
        now = datetime.now()
        results: list[dict[str, Any]] = []
        for topic in active:
            name = str(topic.get("name") or "")
            matched: list[Any] = []
            for item in all_items:
                if not self._watch_matches(topic, item):
                    continue
                if _is_duplicate_title(item.title, recent_titles):
                    continue
                if general_titles and _is_duplicate_title(item.title, general_titles):
                    continue
                matched.append(item)
            matched = matched[:candidates_cap]
            snapshot = ""
            if self._is_stock_topic(topic):
                snapshot = await self._market_snapshot_text()
            items: list[dict[str, str]] = []
            error = ""
            if matched:
                try:
                    raw = await self._ask_llm(
                        WATCH_SYSTEM,
                        build_watch_user_prompt(now, name, matched, recent_titles, max_items=max_items),
                        timeout=timeout,
                    )
                    items = parse_watch_items(raw)
                    items = [
                        item
                        for item in items
                        if not _is_duplicate_title(str(item.get("title") or ""), recent_titles + general_titles)
                    ][:max_items]
                except Exception as exc:  # noqa: BLE001
                    error = f"{type(exc).__name__}: {exc}"
                    self.logger.warning("%s 关注主题「%s」生成失败：%s", _log_label(), name, error)
            results.append(
                {
                    "name": name,
                    "keywords": self._watch_keywords(topic)[:10],
                    "candidates": len(matched),
                    "items": items,
                    "snapshot": snapshot,
                    "error": error,
                }
            )
            self.logger.info(
                "%s 关注主题「%s」：候选 %s 条，产出 %s 条%s",
                _log_label(),
                name,
                len(matched),
                len(items),
                f"，行情={snapshot}" if snapshot else "",
            )
        return results, statuses

    # ------------------------------------------------------------ 生僻词解释

    def _recent_digest_context(self, days: int = 3) -> str:
        parts: list[str] = []
        for offset in range(0, days):
            date_text = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            data = self._load_json(self._daily_path(date_text)) or {}
            digest = str(data.get("digest") or "").strip()
            if digest:
                parts.append(f"[{date_text}]\n{digest}")
        return "\n\n".join(parts)

    async def _explain_term(self, term: str) -> str:
        context = self._recent_digest_context(3)
        return await self._ask_llm(
            TERM_SYSTEM,
            build_term_prompt(term, context),
            timeout=float(self._conf_int("term_timeout", 60, 20, 180)),
        )

    # ------------------------------------------------------------ 状态与文件

    def _load_json(self, path: Path) -> dict[str, Any] | None:
        try:
            if not path.is_file():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("%s 读取 %s 失败：%s", _log_label(), path, exc)
            return None

    def _write_json(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _save_state(self) -> None:
        with contextlib.suppress(Exception):
            self._write_json(self.state_path, self._state)

    def _daily_path(self, date_text: str) -> Path:
        return self.daily_dir / f"{date_text}.json"

    def _weekly_path(self, week_end: datetime) -> Path:
        return self.weekly_dir / f"{week_end:%Y-%m-%d}.json"

    def _daily_done(self, date_text: str) -> bool:
        data = self._load_json(self._daily_path(date_text)) or {}
        return bool(data.get("digest")) and bool(data.get("pushed_at"))

    def _weekly_done(self, now: datetime) -> bool:
        data = self._load_json(self._weekly_path(now)) or {}
        return bool(data.get("digest")) and bool(data.get("pushed_at"))

    def _recent_titles(self, days: int | None = None) -> list[str]:
        """收集最近几天已推送要闻的标题，用于跨天去重。"""
        window = days or self._conf_int("recent_days", 5, 2, 14)
        titles: list[str] = []
        for offset in range(1, window + 1):
            date_text = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            data = self._load_json(self._daily_path(date_text)) or {}
            file_titles = [
                str(item.get("title") or "").strip()
                for item in (data.get("items") or [])
                if isinstance(item, dict)
            ]
            file_titles = [title for title in file_titles if title]
            if file_titles:
                titles.extend(file_titles)
            else:
                titles.extend(_digest_titles(str(data.get("digest") or "")))
        return titles

    def _fail_cooldown_ok(self) -> bool:
        return time.monotonic() - self._last_fail_at >= 300

    def _mark_fail(self, kind: str, exc: BaseException) -> None:
        self._last_fail_at = time.monotonic()
        self._last_fail_msg = f"{type(exc).__name__}: {exc}"
        self.logger.warning("%s %s失败：%s（5 分钟后重试）", _log_label(), kind, self._last_fail_msg)

    # ------------------------------------------------------------ 抓取与生成

    async def _fetch_and_build_materials(
        self,
    ) -> tuple[str, int, dict[str, dict[str, Any]], list[str], list[str], list[Any]]:
        sources = self._enabled_sources()
        if not sources:
            raise RuntimeError("没有启用任何新闻源")
        items, statuses = await fetch_all(
            sources,
            per_source_limit=self._per_source_limits(),
            timeout=float(self._conf_int("source_timeout", 18, 5, 60)),
        )
        total_cap = self._conf_int("max_total_items", 70, 10, 200)
        items = items[:total_cap]

        recent_titles = self._recent_titles()
        kept: list[Any] = []
        skipped: list[str] = []
        for item in items:
            if _is_duplicate_title(item.title, recent_titles):
                skipped.append(item.title)
            else:
                kept.append(item)
        items = kept

        materials, used = format_materials(
            items,
            max_chars=self._conf_int("max_materials_chars", 7000, 2000, 20000),
        )
        ok_sources = [name for name, status in statuses.items() if status.get("ok")]
        self.logger.info(
            "%s 抓取完成：%s 条候选（源：%s），跳过近日重复 %s 条，材料 %s 条/%s 字",
            _log_label(),
            len(items) + len(skipped),
            ",".join(f"{SOURCE_LABELS.get(name, name)}{status.get('count', 0)}" for name, status in statuses.items()),
            len(skipped),
            used,
            len(materials),
        )
        if skipped:
            self.logger.info("%s 近日重复候选：%s", _log_label(), "；".join(skipped[:20]))
        if used == 0 or not ok_sources:
            detail = "；".join(
                f"{SOURCE_LABELS.get(name, name)}：{status.get('error')}"
                for name, status in statuses.items()
                if not status.get("ok")
            )
            raise RuntimeError(f"所有新闻源都没抓到可用内容（{detail or '未知原因'}）")
        return materials, used, statuses, skipped, recent_titles, items

    async def _get_provider(self) -> Any:
        umo = str(self._state.get("working_umo") or "").strip()
        if not umo:
            qq = self._owner_qq()
            umo = f"aiocqhttp:FriendMessage:{qq}" if qq else ""
        provider_id = self._conf_str("provider_id", "")
        if provider_id:
            getter = getattr(self.context, "get_provider_by_id", None)
            if callable(getter):
                try:
                    provider = getter(provider_id)
                    if provider is not None:
                        return provider
                except Exception:  # noqa: BLE001
                    self.logger.warning("%s 按 provider_id=%s 取模型失败", _log_label(), provider_id)
        async_getter = getattr(self.context, "get_using_provider_async", None)
        if callable(async_getter):
            try:
                provider = await async_getter(umo)
                if provider is not None:
                    return provider
            except Exception:  # noqa: BLE001
                self.logger.warning("%s get_using_provider_async 失败", _log_label(), exc_info=True)
        legacy_getter = getattr(self.context, "get_using_provider", None)
        if callable(legacy_getter):
            try:
                provider = legacy_getter(umo)
                if provider is not None:
                    return provider
            except Exception:  # noqa: BLE001
                self.logger.warning("%s get_using_provider 失败", _log_label(), exc_info=True)
        return None

    async def _ask_llm(self, system_prompt: str, user_prompt: str, timeout: float) -> str:
        provider = await self._get_provider()
        if provider is None:
            raise RuntimeError("没有可用的对话模型（检查 AstrBot 模型提供商配置）")
        try:
            response = await asyncio.wait_for(
                provider.text_chat(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    contexts=[],
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"模型调用超时（{timeout:.0f}s）") from exc
        text = _sanitize_llm_text(_extract_completion_text(response))
        if not text:
            raise RuntimeError("模型返回了空内容")
        return text

    async def _ensure_daily(self, force: bool = False) -> dict[str, Any]:
        now = datetime.now()
        date_text = now.strftime("%Y-%m-%d")
        path = self._daily_path(date_text)
        data = self._load_json(path) or {"date": date_text}
        if data.get("digest") and not force:
            return data
        materials, used, statuses, skipped_titles, recent_titles, base_items = await self._fetch_and_build_materials()
        raw_digest = await self._ask_llm(
            DAILY_SYSTEM,
            build_daily_user_prompt(
                now,
                materials,
                recent_titles,
                item_count=self._conf_int("daily_items", 5, 3, 6),
            ),
            timeout=float(self._conf_int("llm_timeout", 180, 30, 600)),
        )
        parsed = parse_daily_digest(raw_digest)
        items = parsed["items"]
        post_skipped: list[str] = []
        if items:
            kept_items: list[dict[str, str]] = []
            for item in items:
                if _is_duplicate_title(str(item.get("title") or ""), recent_titles):
                    post_skipped.append(str(item.get("title") or ""))
                else:
                    kept_items.append(item)
            items = kept_items
            if post_skipped:
                self.logger.info("%s 生成后再次过滤近日重复：%s", _log_label(), "；".join(post_skipped))
        max_items = self._conf_int("daily_items", 5, 3, 6)
        if len(items) > max_items:
            items = items[:max_items]
        policy = parsed["policy"]
        one_liner = parsed["one_liner"]
        watch_results, watch_statuses = await self._build_watch_results(
            recent_titles,
            base_items,
            self._general_item_titles(items),
        )
        weather = await self._tomorrow_weather()
        digest = build_daily_digest(items, policy, one_liner) if items else raw_digest
        watch_digest = build_watch_digest(watch_results)
        if watch_digest:
            digest = f"{digest}\n\n{watch_digest}"
        data.update(
            {
                "date": date_text,
                "generated_at": _now_iso(),
                "digest": digest,
                "items": items,
                "summary": {"policy": policy, "one_liner": one_liner},
                "items_count": len(items),
                "recent_titles": len(recent_titles),
                "skipped_recent": (skipped_titles + post_skipped)[:40],
                "materials": materials[:20000],
                "sources": statuses,
                "watches": watch_results,
                "watch_sources": watch_statuses,
                "weather": weather,
            }
        )
        self._write_json(path, data)
        self.logger.info(
            "%s 日报生成完成：%s 字｜%s 条要闻｜跳过近日重复 %s 条",
            _log_label(),
            len(digest),
            len(items),
            len(skipped_titles) + len(post_skipped),
        )
        self._prune_old_files()
        return data

    async def _ensure_weekly(self, force: bool = False) -> dict[str, Any]:
        now = datetime.now()
        week_end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = week_end - timedelta(days=6)
        path = self._weekly_path(week_end)
        data = self._load_json(path) or {
            "week_start": week_start.strftime("%Y-%m-%d"),
            "week_end": week_end.strftime("%Y-%m-%d"),
        }
        if data.get("digest") and not force:
            return data
        entries: list[tuple[str, str]] = []
        for offset in range(7):
            day = week_start + timedelta(days=offset)
            day_text = day.strftime("%Y-%m-%d")
            item = self._load_json(self._daily_path(day_text)) or {}
            digest = str(item.get("digest") or "").strip()
            if digest:
                entries.append((day_text, digest))
        if not entries:
            raise RuntimeError("最近 7 天还没有日报数据，等日报先跑几天再来")
        digest = await self._ask_llm(
            WEEKLY_SYSTEM,
            build_weekly_user_prompt(week_start, week_end, entries),
            timeout=float(self._conf_int("weekly_llm_timeout", 240, 60, 900)),
        )
        data.update(
            {
                "generated_at": _now_iso(),
                "digest": digest,
                "days": len(entries),
                "week_start": week_start.strftime("%Y-%m-%d"),
                "week_end": week_end.strftime("%Y-%m-%d"),
            }
        )
        self._write_json(path, data)
        self.logger.info("%s 周报生成完成：%s 字｜覆盖 %s 天", _log_label(), len(digest), len(entries))
        return data

    def _prune_old_files(self) -> None:
        keep_days = self._conf_int("keep_daily_days", 45, 7, 365)
        cutoff = datetime.now() - timedelta(days=keep_days)
        for path in self.daily_dir.glob("*.json"):
            try:
                day = datetime.strptime(path.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if day < cutoff:
                with contextlib.suppress(OSError):
                    path.unlink()
        for path in self.weekly_dir.glob("*.json"):
            try:
                day = datetime.strptime(path.stem, "%Y-%m-%d")
            except ValueError:
                continue
            if day < cutoff:
                with contextlib.suppress(OSError):
                    path.unlink()

    # ------------------------------------------------------------ 推送

    async def _deliver(self, text: str) -> tuple[bool, str]:
        return await self._deliver_messages([text])

    async def _deliver_messages(self, messages: list[str]) -> tuple[bool, str]:
        limit = self._conf_int("push_chunk_chars", 1200, 400, 4000)
        prepared: list[str] = []
        for message in messages or []:
            prepared.extend(_split_chunks(message, limit))
        if not prepared:
            return False, ""
        errors: list[str] = []
        for umo in self._umos_to_try():
            ok = True
            for chunk in prepared:
                try:
                    sent = await self.context.send_message(umo, MessageChain(chain=[Plain(chunk)]))
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    errors.append(f"{umo}: {type(exc).__name__}: {exc}")
                    break
                if sent is False:
                    ok = False
                    errors.append(f"{umo}: 没找到对应平台")
                    break
                await asyncio.sleep(0.4)
            if ok:
                self._remember_umo(umo)
                self.logger.info("%s 已推送 %s 条消息到 %s", _log_label(), len(prepared), umo)
                return True, umo
        self.logger.warning("%s 推送失败：%s", _log_label(), "；".join(errors[-3:]))
        return False, ""

    async def _notify_owner(self, text: str) -> None:
        if time.monotonic() - self._last_notice_at < 1800:
            return
        self._last_notice_at = time.monotonic()
        with contextlib.suppress(Exception):
            await self._deliver(f"【新闻简报】{text}")

    async def _deliver_daily(self, data: dict[str, Any]) -> None:
        digest = str(data.get("digest") or "").strip()
        items = data.get("items") or []
        summary = data.get("summary") or {}
        if not digest and not items:
            raise RuntimeError("日报内容为空")
        weather = data.get("weather")
        if not weather:
            weather = await self._tomorrow_weather()
            if weather:
                data["weather"] = weather
        weather_text = build_weather_text(weather)
        if self._conf_bool("split_items", True) and items:
            messages = build_daily_messages(
                str(data.get("date") or datetime.now().strftime("%Y-%m-%d")),
                items,
                str(summary.get("policy") or "").strip(),
                str(summary.get("one_liner") or "").strip(),
                watch_blocks=build_watch_blocks(data.get("watches") or []),
                weather_text=weather_text,
            )
        else:
            prefix = f"{weather_text}\n\n" if weather_text else ""
            messages = [f"{prefix}{digest}\n\n{EXPLAIN_FOOTER}"]
        ok, umo = await self._deliver_messages(messages)
        if not ok:
            raise RuntimeError("日报推送到 QQ 失败")
        data["pushed_at"] = _now_iso()
        data["pushed_umo"] = umo
        data["messages_count"] = len(messages)
        self._write_json(self._daily_path(str(data.get("date") or datetime.now().strftime("%Y-%m-%d"))), data)

    async def _deliver_weekly(self, data: dict[str, Any], now: datetime) -> None:
        digest = str(data.get("digest") or "").strip()
        if not digest:
            raise RuntimeError("周报内容为空")
        ok, umo = await self._deliver(f"{digest}\n\n{EXPLAIN_FOOTER}")
        if not ok:
            raise RuntimeError("周报推送到 QQ 失败")
        data["pushed_at"] = _now_iso()
        data["pushed_umo"] = umo
        self._write_json(self._weekly_path(now), data)

    async def _scheduled_daily(self, date_text: str) -> None:
        self._daily_running = True
        try:
            data = await self._ensure_daily()
            await self._deliver_daily(data)
            self.logger.info("%s 定时日报已推送（%s）", _log_label(), date_text)
        except Exception as exc:  # noqa: BLE001
            self._mark_fail("定时日报", exc)
            await self._notify_owner(f"今晚日报生成/推送失败：{exc}。稍后会自动重试。")
        finally:
            self._daily_running = False

    async def _scheduled_weekly(self, now: datetime) -> None:
        self._weekly_running = True
        try:
            data = await self._ensure_weekly()
            await self._deliver_weekly(data, now)
            self.logger.info("%s 定时周报已推送（至 %s）", _log_label(), now.strftime("%Y-%m-%d"))
        except Exception as exc:  # noqa: BLE001
            self._mark_fail("定时周报", exc)
            await self._notify_owner(f"本周周报生成/推送失败：{exc}。稍后会自动重试。")
        finally:
            self._weekly_running = False

    # ------------------------------------------------------------ 命令

    def _is_owner(self, event: AstrMessageEvent) -> bool:
        owner = self._owner_qq()
        if not owner:
            return False
        try:
            sender = str(event.get_sender_id())
        except Exception:  # noqa: BLE001
            return False
        return sender == owner

    @filter.command("日报", alias={"今日新闻", "新闻日报"})
    async def cmd_daily(self, event: AstrMessageEvent):
        """生成并发送今天的新闻日报。"""
        if not self._is_owner(event):
            yield event.plain_result(OWNER_ONLY_TEXT)
            return
        self._remember_umo_from_event(event)
        yield event.plain_result("收到，正在抓取今天的新闻并生成简报，大约需要一分钟…")
        try:
            data = await self._ensure_daily(force=False)
            await self._deliver_daily(data)
        except Exception as exc:  # noqa: BLE001
            self._mark_fail("手动日报", exc)
            yield event.plain_result(f"生成失败：{exc}")
            return
        yield event.plain_result("今天的日报已经私聊发给你了。")

    @filter.command("天气", alias={"明天天气"})
    async def cmd_weather(self, event: AstrMessageEvent):
        """查看明天天气和穿衣、带伞建议。"""
        if not self._is_owner(event):
            yield event.plain_result(OWNER_ONLY_TEXT)
            return
        self._remember_umo_from_event(event)
        weather = await self._tomorrow_weather(force=True)
        text = build_weather_text(weather)
        yield event.plain_result(text or "天气没取到，等一下再试试。")

    @filter.command("周报", alias={"本周新闻", "新闻周报"})
    async def cmd_weekly(self, event: AstrMessageEvent):
        """生成并发送本周新闻周报。"""
        if not self._is_owner(event):
            yield event.plain_result(OWNER_ONLY_TEXT)
            return
        self._remember_umo_from_event(event)
        yield event.plain_result("收到，正在汇总最近 7 天的新闻并生成周报，大约需要一两分钟…")
        try:
            data = await self._ensure_weekly(force=False)
            await self._deliver_weekly(data, datetime.now())
        except Exception as exc:  # noqa: BLE001
            self._mark_fail("手动周报", exc)
            yield event.plain_result(f"生成失败：{exc}")
            return
        yield event.plain_result("本周的周报已经私聊发给你了。")

    @filter.command("新闻源", alias={"新闻源检测"})
    async def cmd_sources(self, event: AstrMessageEvent):
        """检测各新闻源是否可用（不调用模型）。"""
        if not self._is_owner(event):
            return event.plain_result(OWNER_ONLY_TEXT)
        enabled = self._enabled_sources()
        if not enabled:
            return event.plain_result("没有启用任何新闻源，请检查插件配置。")
        _, statuses = await fetch_all(
            enabled,
            per_source_limit=self._per_source_limits(),
            timeout=float(self._conf_int("source_timeout", 18, 5, 60)),
        )
        lines = ["新闻源检测："]
        for name in enabled:
            status = statuses.get(name, {})
            label = SOURCE_LABELS.get(name, name)
            if status.get("ok"):
                lines.append(f"· {label}：{status.get('count', 0)} 条（{status.get('elapsed', 0)}s）")
            else:
                lines.append(f"· {label}：失败（{status.get('error', '未知原因')}）")
        return event.plain_result("\n".join(lines))

    # ------------------------------------------------------------ 关注主题命令

    def _watch_list_text(self) -> str:
        topics = self._active_watches()
        if not topics:
            return (
                "现在还没有关注主题。\n"
                "直接跟我说“帮我关注 AI 科技圈”“关注股市和新能源汽车”就行，不用改配置。"
            )
        lines = [f"当前关注 {len(topics)} 个："]
        for index, topic in enumerate(topics, 1):
            keys = "、".join(self._watch_keywords(topic)[:6])
            lines.append(f"{index}. {topic.get('name')}｜关键词：{keys}")
        lines.append("取消：/取消关注 名称或序号")
        return "\n".join(lines)

    @filter.command("关注", alias={"添加关注", "我要关注"})
    async def cmd_watch(self, event: AstrMessageEvent):
        """添加关注主题；不带参数时列出当前关注。"""
        if not self._is_owner(event):
            yield event.plain_result(OWNER_ONLY_TEXT)
            return
        self._remember_umo_from_event(event)
        text = event.get_message_str().strip()
        arg = re.sub(r"^/?(?:添加关注|我要关注|关注)", "", text, count=1).strip()
        if not arg or arg in ("列表", "管理", "查看"):
            yield event.plain_result(self._watch_list_text())
            return
        topics = [name for name in self._parse_topics(arg) if name not in WATCH_BANNED_TOPICS]
        if not topics:
            yield event.plain_result("用法：/关注 主题，比如 /关注 黄金价格、/关注 考研。")
            return
        added: list[dict[str, Any]] = []
        existed: list[dict[str, Any]] = []
        for name in topics[:5]:
            topic, reason = self._add_watch(name)
            if topic is None:
                continue
            (existed if reason == "已关注" else added).append(topic)
        lines: list[str] = []
        if added:
            lines.append("好，已关注：" + "、".join(str(topic.get("name")) for topic in added))
            for topic in added:
                lines.append(f"· {topic.get('name')}｜关键词：{'、'.join(self._watch_keywords(topic)[:8])}")
            lines.append("以后每天 23:00 的日报会多一段「关注」；现在可以发 /日报 看效果。")
        if existed:
            lines.append("这些之前就关注过了：" + "、".join(str(topic.get("name")) for topic in existed))
        if not lines:
            lines.append("没加上，换个说法试试。")
        yield event.plain_result("\n".join(lines))

    @filter.command("关注列表", alias={"我的关注", "关注管理"})
    async def cmd_watch_list(self, event: AstrMessageEvent):
        """查看当前关注的主题。"""
        if not self._is_owner(event):
            return event.plain_result(OWNER_ONLY_TEXT)
        return event.plain_result(self._watch_list_text())

    @filter.command("取消关注", alias={"删除关注", "别关注了"})
    async def cmd_unwatch(self, event: AstrMessageEvent):
        """取消关注主题，按名称或序号。"""
        if not self._is_owner(event):
            yield event.plain_result(OWNER_ONLY_TEXT)
            return
        text = event.get_message_str().strip()
        arg = re.sub(r"^/?(?:取消关注|删除关注|别关注了)", "", text, count=1).strip()
        if not arg:
            yield event.plain_result("用法：/取消关注 名称或序号，比如 /取消关注 股市。/关注列表 看当前关注。")
            return
        removed: list[str] = []
        for name in self._parse_topics(arg) or [arg]:
            removed_name = self._remove_watch(name)
            if removed_name:
                removed.append(removed_name)
        if removed:
            yield event.plain_result("已取消：" + "、".join(removed))
        else:
            yield event.plain_result("关注列表里没找到这个主题，发 /关注列表 看看。")

    @filter.regex(WATCH_ADD_RE)
    async def on_watch_request(self, event: AstrMessageEvent):
        """自然语言加关注，比如“明天开始，每天额外帮我报道一下 AI 科技圈”。"""
        if not self._is_owner(event):
            return
        text = event.get_message_str().strip()
        if not text or text.startswith("/") or len(text) > 60:
            return
        match = WATCH_ADD_RE.match(text)
        if not match:
            return
        topics = [
            name
            for name in self._parse_topics(match.group("topic"))
            if name not in WATCH_BANNED_TOPICS
            and not name.startswith(("列表", "设置", "帮助"))
            and not any(word in name for word in ("这个", "那个", "一下", "什么"))
        ]
        if not topics:
            return
        self._remember_umo_from_event(event)
        added: list[str] = []
        existed: list[str] = []
        for name in topics[:5]:
            topic, reason = self._add_watch(name)
            if topic is None:
                continue
            if reason == "已关注":
                existed.append(str(topic.get("name")))
            else:
                added.append(str(topic.get("name")))
        if not added and not existed:
            return
        event.stop_event()
        if added:
            reply = (
                "好，已关注：" + "、".join(added) + "\n"
                "以后每天 23:00 的日报里会多一段「关注」；今天想先看效果就发 /日报。"
            )
            if existed:
                reply += "\n之前就在关注里：" + "、".join(existed)
        else:
            reply = "这些已经在关注里了：" + "、".join(existed) + "\n发 /关注列表 可以管理。"
        yield event.plain_result(reply)

    @filter.regex(WATCH_DEL_RE)
    async def on_unwatch_request(self, event: AstrMessageEvent):
        """自然语言取消关注，比如“取消关注股市”。"""
        if not self._is_owner(event):
            return
        text = event.get_message_str().strip()
        if not text or text.startswith("/") or len(text) > 60:
            return
        match = WATCH_DEL_RE.match(text)
        if not match:
            return
        removed: list[str] = []
        for name in self._parse_topics(match.group("topic")):
            removed_name = self._remove_watch(name)
            if removed_name:
                removed.append(removed_name)
        if not removed:
            return
        event.stop_event()
        yield event.plain_result("已取消：" + "、".join(removed))

    # ------------------------------------------------------------ 生僻词解释

    async def _handle_term_question(self, event: AstrMessageEvent, pattern: re.Pattern[str]):
        if not self._is_owner(event) or not self._conf_bool("enable_term_explain", True):
            return
        text = event.get_message_str().strip()
        if not text or text.startswith("/") or len(text) > 40:
            return
        match = pattern.match(text)
        if not match:
            return
        term = _clean_term(match.group("term"))
        if len(term) < 2 or term.isdigit() or term in TERM_STOPWORDS:
            return
        self._remember_umo_from_event(event)
        event.stop_event()
        try:
            answer = await self._explain_term(term)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning("%s 生僻词解释失败：%s", _log_label(), exc)
            yield event.plain_result(f"「{term}」没查到，换个说法再问我，或者把原句发我。")
            return
        yield event.plain_result(answer)

    @filter.regex(TERM_PREFIX_RE)
    async def on_term_prefix(self, event: AstrMessageEvent):
        """解释一下 XX / 什么是 XX。"""
        async for result in self._handle_term_question(event, TERM_PREFIX_RE):
            yield result

    @filter.regex(TERM_SUFFIX_RE)
    async def on_term_suffix(self, event: AstrMessageEvent):
        """XX 是什么意思 / XX 什么意思。"""
        async for result in self._handle_term_question(event, TERM_SUFFIX_RE):
            yield result

    # ------------------------------------------------------------ 设置与帮助

    @filter.command("新闻设置", alias={"新闻配置"})
    async def cmd_settings(self, event: AstrMessageEvent):
        """查看新闻插件当前配置与运行状态。"""
        if not self._is_owner(event):
            return event.plain_result(OWNER_ONLY_TEXT)
        today = datetime.now().strftime("%Y-%m-%d")
        daily = self._load_json(self._daily_path(today)) or {}
        now = datetime.now()
        weekly = self._load_json(self._weekly_path(now)) or {}
        lines = [
            "新闻简报设置：",
            f"· 日报时间：每天 {self._conf_str('daily_time', '23:00')}（{'开' if self._conf_bool('enable_daily', True) else '关'}）",
            f"· 周报时间：周{self._weekday_label()} {self._conf_str('weekly_time', '23:10')}（{'开' if self._conf_bool('enable_weekly', True) else '关'}）",
            f"· 启用源：{','.join(SOURCE_LABELS.get(s, s) for s in self._enabled_sources()) or '无'}",
            f"· 关注主题：{len(self._active_watches())} 个（{'、'.join(str(t.get('name')) for t in self._active_watches()[:6]) or '无'}）",
            f"· 生僻词解释：{'开' if self._conf_bool('enable_term_explain', True) else '关'}",
            f"· 天气提示：{'开' if self._conf_bool('enable_weather', True) else '关'}"
            f"（城市：{str(self._conf('weather_city', '') or '').strip() or '按网络自动定位'}）",
            f"· 主人 QQ：{self._owner_qq()}｜推送会话：{self._state.get('working_umo') or '自动探测'}",
            f"· 今日日报：{'已生成' if daily.get('digest') else '未生成'}"
            f"{'，已推送' if daily.get('pushed_at') else ''}",
            f"· 本周周报：{'已生成' if weekly.get('digest') else '未生成'}"
            f"{'，已推送' if weekly.get('pushed_at') else ''}（覆盖 {weekly.get('days', 0)} 天）",
            f"· 数据目录：{self.data_root}",
        ]
        return event.plain_result("\n".join(lines))

    @filter.command("新闻帮助", alias={"新闻说明"})
    async def cmd_help(self, event: AstrMessageEvent):
        """新闻插件帮助。"""
        if not self._is_owner(event):
            return event.plain_result(OWNER_ONLY_TEXT)
        return event.plain_result(
            "新闻简报命令：\n"
            "· /日报 —— 马上生成今天的日报（开头附明天天气）\n"
            "· /天气 —— 看明天天气和穿衣、带伞建议\n"
            "· /周报 —— 马上生成最近 7 天的周报\n"
            "· /新闻源 —— 检测新闻源是否可用\n"
            "· /关注 主题 —— 加关注（也可直接说“帮我关注 AI 科技圈”）\n"
            "· /关注列表 —— 看当前关注；/取消关注 名称 取消\n"
            "· 直接问「XX 是什么意思」—— 我解释词义和它在新闻里的意思\n"
            "· /新闻设置 —— 查看定时、源与关注配置\n"
            f"默认每天 {self._conf_str('daily_time', '23:00')} 自动私聊推送，"
            f"周{self._weekday_label()} {self._conf_str('weekly_time', '23:10')} 推送周报。"
        )

    def _weekday_label(self) -> str:
        return "一二三四五六日"[_weekday_index(self._conf_str("weekly_day", "sun"))]
