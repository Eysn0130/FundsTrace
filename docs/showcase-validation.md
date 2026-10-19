# 主页展示与素材核验

本轮在 `codex/homepage-showcase` 隔离工作树制作，仅修改 README、展示素材、合成样例及其说明。运行界面来自已合并 v0.1.5 源码 `ddc9ef0305e9bbfedd8329a84538ce36b2af32c8`，采集日期为 2026-10-18（America/Los_Angeles）。没有修改业务、图谱渲染、桌面身份或发布配置。

## 设计与内容

主页依次回答：是什么 → 看得到的结果 → 三步怎么做 → 两段实际操作 → 本机架构 → 下载与文档。保留现有 FundsTrace 标识，统一纸白、石墨与玉绿；用真实界面作主视觉，用原生矢量示意图解释业务和架构。

研究参考了 [Linear](https://linear.app/) 的定位与产品界面顺序、[PowerToys README](https://github.com/microsoft/PowerToys#readme) 的简短导航与渐进展开、[DuckDB README](https://github.com/duckdb/duckdb#readme) 的最小可用示例。仅参考信息组织，没有复制其品牌、图片或宣传能力。GitHub 内容遵循[官方 README 说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes)。

## 当前素材

| 内容 | 入口与规格 |
| --- | --- |
| 产品主视觉 | [展示版 PNG](media/showcase/hero.png)，1600 × 1180；[原始工作台](media/showcase/workspace-focus.png)，1600 × 1000 |
| 业务逻辑 | [SVG](media/fundstrace-workflow.svg)／[2× PNG](media/fundstrace-workflow.png)，导入核对 → 分析追踪 → 整理导出 |
| 本机架构 | [SVG](media/fundstrace-architecture.svg)／[2× PNG](media/fundstrace-architecture.png)，界面、所属后端与两类存储 |
| 数据导入 GIF | [11.4 秒 / 4 个步骤](media/showcase/import-demo.gif)，1200 × 886，252,535 B |
| 数据分析 GIF | [9 秒 / 3 个步骤](media/showcase/analysis-demo.gif)，1200 × 886，170,319 B |
| 静态替代 | [导入封面](media/showcase/import-poster.png)／[分析封面](media/showcase/analysis-poster.png)，与相应 GIF 首帧一致 |
| 可复现样例与实际导出 | [样例说明](examples/harbor-path/README.md)、[CSV](examples/harbor-path/fundstrace-synthetic.csv)、[XLSX](examples/harbor-path/fundstrace-synthetic.xlsx)、[图谱](examples/harbor-path/exports/traced-graph.png)、[追踪流水](examples/harbor-path/exports/tracked-flows.xlsx)、[分析报告](examples/harbor-path/exports/analysis-report.html) |

GIF 直接由本次真实 PNG 截图编码，是带阶段字幕的步骤演示，不称连续录屏；没有 MP4 输入、转换或新视频产物。仅对截图等比缩放，字幕、边框与进度放在产品画面之外，没有重画控件、节点或内部状态。GIF 调色压缩不改变业务内容。README 不再引用旧视频。

业务与架构图是逻辑示意，文字已转路径，不含脚本、外部字体或远程资源。架构明确 React 为案件／导入／核对试点，G6 仍承载图谱；没有把 Rust、AI、云端或其他宿主接入画成已实现能力。生成入口见[品牌说明](brand/README.md#主页业务展示图)。

## 本次实际操作

在 macOS arm64、Python 3.11、Node 22.22.1 与隔离 Chrome 上下文中运行 `scripts/preview_synthetic.py`，仅使用它创建的临时数据库与公开合成样例，不访问原业务目录或已有服务。

| 行为 | 本次观察 |
| --- | --- |
| CSV 导入 | 新建“合成演示 · 云杉项目”，正常选择文件与确认映射；新增 12 笔、重复 0、跳过 0，共 7 个账户 |
| XLSX 导入 | 在独立空白合成案件正常选择文件；可导入 12 行，完成新增 12 笔、重复 0、跳过 0；最终 GIF 的四帧来自同一“导入核对”案件 |
| 主账号 | 两种格式均只保留星桥商贸 `SYN-B001`，其余主账号取消 |
| 锚点追踪 | 查询 `SYN-PATH-001` 得到 1 笔，按去向、最近 5 笔追踪后，5 笔上图 |
| 完整样例与画布 | 流水全览“本页全部上图”后 12/12；图谱 7 个账户、8 条关系。选择资金流向分层、全部标签，保存“分流与归集 · 合成路径”，收起查询区采集主视觉 |
| 全案分析 | 仅选路径追踪，`SYN-A001 → SYN-F001`、最大 3 跳，得到 6 条路径 |
| 实际导出 | 从界面下载 PNG、Excel 与重新计算的 HTML 分析报告，落盘后检查，不以点击按钮代替下载完成 |
| 事实核对 | 运行现有 `docs/examples/harbor-path/verify.py`：CSV/XLSX 逐格相同；导出 12 笔的时间、完整账号、金额字符串、币种与摘要逐笔一致 |

6 条路径的独立解释：青禾支路的两笔入边 × 两笔出边形成 4 种组合，其余两支各 1 种。路径算法是交易边的有界拓扑组合；金额按边累加，非去重本金，也不保证交易时间递增。截图保留页面上的金额口径与“需人工复核”。全部名称、账号、交易和业务背景均虚构。

## 渲染与检查

README 使用 GitHub 原生 Markdown、`picture/source/img`、`details/summary` 与简短说明；没有自定义 CSS、脚本或内嵌播放器。GitHub Markdown API 已保留深浅字标与 `prefers-reduced-motion` 静态图片来源；静态封面亦有普通链接。

本地预览只用于检查候选内容。它使用 GitHub Markdown API 的返回 HTML 和近似 GitHub 的本地排版，不是已经更新的 GitHub 主页。

已执行：

- 前端可重复安装与 TypeScript／Vite 构建通过；用于启动本轮合成预览，不代表新增业务改动。
- 实际查看 1440px 桌面、390px 窄屏的深／浅主题；页面宽度与 scrollWidth 相同，六张主图均成功解码。
- 浏览器启用减少动态效果后，两个 GIF 的 currentSrc 均变为对应 PNG；深浅字标也按主题切换。
- 113 个相对文件引用存在；主页 MP4 引用为 0。两张 SVG 可解析、带 title／desc、无脚本／外部位图／字体请求。
- 两段 GIF 逐帧解码通过，帧数、时长及尺寸与上表一致；静态封面与首帧像素一致。已查看各步骤原始画面与渲染结果。
- 合成输入／当前 Excel 导出逐笔校验通过；Node 生成脚本语法和 git diff --check 通过。当前候选仅涉及文档与展示素材，未重复运行全仓业务回归。

本地检查不能代替合并后 GitHub 页面的实际展示与缓存行为。本节记录候选素材的本地验证；提交与合并状态以关联 PR 和 main 为准。

## 证据与交付边界

这些素材证明所述合成界面路径和导出结果，不代替安装包、Windows 实机 DPI、真实案件性能或完整可访问性验收。下载入口指向真实 Releases；未签名预览及各环境已执行范围见[桌面交付](desktop-delivery.md)。案件包不含原始上传、导入任务或回执。

历史 v0.1.1 展示资源与 PR #2 已合并的记录仍属于历史；本轮不再使用旧视频、旧 GIF 或旧截图作为当前主页演示。本轮没有重打包、修改 Release 资产或宣称新版安装包已包含本次文档素材。
