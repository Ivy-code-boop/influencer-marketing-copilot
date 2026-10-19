"""AI 海外达人营销助手（Influencer Marketing Copilot）— Streamlit 入口。

Phase 1 目标：不配置 API 也能完整跑通
「建任务 → 导数据 → 规则初筛 → 人工审核 → 导出」。
AI 语义分析（Phase 2）在此版本中明确标注为「未配置 / 未运行」，不用假结果顶替。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import pandas as pd
import streamlit as st

# Streamlit 只重跑主脚本，不会重新导入 src/ 下的模块。
# 改了 src/ 里的代码后，进程里仍是旧模块，轻则页面继续用旧逻辑，重则直接
# ImportError（例如给 models.py 新增了模型类）。这里在每次重跑脚本前把 src 包
# 从模块缓存里清掉，强制加载最新代码 —— 这样改完代码按 F5 就够了，不用重启服务。
# 测试环境跳过：pytest 会直接 import src.* 并做 monkeypatch，清缓存会让替换失效。
if os.environ.get("PYTEST_CURRENT_TEST") is None:
    for _stale_name in [
        name
        for name in list(sys.modules)
        if name == "src"
        or name.startswith("src.")
        or name == "ui"
        or name.startswith("ui.")
    ]:
        del sys.modules[_stale_name]

from src import ai_analyzer, ai_client, export_service, rule_engine, snapshot, validation
from src.data_loader import DEMO_TASK, load_demo_dataset, load_upload_dataset
from src.models import (
    GOAL_LABELS,
    REVIEW_STATUS_LABELS,
    VERDICT_LABELS,
    MarketingTask,
    ReviewDecision,
    RuleConfig,
)
from ui.styles import GLOBAL_CSS

st.set_page_config(page_title="AI 海外达人营销助手", layout="wide")

STEPS = ["① 任务配置", "② 达人数据", "③ 规则初筛", "④ AI 语义分析", "⑤ 人工审核", "⑥ 导出"]


def load_cloud_secrets() -> None:
    """Streamlit Community Cloud 把密钥放在 st.secrets 里，本地则用 .env。

    这里把 secrets 同步到环境变量，让 ai_client 的读取逻辑在两种环境一致。
    """
    secrets_file = os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml")
    if not os.path.exists(secrets_file):
        return
    try:
        for key in (
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_MODEL",
            "AI_MAX_TOP_N",
        ):
            if key in st.secrets and not os.environ.get(key):
                os.environ[key] = str(st.secrets[key])
    except Exception:
        # 读取 secrets 失败不应影响规则链路
        return


def max_top_n() -> int:
    """公网部署时用 AI_MAX_TOP_N 限制单次分析人数，避免链接被转发后产生意外费用。"""
    try:
        configured = int(os.environ.get("AI_MAX_TOP_N", ai_analyzer.MAX_TOP_N))
    except ValueError:
        configured = ai_analyzer.MAX_TOP_N
    return max(1, min(configured, ai_analyzer.MAX_TOP_N))


def init_state() -> None:
    st.session_state.setdefault("task", None)
    st.session_state.setdefault("dataset", None)
    st.session_state.setdefault("config", RuleConfig())
    st.session_state.setdefault("assessments", None)
    st.session_state.setdefault("decisions", {})
    st.session_state.setdefault("ai_records", {})
    st.session_state.setdefault("run_meta", None)
    st.session_state.setdefault("last_run_meta", None)


def current_fingerprint() -> dict:
    dataset = st.session_state.dataset
    return snapshot.current_fingerprint(
        dataset.creators if dataset else None,
        st.session_state.task,
        st.session_state.config,
        prompt_version=ai_analyzer.PROMPT_VERSION,
        model=st.session_state.get("ai_model_in_use") or os.environ.get("DEEPSEEK_MODEL"),
    )


def render_sidebar() -> str:
    st.sidebar.title("AI 海外达人营销助手")
    st.sidebar.caption(
        f"版本 {snapshot.APP_VERSION} · Phase 1：规则链路端到端（未接入 API 也能跑）"
    )
    step = st.sidebar.radio("流程", STEPS)
    st.sidebar.divider()
    if st.sidebar.button("清空当前会话"):
        for key in ("task", "dataset", "assessments", "decisions", "run_meta", "last_run_meta"):
            st.session_state.pop(key, None)
        st.rerun()
    with st.sidebar.expander("关于规则权重", expanded=False):
        st.write(st.session_state.config.weights_note)
        st.json(st.session_state.config.weights)
    with st.sidebar.expander("历史运行快照", expanded=False):
        runs = snapshot.list_runs()
        if not runs:
            st.caption("还没有运行记录。")
        for run in reversed(runs[-8:]):
            st.caption(
                f"{run.get('run_id')} · 数据 {run.get('data_row_count')} 条 · "
                f"data_hash {run.get('data_hash')} · 规则 {run.get('rule_version')}"
            )
    return step


def step_task() -> None:
    st.header("① 营销任务配置")
    st.caption("必填字段缺失会影响分析范围；可选字段留空只影响对应维度，不会阻止流程。")

    flash = st.session_state.pop("task_flash", None)
    if flash:
        level, payload = flash
        if level == "success":
            st.success(payload)
        else:
            st.error("保存失败：\n\n- " + "\n- ".join(payload))

    saved_task = st.session_state.task
    if saved_task:
        active = saved_task.hard_requirements()
        st.info(
            f"当前任务：{saved_task.product_name} · {GOAL_LABELS[saved_task.marketing_goal]} · "
            f"市场 {saved_task.target_market} · 平台 {saved_task.target_platform} · "
            f"硬性条件 {('、'.join(active)) if active else '未设置'}"
        )
        with st.expander("查看任务配置明细（含版本 hash）", expanded=False):
            st.json(saved_task.model_dump(mode="json"))
            st.caption(f"task_hash：{snapshot.task_hash(saved_task)}")
    else:
        st.warning("还没有保存任务配置。")

    preset = st.session_state.task or DEMO_TASK
    with st.form("task_form"):
        col1, col2 = st.columns(2)
        with col1:
            product_name = st.text_input("产品名称（必填）", value=preset.product_name)
            product_pitch = st.text_area("产品介绍 / 核心卖点（必填）", value=preset.product_pitch, height=100)
            target_market = st.text_input("目标市场（必填）", value=preset.target_market)
            target_platform = st.selectbox(
                "目标平台（必填）",
                ["TikTok", "Instagram", "YouTube"],
                index=max(0, ["TikTok", "Instagram", "YouTube"].index(preset.target_platform))
                if preset.target_platform in {"TikTok", "Instagram", "YouTube"}
                else 0,
            )
        with col2:
            goal_keys = list(GOAL_LABELS.keys())
            marketing_goal = st.selectbox(
                "营销目标（必填）",
                goal_keys,
                format_func=lambda k: GOAL_LABELS[k],
                index=goal_keys.index(preset.marketing_goal),
            )
            target_audience = st.text_input("目标受众（可选）", value=preset.target_audience or "")
            collaboration_type = st.text_input("合作方式（可选）", value=preset.collaboration_type or "")
            budget_range = st.text_input("预算范围（可选）", value=preset.budget_range or "")
        st.markdown("**硬性条件（可选，只做「通过 / 未通过 / 无法判定」的判定，不做加权）**")
        col3, col4, col5 = st.columns(3)
        with col3:
            min_followers = st.number_input(
                "最低粉丝量", min_value=0, value=int(preset.min_follower_count or 0), step=1000
            )
        with col4:
            required_language = st.text_input("内容语言要求", value=preset.required_language or "")
        with col5:
            required_market = st.text_input("目标地区要求", value=preset.required_market or "")
        submitted = st.form_submit_button("保存任务配置", type="primary")

    if submitted:
        errors = []
        if not product_name.strip():
            errors.append("产品名称为必填。")
        if not product_pitch.strip():
            errors.append("产品介绍 / 核心卖点为必填。")
        if not target_market.strip():
            errors.append("目标市场为必填。")
        if errors:
            st.session_state["task_flash"] = ("error", errors)
            st.rerun()
        new_task = MarketingTask(
            product_name=product_name.strip(),
            product_pitch=product_pitch.strip(),
            target_market=target_market.strip(),
            target_platform=target_platform,
            marketing_goal=marketing_goal,
            target_audience=target_audience.strip() or None,
            collaboration_type=collaboration_type.strip() or None,
            budget_range=budget_range.strip() or None,
            min_follower_count=int(min_followers) if min_followers else None,
            required_language=required_language.strip() or None,
            required_market=required_market.strip() or None,
        )
        st.session_state.task = new_task
        st.session_state["task_flash"] = (
            "success",
            f"任务配置已保存：{new_task.product_name} · 目标市场 {new_task.target_market} · "
            f"task_hash {snapshot.task_hash(new_task)}",
        )
        st.rerun()


def step_data() -> None:
    st.header("② 达人数据")
    tab_demo, tab_upload = st.tabs(["加载演示数据", "上传我的 CSV"])
    with tab_demo:
        st.caption("演示数据是项目内置的模拟数据，所有人名与指标均为虚构，不代表真实达人。")
        if st.button("加载演示数据（30 条）"):
            st.session_state.dataset = load_demo_dataset()
            st.session_state.assessments = None
            st.success("已加载演示数据。")
    with tab_upload:
        uploaded = st.file_uploader("选择 CSV 文件（UTF-8 优先，也兼容 GBK 等常见编码）", type=["csv"])
        if uploaded is not None and st.button("解析上传文件"):
            st.session_state.dataset = load_upload_dataset(uploaded.getvalue(), uploaded.name)
            st.session_state.assessments = None
            st.success("文件已解析，请查看下方校验结果。")

    dataset = st.session_state.dataset
    if not dataset:
        st.info("请先加载数据。")
        return

    summary = validation.summarize(dataset.report)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("有效记录", summary["row_count"])
    col2.metric("错误", summary["error_count"])
    col3.metric("警告", summary["warning_count"])
    col4.metric("编码", summary["encoding_used"] or "—")

    st.caption(f"数据来源：{dataset.source_name}（类型：{dataset.origin}）· data_hash：{snapshot.data_hash(dataset.creators)}")

    if not summary["can_continue"]:
        st.error("存在阻碍性问题，无法继续分析。请按下面的建议修正后重新上传。")
    elif summary["error_count"]:
        st.warning("存在问题记录。已被跳过的行不会进入分析，其余记录可以继续。")
    else:
        st.success("校验通过。缺失字段会在打分时被跳过，不会被当作 0 分。")

    issues = dataset.report.issues
    if issues:
        with st.expander(f"校验明细（{len(issues)} 条）", expanded=summary["error_count"] > 0):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "级别": i.level,
                            "行号": i.row if i.row is not None else "",
                            "字段": i.field or "",
                            "说明": i.message,
                            "建议": i.suggestion,
                        }
                        for i in issues
                    ]
                ),
                width="stretch",
            )

    st.markdown("**字段覆盖率**（判断数据是否足够支撑分析）")
    coverage = pd.DataFrame(validation.field_coverage(dataset.creators))
    coverage["coverage"] = coverage["coverage"].map(lambda v: f"{v:.0%}")
    st.dataframe(
        coverage.rename(
            columns={"field": "字段", "present": "非空条数", "total": "总条数", "coverage": "覆盖率"}
        ),
        width="stretch",
    )

    st.markdown("**数据预览（前 10 条）**")
    st.dataframe(pd.DataFrame(validation.preview_rows(dataset.creators)), width="stretch")


def step_rules() -> None:
    st.header("③ 规则初筛")
    task = st.session_state.task
    dataset = st.session_state.dataset
    if not task or not dataset:
        st.warning("请先完成任务配置并加载数据。")
        return

    config = st.session_state.config

    if st.button("运行规则初筛", type="primary", key="run_rules"):
        assessments = rule_engine.evaluate_dataset(dataset.creators, task, config)
        run_id = snapshot.new_run_id()
        meta = snapshot.build_meta(
            run_id=run_id,
            task=task,
            config=config,
            creators=dataset.creators,
            dataset_source=dataset.source_name,
            data_origin=dataset.origin,
        )
        rows = export_service.build_export_rows(assessments, dataset.creators, st.session_state.decisions)
        run_dir = snapshot.write_run(meta, rule_rows=rows, decisions={
            cid: d.model_dump() for cid, d in st.session_state.decisions.items()
        })
        had_ai = bool(st.session_state.get("ai_records"))
        st.session_state["ai_records"] = {}
        st.session_state.assessments = assessments
        st.session_state.run_meta = meta
        st.session_state.last_run_meta = snapshot.read_run(run_dir)
        flash = f"规则初筛完成，快照已写入 runtime/runs/{run_id}"
        if had_ai:
            flash += "。之前的 AI 结果属于上一次运行，已清空，需要时请重新分析"
        st.session_state["rules_flash"] = flash
        st.rerun()

    rules_flash = st.session_state.pop("rules_flash", None)
    if rules_flash:
        st.success(rules_flash)

    with st.expander("规则配置（权重为项目初始假设，可调整）", expanded=False):
        cols = st.columns(len(config.weights))
        new_weights = {}
        for col, (name, weight) in zip(cols, config.weights.items()):
            with col:
                new_weights[name] = st.number_input(
                    name, min_value=0.0, max_value=1.0, value=float(weight), step=0.05
                )
        if st.button("保存权重"):
            config.weights = new_weights
            st.session_state.config = config
            st.success("权重已更新（会改变 rule_config_hash）。")

    assessments = st.session_state.assessments
    if not assessments:
        st.info("还没有运行结果。")
        return

    meta = st.session_state.run_meta or {}
    stale = snapshot.staleness_reasons(meta, current_fingerprint()) if meta else []
    if stale:
        st.error("当前配置与这份结果不一致，结果已过期：\n\n- " + "\n- ".join(stale))

    counts = rule_engine.summarize_assessments(assessments)
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("候选总数", counts["total"])
    col2.metric("硬性条件通过", counts["qualified"])
    col3.metric("需人工核实", counts["needs_review"])
    col4.metric("已排除", counts["excluded"])
    col5.metric("无法判定", counts["unknown_hard_check"])
    st.caption(
        f"筛选前后：{counts['total']} 条 → 可排序候选 {counts['qualified']} 条"
        f"（另有 {counts['needs_review']} 条因数据缺失无法判定，未自动淘汰）。"
    )

    table = pd.DataFrame(
        [
            {
                "排名": a.rank if a.rank is not None else "—",
                "分组": {"qualified": "通过", "needs_review": "需核实", "excluded": "已排除"}[a.group],
                "达人": a.display_name,
                "creator_id": a.creator_id,
                "规则得分": round(a.weighted_score, 3) if a.weighted_score is not None else None,
                "信息完整度": f"{a.coverage:.0%}",
                "硬性判定": {"pass": "通过", "fail": "未通过", "unknown": "无法判定"}[a.hard_check.verdict],
            }
            for a in assessments
        ]
    )
    st.dataframe(table, width="stretch", hide_index=True)

    st.markdown("**逐条依据（可核查）**")
    for a in assessments:
        title = f"{a.display_name} · 得分 {a.weighted_score:.3f}" if a.weighted_score is not None else f"{a.display_name} · 信息不足"
        with st.expander(title):
            st.write("判定依据：")
            for reason in a.reasons:
                st.write(f"- {reason}")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "维度": d.label,
                            "得分": round(d.score, 3) if d.score is not None else "信息不足",
                            "权重": d.weight,
                            "使用字段": ", ".join(d.used_fields) or "—",
                            "方法": d.method,
                            "说明": d.detail,
                            "缺失": ", ".join(d.missing) or "—",
                        }
                        for d in a.dimensions
                    ]
                ),
                width="stretch",
                hide_index=True,
            )


def step_ai() -> None:
    st.header("④ AI 语义分析")
    task = st.session_state.task
    dataset = st.session_state.dataset
    assessments = st.session_state.assessments
    if not (task and dataset and assessments):
        st.warning("请先完成任务配置、加载数据并运行规则初筛。")
        return

    flash = st.session_state.pop("ai_flash", None)
    if flash:
        st.success(flash)

    override = st.session_state.get("ai_client_override")
    env_client = None if override is not None else ai_client.DeepSeekClient.from_env()
    if override is not None:
        client = override
        st.info(f"当前使用注入的测试客户端：{getattr(client, 'model', 'unknown')}（不会产生真实调用）")
    elif env_client is None:
        client = None
        st.warning(
            "未配置 DEEPSEEK_API_KEY：AI 语义分析暂不可用。\n\n"
            "- 规则初筛、人工审核、导出都不受影响，可以先继续用。\n"
            "- 配置方法：把 `.env.example` 复制为 `.env`，填入你自己的 Key"
            "（`.env` 已在 `.gitignore` 中，不会被提交）。\n"
            "- 没有 Key 时我不会用假结果顶替 AI 结论。"
        )
    else:
        client = env_client
        st.info(f"已检测到 API Key，模型：{client.model}")
    st.session_state["ai_model_in_use"] = getattr(client, "model", None)

    run_meta = st.session_state.run_meta or {}
    ai_fingerprint = snapshot.current_fingerprint(
        dataset.creators,
        task,
        st.session_state.config,
        prompt_version=ai_analyzer.PROMPT_VERSION,
        model=getattr(client, "model", None),
    )
    ai_stale = snapshot.staleness_reasons(run_meta, ai_fingerprint)
    if ai_stale and st.session_state.get("ai_records"):
        st.error(
            "当前配置与这份 AI 结果不一致，结果已过期，不能当作最新分析使用：\n\n- "
            + "\n- ".join(ai_stale)
        )

    qualified = [a for a in assessments if a.group == "qualified"]
    if not qualified:
        st.warning("没有通过硬性条件的候选，AI 环节没有可分析对象。")
        return
    if client is None:
        return

    by_id = {c.creator_id: c for c in dataset.creators}
    col1, col2 = st.columns([1, 2])
    with col1:
        top_n = st.number_input(
            "分析排名前 N 名",
            min_value=1,
            max_value=max_top_n(),
            value=min(ai_analyzer.DEFAULT_TOP_N, max_top_n(), len(qualified)),
            step=1,
        )
    with col2:
        force = st.checkbox("忽略缓存，强制重新分析", value=False)
        st.caption("AI 只做语义判断，不重算粉丝量、互动率等基础指标；每条结论都必须引用原始字段作为证据。")

    pairs = [
        (by_id[a.creator_id], a) for a in qualified[: int(top_n)] if a.creator_id in by_id
    ]
    cache = ai_analyzer.AnalysisCache()
    data_h = snapshot.data_hash(dataset.creators)
    task_h = snapshot.task_hash(task)
    estimate = ai_analyzer.estimate_requests(
        pairs, data_h, task_h, getattr(client, "model", "unknown"), cache
    )
    st.caption(
        f"候选 {estimate['candidates']} 条 · 命中缓存 {estimate['cached']} 条 · "
        f"预计实际调用 {estimate['to_call']} 次 · 模型 {estimate['model']} · "
        f"Prompt {estimate['prompt_version']}"
    )
    confirm = st.checkbox(
        f"我确认发起最多 {estimate['to_call']} 次调用（会消耗额度）", value=False
    )
    if st.button(
        "运行 AI 分析", type="primary", disabled=(not confirm) or not pairs
    ):
        with st.spinner("正在分析，请稍候…"):
            records = ai_analyzer.analyze_candidates(
                pairs,
                task,
                client,
                cache=cache,
                data_hash=data_h,
                task_hash_value=task_h,
                force=force,
            )
        st.session_state["ai_records"] = {r.creator_id: r for r in records}
        meta = st.session_state.run_meta
        if meta and meta.get("run_id"):
            run_dir = snapshot.RUNS_DIR / meta["run_id"]
            updated = snapshot.read_run(run_dir) or meta
            updated.update(
                {
                    "prompt_version": ai_analyzer.PROMPT_VERSION,
                    "model_requested": getattr(client, "model", None),
                    "model_returned": next(
                        (r.model_returned for r in records if r.model_returned), None
                    ),
                    "analysis_top_n": int(top_n),
                    "ai_run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "ai_enabled": True,
                }
            )
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "ai_results.json").write_text(
                json.dumps(
                    [r.model_dump(mode="json") for r in records], ensure_ascii=False, indent=2
                ),
                encoding="utf-8",
            )
            snapshot.write_run(updated)
            st.session_state.run_meta = updated
        ok = sum(1 for r in records if r.status == "ok")
        ins = sum(1 for r in records if r.status == "insufficient_info")
        failed = sum(1 for r in records if r.status == "failed")
        st.session_state["ai_flash"] = (
            f"AI 分析完成：已完成 {ok} · 信息不足 {ins} · 失败 {failed}"
            f"（失败与信息不足都如实记录，没有被伪造或丢弃）"
        )
        st.rerun()

    records = st.session_state.get("ai_records") or {}
    if not records:
        st.info("还没有 AI 结果。")
        return

    if estimate["candidates"] and len(records) != estimate["candidates"]:
        st.warning(
            "当前 AI 结果与规则候选数量不一致，可能规则配置已经改过，建议重新运行。"
        )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("已完成", sum(1 for r in records.values() if r.status == "ok"))
    c2.metric("信息不足", sum(1 for r in records.values() if r.status == "insufficient_info"))
    c3.metric("失败", sum(1 for r in records.values() if r.status == "failed"))
    c4.metric("来自缓存", sum(1 for r in records.values() if r.from_cache))

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "creator_id": cid,
                    "状态": export_service.AI_STATUS_LABELS.get(r.status, r.status)
                    if r.status != "failed"
                    else f"失败（{r.error_label}）",
                    "内容相关性": VERDICT_LABELS.get(r.analysis.content_relevance.verdict, "")
                    if r.analysis
                    else "",
                    "目标匹配": VERDICT_LABELS.get(r.analysis.goal_fit.verdict, "") if r.analysis else "",
                    "受众匹配": VERDICT_LABELS.get(r.analysis.audience_fit.verdict, "")
                    if r.analysis
                    else "",
                    "置信度": r.analysis.confidence if r.analysis else "",
                    "证据问题": len(r.evidence_problems),
                    "可疑数字": len(r.numeric_conflicts),
                }
                for cid, r in records.items()
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.markdown("**逐条结论与证据**")
    for cid, record in records.items():
        title = f"{cid} · " + (
            export_service.AI_STATUS_LABELS.get(record.status, record.status)
            if record.status != "failed"
            else f"失败（{record.error_label}）"
        )
        with st.expander(title):
            if record.status == "failed":
                st.error(f"{record.error_label}：{record.error_message}")
            if record.analysis is not None:
                analysis = record.analysis
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "维度": label,
                                "判断": VERDICT_LABELS[item.verdict],
                                "理由": item.reason,
                                "证据": "；".join(
                                    f"{e.field}：{e.quote[:40]}" for e in item.evidence
                                )
                                or "—",
                            }
                            for label, item in (
                                ("内容相关性", analysis.content_relevance),
                                ("目标匹配", analysis.goal_fit),
                                ("受众匹配", analysis.audience_fit),
                            )
                        ]
                    ),
                    width="stretch",
                    hide_index=True,
                )
                for reason in analysis.recommendation_reasons:
                    st.write(f"- 推荐理由：{reason}")
                for item in analysis.mismatches:
                    st.write(f"- 不匹配：{item}")
                for risk in analysis.risks:
                    st.write(f"- 风险〔{risk.type}〕{risk.detail}")
                if analysis.missing_info:
                    st.write("- 缺失信息：" + "；".join(analysis.missing_info))
                if analysis.human_checks:
                    st.write("- 需人工核实：" + "；".join(analysis.human_checks))
            if record.evidence_problems:
                st.warning("证据无法核对：\n\n- " + "\n- ".join(record.evidence_problems))
            if record.numeric_conflicts:
                st.warning("出现输入里没有的数字：\n\n- " + "\n- ".join(record.numeric_conflicts))
            if record.from_cache:
                st.caption("这条结论来自缓存，没有重复调用模型。")

            if st.button("重新分析这一条（忽略缓存）", key=f"retry_{cid}"):
                assessment = next((a for a in assessments if a.creator_id == cid), None)
                if assessment is not None and cid in by_id:
                    with st.spinner(f"正在重新分析 {cid}…"):
                        retried = ai_analyzer.analyze_one(
                            by_id[cid],
                            assessment,
                            task,
                            client,
                            cache=cache,
                            data_hash=data_h,
                            task_hash_value=task_h,
                            force=True,
                        )
                    st.session_state["ai_records"][cid] = retried
                    st.session_state["ai_flash"] = (
                        f"{cid} 已重新分析："
                        + export_service.AI_STATUS_LABELS.get(retried.status, retried.status)
                    )
                    st.rerun()


def step_review() -> None:
    st.header("⑤ 人工审核")
    assessments = st.session_state.assessments
    if not assessments:
        st.warning("请先运行规则初筛。")
        return
    st.caption("人工决定与规则/AI 结果分开保存：修改人工状态不会覆盖规则或 AI 的原始输出。")

    flash = st.session_state.pop("review_flash", None)
    if flash:
        st.success(flash)

    # 「应用并跳到下一位」不能直接改 Selectbox 的值（控件创建后禁止修改），
    # 先存进另一个 key，等下一轮在控件创建之前再写进去。
    pending_select = st.session_state.pop("review_pending_select", None)
    if pending_select is not None:
        st.session_state["review_selected"] = pending_select

    reviewable = [a for a in assessments if a.group != "excluded"]
    excluded_count = len(assessments) - len(reviewable)
    if not reviewable:
        st.warning("没有可审核的候选（全部被硬性条件排除）。")
        return
    by_id = {a.creator_id: a for a in reviewable}

    col_btn, col_hint = st.columns([1, 3])
    with col_btn:
        save_clicked = st.button("保存人工审核结果", type="primary")
    with col_hint:
        decided = sum(
            1
            for a in reviewable
            if st.session_state.decisions.get(a.creator_id)
            and st.session_state.decisions[a.creator_id].status != "pending"
        )
        st.caption(
            f"待审核 {len(reviewable) - decided} 条 · 已给结论 {decided} 条"
            + (f" · 已排除的 {excluded_count} 条不在本列表" if excluded_count else "")
            + "。保存后写入本次运行的 decisions.json，与规则结果分开存放。"
        )

    st.divider()
    st.markdown("**逐条审核**")
    st.caption(
        "选一位达人 → 选审核状态 → 填写备注 → 点「应用到这位达人」。"
        "为避免表格内联编辑在滚动时错位，这里改成一条一条地填，填完的记录会自动出现在下方进度表里。"
    )

    ids = [a.creator_id for a in reviewable]
    label_to_id = {f"{by_id[cid].display_name} · {cid}": cid for cid in ids}
    labels = list(label_to_id.keys())
    if st.session_state.get("review_selected") not in labels:
        st.session_state["review_selected"] = labels[0]

    selected_label = st.selectbox("要审核的达人", labels, key="review_selected")
    selected = label_to_id[selected_label]
    current = by_id[selected]
    decision = st.session_state.decisions.get(
        selected, ReviewDecision(creator_id=selected)
    )

    col_status, col_note = st.columns([1, 2])
    with col_status:
        status_label = st.selectbox(
            "审核状态",
            list(REVIEW_STATUS_LABELS.values()),
            index=list(REVIEW_STATUS_LABELS.values()).index(
                REVIEW_STATUS_LABELS[decision.status]
            ),
            key=f"status_{selected}",
        )
    with col_note:
        note = st.text_input("备注（可选）", value=decision.note, key=f"note_{selected}")

    with st.expander(f"查看 {current.display_name} 的规则依据", expanded=False):
        st.caption(f"规则得分 {current.weighted_score if current.weighted_score is not None else '信息不足'}"
                   f" · 信息完整度 {current.coverage:.0%}")
        for reason in current.reasons:
            st.write(f"- {reason}")
        st.divider()
        ai_record = (st.session_state.get("ai_records") or {}).get(current.creator_id)
        if ai_record is None:
            st.caption("AI 分析：未运行")
        elif ai_record.analysis is None:
            st.caption(f"AI 分析：失败（{ai_record.error_label}）—— 不是结论，请勿据此决策")
        else:
            analysis = ai_record.analysis
            st.write(
                f"AI 判断：内容相关性 {VERDICT_LABELS[analysis.content_relevance.verdict]} · "
                f"目标匹配 {VERDICT_LABELS[analysis.goal_fit.verdict]} · "
                f"受众匹配 {VERDICT_LABELS[analysis.audience_fit.verdict]}"
                f"（置信度 {analysis.confidence}）"
            )
            for reason in analysis.recommendation_reasons:
                st.write(f"- {reason}")
            for risk in analysis.risks:
                st.write(f"- 风险〔{risk.type}〕{risk.detail}")
            if ai_record.evidence_problems:
                st.warning("这条 AI 结论有证据无法核对，请谨慎采用。")

    col_apply, col_next = st.columns([1, 1])
    with col_apply:
        apply_clicked = st.button("应用到这位达人")
    with col_next:
        apply_next_clicked = st.button("应用并跳到下一位")

    if apply_clicked or apply_next_clicked:
        code = {v: k for k, v in REVIEW_STATUS_LABELS.items()}.get(status_label, "pending")
        st.session_state.decisions[selected] = ReviewDecision(
            creator_id=selected, status=code, note=note
        )
        st.session_state["review_flash"] = (
            f"已记录 {current.display_name}：{status_label}"
            + (f"（备注：{note}）" if note else "")
        )
        if apply_next_clicked:
            idx = labels.index(selected_label)
            st.session_state["review_pending_select"] = labels[(idx + 1) % len(labels)]
        st.rerun()

    rows = []
    for a in reviewable:
        d = st.session_state.decisions.get(a.creator_id)
        rows.append(
            {
                "达人": a.display_name,
                "creator_id": a.creator_id,
                "分组": {"qualified": "通过", "needs_review": "需核实"}[a.group],
                "规则得分": round(a.weighted_score, 3) if a.weighted_score is not None else None,
                "审核状态": REVIEW_STATUS_LABELS[d.status] if d else "待审核",
                "备注": d.note if d else "",
            }
        )
    st.markdown("**审核进度（只读）**")
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    if save_clicked:
        decisions = {
            cid: d for cid, d in st.session_state.decisions.items() if cid in by_id
        }
        st.session_state.decisions = decisions
        meta = st.session_state.run_meta
        target = ""
        if meta and meta.get("run_id"):
            run_dir = snapshot.RUNS_DIR / meta["run_id"]
            snapshot.write_run(
                snapshot.read_run(run_dir) or meta,
                decisions={cid: d.model_dump() for cid, d in decisions.items()},
            )
            target = f"（写入 runtime/runs/{meta['run_id']}/decisions.json）"
        st.session_state["review_flash"] = f"已保存 {len(decisions)} 条人工审核结果{target}"
        st.rerun()


def step_export() -> None:
    st.header("⑥ 导出")
    assessments = st.session_state.assessments
    dataset = st.session_state.dataset
    meta = st.session_state.run_meta
    if not assessments or not dataset or not meta:
        st.warning("请先运行规则初筛。")
        return

    stale_reasons = snapshot.staleness_reasons(meta, current_fingerprint())
    rows = export_service.build_export_rows(
        assessments,
        dataset.creators,
        st.session_state.decisions,
        st.session_state.get("ai_records") or {},
        result_is_stale=bool(stale_reasons),
    )
    csv_text = export_service.rows_to_csv_text(rows)
    export_meta = export_service.build_export_meta(
        {**meta, "staleness_reasons": stale_reasons},
        rows,
    )

    if stale_reasons:
        st.error(
            "这份结果已过期（导出文件会标注 result_is_stale=是）：\n\n- "
            + "\n- ".join(stale_reasons)
        )

    st.caption(
        "导出包含全部记录（含无法判定与信息不足），并附带元数据文件说明数据来源、时间与版本。"
    )
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "下载结果 CSV",
            data=csv_text.encode("utf-8-sig"),
            file_name=f"results_{meta['run_id']}.csv",
            mime="text/csv",
        )
    with col2:
        st.download_button(
            "下载元数据 JSON",
            data=json.dumps(export_meta, ensure_ascii=False, indent=2).encode("utf-8"),
            file_name=f"results_{meta['run_id']}.meta.json",
            mime="application/json",
        )

    with st.expander("导出预览（前 5 行）", expanded=True):
        st.dataframe(pd.DataFrame(rows).head(5), width="stretch", hide_index=True)
    with st.expander("元数据内容"):
        st.json(export_meta)


def main() -> None:
    load_cloud_secrets()
    # 全局视觉层：所有 CSS 集中在 ui/styles.py，这里只注入一次
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)
    init_state()
    step = render_sidebar()
    if step == STEPS[0]:
        step_task()
    elif step == STEPS[1]:
        step_data()
    elif step == STEPS[2]:
        step_rules()
    elif step == STEPS[3]:
        step_ai()
    elif step == STEPS[4]:
        step_review()
    else:
        step_export()
    st.divider()
    st.caption(
        "AI 语义分析尚未接入（Phase 2）。当前版本所有结论均由确定性规则产生，"
        "内容相关性仅为词面/类目匹配，不代表语义判断。"
    )


if __name__ == "__main__":
    main()
