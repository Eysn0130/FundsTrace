# FundsTrace 渐进演进建议

本建议基于源码与公开一手资料；未读取业务数据。**先稳定资金数据契约、拆分职责和实测，再选择新技术。** 文件行数只能提示维护成本，不能证明 Python 是瓶颈。

第二阶段已建立 v1 API、生成契约、合成性能基线及 React 案件／导入／核对试点；具体证据与限制见 [phase-two.md](phase-two.md)、[performance.md](performance.md)。下文的更大采样、图表联动、导出与宿主接入仍是后续验收目标；没有 Rust／Polars 重写，也没有接入 Analytix。未来整合路线见 [analytix-integration.md](analytix-integration.md)。

## 命名与目录

保留仓库名 `funds-trace`，产品名统一为 **FundsTrace／资金流追踪**，描述为“本地资金流水追踪与案件分析工具”。相比 `fund-tracker`，它更明确表达流水追踪，减少与基金投资跟踪产品混淆。若以后需要独立品牌，可另行评估 `FlowTrace`；本次未核验商标、域名、应用商店或同名项目，不据此宣称名称可独占。原 `fund-tracker` 仅作受保护来源，不作为开发工作目录。

当前保持可运行入口，按真实职责逐步迁移；下列目标目录是路线，不要求一次创建所有空层级：

```text
当前                         渐进目标
run.py                       run.py
app/main.py                  app/main.py          # 组装应用与路由
app/analysis_routes.py       app/api/             # 路由与请求/响应契约
app/importer.py              app/services/        # 导入、查询、导出用例
app/analytics.py             app/analytics.py     # 先保留，按热点再拆
app/db.py                    app/storage/         # 连接、事务、查询
app/report.py               app/report.py        # 先保留兼容入口
app/paths.py                 app/paths.py
static/{index.html,          static/              # 迁移期间继续维护
        app.js,style.css}    frontend/src/
static/analysis.*              features/{cases,imports,transactions,graph,exports}/
static/vendor/g6.min.js        components/、api/、styles/
tests/、scripts/             tests/、scripts/、docs/
                             crates/              # 仅 Rust 试验通过后增加
```

先从 `main.py` 提取一个完整业务用例及必要调用方，避免只按行数切文件。数据库连接与事务仍由统一边界管理；UI 分组不复制数据语义。Python 模块用 `snake_case.py`、回归用 `test_<behavior>.py`；React 组件用 `PascalCase.tsx`，工具和功能文件用 `kebab-case.ts`，文档用 `kebab-case.md`，Rust crate 用 `snake_case`。避免 `utils2`、`final-new`、`patch_*`。按用途分离源码、合成样例、测试夹具、生成物和本地运行数据；旧文件名、环境变量及 `.fundcase` 格式保留到有兼容迁移证据时再调整。

## 数据执行：先 SQL，再试 Polars，最后考虑 Rust

DuckDB 已是原生执行引擎。过滤、分组、窗口和关联优先放在 SQL；导入可先把标准化后的列式数据注册为表，再 `INSERT … SELECT` 批量落库，并保持一次导入的事务与回滚。Excel 的格式识别及字段映射可继续留在 Python。官方明确建议避免逐行插入；把循环搬到 Rust 后仍逐行调用数据库，未必解决原瓶颈。[DuckDB 导入建议](https://duckdb.org/docs/current/guides/performance/import)

Polars 适合需要列式字符串清洗、多步转换的候选路径，可先试其 Python API；lazy 查询支持整体优化，逐元素 Python UDF 仍有调用成本。成本包括另一套表达式、类型转换、依赖体积及重复物化，不能默认替换 pandas 或与 DuckDB 重复计算。[Polars lazy](https://docs.pola.rs/user-guide/lazy/using/)、[UDF 边界](https://docs.pola.rs/user-guide/expressions/user-defined-python-functions/)

Rust/PyO3 只用于剖析确认的 CPU 热点，例如无法合理表达成 SQL 的批量匹配；按数组或批次跨边界，避免每行往返。收益来自具体算法、内存与并行控制；代价是数据转换、错误映射、ABI、跨平台 wheel 与构建链。PyO3 可释放 Python 解释器执行约束，但不会自动消除 I/O、SQL 或前端瓶颈。[PyO3 并行](https://pyo3.rs/main/parallelism)、[分发](https://pyo3.rs/main/building-and-distribution)

建立仅含合成数据的基准：固定硬件、依赖、随机种子，覆盖 1万／10万／100万行及宽表、重复流水、缺失值、中文与异常日期；分别记录解析、清洗、写库、查询、图谱准备和导出，不能只报内部函数耗时。冷启动至少 10 次、预热后至少 30 次，记录中位数、P95、峰值 RSS、磁盘与转换成本。对拍行数、定点金额、方向、日期、原始行来源、聚合口径和失败回滚；账户编号不得数值化丢前导零。

**以下均为建议决策阈值，尚未实测，也不是承诺：**交互查询 P95 持续超过 2秒或导入超过 30秒时先剖析；SQL／批量路径优化后，同一 CPU 热点仍占端到端耗时至少 30%，才做 Rust 小样。小样须在两档以上代表规模让端到端 P95 改善至少 25%，峰值内存不高于基线 10%，全部资金契约与回滚检查通过，且目标系统可离线安装，才考虑合入。Polars 使用同一对拍与端到端收益门槛。阈值应随真实工作规模和用户容忍时间校准。

## 前端与图谱选择

React＋TypeScript＋Vite 的价值是组件、类型和状态管理；视觉质量仍依赖信息层级与交互设计。先试一条“案件→导入→表格核对”完整路径，可挂在独立区域或子路径；其余继续使用 `static/`。单个区域只设一个状态所有者。Vite 构建之外还需 `tsc --noEmit`；API 金额建议用十进制字符串，业务校验仍由服务端负责。[React 增量接入](https://react.dev/learn/add-react-to-an-existing-project)、[Vite 类型检查](https://vite.dev/guide/features.html#typescript)

| 图谱方案 | 适用边界与本项目选择 |
| --- | --- |
| G6 | 继续承载资金关系探索；React 包装层负责创建、更新与销毁实例。升级 G6 主版本与 React 迁移分别验证。[官方集成](https://g6.antv.antgroup.com/en/manual/getting-started/integration/react) |
| React Flow | 当产品需要节点表单、连线编辑和工作流编排时评估；现有资金关系图无需因此更换。[项目文档](https://reactflow.dev/learn) |
| AntV X6 | SVG／HTML 节点、吸附等图编辑能力成为明确需求时评估。[项目文档](https://x6.antv.antgroup.com/en/tutorial/getting-started) |
| Cytoscape.js | 图论算法、选择器及关系网络分析成为主要需求时对比；须另测布局、交互和导出。[项目文档](https://js.cytoscape.org/) |

不按演示页或未经复现的节点数量排行。用相同合成图规模测布局、首屏、缩放、选中、筛选及导出；图上合并显示不可改变原始流水和统计口径。只有明确需要桌面安装、系统文件关联、窗口与权限能力时，再评估 [Tauri](https://v2.tauri.app/start/)；它会增加系统 WebView 差异、安装升级与原生权限验收，不是数据加速步骤。

## UI／UX 与开源参考

优先完成可核查的工作流：固定案件上下文；导入前预览映射、导入后显示行数及异常；表格与图谱选择联动并可返回来源流水；过滤条件始终可见；导出明确当前范围、口径与文件内容。金额右对齐，颜色同时配文字或形状，支持键盘焦点、空态、错误恢复和长任务进度；高密度数据界面减少装饰性卡片。

本轮仅用合成数据做了浏览器冒烟：上传、金额预览、导入、上图与案件切换可观察。1280px 截图中顶栏与图谱工具条明显拥挤、部分按钮逐字换行，后续 UI 试点应先重排案件上下文、导入/导出动作及图谱工具分组，再调整视觉主题。这不是完整 UX、可访问性或真实数据验收。

可参考三个原始项目，实施时选一套主要视觉基础，避免并装多套完整组件库：

- [Ant Design Table](https://ant.design/components/table/)：借鉴筛选、固定列、分页与异常信息组织，适合密集表单和数据表；主题仍需结合本项目设计。
- [shadcn/ui](https://ui.shadcn.com/docs)：适合自有品牌和可修改组件；复制进仓库的代码及后续更新由本项目维护。
- [Radix Primitives](https://www.radix-ui.com/primitives/docs/overview/introduction)：可渐进复用对话框、菜单的焦点与键盘机制；无样式基础不等于完整产品设计。

三者已核对上游 [Ant Design MIT](https://github.com/ant-design/ant-design/blob/master/LICENSE)、[shadcn MIT](https://github.com/shadcn-ui/ui/blob/main/LICENSE.md)、[Radix MIT](https://github.com/radix-ui/primitives/blob/main/LICENSE)。灵感参考与代码复制分别记账；复制实质代码须保留版权和许可，图片、图标、字体及第三方模板另查。公开仓库可见不代表当前项目整体可公开；现有 vendored G6 的版本与来源仍需独立核验，不能由上游许可证替代。

## 阶段与验收

| 阶段 | 最小交付与停止条件 |
| --- | --- |
| 1 稳定基础 | 合成数据、案件隔离、金额与方向、导入失败回滚、`.fundcase` 往返检查通过；入口与旧格式可用。 |
| 2 模块与基准 | 提取首个业务用例，API 契约保持；建立上述基线，识别真实热点后停止结构扩张。 |
| 3 UI 试点 | 导入核对、图谱追踪、导出回读三条任务全部完成；键盘、错误恢复、来源追溯、表格图谱联动和 HTML／Excel／案件包导出通过浏览器检查；任务耗时不劣于旧版，再逐页替换。 |
| 4 数据试验 | 仅在阈值触发时选择 Polars 或单个 Rust 内核；对拍、收益与离线安装同时通过，否则保留现状。 |
| 5 可选桌面 | 有明确分发需求后验证目标系统安装、启动、升级、数据路径、权限和卸载保留策略；本次不做正式打包。 |

GitHub 实践先做小而完整：合成夹具、锁定依赖、可复现命令、每次变更的回归与兼容说明、可审查的小 PR；记录所引依赖版本、来源与许可证。CI、截图、问题报告和公开检索只使用合成材料，真实账户、身份、交易、数据库、授权记录及签名材料留在受保护的本地边界。公开发布、许可证选择和正式发行另行授权。
