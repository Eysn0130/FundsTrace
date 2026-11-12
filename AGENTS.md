# AGENTS.md

## 开始、协作与权限

- FundsTrace 是本地流水追踪与案件分析应用。本目录是维护源码；原 `fund-tracker` 目录保持原状，开发不改动该目录、不读取其业务数据。先读 `CONTRIBUTING.md` 和本任务涉及的专题契约。
- 开始与扩围时确认实际 checkout、基准/候选 SHA、dirty 状态、写入范围及验收行为，并显式查读目标目录适用的 AGENTS 链。不能默认 cwd/HEAD 是最新主线，也不能假定从根启动会加载全部子规则或会话中 `cd` 自动刷新规则。保留他人修改，不用 reset/clean 整理现场。
- 各 Harness 共用本文件和 `CONTRIBUTING.md`，`CLAUDE.md` 仅导入本文件。新施工按既有短分支、隔离 checkout 和独占文件约定；在明确施工任务及其授权内选用适合的隔离 checkout/短分支；保留其他候选，不为满足流程迁移或清理已有现场。
- 最小完整修改包括必要调用方、回归和文档。按真实职责提取完整用例，不按行数拆文件，不预建空目录；没有当前需求不新增抽象、依赖、重试或兼容路径。保留真实授权、可变/过期状态、事务、回滚和数据丢失保护。
- Git 提交、push、merge、tag、远端/其他外部写入、凭据操作、正式打包与发布须有当前任务授权。测试、构建、Hooks、角色及历史授权不增加权限；普通 Agent 交付到获准的待审 PR，未经维护者对当前修订明确授权，不自批、自动合并或绕过保护。协作细节以 `CONTRIBUTING.md` 为准。

## 跨目录契约

- 案件隔离、交易方向、交易时间和来源可追溯性必须保持；会话/进度绑定所属案件，图谱与异步状态沿各入口既有 case/canvas、session/generation 和修订约束校验，换案或撤销使旧请求和显示失效。原件、原始观察、身份关系、经济事件和条件本金是不同层；派生重算不得改写原件或借显示合并推定业务事实。
- 金额从原始 Decimal 或明确尺度的精确整数计算，按币种分开。v1 API 的 ID 与金额保持字符串；legacy 金额延续十进制字符串，既有 ID wire 形态保持兼容。React 面板/adapter 不以 `Number` 转换 v1 ID 或金额；余额未知为 `null`。精确总和与均值/评分/显示几何分开；未知、冲突、范围及限制不得补成零或确定值。详见 `docs/money-contract.md`。
- 导入由 `app/services/imports.py` 编排，HTTP 入口适配该服务。流水、账户、来源和持久回执同一数据库事务提交；导入、案件包和多表删除须完整原子操作，失败不留半案、半批或孤儿照片。启动恢复不自动重放写入。操作时间用显式 UTC，不重解释交易时间；上传清理只针对已登记到期项并复查活动/来源引用，不靠扫描文件取得删除权。详见 `docs/import-lifecycle.md`。
- 保留 `FUND_DATA_DIR`、`FUND_PORT`、既有数据库/文件名和 `.fundcase` 兼容边界；修改 schema、解析版本或包格式须有对应兼容/回滚证据。旧会话及来源按冻结解析版本解释，原处置和回执不悄悄应用新语义；当前派生投影按其 policy/analysis_revision 可重建，但不改写原观察或旧回执。归档拒绝格式外路径，临时文件按操作隔离并清理。来源与私有数据边界见 `docs/source-boundaries.md`。
- `contracts/` 仅从 Python schema 生成。v1 API 变更执行 `.venv/bin/python scripts/export_api_contract.py --check`；schema 有意改变时从 Python 同步生成两份输出，不手改生成类型。后端与 React 共同维护同一无损金额/ID 契约。
- `desktop/` 与根目录 `desktop_backend.py` 共同维护独立 FundsTrace 桌面身份、数据及进程所有权，不借用 Analytix 的 appId、目录、认证或清理权。bootstrap token 仅经父进程 stdin 和 Main 持有的精确 origin 请求头传递，不进 URL、renderer 状态、IPC、日志或导出。冻结/分发后端拒绝 source-only synthetic 模式，不随包带 seed 或业务数据。
- 当前 v0.1.x 正常 Python/API/UI/包内启动免激活，不用 synthetic 或共享测试许可代替正常启动；桌面请求认证、更新完整性和案件隔离继续有效。保留历史 alpha 的 FT2/HMAC 兼容、签发材料与已有数据；仅 SPKI 公钥可进客户端，私钥/共享秘密及默认受信测试钥不得进安装器。免激活不授权删除密钥。详见 `docs/activation-history.md`、`docs/desktop-delivery.md`。
- 适用许可统一读 `LICENSE`、`docs/licensing.md`/英文版及 `THIRD_PARTY_NOTICES.md`；中文 LICENSE 优先。当前版本是源码可见软件，保留历史 MIT（含当前快照沿用部分）、第三方权利、来源与通知，以及 CodeHaven969/linzijie6524 已确认的特别许可边界，不复制、改写或扩大许可授予；不推定未确认材料权属。新增外部贡献的授权留证按 `CONTRIBUTING.md` 执行，PR 不等于版权转让。法律许可与免激活分开，私有授权记录不进仓库。

## 目录与权威入口

- `run.py`/`app/main.py` 负责启动、应用组装与兼容 API；`app/` 的后端职责和验证见 `app/AGENTS.md`。身份入口为 `evidence_resolution`；事件/所选资金口径为 `event_resolution`/`funds_projection`；条件本金为 `principal_evidence`/`principal_ledger`，来源记账与时序分别为 `source_ledger`/`ledger_navigation`。先定位现有入口，不在 UI 或路由复制判断。
- `static/` 继续承载 G6 工作台；加载链、显示/事实边界与工作区规则见 `static/AGENTS.md`。`frontend/` 是 `/workspace/` 的 React 试点，共享面板、独立外壳和 adapter 边界见 `frontend/AGENTS.md`。
- `desktop/` 是独立 Electron 宿主与历史维护者 Issuer，局部规则见 `desktop/AGENTS.md`；根目录 sidecar 仍受上文认证约束。`scripts/` 提供检查、生成、预览与发行工具；有脚本不表示获准执行破坏性或外部操作。`tests/`/`benchmarks/` 使用合成与临时状态；`docs/` 的现行契约与历史证据须区分，文档中的可执行工具不能按纯说明文档处理。命名与渐进目录建议见 `docs/evolution.md`。

## 验证与交付

- 本文件及子规则中的 `.venv/...`、`scripts/...`、`npm --prefix ...` 等验证命令，均从选定 checkout 的仓库根目录执行；在子目录启动或读取局部规则不代表相对命令路径正确。使用 Python 3.11、Node 22.22.1 的已验证基线及所属锁文件，安装/详细命令见 `CONTRIBUTING.md`。
- 后端通过 `.venv/bin/python scripts/test.py` 运行受影响公共行为回归；React 执行所属 build/test；Desktop 执行 `npm --prefix desktop test`。按实际影响扩围并满足现有 CI，规则文件变更不能冒充纯 Markdown 文档豁免。文件/资源路径变化包括必要 imports、HTML、配置、文档入口和源码抽取式测试，不只核对被改文件。
- 仅用合成交易、临时数据库和隔离服务，不把测试接到运行中的业务实例，不执行历史 `dbg_*`、`patch_*` 或恢复脚本。UI 变更补受影响用户流程；分发变更补目标系统安装入口，源码/API/VM harness 不能替代浏览器、Windows GUI/DPI/恢复或安装后证据。性能采样按 `docs/performance.md` 串行；`benchmark.py --check` 只证明正确性冒烟。
- 发行仍是单独的当前授权：每个具体 v0.1.x Tag 对应带 Windows x64 NSIS `.exe` 的 Release，保留 appId、数据/升级身份和默认卸载留数据策略，只安全关闭 FundsTrace 所属服务。公开说明按 `CONTRIBUTING.md`，国内同步须核对同版及同说明；不得把计划、配置或旧候选检查写成实际交付。
- 汇报实际候选、输入/环境、执行检查和未验证边界。独立合成期望覆盖受影响的精度/币种、跨案拒绝、失败回滚、来源撤回或兼容行为；不弱化断言。复用条件未变的有效证据，相关检查通过且无新疑点后停止。Issue/PR/截图/日志不含真实案件、身份、账户、交易、凭据、许可记录或签名材料。Analytix 接入仍是研究，UI port 和独立 API 不授予宿主数据库或受保护显示权。
