# static/ 局部规则

继承根 AGENTS；本目录维护传统 G6 工作台。业务判断消费后端，交互／状态所有权见 `docs/graph-workspace-plan.md`。

## 组装与状态

- 经典脚本加载次序参与接口。改 loadGraph、selectCase、activateCanvas、刷新或脚本排列前，先读工作区专题“现有入口与包装次序”，核对 index.html 的 app → canvas-history → canvas-editor → table-workspace → workspace-session 及全部包装；保留 Promise／同步返回、capture 早于清表、旧写结算后再读、caller 查询所有权和首次成功后单次重查。
- workspace-shell 管外壳，workspace-camera 管镜头，table-workspace 管查询／表格，canvas-editor 适配命令及原子 membership，canvas-history 管绑定内历史；不新建第二状态所有者。外壳交付保留原渲染，渲染改变须有明确需求与自身验证。
- 查询／工具独立折叠，hover／focus 仅露 seam，点击才改状态；保留查询、选择、G6 camera、真实七列表格、完整金额及局部横滚。历史只存 ID／几何／显示，不存交易事实或照片；membership 恢复沿 case/canvas 原子 compare-and-set，含糊或失败不报告 undo 成功。

## 显示与资源

- graph-view、布局、标签和裁剪仅为显示，canvas 保存完整图，视觉导出说明视图／标签状态；按后端 policy 呈精确值／范围／未知，不从几何重算金融结论。普通资金展示用“笔”，来源观察／审计用“条”。
- 裁剪仅隐藏 drawing group，考虑路径／标签边界及穿窗边，编辑后失效几何。model.label 可懒绘，fundsLabel／来源元数据仍保全文本金额；保留 all／export、手动 path ratio 及隐藏标签几何，结构锚点不是业务角色。细节依 `docs/graph-viewport.md`、`docs/graph-render.md`。
- G6 4.8.19 本地补丁以完整哈希固定，vendor 更换按 `static/vendor/G6-PATCH.md` 核来源／许可及验证，不向其他版本盲套。--graph-assets 仅合成预览，不进生产配置。

## 选择验证

生命周期联查 tests/test_canvas_editor.py、test_canvas_history.py、test_table_workspace.py、test_workspace_session.py；边详情联查 test_edge_workspace.py 和 harness 抽取点，文件／加载链变化联查 HTML 与调用方。经根规则的 scripts/test.py 跑受影响包装；vendor 执行 `.venv/bin/python scripts/patch_g6_validation.py --check`，几何／标签／导出依专题查完整金额、保存／导出及失效。合成预览补实际用户流程；Node VM／DOM／G6 fixture 不代替浏览器，harness 时间不算渲染收益。
