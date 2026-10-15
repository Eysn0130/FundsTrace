# 图谱渲染、标签与本轮计时

历史候选记录：下文指纹和计时对应 2026-10-15 工作区压缩／Electron 施工之前的渲染候选。后续界面和桌面交付见 [desktop-delivery.md](desktop-delivery.md)；本表不作为新尺寸或桌面运行的性能结果。

本文件记录当前渲染候选的实现与验证方法。范围是独立网页和合成图，不增加 Electron、不修改 Analytix，不改变案件、流水、金额、方向、币种或追踪集合。前端工作区重排仍是 [后续方案](graph-workspace-plan.md)。

状态：端点节点遮挡过滤后的源码已冻结，正式结果来自共同空白输入位置下、DPR2 的 12 条交替记录；基线 15 项、候选 16 项资产均已与对应文件核对。首绘与布局的 median／P95 较低，但第 5 对首绘回退，zoom median 和 canvas-drag median／P95 也回退。下文分别记录计时、合成功能验收及其限制，不作全指标提速或发行通过结论。

## 实现方法与边界

### 数据校验与完整索引

G6 继续固定在本地 4.8.19。已有 Chrome CPU 诊断把 5000 节点／4999 条关系下约 204 ms 归因到 `dataValidation` 的逐边 `Array.includes` 成员扫描。该采样用于定位复杂度，**不是正式 benchmark、不是 event→render 样本，也不等于可兑现的首绘节省**。

本地 vendor 补丁只把同一校验中的端点集合构造为一次 `Set`，两次成员查询改为 `Set.has`，保留遍历、告警、失败分支和调用方差异；不是移除校验。来源、许可边界、补丁前后完整 SHA、异常边界及补丁检查方式见 [G6-PATCH.md](../static/vendor/G6-PATCH.md)。未知文件或新版本不能盲套本补丁；精确产物检查和校验回归见下文。

应用在加载／改变完整图时建立 `fullIndex`，全图重绘复用它；只在显示邻域确实变化时构建相应视图索引。索引、裁剪和标签都是显示层，不成为资金事实真源。[app.js](../static/app.js)、[graph-view.js](../static/graph-view.js)

### 几何与标签收敛

构建视窗几何时，先读取屏幕点到世界坐标的仿射逆变换基，再对多个包围盒复用；矩形四角均转换，不能只假定某一缩放／原点。图容器、裁剪与实际 G6 group 边界仍需保持一致。[app.js](../static/app.js)、[graph-viewport.js](../static/graph-viewport.js)

新增 [graph-labels.js](../static/graph-labels.js) 只做屏幕空间标签选择：稳定排序后，以有限网格索引检查 padded bbox；过大矩形进入有界回退路径。选中／关注文字优先，普通候选缺少有效几何时拒绝；required 标签仍可保留，因此不承诺任何状态下绝无重叠。没有删除关系或把几何优先级解释为涉案角色。

普通 auto 边标签另检查其源、目标两个节点的屏幕 bbox：标签与任一端点 bbox 外扩 2 px 的区域相交时，拒绝该自动标签。端点边界按本次选择缓存，优先使用实际 key shape bbox，缺少时才按节点尺寸与当前变换估算；每个预算内边标签只查询自己的两个端点，不扫描全图节点。selected 边标签、`all` 和导出绕过这项拒绝；它不保证避开其他节点或保证全图无遮挡，也不改变金额或导出完整性。[app.js](../static/app.js)

应用先用当前／估算文字边界选择，再在 G6 创建真实文字后以实际 screen bbox 收敛一次；第二次只从第一次保留集合继续筛选，避免来回创建被拒标签。自动模式可以减少创建的文字，`all` 与 PNG／PDF 导出绕过碰撞筛选恢复全部应显示标签。规范文本继续保存在 `fundsLabel` 和原始元数据，不能把隐藏的 G6 `model.label` 当缺少金额。

fit 等同步更新会清除冗余的 140 ms 标签 timer，再立即处理当前标签，避免下一次拖动中重复收敛。案件／画布／graph instance／view revision 的撤销检查仍适用于延迟任务。

手工边标签位置需要随重新布局重新基准化：先捕获相对位置，刷新几何后恢复并更新缓存，包括当前隐藏的边。否则同一 scope 的旧绝对快照可能在下一次显示时把标签带回屏外。小图 fit 另处理边界与 zoom cap：不超过 20 个节点时按真实 bbox 居中并限制缩放；常规 fit 超过 cap 时也改为绕容器中心缩放。后者可以改变紧凑的 5000 节点 force 图在画布上的位置，因此除了重新绑定资产指纹，还必须重新确认输入坐标的命中区域。[fitGraph](../static/app.js)

### 透明标签与密集关系

边标签背景改为透明，文字保持可读颜色；保留透明命中矩形及 padding，使手工拖动和命中区域继续存在。它不再有可见的深色背景块，**不等于完全移除了背景 shape**。源码和导出都需按这一区别验证。

密集概览使用不透明、低对比 stroke `#3f4854`，避免多条半透明线在交叉处叠成更亮的块；箭头和较强关系强调按需呈现，并限制关注关系预算。方向事实仍在原关系与 inspector；`all` 和 PNG／PDF 输出恢复全部关系细节。该策略降低概览装饰密度，不减少关系数、金额或来源。

这些改动同时涉及校验、索引、几何、标签数量和密集线策略。即使同一合成图事实一致，实际创建标签数也可能不同；最终计时不能被归因到单一 vendor 补丁，不能用 CPU profile 的局部时间抵扣或预测整体首绘。

## 正式计时协议

- 图集固定为合成 5000 节点／4999 边 hub，seed=2026；最终 12 条记录均为 CSS viewport 1280×900、实际 DPR=2。该接口只设置 CSS 视窗，不把系统 DPR 改成 1；本组内部环境一致。核对 fixture 的所有案件元数据、当前运行对应图和分币种金额字符串，不把 CNY／USD 加成一个总额。fixture 元数据相等不是重新查库或导出回读的替代证据。
- baseline 为**上一轮最终候选**的完整冻结资产，位于 `output/graph-render/baseline-source/static`，与 `baseline-sha256.json` 的 15 个 SHA 及上一轮 `final-png` 摘要对应；不是更早的旧 G6 对照。candidate 为本轮最终源码，含新增 `graph-labels.js`，预期 16 个渲染资产。
- 两边各预热一次，预热排除。正式 sequence 1–12 按 baseline／candidate 严格交替，组成六对；每条只取其唯一的 run index 0。每个 variant 为六次首绘／布局、24 个 zoom 和 36 个 canvas-drag 样本。计时串行，停止其他测试、构建和性能工作。
- 最终每条记录显式保存相同输入：drag `[[1200,400],[1230,420]]`，wheel `[1200,400]`，使用双方共同空白区域。[基线位置截图](../output/graph-render/baseline-input-area.png)与[候选位置截图](../output/graph-render/candidate-input-area.png)是前一 DPR1 诊断组的位置参考，不是本组 DPR2 截图。相同像素坐标本身不证明相同输入语义，最终协议也不覆盖关系／节点上的 hover+zoom。
- 本轮所有 `diagnostic*`、预热、profiling 和非最终候选均排除，不能挑旧诊断中较好的值替换。固定 baseline 先、candidate 后的顺序不是随机化或盲测，交替不能消除全部预热／顺序偏差。

首绘为 graph request→可见 G6 commit 后下一次 RAF 回调接收的时长；navigation→首绘单列。交互为最新匹配 DOM input timestamp→G6 commit 后 RAF 回调的 `performance.now()` 接收值。它不是 RAF frame timestamp、GPU 时间、物理屏幕呈现或 FPS；layout settled 是单独里程碑，不混入首绘定义。

对同一 variant 的全部匹配 `eventToRenderMs` 直接合并，使用 usual median（偶数样本取中间两值平均）与 nearest-rank P95（排序后第 `ceil(0.95×n)` 项，不插值）。六次首绘和布局也分别报告 median／P95。n=6 的 P95 就是该组最大值，24／36 条交互的尾部估计同样不稳定。保留每一对首绘原值及 candidate−baseline 差值，不只报有利结果。

每条记录使用自己的 timeOrigin，只汇总时长。逐样本核对 `input≤received≤committed≤rendered`，逐运行核对 `requested≤paintCommitted≤firstGraphPaint`、布局完成晚于 request，并核对所有派生时长与时间差、discardedRuns／droppedSamples。候选和基线的各次 SHA 必须稳定，分别对应当前文件和冻结目录；任何末尾源码修复都使旧候选指纹失效。

## 正式结果

原始记录为 [samples-alternating.json](../output/graph-render/samples-alternating.json)，聚合、逐条原值、派生核对与完整 SHA 为 [summary.json](../output/graph-render/summary.json)。下表单位均为 ms，`median / P95`；P95 使用上述 nearest-rank 方法。

| 指标 | 每组样本数 | baseline | candidate |
| --- | ---: | ---: | ---: |
| request→首绘 | 6 | 603.55 / 874.30 | 526.65 / 724.30 |
| navigation→首绘 | 6 | 659.95 / 958.80 | 581.25 / 772.70 |
| request→layout settled | 6 | 1026.60 / 1285.50 | 997.60 / 1188.50 |
| zoom event→render | 24 | 33.75 / 40.80 | 35.05 / 40.70 |
| canvas-drag event→render | 36 | 16.00 / 37.10 | 17.80 / 41.60 |

六对首绘原值全部保留，差值为 candidate−baseline；负值表示本对候选较短。

| 对次 | baseline ms | candidate ms | 差值 ms |
| --- | ---: | ---: | ---: |
| 1 | 874.3 | 518.8 | -355.5 |
| 2 | 794.4 | 501.3 | -293.1 |
| 3 | 595.6 | 523.5 | -72.1 |
| 4 | 589.5 | 529.8 | -59.7 |
| 5 | 611.5 | 724.3 | +112.8 |
| 6 | 575.6 | 531.5 | -44.1 |

本组六对首绘中五对较短，第 5 对增加 112.8 ms；全部配对差值从 -355.5 到 +112.8 ms，不能把 median 差异解释为稳定收益或统计显著性。zoom median 增加 1.30 ms，其 P95 仅减少 0.10 ms，不能据此声称缩放整体更快。canvas-drag median 增加 1.80 ms、P95 增加 4.50 ms，明确保留这些回退。固定 baseline 先、candidate 后且只有同一合成图，不能排除顺序、缓存、布局和环境波动，也不能把全部差异归因到 `Set` 补丁。

全部 12 个 run 及 120 个交互样本均保留，`discardedRuns=0`、`droppedSamples=0`。运行里程碑及样本时钟顺序失败均为 0；528 项派生时长核对的最大浮点误差约 `3.64×10⁻¹³ ms`，小于 `10⁻⁹ ms`。baseline 的 15 项资产在六次记录间稳定、匹配冻结目录及上一轮 `final-png` 候选；candidate 的 16 项资产在六次记录间稳定、匹配当前静态文件。

所有记录的图与分币种 fixture 元数据一致：5000 节点、4999 边，CNY `12500000000000000124.99`、USD `250.00`。两组每次的 rendered／drawn 节点与边仍为 5000／4999；baseline 创建标签数每次为 20，candidate 依次为 4、6、5、4、5、4。事实一致不等于几何与文字绘制工作相同，也不能把本组首绘结果解释为普遍的逐图性能承诺。

## 排除的诊断与保留的风险

标签重新基准化前、标签 timer 修正前、透明线试验、CPU profiling 与预热全部排除。相应 `diagnostic*` 原始文件保留，约 204 ms 的 CPU profile 只用于定位校验扫描，未计入任何正式指标。

端点过滤之前的完整共同空白输入 12 条记录另保存在 [diagnostic-before-endpoint-filter.json](../output/graph-render/diagnostic-before-endpoint-filter.json)，全部排除，聚合及六对原值保留在 `summary.json` 的 `preEndpointFilterDiagnostic`。该旧组为 DPR1，当前组为 DPR2；两者数值变化不能单独归因于端点过滤，也不能跨显示环境外推。

在最终共同空白协议之前，完整 12 条交替记录使用 wheel `[920,560]`，保留于 [diagnostic-before-blank-anchor.json](../output/graph-render/diagnostic-before-blank-anchor.json)。几何居中修复改变簇的位置，该坐标可能从基线空白处变为候选节点／关系处；因协议的命中语义无法保证等价，**整组**退出正式统计，而非删除较慢的个别样本。其全部统计、六对原值和六个尾部条目的阶段值仍保留在 `summary.json` 的 `diagnosticObservations`，有利和不利数值均未混入最终表。

该旧组 zoom median／P95 为 baseline `33.90 / 44.30 ms`、candidate `37.30 / 75.70 ms`，尾部风险仍需记录。六个 candidate `>55 ms` 均为各 rep 的第一个 zoom；input→handler capture 为 31.2–45.1 ms，对应 baseline 为 10.8–16.1 ms，而 capture→commit 为 7.9–11.3 ms。差异主要已发生在 handler 捕获之前，但这仅定位计时阶段，不能据此认定 hover 或其他根因。共同空白重采不证明 hover+zoom 风险已解决，也不能把旧组较好或较坏的值替换到最终结果中。

## 功能、金额与导出验收

以下功能证据与性能采样分开记录，均使用端点过滤后的源码、实际浏览器和一次性合成预览副本。

- 浏览器验证了结构图居中放大时绘制节点 339／5000、85 个标签，以及全量 HTML／PNG／PDF 下载、完整画布另存为 canvas 1 后切换 0→1 重载。两节点短边的 structural auto 场景只有 2 个标签，端点碰撞的金额自动标签被隐藏；force 重新布局后为 3 个标签，巨额金额完整可读、叶邻域 HTML 已下载。[transparent-money-label.png](../output/graph-render/transparent-money-label.png) 为这一两节点 force 场景。这些计数属于交互后功能观察，不与正式 run 的概览标签计数混用。
- 实际 UI 的大金额下界筛选返回 1250，切换独立 case 3 为 0、返回原 case 恢复 1250；`0.01` 上下界检查为 1249，空 case 4 清图为零。这些数量来自合成用例，不外推真实案件。
- 最终 [export-validation.json](../output/graph-render/export-validation.json) 的 `frontendAppSha256` 与正式候选的 `app.js` 一致。从最终一次性合成预览独立复制 DuckDB＋WAL，逐 raw-flow 比较端点、ID、币种与 Decimal 金额：4999 条流水与全量 HTML、API 保存图及 canvas 1 的 5000 节点／4999 边逐条一致，CNY `12500000000000000124.99`、USD `250.00`。该隔离副本不连接业务库；fixture 元数据相等不能替代此独立回读证据。
- 最终 force leaf HTML 为 2 节点／1 边，金额字符串 `9999999999999999.99`，`labelVisible=true`，金额标签未缩写；PNG／PDF 文件签名通过，逐导出文件 SHA 已保存在结果中。独立 case 3 为 CNY `11111111111111111499.98`、USD `500.00`，未与原 case 混合。
- 完整 Python 回归 `271 passed`（25.50 s，1 项既有弃用 warning），其中包含 Node harness 53 项的包装检查；vendor 精确产物检查、Node 语法及 API contract 检查通过，实际浏览器 error console 为空。专项 collision／vendor 的子用例存在覆盖关系，不与这些数字相加制造总数。

本轮证据限于合成数据、该网页环境与所列路径。没有 Windows／安装包／Electron／Analytix 插件验收，也没有 GPU 呈现、FPS、真实案件、所有图形状或 hover+zoom 性能通过结论。
