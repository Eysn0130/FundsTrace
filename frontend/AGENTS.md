# frontend/ 局部规则

继承根 AGENTS；React `/workspace/` 试点与 G6 并存。共享面板／宿主边界依 `docs/frontend-host-adaptation.md`，命名及枚举兼容例外依 `docs/evolution.md`。

- src/panel/index.ts 是共享入口；panel 只消费宿主的案件绑定版本会话，不读浏览器全局或另起请求路径。路由、选案、HTTP、文件／下载导航属于 App.tsx、standalone 和 adapters；UI port 不是宿主授权／受保护显示合同，也不代表已接入 Analytix。
- 换案、同案重绑定及撤销结束旧会话；请求、回调、宿主动作与显示检验 session/binding，保留迟到拒绝及缺失 action 处理，案号相同不代替会话版本，宿主行为不代替服务器校验。
- adapter／组件消费原始字符串和返回 policy，不用格式化显示值重算金额、身份、事件或本金。
- CSS 限 `.funds-trace`，适应所在 slot，不做 document reset；host-demo DEV fixture 只供内存合成绑定／撤销／窄槽／迟到检查，不接 HTTP 或业务库，生产产物不含它。
- 保留本目录 .npmrc／锁文件，不改全局 npm。新环境／锁变更先 `npm --prefix frontend ci`；候选执行 `npm --prefix frontend run build`（含 TypeScript）和 `npm --prefix frontend test`。panel 边界覆盖同案重绑定、撤销、迟到响应、缺失动作及 slot；组件、host-demo、独立合成导入与实际浏览器／宿主证据分别报告。
