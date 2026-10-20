# 部署指南：让面试官用一条链接打开

目标：把项目部署到 public URL，面试官点开就能用；你的电脑不需要开着。

推荐方案：**GitHub + Streamlit Community Cloud**（免费，原生支持 Streamlit，部署只要几分钟）。

---

## 0. 你需要先准备（只有你能做，涉及账号）

1. 一个 GitHub 账号（邮箱注册即可）：https://github.com/signup
2. 用同一个邮箱登录 https://share.streamlit.io ，授权 GitHub（会自动创建一个 Streamlit 账号）。
3. 决定 AI 环节怎么处理：

   - **方案 A**：把 DeepSeek Key 配到云端 Secrets，AI 环节真实可用。
     风险是任何拿到链接的人都能消耗你的额度，必须配合第 4 节的成本闸门。
   - **方案 B**：不配 Key。网站照常跑完整规则链路，AI 那一步明确显示「未配置」。
     零成本、零风险；缺点是看不到 AI 效果。

## 1. 先把本地仓库推到 GitHub

项目已经是 Git 仓库（本地提交已完成）。在你的 PowerShell 里执行：

```powershell
cd "D:\vibe coding\influencer-marketing-copilot"

# 先在 GitHub 网页上新建一个空仓库（不要勾选 README / .gitignore）
# 假设仓库名是 influencer-marketing-copilot，把地址换成你自己的
git remote add origin https://github.com/<你的用户名>/influencer-marketing-copilot.git
git branch -M main
git push -u origin main
```

推送时 GitHub 会要求登录，按提示在浏览器里授权即可（**不要把密码或 token 发给我**）。

## 2. 在 Streamlit Cloud 建应用

1. 打开 https://share.streamlit.io ，点 `Create app`
2. Repository 选你的仓库，Branch 选 `main`
3. Main file path 填 `app.py`
4. Python version 选 3.12
5. 点 `Deploy`

首次部署会安装 `requirements.txt`（已锁定版本），大约 2 到 5 分钟。

## 3. 配置密钥（对应方案 A）

进入应用页面：`Settings` → `Secrets`，粘贴：

```toml
DEEPSEEK_API_KEY = "sk-你的真实Key"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
AI_MAX_TOP_N = "5"
```

`AI_MAX_TOP_N` 会把界面上「分析排名前 N 名」的上限锁死（这里设为 5 条），
即使用户手动调大也超不过这个数。保存后应用会自动重启。
**不要把这些内容提交到 GitHub**；`.streamlit/secrets.toml` 已在 `.gitignore` 中。

## 4. 成本闸门（必看）

公网应用意味着任何人都能点「运行 AI 分析」。请至少做两件事：

1. 在 DeepSeek 后台给这个 Key 设置额度上限，或单独用一个金额很小的 Key。
2. 部署版把「分析前 N 名」调小（建议 3 到 5 条），面试演示足够说明问题。

如果不想承担任何费用，选方案 B：不配 Key，规则链路、人工审核、导出全部可用，AI 那一步显示「未配置」——这本身也能说明你考虑过成本与滥用问题。

## 5. 验收清单

* [ ] 打开公网链接，左上角显示 `版本 0.5.0`
* [ ] 加载演示数据 → 运行规则初筛 → 看到 30 条中 24 通过 / 2 需核实 / 4 排除
* [ ] 第 ④ 步：配了 Key 能跑前 3 到 5 名；没配 Key 显示「未配置」且不报错
* [ ] 第 ⑤ 步能逐条填写审核状态与备注
* [ ] 第 ⑥ 步两个下载按钮都能用，CSV 用 Excel 打开中文不乱码
* [ ] 面试官的电脑（不是你的电脑、不是你的网络）能打开

## 6. 常见问题

| 现象 | 原因与处理 |
|---|---|
| 部署日志里 `ModuleNotFoundError` | 检查 `requirements.txt` 是否在仓库根目录 |
| 页面报 `ImportError` 找不到某个名字 | 云端是全新进程，正常不会出现；若出现，点 `Reboot app` |
| 应用休眠后打开很慢 | 社区版长时间无访问会休眠，首次访问需要唤醒，属正常现象 |
| AI 报「鉴权失败」 | Secrets 里的 Key 写错或已失效；本地的 `.env` 不会上传，必须单独配 |
| CSV 中文乱码 | 导出使用 UTF-8 BOM；用记事本打开时请选择 UTF-8 |
