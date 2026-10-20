# POV v0.5.0 · 采集来源体系扩展

按 `舆情与AI新闻采集来源总清单.md`（12 章、80+ 来源登记表）完善舆情验证系统的采集层。

## 做了什么

### 1. 新增 4 个模块

| 文件 | 内容 |
|------|------|
| `core/source_registry.py` | ★ 来源总目录：32 项登记（label / tier / group / status / note）+ 配置校验 + 采集器合并 + 类别映射 |
| `core/ai_sources.py` | arXiv / HuggingFace Daily Papers / GitHub Trending / VentureBeat AI |
| `core/policy_sources.py` | Federal Register / 中国政府网政策文件库 / GDELT DOC 2.0 |
| `core/hotlist.py` | 微博热搜 / 百度热搜 / 头条热榜 |

`core/feedkit.py` 新增统一 `http_get()` / `http_get_json()`：
`trust_env=False` 绕过系统代理 + 编码兜底链 + 429 退避重试。

### 2. 采集源从 14 → 26

| 层 | 优先级 | 数量 | 源 |
|----|--------|------|-----|
| L0 官方接口 | 最高 | 10 | 中国政府网政策 / 美联邦公报 / GDELT / arXiv / HF论文 / HackerNews / B站热榜 / 微博热搜 / 百度热搜 / 头条热榜 |
| L1 原生 RSS | 高 | 9 | 量子位 / InfoQ / VentureBeat / TechCrunch / TheVerge / ArsTechnica / MIT科技评论 / OpenAI官方 / ProductHunt |
| L3 页面解析 | 兜底 | 4 | GitHub趋势 / 36氪 / 百度新闻 / 今日头条 |
| 桥接喂入 | 特殊 | 3 | 搜索引擎 / X / YouTube |

另有 6 个 `needs-rsshub` 源（机器之心 / 新智元 / 财联社 / 商务部 / 工信部 / 网信办）
已登记但未启用——它们原生 RSS 已失效，需 Docker 自建 RSSHub 后再接。

### 3. 配置校验（防静默失效）

启动时校验 `baseline.sources`，两类问题告警落日志：
- **未登记源**（拼写错误）→ 采集不到却无任何报错，最难排查
- **已关停源**（清单第十章）→ bilibili-API-collect / tiny-tiny-rss /
  nitter / snscrape / twint / bing-news-api / newsnow 等

### 4. 前沿信号分层

论文与开源项目是**超前信号**，几乎必定是独苗事件。若混入主事件库，
会让博主因「没追一篇当下无人讨论的论文」而被误判滞后。

→ 事件打标 `is_signal: true`，**不参与**追热/滞后判据；
  在报告「六、前沿信号」与看板侧栏单独呈现，用于判断**提前卡位**能力。

### 5. 报告与看板

- 报告新增「六、前沿信号（论文 / 开源动态）」表格（Markdown + HTML 双端）
- 看板侧栏新增「前沿信号」面板，API `/api/state` 输出 `signals` 与 `source_catalog`

## 关键实测结论（避坑）

先 curl 逐个实测再接入，只接跑得通的：

- **arXiv**：`all:"关键词"` 检索普遍 **429 / 30s+ 超时**（要扫全库），
  而 `cat:cs.AI` 类查询稳定 200、约 1.4s → 改为按分类取最新，
  关键词只做**本地**过滤，不发给 API。
- **GDELT**：清单称「含情绪分数」，但 `mode=artlist` **不返回 tone 字段**；
  tone 只在 `timelinetone` 模式或 GKG 离线集 → 删死代码、改正文档，
  情绪维持 neutral，不伪造。
- **微博热搜**：必须带 `Referer: https://weibo.com/`，否则 403。
- **百度热搜**：结构 `cards[].content[].content[]`（多嵌一层），需两种兼容。
- **已失效源**：机器之心 RSS（302 跳 /data-service）、虎嗅（阿里云 WAF）、
  36氪（全站 SPA）、财联社 nodeapi（404）、网信办 rss.xml（404）、工信部 RSS（404）。

## 校验结果

- 26 源中 **23 个 ok**；非 ok 仅 gdelt（临时限流，1 请求/5 秒，下轮自动恢复）、
  kr36 / baidu_news（SPA / 反爬，既有问题）
- 221 条条目 → 216 事件（前沿信号 46 / 参与验证 175）
- 验证分布 {独苗 287 / 偏早 17 / 滞后 10 / 稳健 8 / 偏晚 1}，
  与分层前基本一致（稳健档 6→8），判据未被稀释
- 看板运行于 http://127.0.0.1:8766

## 后续可选项

1. **Docker 自建 RSSHub** → 解锁机器之心 / 新智元 / 财联社 / 部委公告栏 6 个源
2. **changedetection.io** 盯住网信办 / 工信部公告栏（生成式 AI 备案、算法备案政策密集期高优先）
3. **GDELT 话题级情绪**：改用 `mode=timelinetone` 拿「AI 话题整体情绪走势」，
   补上目前缺失的情绪维度（需权衡 5 秒限流，建议与 artlist 错开调度）
4. **Reddit**：公共 API 2027-03 终止，如需要应尽早申请 OAuth
