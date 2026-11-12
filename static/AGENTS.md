# static/ 局部规则

适用于传统浏览器工作台；先读根 AGENTS。业务判断入口在后端，不由 G6 显示重新定义。所有相对验证命令从选定 checkout 的仓库根目录执行。

## 现有组装与生命周期

- 经典脚本的加载次序参与接口组成。涉及 `loadGraph`、`selectCase`、`activateCanvas`、脚本重排或刷新入口时，先登记并核对 `index.html` 的 `app → canvas-history → canvas-editor → table-workspace → workspace-session` 顺序及现有包装者，不能只移动定义所在文件。
- `app.js` 提供基础 `loadGraph(retry, context)`，校验当前绑定/读取序列后发布图；`canvas-editor.js` 包装它，等待同案同画布旧 generation 已发写入结束，仍拒绝旧回调发布，再更新 membership；`table-workspace.js` 是外层包装，增加第三参数 `{ tableQuery = 'binding' }`、使查询绑定失效并只在首次成功读图后重查一次，`tableQuery: 'caller'` 留给恢复调用方。保持 Promise/提前失效的返回含义，不把它改成表示服务端写成功的布尔值。
- `selectCase`/`activateCanvas` 的最外层是 `workspace-session`：先 capture 草稿，再调用表格包装，最后 resetQuery。表格层先失效、调用 editor/原函数后登记重查绑定；editor 层在原函数返回后重置 history/membership。保持 capture 发生在表格清空选择/行之前。改变这些接口时联查源码边界式 harness 的抽取点，以及 A–B–A、旧写完成后再读、恢复输入后的单次查询与历史清空。
- `workspace-shell.js` 只管外围 chrome；`workspace-camera.js` 管镜头补偿；`table-workspace.js` 管查询/表格；`canvas-editor.js` 适配既有命令与原子追踪集合；`canvas-history.js` 管有界绑定内历史。沿 `docs/graph-workspace-plan.md` 的现有职责补接口，不新增同名第二状态所有者。做外壳交付时保持原节点/关系/边渲染，渲染改变须有自身明确需求和验证范围。

## 显示、事实与工作区

- `graph-view.js`、标签、布局和裁剪是显示政策：过滤不能修改完整追踪图、原观察、事件判断或精确金额；canvas 保存保留全部图数据，视觉导出说明当前视图和标签状态。按服务端 policy 呈现精确值/范围/未知，不从选中节点或几何重算金融结论。普通资金展示使用“笔”，来源观察/审计保留“条”，不混用两种口径。
- 案件/画布改变即撤销旧图事实；延迟布局、照片、下载/导出和请求回包都校验当前绑定与修订，包含 A–B–A。历史只存 ID、几何和显示设置，不存交易事实或照片；恢复追踪 membership 用既有 case/canvas 绑定的原子 compare-and-set，失败或含糊不能报告 undo 成功。
- 视窗裁剪只隐藏 drawing group，考虑路径/标签真实边界及穿越边，编辑后失效几何。`model.label` 可懒绘，`fundsLabel` 和来源元数据仍保留完整文本/金额用于保存和导出。标签保持 all/export、手动 path ratio 和几何变化后的隐藏标签坐标；结构锚点不是业务角色。细节见 `docs/graph-viewport.md`、`docs/graph-render.md`。
- 查询侧栏与编辑工具独立折叠，hover/focus 只露 seam，点击才改状态；折叠仍可直接保存/撤销/重做/布局/适配。外壳变化保留查询、选择和 G6 camera；保持真实七列表格、完整金额字符串及局部横向滚动，详见 `docs/graph-workspace-plan.md`。
- 本地 G6 4.8.19 补丁以完整哈希固定；更换 vendor 不是普通源码格式化，须按 `static/vendor/G6-PATCH.md` 核对来源/许可及验证契约，不向其他版本盲目重打补丁。`--graph-assets` 仅为合成预览，不进入生产配置。

## 选择验证

- 生命周期接口变更联查 `tests/test_canvas_editor.py`、`tests/test_canvas_history.py`、`tests/test_table_workspace.py`、`tests/test_workspace_session.py`；边详情移动联查 `tests/test_edge_workspace.py` 及其 harness 抽取点。文件名/资源/加载链变化须同步核对 HTML 引用、必要调用方和相应测试，不按风格改历史协议。
- 在选定 checkout 根目录经 `.venv/bin/python scripts/test.py` 运行所涉 harness 包装，再在隔离合成预览中验证受影响用户流程；Node VM/DOM/G6 fixture 不等于浏览器验收。vendor 变化执行 `.venv/bin/python scripts/patch_g6_validation.py --check`；几何/标签/导出变化按相应专题检查完整金额、保存/导出和失效行为。性能结论另依固定合成输入、实际候选与 `docs/performance.md`，不将 harness 运行时当真实渲染收益。
