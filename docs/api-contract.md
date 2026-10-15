# FundsTrace v1 API

新导入目录的运营时间（会话 `created_at/expires_at`、来源 `imported_at`）使用带 `Z` 的 UTC 字符串，界面明确标注 UTC。交易时间和既有业务表时间保留原口径，不据此推断交易时区。

React 工作台使用 `/api/v1`，G6 工作区继续使用兼容 `/api`。`app/api/v1.py` 声明请求、响应和固定 `operation_id`；两套导入路由都调用 [统一导入服务](../app/services/imports.py)，不再通过 `main._run_commit` 或进程内任务字典执行。服务负责案件／surface 绑定、文件核验、持久任务、事务与留存，导入器保留资金清洗算法。生命周期和操作入口见 [import-lifecycle.md](import-lifecycle.md)。

## 12 个版本化操作

| 方法与路径 | 请求 | 响应 |
| --- | --- | --- |
| `GET /cases` | 无 | `CaseList`，`items: Case[]` |
| `POST /cases` | `CaseCreate`，`name` | 201 `Case`；名称唯一冲突 409 |
| `GET /cases/{case_id}/stats` | 无 | `CaseStats`，流水数、账户数、批次数、金额合计 |
| `GET /cases/{case_id}/flows` | `account`、`kw`、`page=1`、`page_size=50` | `FlowPage`，`items/total/page/page_size` |
| `GET /cases/{case_id}/flows/{flow_id}/sources` | `page=1`、`page_size=50` | `FlowSourceList`，来源摘要、源行口径及首次／重复关联 |
| `GET /cases/{case_id}/sources/{source_id}/file` | 无 | 本案已登记的原始上传文件；附件流，`Cache-Control: no-store` |
| `GET /cases/{case_id}/imports` | `limit=100`，1～200 | `ImportSessionList`，按创建时间、ID 倒序的最近会话与持久回执 |
| `POST /cases/{case_id}/imports/cleanup` | `CleanupRequest`，`dry_run=true` | `CleanupResult`；默认预览，`false` 执行本案到期文件清理 |
| `POST /cases/{case_id}/imports/preview` | multipart `file` | `ImportPreview`，模式、列映射、统计、前 8 行及本方账号候选 |
| `POST /cases/{case_id}/imports/{import_id}/preview` | `RepreviewRequest`，`mode/mapping` | `ImportPreview` |
| `POST /cases/{case_id}/imports/{import_id}/commit` | `CommitRequest` | `CommitStarted`，`started` |
| `GET /cases/{case_id}/imports/{import_id}` | 无 | `ImportJob`，进度、结果或安全错误提示 |

表中路径均追加在 `/api/v1` 后。契约完整字段见 [OpenAPI](../contracts/openapi-v1.json) 和由同一 schema 生成的 [TypeScript](../contracts/api-v1.d.ts)。

## 无损数据与分页

案件、流水、批次 ID 是十进制字符串；上传／来源 ID 是 opaque 十六进制字符串，不是授权。账户始终是文本。金额和余额使用固定两位十进制字符串，禁止客户端将资金数值或 ID 转成 JavaScript `number`。缺失余额、交易时间及案件创建时间为 `null`；其余流水文本空值规范化为空字符串。查询在存储层提取原始 Decimal 后序列化，避免经过旧 API 的浮点转换。统计的 `amount_totals` 按币种分别累加当前案件全部原始流水，不按图谱显示去重，不换汇。只有单币种时 `amount_total` 才是该币种的合计，`currency` 是其币种；多币种时两者均为 `null`。空案件返回 `amount_total:"0.00"`、`currency:null`、`amount_totals:[]`。历史缺失币种按空字符串归组，不能据此假定为人民币；现有导入器的默认币种规则保留。

流水按 `trade_time DESC NULLS LAST, id DESC` 排序。`page` 为 1 至 2147483647，`page_size` 为 1 至 200；`account` 在付款及收款账号上按包含匹配，`kw` 在摘要及流水号上按包含匹配。过滤、计数和分页保持同一案件范围。案件不存在返回 404，不伪装成空列表。

## 导入、恢复与兼容路由

预览成功后，数据库保存案件、surface、文件摘要／大小、模式／映射和到期时间。重新预览、提交和进度均核对案件与会话；不能访问其他案件。提交冻结本次 `mode/mapping` 和主账号选项，再启动后台解析与写入。映射必须引用文件实际列。`self_accounts` 默认 `[]`；`mark_accounts:null` 保留自动标记语义，`[]` 表示明确不勾选文件候选。账号数组不得包含空值、空白或内部接口分隔符。

会话同时绑定案件与 surface。v1 不能接管 legacy 会话，legacy 不能读写 v1 会话。v1 列表可显示同案 legacy 记录，React 将它导向 G6 工作区。新建 legacy 会话可在预览时绑定案件；未绑定者在第一次有效重新预览／提交时绑定，此后不能换案。旧进度接口须提供绑定案件，不接受 glob。此前旧磁盘缓存和旧进程内任务不会被扫描或伪装成新的持久会话。

同一会话并发提交只启动一次；`parsing/importing/done` 状态再次提交返回 `started:false`，不覆盖冻结请求。错误后可重新预览并确认重试。事务同时提交流水、批次、主账号、来源关联与成功回执。`result.ok` 为本次新增流水，`rows_dup` 为重复行，`skipped` 为解析跳过行，`batch_id` 为字符串；进度 `done/total` 不能当作新增笔数。

任务、尝试次数和回执存于数据库。启动恢复把旧进程遗留活动任务标为 `error/import_interrupted`，不自动重放；用户从“导入记录”继续，重新预览原文件后确认。已完成回执可重读，即使原文件后来已清理。`ready` 尚未形成任务，进度接口返回 404，应从会话列表发现。`source_available/recoverable` 是廉价文件元数据提示，实际预览／提交仍须完整内容核验。

## 来源、下载与清理

来源响应包括文件名、SHA-256、字节数、模式／映射、工作表、解析器版本、导入时间、`source_row` 与 `disposition`。`csv_record` 按逻辑记录计数，包括表头与空记录；带引号多行字段仍是一条记录。`worksheet_row` 是工作表实际行号；`unknown` 不提供已标定行号保证。同一流水可关联多条首次／重复来源。历史无来源流水返回空列表，不补造文件或源行。

下载核对本案来源和会话摘要，再从不跟随 symlink 的文件描述符生成校验快照；实际发送同一快照，不重新打开路径。包导入来源 `origin=package` 没有本地原始上传，不能下载。成功下载是二进制响应，不能按 JSON 解码。

待确认／重新预览上传为 7 天，完成／失败／启动标记中断任务为 30 天；到期只是最早清理资格。没有定时自动清理，只有显式 UI／API／CLI 调用才处理。活动任务和仍被 `flow_sources` 引用的文件受保护；重新预览撤销旧清理队列并续期，GC 重试也复核到期状态。清理不删除来源元数据或回执，不盲删未知／变更文件，不跟随 symlink；失败保留持久队列。`eligible/protected/queued` 是计划／检查计数，不能简单相加为独立文件总数；执行结果另有 `deleted/missing/failed`。

`.fundcase` 格式 2 携带来源元数据与关联，明确不含原始上传、会话、任务或回执；仍接受格式 1。包不是原始证据文件备份，详见 [生命周期与案件包](import-lifecycle.md)。

## 错误与生成

JSON 错误统一为 `{"error":{"code":"...","message":"..."}}`。400 表示文件或请求不能处理，403 未激活，404 案件／会话／来源不存在或不属于当前范围，405 方法不支持，409 状态冲突（案件重名、原文件缺失／变化、已开始会话重新预览等），422 参数或映射无效，500 内部失败。界面可显示 `message` 并根据 `code` 决定恢复动作。响应和任务轮询不返回服务器路径、异常原文或 traceback。应用组装时同时挂载 `router` 并调用 `install_error_handlers(app)`，覆盖 v1 路由级 404／405；旧路径沿用兼容异常格式。

运行 `.venv/bin/python scripts/export_api_contract.py` 更新两个契约文件，运行同一命令追加 `--check` 检测漂移。生成器在导入任何应用模块前强制使用临时 `FUND_DATA_DIR`，只导出 v1 路径和可达 schema，不接触运行中的业务目录；无需 Node 依赖。HTTP 回归使用临时真实 DuckDB 与合成 CSV/Excel，不代表浏览器、安装包或发行验收。
