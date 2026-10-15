# Windows 与 Analytix 插件入驻施工建议

> **当前状态更新（2026-10-15）：** 用户现已授权独立桌面施工；Electron／Python sidecar 与原生 Windows x64 构建脚本已落地，当前结果见 [desktop-delivery.md](desktop-delivery.md)。下面保留上一阶段的研究与验收矩阵，“本轮不做 Electron”等表述为历史范围。Windows 构建／运行仍未完成，Analytix 未修改或接入。

状态：Reference／后续施工建议。核对日期：2026-10-15。**本轮不做 Electron，不修改 Analytix，不安装、封包、签名或发布；本文件不证明桌面或插件已经可用。**

后续分开验收三层：网页业务成熟、桌面宿主适配、安装与发行。网页功能和 fake host 通过，不能替代真实 Windows 或真实 Analytix Host 验收。最终 Analytix 路线是共享 React 面板接既有宿主；新增一套 FundsTrace Electron runtime 不是最终嵌入方式，也不是前置必需品。[现有宿主边界](frontend-host-adaptation.md)、[整合研究](analytix-integration.md)

## 当前观察与证据范围

本轮只读核对 Analytix 根及相关目录 `AGENTS.md`、有界 Git 状态和源码。观察到分支 `main`，HEAD 为 `c7d2a370855a8d371c61c32cc2422515b9404ef2`；工作树有其他未提交修改和未跟踪研究，本文件没有改动或采纳这些内容。下表路径均相对 **Analytix 仓库根**，行号为本轮本地观察；链接固定到该 commit，相关研究源码没有本地 diff。这不是对未来 main、安装包或当前启用状态的承诺。

没有读取实际案件绑定、业务库、Provider／凭据配置、受保护运行状态或会话历史；没有运行 Analytix GUI、服务、Go／Rust 引擎、测试或构建。目录和源码存在，不等于已安装、已获准执行或已完成隐私验收。

## 可视分析算法与生命周期的取舍

| 本地源码观察 | 可借鉴的思路 | 不应直接复制或推断 |
| --- | --- | --- |
| [src/renderer/src/data-analysis/features/flow/graph/GraphPage.tsx:84–145](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/graph/GraphPage.tsx#L84-L145)，147–176 | React 外壳与 graph runtime 分离；先等 readiness，再 attach；卸载 detach，订阅可释放，案件改变后重新绑定。 | 不复制后端发现、桌面 bridge、工作区或案件来源。React cleanup 本身不构成权限撤销。 |
| [src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/layout_worker.js:12–20](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/layout_worker.js#L12-L20)，34–99 | compact、network、hierarchy、flow 四种布局通过 worker 合同调度；返回进度、序号和算法版本。 | 不整组拷贝 `window.__ANALYTIX_*` 全局装配或全量节点业务对象。Web Worker 是响应性工具，不是案件权限或强隔离证明。 |
| [src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/layout-worker-contract.js:261–319](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/layout-worker-contract.js#L261-L319)；[layout-worker-scheduler-controller.js:87–118](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/layout-worker-scheduler-controller.js#L87-L118) | 输入／结果有限投影，相同请求合并，旧 timer 清理；使用 seq 和绑定拒绝过期结果。 | 不把算法版本、trace id 或 UI 请求序号当宿主 grant。取消旧 UI 请求不代表服务端写操作回滚。 |
| [src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/flow.js:512–553](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/flow.js#L512-L553)；[hierarchy.js:250–281](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/hierarchy.js#L250-L281) | 按资金方向、连通分量及标签空间布局；层级模式复用垂直方向。 | 布局方向只服务阅读，不重新定义付款／收款、资金路径证明或交易聚合。 |
| [src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/compact.js:1577–1608](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/compact.js#L1577-L1608)；[tools/analysis_compute/src/flow_layout_network_plan.rs:34–119](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/tools/analysis_compute/src/flow_layout_network_plan.rs#L34-L119) | semantic cluster、标签碰撞半径、扇区、中心与 territory 分配，适合研究复杂图的可读性。 | 不把庞大 network 实现整体搬入，也不先复制 Rust 进程链。先定义合成图、可读性与耗时基准，再选择一个实际问题。 |
| [src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/graph-minimap-core.js:49–109](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/graph-minimap-core.js#L49-L109)，118–207 | bounds 同时包含节点与当前 viewport；保持比例居中；overview 对节点／边限量抽样，viewport 单独绘制。 | 抽样仅限缩略图显示，不能改变完整图、金额、笔数或导出事实；现有默认样本上限不是 FundsTrace 的性能承诺。 |
| [src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/minimap.js:72–124](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/minimap.js#L72-L124)，156–173 | 点击采用与绘制相同的比例／padding 逆变换，滚轮缩放；destroy 清监听与实例。 | 不照搬固定画布尺寸、滚轮系数或设备像素处理，需用目标窗口、DPI、触控板及合成大图验收。 |

现有图引擎还依赖 renderer、标签、边路由和 PII projection 等模块，[graph_engine.js:6–52](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/data-analysis/features/flow/runtime/embedded/analysis_flow/graph_engine.js#L6-L52) 表明它不是独立可搬移组件。若后续复制或实质改编代码，应固定 commit、文件和最小改编范围，核对其依赖来源／许可并保留所需通知；本文件只提出思路，没有复制实现。

当前不能把算法源码当现成服务：Python `/layout-role-graph`、`/layout-node-plan` 在案件检查后仍返回 `403 FLOW_CONTENT_CONTROLLED_ARTIFACT_REQUIRED`，[backend/app/api/v1/flow.py:89–94](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/backend/app/api/v1/flow.py#L89-L94)、[266–285](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/backend/app/api/v1/flow.py#L266-L285)。桌面 data-analysis manager 的 `request()` 同样拒绝 native authority 不可用，[src/main/data-analysis/backend-manager.ts:472–477](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/data-analysis/backend-manager.ts#L472-L477)。后续不能解除这些保护来“接通”页面。

## 共享面板如何进入未来插件库

FundsTrace 已有共享 React 入口 [frontend/src/panel/index.ts](../frontend/src/panel/index.ts)。[host.ts](../frontend/src/panel/host.ts) 定义 `funds-panel.v1`、一案 context、`scopeKey/generation`、session signal、用例级 client 与可选动作；[HTTP adapter](../frontend/src/adapters/http.ts) 提供独立模式的案件绑定与 `revoke()`。面板不自行选择服务或案件；独立外壳拥有案件选择、浏览器路由和文件／下载导航。

这仍是本项目 UI port。`File`、`AbortSignal`、原值 DTO 和 session 对象不是已接受的 Electron IPC、Analytix capability 或 protected-display 合同。宿主 adapter 必须转换为有限可序列化请求、受批准的 source handle／operation id、safe view model 及 Core protected display。当前入口可复用，不意味着当前数据可直接进入宿主共享 store、聊天、日志、Provider 或普通导出。

Analytix 当前已经有一部分可参考的生命周期，但范围有限：

| 当前事实 | 对后续 FundsTrace 的约束 |
| --- | --- |
| [src/renderer/src/components/BuiltinOfficePlugins.tsx:18–54](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/components/BuiltinOfficePlugins.tsx#L18-L54) 从 Core 读取状态；启停发送 generation／expectedRevision；无论结果如何都重新读取。 | 复用“读当前状态→请求变更→重新确认”的模式，不能乐观翻转开关或将丢失 ACK 当失败重试。 |
| [packages/runtime/src/contracts/plugin-package-host.ts:3–38](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/packages/runtime/src/contracts/plugin-package-host.ts#L3-L38) 只列 Documents、Spreadsheets、Presentations、Canvas 四个 package id；invoke 仅 `workspace-editor`；view 是 `development-source`、`publishable:false`。 | 现有 Host 不是可直接注册任意面板的通用入口。需要后续接受 Funds contribution／lifecycle 扩展，不把 FundsTrace 伪装成 editor kind。 |
| [src/main/ipc/register-app-ipc-handlers.ts:2759–2769](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/ipc/register-app-ipc-handlers.ts#L2759-L2769) 限主窗口 main frame；[plugin-package-host-ipc.ts:13–16](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/ipc/plugin-package-host-ipc.ts#L13-L16) 拒绝 renderer 直接 invoke 对象操作。 | 侧栏和插件网页不自动获得执行权限；不将整个 preload 交给 iframe，不借 localhost 绕过主 frame／Host 检查。 |
| [packages/runtime-go/internal/app/pluginpackagehost/service.go:257–310](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/packages/runtime-go/internal/app/pluginpackagehost/service.go#L257-L310)，331–426 核验签名 activation／generation／principal，启停后释放捕获 scope，调用前后重新核验。 | UI enabled、包已物化、adapter ready 和某案件获准使用是不同状态。旧 scope 不得复活；调用后拒绝过期结果不代表已有副作用被撤销。 |
| [src/renderer/src/components/chat/SidebarProjectsSection.tsx:1269–1286](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/components/chat/SidebarProjectsSection.tsx#L1269-L1286) 在项目下挂数据分析入口。 | 优先在现有 workspace 上派生入口；不新增独立案件真源或永远存在的全局资金侧栏。 |
| [src/shared/ui-plugin.ts:1–7](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/shared/ui-plugin.ts#L1-L7) 的 UI 插件是声明式形象／主题包，不允许 JS／HTML／自定义 CSS；[PluginMarketplaceView.tsx:104–145](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/renderer/src/components/PluginMarketplaceView.tsx#L104-L145) 还有展示用 installed 标记。 | 主题包不能承载业务能力；marketplace 的本地 installed 标记不能成为案件授权或插件可执行状态。 |

下面四类接口是**后续待接受的责任合同**，不是当前已存在的新 API 名称：

1. **contribution／发现**：Core 返回经安装身份、物化代次和贡献声明验证的面板信息及协议范围；FundsTrace 提供共享 React 入口和领域用例，不提供任意代码加载器或 URL 代理。
2. **enabled／启停**：Core 持久化期望状态、generation 和 revision，输出当前 availability／原因；插件库只显示该投影。启用与关闭确认后都重新读取，冲突则刷新；不用 localStorage 制造第二状态真源。
3. **case／绑定**：Core 根据当前 principal、workspace、case、snapshot 和能力发出有界 session；adapter 将其转换为 `FundsPanelHostV1`。`scopeKey/generation` 是 UI 失效标识，不是 grant。案件不可用时显示明确状态，不直开库、不回退默认案件或独立 HTTP。
4. **revoke／释放**：先撤销操作与显示权，再取消查询／轮询、终止 worker、释放订阅／minimap、清旧案显示并使旧动作闭包失效。同案重新绑定也要新 scope。已到达数据所有者的事务以持久任务／回执决定结果，重开只查询，不自动重放。

侧栏条目应由 `当前有效 contribution + Core 已确认 enabled/available + 当前工作区案件绑定` 派生；缺少当前能力时可显示不可用原因及受控启用入口。点击后仍需 Host 在操作时核验，隐藏条目不是权限控制。文件、下载、图谱打开和原值显示分别走宿主批准的动作；不得复制 backend、Provider／权限、Go runtime、案件 registry 或受保护数据路径。

最终只保留一个 Go Harness、一个插件身份／能力生命周期、一个案件／快照 authority 和每次用例的一个数据所有者。独立 Python 可以继续服务独立网页；宿主模式不能后台双写或让两个程序同时打开 active-case 库。详细边界见 [analytix-integration.md](analytix-integration.md)。

## Windows 分阶段最小验收矩阵

以下全是未来门槛，本轮没有执行。先确定目标 Windows 版本、架构、分发形式和企业策略；首个最小候选可限一个明确的 Windows x64 目标及普通用户，只有产品实际承诺 ARM64／其他版本时再扩展。没有目标平台证据，不标记“Windows 支持完成”。

| 层与阶段 | 最小候选／场景 | 必须取得的证据 |
| --- | --- | --- |
| A. 网页业务成熟 | Python＋现有 React／G6，合成多案件；精确金额／ID／时间、导入失败、恢复、来源、图显示与导出。 | API、浏览器流程及独立资金 oracle 分开记录；完整追踪图与显示筛选分离，迟到请求不能换案串案。先达到明确业务范围，再讨论桌面。 |
| B1. 宿主契约 | 共享面板＋fake host；启用、缺能力、同案重新绑定、A→B→A、撤销与旧闭包。 | contribution／enabled／case／revoke 合同，有限序列化输入、safe view 与 protected-display 分工。fake host 只证明本地 UI 协作。 |
| B2. 实际 Windows 开发运行 | 未来获准的薄壳或真实 Analytix adapter，二者分别验收；普通用户、中文／空格路径、可写外部数据根、离线资源。 | 实际 Windows 启动、文件选择／保存、DPI／缩放、键盘、资源加载与下载；没有用户安装 Python／Rust 的隐含依赖，不能写入只读安装目录。 |
| B3. 进程与恢复 | 两个实例、服务就绪前操作、计算进程崩溃、renderer 重载／销毁、关闭窗口、正常退出、强杀、注销／关机。 | 只管理自己创建并持有句柄的进程；无猜端口接管、全局 kill 或残留锁。重新打开识别未决任务，旧 renderer／代次无法调用，关闭一方不影响另一项目。 |
| B4. 导入中断 | 解析／预览、提交前、数据库提交边界、文件补偿及结果 ACK 丢失；磁盘空间／权限异常。 | 源行、金额、方向与回执对拍；完整提交或可识别恢复状态，没有半批次或重复导入。退出、请求取消和服务端事务结果不混同。 |
| C1. 安装／升级 | 明确安装包与最终资源；全新安装、有旧数据升级、失败升级／回滚、重启、卸载保留策略。 | 实际 Windows 安装入口、离线启动、目录权限和包内资源核对；用户数据与安装资源分离，不读取真实业务样本验收。开发运行不能代替此项。 |
| C2. 签名／发行 | 最终安装器和适用第一方 PE，受保护发布身份；发布与更新路径。 | 签名链、哈希算法／时间戳、签名验证和完整性证据绑定最终候选；许可／来源／通知、更新和安装行为分别验收。签名成功不等于发行授权，也不保证 SmartScreen 无提示。 |

如果后续确有独立桌面需求，薄壳只负责窗口、受限系统动作及自有进程；独立身份、数据根和生命周期与 Analytix 分离。若目标是入驻 Analytix，则使用它现有窗口、preload→main→Go Host 和发行链，不把独立薄壳整体套入宿主。[桌面边界比较](electron-boundary.md)

## Windows 安全、进程与签名依据

- Renderer 保持 `nodeIntegration:false`、`contextIsolation:true`、`sandbox:true`、`webSecurity:true`，使用严格 CSP，限制导航／新窗口／默认权限；preload 每个用例一个有限方法，main 验证 sender/frame、上下文和输入。不要暴露通用 `ipcRenderer`、命令、路径、SQL 或任意 URL。[Electron Security](https://electronjs.org/docs/latest/tutorial/security)、[ipcRenderer](https://electronjs.org/docs/latest/api/ipc-renderer)
- Main 管理应用生命周期与窗口，renderer 不拥有业务进程。`utilityProcess` 是 Node＋MessagePort 子进程接口，不能被描述为 Python／Rust 的通用替代或完整安全沙箱；其 readiness、退出、消息和回收应单独验证。[Process Model](https://www.electronjs.org/docs/latest/tutorial/process-model)、[utilityProcess](https://electronjs.org/docs/latest/api/utility-process)
- Windows 注销、系统关机或重启时可能不发出 `before-quit`、`will-quit`、`quit`；`app.exit()` 也绕过正常退出事件。因此持久任务恢复、原子写与数据一致性不能只依赖退出 hook。进程崩溃和 renderer 崩溃应分别处理。[Electron app](https://www.electronjs.org/docs/latest/api/app)
- 自分发 EXE／MSI 与 Store MSIX 的签名责任不同；不要为了演示而改变分发形式或创建签名材料。发布时采用受保护证书／签名服务，核对适用 SHA-256 文件及时间戳算法，并用 SignTool 等实际验证最终文件。证书、私钥和口令不进入源码、文档或前端。[Microsoft code-signing options](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)、[SignTool](https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool)
- **不承诺 EV、OV 或 Azure Artifact Signing 自动免除初始 SmartScreen 提示。** Microsoft 当前文档说明新文件仍可能需要积累信誉，EV 已不再提供即时绕过。Electron signing 页含有不同概括，此处采用 Windows 平台方当前规则，并在真实分发路径记录观察结果；不要求用户关闭系统保护。[Microsoft 当前规则](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options)、[Electron signing 文档](https://electronjs.org/docs/latest/tutorial/code-signing)

上述官方材料说明平台机制，不能替代本项目配置、实际二进制或目标环境验证。没有据此引入新的构建工具、Electron 版本、证书、CI 或发布服务。

## 研究方法与本文件检查

本轮使用指定 Exa Search 技能及 `searching.md`、`source-quality.md`。完成 **2 次有效检索，每次 `numResults:5`，`sources_reviewed:10` 表示请求结果席位之和**；它不表示审阅了 10 个独立、高质量、相互验证的来源。另有 2 次请求在 schema 层被拒、未执行检索，修正参数后才完成上述有效调用。通过 Exa fetch 补读 7 个官方 URL，和检索结果存在重叠。

采用 Electron／Microsoft 平台文档及工具维护方材料作为一手来源；正文判断优先采用与问题直接对应的平台方规则。旧 AppX 页面只作背景，没有据其旧版本描述承诺当前系统支持；Electron 与 Microsoft 的 SmartScreen 表述差异如上保留，没有强行合并成免提示承诺。

本文件只做文档差异、引用路径和链接格式核对；没有新增测试、启动浏览器或执行平台验收。未来开始桌面或 Analytix 施工时，重新核对 HEAD、适用规则、相关 dirty overlap、Host capability 与验收范围。三层证据分别报告，不把研究建议转写为已集成可用。
