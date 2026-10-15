# FundsTrace 与 Analytix 的后期整合路线

本文件为 **Reference／研究建议**，核对日期为 2026-10-14。当前只维护 FundsTrace 源码候选；没有修改 Analytix、启动其服务或读取业务数据库。下述宿主改造是后续范围，不能把文档当成接入授权或已完成集成。

建议先把 FundsTrace 的资金语义、服务与存储边界稳定下来，再将 React 面板作为 Analytix 既有数据分析工作区的一项能力接入。**复用一个 Go Harness、一个权限与案件 authority、一个受保护数据所有者；FundsTrace 贡献领域算法和 UI，不另建 Agent／Provider runtime。**

当前独立施工已将 v1／legacy 导入统一到 Python 应用服务，持久保存任务、回执和来源，并提供明确恢复与注册上传清理；React 增加本案导入记录和流水来源入口。它们为未来接入建立可测试的领域边界，尚未形成 Analytix Host adapter。具体独立行为见 [导入生命周期](import-lifecycle.md) 和 [12 个 v1 操作](api-contract.md)。原业务目录不动，本轮未改 Analytix、未上传 GitHub、未重写 Rust。

关于“先引入 Analytix 的 Electron”：建议复用技术路线及明确合同，有桌面需求时建立 FundsTrace **独立薄开发壳**，未来再把共享 React 面板挂入宿主；不复制 Analytix 整套 main／preload／身份／迁移。当前**没有实现运行 Electron 壳**，方案、当前源码事实和后续验收见 [electron-boundary.md](electron-boundary.md)。

## 核对范围与公开状态

- 本地 Analytix：`<local-analytix-checkout>`，分支 `main`，HEAD 为 `c7d2a370855a8d371c61c32cc2422515b9404ef2`，tree 为 `f06351e3822c0bfc93e2887b94e40d88a7ad4252`。
- 只读 `git ls-remote origin refs/heads/main` 返回同一 HEAD；公开仓库为 [Eysn0130/analytix](https://github.com/Eysn0130/analytix)。以下 Analytix 源码链接均固定到该 commit，路径与行号来自本轮本地核对。
- 根 `README.md` 没有本地修改，公开 README 同样声明唯一 Go Agent core、Funds 专业插件与内建 Privacy Layer，并明确完整隐私／安装包验收尚不能由架构目标推得。[公开 README，L34–44](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/README.md#L34-L44)
- 本地工作树另有未提交的规则／文档／恢复代码修改和未跟踪研究文件。它们被保留；本建议不把这些修改描述为公开 main、已合并成果或已发布能力。尤其不以本地 privacy 研究目录证明生产接入。
- FundsTrace 是当前维护候选，原 `fund-tracker` 被保留；其源码来源、资源许可和商业授权边界见 [source-boundaries.md](source-boundaries.md)。本轮没有取得候选的 Git 历史，也没有进行 commit、push、安装、正式封包或发布。

## Analytix 当前可复用的边界

| 当前事实 | 当前源码证据及接入含义 |
| --- | --- |
| 桌面主入口是 React 工作台／设置切换，业务导航由 store 与组件状态驱动 | [AppShell.tsx，L45–90](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/AppShell.tsx#L45-L90)。不应假定已有一个可注册任意 URL 的 React Router 插件路由。 |
| 项目侧栏已包含导入、清洗、统计、可视化四项 | [SidebarProjectsSection.tsx，L138–176](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/components/chat/SidebarProjectsSection.tsx#L138-L176)，[项目内挂载，L1269–1286](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/components/chat/SidebarProjectsSection.tsx#L1269-L1286)。后期应评估复用这个项目上下文，避免再建一套全局案件选择器。 |
| 点击数据分析入口会保存 workspaceRoot／itemId，再加载本地 React surface | [Workbench.tsx，L2900–2909](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/components/Workbench.tsx#L2900-L2909)，[实际渲染，L3560–3570](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/components/Workbench.tsx#L3560-L3570)。这是优先考虑的面板位置。 |
| DataAnalysisSurface 已有页面级组合与案件引导 | [DataAnalysisSurface.tsx，L100–131](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/DataAnalysisSurface.tsx#L100-L131)，[L293–329](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/DataAnalysisSurface.tsx#L293-L329)。图谱与统计已有产品面，先比较领域差距，再决定新增“追踪”子项或复用已有可视化页。 |
| Renderer 通过受限 preload bridge 调用 main | [preload/index.ts，L796–818](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/preload/index.ts#L796-L818)，[data-analysis/ipc.ts，L24–45](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/data-analysis/ipc.ts#L24-L45)。已有数据分析 IPC 检查 main frame、已登记 renderer 及 generation；子 frame 或任意本地网页不自动继承权限。 |
| 普通 runtime 请求与精确本地显示分别处理 | [main/index.ts，L1875–1922](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/index.ts#L1875-L1922)，[typed display IPC，L1957–1991](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/ipc/register-app-ipc-handlers.ts#L1957-L1991)。账户、身份和来源原值不可为了方便进入普通请求 sanitizer、日志或共享 UI store。 |
| 生产 Agent core 是 Go，TypeScript backend 已被拒绝 | [analytix-adapter.ts，L452–482](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/runtime/analytix-adapter.ts#L452-L482)，[Go executable 装配，L35–52](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/packages/runtime-go/cmd/runtime-server/main.go#L35-L52)。FundsTrace 不承接会话、模型调用、工具权限、恢复或子代理编排。 |
| 已有 Funds 专业插件和 Go Host 准入 | [canonical manifest](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/plugins/analytix-fund-analysis/.analytix-plugin/package.json)，[bundled_funds_host.go，L116–188](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/packages/runtime-go/internal/runtimeapp/bundled_funds_host.go#L116-L188)。manifest 当前声明 `analytix-fund-analysis`、`host-static-first-party` 及 case-bound/source-verified 读能力；源码存在并不等于包已安装、已激活或当前案件已授权。 |
| Go 中的资金账户流能力以当前案件／快照条件组合 | [runtimeapp/app.go，L1520–1545](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/packages/runtime-go/internal/runtimeapp/app.go#L1520-L1545)，[funds_account_flow_composition_v1.go，L116–127](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/packages/runtime-go/internal/runtimeapp/funds_account_flow_composition_v1.go#L116-L127)。结果会核对 snapshot、context epoch/digest 和 case binding；失效的能力不能用 UI 中缓存的 caseId 恢复。 |

普通链路是 `Renderer → window.analytix → preload → Electron main → Go HTTP/SSE`；SSE 在 main 绑定 sender／stream owner 并配置 public event filter。[runtime-sse-ipc.ts，L277–305](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/runtime-sse-ipc.ts#L277-L305)

### 已有 UI 不等于旧后端可直接启用

这是后期排期的实际依赖：当前公开 HEAD 的 `DataAnalysisBackendManager` 固定返回 `data_analysis_native_authority_unavailable`，`ensureBackend()` 只返回该状态，`request()` 直接拒绝；`ensure-workspace-case` IPC 也要求运行中的 `go-native` authority。[backend-manager.ts，L323–337](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/data-analysis/backend-manager.ts#L323-L337)、[L458–477](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/data-analysis/backend-manager.ts#L458-L477)、[ipc.ts，L81–100](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/data-analysis/ipc.ts#L81-L100)

Python `backend/app/main.py` 仍有 FastAPI、认证 middleware、`/api/v1` 和 WebSocket 服务定义；它们是现存实现材料，不能据此声称桌面当前会启动这一服务。[服务组装，L244–263](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/backend/app/main.py#L244-L263)、[router／WS，L330–338](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/backend/app/main.py#L330-L338)

Python data-engine consumer 明确指出缺少版本化、特定消费者的 capability admission；发现函数返回 `None`，执行入口直接失败，且没有直接进程 fallback。当前 Python active-case DuckDB 直接访问也被硬性关闭。[data_engine_client.py，L201–242](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/backend/app/core/data_engine_client.py#L201-L242)、[db_engine.py，L57–73](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/backend/app/core/db_engine.py#L57-L73)

因此后续接入须补齐 Host 接受的能力与调用合同；不能解除这些失败关闭条件、私启旧 Python 后端或给 React 直连数据库来“打通”功能。

### 案件绑定、持久化与数据所有者

Analytix 的项目绑定入口是工作区中的 `.analytix/case-project.json`：代码严格检查 version、workspaceRoot、caseId、source、updatedAt；无效、不可读或指向不可用案件时保留原文件并失败，不默默换案。只有运行中的宿主 authority 才能继续案件查询／创建／激活及绑定写入。[绑定结构，L42–51](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/data-analysis/case-project-registry.ts#L42-L51)、[严格解析，L100–123](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/data-analysis/case-project-registry.ts#L100-L123)、[入口，L290–326](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/data-analysis/case-project-registry.ts#L290-L326)

旧 Python 数据路径设计为 `cases/<case_id>/case.duckdb`，验证 case id、目录边界和 symlink；这是源码层路径合同，本轮没有读取任何实际绑定文件或库。[db_engine.py，L16–34](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/backend/app/core/db_engine.py#L16-L34)

FundsTrace 目前是 `FUND_DATA_DIR/fund.duckdb` 的统一存储，依靠表内 `case_id` 隔离，连接与事务由一个共享锁管理；这与 Analytix 的 case/snapshot authority 不等价。[本地 paths.py](../app/paths.py)、[本地 db.py](../app/db.py)。后期 host adapter 应接收宿主发出的有界案件／快照上下文，再选择 storage port；不得把 FundsTrace 的整数 case id、默认案件 1、文件路径或用户输入当宿主授权。独立模式继续保持当前文件和格式；宿主模式应在一个用例开始时选定唯一数据所有者，不能让两个系统同时打开、写入或双向同步同一 active-case 库。

Analytix 已有 Rust 数据平面：`tools/data_engine` 的 crate 为 `analytix-data-engine`，使用 bundled DuckDB，复用 `analysis_compute` 和 `cleaning_ops`；Go native manifest 把它列为 `single-owner-case-database`。命令面含 DuckDB session、规范 CSV snapshot、源流水分页、账户入口解析、账户流分析、精确预览和清洗。session 实现拒绝同路径重复所有者并取得 owner guard。[Cargo.toml，L1–17](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/tools/data_engine/Cargo.toml#L1-L17)、[Go native manifest，L51–55](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/packages/runtime-go/internal/adapters/outbound/nativecomponentregistry/manifest.go#L51-L55)、[命令，L696–724](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/tools/data_engine/src/main.rs#L696-L724)、[所有者检查，L1053–1084](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/tools/data_engine/src/main.rs#L1053-L1084)

这意味着不应为了 FundsTrace 接入而另造通用 Rust 数据引擎，也不能把 Rust 的内部通用 SQL 命令直接暴露给插件。先比较现有 typed Funds 能力与所需算法；缺失部分通过既有 Go admission、取消、资源限制与结果投影扩展。源代码中存在某命令不证明任何平台的当前二进制、receipt、安装资源或消费者已获准使用。

### Privacy Layer 与 UI 边界

接受的架构要求区分 `private-source`、`model-safe`、`public-durable`、`protected-local` 四个通道。插件负责领域 schema、算法和安全呈现模板；Core 负责 Provider、权限、事件、持久化、日志与受保护精确显示。插件 UI 不因挂到侧栏就获得原始账户、身份、DuckDB 路径或凭据。[接受目标，L58–90](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/docs/analytix/specs/11-agent-platform-brand-and-architecture.md#L58-L90)、[Funds 责任，L105–111](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/docs/analytix/specs/11-agent-platform-brand-and-architecture.md#L105-L111)

FundsTrace 原有“浏览器看到什么，API 就返回什么”的模式不能原样视作宿主合规：图和表的一般状态使用经批准的事实／引用；要显示来源原值时，请求 Core 已有或后续接受的 typed protected display，离开案件、切换 workspace、撤销 grant 或重启后重新核验。raw 值不进入 Provider prompt、普通聊天／摘要、URL、localStorage、console、共享缓存或普通导出。精确原文导出也须单列受保护合同，不能因为是本地文件就默认属于 public-safe。

本轮 `frontend/src/lib/api.ts` 的可替换 `FundsApi` 是独立产品的传输接缝，DTO 包含本地案件原值。它不是已经通过宿主准入的 Host contract。以后共享 React 时须拆开 safe view model 与 Core protected display slot，并重新定义宿主 adapter；不能把当前 `Flow` DTO 原样转发到 Analytix store／preload 就称为完成整合。

上述 Analytix 评估只确认具体源码与目标边界，没有验证 Privacy Layer 全通道覆盖，不能据此作完整隐私或发行结论。FundsTrace 独立模式的回归同样不能证明宿主准入、受保护显示或正式安装已经通过。

### 本轮已稳定的领域接缝与剩余适配

导入服务现在统一拥有会话状态、冻结请求、事务、回执、来源与留存；HTTP 路由负责协议，不再通过 `main._run_commit` 或内存任务字典执行业务。后续 Host adapter 应复用这些资金语义，替换案件授权、来源 handle、存储与执行生命周期；当前全局 `db.py` 仍是独立模式 adapter，并未实现宿主 storage port。

恢复采用“发现旧进程活动任务 → 标记中断 → 用户重新预览并确认”，不自动重放。来源摘要／原行关联是领域事实，原始上传的读取和清理仍由独立服务管理。宿主接入时须明确谁拥有原件、如何撤销 handle、来源摘要可进入哪条隐私通道，以及由谁协调任务恢复；不能让 Python 清理宿主路径。

案件包格式 2 只交换来源元数据／流水关联和既有业务内容，不带原始上传文件、会话、后台任务或回执；导入为 `origin=package`，不冒认本地原件。旧格式仍受兼容校验，无来源的旧数据不补造。该交换格式可以作为后期迁移研究材料，但不是 active-case 双写或宿主授权合同。

## 三种 UI 接入方式的成本

下表是基于当前源码边界的工程判断，未实施、未做包体或性能基准。

| 方式 | 维护与打包成本 | 安全／案件成本 | 判断 |
| --- | --- | --- | --- |
| 共享 React 模块，由 Analytix lazy-load 面板 | 前期需抽离路由、CSS、API 和全局状态；之后复用宿主构建、设计 token、错误及窗口生命周期，无需为页面增加监听端口。React／构建版本需一起验证，模块版本与宿主契约要锁定。 | 通过 host adapter 使用同一当前案件与权限；纯 UI 模块不可直接取得 raw 值，精确显示由 Core 组件承担。 | **长期优先**。先在 FundsTrace 做一条独立 React 纵切，再在明确的后续 Analytix 任务中接现有 workspace surface。 |
| 本地 Web 服务页面嵌入 Electron | 可以较快复用现有 FastAPI／静态 UI，但多一套进程、端口、健康、退出恢复、版本兼容和 Python 依赖；需要目标平台离线运行资源、完整性与签名／许可验收。现有 builder 已列 backend／Funds 材料，也不代表新服务自动满足分发合同。 | localhost 不是信任证明。必须由 main／Go Host 启动和准入，限定 origin、sender、代次、方法、大小、超时与撤销。不得给 iframe／WebView 整个 preload、共享 bearer、文件路径或数据库。 | **只作为有期限的兼容候选**。若只需 Python 算法，优先把它当 Host 管理的领域计算进程，UI 仍走受限 adapter；不以 iframe 直连绕过关闭的 data-analysis authority。 |
| 打开独立浏览器／外部窗口 | 初期对两端源码耦合最小，但用户要切窗口、分别升级；多一套安装入口、生命周期与错误支持。若用新的 Electron 窗口仍需独立校验 preload／导航／权限。 | 保持独立案件和数据根；不通过 URL 传账户、case DB 路径或凭据，不默认共享 active-case。交换只走明确授权、有版本和来源追溯的快照／导出合同。 | **适合独立产品试用或联调阶段**，不能称为统一工作区集成。后续需要一体化体验时再迁共享模块。 |

打包现状参考 [electron-builder.config.cjs，L188–234](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/electron-builder.config.cjs#L188-L234)。Electron 官方要求验证 IPC sender、保留 context isolation／sandbox 和 webSecurity、限制导航与新窗口；本地服务也不能据此获得环境权限。[Electron 安全指南](https://www.electronjs.org/docs/latest/tutorial/security)

React 可以逐个区域接入现有页面，适合先迁移一条流程，无需一次重写全部 UI。[React 官方增量接入](https://react.dev/learn/add-react-to-an-existing-project)

## FundsTrace 先做的模块与契约路线

目录方向沿用 [evolution.md](evolution.md)。以下阶段按依赖推进，每次提取一个可验收用例；没有需求时不批量创建空抽象。

| 阶段 | 最小变更 | 验收与停止条件 |
| --- | --- | --- |
| 1. 固定独立模式行为 | 保留 `run.py`、当前 API、`FUND_DATA_DIR`、`fund.duckdb` 和 `.fundcase`。用合成案例固定行来源、Decimal 金额、方向、时间、缺失值、图去重与聚合含义。 | 公共 API 的正例、跨案反例、导入／案件包失败回滚和重新打开通过；金额 oracle 独立计算。此阶段不切换存储或引擎。 |
| 2. 从 main 提取 service | 当前导入已由 `app/services/imports.py` 统一，v1／legacy 保留各自协议与 surface 绑定；持久任务／来源／留存见生命周期文档。连接仍由 `db.py` 管理，尚未引入通用 `app/storage/` 层；按真实宿主需求再定义窄 port。 | 对新增持久语义做公共协议、重试、回滚及真实进程恢复验证；不是要求所有旧字段／内存行为保持不变。不得为拆分把一次导入事务拆成多个提交。 |
| 3. 形成窄 storage port | service 接收明确的 case context 与 repository，不在函数中自行发现 home、业务目录或全局默认案件。独立 adapter 继续使用当前锁和库。分析查询与显示 DTO 分开。 | 两个合成案件及并发／切换测试不能互读互删；取消、错误与重新打开语义明确。没有 Host adapter 时明确不可用，不偷偷回退默认案件 1。 |
| 4. 试点 React 面板 | 当前独立流程包括案件、预览／提交、核对、导入记录恢复与来源追溯，仍使用 typed HTTP client；G6 保留。面板仍含独立案件侧栏和浏览器导航，未来嵌入前需抽离这些外壳职责，再实现受限 bridge adapter。 | 类型、构建、浏览器与合成 oracle 分层验证。独立 UI 证据不代表现有 App 已是宿主无关模块；未来仍需换案、撤销、缺能力和保护显示验收。 |
| 5. 定义 host contract | 后续授权时，与 Analytix owner 确定可用 capability/version、当前 workspace/case/snapshot、幂等 request id、取消、分页／大小／时间上限、错误码、来源 digest 及结果投影。使用既有 Funds package 身份与生命周期；是否需要新增 contribution 由该合同决定。 | 用 fake host／合成数据证明成功、缺能力、过期 snapshot、撤销、换案、重复请求、部分失败及重启后重新核验；不会读取路径、启动 Provider 或直开宿主库。fake host 通过不等于真实 Host 准入通过。 |
| 6. 接真实 Host 与侧栏 | 在独立的 Analytix 授权任务中补齐当前被关闭的 native authority 依赖；按一个用例先读后写接真实 Go capability，再挂既有 workspace surface。共享 React 模块与 Core protected display 分工明确。 | 验证 renderer→preload→main→Go→数据平面，以及反向投影／UI。用跨案、失效、撤销、恢复和敏感 canary 验证边界；Funds 不可用时普通 Agent 可用。接口测试不能代替可见 UI 验收。 |
| 7. 再判断数据热点与分发 | 先 SQL／批量路径，只有实测热点才试 Polars 或 Rust；宿主侧优先复用已有 data-engine typed operation。新增进程／二进制须对应 packaging owner、依赖、license、签名、升级和卸载数据策略。 | 与 Python 独立 oracle 对拍，端到端收益达到项目事先接受的阈值；目标平台离线安装、启动、退出、升级与恢复另验。源码／API／浏览器通过分别报告，不自动变成安装或正式发布就绪。 |

拟定的 host contract 只传用例需要的 opaque 引用和有界输入：例如当前上下文引用、操作名、分页／筛选、幂等键与 cancellation。宿主核验引用对应的 principal、workspace、case、snapshot、generation 和权限，返回结构化事实、来源摘要与可供 Core 精确展示的绑定。引用或 alias 只是选择器，不是 authority；客户端不得自行构造 grant、快照真源或反向身份映射。

独立产品与宿主集成可共用 service 的资金规则和 UI 交互，但 storage／transport adapter 在一次调用中只能有一个有效所有者。迁移数据时应采用显式、版本化、只读源快照→目标导入／核对→切换的路线，并有回滚点；不使用后台双写来掩盖两个案件真源。

## 本轮未验证范围

Analytix 侧研究只执行规范／源码／有界 Git 元数据读取与公开网页核对。没有运行其测试、构建、Provider、Python 服务、Rust 引擎或 GUI；没有读取业务 DB、实际 case binding、凭据／Provider 配置、受保护运行状态或会话历史。FundsTrace 独立施工与证据另见生命周期和主交付记录，没有修改 Analytix。未验证其当前安装包、Hub 发布／安装、二进制完整性、OS 进程隔离、真实案件或正式隐私／发行资格。

后续开始 Analytix 实施前需重新核对其 HEAD、适用规范、相关 dirty overlap 与当前 capability 状态；本文件的固定 commit 提供可复查依据，不会随着 main 更新自动成为新候选的验收证据。
