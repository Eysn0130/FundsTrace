# AGENTS.md

## 开始、协作与权限

- FundsTrace 是本地流水追踪与案件分析应用；本目录维护源码，原 `fund-tracker` 不改动、不读其业务数据。先读 `CONTRIBUTING.md`、目标目录适用的 AGENTS 链及直接相关契约；从根启动或会话中 `cd` 不证明已加载子规则。
- 开始与扩围确认 checkout、基准／候选 SHA、dirty 状态、独占写入范围及验收行为，不能假定 HEAD 是最新 main。保留他人修改及其他候选，不用 reset／clean 整理现场。各 Harness 共用本文件与 CONTRIBUTING；`CLAUDE.md` 仅导入本文件，新施工在获准范围内沿既有短分支／隔离 checkout 协作。
- 做最小完整修改，包括必要调用方、回归和文档；按完整职责提取，不按行数拆分或预建空层。新增抽象、依赖、重试或兼容路径须有当前需求；保留授权、可变／过期状态、事务、回滚及数据丢失保护。
- Git 提交、push、merge、Tag、外部写入、凭据操作、正式打包／发布须有当前任务授权，测试、构建、Hooks、角色及历史授权不增加权限。默认交付获准的待审 PR；未经维护者对当前修订明确授权，不自批、自动合并或绕过保护。详细协作与 main 门槛依 CONTRIBUTING。

## 跨目录契约

- 保持案件隔离、方向、交易时间和来源可追溯性。会话／进度绑定案件，图谱及异步入口保留 case/canvas、session/generation 和修订约束，换案或撤销使旧请求／显示失效。原件、原观察、身份、经济事件和条件本金分层；派生重算不改原件或以显示合并推定事实。
- 金额用原始 Decimal 或明确尺度的精确整数，按币种计算；v1 ID／金额为字符串，legacy 金额十进制字符串及既有 ID wire 形态兼容，React 不用 Number 转 v1 ID／金额，未知余额为 null。精确总和与均值／评分／几何分开，未知／冲突／范围／限制不补零或确定值，详见 `docs/money-contract.md`。
- `app/services/imports.py` 编排导入，HTTP 适配它；流水、账户、来源及持久回执同事务，导入、案件包及多表删除保持原子性，失败不留半案／半批／孤儿照片。启动恢复不自动重放；操作时间显式 UTC，不重解释交易时间。清理仅处理已登记到期项并复查活动／来源引用，扫描文件不授予删除权。见 `docs/import-lifecycle.md`。
- 保留 FUND_DATA_DIR、FUND_PORT、数据库／文件名及 .fundcase 边界。schema、解析版本或包格式变化需兼容／回滚证据；旧会话／来源按冻结版本解释，不改旧回执。当前派生可按 policy/analysis_revision 重建但不改原观察。归档拒绝格式外路径，临时状态按操作隔离清理；私有数据边界见 `docs/source-boundaries.md`。
- `contracts/` 只从 Python schema 生成；v1 变更执行 `.venv/bin/python scripts/export_api_contract.py --check`，有意 schema 改变同步生成两份输出，不手改类型。后端与 React 共守无损 ID／金额契约。
- `desktop/` 与根 `desktop_backend.py` 共守独立 FundsTrace 身份、数据及进程所有权，不借用 Analytix 的 appId、目录、认证或清理权。bootstrap token 只经父进程 stdin 和 Main 持有的精确 origin 请求头传递，不进 URL、renderer、IPC、日志或导出。冻结／分发后端拒绝 source-only synthetic，不带 seed／业务数据。
- 当前 v0.1.x 正常 Python／API／UI／包内启动免激活，不能用 synthetic 或共享测试许可代替正常启动；桌面认证、更新完整性和案件隔离仍有效。保留历史 alpha FT2／HMAC、签发材料及已有数据，免激活不授权删密钥；客户端只可带 SPKI 公钥，不含私钥、共享秘密或默认受信测试钥。见 `docs/activation-history.md`、`docs/desktop-delivery.md`。
- 许可统一读 LICENSE、`docs/licensing.md`／英文版及 THIRD_PARTY_NOTICES，中文 LICENSE 优先。保留源码可见许可、历史 MIT、第三方权利／来源／通知及 CodeHaven969、linzijie6524 已确认特别许可，不扩大授予或推定未确认权属。外部贡献授权留证依 CONTRIBUTING，PR 不等于版权转让；法律许可与免激活分开，私有授权记录不入仓。

## 目录与权威入口

- `run.py`／`app/main.py` 负责启动、组装及兼容 API；后端职责见 `app/AGENTS.md`，来源／身份／事件／本金入口见 CONTRIBUTING 的领域导航，先找现有用例，不在 UI／路由复制判断。
- `static/` 是 G6 工作台，`frontend/` 是 `/workspace/` React 试点，`desktop/` 是 Electron 宿主及历史 Issuer；分别读其局部规则。修改根 sidecar 显式联读 desktop 规则。`scripts/` 有工具不代表获准执行它；命名与渐进目录见 `docs/evolution.md`。
- `docs/` 区分现行契约和历史证据，其中脚本／配置仍按执行输入判断；文档归属、长期价值、引用同步与证据保留依 CONTRIBUTING“文档与证据”。公开仓库须能独立解释契约，私人笔记不能成为必要前置。

## 验证与交付

- 所有本文件／子规则中的相对验证命令从选定 checkout 根目录执行；基线 Python 3.11、Node 22.22.1，安装和锁文件依 CONTRIBUTING。后端入口 `.venv/bin/python scripts/test.py`，React／Desktop 用所属 build/test。按受影响公共行为选择最小充分检查并满足现有 CI；规则文件不能按纯 Markdown 豁免，路径变化联查 imports、HTML、配置、文档及源码抽取测试。
- 仅合成交易、临时库和隔离服务，不接现有业务实例、不运行历史 dbg_*／patch_*／恢复脚本。UI 补实际受影响流程，分发补目标系统安装入口；源码／API／VM 不替代浏览器、Windows GUI／DPI／恢复及安装后证据。性能采样按 `docs/performance.md` 串行，benchmark.py --check 只证正确性冒烟。
- 每个获准发布的 v0.1.x Tag 对应带 Windows x64 NSIS .exe 的 Release，保留 appId／数据／升级身份及默认卸载留数据，只关闭 FundsTrace 所属服务。发行须单独当前授权，公开说明依 CONTRIBUTING，国内同步核同版同说明；计划／配置／旧检查不是交付证明。
- 汇报候选、输入／环境、实际检查及未验证边界；独立期望覆盖受影响精度／币种、跨案拒绝、回滚、撤回或兼容，不弱化断言。条件未变复用证据，相关检查通过且无新疑点后停止。Issue／PR／截图／日志不含真实案件、身份、账户、交易、凭据、许可或签名材料；Analytix 接入仍是研究，UI port／独立 API 不授予宿主数据库或受保护显示权。
