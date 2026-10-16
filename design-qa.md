# FundsTrace 工作台候选设计与流程验收

final result: passed

日期：2026-10-15。此结论限于本轮工作台源码、合成浏览器流程及下列自动检查；不是 Windows 安装包、GUI／DPI 或正式发布结论。当前候选未改变已发布 v0.1.0。

## 视觉依据与比较方式

延续已确认的石墨色工作台。原稿、实现与组合图存于本地忽略目录 `output/release-ui/`，不作为安装资源或业务数据发布。原稿是生成的设计参考，其示意账户、统计、布局与不存在的控件不是业务契约。

| 状态 | 原稿 | 当前截图 | 同图并排 |
| --- | --- | --- | --- |
| 完整工具带、查询展开 | `reference-expanded.png` | `20-expanded.png` | `comparisons/expanded.png` |
| 左侧收起、顶部展开 | `reference-sidebar-collapsed.png` | `23-sidebar-collapsed.png` | `comparisons/sidebar-collapsed.png` |
| 双收起、紧凑工具带 | `reference-compact.png` | `24-compact.png` | `comparisons/compact.png` |

原稿与实现均为1586×992；截图 CSS 视口1586×992、deviceScaleFactor=1。直接并排，无拉伸／重采样，增加32px标签栏。已实际查看三张整页组合图，并用 `comparisons/toolbar.png`、`query-table.png`、`legend-minimap.png` 的原尺度裁剪补查细节；整页工具回传会缩小，不能据此声称逐像素一致。

授权新增状态另查 `21-overview.png`（全览）、`22-query-collapsed.png`（查询折叠）。五个视口为599×640、960×640、1366×768、1440×900、1920×1080，使用同一 IAB/macOS arm64 浏览器；截图为 `viewport-*.png`，小窗口展开查询修订后另拍 `40-narrow-599.png`。媒体截图使用 simulated reduced-motion 使捕获稳定；正常状态的过渡另按交互检查。不是 Windows 系统 DPI 或真实浏览器缩放。

## 发现与修订

本轮范围内没有剩余、具备明确触发依据的 P0/P1/P2。没有沿用上一轮49项检查的 passed。

| 问题与触发 | 修复与证据 |
| --- | --- |
| P1：拖宽侧栏→收起→缩至599px→展开，侧栏越界、画布仅11px | 偏好宽度与实际宽度分开，展开／resize重新夹取。最终侧栏399px、画布200px、把手right420px；回宽恢复偏好。shell公开行为回归及小窗口截图。 |
| P2：紧凑显示弹层内焦点遇resize落到BODY | 只在焦点位于关闭层内部时还焦。浏览器 showPhotoCk→resize1366→btnDisplayActions；弹层关闭且 inert。 |
| P1：连续上图意图被 busy 丢弃或重置相机 | 共享 membership adapter 按最新意图串行收敛，沿用历史／CAS，保留已有坐标与相机。快速反选、409、关闭、A–B–A均复验。 |
| P1：查询乱序、切画布保留旧行；原renderer覆盖锚点 | 请求序号与绑定令牌、同步清旧状态，共用表格模型与稳定ID选择。实际暂停两次查询并倒序返回，旧999笔不覆盖最新0笔。 |
| P1/P2：上传A/B乱序、补充账号残留、未知回执重复提交、延迟关闭新视图 | 会话序号、提交前禁重入、恢复只读原回执。实际导入2笔后注入进度503，恢复后总数19，未重复写入。 |
| P2：详情／前后流水迟到响应、异步保存污染新抽屉及丢焦 | 绑定／请求／抽屉令牌，异步写回检查，重建同抽屉时保留有效控件焦点。合成回归；实际保存合成身份字段及裁剪键盘复验。 |
| P2：旧modal无焦点边界、嵌套Escape或拖出遮罩误关闭 | 背景inert、Tab边界、逐层Escape和还焦；pointerdown/up同遮罩才关闭。实际嵌套详情、外点与拖出复验。IME不拦截输入法默认操作。 |
| P2：分析勾选与实际计算／报告范围不一致；subjects进入innerHTML | 后端验证可选算法白名单与窗口范围，旧调用保留默认项；仅运行选中项，报告说明范围且不推断未运行项；标题转义。实际仅cycles报告验证。 |
| P2：混用select箭头、控件字体／禁用态不一致、窄屏查询被压至零 | 统一本地Tabler箭头、留白与字体；完整金额不截断；查询独立滚动且有最小高度，hidden语义统一。五断点及全览复验。 |

“箭头脱框”最初未在100%基线复现；确认的混用native箭头和内部留白问题已统一，当前截图未见越框。125%/150%真实缩放未执行，不扩写成已跨平台解决。

## 五项视觉表面

- **字体／排版：** 系统 Segoe UI／Microsoft YaHei／sans-serif，正文13px、密集控件及表格12px；账号等宽，金额保留完整十进制与币种。短标签和SVG基线统一，无孤立单位。生成图不能提供可靠精确字体，不宣称字体像素一致。
- **空间／布局：** 保持案件栏、分组工具带、查询／表格、真实G6画布。查询、侧栏、顶部独立折叠。全览保留七列、详情及分页；1586px下完整七列同屏。侧栏保留局部横滚，599px工具带亦局部滚动；960px及以上工具内容在视口内。
- **颜色／状态：** 中性石墨表面、细边框、蓝色操作及选中态、独立红色危险操作；hover／pressed／focus／disabled／pending／failure各有反馈。大量已上图行仍可读，不用颜色独自承载状态。
- **图像／图标：** 24个Tabler 3.35.0本地轮廓图标与MIT许可；下拉箭头仅改stroke以适配暗色背景，修改通知保留。未引入SF Symbols或外部字体。原G6、图例语义及缩略图操作保留。
- **内容／语义：** 侧栏“锚点”与全览“上图”分别表示批量起点与持久追踪事实；“本页全部上图”限定当前页。无确认页脚，关闭不撤销成功操作。参考图与实际合成数据／拓扑差异可接受；完整金额、当前绑定优先。

## 合成浏览器流程

临时 `preview_synthetic.py --graph-nodes 18`：case2初始17笔／18账户；导入合成CSV新增2笔后19笔／21账户。case3同账号用于隔离，case4为空。所有输入、导出及截图均为合成数据。

| 路径 | 实际结果 |
| --- | --- |
| 查询折叠、侧栏／顶部组合 | 条件、页码、锚点保留；批量与表格工具常驻。按节点ID比较位置、相机矩阵与追踪集合不变。 |
| 全览连续勾选／反选 | 17→16，其他节点位置和相机不变；快速17反选及16反选／重选收敛。失败行回退已确认勾选，其他成功行保留。 |
| 关闭与失效 | 发送后关闭不取消；焦点回全览触发按钮。切案2→3→2时旧表格先清空，迟到CAS不进入新历史；新绑定重新读取。 |
| 全览键盘／滚动／遮罩 | Tab／Shift+Tab在最上层循环，逐层Escape还焦；滚动在表格内且th保持顶部。拖出不关闭，真正外点关闭且不穿透画布。 |
| 画布 | 实际另存17笔与新建空画布；空画布行均未上图，加入1笔后关闭仍保留。自动刷新当前绑定查询。历史事务／撤销失败边界由回归覆盖。 |
| 导入 | 实际无效XLSX错误、有效CSV识别／映射／提交／进度／回执；503后显式恢复只读状态，新增2笔没有重放。跨上传会话等由回归覆盖。 |
| 邻域／详情 | 实际查看000GRAPH-00008邻域3/18账户、2/17关系，返回全图；缩略图开关、G6节点详情和合成字段保存。前后payer按钮可Enter切换aria-pressed。 |
| 裁剪／自动补全／IME | 实际裁剪ArrowRight令中心0.50→0.51，Escape取消；自动补全键盘、IME合成确认与native dialog优先级由真实脚本DOM harness覆盖，不冒充输入法实机。 |
| 分析 | 实际只选cycles，恶意样式字符串按文本显示；下载报告snapshot仅含cycles与对应范围，未选项没有“未发现”段落。 |
| 导出 | 经UI实际下载HTML／PNG／PDF／XLSX；PDF渲染检查1页，PNG1872×1558，XLSX双表。超大金额9999999999999999.99为精确字符串单元格。 |

故障注入仅针对当前loopback合成服务。注入期间HTTP400/409/503是预期错误；新建干净预览tab加载19行／17笔上图，控制台error列表为空，调试监听未残留。临时拦截、媒体与视口覆盖已恢复。

## 自动检查与保护边界

稳定候选运行 `.venv/bin/python scripts/test.py -q`：**375 passed，1条既有Starlette/httpx弃用警告，81.40秒**。覆盖最终键盘、绑定和窗口修复；本轮新缺陷曾用原实现对照确认失败后再验证修复。

`npm --prefix desktop test`：22项中19 passed、3 Windows-only skipped、0 failed。API契约导出 `--check`、G6固定hash、`git diff --check`通过。49条桌面静态资源白名单，HTML／CSS35个直接本地引用全部存在并包含；没有新增运行依赖。

`git diff HEAD -- static/app.js 'static/graph-*.js' static/vendor`为空；七个冻结SHA-256与接管基线一致，另记录benchmark hash。未读取原业务数据、未调用Provider、未改Analytix或原始fund-tracker。

## Windows 原生目录候选补充证据

[Actions 38784969693](https://github.com/Eysn0130/FundsTrace/actions/runs/38784969693) 成功，严格绑定运行源码 `02b6c303d8a4d9f0cf0b390999969a482efdb771`，`installer=false`。Windows x64定向Python56项、桌面Node22项（无skip）、React类型检查／构建和原生PyInstaller sidecar通过；11项包内后端检查覆盖无授权启动、token拒绝、中文／空格路径、一个合成案件崩溃重开及EOF退出。

安全报告经artifact分段读取核对：54个资源记录全部与本地相符，其中25项逐字节相同，29项仅为已确认的Windows检出CRLF；源码入口与三个锁文件也相符。后端manifest、smoke和candidate JSON摘要与校验文件一致，两个manifest副本摘要相同。CI列出1542个产物文件；本机只读取安全报告，未独立下载并重新核验整个目录。artifact约550MB、保留3天，安全报告已另存交付证据。

报告保持 `installerLifecycle:not-run`、`guiAcceptance:false`、`installedAcceptance:false`、`signed:false`、`published:false`。没有新安装器、版本、Tag或Release。后续提交只补本文等文档记录，不改变这次构建的运行源码或资源；不把文档提交号写成原生构建SHA。

## 未执行与停止边界

- **目录候选阶段 NOT_EXECUTED（后续安装器补证见文末）：** Windows安装／升级／卸载、GUI／DPI、原生保存／取消、触屏实机、125%/150%真实浏览器缩放。IAB快捷键未改变zoom，因此没有用CSS／pinch替代证据。
- **已知冻结限制：** 密集G6拓扑仍可能标签重叠；本轮按授权冻结节点／关系／标签／布局实现。此次没有性能基准或任意拓扑可读性结论。
- 案件包、删除事务和进程恢复等由合成API／进程回归覆盖；本轮未实际执行浏览器删除案件／画布或Windows活动导入恢复，不把自动检查泛称所有GUI路径验收。
- 新版本号、Windows构建与公开发行仍需绑定后续具体候选。此处passed只表示当前工作台源码范围的发现已关闭，不代表安装包或发行完成。

## Implementation Checklist

- [x] 三种确认状态同尺寸同图比较，补查局部与新增状态。
- [x] 修复可复现P1/P2，真实浏览器及公共行为回归覆盖。
- [x] 条件／锚点／追踪分别管理，快速操作与跨绑定保护。
- [x] 字体、SVG、状态、键盘、遮罩、动效与小窗口可达性。
- [x] 冻结渲染、资源与许可边界核对。
- [x] 使用说明、当前证据及未执行项明确。

## Follow-up Polish

系统字体抗锯齿、真实浏览器125%／150%缩放、Windows GUI／DPI与触屏在对应环境补验。密集图标签属于另行授权的渲染范围；不为本轮风格精修改动被冻结的实现。

## 后续 v0.1.1 发布补证

[Release v0.1.1](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.1) 已公开，绑定发布源码 `e7be2f5675dcd2dc896a20d6d644d5aa05125a73` 及 [Windows安装器构建](https://github.com/Eysn0130/FundsTrace/actions/runs/38827843574)。发布源码完整回归375项、Windows定向Python56项、桌面Node22项、54条资源／11项后端及14项静默安装生命周期通过。EXE实际版本资源正确，升级基线为固定v0.1.0。安装包摘要、源码和报告已核对；原先目录候选记录保持其原SHA范围。安装器未签名，Windows GUI／DPI与其他未执行项不变。此次发布是后续交付动作，不将源码设计passed改称GUI或全面兼容验收。
