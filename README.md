# Promo Scanner · 本机扫描工作台

**简体中文** | [English](README_EN.md)

为现有 ChatGPT Business 促销码扫描脚本提供一个中文前端面板：在页面配置连接、控制任务、查看实时执行数据和导出结果。

界面遵循 [Impeccable](https://github.com/pbakaus/impeccable) 的操作型界面指导。后端使用 FastAPI，前端使用原生 HTML/CSS/JavaScript，Python 依赖统一由 **uv + pyproject.toml + uv.lock** 管理，无需 Node 或前端构建步骤。

## 快速启动

需要 [uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Python 3.11+；仓库默认选择 Python 3.12。

```bash
git clone https://github.com/Rain-kl/gpt-promo-scanner.git
cd gpt-promo-scanner
uv sync --frozen
uv run python -m promo_dashboard
```

在本机浏览器输入 `http://127.0.0.1:8765`。更换端口可以运行：

```bash
uv run python -m promo_dashboard --port 8888
```

**首次使用无需改配置文件。** 可以先在工作台点击「预览候选」，无需 Token 或 Clash；需要真实扫描时，再进入「连接设置」填写凭据和代理并保存。

## 面板功能

| 页面 | 用途 |
|---|---|
| 扫描工作台 | 选择任务类型和地区、预览候选、开始任务、暂停/继续/停止 |
| 执行结果 / 运行日志 | 查看真实处理进度、可用/存在/异常/跳过统计、耗时、逐条结果和增量日志 |
| 任务记录 | 回看任务、结果和日志，删除已结束的记录 |
| 已知码库 | 搜索、按地区筛选并复制仓库历史参考码 |
| 连接设置 | 页面配置所有连接参数、任务默认值及候选来源 |

结果支持搜索、状态筛选、分页和 **JSON / CSV 导出**。导出遵循当前筛选条件；扫描成功生成的结账链接可从结果表中打开。

### 三种任务

- **候选发现**：组合公司基础名和国家后缀，调用 eligibility API 判断 `ELIGIBLE`、`EXISTS` 和 `NOT_FOUND`。英国同时尝试 `gb` / `uk`。
- **已知码扫描**：验证内置码和自定义码，可切换地区节点并收集折扣、估算价格和结账链接。
- **矩阵发现**：将公司名与所选地区交叉组合，同时尝试无后缀码。相同候选自动去重。

每种任务都可以「预览候选」；预览只生成待验证列表，不发送外部请求。发现和矩阵任务使用当前出站连接，地区用于生成候选后缀；需要切换地区节点验证时使用「已知码扫描」。单个任务上限为 5000 个候选，避免误启动过大的任务。

### 全部设置在页面完成

- ChatGPT Access Token：支持更新和显式清除；留空保留已有值。
- 出站代理：HTTP / HTTPS / SOCKS5；留空直连，面板设置优先于继承的代理环境变量。
- Clash：Unix Socket 或 HTTP API、API 密钥、Rule / Global 模式代理组、自动切换开关、各地区节点匹配关键字。
- 执行选项：请求间隔、请求超时、默认任务和地区、是否收集价格。
- 候选来源：内置公司基础名、内置公司列表、自定义完整候选码和基础名。

Token 可从登录后的 ChatGPT 会话页面获取。真实请求会验证 Token；认证失败、限流或连续连接异常会结束任务并显示原因，而不会把这些失败当成「促销码不存在」。

Clash 控制器需要能从**运行面板的机器**访问。候选发现和预览不要求控制器；已知码扫描关闭自动切换后也可以使用当前出站连接。「检查已保存的连接」验证控制器连通性，并显示 Token 是否配置；Token 有效性在真实任务开始前验证。

### 控制和数据保存

同一时间只执行一个任务，避免代理节点相互干扰。暂停会在当前请求结束后的检查点生效；停止会中断执行并保留已有结果。正常结束或取消时会尝试恢复原 Clash 节点；强制终止时应自行检查 Clash 当前节点。

配置、凭据、任务摘要、日志和增量结果保存在被 Git 忽略的 `.dashboard/`，数据库权限为 `0600`。凭据不会从配置接口回显，也会从任务日志、结果和导出中脱敏。凭据在本机数据库中保存，因此应保护工作目录和备份。

面板默认仅监听回环地址，包含来源检查和 Host 校验，适用于本机单用户。服务关闭会停止自己启动的执行进程；意外重启后，未结束任务标为「服务中断」，已经保存的结果仍可查看。

历史码库明确显示仓库数据的更新时间，**不表示当前仍然有效**。价格沿用原扫描逻辑的估算，包含汇率与基价假设；最终金额以结账页为准，面板不会自动完成付款。

## 开发与验证

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check promo_dashboard tests config.py
uv run ruff format --check promo_dashboard tests config.py
node --check promo_dashboard/static/app.js
```

浏览器验证使用 Chromium，只执行离线候选预览，不需要真实 Token：

```bash
# 浏览器驱动已在 dev 依赖中；首次使用可安装 Chromium
uv run playwright install chromium
uv run python tests/browser_smoke.py
```

浏览器脚本优先使用本机 Chromium，也支持 Playwright 安装的浏览器。验证覆盖页面配置、预览、筛选、导出、任务历史、暂停/继续/停止及手机宽度布局。截图保存在被忽略的 `test-artifacts/`。

原 CLI 保留，可通过 uv 调用；例如：

```bash
uv run python auto_scan.py --list
uv run python discover_codes.py --list
```

旧 CLI 的环境变量 / TOML 配置仍兼容；面板任务使用页面中保存的独立配置快照，修改设置只影响新任务。

## 主要文件

```text
pyproject.toml / uv.lock     Python 项目声明与可复现依赖
promo_dashboard/app.py      页面、配置和任务 API
promo_dashboard/manager.py  子进程生命周期与事件持久化
promo_dashboard/worker.py   扫描执行、进度事件、暂停检查点
promo_dashboard/storage.py 本机 SQLite 存储
promo_dashboard/static/    原生前端、样式、本地字体
config.py                  旧 CLI 兼容配置及面板运行时快照
auto_scan.py               已有结账/价格/Clash 扫描逻辑
discover_codes.py          已有候选生成逻辑
known_codes.json           仓库历史参考码库
tests/                     API、执行器及浏览器验证
```

[MIT](LICENSE) · Manrope 字体使用 [SIL Open Font License](promo_dashboard/static/fonts/OFL.txt)。
