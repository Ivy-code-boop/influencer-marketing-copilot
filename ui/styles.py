"""全局视觉样式（纯展示层，不含任何业务逻辑）。

约定：
- 所有 CSS 只放在这里，app.py 通过一次 st.markdown 注入；
- 选择器优先使用 Streamlit 的 data-testid，减少版本升级带来的影响；
- 只做视觉修饰，不改变页面结构与交互逻辑。
"""

GLOBAL_CSS = """
<style>
/* imc-global-css */
:root {
  --imc-bg: #F6F7F9;
  --imc-surface: #FFFFFF;
  --imc-border: #E6E8EF;
  --imc-text: #0F172A;
  --imc-muted: #64748B;
  --imc-primary: #4F46E5;
  --imc-primary-2: #7C3AED;
  --imc-radius-card: 12px;
  --imc-radius-control: 8px;
  --imc-shadow: 0 1px 2px rgba(15, 23, 42, .05), 0 1px 3px rgba(15, 23, 42, .04);
  --imc-shadow-hover: 0 6px 18px rgba(15, 23, 42, .08);
}

html, body, [class*="css"] {
  font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI",
               "PingFang SC", "Microsoft YaHei", sans-serif;
  color: var(--imc-text);
}

.stApp { background: var(--imc-bg); }

/* ---------- 页面骨架：最大宽度 1100px 居中 ---------- */
.block-container {
  max-width: 1100px;
  margin: 0 auto;
  padding: 2.2rem 2rem 4rem;
}
[data-testid="stSidebar"] .block-container {
  max-width: none;
  padding: 1rem 1rem 2rem;
}
header[data-testid="stHeader"] { background: transparent; }
#MainMenu, [data-testid="stMainMenu"], footer { display: none !important; }

/* ---------- 标题层级：收紧字号与行高 ---------- */
h1 { font-size: 26px !important; line-height: 1.25 !important; letter-spacing: -.02em; font-weight: 700; }
h2 { font-size: 25px !important; line-height: 1.28 !important; letter-spacing: -.02em; font-weight: 700; }
h3 { font-size: 17px !important; line-height: 1.40 !important; font-weight: 600; }
[data-testid="stSidebar"] h1 { font-size: 18px !important; letter-spacing: -.01em; }
p, li, label { font-size: 14.5px; line-height: 1.65; }
[data-testid="stCaptionContainer"] { color: var(--imc-muted); }

/* ---------- 卡片：12px 圆角 + 柔和阴影 ---------- */
[data-testid="stForm"],
[data-testid="stExpander"],
[data-testid="stMetric"],
[data-testid="stDataFrame"],
[data-testid="stJson"] {
  background: var(--imc-surface);
  border: 1px solid var(--imc-border);
  border-radius: var(--imc-radius-card);
  box-shadow: var(--imc-shadow);
}
[data-testid="stForm"] { padding: 1.1rem 1.15rem .4rem; }
[data-testid="stMetric"] { padding: 14px 16px; }
[data-testid="stMetricLabel"] { color: var(--imc-muted); font-size: 12px; }
[data-testid="stMetricValue"] { font-size: 26px; font-weight: 700; letter-spacing: -.02em; }
[data-testid="stExpander"] { overflow: hidden; }
[data-testid="stExpander"] details { border: none !important; background: transparent !important; }
[data-testid="stExpander"] summary { padding: .55rem .9rem; }
[data-testid="stDataFrame"] { overflow: hidden; padding: 2px; }
[data-testid="stJson"] { padding: .5rem .75rem; }

/* ---------- 按钮：8px 圆角 + hover 过渡 ---------- */
.stButton > button,
.stDownloadButton > button,
[data-testid^="stBaseButton"] {
  border-radius: var(--imc-radius-control) !important;
  border: 1px solid var(--imc-border);
  background: var(--imc-surface);
  color: var(--imc-text);
  font-weight: 600;
  box-shadow: var(--imc-shadow);
  transition: transform .15s ease, box-shadow .15s ease,
              border-color .15s ease, background .15s ease;
}
.stButton > button:hover,
.stDownloadButton > button:hover,
[data-testid^="stBaseButton"]:hover {
  border-color: #C9CDD8;
  box-shadow: var(--imc-shadow-hover);
  transform: translateY(-1px);
}
[data-testid="stBaseButton-primary"] {
  background: linear-gradient(135deg, var(--imc-primary) 0%, var(--imc-primary-2) 100%) !important;
  border: none !important;
  color: #FFFFFF !important;
}
[data-testid="stBaseButton-primary"]:hover { filter: brightness(1.06); }

/* ---------- 输入控件：focus 高亮 ---------- */
[data-baseweb="input"],
[data-baseweb="textarea"],
[data-baseweb="select"] > div {
  border-radius: var(--imc-radius-control) !important;
  border-color: var(--imc-border) !important;
  background: var(--imc-surface);
  transition: border-color .15s ease, box-shadow .15s ease;
}
[data-baseweb="input"]:focus-within,
[data-baseweb="textarea"]:focus-within,
[data-baseweb="select"]:focus-within > div {
  border-color: var(--imc-primary) !important;
  box-shadow: 0 0 0 3px rgba(79, 70, 229, .14) !important;
}
[data-testid="stFileUploaderDropzone"] {
  border-radius: var(--imc-radius-card);
  border: 1px dashed #C9CDD8;
  background: var(--imc-surface);
  transition: border-color .15s ease, background .15s ease;
}
[data-testid="stFileUploaderDropzone"]:hover {
  border-color: var(--imc-primary);
  background: #FBFAFF;
}

/* ---------- 提示条 ---------- */
[data-testid="stAlert"] {
  border-radius: 10px;
  border: 1px solid var(--imc-border);
  box-shadow: var(--imc-shadow);
}

/* ---------- 侧边栏 ---------- */
[data-testid="stSidebar"] {
  background: var(--imc-surface);
  border-right: 1px solid var(--imc-border);
}
[data-testid="stSidebar"] [role="radiogroup"] label {
  padding: .42rem .55rem;
  border-radius: var(--imc-radius-control);
  transition: background .15s ease, box-shadow .15s ease;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover { background: #F3F4F8; }
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
  background: linear-gradient(90deg, rgba(79, 70, 229, .10), rgba(124, 58, 237, .03));
  box-shadow: inset 3px 0 0 var(--imc-primary);
}
[data-testid="stSidebar"] hr { margin: .9rem 0; }

/* ---------- 细节 ---------- */
hr { border-color: var(--imc-border); opacity: .9; }
[data-testid="stTabs"] [data-baseweb="tab-highlight"] { background-color: var(--imc-primary); }
::selection { background: rgba(79, 70, 229, .18); }
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-thumb { background: #D7DAE3; border-radius: 999px; }
::-webkit-scrollbar-thumb:hover { background: #C4C8D4; }
</style>
"""
