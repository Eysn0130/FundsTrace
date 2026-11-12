# 开发与贡献

当前维护版本按自定义的[循迹 FundsTrace 社区使用与源码许可协议 1.0](LICENSE)提供有权授权的材料，属于源码可见软件，非 OSI 标准开源；同一文件附完整英文参考译文，以中文为准。欢迎可复现问题和范围清楚的 PR。先读 [AGENTS.md](AGENTS.md)、[许可说明](docs/licensing.md)（[English](docs/licensing.en.md)）和 [源码与私有数据边界](docs/source-boundaries.md)，再按本文建立开发环境。产品操作见 [工作台使用指南](docs/workspace-guide.md)，已发布安装包和验证限制见 [桌面交付](docs/desktop-delivery.md)。

## 贡献许可与权限

接受新的外部贡献前，贡献者与维护团队须确认其有权提供该贡献，并留存可核验的授权记录，至少包括：

- 实际权利人、贡献者身份及代表其他主体授权的权限；涉及雇主、共同作者或第三方材料的限制。
- 所覆盖的文件、提交或其他可定位的贡献范围。
- 按社区许可分发、由团队另行授予机构／商业许可，以及 [LICENSE 第七条](LICENSE)指定特别许可所需的明确权限。
- 确认日期、适用期限和可核验的原始确认；不得把普通 PR、代码被合并或沉默视为已授予全部权限。

这些记录由维护者妥善留存；公开仓库仅保留必要的来源和许可说明，不提交私人协议、身份材料或邮件全文。权限未明确前不接受相关贡献，也不对有争议的部分承诺独立商业再许可。这不构成版权转让、无限再许可或已经建立自动 CLA 签署流程。

贡献者保留其依法享有的版权；历史 [MIT](LICENSES/MIT.txt) 代码（包括当前快照沿用的部分）和第三方材料继续保留原许可与既有权利。CodeHaven969、linzijie6524 的非独占、免许可费特许涵盖本项目研发，以及各自其他产品的闭源商业集成、交付、在线服务和必要客户使用，并豁免相应源码公开义务，仍须保留适用通知。维护者已明确确认最初接手的原同事开发代码属于二人已确认的完整授权范围；这不构成版权转让，也不改变历史 MIT 或第三方权利，不推定其他未确认材料的授权，特许不自动适用于其他贡献者。

一般社区条款允许个人非商业修复和增功能，对外分发修改版须公开完整对应源码；机构、商业、对外在线服务及未获许可的单独换皮改名、转售行为，依 [LICENSE](LICENSE) 的授权要求处理。提交 PR 不替代使用或分发所需的许可。授权联系人为循迹 FundsTrace 项目维护团队的 Eysn0130：[eysn0130@gmail.com](mailto:eysn0130@gmail.com) 或 [402168648@qq.com](mailto:402168648@qq.com)。软件许可与启动授权码分别处理，现有免激活政策不变。

## 环境安装与测试入口

已验证的源码开发基线为 macOS arm64、Python 3.11、Node 22.22.1 与 npm 10.9.4。完整 Python 回归入口也需要 Node，用于合成导航检查。Windows 原生构建使用独立锁文件和入口，见 [可重复 Windows 构建与验证](docs/desktop-delivery.md#可重复-windows-构建与验证)。日常改动按下文选择受影响层的最小充分验证。

```sh
uv venv --python 3.11
uv pip sync requirements-dev.lock
```

锁文件包含精确版本和分发文件哈希；`requirements.txt` 是生产依赖输入，`requirements-dev.in` 增加测试工具。不使用 uv 时，先用 Python 3.11 创建 `.venv`，再在该环境执行 `python -m pip install --require-hashes -r requirements-dev.lock`。

```sh
# 完整回归
.venv/bin/python scripts/test.py

# 定向回归，可追加 pytest 参数
.venv/bin/python scripts/test.py tests/test_cases.py -q --tb=short
```

Windows 使用 `.venv\Scripts\python scripts\test.py`。入口强制使用临时目录、合成交易、合成许可和进程内 API 客户端，不读取已有服务地址；每个测试使用独立 DuckDB。不要复用原目录的 `dbg_*`、`patch_*` 或恢复脚本。

前端版本锁在 `frontend/package-lock.json`。`frontend/.npmrc` 固定安装选项并禁用依赖安装脚本，当前构建不依赖安装后脚本，无需改变全局 npm 配置。

```sh
cd frontend
npm ci
npm run build
npm test
cd ..
.venv/bin/python scripts/export_api_contract.py --check
```

`build` 同时执行 TypeScript 类型检查和 Vite 编译。前端目录中的 `npm run dev` 通过 Vite 代理连接 `127.0.0.1` 的隔离 FastAPI，默认端口 8300；可在启动前将 `FUNDS_TRACE_DEV_API_PORT` 设为合成后端打印的端口，仅接受 1–65535 的本机端口。代理覆盖 G6 根页和 `/analysis`，独立导航使用相对路径；不要指向业务服务。

## 合成界面与本地启动

完成 React 构建后，用一次性数据预览：

```sh
.venv/bin/python scripts/preview_synthetic.py
```

入口覆盖继承的数据目录与签名设置，使用公开合成许可、一次性数据库及 CSV，打印随机本地地址和上传样例路径；Ctrl-C 后删除临时状态，不连接原业务服务。

Vite 开发服务的 `/workspace/host-preview/` 提供纯内存合成宿主，用于换案、同案重绑定、撤销、600px 分栏和迟到响应检查，不请求 HTTP 或业务库。导入等真实用例仍在独立合成版验证。该入口由 `import.meta.env.DEV` 分支加载；生产产物须另检不含 fixture，见 [前端宿主适配](docs/frontend-host-adaptation.md)。

独立桌面开发预览也使用一次性合成数据：

```sh
uv pip install --python .venv/bin/python --require-hashes -r requirements-desktop.lock
npm --prefix desktop ci
npm --prefix desktop test
npm --prefix desktop run synthetic
```

Electron 44 运行时会在首次运行时下载；安装 NPM 包本身不证明二进制已安装。普通桌面启动为 `npm --prefix desktop start`，数据在自身 `userData/data`，无需配置产品授权。Windows x64 原生构建与 `-Installer`、手动 Actions、安装生命周期证据统一见 [桌面交付](docs/desktop-delivery.md)；不要将本机源码预览当作 Windows 安装验收。已发布 alpha.3 的历史授权与保留的 FT2／HMAC 验签、私钥和独立 Issuer 见 [历史授权维护](docs/activation-history.md)。

需要普通浏览器服务时，显式选择独立运行目录：

```sh
FUND_DATA_DIR=/absolute/path/to/private-runtime .venv/bin/python run.py
```

默认只监听 `127.0.0.1:8300`，`FUND_PORT` 可指定其他端口；Windows 配好环境后可用 `run.bat`。构建后 `/workspace/` 提供 React 试点，`/` 保留 G6；React 的“打开 G6 图谱”携带当前案件 ID，表格与图谱保留各自明确的统计口径。未构建前端时 `/workspace/` 返回 503 和构建提示。

## 运行目录与上传清理

`FUND_DATA_DIR` 统一控制 `fund.duckdb`、上传、照片、`license.dat`、`runtime.log` 和绘图缓存；未设置时使用被忽略的 `data/`。不要在原业务数据目录上试运行新候选。

React 的“导入记录”提供任务进度、完成回执、继续导入和上传清理；“核对 → 来源追溯”显示摘要、源位置及可用原件。继续导入前必须重新预览并确认，重启不会自动重放写入；没有可靠来源的历史数据明确显示缺失。完整流程及来源规则见 [导入生命周期](docs/import-lifecycle.md)。

上传待确认／重新预览后为 7 天留存，完成／失败／恢复标记后为 30 天；到期只获得清理资格，活动任务和来源引用继续保护，没有定时自动删除。CLI 默认只预览文件删除计划：

```sh
.venv/bin/python scripts/cleanup_imports.py --data-dir /absolute/path/to/managed-runtime --case-id 2
```

追加 `--apply` 才执行；省略 `--case-id` 可处理已删案件的 GC 队列。即使默认预览也会打开／初始化数据库，先停止占用同一库的服务并确认目录。完整命令、恢复与操作限制见 [显式清理](docs/import-lifecycle.md#7-天30-天与显式清理)。

## 代码地图与兼容性

以下按现有物理布局定位职责，不要求建立新的空目录。目录或符号的职责说明不授予读取业务资料、运行工具或外部写入的权限；执行前仍须读适用的项目指令和专题约束。

| 路径 | 职责 |
| --- | --- |
| `run.py`、`desktop_backend.py` | 根目录启动入口；后者是 Desktop 拥有的 Python sidecar，保持其认证、数据和进程归属 |
| `app/main.py` | 应用组装与兼容业务 API |
| `app/main_account_routes.py`、`app/services/main_accounts.py` | 主账号管理的 legacy HTTP／请求模型与完整用例；路由保留案件校验和事务，服务集中候选、自动识别及人工标记逻辑 |
| `app/api/`、`app/services/` | v1 契约、案件查询，两套路由共用的持久导入用例 |
| `app/db.py`、`app/paths.py` | 持久化事务和运行目录 |
| `app/importer.py` | 流水识别、清洗、案件内去重、源行关联及既有事务写入适配；`commit_rows` 不是纯解析函数 |
| `app/table_import.py`、`app/table_archive.py`、`app/feedback_import.py` | 各来源家族的读取、版本解释与逐记录处置；选择及冻结由导入服务拥有 |
| `app/case_packages.py` | 案件包校验、ID 重映射与导入导出 |
| `app/analytics.py`、`app/report.py` | 分析与 HTML 报告 |
| `static/` | G6 产品工作台、显示策略和几何状态；消费服务端金融判断 |
| `frontend/src/panel/`、`frontend/src/adapters/` | 共享 React 面板、绑定会话与独立 HTTP adapter |
| `frontend/src/App.tsx` | 独立案件外壳与面板组装 |
| `desktop/`、`desktop_backend.py` | Electron 窗口、受控下载与所属 Python 服务 |
| `desktop/issuer/` | 独立离线维护者签发工具，不连接案件数据 |
| `contracts/` | 从 `app/api/` 的 Python schema 生成的 v1 OpenAPI 与 TypeScript，不另行手改 schema |
| `tests/`、前端及桌面测试 | 合成公共行为、界面与宿主回归，期望值独立于产品结果 |
| `benchmarks/`、`scripts/` | 合成基准、隔离测试、契约导出、预览、显式清理与构建工具；工具能力不构成执行授权 |
| `static/vendor/`、`resources/` | 锁定的 G6、品牌及第三方解码资源；保留版本、来源、校验和许可边界 |
| `docs/` | 现行及已确认应有契约、设计、历史验收和展示材料；其中脚本、配置仍按实际执行影响判断 |
| `.github/`、`deploy/` | PR、审阅路由、工作流及发布策略输入，不是已部署或获准发布的证明 |
| `LICENSES/` | 保留独立许可文本，适用权利仍由许可材料维护 |
| `output/` | 被忽略的本机研究和执行输出，不是产品源码、分发清单或生效项目指令 |

### 来源基础与三类领域判断

| 层次 | 维护模块与入口 | 结论边界 |
| --- | --- | --- |
| 来源／原始记账基础 | [source_ledger](app/source_ledger.py) 的 `capture / validate / commit / selected_rows / summary`；[imports](app/services/imports.py) 的共享导入事务 | 保留已校验来源角色、版本、物理位置、原始观察和余额；来源观察不自动确定经济转账或净结算。 |
| 身份：它是谁 | [evidence_resolution](app/evidence_resolution.py) 的 `rebuild / registry`；[representative_cards](app/representative_cards.py) 的 `decorate` 只负责展示 | 根据带来源、机构、任务、字段角色和有效支持的类型关系生成实体与端点；名称、近似号码、代表显示号码不证明身份或历史有效期。关系不直接作为无条件等价合并。 |
| 事件：哪些观察可作为同一证据事件 | [event_resolution](app/event_resolution.py) 的 `parse_fields / resolve / rebuild`；[bank_posting_pairs](app/bank_posting_pairs.py)、[bank_single_postings](app/bank_single_postings.py) 的 `project`；[funds_projection](app/funds_projection.py) 的 `cte / summary / from_rows` | 身份归一不等于事件融合；通用流水号、凭证号、同秒同额不自动成为事件键。所选来源可给精确值或保守范围，账面过账证明不冒称外部结算。 |
| 本金：明确账面模型约束下可分配多少 | [ledger_navigation](app/ledger_navigation.py) 的 `context_window / neighbors`；[principal_ledger](app/principal_ledger.py) 的 `read / source_order / prepare / calculate`；[principal_evidence](app/principal_evidence.py) 的 `assess / context_result / tracking_result / path_result`；纯模型 [principal](app/principal.py)、[principal_network](app/principal_network.py)，[principal_multihop](app/principal_multihop.py) 的 `calculate` 与[修订绑定只读 adapter](app/principal_routes.py) | 来源须支持账务实体、币种、模型区间、顺序、期初、余额种类、竞争分录和确认事件。只有非空可行集的上下界一致，才是给定模型内确定份额；空可行集是冲突。独立上界不可相加，FIFO 单独列作规则结果；请求条件、显示子图或余额吻合不建立现实覆盖与历史可用性。 |

现有调用点按以下四条链定位，不另建事件总线或全局异步重放：

1. 导入提交：[imports._run](app/services/imports.py) 在 `db.import_transaction()` 中经 `importer.commit_rows` 写入；适用时 `source_ledger.commit`，再 `evidence_resolution.rebuild` → `event_resolution.rebuild(changed_sources=[import_id])`，成功回执在同一事务内保存。
2. 撤回：[main.del_batch](app/main.py) 在既有案件事务内撤回操作／身份支持及相应流水链接，重建身份 → 按变更来源重建事件 → `analysis_snapshot.invalidate`。仍有有效支持的事实保留；撤回不复活已移除事实。
3. 案件包恢复：[case_packages.import_package](app/case_packages.py) 校验后创建新案并重映射来源／流水等 ID，`source_ledger.refresh_index` → 适用的身份重建 → 事件重建 → 分析快照失效；合法派生可重建，不重新解析缺失原件或猜补关系。
4. 修订绑定只读：选择、修订校验及相应投影在锁或完整已提交快照内读取；[principal_routes.calculate / calculate_network](app/principal_routes.py) 在锁内核对修订，再计算条件模型，本金结果不写成源事实。

### 兼容性与专题契约

保留 `FUND_DATA_DIR`、`FUND_PORT`、数据库文件名和 `.fundcase` 扩展名。新上传的选择条件与保存解析版本见[自动导入](docs/automatic-import.md#新上传解析版本)，案件包的导出优先级、导入兼容和备份限制见[导入生命周期](docs/import-lifecycle.md#案件包格式与兼容)。案件包不携带原始上传字节、会话、任务或回执；恢复重映射来源／流水 ID，不补造缺失来源。批次删除仍须 `case_id`。

v1 的 ID 和金额采用字符串、空余额为 `null`、币种分别汇总；React 不把 v1 ID 或金额转为 `Number`。legacy 金额同样保持十进制字符串，既有 ID wire 形态继续兼容，不因治理文案统一迁移。API schema 及生成流程见[API 契约](docs/api-contract.md)。

导入支持范围见[自动导入](docs/automatic-import.md)；CSV／XLSX 及 BIFF8 XLS 的合成回归与平台／真实资料专项证据分别判断，可选 PDF 优化依赖仍须按实际环境验证。Windows 静默安装证据不覆盖交互 GUI、DPI 和系统保存。[性能记录](docs/performance.md) 绑定各自历史候选、输入和计时边界，不代表当前候选性能。初始化 checkpoint 和进程恢复回归不构成磁盘损坏或任意断电保证；业务备份应在一致状态下包含完整运行数据。

进一步设计与历史证据按主题查阅：

| 主题 | 文档 |
| --- | --- |
| 工作台操作、交互验收与工作区约束 | [使用指南](docs/workspace-guide.md)、[设计验收](design-qa.md)、[工作区交付](docs/graph-workspace-plan.md) |
| 新上传入口、选择条件与保存版本 | [新上传解析版本](docs/automatic-import.md#新上传解析版本) |
| 导入操作、恢复、留存与案件包格式 | [导入生命周期](docs/import-lifecycle.md)、[案件包格式与兼容](docs/import-lifecycle.md#案件包格式与兼容) |
| 金额、币种、时间、有效流水与普通笔数 | [金额契约](docs/money-contract.md) |
| 来源身份、事件与条件本金 | [文件夹导入与来源证据关联](docs/folder-evidence-import.md)；实现导航见[来源基础与三类领域判断](#来源基础与三类领域判断) |
| API 形状及生成契约 | [API 契约](docs/api-contract.md)；Python schema 为输入，`contracts/` 为生成输出 |
| 传统工作台入口及包装职责 | [现有入口与包装次序](docs/graph-workspace-plan.md#现有入口与包装次序) |
| G6 首绘、标签、裁剪与历史性能 | [首绘与标签记录](docs/graph-render.md)、[图谱视窗](docs/graph-viewport.md)；不据此宣称新布局或桌面性能 |
| 共享面板和未来宿主 | [前端宿主适配](docs/frontend-host-adaptation.md)、[Analytix 整合建议](docs/analytix-integration.md)、[Windows／插件研究](docs/windows-and-plugin-plan.md)；UI port 尚非已接入的 Host／受保护显示合同 |
| 技术选型、命名与接管历史 | [演进建议](docs/evolution.md)、[新文件命名与兼容例外](docs/evolution.md#新文件命名与兼容例外)、[接管记录](docs/takeover.md) |

仓库专题保存现行契约和已确认、可公开的应有规则；尚未实现或未验证的部分须明确标注。源码与实际执行证据说明候选当前行为，不能单凭实现存在认定应有行为正确，也不能把写入规范当作已经实现。二者冲突时记录可出现的触发、受影响契约、候选及差异，由相应领域审阅者／维护者决定处理，不以改文案自动合法化缺陷。

Obsidian 工程笔记保留目标、决定理由、私有定位及历史链接；实际执行收据留在所属候选的输出或获准证据位置，笔记只留可定位索引。不自动同步两处正文，也不上传本机绝对路径、原始日志、私人材料或冻结证据。

维护只在直接相关变化时触发：上传选择器、profile 或保存／恢复分派改变，更新自动导入的版本表；包字段、优先级、校验或迁移改变，更新生命周期的格式章节；领域意图改变，更新对应专题及实现／证据状态；文件或标题移动，只改受影响的直接引用；候选或证据变化，更新本批交接。先复用现有工具，出现具体漏项后再评估窄范围自动化，不维护逐测试依赖图。

## 新成员从一个合成用例开始

按上文的 Python 3.11、Node 基线和锁文件安装依赖，先运行一个独立合成用例，确认理解案件包往返行为：

```sh
.venv/bin/python scripts/test.py tests/test_cases.py::test_package_round_trip_preserves_duplicate_rows_and_all_associations -q
```

Windows 将 Python 路径换为 `.venv\Scripts\python`。需要看界面时，使用上文的合成预览地址和样例；不要连接已有业务服务。代码职责见 [代码地图](#代码地图与兼容性)。

## 分工和 PR

- `main` 保持可交付；新主线任务从核实的最新 `origin/main` 建短分支，指定候选接续和固定只读对照按下文处理。每个 PR 完成一个可说明、可验证的行为变化，合并后结束该分支。简单修复直接开 PR；较大功能、数据迁移或公共契约变更先明确方案和验收，不要求所有修改额外建立 Issue 或计划文件。
- 开始前在 Issue、PR 或团队任务中写清问题、验收行为和认领的模块／文件。多人并行时独占写入范围，保留他人修改；尤其避免同时编辑大文件 `app/main.py` 和 `static/app.js`。确需交叉修改时先协调顺序，再由后一位接续最新改动。
- 修缺陷先给最小合成复现，说明步骤、预期和实际结果。可行时用公共行为回归覆盖原问题，确认修复前失败、修复后通过；期望值来自独立合成样例。
- 做最小完整修改，连同必要调用方、测试和文档一起交付。新增抽象、依赖或兼容路径须有当前需求；无关重构另开任务，不用临时绕过、弱化断言或重复实现掩盖缺陷。
- 一个提交表达一个完整意图，形成可审查进展后 push；未完成可使用 Draft PR。PR 使用仓库模板说明问题、行为变化、实际验证及兼容／数据影响。作者对完整交付负责，Eysn0130 审核后合并；合并不自动触发封装或发布。

### 候选入口与交接

开始或接续时先确定本次入口：

- 新主线任务：核对当时 main 身份，在当前授权内选用短分支及隔离 checkout，再认领写入范围。
- 指定候选接续：核对指定位置、分支／实际 SHA 或内容身份、owner、与 main 的关系和未提交改动；保留原候选与证据，不自动改成最新 main、rebase 或清理旧现场。
- 固定只读对照：按指定身份和范围读取、比较，不把它登记为当前施工候选，不因读取而创建分支或处置其他目录。

普通任务用以下四项交接即可，复杂任务再补实际需要的资源或交付定位，不强制新 schema：

- 候选：类型、用途、位置、基准、范围／owner 和记录日期；Git commit 记实际 SHA，未提交候选记 HEAD 加获准文件清单及必要内容指纹，不能将 SHA-256 内容摘要称为 Git commit。
- 决定：本批结论、任务进展及理由入口，标明当前采用、保留或未决事项。
- 证据：实际输入、环境、检查、结果、原执行候选身份及限制；区分本轮执行、有效继承和未执行。
- 下一步：未决项、下一动作、责任范围及授权边界。

`status` 仍表示任务进展；候选用途／状态、检查结果、实际合并、发行资格及操作授权分别表达。目录存在、clean／dirty、旧绿色检查或聊天状态都不能单独决定候选是否已采用、已结束或可删除；不用先清理全部旧目录、分类全部分支才能开展已明确的任务。HEAD 和 status 相同也不证明未提交正文相同，按本次依赖的获准文件核对内容即可，不扫描业务数据或凭据。

内容、输入、相关环境及受测契约未变时复用有效证据，注明原执行身份和对当前候选适用的理由；不把继承改称新 head 已执行。运行实现、调用方、规则或相关环境变化时，重新选择受影响检查；有新失败或相关疑点再扩大。实际 PR required 仍须满足其合并候选的门槛，旧本地结果不替代它或维护者对当前修订的审核。新失败和被替代结论追加后继说明，保留原始失败及冻结历史。

### 不同 Agent Harness 共用同一流程

- 仓库约定以 [AGENTS.md](AGENTS.md) 和本文为准。Codex 读取 AGENTS；Claude 的 [CLAUDE.md](CLAUDE.md) 仅以 `@AGENTS.md` 导入同一份内容；其他 Harness 在任务开始显式读取这两份文件。不要为每种工具复制一套开发规则或把机器、凭据及个人设置提交进仓库。
- 需写入的任务使用独立分支；同机并行使用独立 clone 或 Git worktree，安装目录、临时数据和生成物也独立。不要让多个 Harness 共同切换同一个 checkout 的分支或操作同一个 Git index。换工具接续同一任务时，按[候选入口与交接](#候选入口与交接)核对指定候选和四项交接，无需再开重复 PR。
- 团队成员使用自己的 GitHub 身份，一般贡献者先 fork 后提 PR；不共享 Eysn0130 的账号或 token。需要上游写权限时由维护者单独授予，仍遵守 main 保护。Harness、模型和自动审查都不增加成员权限。
- Agent 默认交付到待审 PR，汇报当前 head SHA 和检查结果；没有 Eysn0130 对该版本的明确授权，不自行合并、开启自动合并、打 Tag 或发布。修改了已审版本后，新增差异需要重新审阅；历史同意不覆盖新内容。

### main 门槛与单维护者阶段

main 要求 PR、稳定的 `PR required` 检查、同步最新 main 和解决阻塞讨论，并禁止强推／删除；规则也覆盖管理员。先让检查产生实际成功结果，再将其设为 required，避免配置不存在的检查名。失败应修复或通过修复 PR 处理，不临时关闭保护绕过检查。

[CODEOWNERS](.github/CODEOWNERS) 将审阅路由到真实维护者 `@Eysn0130`。当前仓库仅有该维护身份，而 GitHub 不允许作者批准自己的 PR；因此现在的技术门槛不强制另一账号批准。以 Eysn0130 身份创建的 Agent PR 必须由 Eysn0130 人工查看差异、检查结果后操作合并，Agent 不代替这次审核；GitHub 无法区分同一账号背后的人工与自动操作。

成员开始通过独立身份稳定提交后，由 Eysn0130 启用 **1 个批准＋必须由 Code Owner 批准＋新代码提交撤销旧批准**。届时其他成员的 PR 由 Eysn0130 批准；Eysn0130 自己发起的 PR 则需要另一位有写权限的真实 Code Owner，或改为由独立贡献者提交供其审核。不要制造自我批准、机器人代批或默认双审来满足门槛。审批流程、Code Owner 和凭据访问权限是不同层，AGENTS 不是权限隔离机制。

已合并缺陷通过修复或 revert PR 处理，不重写 main 或移动已发布 Tag。涉及数据库／文件格式时另有兼容与恢复方案，Git revert 不能恢复运行数据。

工具机制依据：[Codex 项目指令](https://learn.chatgpt.com/docs/agent-configuration/agents-md)、[Claude 共用 AGENTS](https://code.claude.com/docs/en/memory#share-one-file-with-other-coding-tools)、[GitHub 作者不能自批](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews)。仓库规则不能识别同一账号背后是谁在操作，因此独立身份和维护者人工审核仍是必要边界。

### 自动 PR 检查

[Pull request checks](.github/workflows/pull-request.yml) 使用只读 token、完整 SHA 固定的 Actions 和合成材料，不读取业务环境、调用 Provider、打包或发布。它在 PR 的合并候选上运行，按 `scripts/ci.py` 中少量目录规则选择：

| 检查组 | 自动验证及边界 |
| --- | --- |
| Python／G6 | 现有合成回归、API 生成契约和固定 G6 校验；Node 也安装在该组以运行旧界面 harness |
| React | 锁定依赖安装、包含类型检查的 build、前端测试；共享 API／契约变更同时验证调用方 |
| Desktop | Windows 上的桌面 Node 检查，不下载 Electron；相关 sidecar／资源变更同时验证 Python。它不是包内或安装后验收 |

纯说明文档免业务测试，仍核对实际差异、引用、路径和必要源码事实；文档下的脚本、配置及样例数据不能冒充纯文档。`AGENTS.md`／`CLAUDE.md` 是执行输入，当前选择器在 `.md` 豁免前识别它们并选择 Python／React／Desktop 三组。共享配置和未知源码路径保守扩大检查，不维护逐测试文件依赖图。独立 `PR required` 始终回报结果：选中的组必须成功，只有明确无关的组可跳过；计划失败、取消或意外跳过均不能通过。Fork PR 使用 `pull_request`，不使用带发布权限的 `pull_request_target` 执行贡献者代码。

检查选择按整个实际交付候选的变化集合判断，包含实际新增文件。`ci.py plan` 读取提交间 diff；未提交候选须另核对实际 tracked／staged／untracked 清单，不能以 `HEAD...HEAD` 空 diff 宣称无需检查。普通说明修正且选择器未变、无新 CI 疑点时，不额外要求本机重跑分类器测试；远端既有 required 门槛仍保留。

本地按下表选择最小充分检查；CI 对所选组执行固定可复现集合，不要求作者本机重复 CI 已有的同候选证据。目录判定及汇总可独立检查：`python -m unittest discover -s tests -p test_ci.py -v`。

## 按影响选择最小充分验证

| 变更范围 | 需要的证据 |
| --- | --- |
| 纯说明文档 | 检查实际差异、引用、路径及必要源码事实；不因文档改动启动全套测试或打包 |
| AGENTS／CLAUDE 等规则及其他执行输入 | 核对保留约束、适用链、命令 cwd 和实际新增文件，再按真实影响及现有 CI 门槛选择检查；不能只按扩展名豁免 |
| Python 业务逻辑 | 通过 `scripts/test.py` 运行受影响模块／用例，例如 `.venv/bin/python scripts/test.py tests/test_cases.py -q` |
| 案件隔离、金额、导入或数据契约 | 对应公共行为回归；覆盖变更涉及的跨案拒绝、精度／币种、失败回滚／恢复及兼容边界 |
| v1 API | 执行 `.venv/bin/python scripts/export_api_contract.py --check`，补调用方受影响回归；仅 schema 有意改变时从 Python 同步生成 `contracts/` 两份输出，不手改生成类型 |
| React／G6 界面 | 相关前端／合成导航回归及受影响用户流程；React 变更执行 `npm --prefix frontend run build`，新环境或锁文件变化先按上文执行 `npm ci` |
| 桌面宿主或分发 | 相关 `npm --prefix desktop test` 检查及受影响用户流程；涉及安装链、安装器／更新包清单或装载契约时补实际安装入口证据，不能用 API 或源码测试替代。纯更新检查提示／状态展示按源码和实际 UI 流程验证，不默认正式打包 |

跨模块修改或共享基础行为变化可能需要完整 `.venv/bin/python scripts/test.py`；按实际影响扩大检查，而非机械叠加。性能变更遵循 [性能口径](docs/performance.md)，不要把正确性烟测当计时结论。记录执行命令、结果及未验证的边界，区分源码、浏览器、包内后端和安装后行为；相关检查通过且无新疑点后停止重复验证。

## 依赖、来源与代码约定

变更 Python 依赖时修改相应输入文件，重新生成对应锁文件，审查版本和哈希差异。开发锁文件的命令为：

```sh
uv --no-config pip compile requirements-dev.in --python-version 3.11 --universal --generate-hashes --default-index https://pypi.org/simple --output-file requirements-dev.lock
uv pip sync requirements-dev.lock
```

Node 依赖同步所属目录的 `package.json` 和 `package-lock.json`，使用 `npm ci` 复现安装。保留适用的社区许可、历史 MIT、原作者和第三方版权与通知；新增或更新外部代码、资源和依赖时核对来源、版本及许可要求，必要时更新 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，不直接替换锁定的 vendor 文件。

源码文件使用 UTF-8；新 Python 模块及新 Python 脚本用 `snake_case.py`，Python 测试用 `test_<behavior>.py`。新 React／TypeScript 和文档命名、既有路径例外及平台／语言惯例统一见[新文件命名与兼容例外](docs/evolution.md#新文件命名与兼容例外)，此处不复制名单。目录按职责组织，不以开发者、修复次数或日期命名功能源码。

## 数据和发布边界

Issue、PR、测试、日志和截图只使用合成数据；不上传案件、真实账户／身份／交易、授权记录、凭据或签名材料。公开报告用合成复现和必要环境信息，不粘贴原始业务日志。不要读取原 `fund-tracker` 业务目录，不在普通测试中访问真实数据、现有服务或真实签名配置，也不要运行历史修补、恢复或终止进程脚本。

桌面版本由 Eysn0130 决定发布时间并按当前授权执行：可积累多个已合并 PR，再选定不可变候选 SHA，手动运行 [Windows Actions](.github/workflows/windows-candidate.yml)，核对该 SHA 的版本、安装包、清单、哈希与检查结果后发布。每个 `v0.1.x` Tag 指向对应候选并有附带 Windows x64 `.exe` 的 Release；不移动旧 Tag、不以旧候选的绿色检查证明新候选。失败时保留现有发布版本，修复后重新选择候选。具体策略及未验收边界以 [桌面交付](docs/desktop-delivery.md) 为准；普通 PR 不要求封装最新版。Git 提交、push、打包和外部写入仍遵守当前任务授权。

### 发行说明

GitHub Release 面向下载安装的用户，采用类似 App Store 的简短更新说明：可先用一句话概括，再列 2–5 条本版实际新增、改善或修复的内容，每条一句。直接说明操作场景与结果，不强凑分类、不写未经验证的承诺。只有一项变化时写一条即可。

CI 数量、候选 SHA、实现细节、完整哈希和平台验收记录放在 PR、独立验收文档或 Release 附件中；公开简介只保留必要的兼容、安装或预览版提示。例如：

```text
本次更新改善了图谱标签显示，并加入应用内更新功能。

- 新增应用内检查更新，支持下载更新并确认重启安装。
- 修复缩放或将节点移出画布再拖回后，节点标签消失、只能悬停查看的问题。
- 修复节点移动或阴影变化后，标签被错误隐藏的问题。

Windows x64 未签名预览版；v0.1.7 及更早版本请手动下载安装。
```

以 GitHub 最新稳定版的 Release 正文为更新说明的唯一来源，analytix.top 下载中心从同一份 latest 记录展示版本和说明。发布新版或修正当前版说明后，都要核对同步工作流和公开页面；同步失败时准确说明 GitHub 已发布与国内入口未完成的区别。更新说明可以修正，已发布安装包、版本清单和校验身份仍不可变。流程见 [国内下载](docs/domestic-downloads.md)。
