# app/ 局部规则

适用于本目录及子目录；先读根 AGENTS。这里补后端职责和验证入口，不扩大任务授权。所有相对验证命令从选定 checkout 的仓库根目录执行。

## 领域入口与依赖边界

| 层/职责 | 现有权威入口 | 应保持的边界 |
| --- | --- | --- |
| 来源解析与记账 | `importer.py`、`table_import.py`、`feedback_import.py`；`source_ledger.py` | 解析保留源行、字段角色与版本；来源观察/记账不等于经济事件或净结算。`services/imports.py` 编排落库；`importer.commit_rows` 保留既有事务内写入适配，纯 parser 不新增数据库写入或另起持久化用例。 |
| 身份 | `evidence_resolution.py` 的 `rebuild`/`registry`；`representative_cards.py` 仅作显示 | 卡/账户/子账户是带类型、来源和作用域的可撤回关系，不做无条件等值合并；名称、余额、邻近和相似交易不证明身份。显示卡号不改变稳定账务实体或原流水。 |
| 事件与所选资金口径 | `event_resolution.py` 的 `rebuild`；`bank_posting_pairs.py`/`bank_single_postings.py` 的已登记来源合同；`funds_projection.py` 的 CTE/summary/from_rows | 同笔须按明确版本/作用域证据判断，通用流水号、凭证号、同秒同额不能代替证明。本地记账证明不宣称外部结算。观察金额、确认事件金额与未决范围各按返回 policy 表达，不能在调用方自行再去重。 |
| 时序与条件本金 | `ledger_navigation.py`、`principal_ledger.py`、`principal_evidence.py`；计算核 `principal.py`/`principal_network.py`；编排 `principal_multihop.py` | 来源记账顺序不由导入 ID/代表 ID 推断。完整账簿区间独立于显示过滤；覆盖、期初、余额种类、透支/可用性、竞争分录及冲正缺证时保留未知、受限或不一致状态。仅在非空可行集的上下界一致时认定模型条件下确定份额，空可行集报告冲突。精确可行范围和 FIFO 分别标注，多跳用联合容量，不传播或相加独立上界。 |

- `main.py`、`api/`、`analysis_routes.py`、`principal_routes.py` 适配 HTTP、请求/响应及既有业务入口；`services/` 编排完整用例；`db.py`/`paths.py` 管连接、事务和运行位置。保留兼容入口，不因文件较大先迁出一半职责，不预建 storage/domain 等空层。
- 身份、事件、本金是不同推理层；取得某一层证据不自动满足下一层前提。原始 `flows`、来源日志与余额不被派生身份/事件结论覆盖；不能用事件余额代表任一账簿余额，或把交易额/中间份额相加当最终本金。
- 复用现有锁/完整私有读快照，让选择、修订校验和投影同属一份事实；`funds_projection` 的 SQL 只接收应用持有的可信查询，保留其保留列约束。本金公共读取沿 `principal_routes`/既有用户动作绑定修订，不把请求者的勾选或余额吻合当来源证明。

## 导入、撤回与兼容

- 继续由 `services/imports.py` 在一个事务里提交来源观察、流水/账户和持久回执，并在该事务内调用既有身份/事件重建。来源删除、冲突或案件包恢复必须沿现有重建/修订失效链保持派生结果一致；不要另开后台写入或把整案重建塞入每次查询。
- 根规则要求的失败回滚也覆盖来源日志、辅助证据、批次、回执和文件补偿。数据库/文件补偿不是任意断电的跨系统原子性保证；不能由一次异常回归宣称全故障恢复。
- parser/profile、来源重放和 `.fundcase` 分支必须保留历史含义；版本改变不自动升级历史会话。包内来源不能冒充本机原件，旧客户端不能据此视为支持新格式。修改时核对实际选择分支与兼容回归，不依靠过期摘要猜版本号。

## 选择验证

- 从受影响公共行为选用例，不把下列导航当必须全跑的列表：身份/撤回看 `tests/test_account_registry.py`、`tests/test_evidence_lifetime.py`、`tests/test_evidence_replay.py`；事件/资金口径看 `tests/test_event_resolution.py`、`tests/test_funds_projection.py`、`tests/test_funds_projection_window.py`；来源记账/时序看 `tests/test_source_ledger.py`、`tests/test_bulk_source_order.py`；本金看 `tests/test_principal_api.py`、`tests/test_principal_evidence_guards.py`、`tests/test_principal_book_model.py`、`tests/test_principal_network_api.py`。
- 在选定 checkout 根目录通过 `.venv/bin/python scripts/test.py` 运行相应测试；事务/来源生命周期变化补 `tests/test_import_tasks.py`、`tests/test_case_isolation.py`、`tests/test_cases.py` 等所涉公共边界。生成 API 契约和 UI/导出调用方按根规则联查；不得仅凭 parser 单测宣称导入、图谱、导出或重开已通过。
