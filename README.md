# FundsTrace

本地资金流水追踪与关联分析工具。Python/FastAPI、DuckDB，React＋TypeScript＋Vite 案件／导入／流水核对界面、保留的 JavaScript/G6 图谱，以及独立 Electron 宿主。导入由统一 Python 服务管理任务、回执、来源与上传清理。项目采用 [MIT](LICENSE)，第三方许可与维护来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。当前为预览阶段。

[下载 Windows x64 预览 v0.1.0-alpha.2](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.0-alpha.2)：包含 FundsTrace 客户端与维护者专用 FundsTrace Issuer 两个未签名 NSIS 安装器，以及 SHA-256、资源清单和原生后端检查报告。已通过原生构建与包内后端烟测；Windows GUI／DPI／安装交互尚未实机验收。首次部署须由维护者配置验证公钥，用户再输入签发的授权码。

## 当前边界

- 本目录只维护源码、合成测试和项目文档；原 `fund-tracker` 工作目录及业务数据保留在原位置。
- 没有迁移或连接原数据库，没有复制授权记录、上传文件、照片、恢复副本和旧安装包。
- 独立版保留激活；新版 FT2 由独立 Electron 签发工具持私钥签发、客户端持公钥验签。旧 HMAC 仅在明确配置时兼容，未配置时保持未激活。
- 维护者已授权公开本源码维护快照；原工作目录与历史不随本次导入上传，签发私钥和生产授权配置不进入仓库。详见 [源码与私有数据边界](docs/source-boundaries.md)。

## 可重复安装和测试

完整回归的开发基线为 macOS arm64、Python 3.11 与 Node 22.22.1；入口也需要 Node：`tests/test_legacy_navigation.py` 会运行无额外 npm 依赖的 `node --test` 合成导航回归。[alpha.2 Windows x64 Actions](https://github.com/Eysn0130/FundsTrace/actions/runs/38729411673) 已在源码 `c66e64bc65809b496b4d6f84beb1071034100529` 完成 53 项定向 Python、16 项桌面 Node、前端类型检查／构建、双安装器构建、23 项资源校验和 9 项包内后端烟测。合成 FT2 激活／重开及错误机器码／更换公钥拒绝已验证；不代表生产授权、Windows GUI 或业务导入恢复验收，其他版本不作支持保证。

```sh
uv venv --python 3.11
uv pip sync requirements-dev.lock
.venv/bin/python scripts/test.py
```

Windows 使用 `.venv\Scripts\python scripts\test.py`。锁文件包含精确版本和分发文件哈希；`requirements.txt` 是生产依赖的输入约束，`requirements-dev.in` 增加测试工具。

不使用 uv 时，可先用 Python 3.11 创建 `.venv`，再在该环境执行 `python -m pip install --require-hashes -r requirements-dev.lock`。

测试入口强制使用临时目录、合成交易、合成许可和进程内 API 客户端，不读取已有服务地址。也可向入口追加 pytest 参数：

```sh
.venv/bin/python scripts/test.py tests/test_cases.py -q --tb=short
```

每个测试有独立 DuckDB；不复用原目录的 `dbg_*`、`patch_*` 或恢复脚本。

前端已验证 Node 22.22.1、npm 10.9.4；版本锁在 `frontend/package-lock.json`。项目 `.npmrc` 固定安装选项并禁用依赖安装脚本，当前构建不依赖安装后脚本：

```sh
cd frontend
npm ci
npm run build
npm test
cd ..
.venv/bin/python scripts/export_api_contract.py --check
```

Vite 的编译之外，`build` 同时执行 TypeScript 类型检查。`npm run dev` 通过 Vite 代理连接 `127.0.0.1` 的隔离 FastAPI，默认端口 8300；可在启动前将 `FUNDS_TRACE_DEV_API_PORT` 设为合成后端打印的端口，仅接受 1–65535 的本机端口。独立导航使用相对路径，开发代理同时覆盖 G6 根页和 `/analysis`；不要把端口指向业务服务。

开发服务的 `/workspace/host-preview/` 提供纯内存合成宿主，用于换案、同案重绑定、撤销、600px 分栏和迟到响应检查，不请求 HTTP 或业务库；导入等真实用例仍在独立合成版验证。该入口由 `import.meta.env.DEV` 分支加载，生产产物须另检不含 fixture。参见 [前端宿主适配](docs/frontend-host-adaptation.md)。

只预览合成数据时，无需配置业务运行环境：

```sh
.venv/bin/python scripts/preview_synthetic.py
```

该入口覆盖继承的数据目录与签名设置，使用公开合成许可、一次性数据库及 CSV，打印随机本地地址和上传样例路径；Ctrl-C 后删除临时状态。不会连接原业务服务。

## 本地启动

独立桌面开发预览使用一次性合成数据，不连接原业务目录：

```sh
uv pip install --python .venv/bin/python --require-hashes -r requirements-desktop.lock
npm --prefix desktop ci
npm --prefix desktop test
npm --prefix desktop run synthetic
```

须先完成上述 React 构建。Electron 44 的运行时会在首次运行时下载；安装 NPM 包本身不证明二进制已安装。普通桌面启动为 `npm --prefix desktop start`，数据由宿主管理到自身 `userData/data`，激活仍需合法配置。Windows x64 原生构建使用 `scripts/build-windows.ps1`，加 `-Installer` 生成客户端与独立签发工具两个未签名 NSIS 候选；当前没有 Windows GUI 实机通过证据。详见 [桌面交付与运行](docs/desktop-delivery.md)。

Windows 构建脚本要求 PowerShell 7，并对实际目录候选内的 backend 做中文／空格路径、空库强制退出／重开、合成 FT2 激活及 EOF 烟测。[Windows Actions](.github/workflows/windows-candidate.yml) 仅手动触发，`installer=true` 可生成两个未签名 NSIS 预览安装器。原生构建、后端烟测与 Windows GUI 体验分开记录，预览发布不使用 GUI 通过标记。[治理依据与取舍](docs/source-boundaries.md#github-与轻量治理) 参考 OpenAI／Anthropic 官方仓库，并保持现有脚本为唯一执行入口。

首次部署先用独立 **FundsTrace Issuer** 创建并保管签发私钥，将导出的公钥配置给客户端；普通用户只安装 FundsTrace，提供机器码并输入维护者签发的授权码。**本轮两个安装器均未预置生产公钥或私钥，客户端在未配置时保持未激活。** 公钥可通过 Actions 的非秘密变量 `FUNDS_TRACE_LICENSE_PUBLIC_KEY` 随构建加入，或由维护者为已安装版配置绝对路径 `FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE`，完全退出后重开并核对公钥指纹。旧 HMAC 环境继续显式使用 `FUNDS_TRACE_LICENSE_SECRET`；不要把共享秘密或私钥发给用户或提交仓库。[授权配置与签发操作](docs/license-activation.md)

```sh
FUND_DATA_DIR=/absolute/path/to/private-runtime .venv/bin/python run.py
```

默认只监听 `127.0.0.1:8300`，`FUND_PORT` 可指定其他端口。Windows 配好环境后可用 `run.bat`。

构建后访问 `/workspace/` 使用 React 试点，`/` 保留 G6。React 的“打开 G6 图谱”传递当前案件 ID；表格与图谱仍使用各自明确的统计口径。未构建前端时 `/workspace/` 返回 503 和构建提示。

“导入记录”查看本案进度、完成回执或继续未完成导入；继续前先重新预览并确认，重启不会自动重放写入。活动任务关闭进度窗口后仍继续。流水行“核对 → 来源追溯”显示文件摘要、源记录位置及可用原件下载；没有可靠来源的历史流水明确显示缺失，不补造来源。

`FUND_DATA_DIR` 统一控制数据库 `fund.duckdb`、上传文件、照片、`license.dat`、`runtime.log` 和绘图缓存。未设置时使用本目录下被忽略的 `data/`。不要在原业务数据目录上试运行新候选。

上传待确认／重新预览后为 7 天留存，完成／失败／恢复标记后为 30 天；到期只获得清理资格，活动任务和被流水来源引用的文件继续保护。当前不定时自动删除，可在“导入记录”先预览再确认本案清理。CLI 默认只预览文件删除计划：

```sh
.venv/bin/python scripts/cleanup_imports.py --data-dir /absolute/path/to/managed-runtime --case-id 2
```

追加 `--apply` 才执行；省略 `--case-id` 可处理已删案件的 GC 队列。该维护命令会打开／初始化数据库，先停止占用同一库的服务并确认目录。完整策略、恢复与操作限制见 [导入生命周期](docs/import-lifecycle.md)。

## 代码地图与兼容性

| 路径 | 职责 |
| --- | --- |
| `app/main.py` | 应用组装与兼容业务 API |
| `app/api/`、`app/services/` | v1 契约、案件查询，以及两套路由共用的持久导入用例 |
| `app/db.py`、`app/paths.py` | 持久化事务和运行目录 |
| `app/importer.py` | 流水识别、清洗、案件内去重、源行关联和原子导入 |
| `app/case_packages.py` | 案件包校验、ID 重映射与导入导出 |
| `app/analytics.py`、`app/report.py` | 分析与 HTML 报告 |
| `static/` | 保留的浏览器图谱界面和本地 G6 |
| `frontend/src/panel/`、`frontend/src/adapters/` | 案件内共享 React 面板、绑定会话与独立 HTTP adapter |
| `frontend/src/App.tsx` | 独立案件外壳与面板组装 |
| `desktop/`、`desktop_backend.py` | 独立 Electron 窗口、受控下载与归属明确的 Python 服务 |
| `desktop/issuer/` | 独立离线维护者签发工具；加密私钥与 FT2 签发，不连接案件数据 |
| `contracts/` | 从 Python schema 生成的 OpenAPI 与 TypeScript |
| `tests/`、`frontend/src/*.test.tsx` | 合成数据的后端与界面回归 |
| `benchmarks/`、`scripts/` | 基准、隔离测试、契约导出和合成预览入口 |

保留 `FUND_DATA_DIR`、`FUND_PORT`、数据库文件名和 `.fundcase` 扩展名。新案件包格式 2 保留来源元数据／关联，**不带原始上传文件、导入会话、任务或回执**；仍接受格式 1，无来源的旧数据不能补造。导入重映射来源和流水 ID，包来源不冒认本地原件。损坏或关联不完整的案件包会被拒绝。批次删除请求必须同时提供 `case_id`。

CSV/XLSX 是当前回归覆盖的导入格式。旧 `.xls` 入口、可选 PDF 优化依赖、Windows 实机安装／DPI／系统保存及真实案件性能仍须专项验证。[性能记录](docs/performance.md) 保留的是本次持久任务／来源改造前的合成基线，不代表当前候选同等性能。初始化 checkpoint 和导入进程恢复回归也不是磁盘损坏／任意断电保证；业务备份应在一致状态下包含完整运行数据。

当前常驻区域压缩、高密度七列表格与图谱面积见 [工作区交付](docs/graph-workspace-plan.md)，Electron 运行、Windows 原生构建及后续单导航项接入见 [桌面交付](docs/desktop-delivery.md)。旧 G6/API 与 React 共用 [精确金额契约](docs/money-contract.md)。首绘补丁、透明标签／碰撞及上一尺寸下的交替基准见 [首绘与标签记录](docs/graph-render.md)，不能当作新布局或桌面性能保证。视窗裁剪、结构布局及缩略图的历史记录见 [图谱视窗](docs/graph-viewport.md)。当前导入行为见 [导入生命周期](docs/import-lifecycle.md)，12 个版本化操作见 [API 契约](docs/api-contract.md)。共享面板接口和业务覆盖缺口见 [前端宿主适配](docs/frontend-host-adaptation.md)。[Analytix 整合建议](docs/analytix-integration.md) 与 [Windows／插件研究](docs/windows-and-plugin-plan.md) 保留宿主契约边界：当前 UI port 使用独立 DTO，真实 Host 及受保护显示尚未接入；没有修改 Analytix。命名与技术选型见 [演进建议](docs/evolution.md)，接管历史见 [接管记录](docs/takeover.md)。
