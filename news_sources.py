# -*- coding: utf-8 -*-
"""每日新闻简报插件：新闻源抓取、清洗、去重。

所有源都是公开接口/RSS，只读、不登录、不带 Cookie。
抓取失败不影响其它源；由调用方汇总 status。
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SOURCE_LABELS = {
    "chinanews": "中新网",
    "sina": "新浪热榜",
    "qqhot": "腾讯热榜",
    "wscn": "华尔街见闻",
    "baidu": "百度热搜",
    "ithome": "IT之家",
    "qbitai": "量子位",
    "wscn_astock": "见闻A股",
}

SOURCE_ORDER = ["chinanews", "sina", "qqhot", "wscn", "baidu"]
# 仅在用户添加了自定义关注主题时才抓取的补充源。
EXTRA_SOURCE_ORDER = ["ithome", "qbitai", "wscn_astock"]


@dataclass
class NewsItem:
    """一条候选新闻。"""

    title: str
    summary: str = ""
    source: str = ""
    url: str = ""
    ts: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


def _clean_text(value: Any) -> str:
    """去 HTML 标签、CDATA、多余空白。"""
    text = str(value or "")
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.S)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _decode_body(resp: httpx.Response) -> str:
    """容错解码：优先 UTF-8，其次 GB18030。有些站点 charset 标错。"""
    raw = resp.content
    for enc in ("utf-8", "gb18030", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _normalize_key(title: str) -> str:
    """标题归一化，用于跨源去重。"""
    text = title.lower()
    text = re.sub(r"[\s\W_]+", "", text, flags=re.UNICODE)
    return text[:30]


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    """按标题归一化去重，保留先出现的（来源顺序即优先级）。"""
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        key = _normalize_key(item.title)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


async def _fetch_chinanews(client: httpx.AsyncClient, timeout: float) -> list[NewsItem]:
    """中新网即时新闻 RSS：标题 + 摘要 + 时间，质量最好。"""
    url = "https://www.chinanews.com.cn/rss/scroll-news.xml"
    resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    text = _decode_body(resp)
    root = ET.fromstring(text)
    items: list[NewsItem] = []
    for node in root.findall(".//item")[:30]:
        title = _clean_text(node.findtext("title"))
        if not title:
            continue
        desc = _clean_text(node.findtext("description"))
        link = _clean_text(node.findtext("link"))
        pub = _clean_text(node.findtext("pubDate"))
        ts = 0.0
        if pub:
            try:
                ts = parsedate_to_datetime(pub).timestamp()
            except Exception:  # noqa: BLE001
                ts = 0.0
        items.append(
            NewsItem(
                title=title,
                summary=desc,
                source=SOURCE_LABELS["chinanews"],
                url=link,
                ts=ts,
            )
        )
    return items


async def _fetch_sina(client: httpx.AsyncClient, timeout: float, today: datetime) -> list[NewsItem]:
    """新浪全站日热榜：当日重要新闻，带媒体来源和时间。"""
    date_str = today.strftime("%Y%m%d")
    url = (
        "https://top.news.sina.com.cn/ws/GetTopDataList.php"
        f"?top_type=day&top_cat=www_www_all_suda_suda&top_time={date_str}"
        "&top_show_num=30&top_order=DESC&js_var=all_1_data"
    )
    resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    text = _decode_body(resp)
    match = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.S)
    if not match:
        return []
    payload = json.loads(match.group(1))
    items: list[NewsItem] = []
    for entry in payload.get("data", []) or []:
        title = _clean_text(entry.get("title"))
        if not title:
            continue
        media = _clean_text(entry.get("media"))
        item_url = _clean_text(entry.get("url"))
        ts = 0.0
        date_part = str(entry.get("create_date") or "").strip()
        time_part = str(entry.get("create_time") or "").strip()
        if date_part and time_part:
            try:
                ts = datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:  # noqa: BLE001
                ts = 0.0
        items.append(
            NewsItem(
                title=title,
                summary=f"来源媒体：{media}" if media else "",
                source=SOURCE_LABELS["sina"],
                url=item_url,
                ts=ts,
            )
        )
    return items


async def _fetch_qqhot(client: httpx.AsyncClient, timeout: float) -> list[NewsItem]:
    """腾讯新闻热点榜：标题类，覆盖政策/社会/国际热点。"""
    url = "https://r.inews.qq.com/gw/event/hot_ranking_list?page_size=20"
    resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = json.loads(_decode_body(resp))
    items: list[NewsItem] = []
    ranking = (payload.get("idlist") or [{}])[0]
    for entry in ranking.get("newslist", []) or []:
        title = _clean_text(entry.get("title"))
        if not title or "每10分钟更新" in title:
            continue
        longtitle = _clean_text(entry.get("longtitle"))
        summary = longtitle if longtitle and longtitle != title else ""
        items.append(
            NewsItem(
                title=title,
                summary=summary,
                source=SOURCE_LABELS["qqhot"],
                url=_clean_text(entry.get("url") or entry.get("surl")),
                ts=0.0,
            )
        )
    return items


async def _fetch_wscn_channel(
    client: httpx.AsyncClient, timeout: float, channel: str, source_label: str
) -> list[NewsItem]:
    """华尔街见闻某个频道的快讯（global-channel / a-stock-channel 等）。"""
    url = f"https://api-one.wallstcn.com/apiv1/content/lives?channel={channel}&limit=20"
    resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = json.loads(_decode_body(resp))
    items: list[NewsItem] = []
    for entry in ((payload.get("data") or {}).get("items") or [])[:20]:
        content = _clean_text(entry.get("content"))
        if len(content) < 8:
            continue
        title = content[:46] + ("…" if len(content) > 46 else "")
        try:
            ts = float(entry.get("display_time") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        items.append(
            NewsItem(
                title=title,
                summary=content,
                source=source_label,
                url="",
                ts=ts,
            )
        )
    return items


async def _fetch_wscn(client: httpx.AsyncClient, timeout: float) -> list[NewsItem]:
    """华尔街见闻全球快讯：财经/资金/宏观信号。"""
    return await _fetch_wscn_channel(client, timeout, "global-channel", SOURCE_LABELS["wscn"])


async def _fetch_wscn_astock(client: httpx.AsyncClient, timeout: float) -> list[NewsItem]:
    """华尔街见闻 A 股频道：股市/大盘/资金流向。"""
    return await _fetch_wscn_channel(client, timeout, "a-stock-channel", SOURCE_LABELS["wscn_astock"])


async def _fetch_baidu(client: httpx.AsyncClient, timeout: float) -> list[NewsItem]:
    """百度实时热搜：判断普通人在关注什么（只取标题）。"""
    url = "https://top.baidu.com/api/board?platform=wise&tab=realtime"
    resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = json.loads(_decode_body(resp))
    items: list[NewsItem] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            word = node.get("word")
            if isinstance(word, str):
                word = _clean_text(word)
                if word and word not in seen:
                    seen.add(word)
                    desc = _clean_text(node.get("desc") or "")
                    items.append(
                        NewsItem(
                            title=word,
                            summary=desc,
                            source=SOURCE_LABELS["baidu"],
                            url="",
                            ts=0.0,
                        )
                    )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload.get("data") or {})
    return items[:12]


async def _fetch_rss(
    client: httpx.AsyncClient, timeout: float, url: str, source_label: str, limit: int = 30
) -> list[NewsItem]:
    """通用 RSS 抓取（IT之家 / 量子位等标准 RSS）。"""
    resp = await client.get(url, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(_decode_body(resp))
    items: list[NewsItem] = []
    for node in root.findall(".//item")[:limit]:
        title = _clean_text(node.findtext("title"))
        if not title:
            continue
        desc = _clean_text(node.findtext("description"))
        link = _clean_text(node.findtext("link"))
        pub = _clean_text(node.findtext("pubDate"))
        ts = 0.0
        if pub:
            try:
                ts = parsedate_to_datetime(pub).timestamp()
            except Exception:  # noqa: BLE001
                ts = 0.0
        items.append(
            NewsItem(
                title=title,
                summary=desc,
                source=source_label,
                url=link,
                ts=ts,
            )
        )
    return items


async def _fetch_ithome(client: httpx.AsyncClient, timeout: float) -> list[NewsItem]:
    """IT之家：科技/数码/互联网，覆盖 AI 科技圈动态。"""
    return await _fetch_rss(client, timeout, "https://www.ithome.com/rss/", SOURCE_LABELS["ithome"], 30)


async def _fetch_qbitai(client: httpx.AsyncClient, timeout: float) -> list[NewsItem]:
    """量子位：AI 圈垂直媒体。"""
    return await _fetch_rss(client, timeout, "https://www.qbitai.com/feed", SOURCE_LABELS["qbitai"], 20)


MARKET_CODES = [
    ("s_sh000001", "上证指数"),
    ("s_sz399001", "深证成指"),
    ("s_sz399006", "创业板指"),
]


async def fetch_market_snapshot(timeout: float = 8.0) -> list[dict[str, Any]]:
    """新浪行情快照：上证/深成/创业板。必须带 Referer，返回 GB18030。"""
    codes = ",".join(code for code, _ in MARKET_CODES)
    url = f"https://hq.sinajs.cn/list={codes}"
    headers = {"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        text = resp.content.decode("gb18030", errors="replace")
    quotes: list[dict[str, Any]] = []
    for code, label in MARKET_CODES:
        match = re.search(rf'hq_str_{code}="([^"]*)"', text)
        if not match:
            continue
        parts = match.group(1).split(",")
        if len(parts) < 4:
            continue
        try:
            price = float(parts[1])
            change = float(parts[2])
            pct = float(parts[3])
        except (TypeError, ValueError):
            continue
        quotes.append({"name": label, "price": price, "change": change, "pct": pct})
    return quotes


async def detect_location(timeout: float = 8.0) -> dict[str, str]:
    """按本机公网 IP 猜所在城市（pconline 接口，GBK）。失败返回空字典。"""
    url = "https://whois.pconline.com.cn/ipJson.jsp?json=true"
    headers = {"User-Agent": UA}
    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            text = resp.content.decode("gb18030", errors="replace")
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start : end + 1]) if start >= 0 and end > start else {}
        city = str(data.get("city") or "").strip()
        province = str(data.get("pro") or "").strip()
        if city:
            return {"city": re.sub(r"市$", "", city), "province": province}
    except Exception:  # noqa: BLE001 - 定位失败不影响天气/日报
        pass
    return {}


WEATHER_DESC_ZH = (
    ("thunder", "雷阵雨"),
    ("heavy rain", "大雨"),
    ("moderate rain", "中雨"),
    ("light rain", "小雨"),
    ("patchy rain", "零星小雨"),
    ("drizzle", "毛毛雨"),
    ("freezing", "冻雨"),
    ("sleet", "雨夹雪"),
    ("snow", "雪"),
    ("blizzard", "暴雪"),
    ("mist", "薄雾"),
    ("fog", "雾"),
    ("overcast", "阴"),
    ("cloudy", "多云"),
    ("partly cloudy", "多云"),
    ("sunny", "晴"),
    ("clear", "晴"),
    ("windy", "大风"),
)


def _translate_weather(desc: str) -> str:
    text = (desc or "").strip().lower()
    for key, value in WEATHER_DESC_ZH:
        if key in text:
            return value
    return (desc or "").strip() or "多云"


async def fetch_weather(city: str = "", timeout: float = 12.0) -> dict[str, Any]:
    """用 wttr.in 的 j1 JSON 取“明天”的天气；city 为空时按本机公网 IP 自动定位。"""
    from urllib.parse import quote

    city = (city or "").strip()
    url = f"https://wttr.in/{quote(city)}?format=j1&lang=zh" if city else "https://wttr.in/?format=j1&lang=zh"
    headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=timeout) as client:
        resp = await client.get(url, timeout=timeout)
        resp.raise_for_status()
        payload = resp.json()

    nearest = (payload.get("nearest_area") or [{}])[0]
    area = ((nearest.get("areaName") or [{}])[0].get("value") or "").strip()
    region = ((nearest.get("region") or [{}])[0].get("value") or "").strip()
    days = payload.get("weather") or []
    if not days:
        raise RuntimeError("weather: no forecast days")
    target = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    day = next((item for item in days if item.get("date") == target), None)
    if day is None:
        day = days[1] if len(days) > 1 else days[0]

    hourly = day.get("hourly") or []
    rain = max((int(h.get("chanceofrain") or 0) for h in hourly), default=0)
    precip = sum(float(h.get("precipMM") or 0) for h in hourly)
    wind = max((int(h.get("windspeedKmph") or 0) for h in hourly), default=0)
    midday = hourly[len(hourly) // 2] if hourly else {}
    desc = _translate_weather(((midday.get("weatherDesc") or [{}])[0].get("value")) or "")
    tmax = int(day.get("maxtempC") or 0)
    tmin = int(day.get("mintempC") or 0)
    today_max = int((days[0].get("maxtempC") if days else tmax) or tmax)
    return {
        "city": city or area,
        "region": region,
        "date": day.get("date") or target,
        "tmin": tmin,
        "tmax": tmax,
        "desc": desc,
        "rain": rain,
        "precip": round(precip, 1),
        "wind": wind,
        "today_max": today_max,
        "diff": tmax - today_max,
    }


async def fetch_one(
    source: str,
    client: httpx.AsyncClient,
    timeout: float,
    today: datetime,
) -> list[NewsItem]:
    """按名字抓一个源。"""
    if source == "chinanews":
        return await _fetch_chinanews(client, timeout)
    if source == "sina":
        return await _fetch_sina(client, timeout, today)
    if source == "qqhot":
        return await _fetch_qqhot(client, timeout)
    if source == "wscn":
        return await _fetch_wscn(client, timeout)
    if source == "baidu":
        return await _fetch_baidu(client, timeout)
    if source == "ithome":
        return await _fetch_ithome(client, timeout)
    if source == "qbitai":
        return await _fetch_qbitai(client, timeout)
    if source == "wscn_astock":
        return await _fetch_wscn_astock(client, timeout)
    raise ValueError(f"未知新闻源：{source}")


async def fetch_all(
    enabled_sources: list[str],
    per_source_limit: dict[str, int] | None = None,
    timeout: float = 18.0,
) -> tuple[list[NewsItem], dict[str, dict[str, Any]]]:
    """并发抓取所有启用源，返回（去重后的条目, 每源状态）。"""
    limits = per_source_limit or {}
    today = datetime.now()
    statuses: dict[str, dict[str, Any]] = {}
    by_source: dict[str, list[NewsItem]] = {}

    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept": "*/*"},
        follow_redirects=True,
        timeout=httpx.Timeout(timeout, connect=8.0),
    ) as client:

        async def run(source: str) -> None:
            started = time.monotonic()
            try:
                items = await fetch_one(source, client, timeout, today)
                limit = int(limits.get(source, 20) or 20)
                items = items[:limit]
                statuses[source] = {
                    "ok": True,
                    "count": len(items),
                    "elapsed": round(time.monotonic() - started, 2),
                    "error": "",
                }
                by_source[source] = items
            except Exception as exc:  # noqa: BLE001 - 单源失败不能拖垮整体
                statuses[source] = {
                    "ok": False,
                    "count": 0,
                    "elapsed": round(time.monotonic() - started, 2),
                    "error": f"{type(exc).__name__}: {exc}",
                }

        await asyncio.gather(*(run(source) for source in enabled_sources))

    # 固定按 SOURCE_ORDER 拼接，保证重要来源排在材料前面，而不是按抓取完成顺序；
    # 补充源（IT之家/量子位/见闻A股）排在其后。
    ordered: list[NewsItem] = []
    for source in SOURCE_ORDER + EXTRA_SOURCE_ORDER:
        if source in by_source:
            ordered.extend(by_source[source])
    merged = dedupe_items(ordered)
    return merged, statuses


def format_materials(items: list[NewsItem], max_chars: int = 7000) -> tuple[str, int]:
    """把候选新闻压成给模型的材料文本，返回（文本, 实际条数）。"""
    lines: list[str] = []
    used = 0
    total = 0
    for item in items:
        summary = _clean_text(item.summary)
        if summary == _clean_text(item.title):
            summary = ""
        if len(summary) > 110:
            summary = summary[:110] + "…"
        line = f"[{used + 1}] {item.source}｜{item.title}"
        if summary:
            line += f"｜{summary}"
        if sum(len(chunk) for chunk in lines) + len(line) > max_chars:
            break
        lines.append(line)
        used += 1
        total += len(line) + 1
    return "\n".join(lines), used
