# 共享资金面板与宿主适配

本文记录 FundsTrace 的面板边界、既有功能覆盖和分阶段验收要求，不是运行、安装或发行证明。当前继续维护独立 Python 服务及 G6；没有复制 Analytix 工程、安装 Electron、创建桌面壳或接通 Analytix。原目录、业务数据及宿主项目均不在本轮修改范围内。

## 当前模块与契约

独立版由外壳管理案件列表、新建案件、品牌导航、浏览器地址及激活入口；案件内的流水、导入记录、恢复与来源核对由共享面板提供。宿主加载面板时不应再得到一套独立案件侧栏。[独立入口](../frontend/src/App.tsx)、[面板导出](../frontend/src/panel/index.ts)

| 模块 | 当前职责与边界 |
| --- | --- |
| `frontend/src/panel/FundsPanel.tsx` | 接受 `FundsPanelHostV1`，显示宿主提供的一案内容。没有默认 HTTP client，不自行选择服务地址、案件或数据根。 |
| `frontend/src/panel/host.ts` | 定义 `FUNDS_PANEL_VERSION = 'funds-panel.v1'`，以及上下文、绑定、案件 client、动作和 session。它是本项目 UI port 版本，不等于 HTTP `/api/v1`，也不是已接受的 Analytix IPC schema。 |
| `frontend/src/adapters/http.ts` | `createHttpFundsSession({context, scopeKey, generation, transport, actions?})` 返回 `{session, revoke}`。独立模式将现有 `FundsApi` 一次绑定到案件；共享面板调用时不再传 `case_id`。 |
| `frontend/src/panel/styles.css` | 面板样式使用 `:where(.funds-trace)` 前缀，而非 CSS `@scope`；named container `funds-panel` 按实际分栏宽度响应。接收 `--funds-font-family`、`--funds-text`、`--funds-background`、`--funds-accent`、`--funds-border` 和 `--funds-muted`。宿主的 token 映射及完整 CSS 互不污染仍需联调验证。 |
| `frontend/src/styles.css` | 独立入口样式组装；嵌入者使用 `panel/index`，不导入独立页面的根布局和全局样式。 |

`FundsPanelHostV1` 持有版本和可为空的 session。session 包含 `{context: {caseId, name}, scopeKey, generation, signal, client, actions?}`；`client.binding` 的 `caseId/scopeKey/generation` 必须与其一致。每次重新绑定或权限撤销后的新授权，要更换 `scopeKey` 或增加 `generation`，不能用相同三元组复活旧闭包。

案件 client 只暴露统计、流水分页、上传预览、重新预览、提交、任务查询、导入会话列表、流水来源和清理这几个用例。可选动作是 `openGraph`、`openLegacyImport`、`downloadSource({sourceId, filename})`；缺少动作时明确提示或禁用，不在面板内部拼接备用 URL。它不包含任意 SQL、文件路径、命令、Provider 或通用 IPC 入口。[完整类型](../frontend/src/panel/host.ts)

当前 `preview(file: File)` 是浏览器试点输入，不是可直接跨 Electron IPC 的文件合同。未来由宿主文件选择器获得批准的来源 handle，再通过专门 adapter 接入；不要把 renderer 传来的绝对路径当授权。现有 DTO 仍包含独立版原值，不能直接进入 Analytix 的共享 store、聊天、日志或 Provider。safe view model 与 Core protected display slot 尚未实现；不能因为 client 已按案件绑定，就宣称符合宿主隐私边界。[Analytix 边界研究](analytix-integration.md)

`AbortSignal` 同样是 renderer 内协作接口，不应原样跨 preload／IPC。未来 adapter 需将文件和取消转换为宿主批准的来源 handle、操作 ID 与受限取消方法，main 对窗口、案件和代次重新核验。当前 UI 类型不替代这份序列化和权限合同。

## 生命周期与独立版兼容

HTTP adapter 捕获案件和绑定，组合每次请求的 `AbortSignal` 与 session 生命周期，并在分发前和异步返回后核对是否已撤销。`revoke()` 立即中止该 session，旧 client 和旧动作不应再启动新操作。面板同时拒绝错案响应、忽略迟到结果，切换三元组时清空筛选、详情、导入及来源显示。[adapter](../frontend/src/adapters/http.ts)、[取消轮询](../frontend/src/panel/cancellation.ts)

撤销或关闭面板不等于取消已到达服务端的导入事务。后台任务是否完成、回滚或等待恢复，以持久任务状态和回执为准；重开后重新查询，不靠旧 UI 状态重放。上传保留、来源验证和清理仍由统一 Python 服务拥有，见 [导入生命周期](import-lifecycle.md)。

`session.signal` 同步传播给面板及子流程的查询、动作和轮询控制器，派发入口先检查撤销；不用等待 React 卸载才通知异步动作。该机制是协作取消，宿主必须核对自己的授权并遵守 signal；它不能阻止故意忽略信号的副作用。独立外壳每次实际创建绑定都分配新 scope，包括同案更名或动作 factory 替换，避免复用已撤销的组件状态。

独立版继续使用现有启动入口、`FUND_DATA_DIR`、HTTP API、`/workspace/` React 页和 `/` G6 页。G6 跳转由独立外壳提供并绑定案件；嵌入者可提供不同动作。两个运行模式各自选择一个数据所有者，不共享 active-case 数据库，不后台双写，也不因宿主能力不可用而偷偷回退默认案件或直连 Python。

### 开发期合成宿主

`npm run dev` 的 `/workspace/host-preview/` 使用内存 transport 和合成案件，不请求 HTTP 或业务数据库。可操作换案、同案重新绑定、撤销、600px 分栏及 800ms 查询延迟，并与外部宿主按钮／标题同屏比较。它不授予图谱或下载动作；导入方法返回明确的 fixture 不可用错误，不能把该页当真实导入验证。[入口](../frontend/src/main.tsx)、[合成宿主](../frontend/src/host-demo/HostPreview.tsx)

入口只从 `import.meta.env.DEV` 分支加载；生产构建还须检查没有 fixture 内容。源码里有此条件不替代构建证据。独立合成后端由 Vite 的 `FUNDS_TRACE_DEV_API_PORT` 选择，固定主机 `127.0.0.1`、默认 8300，允许端口 1–65535；图谱和分析页面仍走精确代理，独立导航不硬编码开发端口。[Vite 配置](../frontend/vite.config.ts)

## 原业务功能地图与 React 缺口

下表来自保留界面的源码入口核对。表中的“保留”表示入口与实现仍在，不表示本轮对每一项完成浏览器或算法验收。React 当前是案件内纵切，不能称为原功能已全部迁移。

| 业务区域 | 保留界面／服务 | React 覆盖及明确缺口 |
| --- | --- | --- |
| 案件与交换 | [app.js](../static/app.js)、[case_packages.py](../app/case_packages.py)：选择、新建、删除、案件包导入导出 | 独立外壳选择／新建；删除及案件包操作仍在保留界面，宿主模式的项目绑定另由 Host 管理。 |
| 流水查询与核对 | [app.js](../static/app.js)、[main.py](../app/main.py)：账号、户名、对手、关键词、金额、时间、已上图及分页 | 已有账号／关键词分页、单笔详情和来源追溯；其余筛选、画布追踪条件仍待迁移。 |
| 导入与批次 | [app.js](../static/app.js)、[imports.py](../app/services/imports.py)：A/B 映射、主账号候选、手工本方账号、进度、批次删除 | 已有 v1 预览／提交、记录恢复、回执、清理和来源下载；旧手工本方输入及批次删除尚未完整迁移。 |
| 追踪集合与多画布 | [app.js](../static/app.js)、[main.py](../app/main.py)：单笔上／下图、批量来源／去向、多画布新建／复制／命名／删除／保存布局 | React 提供 G6 入口；画布及追踪语义没有重写。 |
| G6 图谱操作 | [index.html](../static/index.html)、[app.js](../static/app.js)：力导向／层次布局、拖拽缩放、小地图、边折点／标注、金额／时间／摘要／币种及身份／照片显示 | 保留原 G6 页面；尚未成为共享 React 图组件，也没有在 Electron 内验收。 |
| 账户工作台 | [app.js](../static/app.js)、[main.py](../app/main.py)：主／同名账号、原始与图去重统计、TOP 对手、身份、照片上传裁剪、重点节点 | React 尚无账户抽屉和标注工作台；不能用流水详情替代这些能力。 |
| 身份表与流水上下文 | [app.js](../static/app.js)、[main.py](../app/main.py)：身份模板／导入、账户账本、交易前后记录、疑似来源去向及选中上图 | 保留界面拥有这些入口，React 尚未迁移。 |
| 分析与报告 | [analysis.js](../static/analysis.js)、[report.py](../app/report.py)：环路、快进快出、离群、路径结果及 HTML 报告 | 尚未迁入共享面板。原分析页若有控件但未进入实际请求，不能按控件存在宣称参数已生效；算法契约另作核对。 |
| 导出 | [app.js](../static/app.js)、[main.py](../app/main.py)：追踪 XLSX／待调取账号、PNG／PDF／可拖 HTML 图谱和案件包 | React 目前只有来源下载动作；报告、图谱和业务导出仍需独立的宿主权限／保存合同。 |

保留界面的 `prompt/confirm/alert`、`window.open` 下载及同源 `/api`／`/static` 假设不是桌面适配完成。尤其业务导出必须等真实生成／保存结果后再提示成功；保留界面的存在不能代替失败响应及取消验收。错案与迟到结果修复应覆盖账户抽屉、图谱、分析及报告各自的异步边界，不能只检查 React 表格。

### 实测剩余正确性问题：旧链路金额精度

本轮合成浏览器中，原额 `9999999999999999.99` 在 React 核对页精确显示，G6 流水列表显示为 `10,000,000,000,000,000`。旧 `/api` 经 `db.q()` 将 Decimal 转为 float，旧 JS 再调用 `Number`；图谱聚合、账户统计、上下文推荐、分析／报告及金额筛选也包含浮点路径。[旧查询](../app/main.py)、[转换](../app/db.py)、[格式器](../static/app.js)、[分析](../app/analytics.py)

数据库原始 Decimal 和 v1／React 的字符串金额合同继续保留，但本轮不能宣称 G6／分析金额已无损。下一项应先统一原始金额与精确汇总的合同，并把允许近似的统计评分单独标明，回归覆盖筛选边界、汇总、图谱标签、报告与导出回读，再推进 Electron。仅修改一个列表格式器不能修复全部链路；全局把 `db.clean()` 改为字符串还会破坏既有算法，不能在本次面板抽离尾部悄然实施。

## 分阶段清单

以下是按依赖推进的实施与验收清单；未列为完成证据。

| 阶段 | 施工内容 | 验收重点 |
| --- | --- | --- |
| 1. 独立版共享面板 | 独立外壳组装案件 session、HTTP client 和图谱／下载动作；共享面板不读取浏览器 URL、不自行发现后端。保留 G6 和 Python。 | 独立流程及 fake host 分开验证；同案件重绑定、错配 binding、撤销、旧闭包、迟到响应、缺少动作均不能误操作另一案。 |
| 2. 样式与交互适配 | 将宿主 token 映射到 `--funds-*`，面板 CSS 限域；弹层即使进入 top layer 也保留本面板主题。面板容器可缩小，不假设占满整个窗口。 | 与无关宿主按钮／表格同屏，检查双向 CSS 污染；长中文、超长账号、精确大金额、滚动容器、空态及错误态均可读。 |
| 3. 可选薄 Electron 开发壳 | 按下表增加窗口、受限文件与下载动作以及自有服务生命周期；不复制 Analytix 主进程，不继承其身份或目录。 | 两个项目可同时开发和单独退出；合成数据证明目录、进程、授权与案件不串用。没有桌面需求时不提前建立壳。 |
| 4. Analytix adapter | 在后续授权的宿主任务中接已有 preload／main／Go Host，确定能力版本、案件／快照、撤销和受保护显示，再挂现有工作区面板。 | 先验证实际 Host 能力可用及单一数据所有者，再做 renderer 到数据平面的端到端证据；fake host 和独立浏览器通过不等于真实宿主准入。 |

### Electron 适配逐项要求

| 领域 | 目标边界与检查 |
| --- | --- |
| 窗口与导航 | 使用 FundsTrace 自有开发身份和数据根；限制新窗口、外部 URL、导航及权限。图谱和分析窗口由壳明确创建、关闭和撤销，不能继承当前页的全部权限；保留系统窗口控制，不仿造一套 Analytix chrome。 |
| 文件选择 | main 提供受限打开文件动作，绑定当前窗口／案件／代次；取消选择不启动上传，迟到的选择结果不能带到下一案。renderer 不获取任意文件系统能力。Electron 文件对话框属于 main，可绑定父窗口。[dialog](https://www.electronjs.org/docs/latest/api/dialog) |
| 下载与导出 | 源文件按引用验证；业务导出按类型、案件、快照和保存目标授权。区分用户取消、请求失败、生成失败和写盘完成；保存前不显示成功。不能用通用 URL／路径下载绕开来源校验。[DownloadItem](https://www.electronjs.org/docs/latest/api/download-item) |
| 服务生命周期 | 壳只管理自己创建且持有句柄的 Python 进程，明确就绪、故障、退出与重启。退出不得全局查杀，重启不得自动重放中断导入；面板隐藏、卸载与服务退出分别定义。 |
| 键盘与焦点 | Tab 顺序、可见焦点、Escape、弹层焦点约束及关闭后返回触发点；宿主快捷键不劫持输入框、文件选择和弹层。新增热键前明确宿主优先级，不默认安装全局快捷键。 |
| 密度与滚动 | 保留可核对的密集表格和数字对齐；数据区允许局部横向滚动，主导航及主要操作不能被挤出。桌面密度与触摸目标分别评估，不以营销页大留白取代业务信息。 |
| 缩放 | 检查宿主窄面板、系统 DPI、100%／125%／150%／200% 缩放下的控件、表头、长金额和对话框；网页缩放与 G6 画布缩放分开，保留可访问缩放，不用关闭浏览器缩放掩盖布局问题。[webContents](https://www.electronjs.org/docs/latest/api/web-contents) |
| CSS 与主题 | 仅导入共享面板样式；不修改宿主 `body/:root`、不附带全局 reset／根高度／字体。主题变量和弹层边界显式映射；命名空间是样式约定，不是安全沙箱，仍需宿主 CSS 合成验证。 |

Electron 的 `contextIsolation`、sandbox、IPC sender 核验、受限导航和当前版本等要求仍须逐项落实；一个类型化 React 接口本身不授予系统权限。[官方安全指南](https://www.electronjs.org/docs/latest/tutorial/security)。完整进程、存储与发行边界见 [Electron 边界](electron-boundary.md)。

## 三项设计 Skill 的选择与固定来源

本轮只读取原文并借鉴适用规则，没有安装插件或依赖，没有运行远程脚本、设计 detector、hook 或原生 engine，也没有执行完整 Skill 流程。它们不是本项目运行时依赖，仓库营销表述、启发式评分与案例数量均不作为更优效果或合规证明。

| 参考 | 固定默认分支 HEAD 与入口 | 本工作台的选用范围 |
| --- | --- | --- |
| UI/UX Pro Max | `main`，`56ea9cf174ed86786187daa9de0e6247cb0747e4`；[实际 SKILL.md](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/blob/56ea9cf174ed86786187daa9de0e6247cb0747e4/.claude/skills/ui-ux-pro-max/SKILL.md) | 参考键盘、表单反馈、分页／渲染和 React 定向建议。已有宿主设计系统时不重新生成整套视觉方向；其通用规则不覆盖金额精度、案件隔离或业务核对正确性。 |
| Taste | `main`，`f624f37a8c650b9cc550253104925efe4c8de246`；[实际 SKILL.md](https://github.com/Leonxlnx/taste-skill/blob/f624f37a8c650b9cc550253104925efe4c8de246/skills/taste-skill/SKILL.md) | 默认技能明确面向公开页面，排除 dashboard／app screen；不将营销页图片、宏观留白、编排动效用于资金工作台。本机版本与此上游快照不同，不视为自动升级。 |
| Impeccable | `main`，`a231cc5b30ac7747f8088d076ece12518f2d839d`；[实际 SKILL.md](https://github.com/pbakaus/impeccable/blob/a231cc5b30ac7747f8088d076ece12518f2d839d/.agents/skills/impeccable/SKILL.md) | 优先参考 Operate：一致、可扫描、标准操作、高密度表格和完整状态；结合 audit／harden 检查可访问性与异常。避免将主张大胆表现的通用段落覆盖宿主视觉和任务效率。 |

Impeccable 的直接适用参考为 [Operate](https://github.com/pbakaus/impeccable/blob/a231cc5b30ac7747f8088d076ece12518f2d839d/.agents/skills/impeccable/reference/operate.md)、[audit](https://github.com/pbakaus/impeccable/blob/a231cc5b30ac7747f8088d076ece12518f2d839d/.agents/skills/impeccable/reference/audit.md)、[harden](https://github.com/pbakaus/impeccable/blob/a231cc5b30ac7747f8088d076ece12518f2d839d/.agents/skills/impeccable/reference/harden.md)。正式入口要求 `context` launcher，可能下载原生二进制；安装还可能写宿主 hook。只参考这些文本不等于完成该流程或 detector 检查，也不能用其分数声称满足 WCAG。若未来正式引入，单列版本、执行行为、作用范围与验证，避免全局安装使其他项目规则随之改变。

UI/UX Pro Max 与 Taste 为 MIT，复制实质内容需保留版权及许可；Impeccable 为 Apache-2.0，分发适用衍生内容需保留许可、修改标记及适用 NOTICE。[UIUX 许可](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/blob/56ea9cf174ed86786187daa9de0e6247cb0747e4/LICENSE)、[Taste 许可](https://github.com/Leonxlnx/taste-skill/blob/f624f37a8c650b9cc550253104925efe4c8de246/LICENSE)、[Impeccable 许可](https://github.com/pbakaus/impeccable/blob/a231cc5b30ac7747f8088d076ece12518f2d839d/LICENSE)、[NOTICE](https://github.com/pbakaus/impeccable/blob/a231cc5b30ac7747f8088d076ece12518f2d839d/NOTICE.md)。外部字体、图片、图标及 FundsTrace 历史源码的权利另核；这些 Skill 的许可证不替代项目开源许可。

## 本轮本地验收记录

环境为 macOS arm64、Python 3.11.15、Node 22.22.1。完整 Python 回归 **166 项通过**，其中 legacy Node VM 包装测试执行 **9 个行为场景**；前端 **37 项测试通过**，类型检查与 Vite 生产构建通过。API 契约无漂移，最终生产产物确认不含合成宿主 fixture。VM 使用可控迟到响应；非 HTTP Host 回归另覆盖同一轮撤销同步传递、StrictMode、旧动作拒绝与首查询撤销后不派发第二次查询。

本轮浏览器使用一次性合成后端及纯内存 Host，流程与健康状态如下：

1. **独立核对入口：通过。** 保留原始精度与案件统计；原有低对比度小字已调整。桌面基线截图 `01-before-workspace.png`、`02-before-inspector.png`。
2. **合成 Host 分栏：通过。** 600px slot 的表格局部横滚，外部宿主按钮保留原样式；换案、A→B→A 迟到查询、同案重绑定及撤销清旧数据显示。键盘 Escape 关闭核对后回到触发按钮。最终截图 `08-final-shared-panel.png`。
3. **生产构建独立业务流：通过。** 找回先前待确认会话、重新预览、确认导入两笔、重读回执、显示来源并经宿主动作下载；下载与合成原件逐字节一致。换到空案后旧案流水消失。截图 `07-built-source-download.png`。
4. **React／G6／分析导航：通过本次范围。** 先在另案打开 G6，再从 React 打开当前案，分析页仍为当前案；分析换案清结果并禁用旧报告，返回链接携带新案件。9 个 VM 场景还覆盖刷新深链、迟到图／列表、画布状态及报告请求。截图 `05-g6-case-bound.png`、`06-analysis-switched.png`。这不证明原算法及所有导出已验收；上面的旧金额精度问题仍在。
5. **390px 窄窗口：通过本次范围。** 修复侧栏 min-content 将单列 Grid 撑到 453px 的问题后，document／panel 均为 390px，无页面横向溢出；新建案件仍可达，弹层可用 Escape 关闭。截图 `09-narrow-standalone.png`。这是浏览器视口验证，不是物理触摸、系统 DPI、屏幕阅读器或 Electron 证据。

截图和候选摘要在本地忽略目录 `output/panel-audit/`，不随源码发布。真实 Analytix、Electron 进程／IPC／系统文件选择、安装升级和完整 WCAG 合规均未验收；当前完成的是独立版共享面板边界和上述合成验证。
