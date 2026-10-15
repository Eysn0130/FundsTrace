# 大图绘制、结构布局与缩略图

2026-10-15：保留 G6 4.8.19 和独立版入口。本轮只实现浏览器图谱，不建立 Electron 壳、不重写 Rust、不修改 Analytix；Windows 与插件库接入计划另见 [Windows 与插件计划](windows-and-plugin-plan.md)。

## 实现边界

完整事实仍在 `state.lastGraph`，邻域集合仍由 `graph-view.js` 决定。视窗裁剪只改变 G6 item 的绘制 group，不删除图元、节点、边或追踪记录；逻辑视图的账户/关系数与实际绘制数分别显示。`graph-viewport.js` 只持有几何包围盒，没有流水、金额、账号归属等业务推断。

节点、路径、箭头、曲线、自环、折点与拖动标签采用保守包络，并合并 G6 实际 group 边界逆转换后的世界坐标。视窗外扩 120 屏幕像素，跨窗边即使两端都在屏外也保留；标签策略补入这些边的逻辑端点，但不因此显示屏外节点。无效/零面积视口回退完整绘制。部分视窗查询为 O(V+E) 的简单包围盒相交，按 RAF 合并；overview 先用 `containsAll` 判断是否已包含完整节点范围，连续完整视窗只更新缩略图视口，不重复扫描图元。裁剪只在可见成员改变时额外 `paint`，不声称是图元虚拟化或空间树检索。

标签不再先创建再隐藏。G6 `model.label` 只在当前策略需要时存在；完整文本保存在 `fundsLabel`，精确金额仍来自原始 Decimal/string 元数据。保存和 HTML 导出从完整文本及原始数据采集，不能把缺少 G6 文字形状当作没有金额。标签删去前缓存拖动位置，重建后恢复比例/绝对坐标，并扩充实际几何边界。节点/折点/标签编辑完成后重新建几何索引。

撤销案件/画布绑定会同步清空缩略图、取消标签 timer 与 viewport RAF；延迟任务核对 generation、画布 generation、图实例及 view revision。当前保留 G6 内置 zoom 优化：`enableOptimize:true`、`optimizeZoom:0.001`，平移也保留优化。G6 4.8.19 的延迟恢复会捕获旧 item 数组，`revokeZoomOptimization()` 因此在清图、`changeData` 或 destroy 前清除其 timeout、重置 optimized，并按 `ori-visibility` 恢复仍有效的 shape、清理该标记；140 ms 标签收敛另外由本项目撤销。PNG/PDF 导出临时解除裁剪，适应整个逻辑视图，结束后恢复裁剪；HTML 导出和画布保存不会因屏外绘制优化而减少账户或关系。

旧缩略图为 G6 默认 plugin，会克隆全部形状。现在 `graph-minimap.js` 用独立 Canvas 只保存节点几何；数据/布局改变时更新缓存底图，缩放/平移只复制缓存并画矩形。点击、捕获拖拽可以定位，旁边四个平移按钮提供键盘替代。可收起；clear/destroy 撤销回调并移除监听。不会把原始账号、金额或边标签复制到缩略图。

## 算法选择

保留力导向和有向层次，增加“主节点与叶子铺开”：显式主账号优先作为多源锚点；未标记时，每个无向连通分量按不同邻居数选择几何中心，ID 稳定打平。该中心不是业务主账号、涉案程度或资金控制关系的推断，不写回任何角色标记。

多源 BFS 安排关联层，叶子按节点尺寸与间隔分多圈；长链/深环转为 BFS 蛇形网格，分量按包围盒打包。只输出位置和布局诊断，不改变有向边、金额、币种或追踪集合。5000 节点不会被强行压进固定 900×600 的区域，视图允许缩小到 0.02，再使用缩略图、缩放或分批邻域查看。

参考 [AntV Radial](https://github.com/antvis/layout/blob/v5/src/algorithm/radial/index.ts) 的径向距离语义和 [Concentric](https://github.com/antvis/layout/blob/v5/src/algorithm/concentric/index.ts) 的分层间距。Radial 上游含全对距离矩阵/MDS，当前稀疏图不直接引入该成本；独立实现 BFS 与分量打包，没有升级 G6、复制上游代码或增加依赖。G6 的 [维护方性能建议](https://github.com/antvis/G6/blob/4.8.19/packages/site/docs/manual/FAQ/performance-opt.en.md)也指出默认缩略图完整克隆及多余形状的成本。

只读借鉴 Analytix 的 worker 有限 DTO、序号/取消生命周期、分量/方向布局及缩略图逆变换。没有复制其全局装配、旧后端 fallback、权限发现或 Rust 计算链。该项目当前 Host 不能直接注册任意 FundsTrace 侧栏页面，详见 Windows/插件计划。Exa 三个检索方向共返回 25 条候选结果，筛选后采用维护方代码和官方文档；候选数不等于已核实的独立来源数。

## 合成验证与同图集对照

```sh
.venv/bin/python scripts/test.py
.venv/bin/python scripts/export_api_contract.py --check
.venv/bin/python scripts/preview_synthetic.py --graph-nodes 5000 --graph-shape hub --graph-seed 2026
```

保留上一轮首绘、布局、真实 wheel/节点拖动/画布拖动 → RAF 回调接收的定义，见 `graph-interaction.md`。新增 `drawnNodes/drawnEdges/createdLabels` 诊断数，不改变输入时钟和匹配方法。只在合成预览加载记录器。

`--graph-assets /绝对路径/static` 仅用于隔离预览资产对照：首页、基准页、静态文件与源码 SHA 必须来自同一完整目录，元数据不暴露绝对路径。生产应用不注册这项参数或预览路由。上一候选的七个渲染资产在修改前按旧指纹冻结，位于被忽略的 `output/graph-viewport/baseline-source/static`；比较时两边使用相同 Python/合成数据生成器，不复制业务数据或原项目目录。

计时必须串行，固定窗口/图集/种子，停止构建、测试和其他性能工作；后续复验宜严格交替采样。本轮实际为先采三次冻结资产的 baseline，再采三次最终 candidate：baseline 采于候选开发期间，计时段没有并行工作，candidate 在最终资产稳定后采集；这不是严格交替或随机化对照。原始记录、源码 SHA、统计、截图和导出回读放在被忽略的 `output/graph-viewport/`。小样本回调代理不能证明 60FPS、真实案件或 Windows 安装体验。

### 最终六条样本的聚合结果

只纳入 [samples-final.json](../output/graph-viewport/samples-final.json) 的 baseline 1–3（`overview`）及 PNG 下载分支修复后重新采集的 candidate 1–3（`final-png`），每条恰好一个运行。PNG 修复前的 `final-native`／pre-png 样本保留作诊断，和其他候选阶段一并排除，不混用旧候选的较好数值。两组均为 5000 节点、4999 条关系、1280×900、devicePixelRatio=1。候选的三次记录均为 20 个实际创建标签；overview 初始记录仍绘制全部 5000 节点／4999 条边，不能把本表解释为已裁剪掉大部分图元。

交互指标对同一 variant 的全部匹配 `eventToRenderMs` 样本直接合并，不先平均各次运行。median 使用通常的中位数；P95 使用 nearest-rank，即排序后的第 `ceil(0.95×n)` 个值，不插值。本次 n=12／18 的 P95 均落在最大观测值，不能据此稳定估计尾部。首绘和布局各使用三次运行时长的 median。单位均为 ms。

| 指标 | baseline | final candidate | 每组样本数 |
| --- | ---: | ---: | ---: |
| zoom event→RAF 回调接收 median | 37.25 | 35.25 | 12 |
| zoom event→RAF 回调接收 P95 | 55.00 | 41.20 | 12 |
| canvas-drag event→RAF 回调接收 median | 18.75 | 16.65 | 18 |
| canvas-drag event→RAF 回调接收 P95 | 44.10 | 37.10 | 18 |
| graph request→首绘 median | 656.80 | 754.30 | 3 |
| graph request→布局完成 median | 1238.00 | 1176.00 | 3 |
| navigation→首绘 median（单列） | 721.00 | 822.50 | 3 |

graph request→首绘三次原值分别为 baseline `935.1 / 622.5 / 656.8`、candidate `599.3 / 821.3 / 754.3`；布局完成分别为 `1569.6 / 1231.1 / 1238.0`、`1006.3 / 1226.2 / 1176.0`。**最终候选的 request→首绘中位数回退 97.5 ms，navigation→首绘也回退 101.5 ms。** 本样本中 zoom、画布拖动和布局完成的指标较低，但不能据此宣称整体首绘或页面冷启动加速，也不能把首绘回退归因于 PNG 下载分支的修改。

每组共 30 条交互记录，六条记录合计 discardedRuns=0、droppedSamples=0。核对全部 60 条交互时钟满足 `inputAtMs ≤ receivedAtMs ≤ committedAtMs ≤ renderedAtMs`；六次运行满足 `requestedAtMs ≤ paintCommittedAtMs ≤ firstGraphPaintAtMs`，布局完成均晚于 request。264 项派生时长与相应时间差一致，仅有小于 `1e-9 ms` 的浮点误差。不同记录的绝对时钟不合并；这里的 RAF 回调接收是提交后的代理终点，不是 RAF frame timestamp、屏幕实际呈现或 FPS。

三次 candidate 的 fixture `sourceSha256` 相同，15 个资产全部匹配当前 `static/`；三次 baseline 的 15 个资产全部匹配冻结目录。其中七个核心旧指纹同时匹配 [previous-baseline.json](../output/graph-viewport/previous-baseline.json) 与冻结资产，未用候选源码冒充基线。可复查的统计定义、逐次时长、指纹和核对结果保存在 [summary.json](../output/graph-viewport/summary.json)。

这些数据支持本机这一合成图和这一采集顺序中的观察差异；样本少、两组非严格交替，不能排除运行顺序、预热或环境变化，也不能归因于某一个优化。没有从该表推导真实案件、大规模任意拓扑、Windows、安装包或发行结论。

### 最终候选的功能验收

- `.venv/bin/python scripts/test.py`：265 passed，23.19 s；保留一个已有 Starlette/httpx 弃用警告。覆盖金额、案件隔离、导入恢复、保存导出及 Node 包装测试；其中导航 harness 为 46 个 Node 行为用例。API 契约检查、CSS 解析通过，未改 React 或构建桌面包。
- 真实浏览器：5000 节点结构布局放大后，逻辑图仍为 5000／4999，视窗实际绘制 333 个账户；邻域按 51→101 展开，剩余邻居 4949→4899；中心账户转入为 1／0、转出为 51／50。缩略图收起、点击定位、平移按钮可操作；390 px 下页面与工具栏无横向溢出，按钮保持单行。
- 案件 2 的 `9999999999999999.99` 下界命中 1250 笔，切换同账号案件 3 为 0，返回案件 2 恢复 1250；`0.01` 同上下界命中 1249 笔。空案件 4 显示 0／0 并清空旧图和缩略图。
- 裁剪视图的 HTML 导出回读、邻域另存画布及保存后 API 图均为 5000 个账户、4999 条关系，边身份集合相同。独立 Decimal 汇总均为 CNY `12500000000000000124.99`、USD `250.00`。邻域 HTML 则明确保留当前 51／50 范围，没有把显示范围当全量事实。结果见被忽略的 [export-validation.json](../output/graph-viewport/export-validation.json)。
- 实际发现并修复 PNG 下载兼容问题：直接以长 data URL 作为下载地址时，浏览器收到“PNG 已导出”却没有下载事件；现在按二进制解码成 PNG Blob，复用 HTML／PDF 的 object URL 路径。回归核对文件名、MIME、完整 PNG 字节及迟到绑定拒绝；实际浏览器 PNG／PDF 均下载成功，核对 PNG／PDF 文件签名并查看 PNG 完整图形。图片不承担逐条金额事实回读，金额完整性由上面的结构化回读验证。
- 原目录 6040 个文件的大小、mtime_ns 及路径集合与开工前一致；只核对元数据，没有读取其文件内容。本轮未上传 GitHub、未修改 Analytix、未接触业务库。

后续优先独立定位首绘成本，并扩大严格交替的采样。当前仍保留全部 G6 item，部分视窗查询仍线性扫描，中心发散图的跨窗边可能大量保留；不能声称已解决任意大图的全部延迟。若证据表明索引扫描成为瓶颈，再引入空间索引；若绘制成为瓶颈，先处理边几何、标签碰撞与显示策略。保持完整事实和显示范围分离，避免用删边或近似金额换取指标。修改固定 G6 版本时必须复验其内部缩放撤销适配器。
