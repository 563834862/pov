# 舆情验证系统（POV · Public Opinion Verification）

监控你关注的博主 → 在博主发内容前维护一份**最新舆情基线** → 博主发内容后，将其**选题与舆情关注点比对**，判定选题质量，并输出「当前舆情关注点清单」供你自己的选题参考。
## 核心流程

```
┌────────────────────────────────────────────────────────────┐
│ ① 舆情基线采集（26 源分层并行 + 降级 + 噪声过滤）            │
│    政策层 / AI一手 / 媒体社区 / 国内舆情 / 热度榜 / 桥接喂入 │
│         ↓ 统一 JSON                                          │
│    public-opinion-monitor 技能 analyze.py（去重/健康分）      │
│         ↓ 营销物料 + 客户案例稿过滤 + 重复舆情去重            │
│         ↓ 新闻事件聚类（故事级，E01/E02…）+ 前沿信号分层      │
├────────────────────────────────────────────────────────────┤
│ ② 博主监控（配置清单，抖音桥接采集真实关注列表）             │
│    新内容检测（URL去重）→ 选题话题抽取（领域词典）             │
├────────────────────────────────────────────────────────────┤
│ ③ 比对验证引擎（四维评分 100 分制）                           │
│    热度契合45 + 时效契合25 + 声量参照10 + 差异化20            │
│         ↓                                                   │
│    🟢踩中热点 ≥75 ｜ 🟡追热稳健/偏早/偏晚 50-74 ｜ 🔴选题滞后<50 ｜ 🔵独苗选题 │
├────────────────────────────────────────────────────────────┤
│ ④ 输出：Web 看板 / Markdown / 单文件 HTML 报告               │
│    看板主视图「舆情事件库」：日期查询+分页，点击看跟进博主     │
└────────────────────────────────────────────────────────────┘
```

## 采集范围与噪声控制（v0.4.0）

- **范围 = AI 全领域 + 科技前沿热点**：关键词含 AI工具/AIGC/AI视频/AI短剧/大模型/智能体/AI行业/科技热点；
  词典覆盖 马斯克/特斯拉/Roadster/SpaceX 等关注面词，Tesla 飞车、Claude 封号类事件可正常成事件
- **营销物料过滤**：全民制作人/恰饭/商单/广告合作/sponsored（平台 campaign 官号内容）
- **客户案例稿过滤**：OpenAI 官方 RSS 里大量 "How X cuts costs with ChatGPT" 类效果营销稿，
  按正则识别剔除（属市场物料，不是舆情热点）
- **ProductHunt 标题级过滤**：只保留标题本身即 AI 相关的新品，避免每日泛工具新品刷成假事件
- **事件命名规则**：有互动数据取最热条目；无互动数据取**时间最早**的条目（新闻由首发报道命名）
- **英文标题自动翻译**：海外源（HN/TechCrunch/Ars/X/PH/OpenAI…）英文标题经 StepFun API
  批量译成中文（`core/translate.py`），译文入条目 `title_zh`，事件命名/看板/报告优先展示；
  话题匹配仍用原文（产品名不受影响）。凭证从 `~/.workbuddy/models.json` 的 StepFun 条目读取，
  译文缓存在 `data/translate-cache.json`（可人工审校），API 失败自动保留英文不影响采集。
  注意：step-5-preview 为推理模型，max_tokens 需 ≥3000，否则 reasoning 吃光 token 导致 content 为空

## 快速开始

```bash
cd E:\0AISoftwareCustom\new-auto\pov

# 全流程：采集基线 → 监控博主 → 验证 → 报告
python pov.py run

# 分步执行
python pov.py collect   # 只采集舆情基线
python pov.py verify    # 监控博主新内容 + 比对验证
python pov.py report    # 重新生成报告

# 启动 Web 看板（默认 http://127.0.0.1:8766）
python pov.py serve
# 端口被占用时临时换端口：python pov.py serve --port=8899
```

看板支持从页面直接触发「采集基线」「运行验证」，5 秒自动刷新。

## 自动调度（v0.6.0）

**采集和验证都是自动的**，不需要每次手动点击。三层机制叠加：

| 层 | 机制 | 生效条件 | 状态 |
|---|---|---|---|
| 1 | 看板内嵌调度线程 | `python pov.py serve` 运行中（含开机自启） | 采集每 6h / 验证每 2h |
| 2 | 独立常驻循环 | `python pov.py schedule`（不看板，Ctrl+C 停止） | 同上 |
| 3 | Windows 启动文件夹 | 已安装 `POV Dashboard.vbs`（开机登录自动起看板，pythonw 无窗口） | 重启后自动恢复 |

配置在 `config.yaml` 的 `schedule` 段：

```yaml
schedule:
  enabled: true               # false = 全手动（点按钮或 CLI）
  collect_interval_hours: 6   # 舆情基线采集周期
  verify_interval_hours: 2    # 博主验证周期（每次采集后约 1 分钟自动触发一次验证）
  run_on_start: true          # 启动后先立即跑一轮
  start_delay_seconds: 30     # 启动延迟（避开开机网络未就绪）
```

- **防重入**：采集/验证各持 `data/.lock-*` 文件锁，看板线程与手动按钮同时触发只有一个能跑；崩溃残留锁 45 分钟自动破除。
- **状态可见**：看板右侧「自动调度」面板实时显示运行状态、下次采集/验证时间、上次运行时间、最近异常（数据来自 `/api/state` 的 `scheduler` 字段）。
- **博主监控失败不阻塞**：桥接批次失败（如 Chrome 桥接未连接）时自动跳过监控，用已采集数据继续验证，基线照常更新。
- **想改周期**：编辑 `config.yaml` 后重启看板即可；想彻底手动：`schedule.enabled: false`。
- **想停止看板**：任务管理器结束 `pythonw.exe`（POV）进程，或下次登录前删掉启动文件夹里的 `POV Dashboard.vbs`。

> 本机 `schtasks.exe` 被安全策略拉黑，计划任务方式不可用；如确实需要"每天定点跑"，需在 Security Center → Command Security → Program Blacklist 中移除 schtasks 后再注册（`schtasks /Create /TN "POV-Daily-Run" /SC DAILY /ST 08:00 /TR "<python> pov.py run"`）。当前 6h/2h 内嵌周期已覆盖相同需求。

## 配置（config.yaml）

| 配置项 | 说明 |
|--------|------|
| `keywords` | 舆情基线关键词（博主所在领域），如 AI工具 / AIGC / AI视频生成 / AI短剧 |
| `baseline.sources` | 采集源清单，网络不可达自动降级并记录状态 |
| `bloggers` | 监控博主清单：`name` / `platform` / `url` / `mode` |
| `verify.weights` | 四维评分权重（热度45/时效25/声量10/差异化20） |
| `dedup.*` | 重复舆情判重（相似度阈值 / 时间窗 / 是否合并同源报道） |
| `sources_extra.*` | 各源附加参数（arXiv 检索词 / 联邦公报词 / GDELT 检索式 / GitHub Token） |
| `web.port` | 看板端口（默认 8766；8765 已被本机其他 uvicorn 占用） |

### 博主监控模式

- `douyin_bridge`：**抖音采集（当前 23 位真实博主在用）**——经 Playwright 浏览器桥接打开博主主页（复用 Chrome 登录态），从作品卡片提取视频。发布时间由 aweme_id 雪花解码（`id >> 32` = Unix 秒，已验证）；抖音作品列表由签名 API 加载、不在 RENDER_DATA 中，故走 DOM 提取。**采集时需用户 Chrome 开着且 Playwright MCP Bridge 扩展在线**
- `simulated`：示例数据（明确标记），用于跑通验证流程
- `weibo_uid`：微博采集，需在博主配置中加 `uid:` 字段（m.weibo.cn container API）

**新增博主**：在 config.yaml 的 bloggers 里加一行 `{name: 名字, url: "抖音主页链接", mode: douyin_bridge}`。

## 采集来源体系（v0.5.0）

来源清单依据《舆情与 AI 新闻采集来源总清单》建立，全部登记在
`core/source_registry.py`。当前启用 **26 个源**，分五层：

| 层 | 优先级 | 源 | 说明 |
|----|--------|-----|------|
| **L0 官方接口** | 最高 | `gov_policy` `federal_register` `gdelt` `arxiv` `hf_papers` `hackernews` `bilibili_rank` `weibo_hot` `baidu_hot` `toutiao_hot` | 免 key 公开接口，最稳定 |
| **L1 原生 RSS** | 高 | `qbitai` `infoq` `venturebeat_ai` `techcrunch_ai` `theverge_ai` `arstechnica` `mittr` `openai_news` `producthunt` | 不依赖 RSSHub、不受 SPA 改版影响 |
| **L3 页面解析** | 兜底 | `github_trending` `kr36` `baidu_news` `toutiao` | 结构易变，仅在前三层无覆盖时用 |
| **桥接喂入** | 特殊 | `x_search` `youtube_search` `search_engine` | 域名被墙/强风控，由 Agent 喂入 |
| **待自建 RSSHub** | 未启用 | `jiqizhixin` `xinzhiyuan` `cls_telegraph` `mofcom` `miit` `cac` | 标记 `needs-rsshub`，未自建前不启用 |

### v0.5.0 新增源

| 源 | 层 | 接入要点 |
|----|----|----------|
| **arXiv** `arxiv` | L0 | 官方 API，取 cs.AI/CL/LG/CV/MA 最新提交。**不用关键词检索**：实测 `all:"..."` 普遍 429/30s+ 超时，而 `cat:` 查询稳定 200、约 1.4s |
| **HuggingFace Daily Papers** `hf_papers` | L0 | `/api/daily_papers` 免认证，upvotes 直接作为互动量参与热度计算 |
| **GitHub Trending** `github_trending` | L3 | Trending 页解析为主，Search API 兜底 |
| **美联邦公报** `federal_register` | L0 | 官方 API 完全免费无 key，含规则/行政令/关税 |
| **GDELT DOC 2.0** `gdelt` | L0 | 免 key，15 分钟更新，覆盖 100+ 语种。⚠️ `mode=artlist` **不返回 tone 字段**（tone 需 `timelinetone` 模式或 GKG 数据集），故情绪维度不伪造，维持 neutral |
| **微博热搜** `weibo_hot` | L0 | 必须带 `Referer: https://weibo.com/`，否则 403 |
| **百度热搜** `baidu_hot` | L0 | PC board 接口，返回结构为 `cards[].content[].content[]`（多嵌一层） |
| **头条热榜** `toutiao_hot` | L0 | hot-board 公开接口，含 HotValue |
| **量子位** `qbitai` | L1 | 中文原生 RSS |
| **InfoQ** `infoq` | L1 | 中文原生 RSS |
| **VentureBeat AI** `venturebeat_ai` | L1 | 英文媒体 AI 专栏 RSS |

### 配置校验（防静默失效）

启动时校验 `baseline.sources`，两类问题会告警落日志：

- **未登记源**（拼写错误）→ 采集不到却无任何报错，最难排查
- **已关停源**（清单第十章避坑清单）→ `bilibili-API-collect`（2026-01-28 法务关停）、
  `tiny-tiny-rss`（2025-11-01 关停）、`nitter`/`snscrape`/`twint`（2026-08 X 律师函）、
  `bing-news-api`（2025-08 退役）、`newsnow`（代码冻结）

## 前沿信号分层（v0.5.0）

论文与开源项目是**超前信号**而非已成形舆情——它们几乎必定是独苗事件。
若直接混入主事件库，会让博主因为「没追一篇当下无人讨论的论文」而被误判滞后。

因此按来源类别分层（`source_registry.SIGNAL_CLASSES`）：

- **学术信号**（arXiv / HF Papers）与**开源动态**（GitHub Trending）→
  事件打标 `is_signal: true`，**不参与**追热/滞后判据
- 其余源（媒体/社交/热榜/政策）→ 正常参与验证
- 前沿信号在报告「六、前沿信号」与看板中单独呈现，用于判断**提前卡位**能力

**实测效果**：228 条条目 → 221 个事件，其中前沿信号 46 个、参与验证 175 个；
验证结论分布与分层前基本一致（稳健档反而从 6 条升至 9 条），说明判据未被稀释。

## 舆情事件库（看板主视图）

看板首屏为「舆情事件库」，回答「最近有什么舆情、什么时候的、谁跟进了」。事件按**新闻事件**（而非话题标签）组织：
同话题的舆情条目按发布时间间隔 72h 切分为不同波次，每个波次即一个新闻事件
（如「豆包工作×飞书联动（9月波）」与「豆包1.6发布（10月波）」是两个事件），
事件带首发/末次时间、代表条目与平台分布，编号 E01/E02…（E=Event）。

- **按日期查询**：起止日期选择 + 今天/近7天/近30天/全部快捷切换，默认近 30 天
- **分页浏览**：10/20/50 条每页，上一页/下一页，最新舆情在前（按首发时间倒序）
- **标题时间突出**：每行重点展示事件标题（中文译名）与首发时间，附阶段/话题/报道数/平台/跟进人数
- **点击事件看跟进**：展开双栏详情——左栏「舆情报道」（标题+时间+平台+原文链接），
  右栏「跟进博主及作品」（博主+作品标题+抖音链接+发布时间+滞后+验证判定）；
  滞后区分「舆情发布后跟进」与「⏪ 提前布局」，跟进基准取自事件内最近一条舆情
- 关键词搜索（标题/话题）叠加在日期过滤之上

API：`GET /api/events?start=&end=&q=&page=&size=`（分页列表）、
`GET /api/event?id=E03`（事件详情：报道+跟进博主）。

### 博主 ↔ 舆情 关联规则（防假关联）

- **话题交集**：帖子话题与事件话题必须有交集；宽泛概念（智能体/大模型/AI编程/工作流…）
  单独命中不算数——还需与事件标题有实质共同实体词（显著性 2-4 gram + 英文词，如
  封号/飞书/roadster/万星/bluesky），否则任何同类旧帖都会绑到最近的事件上
- **产品话题**（豆包/Claude/REA/可灵…）命中即算相关（同产品圈动态），实体词重合仅影响排序
- **时间门控**：早于事件首发 >7 天、或晚于事件末次 >30 天 → 不绑定（不属于这一波新闻）
- **话题重抽取**：词典会不断扩充（REA/Claude/马斯克汽车等后加入），验证时用当前词典
  重新抽取并与采集时缓存取并集，避免缓存旧值漏配
- 匹配度 = 0.5×话题分 + 0.3×实体词重合 + 0.2×时间贴近度，≥0.25 才建立关联

## 你的关注圈在热议（博主选题面镜像）

从已监控博主的内容按话题聚合（近 30 天）：帖数、涉及博主数、追热命中率、代表内容。
与「当前舆情关注点」互为镜像——两边都有的话题 = 关注圈与舆情同频；只出现在一边的 =
潜在内容缺口（舆情热但关注圈没做）或差异化机会（关注圈在做但舆情未热）。看板与报告均有此面板。

## 重复舆情判重（采集即去重）

同一件事常被多个源重复采集（搜索引擎 + X + HN + 多家媒体），
旧逻辑还会让「一条新闻命中 N 个话题」生成 N 个事件——事件库里就出现重复舆情。
现在分两层处理：

**① 条目层（collect 时，重复不入库）** `core/dedup.py: dedup_items`
- 精确重复：URL 归一化后相同，或标题指纹（小写+去标点空格）完全相同 → 丢弃
- 丢弃条目的 URL/平台并入保留条目的 `sources`，来源不丢、也不重复计数抬热度
- 近似重复（不同来源报道同一件事）默认**保留**为独立报道条目，
  由事件层聚成一簇；置 `dedup.near_dup_merge: true` 可直接合并为一条
- 注意：采集器给同一来源页的多条新闻加了 `#s0/#s1…` 片段区分符，
  归一化时**保留**该片段，否则这些独立条目会被误判为重复

**② 事件层（build_news_events，故事聚类）**
- 旧：按话题分组 + 话题内 72h 切波次 → 一条新闻多话题 = 多个同名事件
- 新：先用 `cluster_stories` 把「同一件事」的条目连通成一簇，一簇 = 一个事件；
  话题退化为事件属性（簇内条目话题并集），不再决定事件身份
- 相似度 = IDF 加权余弦（中文 bigram + 英文词），阈值 ≥0.30 且发布间隔 ≤72h
  高频泛词（视频/制作/工具）被 IDF 压低，稀有实体词（逆向/万星/roadster）主导
- 栏目前缀（Show HN / Ask HN / The Download）会被剥离——
  稀有却无区分度，否则同栏目不同新闻会被误并

**效果**：本轮 138 条条目 → 132 个事件，**重复标题 0 组**
（旧逻辑下 REA 一条新闻被拆成 5 个同名事件）。

配置（`config.yaml`）：

```yaml
dedup:
  enabled: true
  similarity: 0.30       # IDF 余弦阈值
  time_window_hours: 72  # 同故事允许的最大发布间隔
  near_dup_merge: false  # true=同源报道直接合并为一条
```

## 用 Agent 喂入搜索热点（search_engine 源）

本机网络下 Google/V2EX/Reddit/YouTube/X 不可达，搜索引擎源通过 Agent 预采集喂入：
把搜索结果按以下格式存为 `data/inputs/search-<日期>.json`（海外 X/YouTube 源用 `oversea-x-*.json` / `oversea-yt-*.json`，条目格式相同，`source` 字段建议写「X · @账号」）：

```json
[{"title": "标题", "url": "https://原文链接", "snippet": "摘要",
  "date": "2026-10-17", "source": "来源"}]
```

## 目录结构

```
pov/
├── pov.py                  # CLI 入口
├── config.yaml             # 系统配置
├── core/
│   ├── baseline.py         # 采集编排（分层注册表 + 并发 + 降级）+ analyze.py 调用
│   ├── source_registry.py  # ★ 采集来源总目录（分层/状态/类别/避坑登记）+ 配置校验
│   ├── ai_sources.py       # AI 一手信源：arXiv / HF Daily Papers / GitHub Trending / VentureBeat
│   ├── policy_sources.py   # 政策与全球事件：Federal Register / 中国政府网 / GDELT
│   ├── hotlist.py          # 国内热度榜：微博热搜 / 百度热搜 / 头条热榜
│   ├── dedup.py            # 重复舆情判重（IDF 余弦 + 故事聚类）
│   ├── events.py           # 新闻事件聚类（故事级 → E01/E02…）+ 前沿信号分层
│   ├── douyin_bridge.py    # 抖音采集（Playwright 浏览器桥接）
│   ├── blogger_monitor.py  # 博主监控路由（抖音桥接/示例/微博）
│   ├── overseas_collector.py # 海外舆情采集（X/YouTube 喂入 + HN/PH/科技媒体 RSS）
│   ├── timeline.py         # 博主跟进辅助（滞后计算/响应速度榜）
│   ├── feedkit.py          # 采集器共享工具（UA/AI 过滤/条目构造）
│   ├── lexicon.py          # AI工具/AIGC 领域词典（70+ 话题）
│   ├── verify.py           # 比对验证引擎 + 关注点清单 + 博主画像
│   ├── report.py           # Markdown / HTML / JSON 报告
│   ├── scheduler.py        # ★ 自动调度（周期采集/验证 + 文件锁防重入）
│   └── storage.py          # 路径与数据存取
├── web/                    # Web 看板（标准库 http.server，零依赖）
│   ├── server.py           # API：/api/state /api/collect /api/verify
│   └── static/             # 看板前端
├── start-dashboard.vbs     # 看板无窗口启动脚本（启动文件夹同款，可双击）
└── data/
    ├── baselines/          # 舆情基线快照（latest + 历史）
    ├── posts/              # 博主内容 + 验证结果
    ├── reports/            # 验证报告（md/html/json）
    ├── inputs/             # 搜索源输入 + 采集中间产物
    └── logs/               # 运行日志
```

## 验证引擎说明

**匹配**：帖子选题话题 ∩ 舆情事件话题（产品词精度 1.0 / 概念词 0.8，权重 55%）
+ 标题二元组相似度（25%）+ 发布时间与事件窗口的贴近度（20%），总分阈值 0.25。
「可灵新版本实测」命中可灵事件，而非宽泛的「AI视频生成」事件；
时间贴近度让同话题的不同新闻波次各归各家（9 月豆包内容 → 9 月波事件）。

**四维评分**：
- 热度契合（45）：事件报道覆盖广度（相对值）+ 互动量（相对最热事件归一，无数据取中性）
- 时效契合（25）：事件传播阶段——发酵期 1.0 / 活跃期 0.85 / 苗头期 0.70 / 回落期 0.40 / 消退期 0.10
- 声量参照（10）：博主内容互动量 vs 事件平均互动量
- 差异化（20）：热点饱和度——同热点报道越多分越低（避开红海）

**判定**（相位感知）：降温期话题（回落/消退）按分数区分 踩中/偏晚/滞后；活跃期话题按分数区分 踩中/稳健/偏早。

## 数据红线

- 舆情条目均带真实来源 URL；博主「示例数据」明确标记，绝不用模拟内容冒充真实发布
- 采集失败的平台在看板「采集源状态」中如实标注，不编造覆盖

## 依赖

- Python 3.13（标准库 + requests + PyYAML，已装）
- 复用技能 `public-opinion-monitor` 的 `scripts/analyze.py`（纯标准库）
- 看板无外部依赖（http.server + 原生 JS）
