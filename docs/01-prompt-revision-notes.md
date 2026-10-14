# 项目总指令 · 修订说明 v1.1

> 项目：AI 海外达人营销助手（Influencer Marketing Copilot）
> 执行依据 = `docs/00-prompt-original.md`（你的原文，逐字保留）＋ 本文档（对原文缺失、模糊、冲突之处的补充定义）
> 本文档里的阈值、权重、字段名、枚举都是**可修改的默认假设**，不是行业标准。

## 0. 发现的问题与修订一览

| # | 原指令的问题 | 影响 | 修订位置 |
|---|---|---|---|
| 1 | 没说项目放哪、用什么 Python 版本 | 无法开工 | §1 |
| 2 | 4.1 要求「明确必填/可选」，但没给定义 | 表单与校验无法实现 | §2 |
| 3 | 4.3 要求「必要条件过滤」，但没说条件从哪来 | 容易被理解为写死阈值 | §3 |
| 4 | 4.2 要求注明互动率口径、4.3 要求计算，却没定口径 | 同一批数据可能算出不同分数 | §4 |
| 5 | 4.3 要求排序、4.4 要求「排名靠前」，但没说排序键 | 送进 AI 的名单不可复现 | §5 |
| 6 | 4.4 只列了输出内容，没给字段名与枚举 | Pydantic 校验、重试、UI 无处对齐 | §6 |
| 7 | 5.2 要求记录版本，但没说存哪、hash 怎么算 | 「版本号没有实际含义」正是原文禁止的 | §7 |
| 8 | 4.5 要求 CSV 带元信息 | 单个 CSV 塞不下，且会污染数据列 | §8 |
| 9 | 6.1 要求「人工编写的参考判断」，但没说谁写 | 诚信风险：我写的标签不是独立人工标注 | §9 |
| 10 | 6.2 的 A/B 对比容易被讲成「谁分高」 | 结论失真：规则看不到语义，本就必输 | §10 |
| 11 | 没提调用成本上限与确认 | 可能在不知情的情况下被扣费 | §11 |
| 12 | 界面中文 / 海外术语 / 英文字段名混用 | 后期加英文界面要返工 | §12 |
| 13 | 测试清单含超时、401、429，但没说怎么测 | 要么真调用烧钱，要么不测 | §13 |
| 14 | 「不要等确认」与「每阶段汇报」略有冲突 | 协作节奏不清 | §14 |

## 1. 项目位置与环境基线（补充原文 §三）

- 项目根目录：`D:\vibe coding\influencer-marketing-copilot`
- 技术栈（对原文默认方案的确认）：Python 3.12 / Streamlit / pandas / Pydantic v2 / python-dotenv / pytest / DeepSeek OpenAI 兼容接口（`openai` SDK）
- 命名约定：数据字段一律英文小写下划线；界面文案中文；业务术语保留英文原词
- 本机目前未安装 Python，Phase 1 开工前必须先装好（见 `02-phase0-environment-and-design.md`）

## 2. 表单字段定义（补充原文 4.1）

必填：产品名称、产品介绍或核心卖点、目标市场、目标平台、营销目标

可选：目标受众、合作方式、预算范围、硬性要求（最低粉丝量 / 内容语言 / 目标地区）

| 缺失字段 | 影响 |
|---|---|
| 目标受众 | 规则层「受众匹配」维度不参与加权；AI 的 audience_fit 输出 unknown 并写入 missing_info |
| 合作方式 / 预算 | 「合作可行性」维度只做定性提示，不参与排序 |
| 硬性要求 | 不启用硬性过滤，界面显示「未设置硬性条件」 |
| 营销目标 | 不允许为空。不同目标（曝光 / 种草 / 转化）评估侧重点不同，属于必填 |

## 3. 硬性条件的来源（补充原文 4.3）

- 硬性条件**只**来自用户在表单里显式填写的硬性要求，系统不内置任何硬性阈值
- 未填写 = 该条不启用
- 每条判定输出：字段名、实际值、阈值、结论（通过 / 未通过 / 无法判定）
- **数据缺失不等于未通过**：结论记为「无法判定」，进入「需人工核实」分组，默认不自动淘汰，排序时排在有明确结论者之后，界面显式提示

## 4. 互动率口径（补充原文 4.2 / 4.3）

优先级：

1. 数据自带 `engagement_rate` 且能说明来源 → 直接使用，同时写入 `engagement_rate_source`
2. 只有 likes / comments / follower_count → 计算 `(likes + comments) / follower_count`，口径标注为「项目内近似口径：likes+comments/followers」，写入 `engagement_rate_source`
3. 字段不全 → 不计算，标记「信息不足」，界面显示 `—`，不参与加权（**不视为 0 分**）

解析规则：`"4.2%"` → 0.042；`"1,234"`、`"12.5K"`、`"1.2M"` 统一解析为数值；无法解析的进入校验报告，不静默丢弃。

## 5. 排序算法与 AI 候选池（补充原文 4.3 / 4.4）

软性维度默认权重（标注：项目初始假设，页面可调）：

| 维度 | 默认权重 | 使用字段 | 缺失处理 |
|---|---|---|---|
| 内容相关性（词面/类目） | 30% | content_categories、recent_content_summary、sample_content_text vs 产品卖点 | 不参与 |
| 目标市场匹配 | 20% | market vs 目标市场 | 不参与 |
| 受众匹配 | 15% | audience_description vs 目标受众 | 不参与 |
| 互动表现 | 20% | engagement_rate（缺失时降级用 average_views / follower_count 并标注口径） | 不参与 |
| 合作可行性 | 15% | collaboration_history、language、platform | 不参与 |

- 总分 = Σ(维度分 × 权重) / Σ(参与维度权重)，缺数据不惩罚
- 同时输出 `信息完整度 = 参与维度数 / 启用维度数`
- 排序键：总分降序 → 信息完整度降序 → creator_id 升序
- 内容相关性只做确定性匹配（关键词 / 类目重叠），**语义判断一律交给 AI**，避免把简单计算交给大模型
- AI 候选池：默认规则总分 Top 10（`top_n` 可配 1–30），仅含硬性条件通过者；「无法判定」分组默认不进 AI，用户可在界面手动勾选加入（计入调用次数）

## 6. AI 结构化输出 schema（固定原文 4.4）

```json
{
  "creator_id": "...",
  "analysis_status": "ok | insufficient_info | failed",
  "content_relevance": {"verdict": "strong | moderate | weak | unknown", "reason": "...", "evidence": [{"field": "...", "quote": "..."}]},
  "goal_fit": {"verdict": "...", "reason": "...", "evidence": [{"field": "...", "quote": "..."}]},
  "audience_fit": {"verdict": "...", "reason": "...", "evidence": [{"field": "...", "quote": "..."}]},
  "recommendation_reasons": ["..."],
  "mismatches": ["..."],
  "risks": [{"type": "...", "detail": "...", "evidence": [{"field": "...", "quote": "..."}]}],
  "missing_info": ["..."],
  "human_checks": ["..."],
  "data_conflicts": ["..."],
  "confidence": "high | medium | low"
}
```

约束：

- `evidence.field` 必须是输入里真实存在的字段名，`quote` 必须来自该字段的实际文本；不得出现输入里没有的数字
- `confidence` 只表示「基于现有信息的判断把握」，不是合作成功率
- `analysis_status != ok` 时结论字段允许为空数组，界面显示「信息不足，需人工核实」
- 校验失败（非法 JSON / 缺字段 / 枚举非法）→ 最多重试 2 次；仍失败记 `failed` 并附原因，不作为可靠结论展示
- `data_conflicts` 非空 → 界面顶部显式标记「AI 结论与原始数据存在冲突」

## 7. 快照、版本与过期（补充原文 5.2）

- 运行目录：`runtime/runs/<run_id>/`，内含 `meta.json`、`rule_results.csv`、`ai_results.json`（Phase 2 起）、`decisions.json`
- `meta.json` 字段：`run_id`、`created_at`（本地时区 ISO8601）、`data_source`、`data_origin`（demo / upload / evaluation）、`data_hash`、`data_row_count`、`task_config`、`task_hash`、`rule_version`、`rule_config_hash`、`prompt_version`、`model_requested`、`model_returned`、`analysis_top_n`、`app_version`
- hash 算法：规范化（字段名排序、去空白、统一小写、行按 creator_id 排序）后 sha256 取前 12 位
- 过期判定：当前 data_hash / task_hash / rule_config_hash / prompt_version / model 与 run 的 meta 任一不一致 → 标记「结果已过期」，界面不当作最新结果，导出时在元数据中标记
- `st.session_state` 只做页面缓存；落盘 `meta.json` 是唯一事实来源
- `model_returned` 以 API 响应里的 model 字段为准，取不到才回退为配置值并标注

## 8. 导出格式（补充原文 4.5）

导出两个文件，而不是一个：

- `results_<run_id>.csv`：creator_id、基础字段、硬性条件结论、规则得分明细、AI 结论摘要、风险、缺失信息、人工审核状态、人工备注
- `results_<run_id>.meta.json`：数据来源、分析时间、data_hash、rule_version、prompt_version、model、导出时间、失败与信息不足记录条数

CSV 用 UTF-8 with BOM（Excel 打开中文不乱码）；失败与信息不足的记录全部保留并标注状态，不静默删除。

## 9. 评估集标签的来源（补充原文 6.1）

- 12–20 个固定案例与参考判断由我起草，性质是**项目内假设标签**，需要你逐条确认后才能在报告里作为参考基准
- 报告必须写明：标签来源（AI 起草 + 人工确认）、该评估集是否参与过规则或 Prompt 调整
- 禁止把项目内假设标签说成行业标注；禁止用 12–20 个案例的结论宣称统计显著

## 10. 方案 A/B 的对比口径（补充原文 6.2）

不看「谁总分高」，看两组指标：

- 增量：AI 是否识别出规则无法识别的语义信号（例如「内容领域相关但营销目标不符」）
- 代价：引入 AI 后是否**新增**错误（幻觉、把证据不足说成确定结论、与原始数据冲突）

两组都要如实列出，包括 AI 表现更差的案例。

## 11. 调用成本与限流（补充原文 5.3）

- 默认模型 `deepseek-chat`；单条超时 60 秒；失败最多重试 2 次（指数退避）
- 批量分析前显示「候选人数 / 预计调用次数 / 预计命中缓存数」，要求二次确认
- 单次运行上限默认 30 条，可配置
- 缓存键 = hash(creator_id + 该达人数据 hash + task_hash + prompt_version + model)；命中缓存不重复调用，界面标注「来自缓存」
- 日志、异常信息、导出文件均不得出现 API Key（header 内容需屏蔽）

## 12. 界面语言策略（补充原文 §七）

- 界面中文、业务术语保留英文原词、数据字段英文小写下划线
- 所有界面文案集中在一个文案字典里，便于未来增加英文界面

## 13. 测试策略（补充原文 §八）

- 纯逻辑测试（数据加载、校验、规则、导出、过期）用 pytest，不依赖网络
- API 行为测试通过注入假客户端（`analyzer` 接受 client 参数）覆盖：合法 JSON、非法 JSON、缺字段、超时、401、429；**不产生真实调用与费用**
- 界面关键流程以手动运行检查，汇报时注明「未自动化」

## 14. 协作节奏（澄清原文 §三 / §八）

- 每完成一个阶段，按原文模板汇报（完成内容 / 改动文件 / 你能看到什么 / 执行过的命令 / 测试通过-失败-未执行 / 已知问题 / 下一步），随后**继续下一阶段，不停工等确认**
- 只有以下情况先停下来问你：改项目位置或技术栈、需要真实 API Key 或产生费用、需要删除或覆盖已有文件、发现当前方案会让你返工
