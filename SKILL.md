---
name: global-finance-radar
description: >-
  拉取免费非订阅全球财经 RSS（美洲/欧洲/亚洲），过滤个股热点、中国相关与明显政治稿，
  中英对照输出 15 条题目、约100字摘要与原文链接到对话屏幕（不落盘）。
  Use when the user asks 今天全球财经、全球市场新闻、全球财经速报、跑一下财经雷达、global markets brief、
  global finance news、markets radar，或点名 global-finance-radar。
  不要用于中国财经、A股/港股专讯、个股推荐、或纯政治新闻。仅手动对话触发，不做定时调度。
---

# Global Finance Radar

从公开免费 RSS 取近 24 小时**美洲 / 欧洲 / 亚洲**宏观与市场新闻，整理成中英对照简报。无 API Key、无订阅墙抓取、**不写任何文件**，结果只打到对话屏幕。

核心原则：**先跑脚本拉实时数据，不要用训练记忆编新闻。**

## 快速执行

在本 skill 目录运行：

```bash
python scripts/fetch_news.py
```

默认即 **15** 条。可选覆盖：

```bash
python scripts/fetch_news.py --limit 15
```

依赖：仅 Python 标准库（`urllib` + `xml.etree`），无第三方包、无 API Key。若本机代理失效导致连接拒绝，脚本会自动直连重试一次。

stdout 是 JSON：`generated_at`、`count`、`region_counts`、`sources`、`warnings`、`items[]`（`title`、`link`、`source`、`region`、`published`、`raw_summary`）。`link` 一律是出版方原文 URL，**不是** `news.google.com`。Google News 聚合条目会在输出前解析；解析失败则丢弃该条并在 stderr 记 `WARNING:`。某源失败同样记 `WARNING:`，其余源继续。

## Agent 工作流

1. 在 skill 根目录执行 `python scripts/fetch_news.py`（不要用更小的 `--limit`）。
2. 读取 stdout JSON；若 `count == 0` 或全部源失败，如实告知用户，**不编造**。
3. 对 `items` 再做一轮终筛：丢掉漏网的**个股热点**、中国相关、明显政治稿；央行利率与宏观数据保留。
4. **固定输出 15 条**。若终筛后不足 15，如实说明缺几条及原因，绝不编造凑数。
5. 简报应覆盖美洲、欧洲、亚洲（看 `region` / `region_counts`）；若某区为 0，在文末注明。
6. 为每条写中英对照标题与摘要，按下方模板打印到对话；**禁止写入文件或 output 目录**。

触发方式：**仅手动**——用户点名本 skill 或说出触发词时执行。不要暗示每天自动跑，不要配置 `/loop` 或 Automation。

## 信源（config.json）

| 来源 | 区域 | 说明 |
|---|---|---|
| Americas Markets | americas | 美联储 / 美股指数 / 美债等宏观检索 |
| CNBC Markets | americas | 官方 RSS |
| MarketWatch | americas | Dow Jones 公共 RSS |
| Europe Markets | europe | ECB / 欧元区 / 英欧市场检索 |
| Asia Markets | asia | 日韩印澳新等亚洲市场（刻意排除中国） |
| Reuters Macro | global | 路透宏观与市场 |
| Investing.com Macro | global | Investing.com 宏观检索 |

脚本按区域配额取样（默认每区至少约 4 条，再补齐到 15）。增删源或过滤词：改 [`config.json`](config.json)。

## 过滤规则

**保留**：区域股指与债市汇市、大宗商品、利率汇率、宏观数据与央行决议（Fed / ECB / BoJ 等）、行业或市场结构讨论（非单票）。

**丢弃**（脚本初筛 + Agent 终筛）：

- **个股热点**：单票买卖推荐、目标价、增减持、单季业绩、带 ticker 的个股稿（如 `(AAPL)`、`TSX:ARG`）
- **硬排除**：中国区词、战争/制裁/选举等硬政治词
- **软排除**：congress / parliament / senate — 若同时出现利率、CPI、GDP 等宏观词则可保留

硬排除与个股过滤优先于宏观保留词。

## 输出模板

```markdown
# 全球财经速报 / Global Markets Brief
数据窗口：过去 24 小时 | 拉取时间：<generated_at>
区域覆盖 / Regions: Americas <n> · Europe <n> · Asia <n> · Global <n>
Sources: Americas Markets, CNBC Markets, MarketWatch, Europe Markets, Asia Markets, Reuters Macro, Investing.com Macro

## 1. <中文标题>
**EN:** <English title>
- 区域 / Region: <americas|europe|asia|global>
- 摘要：<约 100 字中文，依据 raw_summary / 标题改写，不臆造数字>
- Summary: <English counterpart, ~40–60 words>
- 链接 / Link: <原文 url>
- 来源 / Source: <source>

## 2. ...
（共 15 条）
```

要点：

- 标题与摘要必须中英对照；链接只用脚本给出的 `link`（已是出版方 URL）。不要改回 `news.google.com`。
- 摘要忠于条目内容；`raw_summary` 为空时可根据标题写短摘要，并避免编造未出现的数据点。
- 某源失败或某区缺失：文末一行注明（来自 `warnings` / `region_counts`），不要中断整份简报。

## 安全边界

- 只做 HTTP GET 读公开 RSS，以及解析 Google News 原文所需的公开请求；不发写请求。
- 不需要也不接受 API Key、cookie、付费账号。
- 拉取失败如实说明，不编造新闻。
