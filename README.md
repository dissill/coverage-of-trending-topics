# AstrBot 每日新闻简报（astrbot_plugin_daily_news）

一个给 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 用的新闻插件：每天定时抓取国内公开新闻源，用你配置的对话模型生成简短日报，私聊推送到 QQ；支持每周趋势周报、聊天里直接添加关注主题、生僻词解释，以及明天的天气提醒。

> 当前版本 **v1.3.0**，在 AstrBot 4.29.x + aiocqhttp（SnowLuma / NapCat 等）+ DeepSeek 上实测运行。

## 功能

- **每日简报**：每天 23:00 私聊推送，头一条报日期、条数、关注数，第二行是明天天气；每条要闻单独一条消息，读起来不累。
  - 分类固定：政策 / 经济 / 民生 / 国际 / 科技 / 社会
  - 每条只讲两件事：`事实`（发生了什么）+ `影响`（跟你有什么关系），各不超过 45 字
  - 条数默认 5 条（可配 3-6），总字数 600-900
- **跨天去重**：默认回看最近 5 天，抓候选时过滤一遍、模型输出后再过滤一遍，提示词里附最近推送过的标题，同一件事没有新进展就跳过。
- **每周周报**：默认周日 23:10，汇总最近 7 天日报，输出：本周主线 / 趋势判断 / 政策与资金 / 普通人该做 / 普通人别做 / 下周关注。
- **明天天气**：按本机公网 IP 自动定位城市（也可写死），提示温度、冷暖、降雨概率、是否带伞、要不要加衣。天气失败只是少一行，不影响日报。
- **自定义关注主题**：不用改代码，在 QQ 里说一句就行：
  - 「明天开始，每天额外帮我报道一下 AI 科技圈的热点消息」
  - 「帮我额外关注股市方面的信息」
  - 「以后每天关注新能源汽车和黄金」
- **生僻词解释**：日报/周报末尾会提示，可以直接问「Prompt 是什么意思」「AI 圈里的 Protmat 是什么」，机器人结合最近 3 天新闻解释词义和语境。
- **只认主人**：所有命令和推送只对配置的 `owner_qq` 生效。

## 效果示例

```text
【新闻简报】11月18日 · 5 条要闻 · 关注 3 个
【明天天气】桂林 阴 11~17℃；有点凉，早晚加件外套；降雨概率46%，可能下雨，带把折叠伞。

1. 政策｜商务部推车电一体报废
事实：……
影响：……

2. 经济｜深圳外贸破5万亿后换挡
事实：……
影响：……

【关注 · 股市】
上证指数 3587.83（+0.09%）｜深证成指 10659.41（+0.20%）｜创业板指 2311.62（+0.85%）
· ……

【政策与资金】
……

【一句话】
……

这次汇报里有什么不懂的关键词吗？告诉我，我可以帮你解答。
```

## 安装

1. 把整个目录放到 AstrBot 的插件目录：

   ```text
   <AstrBot 数据目录>/plugins/astrbot_plugin_daily_news/
   ```

   Launcher 版一般在：`C:\QQBot\instances\<实例ID>\core\data\plugins\`

2. 在 AstrBot 使用的 Python 环境里装依赖：

   ```bash
   python -m pip install -r requirements.txt
   # 国内网络可用：-i https://pypi.tuna.tsinghua.edu.cn/simple
   ```

3. 在 WebUI「插件」页重载插件，或调用：

   ```bash
   POST /api/plugin/reload
   {"name": "astrbot_plugin_daily_news"}
   ```

4. 打开插件配置，把 `owner_qq` 改成你自己的 QQ 号并保存（**必填**；留空则命令和推送都不会生效）。
5. 私聊机器人发 `/新闻源` 确认抓取正常，再发 `/日报` 看效果。

## 命令

| 命令 | 别名 | 作用 |
| --- | --- | --- |
| `/日报` | `/今日新闻`、`/新闻日报` | 立刻生成并推送今天的日报（开头附明天天气） |
| `/天气` | `/明天天气` | 查明天天气和穿衣、带伞建议 |
| `/周报` | `/本周新闻`、`/新闻周报` | 生成最近 7 天周报 |
| `/关注 主题` | `/添加关注`、`/我要关注` | 添加关注主题；不带参数等于查看列表 |
| `/关注列表` | `/我的关注`、`/关注管理` | 查看当前关注 |
| `/取消关注 主题` | `/删除关注`、`/别关注了` | 删除关注 |
| `/新闻源` | `/新闻源检测` | 检查各新闻源连通性和条数 |
| `/新闻设置` | `/新闻配置` | 查看定时、关注、生僻词、天气等配置 |
| `/新闻帮助` | `/新闻说明` | 命令说明 |

自然语言也能加关注/取消关注，例如「帮我关注半导体」「取消关注黄金」。

生僻词直接在私聊里问，不用加命令前缀，例如「Prompt是什么意思」「什么叫降准」。

## 新闻源

基础源（每天抓）：

| 源 | 内容 | 形式 |
| --- | --- | --- |
| 中新网即时新闻 | 综合滚动要闻 | RSS |
| 新浪全站日热榜 | 当日热点 | JSON |
| 腾讯新闻热榜 | 时事、政策、社会 | JSON |
| 华尔街见闻快讯 | 财经、资金、宏观 | JSON |
| 百度实时热搜 | 普通人在关注什么 | JSON |

关注主题补充源（有对应关注时才抓）：

| 源 | 用途 |
| --- | --- |
| IT之家 RSS | AI / 科技圈 |
| 量子位 RSS | AI 垂直 |
| 华尔街见闻 A 股频道 | 股市快讯 |
| 新浪行情 | 上证/深成/创业板指数快照 |

天气：`wttr.in` 取预报，`pconline` IP 接口取中文城市名。

所有源都是公开只读接口，不带 Cookie、不登录；单源失败不影响整体。

## 关注主题怎么工作

- 关注存在 `plugin_data/astrbot_plugin_daily_news/watches.json`，字段：`name` / `keywords` / `created_at` / `active`。
- 加关注时自动从主题名派生关键词（内置 AI、股市、新能源车、房地产、就业、黄金、汇率、芯片等映射；其他主题按词拆分）。
- 生成日报时，用关键词在候选新闻里匹配，每主题最多 1-3 条交给模型摘要；股市类主题额外附三大指数快照。
- 每个主题在日报里单独一段：`【关注 · 主题】`；周报会带上这些主题的摘要。

## 配置

配置文件：`<AstrBot 数据目录>/config/astrbot_plugin_daily_news_config.json`，或在 WebUI 插件配置页修改。

| 配置 | 默认 | 说明 |
| --- | --- | --- |
| `owner_qq` | （必填） | 只有这个号能下命令、收推送；留空插件不工作 |
| `target_umos` | `[]` | 推送会话；留空自动探测，成功后记住 |
| `enable_daily` / `daily_time` | `true` / `23:00` | 每日推送开关和时间 |
| `daily_items` | `5` | 要闻条数，3-6 |
| `split_items` | `true` | 每条要闻单独一条消息；关掉合并成长消息 |
| `recent_days` | `5` | 跨天去重窗口，2-14 |
| `enable_watch` / `watch_items` | `true` / `3` | 关注主题开关、每主题最多几条 |
| `enable_term_explain` | `true` | 生僻词解释开关 |
| `enable_weather` | `true` | 日报开头天气提示 |
| `weather_city` | `""` | 天气城市，留空按公网 IP 自动定位 |
| `weather_timeout` | `12` | 天气接口超时（秒） |
| `enable_weekly` / `weekly_day` / `weekly_time` | `true` / `sun` / `23:10` | 周报开关、星期、时间 |
| `push_chunk_chars` | `1200` | QQ 单条消息最大字数，超长自动拆 |
| `llm_timeout` / `weekly_llm_timeout` | `180` / `240` | 模型超时 |
| `provider_id` | `""` | 指定模型提供商；留空用 AstrBot 当前默认模型 |

## 数据

```text
plugin_data/astrbot_plugin_daily_news/
├── daily/YYYY-MM-DD.json    # 日报：items、summary、digest、weather、pushed_at、skipped_recent
├── weekly/YYYY-MM-DD.json   # 周报（文件名为当周结束日）
├── watches.json             # 关注主题
└── state.json               # 已成功推送的会话等
```

日报档案保留 45 天（`keep_daily_days` 可改）。

## 目录结构

```text
astrbot_plugin_daily_news/
├── main.py                # 插件主体：命令、定时、日报/周报生成、关注、生僻词、天气
├── news_sources.py        # 新闻源抓取、去重、格式化、行情快照、天气
├── _conf_schema.json      # WebUI 配置 schema
├── metadata.yaml          # 插件元数据
├── requirements.txt       # 依赖：httpx
├── README.md
├── README-PUSH.md         # 首次推送到 GitHub 的说明（PAT、代理、TLS 排错）
├── CHANGELOG.md
├── LICENSE
├── .gitignore
├── .gitattributes
├── .github/
│   └── workflows/ci.yml   # CI：py_compile + 配置 JSON 校验
└── scripts/
    ├── init-and-push.ps1  # 初始化仓库并推送
    ├── push.ps1           # 日常提交并推送
    └── push.bat           # 双击用的入口
```

## 已知限制

- 模型输出由 AI 生成，重要信息请自行核实；插件只整理公开新闻，不构成投资建议。
- 天气按公网 IP 定位，可能落到附近城市；要精确就配置 `weather_city`。
- 周报依赖最近 7 天的日报档案；刚装上时前几天数据会缺，模型会说明覆盖不全，不会编造。
- 去重是标题相似度判断，极少数同事件不同措辞可能漏判；可在 `main.py` 的 `_is_duplicate_title` 调阈值。
- 只支持 aiocqhttp 平台推送（`support_platforms: aiocqhttp`）。

## 开发

```bash
python -m py_compile main.py news_sources.py
```

改完代码后在 AstrBot WebUI 重载插件，或：

```bash
POST /api/plugin/reload
{"name": "astrbot_plugin_daily_news"}
```

## License

[MIT](LICENSE)
