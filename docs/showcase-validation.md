# 主页展示与素材核验

## 2026-10-21 · 首页启动动画

首页首图采用维护者确认的独立设计预览 `bilingual.html#original`：**A 原版 Logo、中文在上英文在下、持续等待态**。只截取中间应用窗口，保留 40% 合成工作台背景、Logo 柔光与持续滑动的加载条，不包含外围播放控件，不触发“模拟就绪”或淡出转场。这是设计预览，不是程序启动录屏，也不表示程序或既有安装包已改变。

[循环动图](media/showcase/startup-wait.png)为无损 APNG，1204 × 826，4.3 秒、84 个编码帧、无限循环，3,143,182 B；从浏览器 650 × 840 CSS 像素、2× 设备像素比的窗口区域逐帧采样，86 个源帧按 50ms 间隔编码，合并相同帧后时长不变。[静态封面](media/showcase/startup-wait-poster.png)与动图首帧像素一致；README 沿用 `picture`，减少动态效果时选用封面。

本轮只更新主页展示文档及媒体，不修改程序代码、许可、安装配置或发布物。全部动画帧与对应浏览器源帧逐像素一致；加载条区域跨帧变化、4.3 秒总时长及无限循环标记已核对。

已在 GitHub 原生分支主页（媒体内容提交 `2b9fe94`）检查 1440 × 1100 桌面及 390 × 844 手机视口：动图成功解码为 1204 × 826，两种宽度均无页面横向溢出；桌面连续截图中的加载条区域实际变化，确认线上没有被转成静态图。手机减少动态效果时，`currentSrc` 切换为静态封面并成功解码。这些记录仅验证主页展示，不增加程序或安装包验收声明。

## 既有业务界面素材

下述业务界面展示图于 **2026-10-20（America/Los_Angeles）** 在 `codex/unified-app-icon` 隔离工作树重新采集。运行界面来自 **0.1.8 源码候选**，基于 `1826c355301db16803c90bffa5d42757af16b0f0`，包含本轮统一应用图标的工作树修改。版本号和源码基线不表示这些图标已经进入任何既有安装包。

## 当前素材

| 内容 | 入口与规格 |
| --- | --- |
| 业务界面展示 | [展示版 PNG](media/showcase/hero.png)，1600 × 1180；[专注图谱](media/showcase/workspace-focus.png)与[完整工作台](media/showcase/workspace.png)，均为 1600 × 1000 |
| 数据导入 GIF | [11.4 秒 / 4 个步骤](media/showcase/import-demo.gif)，1200 × 886，293,662 B |
| 数据分析 GIF | [9 秒 / 3 个步骤](media/showcase/analysis-demo.gif)，1200 × 886，203,675 B |
| 静态替代 | [导入封面](media/showcase/import-poster.png)／[分析封面](media/showcase/analysis-poster.png)，与相应 GIF 首帧像素一致 |
| 导入源截图 | [选择文件](media/showcase/import-select.png)、[字段映射](media/showcase/import-mapping.png)、[数据预览](media/showcase/import-preview.png)、[完成结果](media/showcase/import-complete.png)，均为 1600 × 1000 |
| 分析源截图 | [设置条件](media/showcase/analysis-config.png)、[路径结果](media/showcase/analysis-results.png)、[报告确认](media/showcase/analysis-detail.png)，均为 1600 × 1000 |
| 业务逻辑 | [SVG](media/fundstrace-workflow.svg)／[2× PNG](media/fundstrace-workflow.png)，导入核对 → 分析追踪 → 整理导出 |
| 本机架构 | [SVG](media/fundstrace-architecture.svg)／[2× PNG](media/fundstrace-architecture.png)，界面、所属后端与两类存储 |
| 公开合成输入与历史导出 | [样例说明](examples/harbor-path/README.md)、[CSV](examples/harbor-path/fundstrace-synthetic.csv)、[XLSX](examples/harbor-path/fundstrace-synthetic.xlsx)；历史[图谱](examples/harbor-path/exports/traced-graph.png)、[追踪流水](examples/harbor-path/exports/tracked-flows.xlsx)、[分析报告](examples/harbor-path/exports/analysis-report.html)的核验边界见下文 |

9 张业务源 PNG 均由真实浏览器视口直接截图。导入／分析 GIF 由这些 PNG 编码，是带阶段字幕的步骤演示，不称连续录屏；没有 MP4 输入、转换或新视频产物。生成脚本仅等比缩放完整截图，字幕、边框与进度放在产品画面之外；没有重画、遮盖或替换产品画面中的 Logo、控件、节点或内部状态。GIF 调色压缩不改变业务内容。

应用页头、React 侧栏、浏览器标签页和 README 共用新图标的 PNG／ICO 派生资源，不再按深浅主题切换标识。业务与架构 SVG 的文字转为路径，页眉内嵌同一应用图标 PNG，无脚本、远程图片或字体请求。React 仍为案件／导入／核对试点，G6 仍承载图谱；没有新增 Rust、AI、云端或其他宿主能力声明。来源和重建入口见[品牌资产](brand/README.md)。

## 2026-10-20 实际采集与检查

环境为 macOS arm64、Python 3.11.15、Node 22.22.1、npm 10.9.4、Pillow 12.3.0 和系统 Chrome 155 的独立无头上下文。源截图使用 1600 × 1000 CSS 像素、设备像素比 1。通过 `scripts/preview_synthetic.py` 启动随机回环端口，仅使用它创建的一次性临时数据库、公开合成输入和运行设置；不连接业务服务或原业务目录。

| 行为 | 本轮实际观察 |
| --- | --- |
| XLSX 导入 | 在独立的“云杉项目”和“导入核对”合成案件选择公开 XLSX，识别为单账户流水；均新增 12 笔、重复 0、跳过 0，共 7 个账户。导入 GIF 四帧来自同一“导入核对”案件 |
| 主账号 | 在预览中仅保留星桥商贸 `SYN-B001`；其余本方主账号取消 |
| 锚点追踪 | 在“云杉项目”查询 `SYN-PATH-001` 得到 1 笔，按去向、最近 5 笔追踪后，页头显示已上图 5 笔 |
| 完整图谱与保存 | 重置查询，在流水全览选择“本页全部上图”后为 12/12；图谱形成 7 个账户、8 条关系。选择资金流向分层、金额和全部标签，保存“分流与归集 · 合成路径”，再收起查询区并适应画布 |
| 全案分析 | 仅选路径追踪，`SYN-A001 → SYN-F001`、最大 3 跳，得到 6 条路径；截图保留“按交易边累加，非去重本金”和“需人工复核” |
| 报告入口 | 打开“生成 HTML 报告”的重新计算确认页并截图；确认后下载成功，临时 HTML 文件为 15,550 B。本轮未替换公开样例的历史导出文件，也未重做其逐笔验算 |
| 图标入口 | G6、全案分析、React 工作台和版本／许可页均实际加载 `/static/logo.png`，解码尺寸 256 × 256；页面使用 `/static/favicon.ico` 与 `/static/favicon-32.png`。React 与版本／许可页的两个 favicon 请求均返回 HTTP 200 |

6 条路径的独立解释：青禾支路的两笔入边 × 两笔出边形成 4 种组合，其余两支各 1 种。路径算法是交易边的有界拓扑组合；金额按边累加，非去重本金，也不保证交易时间递增。全部名称、账号、交易和业务背景均虚构。

本轮实际执行：

- 在 `frontend/` 执行 `npm ci`、`npm run build`、`npm test`：安装、TypeScript／Vite 构建通过，6 个测试文件的 38 项测试通过。Vite 保留既有桌面更新静态脚本／样式的运行时引用提示。
- 用 Playwright 操作真实合成界面，重新采集上述 9 张源截图，查看源图和生成结果；不向页面注入展示数据或替换截图内容。
- 运行 `python docs/brand/render-showcase-media.py --font "/System/Library/Fonts/STHeiti Light.ttc"`，直接重建主视觉、两段 GIF 与静态封面。导入四帧时长为 2600／2800／2800／3200ms，分析三帧为 2800／3000／3200ms；逐帧解码、尺寸、时长和静态首帧一致性检查通过。
- 检查本轮文档的本地相对引用及 `git diff --check`。没有重新运行 GitHub Markdown API、线上主页、390px 移动端或减少动态效果的完整验收；这些历史结果不能作为本轮候选的新证据。

GitHub 仓库设置中的 Social preview 已于同日上传本轮 [1280 × 640 品牌图](media/fundstrace-social.png)，刷新设置页后确认保留新图。该设置独立于代码分支生效。统一图标的 PR #27 已合并到 `417ff3d9cf17a5f7d31c5d6c89b39dd3ee4a6609`，仓库主分支已展示新图标；这不表示既有安装包已经改变。

## 2026-10-20 · GitHub 主页排版精修

本轮在 `codex/github-homepage-polish` 隔离工作树调整 README。采用 [Impeccable 4.7.0](https://github.com/pbakaus/impeccable/blob/main/.agents/skills/impeccable/SKILL.md) 的 distill / polish 方法，参考 [PowerToys](https://github.com/microsoft/PowerToys) 的简明导航和 [Immich](https://github.com/immich-app/immich) 的品牌与实景展示层级；没有安装上下文加载器、复制其他项目品牌或添加运行依赖。

Logo 从 64px 调整为 112px，与原生 `FundsTrace` H1 组成同一标题区；保留口号，收敛为下载、指南、演示三个导航入口。首屏直接引用现有工作台截图，避免重复的外框标题。三步流程、产品操作和导出说明优先展示，开发架构与源码运行折叠到页尾。采集版本、制作过程和平台验证细节留在本文及桌面文档，README 保留合成数据、金额口径与人工复核说明。

实际在线检查对应 README 内容提交 `700597fffb5239a169c2adb599323826adde8bca`，使用 GitHub 分支主页的原生渲染，没有注入 HTML 或修改页面样式：

- 1440 × 1000 与 390 × 844 CSS 像素，均检查深色、浅色主题。页面宽度与视口相同，README 的 `scrollWidth` 与 `clientWidth` 相同；标题、导航、图片没有横向溢出。390px 下三个导航入口保持一行。
- 8 个图像入口全部加载、解码成功，包括应用图标、三个徽章、工作台截图、两段演示和折叠区架构图。
- 正常动态偏好下两段演示使用 GIF；`prefers-reduced-motion: reduce` 下实际 `currentSrc` 分别为 `import-poster.png`、`analysis-poster.png`，均成功解码为 1200 × 886。普通静态封面链接继续保留。
- 点击“操作演示”到达对应标题；展开“开发与架构”和源码预览均显示完整内容，代码块使用自身水平滚动区域。H1 原生锚点为 `fundstrace`。
- 本地核对 README 相对文件与锚点、CI 范围判定和差异格式。本轮只修改展示文档，不修改应用、图标文件、数据契约、安装包、Release 或下载版本。检查结果不代替 Windows 桌面验证。

本段记录分支候选的展示效果，主分支排版以此轮 PR 合并后为准；后续仅补充核验记录时，README 的有效证据不变。

## 历史展示与导出核验

2026-10-18 的首次主页升级来自 v0.1.5 源码 `ddc9ef0305e9bbfedd8329a84538ce36b2af32c8`。当时使用真实合成预览采集，正常导入 CSV 与 XLSX、下载 PNG／Excel／HTML，并运行 `docs/examples/harbor-path/verify.py`，核对 CSV／XLSX 逐格相同及导出 12 笔的时间、完整账号、金额字符串、币种和摘要逐笔一致。[文件核对与 SHA-256](examples/harbor-path/verification.json)继续记录该次导出证据；本轮没有重跑该核验或用新截图冒充历史导出的新验收。

首次主页的信息组织参考了 [Linear](https://linear.app/)、[PowerToys README](https://github.com/microsoft/PowerToys#readme)、[DuckDB README](https://github.com/duckdb/duckdb#readme) 与 [Ponytail](https://github.com/DietrichGebert/ponytail)，仅参考定位、导航、真实界面和渐进展开的顺序，未复制其品牌或能力声明。README 保留原生 `FundsTrace` 标题、口号、下载入口和真实 Release／Windows x64／MIT／Stars 徽章；没有借用未经确认的 Trendshift 排名。

此前已检查 GitHub Markdown、1440px／390px 深浅主题、图片解码及减少动态效果的 PNG 替代路径。它们属于 2026-10-18 的候选展示证据，本轮截图和品牌修改没有自动继承这些检查结论。README 中已有的 `picture` 减少动态效果分支与静态封面普通链接继续保留。

## 交付边界

本轮素材证明所述合成界面路径和新图标在源码浏览器入口中的实际加载，不代替安装包、Windows 实机 DPI／任务栏、真实案件性能或完整可访问性验收。下载入口指向真实 Releases；未签名预览及各环境已执行范围见[桌面交付](desktop-delivery.md)。案件包不含原始上传、导入任务或回执。

历史 v0.1.1 展示资源与 PR #2 的记录仍属于历史；当前主页源截图已重新采集。本轮没有重打包、修改 Release 资产或宣称既有安装包已包含新品牌素材。线上 GitHub 页面的展示与缓存行为仍须以对应候选合并后的实际页面为准。
