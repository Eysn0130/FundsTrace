# 品牌与开源展示 · 交付核验

本记录对应 `codex/brand-showcase` 的 README、品牌资源、合成样例和真实演示。工作台运行代码基于 v0.1.1 发布源码 `e7be2f5675dcd2dc896a20d6d644d5aa05125a73`，叠加本支线品牌资源；发布文档已对照 `origin/main` 的 `9e900d5` 更新。本文不把品牌源码就绪视为新安装包已发布。

## 可查看的成果

| 内容 | 入口 |
| --- | --- |
| 产品介绍与使用入口 | [README](../README.md) |
| 原生品牌源、深浅字标、PNG 与 ICO | [品牌目录](brand/README.md) |
| 真实工作台主视觉 | [1600 × 1000 PNG](media/workspace-hero.png) |
| 操作预览与完整演示 | [12 秒 GIF 节选](media/fundstrace-preview.gif)、[31 秒 MP4 下载](media/fundstrace-demo.mp4?raw=1) |
| 分享封面 | [PNG](media/fundstrace-social.png)、[可复用 SVG](media/fundstrace-social.svg) |
| 可下载样例及复现步骤 | [云杉项目](examples/harbor-path/README.md) |
| 实际导出 | [Excel](examples/harbor-path/exports/tracked-flows.xlsx)、[图谱 PNG](examples/harbor-path/exports/traced-graph.png) |
| 文件核对 | [verify.py](examples/harbor-path/verify.py)、[结果与 SHA-256](examples/harbor-path/verification.json) |

## 实际操作证据

使用本支线 Python 3.11.15、Node 22.22.1，在 macOS arm64 上启动 `scripts/preview_synthetic.py`。入口创建一次性数据根、随机 loopback 端口；没有连接主工作树服务或真实业务数据。通过隔离 Chrome 上下文、正常 UI 输入与点击操作，未调用种子接口代替演示导入。

| 操作 | 实际观察 |
| --- | --- |
| CSV 导入 | 自动识别单账户流水；12 行可导入，跳过 0；完成后 12 笔／7 个账户 |
| XLSX 导入 | 在另一个专用案件导入同内容 XLSX；相同预览和完成数。短片记录此流程；仅选星桥商贸为主账号 |
| 锚点追踪 | 关键词 `SYN-PATH-001` 得到 1 笔；去向追踪后，锚点及后续 4 笔共 5 笔上图 |
| 完整样例上图 | 重置查询，流水全览“本页全部上图”；12/12 笔，图谱显示 7 个账户／8 条有向关系 |
| 整理与保存 | 使用已有层次布局、全部标签、金额显示、节点拖动与查询折叠；保存具名画布“分流与归集 · 合成路径” |
| Excel 导出 | 等待真实浏览器 download 并保存；文件无下载失败。含“追踪流水”12 行与“待调取账号”工作表 |
| 图谱 PNG | 从正常导出入口选择 PNG，等待 download 并落盘；文件可解码，画面与当前全图相符 |
| 导出事实核对 | CSV 与 XLSX 输入逐格相同；导出按流水号逐笔核对日期、付款／收款户名、账号、金额字符串、币种、摘要，全部通过 |

金额逐笔合计 `1,832,503.00 CNY` 是跨环节流水相加，包含多次转手；不是项目资金规模或净流入。样例、截图和导出均保留完整账号与分位金额。运行时案件／画布 ID 仅为临时环境标识。

## 媒体真实性与处理

- [工作台主图](media/workspace-hero.png)、导入预览、锚点结果和流水全览均为实际浏览器视口截图；未重绘控件、节点、边或内部状态。
- MP4 来自同一次真实交互录制，保留“导入—追踪—整理—导出”。剪去操作间空等候，操作片段以 0.8 倍速呈现以便阅读，末尾停留主图。产品画面外增加阶段字幕；没有旁白或伪造的系统保存窗口。时长约 31.32 秒，1600 × 1100、25fps、H.264，无音轨。
- 已逐段解码、检查连续操作与关键帧；画面只含本应用及合成内容，没有凭据、真实账号、用户桌面、原业务路径或文件选择器的系统路径。
- 分享封面使用真实主图的图谱区域裁切及外部品牌排版。SVG 内嵌的是该裁切截图；标识与字标仍为原生矢量。未用 AI 生成产品画面。
- README 媒体与品牌资产使用仓库相对路径。MP4 约 2.7 MiB、主图约 281 KiB。真实 GitHub 分支检查发现 MP4 文件页只显示二进制下载，因此 README 使用可展开的 12 秒 GIF 节选（约 1.4 MiB、960 × 660、36 帧），并提供带 `?raw=1` 的完整 MP4 下载；没有依赖 GitHub 不支持的内嵌播放器。GIF 取自四段实际操作，明确标为节选。

## GitHub 兼容与视觉复核

README 仅使用 Markdown、`picture/source/img`、`details/summary` 和简短 `sub`；没有脚本、动画 SVG、嵌入 iframe 或自定义 CSS。图标、品牌板与正式字标 SVG 均无脚本或外部字体依赖；README 字标文字已转轮廓。

已调用 GitHub Markdown API 以 GFM 和本仓库 context 渲染当前 README，确认主题图片、相对图片、链接和结构被保留。在同日 GitHub 仓库页面的原生 README 容器和样式内装入该渲染结果，模拟深／浅主题与 1440px／390px 视口；仅替换本地候选图片源，未发布到远端。主题检查显式选择对应的字标源，避免隔离预览中 GitHub 页面原先主题组件的缓存状态。

| 视口 | 内容宽度／滚动宽度 | 结果 |
| --- | --- | --- |
| 1440px 深／浅 | 838px / 838px | 品牌、入口、主视觉和字幕可见；图片加载成功 |
| 390px 深／浅 | 324px / 324px | 页面宽度 390px，无整体横向溢出；三个主入口同排，无落单字 |

已保留四份 README 兼容预览及真实分支截图于本任务交付目录。提交推送后另查 [真实 GitHub 分支](https://github.com/Eysn0130/FundsTrace/tree/codex/brand-showcase) 的图片加载与演示入口，PR 为 [#2](https://github.com/Eysn0130/FundsTrace/pull/2)。本轮只创建 PR，未合并 main；兼容预览与真实远端截图分开标记。窄屏主图保留整体路径，可通过原图链接检查金额与账号；没有为缩略图隐藏数据或虚构移动端产品适配。

[GitHub README 官方说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes) 用于相对链接与文档分层核对。[Excalidraw](https://github.com/excalidraw/excalidraw) 和 [Penpot](https://github.com/penpot/penpot) 仅参考信息层级，没有复制其资产或文案。

## 资源、许可与适用检查

- `npm --prefix frontend ci` 与 `npm --prefix frontend run build` 通过，使用锁定依赖构建合成预览；没有修改前端源码、依赖或锁文件。
- `npm --prefix desktop ci` 后 `npm --prefix desktop test`：19 通过，3 项 Windows 专属检查在 macOS 跳过，0 失败。初次缺依赖的检查没有作为通过证据；安装本支线锁定依赖后才完成上述检查。
- electron-builder 26.15.3 实际读取配置并通过 schema 校验；`win.icon` 指向本支线 ICO。桌面配置只改该字段，产品名、appId、版本、数据根和安装生命周期没有改变。
- 品牌与静态标识共 12 份 SVG 可解析；正式资产没有脚本、外链、事件或字体加载依赖。8 档图标 PNG 为 RGBA，尺寸正确；ICO 含 16–256px 六档，逐档可解码。静态应用 SVG 和桌面 ICO 与品牌源一致。
- 字体 Manrope 与 Noto Sans SC 使用固定来源及 SHA-256，完整 SIL OFL 1.1 通知保留在品牌目录；图形和排版源随项目 MIT 提供。见 [品牌说明](brand/README.md) 与 [第三方通知](../THIRD_PARTY_NOTICES.md)。
- 已核对 136 个相对路径／锚点，以及公开下载链接和候选差异；按本轮影响执行检查，没有因纯说明文档重新运行全仓业务回归。

## 公开包与后续集成

已核实 [公开 v0.1.1 Release](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.1)、源码 Tag 和 [成功 Windows Actions](https://github.com/Eysn0130/FundsTrace/actions/runs/38827843574)。公开 EXE 为 `FundsTrace-0.1.1-windows-x64-unsigned.exe`，168336906 字节，SHA-256 `81f0e720dc35fa7ed902388cbbd17208881425d6828ae395195135cfcdb35b8d`；详细已发布证据来自该版本的交付记录。

**本支线没有重打包或替换它。** 新 Logo、favicon、ICO 及其配置只在源码候选中就绪。后续安装包需要独立构建并检查 EXE、安装器、任务栏及系统缩放下的图标表现。当前合成浏览器与资产检查不覆盖 Windows GUI、DPI、原生保存取消、安装中正常关窗或活动导入恢复；这些边界继续以 [桌面交付](desktop-delivery.md) 为准。
