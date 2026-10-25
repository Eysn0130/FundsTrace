# 统一反馈与操作进度

FundsTrace 使用第 1 张石墨蓝视觉基线：中性表面、克制的状态色、明确的下一步动作。短提示位于工具带下方；进度、结果与待核对留在原操作界面。按用户最新要求，不新增底栏、导航通知控件、计数徽标或全局任务/通知面板，也不占用工作区布局。导入结果仍通过现有“导入记录”和待核对区域查看。反馈层管理展示，业务控制器继续拥有写入、事务、租约和取消能力。

## 展示契约

| 场景 | 反馈与动作 |
| --- | --- |
| 空闲、就绪 | 不主动通知，不新增全局状态控件。 |
| 输入校验 | 字段或原操作区说明；未选追踪锚点时聚焦选择控件。 |
| 排队 | 仅接收真实 queued 状态，不推算队列位置。 |
| 总量未知 | 有名称的原生不确定进度；不显示假百分比或预计时间。 |
| 总量已知 | 经校验的 done/total；比例只表示当前阶段。 |
| 等待决定 | 原生 dialog 列明对象与影响，危险确认默认聚焦取消。 |
| 已完成 | 短提示或原结果页；原结果页保留回执入口。 |
| 完成、有注意事项 | 具体来源分类仍在处理报告，不将未计入资金的记录统称失败。 |
| 写入完成、视图待刷新 | warning 和只读刷新，不重新提交写入。 |
| 确定失败 | 原位保留原因、输入及原有重试边界。 |
| 结果未知 | 持续琥珀状态和只读核对；不推断成功、失败、回滚或取消。 |
| 取消 | 只有业务确实停止计算或报告取消才显示 cancelled；关闭进度不调用取消接口。 |
| 上下文失效 | 同步撤销旧提示和动作；案件、画布及各自 generation 共同防止 A–B–A 旧回调复活。 |
| 启动恢复、中断 | 读取案件内实际 session/job/receipt；不自动重放 preview 或 commit。 |

Toast 最多同时显示 3 条，等待队列上限 20；重复消息聚合。普通提示默认 4 秒，悬停、键盘焦点、页面隐藏和模态界面打开时暂停。任务和重要状态继续保留在原操作区；导入持久回执来自现有服务端记录。共享 store 的有界会话状态不作为审计日志，也不新增 UI 历史入口。

模态界面打开时全局 Toast 隐藏、暂停并从可访问树移除。React/分析 iframe 向父页面报告模态状态；隐藏页面不能阻挡当前页面，关闭最后一个对话框才解除阻挡。原有导入上传、图谱采样、案件删除等捕获锁仍决定当时是否可以关闭。

## 模块与责任

| 文件 | 责任 |
| --- | --- |
| `static/feedback-core.mjs`、`.d.mts` | 作用域、任务生命周期、去重、队列、暂停、动作执行与稳定快照；无网络和业务写入。 |
| `static/feedback-ui.mjs`、`feedback.css` | 旧界面和独立分析页的 Toast 展示，沿用现有图标与主题 token；不渲染全局任务控件。 |
| `static/feedback-dialogs.js` | 替换批次删除和清空画布的浏览器 confirm；具体对象与绑定在确认前后复核。 |
| `static/feedback-workspace.js` | 只读观察案件导入记录、进度和后台完成；沿用现有租约所有者验证终态。 |
| `static/app-navigation.js` | 当前案件与页面的唯一父桥，实例握手、模态隔离及操作/布局状态语义。 |
| `static/workflow-ui.js` | legacy 导入稳定任务 ID、原位进度与现有导入恢复流程。 |
| `frontend/src/feedback/Feedback.tsx` | 可选反馈 port 与独立 React Toast fallback；通用 panel 不直接访问 parent，不增加全局入口。 |
| `frontend/src/standalone/workspaceBridge.ts` | 独立 shell 的受约束消息通道。 |
| `static/analysis-link.js`、`analysis.js` | 分析/报告各自生命周期，数据 revision 和页面实例失效时撤销迟到结果。 |
| `static/desktop-updates.js` | 镜像真实更新服务，应用级 scope；下载完成与重启安装分别表述。 |

## 导入核对与跨页面边界

父桥验证 exact origin、source、channel、version、caseId、generation 和页面 instanceId。反馈事件仅允许有限字段、长度、状态与合法进度，不能传函数、HTML、文件、账户资料或任意业务动作。原操作由宿主提供，并在执行时再次验绑定。

导入任务键为 `import:<caseId>:<importId>`，账户核对按宿主 operationId 更新同一操作状态。React/legacy 的重复阶段不产生第二份任务；子页面宣告导入终态只触发宿主读取，不能解除写锁。既有 `releaseExternal` 重新读取实际 job 和持久 attempt，核验通过后才解除原租约；随后 `feedback-import-settled` 同步该任务的局部提示。仍打开且匹配原任务的弹窗只读刷新终态，不因提示自动打开窗口、替换另一任务或重放提交。普通 context 中暂时缺少租约不能被当成核验成功。

提交回执丢失、进度读取失败、ready 记录与未知租约并存时保留 unknown。查询和恢复只发送 GET。导入已完成后列表读取失败保持 warning；重试只刷新视图，旧案件迟到的失败不能污染返回后的新 generation。

程序内原任务导航请求在当前 case/generation 内排队；ready 后先发送 context，再读取原 importId，切案清理排队请求；不新增全局导航控件。iframe reload 或新实例替换将旧实例的运行任务收敛为 unknown，保留已完成结果；旧实例消息不能更新新实例。导入真实后台任务由独立只读观察继续恢复。

## 视觉、动画与可访问性

- 反馈继承 surface/text/border/accent/state token，图谱资金角色色独立维护；浅色与深色共享结构。
- 正文 14px/1.5、标题 semibold、通知圆角 10px、确认弹窗 12px；控件沿用现有字体和 SVG 图标。
- Toast 进入 180ms，退出 140ms。DOM 按稳定 ID 更新，保留已聚焦动作；reduced-motion 移除动画与过渡。
- 不自动夺焦。短提示用克制的 polite/alert，图标配状态文字；原生 progress 有名称，原生 dialog 处理焦点边界、Escape、IME 和回焦。

## 验证与并行施工

公共行为回归覆盖 scope/queue/暂停、原生确认、父桥校验、iframe 重载、导入原租约核验、关闭进度与恢复、A–B–A、已写入刷新失败和更新真实阶段。React 测试和构建、完整合成 Python/G6 回归、桌面 Node 检查及资源白名单分别提供对应证据。浏览器证据与限制见根目录 `design-qa.md`。

导入清洗和账户索引施工在另一候选分支。本变更保持既有 API、持久化格式、清洗分类与金额契约；两候选汇合时重点复核 `ImportDialog.tsx`、`workspaceBridge.ts`、`panel/styles.css`、`static/index.html`、`workflow-ui.js` 的重叠差异，在最终合并候选重新运行 CI 和合成用户流程。当前 PR 不代表安装包、Windows/DPI、读屏或真实案件验收。

OS Notification 尚无用户设置和受验证平台通道，本变更不创建系统通知权限、外部推送或敏感内容广播。若以后增加，需单独实现用户开启、失焦去重、脱敏和点击回到有效任务。
