"""AI 海外达人营销助手（Influencer Marketing Copilot）— Streamlit 入口。

Phase 1 目标：不配置 API 也能完整跑通
「建任务 → 导数据 → 规则初筛 → 人工审核 → 导出」。
AI 语义分析（Phase 2）在此版本中明确标注为「未配置 / 未运行」，不用假结果顶替。
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src import export_service, rule_engine, snapshot, validation
from src.data_loader import DEMO_TASK, load_demo_dataset, load_upload_dataset
from src.models import (
    GOAL_LABELS,
    REVIEW_STATUS_LABELS,
    MarketingTask,
    ReviewDecision,
    RuleConfig,
)

st.set_page_config(page_title="AI 海外达人营销助手", layout="wide")

STEPS = ["① 任务配置", "② 达人数据", "③ 规则初筛", "④ 人工审核", "⑤ 导出"]


def init_state() -> None:
    st.session_state.setdefault("task", None)
    st.session_state.setdefault("dataset", None)
    st.session_state.setdefault("config", RuleConfig())
    st.session_state.setdefault("assessments", None)
    st.session_state.setdefault("decisions", {})
    st.session_state.setdefault("run_meta", None)
    st.session_state.setdefault("last_run_meta", None)


def current_fingerprint() -> dict:
    task = st.session_state.task
    dataset = st.session_state.dataset
    config = st.session_state.config
    return {
        "data_hash": snapshot.data_hash(dataset.creators) if dataset else None,
        "task_hash": snapshot.task_hash(task) if task else None,
        "rule_config_hash": snapshot.rule_config_hash(config),
        "prompt_version": None,
        "model_requested": None,
    }


def render_sidebar() -> str:
    st.sidebar.title("AI 海外达人营销助手")
    st.sidebar.caption("Phase 1：规则链路端到端（未接入 API 也能跑）")
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
                use_container_width=True,
            )

    st.markdown("**字段覆盖率**（判断数据是否足够支撑分析）")
    coverage = pd.DataFrame(validation.field_coverage(dataset.creators))
    coverage["coverage"] = coverage["coverage"].map(lambda v: f"{v:.0%}")
    st.dataframe(coverage.rename(columns={"field": "字段", "present": "非空条数", "total": "总条数", "coverage": "覆盖率"}), use_container_width=True)

    st.markdown("**数据预览（前 10 条）**")
    st.dataframe(pd.DataFrame(validation.preview_rows(dataset.creators)), use_container_width=True)


def step_rules() -> None:
    st.header("③ 规则初筛")
    task = st.session_state.task
    dataset = st.session_state.dataset
    if not task or not dataset:
        st.warning("请先完成任务配置并加载数据。")
        return

    config = st.session_state.config
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

    if st.button("运行规则初筛", type="primary"):
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
        st.session_state.assessments = assessments
        st.session_state.run_meta = meta
        st.session_state.last_run_meta = snapshot.read_run(run_dir)
        st.success(f"规则初筛完成，快照已写入 {run_dir}")

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
    st.dataframe(table, use_container_width=True, hide_index=True)

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
                use_container_width=True,
                hide_index=True,
            )


def step_review() -> None:
    st.header("④ 人工审核")
    assessments = st.session_state.assessments
    if not assessments:
        st.warning("请先运行规则初筛。")
        return
    st.caption("人工决定与规则/AI 结果分开保存：修改人工状态不会覆盖规则或 AI 的原始输出。")

    flash = st.session_state.pop("review_flash", None)
    if flash:
        st.success(flash)

    label_to_code = {v: k for k, v in REVIEW_STATUS_LABELS.items()}
    reviewable = [a for a in assessments if a.group != "excluded"]
    excluded_count = len(assessments) - len(reviewable)

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

    editor_rows = []
    for a in reviewable:
        decision = st.session_state.decisions.get(
            a.creator_id, ReviewDecision(creator_id=a.creator_id)
        )
        editor_rows.append(
            {
                "达人": a.display_name,
                "creator_id": a.creator_id,
                "分组": {"qualified": "通过", "needs_review": "需核实"}[a.group],
                "规则得分": round(a.weighted_score, 3) if a.weighted_score is not None else None,
                "审核状态": REVIEW_STATUS_LABELS[decision.status],
                "备注": decision.note,
            }
        )
    edited = st.data_editor(
        pd.DataFrame(editor_rows),
        key="review_editor",
        hide_index=True,
        use_container_width=True,
        disabled=["达人", "creator_id", "分组", "规则得分"],
        column_config={
            "审核状态": st.column_config.SelectboxColumn(
                "审核状态",
                help="人工结论，与规则/AI 结果分开保存。",
                options=list(REVIEW_STATUS_LABELS.values()),
                required=True,
            ),
            "备注": st.column_config.TextColumn("备注", help="人工备注，会一并导出。", width="large"),
        },
    )

    if save_clicked:
        decisions: dict[str, ReviewDecision] = {}
        for row in edited.to_dict("records"):
            creator_id = str(row["creator_id"])
            decisions[creator_id] = ReviewDecision(
                creator_id=creator_id,
                status=label_to_code.get(str(row["审核状态"]), "pending"),
                note=str(row.get("备注") or ""),
            )
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
    st.header("⑤ 导出")
    assessments = st.session_state.assessments
    dataset = st.session_state.dataset
    meta = st.session_state.run_meta
    if not assessments or not dataset or not meta:
        st.warning("请先运行规则初筛。")
        return

    rows = export_service.build_export_rows(assessments, dataset.creators, st.session_state.decisions)
    csv_text = export_service.rows_to_csv_text(rows)
    export_meta = export_service.build_export_meta(meta, rows)

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
        st.dataframe(pd.DataFrame(rows).head(5), use_container_width=True, hide_index=True)
    with st.expander("元数据内容"):
        st.json(export_meta)


def main() -> None:
    init_state()
    step = render_sidebar()
    if step == STEPS[0]:
        step_task()
    elif step == STEPS[1]:
        step_data()
    elif step == STEPS[2]:
        step_rules()
    elif step == STEPS[3]:
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
