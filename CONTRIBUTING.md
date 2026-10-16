# 开发与贡献

项目按 [MIT](LICENSE) 公开维护，欢迎可复现问题和范围清楚的 PR。先读 [AGENTS.md](AGENTS.md) 和 [源码与私有数据边界](docs/source-boundaries.md)，再按本文建立开发环境。产品操作见 [工作台使用指南](docs/workspace-guide.md)，已发布安装包和验证限制见 [桌面交付](docs/desktop-delivery.md)。

## 环境安装与测试入口

已验证的源码开发基线为 macOS arm64、Python 3.11、Node 22.22.1 与 npm 10.9.4。完整 Python 回归入口也需要 Node，用于合成导航检查。Windows 原生构建使用独立锁文件和入口，见 [可重复 Windows 构建与验证](docs/desktop-delivery.md#可重复-windows-构建与验证)。日常改动按下文选择受影响层的最小充分验证。

```sh
uv venv --python 3.11
uv pip sync requirements-dev.lock
```

锁文件包含精确版本和分发文件哈希；`requirements.txt` 是生产依赖输入，`requirements-dev.in` 增加测试工具。不使用 uv 时，先用 Python 3.11 创建 `.venv`，再在该环境执行 `python -m pip install --require-hashes -r requirements-dev.lock`。

```sh
# 完整回归
.venv/bin/python scripts/test.py

# 定向回归，可追加 pytest 参数
.venv/bin/python scripts/test.py tests/test_cases.py -q --tb=short
```

Windows 使用 `.venv\Scripts\python scripts\test.py`。入口强制使用临时目录、合成交易、合成许可和进程内 API 客户端，不读取已有服务地址；每个测试使用独立 DuckDB。不要复用原目录的 `dbg_*`、`patch_*` 或恢复脚本。

前端版本锁在 `frontend/package-lock.json`。`frontend/.npmrc` 固定安装选项并禁用依赖安装脚本，当前构建不依赖安装后脚本，无需改变全局 npm 配置。

```sh
cd frontend
npm ci
npm run build
npm test
cd ..
.venv/bin/python scripts/export_api_contract.py --check
```

`build` 同时执行 TypeScript 类型检查和 Vite 编译。前端目录中的 `npm run dev` 通过 Vite 代理连接 `127.0.0.1` 的隔离 FastAPI，默认端口 8300；可在启动前将 `FUNDS_TRACE_DEV_API_PORT` 设为合成后端打印的端口，仅接受 1–65535 的本机端口。代理覆盖 G6 根页和 `/analysis`，独立导航使用相对路径；不要指向业务服务。

## 合成界面与本地启动

完成 React 构建后，用一次性数据预览：

```sh
.venv/bin/python scripts/preview_synthetic.py
```

入口覆盖继承的数据目录与签名设置，使用公开合成许可、一次性数据库及 CSV，打印随机本地地址和上传样例路径；Ctrl-C 后删除临时状态，不连接原业务服务。

Vite 开发服务的 `/workspace/host-preview/` 提供纯内存合成宿主，用于换案、同案重绑定、撤销、600px 分栏和迟到响应检查，不请求 HTTP 或业务库。导入等真实用例仍在独立合成版验证。该入口由 `import.meta.env.DEV` 分支加载；生产产物须另检不含 fixture，见 [前端宿主适配](docs/frontend-host-adaptation.md)。

独立桌面开发预览也使用一次性合成数据：

```sh
uv pip install --python .venv/bin/python --require-hashes -r requirements-desktop.lock
npm --prefix desktop ci
npm --prefix desktop test
npm --prefix desktop run synthetic
```

Electron 44 运行时会在首次运行时下载；安装 NPM 包本身不证明二进制已安装。普通桌面启动为 `npm --prefix desktop start`，数据在自身 `userData/data`，无需配置产品授权。Windows x64 原生构建与 `-Installer`、手动 Actions、安装生命周期证据统一见 [桌面交付](docs/desktop-delivery.md)；不要将本机源码预览当作 Windows 安装验收。已发布 alpha.3 的历史授权与保留的 FT2／HMAC 验签、私钥和独立 Issuer 见 [历史授权维护](docs/license-activation.md)。

需要普通浏览器服务时，显式选择独立运行目录：

```sh
FUND_DATA_DIR=/absolute/path/to/private-runtime .venv/bin/python run.py
```

默认只监听 `127.0.0.1:8300`，`FUND_PORT` 可指定其他端口；Windows 配好环境后可用 `run.bat`。构建后 `/workspace/` 提供 React 试点，`/` 保留 G6；React 的“打开 G6 图谱”携带当前案件 ID，表格与图谱保留各自明确的统计口径。未构建前端时 `/workspace/` 返回 503 和构建提示。

## 运行目录与上传清理

`FUND_DATA_DIR` 统一控制 `fund.duckdb`、上传、照片、`license.dat`、`runtime.log` 和绘图缓存；未设置时使用被忽略的 `data/`。不要在原业务数据目录上试运行新候选。

React 的“导入记录”提供任务进度、完成回执、继续导入和上传清理；“核对 → 来源追溯”显示摘要、源位置及可用原件。继续导入前必须重新预览并确认，重启不会自动重放写入；没有可靠来源的历史数据明确显示缺失。完整流程及来源规则见 [导入生命周期](docs/import-lifecycle.md)。

上传待确认／重新预览后为 7 天留存，完成／失败／恢复标记后为 30 天；到期只获得清理资格，活动任务和来源引用继续保护，没有定时自动删除。CLI 默认只预览文件删除计划：

```sh
.venv/bin/python scripts/cleanup_imports.py --data-dir /absolute/path/to/managed-runtime --case-id 2
```

追加 `--apply` 才执行；省略 `--case-id` 可处理已删案件的 GC 队列。即使默认预览也会打开／初始化数据库，先停止占用同一库的服务并确认目录。完整命令、恢复与操作限制见 [显式清理](docs/import-lifecycle.md#7-天30-天与显式清理)。

## 代码地图与兼容性

| 路径 | 职责 |
| --- | --- |
| `app/main.py` | 应用组装与兼容业务 API |
| `app/api/`、`app/services/` | v1 契约、案件查询，两套路由共用的持久导入用例 |
| `app/db.py`、`app/paths.py` | 持久化事务和运行目录 |
| `app/importer.py` | 流水识别、清洗、案件内去重、源行关联和原子导入 |
| `app/case_packages.py` | 案件包校验、ID 重映射与导入导出 |
| `app/analytics.py`、`app/report.py` | 分析与 HTML 报告 |
| `static/` | 浏览器图谱界面和本地 G6 |
| `frontend/src/panel/`、`frontend/src/adapters/` | 共享 React 面板、绑定会话与独立 HTTP adapter |
| `frontend/src/App.tsx` | 独立案件外壳与面板组装 |
| `desktop/`、`desktop_backend.py` | Electron 窗口、受控下载与所属 Python 服务 |
| `desktop/issuer/` | 独立离线维护者签发工具，不连接案件数据 |
| `contracts/` | 从 Python schema 生成的 OpenAPI 与 TypeScript |
| `tests/`、`frontend/src/*.test.tsx` | 合成后端与界面回归 |
| `benchmarks/`、`scripts/` | 基准、隔离测试、契约导出与合成预览 |

保留 `FUND_DATA_DIR`、`FUND_PORT`、数据库文件名和 `.fundcase` 扩展名。案件包格式 2 保留来源元数据及关联，**不带原始上传文件、导入会话、任务或回执**；仍接受格式 1，无来源的旧数据不补造。导入重映射来源和流水 ID，包来源不冒认本地原件；损坏或关联不完整的案件包会被拒绝。批次删除请求必须同时提供 `case_id`。

CSV/XLSX 是当前回归覆盖的导入格式。旧 `.xls` 入口、可选 PDF 优化依赖及真实案件性能仍须专项验证。Windows 静默安装证据不覆盖交互 GUI、DPI 和系统保存。[性能记录](docs/performance.md) 是持久任务／来源改造前的合成基线，不代表当前候选性能。初始化 checkpoint 和进程恢复回归不构成磁盘损坏或任意断电保证；业务备份应在一致状态下包含完整运行数据。

进一步设计与历史证据按主题查阅：

| 主题 | 文档 |
| --- | --- |
| 工作台操作、交互验收与工作区约束 | [使用指南](docs/workspace-guide.md)、[设计验收](design-qa.md)、[工作区交付](docs/graph-workspace-plan.md) |
| 精确金额、导入、API | [金额契约](docs/money-contract.md)、[导入生命周期](docs/import-lifecycle.md)、[API 契约](docs/api-contract.md) |
| G6 首绘、标签、裁剪与历史性能 | [首绘与标签记录](docs/graph-render.md)、[图谱视窗](docs/graph-viewport.md)；不据此宣称新布局或桌面性能 |
| 共享面板和未来宿主 | [前端宿主适配](docs/frontend-host-adaptation.md)、[Analytix 整合建议](docs/analytix-integration.md)、[Windows／插件研究](docs/windows-and-plugin-plan.md)；UI port 尚非已接入的 Host／受保护显示合同 |
| 技术选型、命名与接管历史 | [演进建议](docs/evolution.md)、[接管记录](docs/takeover.md) |

## 新成员从一个合成用例开始

按上文的 Python 3.11、Node 基线和锁文件安装依赖，先运行一个独立合成用例，确认理解案件包往返行为：

```sh
.venv/bin/python scripts/test.py tests/test_cases.py::test_package_round_trip_preserves_duplicate_rows_and_all_associations -q
```

Windows 将 Python 路径换为 `.venv\Scripts\python`。需要看界面时，使用上文的合成预览地址和样例；不要连接已有业务服务。代码职责见 [代码地图](#代码地图与兼容性)。

## 分工和 PR

- `main` 保持可交付；从最新 `main` 建短分支，每个 PR 完成一个可说明、可验证的行为变化，合并后结束该分支。
- 开始前在 Issue、PR 或团队任务中写清问题、验收行为和认领的模块／文件。多人并行时独占写入范围，保留他人修改；尤其避免同时编辑大文件 `app/main.py` 和 `static/app.js`。确需交叉修改时先协调顺序，再由后一位接续最新改动。
- 修缺陷先给最小合成复现，说明步骤、预期和实际结果。可行时用公共行为回归覆盖原问题，确认修复前失败、修复后通过；期望值来自独立合成样例。
- 做最小完整修改，连同必要调用方、测试和文档一起交付。新增抽象、依赖或兼容路径须有当前需求；无关重构另开任务，不用临时绕过、弱化断言或重复实现掩盖缺陷。
- PR 说明问题、行为变化、实际验证及兼容／数据影响，使用仓库模板。真实协作者到位后，由维护者按模块安排一位相关 reviewer，并设置必要的 required checks；当前不虚构 owner，不要求固定双审或每个 PR 默认跑全套。

## 按影响选择最小充分验证

| 变更范围 | 需要的证据 |
| --- | --- |
| 纯说明文档 | 检查差异、引用和路径；不因文档改动启动全套测试或打包 |
| Python 业务逻辑 | 通过 `scripts/test.py` 运行受影响模块／用例，例如 `.venv/bin/python scripts/test.py tests/test_cases.py -q` |
| 案件隔离、金额、导入或数据契约 | 对应公共行为回归；覆盖变更涉及的跨案拒绝、精度／币种、失败回滚／恢复及兼容边界 |
| v1 API | 执行 `.venv/bin/python scripts/export_api_contract.py --check`，补调用方受影响回归；契约变化时从 Python schema 同步生成 `contracts/` 两份输出，不手改生成类型 |
| React／G6 界面 | 相关前端／合成导航回归及受影响用户流程；React 变更执行 `npm --prefix frontend run build`，新环境或锁文件变化先按上文执行 `npm ci` |
| 桌面宿主或分发 | 相关 `npm --prefix desktop test` 检查；涉及安装、升级、卸载时补实际安装入口证据，不能用 API 或源码测试替代 |

跨模块修改或共享基础行为变化可能需要完整 `.venv/bin/python scripts/test.py`；按实际影响扩大检查，而非机械叠加。性能变更遵循 [性能口径](docs/performance.md)，不要把正确性烟测当计时结论。记录执行命令、结果及未验证的边界，区分源码、浏览器、包内后端和安装后行为；相关检查通过且无新疑点后停止重复验证。

## 依赖、来源与代码约定

变更 Python 依赖时修改相应输入文件，重新生成对应锁文件，审查版本和哈希差异。开发锁文件的命令为：

```sh
uv --no-config pip compile requirements-dev.in --python-version 3.11 --universal --generate-hashes --default-index https://pypi.org/simple --output-file requirements-dev.lock
uv pip sync requirements-dev.lock
```

Node 依赖同步所属目录的 `package.json` 和 `package-lock.json`，使用 `npm ci` 复现安装。保留 MIT、原作者和第三方版权；新增或更新外部代码、资源和依赖时核对来源、版本及许可要求，必要时更新 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，不直接替换锁定的 vendor 文件。

源码文件使用 UTF-8；Python 和脚本用 `snake_case`，测试用 `test_<behavior>.py`，文档用 `kebab-case.md`。目录按职责组织，不以开发者、修复次数或日期命名功能源码。

## 数据和发布边界

Issue、PR、测试、日志和截图只使用合成数据；不上传案件、真实账户／身份／交易、授权记录、凭据或签名材料。公开报告用合成复现和必要环境信息，不粘贴原始业务日志。不要读取原 `fund-tracker` 业务目录，不在普通测试中访问真实数据、现有服务或真实签名配置，也不要运行历史修补、恢复或终止进程脚本。

桌面版本仅由维护者按当前授权打 Tag、手动触发 [Windows Actions](.github/workflows/windows-candidate.yml) 并发布；提交 PR 本身不触发发版。每个 `v0.1.x` Tag 对应带 Windows x64 `.exe` 的 Release，具体策略及未验收边界以 [桌面交付](docs/desktop-delivery.md) 为准。Git 提交、push、打包和外部写入仍遵守当前任务授权。
