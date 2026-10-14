# Phase 0：环境检查与项目设计

检查时间：2026-10-13（本地时区 Asia/Shanghai）
检查方式：直接在 `D:\vibe coding` 上执行命令与写入测试，结果如下。

## 1. 环境检查结果（实测）

| 项目 | 结果 | 说明 |
|---|---|---|
| 项目根目录 | 可用 | `D:\vibe coding`，已实测写入成功（测试文件已删除） |
| 已有文件 | 仅 1 个网页快照 | `AI达人营销助手开发 - DeepSeek.html` + `_files\`，不含任何项目代码，不会被覆盖 |
| Python | **未安装** | PATH 中无 `python` / `py`；注册表 `HKLM/HKCU\SOFTWARE\Python\PythonCore` 无记录；`C:\Program Files\Python3xx`、`%LOCALAPPDATA%\Programs\Python` 均不存在 |
| 依赖 | **全部缺失** | streamlit / pytest / openai 都未安装 |
| Git | 已安装，未初始化仓库 | git 2.53.0 可用；`D:\vibe coding` 不是 Git 仓库 |
| DeepSeek API Key | 未配置 | 环境变量 `DEEPSEEK_API_KEY` 未设置。按原指令要求，未配置时规则链路仍必须完整跑通 |
| 网络 | 受限（我的执行环境） | pip 安装与真实 API 调用需要你在本地终端执行，或单独授权我执行 |

两点诚实说明（避免虚构检查结果）：

1. 我的命令运行在受限 shell 中，`%LOCALAPPDATA%\Microsoft\WindowsApps` 无读取权限，因此理论上存在极少数情况：你装过 Microsoft Store 版 Python。请你在自己的 PowerShell 里执行一次 `python --version` 确认。
2. 我的执行环境里有一个自带的临时 Python 3.12.14（内含 pandas 3.0.1、pydantic 2.13.5、numpy）。它可以用来验证纯逻辑代码（数据解析、规则计算、导出），但它属于 Codex 运行时，**不是你项目的环境**，不作为交付环境。

## 2. 推荐技术架构及理由

```text
influencer-marketing-copilot/
├── app.py                  # Streamlit 入口：页面路由与状态编排
├── pages/                  # 各页面：任务 / 数据 / 规则 / AI / 审核 / 导出 / 评估
├── src/
│   ├── models.py           # Pydantic 模型：达人、营销任务、AI 输出 schema
│   ├── data_loader.py      # 演示数据与上传 CSV 的读取、编码与格式解析
│   ├── validation.py       # 字段映射、校验报告、可修复问题提示
│   ├── rule_engine.py      # 硬性过滤（含无法判定）+ 软性打分 + 评分明细
│   ├── ai_client.py        # DeepSeek OpenAI 兼容客户端：超时/重试/错误分类
│   ├── ai_analyzer.py      # 组装 prompt、调用、schema 校验、缓存去重
│   ├── snapshot.py         # run 目录、hash、版本信息、过期判定
│   ├── export_service.py   # 结果 CSV + 元数据 JSON 双文件导出
│   └── evaluation.py       # 离线评估：方案 A / B 对比与报告
├── prompts/creator_analysis_v1.md
├── data/
│   ├── demo/demo_creators.csv             # 30 条模拟数据（明确标注为模拟）
│   └── evaluation/evaluation_cases.json   # 12–20 个固定评估案例（与演示数据分离）
├── runtime/                # 运行态：runs/ 快照、uploads/ 用户数据（.gitignore）
├── tests/
├── docs/
└── requirements.txt / .env.example / .gitignore / README.md
```

关键选型理由：

- **单进程 Streamlit**：原指令要求「轻量业务工具」而非前后端分离系统。Streamlit 能在一份代码里承载表单、数据预览、表格、详情展开与人工审核，Windows 上一条命令启动，符合「初级编程基础 + 几分钟内可演示」的约束。
- **规则层与 AI 层物理分离**（`rule_engine` 与 `ai_analyzer` 分开）：确定性计算不进大模型，是原指令 4.3 的硬要求，也是面试最容易被追问的设计点——AI 不合适做加减乘除，规则也不合适做语义判断。
- **Pydantic 卡在两端边界**：CSV 进来的原始数据和 AI 返回的 JSON 走同一套校验，保证「非法格式不会变成看起来可信的结论」。
- **本地文件而非数据库**：原指令 5.2 明确第一版允许轻量文件管理；`runtime/runs/<run_id>/` 天然形成快照，你可以直接用记事本打开核查，也避免为了版本管理引入数据库。
- **API Key 只在服务端进程内读取**：key 只从本机 `.env` 读取，不进入前端状态、日志和导出文件。
- **唯一事实来源是落盘的 `meta.json`**：Streamlit 的 `session_state` 只做页面缓存，避免「界面上显示的是旧结果却以为是最新」这类隐蔽错误。

## 3. MVP 实现顺序（Phase 1 内部步骤，端到端优先）

1. 项目骨架 + `requirements.txt` + `.env.example` + `.gitignore` + 最小 README（能启动、能跑测试）
2. `models.py`：达人记录、营销任务、AI 输出三组 Pydantic 模型
3. 演示数据：先写 12 条覆盖典型类别与缺字段样例，跑通链路后补齐到 30 条
4. `data_loader.py` + `validation.py`：编码、百分比、K/M、空值、重复 ID、字段映射与校验报告
5. `rule_engine.py`：硬性过滤（含「无法判定」）、软性加权（缺失不惩罚）、逐条原因与明细
6. `app.py` 页面①②③：任务配置 → 数据预览与校验 → 规则初筛结果（含筛选前后数量变化）
7. 人工审核状态与备注 + `snapshot.py` 生成 run 快照
8. `export_service.py` 双文件导出；随后补 `tests/`，覆盖原指令 §八 的第 1–5、11–13 项

## 4. 各阶段验收标准（可执行、可核查）

| 阶段 | 验收标准 |
|---|---|
| Phase 1 | 不配 API 也能完整跑通「建任务 → 导数据 → 看筛选 → 人工标记 → 导出」；缺失数据不被当 0 分；导出含数据来源与版本信息；pytest 的通过项、失败项、未执行项如实列出 |
| Phase 2 | AI 输出经 Pydantic 校验；非法 JSON / 超时 / 限流分别有可理解的提示；单条重试可用；失败不伪造结果；每条结论带 evidence 字段引用 |
| Phase 3 | 改数据 / 规则 / Prompt 后旧结果被标记「已过期」；meta 字段真实记录（model 以 API 响应为准）；缓存命中不重复计费 |
| Phase 4 | 评估脚本可重复执行并输出真实通过 / 失败数；A/B 两组指标（增量信号 + 新增错误）都有具体案例；报告写明标签来源与局限 |
| Phase 5 | README 能让新人按 Windows 步骤跑起来；面试提纲区分「已实现 / 已验证 / 规划」 |

## 5. 关键风险与应对

| # | 风险 | 后果 | 应对 |
|---|---|---|---|
| 1 | 本机没有 Python | 所有代码无法运行验证 | Phase 1 前先安装 Python 3.12（命令见 §7）；在你确认环境可用前，我不批量堆砌无法验证的代码 |
| 2 | 我的执行环境网络受限 | 装依赖、真调 API 我做不了 | 依赖由你本地安装；AI 逻辑先用假客户端验证，真实调用单独进行 |
| 3 | API Key 未配置 | AI 环节不可用 | 规则链路独立可跑；AI 环节显式显示「未配置」，不使用假结果顶替 |
| 4 | 调用费用失控 | 意外扣费 | 默认只分析 Top 10 + 运行前显示预计调用次数 + 二次确认 + 缓存去重 + 单次上限 30 条 |
| 5 | 幻觉与证据不足 | 业务误导 | schema 强制 evidence 字段引用 + `insufficient_info` 枚举 + `data_conflicts` 冲突标记 |
| 6 | 缺失数据被当 0 分 | 错误淘汰好达人 | 按实际参与维度归一化 + 信息完整度列 + 「无法判定」分组不自动淘汰 |
| 7 | 评估标签不独立 | 结论不可信 | 标签标注为「AI 起草 + 人工确认的项目内假设」，报告写明局限 |
| 8 | Streamlit 状态与落盘快照不一致 | 误用过期结果 | 落盘 `meta.json` 为唯一事实来源，`session_state` 仅做缓存 |
| 9 | 首版范围蔓延 | MVP 做不完 | 严守原指令 §二 的不做清单；每加一个功能先检查与前后环节是否兼容 |
| 10 | 路径含空格与中文 | 命令容易出错 | 所有命令用引号包裹路径；项目内的文件名全部使用 ASCII |

## 6. 待确认的默认假设（不改就按此执行）

1. 项目根目录 = `D:\vibe coding\influencer-marketing-copilot`
2. Python 3.12 + Streamlit + pandas + Pydantic v2 + pytest + `openai` SDK（走 DeepSeek 兼容接口）
3. 界面中文、业务术语英文、数据字段英文小写下划线
4. AI 候选池默认 Top 10，单次上限 30
5. 软性权重：内容相关性 30% / 目标市场 20% / 互动表现 20% / 受众匹配 15% / 合作可行性 15%，标注为项目初始假设
6. 互动率缺失时降级使用 `average_views / follower_count`，并在数据中标明口径
7. 导出为 CSV + meta.json 双文件，CSV 使用 UTF-8 BOM
8. 是否执行 `git init` 初始化仓库（建议做，等你同意）

## 7. 下一步要执行的命令（Windows PowerShell）

```powershell
# 1) 安装 Python（若系统有 winget）
winget install -e --id Python.Python.3.12

# 若没有 winget：到 https://www.python.org/downloads/ 下载 3.12 安装包，
# 安装时务必勾选 "Add python.exe to PATH"

# 2) 新开一个 PowerShell 窗口，确认安装成功
python --version
```

环境就绪后，Phase 1 的启动方式（骨架建好后即可使用）：

```powershell
cd "D:\vibe coding\influencer-marketing-copilot"
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt

# 界面
streamlit run app.py

# 测试
python -m pytest -q
```
