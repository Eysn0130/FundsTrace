# app/ 局部规则

继承根 AGENTS；下文仅补后端职责。HTTP 与完整用例地图见 CONTRIBUTING“代码地图与兼容性”，领域语义见 `docs/folder-evidence-import.md`。

## 领域入口与依赖边界

| 职责 | 现有入口 | 本层边界 |
| --- | --- | --- |
| 解析／来源记账 | importer、table_import、feedback_import；source_ledger | 保留源行、字段角色及版本，来源观察不等于事件／净结算。services/imports 编排；importer.commit_rows 是事务内写入适配，纯 parser 不新增持久化。 |
| 身份 | evidence_resolution.rebuild／registry；representative_cards 仅显示 | 卡／账号／子账是带类型、来源及作用域的可撤回关系，不无条件等值合并；名称、余额、邻近或相似交易不证明身份，显示号不改账务实体。 |
| 事件／所选资金 | event_resolution.rebuild；bank_posting_pairs／bank_single_postings；funds_projection | 只用已登记版本及范围合同，通用编号／同秒同额不代替证明。本地记账不冒称外部结算；返回 policy 的观察值、事件值和未决范围不能由调用方再去重。 |
| 时序／条件本金 | ledger_navigation、principal_ledger／principal_evidence；principal／principal_network 核及 principal_multihop 编排 | 来源顺序不按导入／代表 ID 推断，完整账簿区间独立于显示过滤。覆盖、期初、余额种类、透支／可用性、竞争分录及冲正缺证保留未知／受限／不一致。非空可行集界限一致才是模型内确定，空集为冲突；范围与 FIFO 分列，多跳用联合容量、不相加独立上界。 |

- main.py 组装；api、analysis_routes、principal_routes、main_account_routes 适配 HTTP，services 编排完整用例，db／paths 管事务与运行位置。主账号候选／自动识别／人工标记集中 services/main_accounts，路由保留案件校验和事务；legacy CaseIdIn 由路由定义供 sample-data 复用，不换成 v1 字符串 ID 模型。
- 层间证据不自动满足下一层前提；不以事件余额替代任一账簿余额，或累加中间交易额当本金。复用既有锁／完整私有读快照，使选择、修订和投影同源。funds_projection SQL 只收应用可信查询，保留保留列合同；本金公共读取沿 principal_routes／既有动作绑定修订，请求勾选不是来源证明。

## 导入、撤回与兼容

- 提交事务内沿既有身份→事件重建；删除、冲突及包恢复沿重建／修订失效链同步派生状态，不另开后台写入或每查询整案重建。回滚覆盖来源日志、辅助证据、批次、回执及文件补偿；不宣称任意断电的跨系统原子性。
- parser/profile、重放和包分支保留历史含义；包内来源不冒充本机原件，旧客户端不被推定支持新格式。改动核对实际选择分支及兼容回归，不猜版本号。

## 选择验证

按行为选例，不要求整表全跑：身份／撤回看 test_account_registry、test_evidence_lifetime、test_evidence_replay；事件／资金看 test_event_resolution、test_funds_projection、test_funds_projection_window；来源／时序看 test_source_ledger、test_bulk_source_order；本金看 test_principal_api、test_principal_evidence_guards、test_principal_book_model、test_principal_network_api（均在 tests/ 下）。事务／生命周期另选 test_import_tasks、test_case_isolation、test_cases 等公共边界，经根规则的 scripts/test.py 运行；parser 单测不代替导入、图、导出或重开，契约生成与调用方沿根规则联查。
