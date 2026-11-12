# frontend/ 局部规则

适用于 React 试点；先读根 AGENTS。它与传统 G6 入口并存，不因试点存在自动替换旧工作台。所有相对验证命令从选定 checkout 的仓库根目录执行。

- `src/panel/index.ts` 是共享入口。`src/panel/` 只消费宿主提供的案件绑定版本会话；独立路由、选案、HTTP、文件/下载导航属于 `App.tsx`、`src/standalone/` 和 `src/adapters/`，不让 panel 直接读取浏览器全局或另起请求路径。UI port 不是宿主授权或受保护显示合同，Analytix 未因该接口被接入。
- 换案、同案重绑定或显式撤销都结束旧会话；异步请求、回调、宿主动作和显示状态须检验当前 session/binding，保留拒绝迟到响应及缺失 host action 的处理。不要用案号相同替代版本/会话一致；不把宿主界面行为当服务器校验。
- 根规则的金额/ID 字符串、按币种汇总与 `null` 余额贯穿 adapter 和组件；不要用 `Number`、格式化后的字符串或显示值重算金额/身份/事件/本金。`contracts/` 仍由 Python schema 生成，公共变更联查后端调用方。
- 面板 CSS 限于 `.funds-trace`，按所在 slot 适应尺寸，不做 document reset；host-demo 的 DEV fixture 仅供合成同案重绑定、撤销、窄槽和迟到响应，不接 HTTP 或业务库，生产产物不含该 fixture。详见 `docs/frontend-host-adaptation.md`。
- 新 React 组件用 `PascalCase.tsx`，新工具/功能文件用 `kebab-case.ts`，测试跟随文件基名。既有 `standalone/browserActions`、`standalone/workspaceBridge` 及两个对应测试按演进文档中的枚举兼容例外保留，不把例外推广成该目录新默认，不为统一样式引入重命名或垫片。获准更改路径时一并处理 imports、构建/资源引用、测试与文档入口，不按风格改变协议或保存格式。
- 保留本目录 `.npmrc` 和锁文件，不改全局 npm 设置。在选定 checkout 根目录，新环境/锁变更先 `npm --prefix frontend ci`，React 候选执行 `npm --prefix frontend run build` 和 `npm --prefix frontend test`；build 已含 TypeScript 检查。按受影响流程补实际用户操作；panel 边界变更覆盖同案重绑定、撤销、迟到回调/响应、缺失动作和 slot 尺寸，必要时用现有 host-preview。组件测试、DEV host-demo 与独立合成导入分别报告，不替代浏览器、真实宿主或 Windows 结论。
