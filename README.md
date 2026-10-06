# global-finance-radar

Cursor / 本机 Agent Skill：拉取近 24 小时**美洲 / 欧洲 / 亚洲**宏观与市场新闻，过滤个股热点、中国相关与明显政治稿，整理成**中英对照**简报（题目、约 100 字摘要、出版方原文链接），直接输出到对话屏幕，**不落盘**。

零 API Key、零订阅墙抓取、仅依赖 Python 标准库。

## 功能概览

- 免费公开 RSS / Google News 检索发现条目
- 将 `news.google.com` 跳转解析为出版方原文 URL
- 按区域配额取样（默认美洲 / 欧洲 / 亚洲各至少约 4 条，共 **15** 条）
- 过滤：个股推荐与单票热点、中国区财经、战争/选举等硬政治稿
- Agent 将 JSON 候选排版为中英对照简报

## 触发方式

在 Cursor 对话中可以说：

- 「今天全球财经」「全球市场新闻」「全球财经速报」
- 「跑一下财经雷达」「global markets brief」「markets radar」
- 或直接 `/global-finance-radar`

仅手动对话触发；本仓库不包含定时任务或 Automation。

## 安装

### 作为 Cursor Agent Skill

将本仓库放到个人 skills 目录（二选一或按你本机习惯）：

```text
~/.agents/skills/global-finance-radar/
# 或
~/.cursor/skills/global-finance-radar/
```

Windows 示例：

```text
C:\Users\<你>\.agents\skills\global-finance-radar\
```

目录内需包含 `SKILL.md`、`config.json`、`scripts/fetch_news.py`。重启或新开 Agent 会话后即可被发现。

### 依赖

- Python 3.10+（推荐 3.11 / 3.12）
- **无** `pip install` 第三方包（仅用 `urllib`、`xml.etree` 等标准库）
- 可访问外网；若本机代理失效，脚本会优先直连重试

## 快速验证

在 skill 根目录执行：

```bash
python scripts/fetch_news.py
```

或指定条数：

```bash
python scripts/fetch_news.py --limit 15
```

成功时 stdout 为 JSON（`generated_at`、`count`、`region_counts`、`sources`、`warnings`、`items[]`）。stderr 上的 `WARNING:` 表示某源失败或 Google News 原文解析失败，其余源会继续。

`items[].link` 应为出版方域名（如 `reuters.com`、`cnbc.com`），不应再是 `news.google.com`。

## Agent 工作流（摘要）

完整约定见 [`SKILL.md`](SKILL.md)。要点：

1. 在 skill 目录运行 `python scripts/fetch_news.py`
2. 读取 JSON；空结果如实说明，不编造新闻
3. 终筛漏网的个股 / 中国 / 政治稿
4. 尽量输出 **15** 条中英对照简报到屏幕；不足则说明原因，不凑数
5. **禁止**写入 `output/` 或其他文件

## 信源与区域

| 来源 | 区域 | 说明 |
|---|---|---|
| Americas Markets | americas | 美联储 / 美股指数 / 美债等宏观检索 |
| CNBC Markets | americas | 官方 RSS |
| MarketWatch | americas | Dow Jones 公共 RSS |
| Europe Markets | europe | ECB / 欧元区 / 英欧市场检索 |
| Asia Markets | asia | 日韩印澳新等亚洲市场（排除中国） |
| Reuters Macro | global | 路透宏观与市场 |
| Investing.com Macro | global | Investing.com 宏观检索 |

增删源、调整过滤词或条数：编辑 [`config.json`](config.json)。

常用配置项：

| 字段 | 含义 | 默认 |
|---|---|---|
| `window_hours` | 时间窗口（小时） | `24` |
| `max_items` | 输出条数上限 | `15` |
| `min_per_region` | 美洲/欧洲/亚洲各自下限（尽量满足） | `4` |
| `sources` | RSS / 检索源列表（含 `region`） | 见配置文件 |
| `hard_exclude_keywords` | 硬排除词 | 见配置文件 |
| `single_stock_exclude_keywords` | 个股热点排除词 | 见配置文件 |

## 文件结构

```text
global-finance-radar/
├── SKILL.md                 # Agent 触发条件、工作流、输出模板
├── README.md                # 本文件
├── config.json              # 信源、区域配额、过滤词
└── scripts/
    └── fetch_news.py        # 拉取 / 过滤 / 去重 / 解析原文链接 → stdout JSON
```

## 输出示例（结构）

```markdown
# 全球财经速报 / Global Markets Brief
数据窗口：过去 24 小时 | 拉取时间：...
区域覆盖 / Regions: Americas n · Europe n · Asia n · Global n

## 1. 中文标题
**EN:** English title
- 区域 / Region: americas
- 摘要：约 100 字中文
- Summary: English counterpart
- 链接 / Link: https://publisher.example/...
- 来源 / Source: CNBC Markets
```

## 限制与说明

- 面向**宏观与区域市场**简报，不是个股推荐或交易信号
- 亚洲源刻意弱化/排除中国大陆及港股相关报道（可按需改 `config.json`）
- Google News 仅作发现渠道；输出前会解析为出版方链接，解析失败则丢弃该条
- 部分出版方页面可能有付费墙；本 skill 只提供公开可获得的链接与 RSS 摘要级信息
- 网络不稳定或源站变更时，个别源可能为空；以 `warnings` / `sources` 字段为准

## License

未单独声明许可证时，默认按仓库根目录 LICENSE 执行；若你 fork 后分发，请自行补充许可证文件。
