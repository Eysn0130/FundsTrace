# 输入检索与候选浮层验收 · 2026-10-22

final result: passed

结论限定于本次源码候选、macOS/IAB、合成数据库与下述检查。未合并main、未打包或发布；Windows／真实案件／原生输入法与触屏另行验收。旧章节保留各自历史范围。

## 已确认方案与实际检查

采用用户选择的1／2／4：宽且能放下为同行，窄框／内容放不下为分行，实际账号命中为号码优先。候选没有表头、列网格、操作栏、教学说明或匹配评分；同名按账号分别呈现完整号码与已存证件。真实流水七列表格和管理结果表保留。完整入口矩阵、检索语义及后续边界见 [输入检索说明](docs/input-search.md)。

本机证据目录：`/Users/sun/.codex/visualizations/2026/10/23/01a16667-825d-7fe1-a515-c6e72595b3d4/production-qa/`。设计真值为上级`design-options/selected-responsive.png`；已与实现的宽框、窄框、号码优先截图一起查看对照。截图只用合成节点、账户及SYN-ID占位身份。

- `01-light-narrow.png`、`02-light-wide-graph.png`：真实浅色查询区及图内同行候选。小输入框仍至少240CSS像素，完整账号不截尾；实际内容超宽时允许增加行高。
- `03-modal-candidates.png`：候选DOM仍位于原生dialog内，`:popover-open`为true，浮在管理内容上。打开管理页立即输入时，候选不再被初始结果读取禁用。
- `04-account-first.png`、`07-dark-account.png`：真实React账户页；账号命中采用number布局，完整账号在前，姓名与证件其次。深色浮层背景`rgb(27,37,48)`、文字`rgb(232,238,246)`，持续可见滚动条占9CSS像素。主题来自已有外观系统，不另写主题。
- `06-analysis-fill-only.png`：选择起点填入`000SYN-SEARCH-00000000`，结果仍为「尚未运行分析」，未点击运行或写入数据。
- `latency-100k.json`：绑定源码SHA-256、合成规模及串行HTTP采样。360×680CSS视口另作DOM边界检查：候选左66、右306、上218.15、下578.15，位于视口内；截图合成器在尺寸覆盖时缩放不一致，故不把该截图当作手机或像素验收证据。临时尺寸覆盖已恢复。

实际滚轮跨候选页、反向阅览、滚动条拖动均未执行查询或改变输入：一次拖动后scrollTop为1201，随后滚轮推进到1659且scrollHeight增长到3719，选项DOM为16个，浮层保持打开。图内候选滚轮后父菜单仍开、scrollTop为632。第一次Escape只收候选，第二次关闭父层。Shift+Enter将「合成张」补成「合成张三丰」但查询总数仍100039、精确绑定为空；React账号同样只补到完整原值，不保存资料；原生Command+Z实测恢复补全前的账号前缀。浅色／深色／跟随系统均经现有设置入口实际切换，最终预览保留浅色。两页本轮记录中未见浏览器error。

## 本次发现与修复

- 逐键走完整registry会做count和资料读取：新增按案件／来源有界候选接口，空查询直接返回，正式查询仍独立执行完整结果。
- 父菜单capture阶段抢先处理Escape：由嵌套combobox先处理；保持候选在父表单内避免外部指针／滚动误关。
- 长候选DOM增长及行高变化：虚拟行、测量高度、滚动锚点与续页；3030条已加载候选的行为检查中option DOM不超过71。
- 系统覆盖式滚动条无持续位置反馈：Chromium改为持续可见9px细滚动条，并实际拖动验证。
- 精确选择0001时图中仅10001会模糊回退、大小写不同账号可能串对象：精确定位独立按原字符串查找；自由图内查找也优先原字符串，仅当忽略大小写后唯一时才定位，歧义时保留多候选。缺席／隐藏不改变视图或追踪事实。查询空结果也不移动镜头。
- 主账号查询忙态撤候选会丢精确选择：独立绑定完整账号，连续提交／分页保持精确，用户编辑才解除；管理查询取消自动定位。
- token光标切换与数据刷新：绑定词段起止和案件版本，切词段／A–B–A／同案身份更新立即撤销旧候选；已聚焦框点击可重新打开。
- 候选支持证件但正式查询不支持：统一账户、交易对手与管理查询的案件内证件关联；主账号只提供有流水的可管理对象，身份独有记录不伪装可添加。
- 源码正常但包内缺资源／分析SVG错位：补四个资源allowlist与分析页输入样式；未执行正式打包。

本次定义范围内没有未关闭P0／P1／P2；下述未验证范围不能据此升级为发行证明。

## 查询反馈采样

本地macOS arm64、loopback、100000个未上图候选／100039笔流水／40个图内节点；每形状1次首请求＋8次重复，采样时没有并发构建／测试。单次首请求不是冷盘基线，最大值不是P95。

| HTTP读取 | 重复中位数ms | 重复最大ms |
| --- | ---: | ---: |
| 姓名前缀首候选页 | 31.43 | 33.29 |
| 同查询offset3000 | 33.84 | 34.74 |
| 账号前缀 | 24.58 | 25.60 |
| 证件精确 | 22.50 | 23.63 |
| 无匹配 | 21.66 | 22.01 |
| 正式entity查询第一页／返回总数100000 | 24.81 | 25.34 |

输入候选另有140ms合并等待，以上不是按键到绘制时间，也不是Windows／真实案件性能。取消fetch不保证终止已经运行的DuckDB语句；百万规模、写入竞争、慢磁盘仍需单独采样。

## 检查

- `.venv/bin/python scripts/test.py`：1054 passed，1条既有Starlette/httpx弃用提示；包括公共API、隔离、原有G6几何与金额回归。收尾的图内原字符串优先匹配另经37项finder行为、图谱互动与公共定位定向回归通过；GitHub整套检查绑定PR最终head。
- Node行为harness覆盖虚拟列表、分页失败重试、字面查询、IME合成事件、原生Tab、两级Escape、完整字符串、迟到请求、精确定位与输入／执行分离。真实系统输入法与屏幕阅读器未实机验证。
- Node22.22.1；前端锁定安装、包含TypeScript的build通过，11个测试文件／150项通过。仅有原有运行时静态资源由FastAPI提供的Vite提示。
- 桌面锁定依赖安装（禁用安装脚本，不下载或启动Electron）后，`npm --prefix desktop test`：180 passed、7 skipped。资源allowlist检查通过；不是Windows包内／安装后GUI验证。
- API生成契约、G6固定补丁校验与`git diff --check`通过；没有v1或持久化schema变更。

未执行：Windows GUI／多DPI、真实输入法候选窗、触屏／屏幕阅读器实机、百万数据、冷磁盘／写入竞争、长时间内存采样、安装器及发布。主工作区的既有未提交内容保留，本候选来自独立origin/main worktree。

---

# FundsTrace 四 Tab 功能区交付验收 · 2026-10-16

final result: passed

结论限于本次源码与隔离合成浏览器流程。未重新打包或发布Windows安装器；下方2026-10-15记录保留其原有候选与发布范围。

## 本次视觉真值与实现证据

参考目录：`/Users/sun/.codex/visualizations/2026/10/16/01a14365-027f-7103-8bf6-201523764270/fundstrace-ribbon/`，两张确认图为 `expanded-tabs.png`（1586×992）与 `collapsed-toolbar.png`（1981×240）。它们是功能区设计图，包含示意内容，不是交易事实。

实现目录：`/Users/sun/.codex/visualizations/2026/10/16/01a14365-027f-7103-8bf6-201523764270/fundstrace-ribbon-delivery/`。

- `expanded-common.jpg`、`expanded-trace.jpg`、`expanded-style.jpg`、`expanded-view.jpg`：四个实际Tab各1440×204；`expanded-tabs-implemented.png`为1440×684集合，实际运行一次仅显示一栏。
- `collapsed-toolbar.jpg`：实际折叠态1440×122；`expanded-workspace.jpg`、`collapsed-workspace.jpg`为1440×960完整合成界面。
- `comparison-tabs.png`、`comparison-expanded.png`、`comparison-collapsed.png`：已把参考与实现放入同一图比较。去除设计板标题和编号，参考按宽1440等比归一化；四Tab参考集合1440×682，实现1440×684。折叠参考归一化1440×174，实现1440×122。
- `comparison-focus.png`：1376×185的原尺度局部同图比较，补查Tab、字体、SVG、分隔、按钮标签与禁用态。全景工具回传会缩小，不能据此声称逐像素一致。
- `narrow-850.jpg`：默认850×777窗口；`html-export.jpg`：实际自包含HTML成品1280×720。
- `verification.json`：运行源码文件摘要、尺寸与合成结果摘要；仅包含源码元数据。

IAB/macOS截图CSS视口1440×960、devicePixelRatio=1，PNG/JPEG像素与CSS尺寸一致。临时视口覆盖在交付前恢复。850×777为实际浏览器视口检查，不作为Windows DPI或触屏证据。

## 本次 Findings 与修复复验

| 级别 | 发现与原因 | 修复与后续证据 |
| --- | --- | --- |
| P1 | 折叠时只隐藏四个面板，112px高的父容器仍占位；快捷带从y168开始覆盖主界面 | 父容器一起hidden/inert；最终快捷带y56，工具区66px，图谱y122／高838。`collapsed-workspace.jpg`及容器往返回归通过 |
| P1 | 展开内嵌文字、关系与显示控件仍使用dialog语义，会阻止图谱选择与拖动 | 内嵌时group，折叠弹层dialog；实测样式Tab与视图Tab跨栏Command多选2个节点，整体拖动24／−16屏幕像素，两节点同移、只增1步历史、相机与17笔上图不变 |
| P2 | 850px顶栏右侧案件操作裁切，展开入口随快捷带横滚离开初始视口 | 案件操作转具名图标、限制选择器宽；展开按钮sticky右缘。实测header scrollWidth=clientWidth=850，四个案件操作在x689–840，展开入口右边界838 |
| P2 | 隐藏选中节点时G6先销毁旧图元再发几何事件，选择／视口缓存仍引用旧对象 | 安全撤销已销毁模型；重绘前撤销图元、裁切和排队回调，绘后重建。隐藏17／18→恢复18／18成功；最终浏览器Runtime异常观察区间为空；新增生命周期回归通过 |
| P2 | G6选中态默认文字weight500覆盖所设粗体；旧虚线可能因样式合并残留 | 选择态保留model文字weight，关系默认显式空lineDash；浏览器实测文字shape为bold／18px，关系shape为italic／14px，金额编码线宽仍7；恢复样式回归通过 |
| P2 | 空画布快照被节点非空条件跳过；恢复后既有开关可能显示旧值 | 允许空数组快照并统一同步控件；合成回归及真实保存→切画布→相反开关→调回验证，照片false、标签all、筛选／字体／金额编码与控件一致 |

复验截图、同图比较与功能检查后，本次范围无未关闭P0/P1/P2。

## 五项视觉核对

- 字体：沿用Segoe UI／Microsoft YaHei／sans-serif栈，按钮12px，Tab与分组层级明确；没有切换成营销页大字。图内字体支持白名单，已检查真正G6文字shape及HTML输出，不只看model配置。
- 布局：固定148px展开、66px折叠，相比展开释放82px画布。邻域独立行移入功能区。大图标、两行小控件、分组细分隔均按确认图组织，规划控件不增加栏高。
- 颜色：保留FundsTrace石墨色现有表面与蓝色操作状态，规划入口琥珀标记，清空画布使用独立危险色。生成图的深蓝材质没有整体覆盖原系统；这是“在现有基础保留”的实现取舍。
- 图像／图标：原品牌、G6与既有本地Tabler资源保留；新增30个本地SVG，典型主按钮图标27px，许可与资源清单已更新。用户明确允许自行设计SVG，本轮使用同一24px描边语法，没有图标CDN或网络运行依赖。
- 文案：Tab为“常用／追踪分析／图形样式／视图”；图内条件明确仅影响展示，全案算法明确独立全案范围。无选择时禁用相关按钮；未实现的分组、时间探索、节点造型等带规划标记且禁用，不伪装执行成功。

与生成图的有意差异：保存／撤销／重做在案件栏使用唯一控件，不在折叠带重复；完整参数收进锚定弹层；折叠实际高度低于参考以增加画布；布局切换使用原选择器。保留原业务语义、当前可用性与用户的画布面积要求优先于设计图的示意重复项。

## 真实合成流程与自动检查

- 超大金额`9999999999999999.99`含等号筛出8个账户／4条关系，原17笔上图与相机不变；颠倒区间不改变现图或历史；撤销返回18／17，重做回8／4。
- 金额编码按完整图统一范围，筛选后4条均为7px，缺币种仍普通线宽；固定线宽在编码时禁用。查找、字号／粗体／斜体、选边虚线、隐藏／恢复、多选群拖与折叠弹层Escape均走真实界面。
- 默认画布另存为命名画布，保存筛选、线宽、字体、照片及标签；切画布／案件返回后恢复。切到同账号隔离案件，展示设置及历史清空、绑定正确。
- 真实菜单分别下载HTML、PNG、PDF、Excel。实际HTML含8节点／4边，保留精确金额、7px线宽、16px粗体，声明4／17范围；自包含成品在真实浏览器加载无error／warn。PNG有效1700×1512，PDF头与下载有效；Excel有17条数据行，未被图内筛选缩减。
- 全案算法四个链接带当前case与对应algorithm；环路入口实际打开独立页，仅环路勾选，不自动开始分析。图内筛选没有带入全案计算。
- `.venv/bin/python scripts/test.py`：447 passed（1条既有FastAPI/httpx弃用提示）；末次展示／导出／资源定向41 passed。
- `npm --prefix desktop test`：24 passed、3 skipped；`scripts/export_api_contract.py --check`通过；`git diff --check`通过。资源清单检查不等于重新打包。

## 本次 Implementation Checklist

- [x] 四Tab与两态真实界面、同图比较、原尺度局部检查。
- [x] 现有控件单实例、命令复用、独立折叠与查询／相机／选择保留。
- [x] 精确币种金额、关系边集合、线宽与受控样式、历史及完整画布保存。
- [x] 真实保存／切换／恢复／导出与SVG许可／桌面资源边界。
- [x] 规划功能、源码与发布／原生GUI证据边界明确。

未执行：新Windows安装包、Windows GUI／DPI、真实浏览器缩放125%／150%、触屏实机和长时间性能采样。没有访问原fund-tracker业务数据或真实Provider。无本轮阻塞性视觉问题；系统字体在Windows的字形差异留待对应环境核对。

---

# 2026-10-15 历史工作台候选设计与流程验收

final result: passed

日期：2026-10-15。此结论限于本轮工作台源码、合成浏览器流程及下列自动检查；不是 Windows 安装包、GUI／DPI 或正式发布结论。当前候选未改变已发布 v0.1.0。

## 视觉依据与比较方式

延续已确认的石墨色工作台。原稿、实现与组合图存于本地忽略目录 `output/release-ui/`，不作为安装资源或业务数据发布。原稿是生成的设计参考，其示意账户、统计、布局与不存在的控件不是业务契约。

| 状态 | 原稿 | 当前截图 | 同图并排 |
| --- | --- | --- | --- |
| 完整工具带、查询展开 | `reference-expanded.png` | `20-expanded.png` | `comparisons/expanded.png` |
| 左侧收起、顶部展开 | `reference-sidebar-collapsed.png` | `23-sidebar-collapsed.png` | `comparisons/sidebar-collapsed.png` |
| 双收起、紧凑工具带 | `reference-compact.png` | `24-compact.png` | `comparisons/compact.png` |

原稿与实现均为1586×992；截图 CSS 视口1586×992、deviceScaleFactor=1。直接并排，无拉伸／重采样，增加32px标签栏。已实际查看三张整页组合图，并用 `comparisons/toolbar.png`、`query-table.png`、`legend-minimap.png` 的原尺度裁剪补查细节；整页工具回传会缩小，不能据此声称逐像素一致。

授权新增状态另查 `21-overview.png`（全览）、`22-query-collapsed.png`（查询折叠）。五个视口为599×640、960×640、1366×768、1440×900、1920×1080，使用同一 IAB/macOS arm64 浏览器；截图为 `viewport-*.png`，小窗口展开查询修订后另拍 `40-narrow-599.png`。媒体截图使用 simulated reduced-motion 使捕获稳定；正常状态的过渡另按交互检查。不是 Windows 系统 DPI 或真实浏览器缩放。

## 发现与修订

本轮范围内没有剩余、具备明确触发依据的 P0/P1/P2。没有沿用上一轮49项检查的 passed。

| 问题与触发 | 修复与证据 |
| --- | --- |
| P1：拖宽侧栏→收起→缩至599px→展开，侧栏越界、画布仅11px | 偏好宽度与实际宽度分开，展开／resize重新夹取。最终侧栏399px、画布200px、把手right420px；回宽恢复偏好。shell公开行为回归及小窗口截图。 |
| P2：紧凑显示弹层内焦点遇resize落到BODY | 只在焦点位于关闭层内部时还焦。浏览器 showPhotoCk→resize1366→btnDisplayActions；弹层关闭且 inert。 |
| P1：连续上图意图被 busy 丢弃或重置相机 | 共享 membership adapter 按最新意图串行收敛，沿用历史／CAS，保留已有坐标与相机。快速反选、409、关闭、A–B–A均复验。 |
| P1：查询乱序、切画布保留旧行；原renderer覆盖锚点 | 请求序号与绑定令牌、同步清旧状态，共用表格模型与稳定ID选择。实际暂停两次查询并倒序返回，旧999笔不覆盖最新0笔。 |
| P1/P2：上传A/B乱序、补充账号残留、未知回执重复提交、延迟关闭新视图 | 会话序号、提交前禁重入、恢复只读原回执。实际导入2笔后注入进度503，恢复后总数19，未重复写入。 |
| P2：详情／前后流水迟到响应、异步保存污染新抽屉及丢焦 | 绑定／请求／抽屉令牌，异步写回检查，重建同抽屉时保留有效控件焦点。合成回归；实际保存合成身份字段及裁剪键盘复验。 |
| P2：旧modal无焦点边界、嵌套Escape或拖出遮罩误关闭 | 背景inert、Tab边界、逐层Escape和还焦；pointerdown/up同遮罩才关闭。实际嵌套详情、外点与拖出复验。IME不拦截输入法默认操作。 |
| P2：分析勾选与实际计算／报告范围不一致；subjects进入innerHTML | 后端验证可选算法白名单与窗口范围，旧调用保留默认项；仅运行选中项，报告说明范围且不推断未运行项；标题转义。实际仅cycles报告验证。 |
| P2：混用select箭头、控件字体／禁用态不一致、窄屏查询被压至零 | 统一本地Tabler箭头、留白与字体；完整金额不截断；查询独立滚动且有最小高度，hidden语义统一。五断点及全览复验。 |

“箭头脱框”最初未在100%基线复现；确认的混用native箭头和内部留白问题已统一，当前截图未见越框。125%/150%真实缩放未执行，不扩写成已跨平台解决。

## 五项视觉表面

- **字体／排版：** 系统 Segoe UI／Microsoft YaHei／sans-serif，正文13px、密集控件及表格12px；账号等宽，金额保留完整十进制与币种。短标签和SVG基线统一，无孤立单位。生成图不能提供可靠精确字体，不宣称字体像素一致。
- **空间／布局：** 保持案件栏、分组工具带、查询／表格、真实G6画布。查询、侧栏、顶部独立折叠。全览保留七列、详情及分页；1586px下完整七列同屏。侧栏保留局部横滚，599px工具带亦局部滚动；960px及以上工具内容在视口内。
- **颜色／状态：** 中性石墨表面、细边框、蓝色操作及选中态、独立红色危险操作；hover／pressed／focus／disabled／pending／failure各有反馈。大量已上图行仍可读，不用颜色独自承载状态。
- **图像／图标：** 24个Tabler 3.35.0本地轮廓图标与MIT许可；下拉箭头仅改stroke以适配暗色背景，修改通知保留。未引入SF Symbols或外部字体。原G6、图例语义及缩略图操作保留。
- **内容／语义：** 侧栏“锚点”与全览“上图”分别表示批量起点与持久追踪事实；“本页全部上图”限定当前页。无确认页脚，关闭不撤销成功操作。参考图与实际合成数据／拓扑差异可接受；完整金额、当前绑定优先。

## 合成浏览器流程

临时 `preview_synthetic.py --graph-nodes 18`：case2初始17笔／18账户；导入合成CSV新增2笔后19笔／21账户。case3同账号用于隔离，case4为空。所有输入、导出及截图均为合成数据。

| 路径 | 实际结果 |
| --- | --- |
| 查询折叠、侧栏／顶部组合 | 条件、页码、锚点保留；批量与表格工具常驻。按节点ID比较位置、相机矩阵与追踪集合不变。 |
| 全览连续勾选／反选 | 17→16，其他节点位置和相机不变；快速17反选及16反选／重选收敛。失败行回退已确认勾选，其他成功行保留。 |
| 关闭与失效 | 发送后关闭不取消；焦点回全览触发按钮。切案2→3→2时旧表格先清空，迟到CAS不进入新历史；新绑定重新读取。 |
| 全览键盘／滚动／遮罩 | Tab／Shift+Tab在最上层循环，逐层Escape还焦；滚动在表格内且th保持顶部。拖出不关闭，真正外点关闭且不穿透画布。 |
| 画布 | 实际另存17笔与新建空画布；空画布行均未上图，加入1笔后关闭仍保留。自动刷新当前绑定查询。历史事务／撤销失败边界由回归覆盖。 |
| 导入 | 实际无效XLSX错误、有效CSV识别／映射／提交／进度／回执；503后显式恢复只读状态，新增2笔没有重放。跨上传会话等由回归覆盖。 |
| 邻域／详情 | 实际查看000GRAPH-00008邻域3/18账户、2/17关系，返回全图；缩略图开关、G6节点详情和合成字段保存。前后payer按钮可Enter切换aria-pressed。 |
| 裁剪／自动补全／IME | 实际裁剪ArrowRight令中心0.50→0.51，Escape取消；自动补全键盘、IME合成确认与native dialog优先级由真实脚本DOM harness覆盖，不冒充输入法实机。 |
| 分析 | 实际只选cycles，恶意样式字符串按文本显示；下载报告snapshot仅含cycles与对应范围，未选项没有“未发现”段落。 |
| 导出 | 经UI实际下载HTML／PNG／PDF／XLSX；PDF渲染检查1页，PNG1872×1558，XLSX双表。超大金额9999999999999999.99为精确字符串单元格。 |

故障注入仅针对当前loopback合成服务。注入期间HTTP400/409/503是预期错误；新建干净预览tab加载19行／17笔上图，控制台error列表为空，调试监听未残留。临时拦截、媒体与视口覆盖已恢复。

## 自动检查与保护边界

稳定候选运行 `.venv/bin/python scripts/test.py -q`：**375 passed，1条既有Starlette/httpx弃用警告，81.40秒**。覆盖最终键盘、绑定和窗口修复；本轮新缺陷曾用原实现对照确认失败后再验证修复。

`npm --prefix desktop test`：22项中19 passed、3 Windows-only skipped、0 failed。API契约导出 `--check`、G6固定hash、`git diff --check`通过。49条桌面静态资源白名单，HTML／CSS35个直接本地引用全部存在并包含；没有新增运行依赖。

`git diff HEAD -- static/app.js 'static/graph-*.js' static/vendor`为空；七个冻结SHA-256与接管基线一致，另记录benchmark hash。未读取原业务数据、未调用Provider、未改Analytix或原始fund-tracker。

## Windows 原生目录候选补充证据

[Actions 38784969693](https://github.com/Eysn0130/FundsTrace/actions/runs/38784969693) 成功，严格绑定运行源码 `02b6c303d8a4d9f0cf0b390999969a482efdb771`，`installer=false`。Windows x64定向Python56项、桌面Node22项（无skip）、React类型检查／构建和原生PyInstaller sidecar通过；11项包内后端检查覆盖无授权启动、token拒绝、中文／空格路径、一个合成案件崩溃重开及EOF退出。

安全报告经artifact分段读取核对：54个资源记录全部与本地相符，其中25项逐字节相同，29项仅为已确认的Windows检出CRLF；源码入口与三个锁文件也相符。后端manifest、smoke和candidate JSON摘要与校验文件一致，两个manifest副本摘要相同。CI列出1542个产物文件；本机只读取安全报告，未独立下载并重新核验整个目录。artifact约550MB、保留3天，安全报告已另存交付证据。

报告保持 `installerLifecycle:not-run`、`guiAcceptance:false`、`installedAcceptance:false`、`signed:false`、`published:false`。没有新安装器、版本、Tag或Release。后续提交只补本文等文档记录，不改变这次构建的运行源码或资源；不把文档提交号写成原生构建SHA。

## 未执行与停止边界

- **目录候选阶段 NOT_EXECUTED（后续安装器补证见文末）：** Windows安装／升级／卸载、GUI／DPI、原生保存／取消、触屏实机、125%/150%真实浏览器缩放。IAB快捷键未改变zoom，因此没有用CSS／pinch替代证据。
- **已知冻结限制：** 密集G6拓扑仍可能标签重叠；本轮按授权冻结节点／关系／标签／布局实现。此次没有性能基准或任意拓扑可读性结论。
- 案件包、删除事务和进程恢复等由合成API／进程回归覆盖；本轮未实际执行浏览器删除案件／画布或Windows活动导入恢复，不把自动检查泛称所有GUI路径验收。
- 新版本号、Windows构建与公开发行仍需绑定后续具体候选。此处passed只表示当前工作台源码范围的发现已关闭，不代表安装包或发行完成。

## Implementation Checklist

- [x] 三种确认状态同尺寸同图比较，补查局部与新增状态。
- [x] 修复可复现P1/P2，真实浏览器及公共行为回归覆盖。
- [x] 条件／锚点／追踪分别管理，快速操作与跨绑定保护。
- [x] 字体、SVG、状态、键盘、遮罩、动效与小窗口可达性。
- [x] 冻结渲染、资源与许可边界核对。
- [x] 使用说明、当前证据及未执行项明确。

## Follow-up Polish

系统字体抗锯齿、真实浏览器125%／150%缩放、Windows GUI／DPI与触屏在对应环境补验。密集图标签属于另行授权的渲染范围；不为本轮风格精修改动被冻结的实现。

## 后续 v0.1.1 发布补证

[Release v0.1.1](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.1) 已公开，绑定发布源码 `e7be2f5675dcd2dc896a20d6d644d5aa05125a73` 及 [Windows安装器构建](https://github.com/Eysn0130/FundsTrace/actions/runs/38827843574)。发布源码完整回归375项、Windows定向Python56项、桌面Node22项、54条资源／11项后端及14项静默安装生命周期通过。EXE实际版本资源正确，升级基线为固定v0.1.0。安装包摘要、源码和报告已核对；原先目录候选记录保持其原SHA范围。安装器未签名，Windows GUI／DPI与其他未执行项不变。此次发布是后续交付动作，不将源码设计passed改称GUI或全面兼容验收。


## 2026-10-16 导入页已确认方案落地

本节只验收本轮导入弹窗及共用横向滚动控件，不覆盖工作区其他未改版界面，也不替代上文各历史候选的验收范围。

### 视觉目标与比较证据

- 已确认视觉：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-design-audit/import-combined-r1.png`。图4流程、图3矩阵、图2必填颜色组合。
- 最终实际截图：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-import-delivery/21-final-mapping-top.png`；实际源码预览 `http://127.0.0.1:56346/?case_id=2`，临时合成库。
- 两者均为1355×1161像素，实际CSS视口1355×1161、deviceScaleFactor=1；源图按原始像素使用，无密度重采样。双边模式、同一合成文件、2行预览、字段核对状态。背景列表因实际验收新增2笔，从7笔变为9笔，不将背景计数差异当作弹窗差异。
- 最终同图全景与原尺寸矩阵局部：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-import-delivery/22-final-design-comparison.png`；可查看对照 `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-import-delivery/comparison.html`。此前比较：`15-design-comparison.png`。
- 有意适配：真实条件必填依解析契约显示；姓名与账号为二选一，有两项均映射时不把姓名误标为唯一必填。文案用“已映射”区分列选择与数据有效性。预览扩为13个规范化列，金额/账号/摘要保留全文，通过局部横滚查看。

### 发现、修复与复验

| 级别及触发 | 修复及最终证据 |
| --- | --- |
| P2：首轮对照中标题、表格字号偏小，弹窗1080px过宽 | 标题22px、正文/表格14px、分区17px；弹窗1000px，接近参考975px。最终同图22已比较。真实辅助说明与新增横滚增加高度；1355×1161下内容clientHeight=scrollHeight=831，横滚底部1017.39px在footer顶部1029.39px内完整可见。 |
| P2：错误圆中的叹号受隐藏图标占位偏到右侧 | 伪元素绝对定位并居中；18-final-error.png中已居中、无旋转。 |
| P2：模式往返丢失专有映射、主账号取消勾选被重置 | 会话草稿保留各模式字段和共享字段的最新值，包含清空；重渲染与成功重新识别保留已有账号勾选。公共行为先RED后GREEN，实际B模式取消勾选后重识别仍为false。 |
| P2：B模式规范化预览未直接返回counter/direction，矩阵示例显示空 | 仅在本方账号唯一匹配付款/收款一侧时，从对应规范化字段显示对方和收入/支出；同账号两侧不猜测。16-final-single-account.png、双向回归通过。 |
| P2：抽取横滚控制器后旧实例销毁仍保留属性处理器 | 销毁只清理本实例拥有的处理器，阻断排队回调并释放观察器；独立双表、销毁/重绑回归从RED转GREEN。 |

本节无未关闭P0/P1/P2。模式草稿与选择保留是实际浏览器发现的交互修复；字号、宽度、标记居中及横滚露出是视觉修复，不把测试运行当作设计迭代。

### 五项视觉表面

- 字体/排版：沿用项目系统字体栈，不引入字体依赖。生成参考没有可核对的原始字体文件，不宣称字形像素一致；22/17/14px层级、完整金额与账号等宽、条件说明11px均在最终局部对照检查。
- 空间/布局：保留顶部流程、矩阵、补充账号、预览、固定操作区顺序。窗口不足时只滚内容；599×640与960×640下操作完整可达，页面宽度未溢出；证据20-final-599-preview.png、17-final-960-preview.png。生成图的正文密度按真实控件和条件说明适配。
- 颜色/token：继承surface-raised/border/muted/accent；操作、成功、警告、失败、必填字色和星号使用root语义token。成功绿色#72be9a，必填字色#ddb46c、星号#e18c91，控件6px、弹窗12px圆角。状态同时有文字/图标，不只依赖颜色。
- 资产/图标：完成勾选使用未改路径的Tabler v3.35.0本地check.svg，许可通知与桌面资源白名单齐备。无新增位图/外部图标服务；流程圆环是原生状态控件。
- 内容：紧凑功能名和实际统计，去除旧长段引导。预览明确“规范化后·13列”；其他字段按模式保留15/16项完整映射；不把“已映射”说成已验证通过。

### 实际交互与检查

实际上传/解析、字段工作/错误/恢复、A/B往返、重新识别、主账号选择、提交/关闭/重开、完成持久回执、Tab循环/Shift+Tab/Escape还焦均已核对。环绕使用850ms旋转；暂停的是当前合成请求，状态来自真实上传/请求/任务，不用计时器假进度。提交后完成为绿色勾选，280ms圆形过渡、勾选150/220ms显现；完成回执保留到明确关闭。实际计算样式观察到旋转矩阵变化，完成样式import-complete与check opacity=1；reduce motion下旋转为none，状态文字仍保留。

最终工作态/完成态：`23-final-confirm-working.png`、`24-final-complete.png`。首次实际新增2笔，重复验收显示新增0/重复2，库总数仍9；B合成文件只预览未提交。预览水平范围Home/End、左右按钮、原生滚动与范围控件同步；独立双表、ResizeObserver、销毁重绑及长金额/长文本由回归补足。真实B预览金额9999999999999999.99与余额保留完整字符串。

最终完整回归：`.venv/bin/python scripts/test.py` → **473 passed，1条既有Starlette/httpx弃用警告，137.88秒**；完整输出`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-import-delivery/final-regression.txt`。workflow41、mapping5以及横滚5项均通过；桌面资源、表格、模态焦点入口包括在完整回归中。候选差异空白检查通过。最终浏览器console error为空；Fetch拦截、媒体与应用视口覆盖已清除。

边界核对：对接管时基线，app.js只改横滚绑定段和导入渲染段，其余段完全相同；ui-dialogs.js和table-workspace.js未改。证据`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-import-delivery/final-candidate.json`，含候选SHA-256。未改后端契约、解析器、业务数据或G6绘制实现；未提交、推送、打包或发布。此处浏览器与合成检查不代表Windows GUI/DPI验收。

### Implementation Checklist

- [x] 已确认图与最终实际截图同图比较，全景及矩阵局部复验。
- [x] 动态流程、真实完成、错误与减少动态效果。
- [x] 共用横滚组件、13列全文及独立表格状态。
- [x] 条件必填、模式草稿和账号选择保留。
- [x] 小窗口、键盘、资源许可和最终回归。

final result: passed


---

## 2026-10-17 导入批次 · 用户选择图 2

本节仅验收导入批次页及其行操作；保留前文的候选和交付记录。源码已完成，未提交、推送、打包或发布。

### 视觉依据与证据

- 视觉真值：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-batches-design/design-2.png`。
- 实现与证据目录：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-batches-delivery/`。
- 最终实际渲染：`22-final-matched.png`。真值和实现均为1487×1058像素，CSS视口1487×1058、deviceScaleFactor=1，左侧区宽609px，无密度缩放。
- 两者使用相同四条合成展示记录、相同顺序/文件名/时间/统计；通过浏览器GET响应夹具对齐内容，没有将示意统计写入业务数据库。状态为批次页ready、菜单关闭、石墨色主题、顶部常用Tab。图谱相机位置不作为本次设计变更。
- 全景同图比较：`comparison-final-full.png`，2974×1090像素（每侧1487×1058，另32px证据标题）；局部同图比较：`comparison-final-panel.png`，1218×515像素，两侧均为原尺度609×483裁切，裁切坐标(0,247,609,730)。已实际打开两幅组合图检查，未把分别看图称作并排比较。

### Findings 与迭代记录

| 级别 | 证据/问题 | 修复与最终证据 |
| --- | --- | --- |
| P2 | 首轮同图`comparison-panel.png`：数字列偏右、标题到表头间距略大 | 数字列最小宽66→78px，标题下间距18→12px。`comparison-final-panel.png`中数字列与原图接近同位，四行分隔/文字可读；已重新截图、重新同图比较。 |
| P2 | 浏览器503状态保留缓存行，但行菜单视觉可用、点击无效 | 非ready时明确禁用行操作；`14-read-failure.png`、`15-loading.png`及浏览器断言确认失败/读取中禁用、重试成功恢复。新增公共行为回归先RED后GREEN。 |
| P1 | 删除成功后，先前启动的GET在刷新失败期间可能恢复已删除行 | 删除成功即使旧读取失效；实际竞态回归先RED后GREEN。最新GET、A–B–A案件切换、确认期间换案均有绑定回归。 |

最终无本节未关闭P0/P1/P2。第一轮结果因上表问题为blocked；修复后最终全景与局部对照通过。生成图中的轻微渐变/发光、字体栅格及其他全局工具带差异按既有产品设计系统保留，不宣称像素级复制或重新设计已完成的顶栏。数值、字号、动作和状态来自真实组件。

### 五项视觉表面

- 字体：沿用Segoe UI / Microsoft YaHei / sans-serif。标题18px/600，文件名14px/600，表格14px，时间12px，模式11px；数字右对齐、tabular-nums、完整千分位，零值弱化。文件名完整换行，未截断源文件追溯信息。参考是生成图，没有原字体文件，不宣称字体轮廓完全一致。
- 布局：平铺表格保留“文件/导入/跳过/重复/操作”，文件元信息两层，无额外卡片。表头sticky，窄查询区复用`bindTableHorizontalScroll`及原水平控件；960×640和599×640实际渲染检查，599px时表格局部425px视口/500px内容可横移，document宽等于视口。599px仅是批次局部压力检查，既有全局顶栏在此宽度会裁切，不据此声明整应用移动端适配；本次桌面目标和960px检查未扩改全局外壳。
- 颜色/token：复用surface-raised、border、muted、text-primary、action-fill/action-hover、state-warning/state-danger、control-radius。蓝色只承担主要动作，非零跳过值用现有警告色；危险操作仅在菜单中呈现，键盘焦点可见。未建立另一套品牌色或圆角体系。
- 图像/图标：保留品牌/G6资源；upload/trash/chevron复用本地图标。新增dots.svg为未改路径的Tabler v3.35.0官方资源，来源、MIT许可和桌面资源白名单已登记；没有图标CDN、新装饰位图或自绘替代资产。
- 文案：导入批次、实际批次数、导入流水、文件元信息和实际统计；没有营销引导、虚构队列或未实现的详情入口。读取失败提供重新读取，删除结果不确定时不自动重试DELETE；确认包含文件名、完整笔数及影响范围。

### 浏览器流程与检查

`browser-final-results.txt`记录最终17项浏览器断言：四条对齐夹具、键盘开菜单/接焦、Escape还焦、Tab/外点关闭、复用导入弹窗、503/加载/重试、空案件及切回、局部横滚与范围同步。`12-menu-focus.png`、`14-read-failure.png`、`15-loading.png`、`16-empty.png`、`17-narrow-960.png`、`18-narrow-599-right.png`为对应状态证据。500删除失败夹具保留全部行并显示“删除结果暂未确认”，证据`13-delete-failure.png`；该截图早于最后的缓存菜单禁用修复，删除分支自身未再修改。

原生确认取消已核对，不发送删除请求、行保留。真实成功路径在独立临时合成案件4执行：从批次页上传synthetic-import.csv、确认新增2笔、关闭持久完成回执，得到批次5；行菜单确认删除后，批次数1→0、流水2→0、账户3→0，显示“暂无导入批次”，焦点回到导入按钮。证据`24-real-created-batch.png`、`25-real-delete-success.png`和`browser-real-created-result.txt`、`browser-real-delete-result.txt`。实际成功流程console为0 errors/0 warnings；故障注入阶段的500/503网络错误是预期测试输入，不作为正常路径无错误的证据。

IAB系统确认一度阻塞自动化，用户取消后恢复；先前一次旧合成行删除返回404，本页保留行并显示服务端失败，未伪报成功。随后重新导入到独立空合成案件并完成上述受控真实成功流程。测试自动化中等待导入弹窗自动关闭曾超时；这是持久完成回执的既有行为，改为点击“关闭导入流水”继续验收，未修改产品以配合工具。

最终`.venv/bin/python scripts/test.py`：**474 passed，1条既有Starlette/httpx弃用警告，133.89秒，exit0**；批次公共行为harness **14/14 PASS**。日志`final-regression.txt`，包含最终JS及间距/列宽修复。`git diff --check`通过。未改后端API、解析器、生产数据或G6绘制；17个图谱/画布/表格受保护文件与本轮基线哈希一致，ui-dialogs.js、workflow-ui.js、workspace-shell.js也未改变。`final-candidate.json`与`candidate-incremental.diff`记录实际增量边界。

验收完成后清除测试GET/DELETE拦截和临时视口/媒体覆盖；保留实际批次页预览与设计对照。此处源码/合成浏览器结果不代表Windows安装包或Windows GUI/DPI验收。

### Implementation Checklist

- [x] 图2平铺表格、完整数字、文件/模式/时间层级及紧凑导入入口。
- [x] 同一token/图标/横滚组件，最终同图全景及原尺度局部比较。
- [x] 真实菜单/原生取消/导入/删除/刷新/还焦，读取与删除失败状态。
- [x] 案件绑定、迟到GET、删除竞态和重复动作回归。
- [x] 474项完整回归、源码边界和本地预览清理。

final result: passed


---

## 2026-10-17 前后流水 · 用户选择图 2

本节只验收前后流水弹窗。用户选择最新四图中的第2幅“右侧匹配线索”方案后落地；保留前文导入与批次记录。

### 视觉依据与证据

- 视觉真值：`../fundstrace-context-design/design-2.png`，通过design-manifest.json的displayedOrder["2"]绑定，不使用早先批次页的图2。
- 证据目录：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-context-delivery`。下列截图和组合图均相对于此目录。源图绝对路径为`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-context-design/design-2.png`。
- 最终实际渲染`14-final-selected.png`，源图及实现均1553×1013像素；CSS视口1553×1013，deviceScaleFactor=1，无缩放或设备框。
- 状态：收款方视角、前后各3笔、归属全部；同一组7笔合成交易、相同时间/名称/账号/金额/余额/摘要，19与22已上图，21与23勾选。通过真实导入与真实API得到数据，未伪造接口。实际ID22、案件名“合成演示·空白案件”、共7笔/第4笔与生成示例ID1042、43笔/第22笔不同；真实匹配引擎每组2个候选及评分95/94/100/68，与示例单候选86/91不同。上述数据差异在评估前已明确，不当作布局误差或篡改数据以求一致。
- 全景同图比较：`16-final-comparison.png`，3122×1049；每侧原尺度1553×1013，间隔16px，证据标题36px。已实际打开组合图检查。
- 原尺度局部同图比较：`17-focus-header.png`（双方裁切100,110→1455,335）；`18-focus-table.png`（源105,345→1150,806，实现103,339→1156,801）；`19-focus-rail-footer.png`（源1158,280→1455,924，实现1181,280→1476,924）。已分别打开组合图确认控件、列、金额、评分和固定动作。

### Findings 与迭代记录

| 级别 | 首轮证据与影响 | 修复与复验 |
| --- | --- | --- |
| P2 | `04-initial-comparison.png`：账户定位强制另起一行、表格66px行高，仅约6行露出，改变主要内容密度 | 定位行改为可伸缩240px基宽；表格垂直padding12→6px，实际7行均55px。锚点底部间距20→6px。`14-final-selected.png`及`16-final-comparison.png`中7行完整露出，条件为一行，固定底栏仍可达。 |
| P2 | 同一首轮组合图：真实多候选及长标签将“疑似去向”推到首屏之外 | 评分与账号行并置，补充聚合标签改为按钮外原生details，完整原因保留。`19-focus-rail-footer.png`显示来源及首要去向同时可见；`06-match-details.png`验证可展开完整标签，线索区独立滚动可访问其余候选。 |

首轮结果为blocked；修复后重新捕获并实际打开全景与三个局部同图比较。本节无未关闭P0/P1/P2。

生成图已预先登记的列错位、已上图行多余勾选框与琥珀评分在实现中修正为严格8列、状态文字和中性评分，不复制生成错误。日期采用两行便于保留完整信息；方向和归属分层文字，不采用生成图的多色胶囊。没有实现表头全选新功能。实际多候选使线索区需要滚动；所有候选、理由与补充依据均保留，不裁掉数据。这些是产品契约下的明确适配。

### 五项视觉表面

- 字体/排版：沿用Segoe UI、Microsoft YaHei、sans-serif，账号沿用Consolas/Courier New。22px标题、15px户名、14px表格和候选名、12px元数据，表格数字tabular-nums且右对齐。源是生成位图，无原始字体文件；字形栅格和光学字重存在差异，不宣称像素级复刻。两行日期和完整账号在原尺度局部检查，无金额省略。
- 空间/节奏：1400×840弹窗、294px右栏、78px固定底栏，控件36px、控件圆角6px、弹窗12px。桌面实际表格1052×461.5，7行55px，8列。960×640下左表局部横移328px；599×640下横移425px，弹窗x12→587、底栏y549→627，均在视口内。599px为弹窗压力检查，不表示全应用移动端支持。证据`09-960-start.png`、`10-960-end.png`、`11-599-start.png`、`12-599-horizontal-end.png`、`13-599-rail.png`。
- 颜色/token：复用surface/raised/border/muted/text-primary/accent/action-fill/state-success和圆角token；新增表头、选择面、hover、focus语义token集中在root，不建立新品牌色。选中与锚点有不同底色及文字/勾选/细边标记；已上图为成功色文字；评分保持中性，避免伪概率与风险结论。
- 图像/图标：无装饰位图、外部字体、图标CDN或新增依赖。关闭、方向、勾选、图谱、横滚箭头复用已有本地Tabler资源；没有自绘SVG或图标替代。品牌和G6资产保持原样；生成图的光晕/渐变不引入产品。
- 内容：前后流水、真实案件、锚点交易、匹配线索及准确已选/已上图数量。完整金额/余额通过既有moneyHtml显示，null显示破折号；长金额实际截图`02-real-window.png`与回归覆盖。补充依据保留原引擎标签，评分仅供匹配参考。没有机械引导、虚构结论、额外分析或导出入口。

### 实际交互与验证

浏览器证据`browser-checks.json`：候选Space与Enter均只切换一次；行勾选反向更新候选状态；归属切换清空隐藏选择且禁用提交；付款方视角得到真实1笔账户；960px横滚Home/End分别0/328，599pxEnd为425；Tab/Shift+Tab在弹窗内循环，Escape还焦到原流水的“前后”按钮。原生补充依据可展开，固定底栏在小窗口可达。正常流程consoleErrors为空。

真实合成上图：选择21与23，点击“加入图谱（2）”，弹窗成功关闭，已上图2→4，两个查询行均显示已上图，截图`15-track-complete.png`。随后使用现有撤销恢复演示状态，重新打开并选中两笔供用户查看；未删除记录。所有数据在既有一次性合成预览运行目录中，未触及业务数据库。

异步和失败状态由加载真实上下文函数的公共行为harness补充：最新条件胜出、关闭重开与迟到响应、A–B–A案件/画布绑定、读取失败重试、空结果、过滤推荐范围、追踪锁定/防重、false/异常保留选择与迟到完成。没有以原生浏览器自动化等待代替产品状态。只在确认现有trackFlows成功后关闭，失败保留当前选择并显示结果。

最终检查：context harness10/10、workflow41/41；必需`.venv/bin/python scripts/test.py` **475 passed，1条既有Starlette/httpx弃用警告，137.48秒，exit0**，日志`final-regression.txt`。独立只读状态复核无阻塞意见。保护清单18项（G6/图谱/画布/table-workspace/workspace-shell）SHA-256与本轮基线一致。增量只涉及ctxModal、上下文渲染及绑定、上下文工作态、原生候选键盘适配和相关回归；`candidate.patch`记录接管基线后的差异。无后端契约、解析器或图谱绘制修改；未提交、推送、打包或发布。此验收不代表Windows安装包或GUI/DPI证据。

### Implementation Checklist

- [x] 正确绑定本次图2，表格主区、固定匹配栏与固定动作。
- [x] 同一token、图标、弹窗焦点层和横滚组件。
- [x] 严格8列、完整金额、已上图状态与双向勾选。
- [x] 加载/空/失败/重试/追踪锁定及案件画布时序。
- [x] 实际两笔上图、窄窗口、键盘、全景及局部比较、475项回归。

### Follow-up Polish

P3：生成字体的字形和14px系统字体有光学差异，后续可在全局字体策略确认时统一；本节不引入独立字体或再改一套字号。无阻塞开放问题。

final result: passed


## 2026-10-17 · 资金流向图1与详情手势

### Findings

本节仅验收本次用户选定的图1右侧抽屉及显式详情入口。当前无开放P0/P1/P2。没有改变既有G6绘制、双击用途或节点拖动阈值。

视觉真值：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-edge-delivery/selected-revision.png`。它是本次最新四图中的图1，结合用户手势反馈补入显式“查看详情”的修订稿；不是前一轮上下文图2。原图及选择记录见`selected-target.json`。实现：`http://127.0.0.1:56346/?case_id=4`；最终截图`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-edge-delivery/14-final-four-rows.png`。两者1552×1013像素，浏览器CSS视口1552×1013、DPR1，没有缩放归一化。当前同一条华盛建材→华安商贸关系，4笔、CNY254000.00及USD3500.00分开；日期、摘要与4笔金额相同。生成稿中的案件副标题、页头旧总数和图谱几何属于示意背景，与合成案件当前绑定及真实图谱不一致，使用当前真实状态；不拿它们作像素匹配依据。

全景同图比较：`comparison-final-full.png`（3120×1049）；局部同图比较：`comparison-final-details.png`（1522×845）及`comparison-final-entry.png`（740×82）。已经打开并逐项观察，左侧真值、右侧浏览器；不是分别看图后凭记忆判断。

五项保真检查：

- 字体：沿用产品既有系统字体及字号层级，标题22px、表格14px、日期/账号使用现有小字规则；金额完整右对齐。生成稿的字形、抗锯齿和更亮的按钮为P3光学差异，不引入独立字体与另一套按钮。
- 布局：752px右侧工作区、固定64px标题、付款收款信息、分币种合计、时间跨度、4列表格、共用横滚条及68px底栏均保留。4条时间正序，全景可同时读到4条；没有金额截断或隐藏的行操作。
- 颜色/token：使用现有raised背景、border、muted、文本和按钮token。原生工作区保持石墨色；示意图光晕未被引入。抽屉z30高于minimap z12、低于现有模态层z50，实际elementFromPoint命中抽屉。
- 图像/图标：无新图片或装饰资产；使用已有Tabler本地图标与真实G6图谱，没有用生成图当功能界面或画一套替代图标。
- 文案/事实：保留“付款方/收款方、已追踪、时间跨度、已追踪流水、前后流水、仅当前图谱”；不加入引导话术。金额仍调用共享moneyHtml，人民币显示¥而非生成稿CNY前缀，USD显式列出；分币种不合并。原始流水ID、金额字符串、时间语义与案件隔离不变。已显示数和接口total分开，上限500不暗示全量。

### Comparison history

1. 首次同数据比较`07-initial-comparison.png`发现P2：返回的倒序明细与稿的时间正序不同；多币种合计纵向堆叠使摘要更高，底栏高度不符。修复为复制后按格式化原时间/ID稳定排序、两币种内联可换行、底栏68px；不修改返回事实。截图`08-final-four-rows.png`复查，最终重新捕获`14-final-four-rows.png`并形成上述全景/局部同图，无剩余P2。
2. 实际操作发现P2：不打开详情时，第一次关系样式变更的重建清空选中，第二次修改失效；`03-red-selection-lost.png`为RED。修复独立保存selectedEdgeKey，在rebuildGraphViewport建立新映射后恢复关系选择，`04-green-style-selection.png`实际连续线宽2及虚线编辑保持选中。没有改变图谱绘制模块。
3. 复核发现P2：滚动后行菜单可能继续指向离开的行。增加正文/表格scroll捕获关闭菜单，单元和599px实际横滚均确认关闭。菜单Escape仅退一层并还焦行按钮，第二次Escape关闭抽屉但保留选择。

### Browser evidence

使用既有IAB、一次性合成预览。`browser-checks.json`保存按时间的公开DOM观测（包含修复前RED和后续GREEN，未覆盖失败证据）。单击只选中，节点实际拖动不打开详情；节点连续粗体/斜体、连线连续线宽/虚线编辑可用；Command多选禁用详情；空白单击清除；功能区按钮及图内Enter分别打开详情；关闭保留选择并还焦图谱。既有双击语义由未修改处理器和原图形交互回归覆盖，本轮未宣称浏览器重做所有折点/双击用例。

动画读取到280ms cubic-bezier(.2,.75,.25,1)位移，开始为非零translate、结束为0；减少动态效果时transitionDuration为0s。实际缩略图中央命中drawer子元素。菜单位于抽屉内部可操作，不被缩略图盖住。

599×640截图`10-narrow.png`、`11-narrow-scrolled.png`：抽屉宽599，无页面水平溢出；表格本地横滚96px，操作列固定可达，range End能到末端并关闭行菜单。正文纵向滚动229px后底栏完整可达（y572至640），标题与关闭按钮保留。960×640截图`12-medium.png`验证752px抽屉及可达底栏；顶部既有功能带采用原横向结构，本轮没有重设其布局。

前后流水真实打开#19，关闭回到4笔抽屉并还焦该行。真实移出#28后抽屉沿现有受控命令关闭，已上图5→4而原始流水仍10；截图`13-remove-complete.png`。随即使用现有撤销恢复5笔，再打开4笔关系，最终截图`14-final-four-rows.png`。没有删除源记录或接触业务数据。正常流程console error列表为空。

### Verification and scope

新增edge workspace harness12/12、workflow41/41、ribbon入口25/25；原graph interaction291/291。完整`.venv/bin/python scripts/test.py`：**476 passed，1条既有Starlette/httpx弃用warning，139.58秒，exit0**，完整日志`final-regression.txt`。首次全量的fake DOM旧观测器只支持单参数classList.add且不能序列化动态tbody，已保留RED日志与原文件，仅改fake DOM观测以反映真实DOM；原异步/关闭断言保留。生产浏览器行为另有上述实际证据。

读取乱序、关闭重开、节点/关系切换、案件/画布A–B–A、相同view revision变更、失败重试、移出锁定与防重、false/异常保留、迟到完成、超500计数、完整金额、escape转交由加载真实函数的公共行为harness补充。15项保护清单hash全部与本轮基线相同，包含graph-selection、图谱渲染相关模块、G6、canvas-editor/history、table-workspace及workspace-shell；`git diff --check`通过。`candidate.patch`记录接管基线后差异，`final-candidate.json`记录最终源文件hash。

这是一轮源代码与合成浏览器验收，不代表Windows安装包、GUI/DPI或发行验证。未提交、push、merge、打包或发布。

### Open Questions

无阻塞项。

### Implementation Checklist

- [x] 图1右侧抽屉，平滑位移、减少动态效果、覆盖缩略图。
- [x] 单击选择与详情分离，保留拖动/双击/样式编辑，图内Enter及显式入口。
- [x] 共用token、金额格式、横滚、前后流水及受控移出/撤销。
- [x] 关闭/异步绑定/菜单层级/失败工作态与公共行为回归。
- [x] 同视口全景及可读局部比较、窄窗口、完整476项回归。

### Follow-up Polish

P3：生成稿的字体光学细节和较亮的行按钮与现有共享组件略异，保留统一组件策略；后续如调整，宜作为全局字体/按钮规则一起确认。无本轮阻塞问题。

final result: passed


## 2026-10-17 · 账户资料图1与完整业务流程

### Findings

本节仅验收用户选定的最新账户资料图1。当前无开放P0/P1/P2。沿用上一轮已确认的显式详情入口与手势；没有修改G6、节点拖动阈值、双击用途或图形渲染模块。

视觉真值：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-account-delivery/selected-revision.png`，来自账户图1及用户交互反馈的修订稿，原图选择记录见同目录`selected-target.json`。实现为`http://127.0.0.1:56346/?case_id=4`，最终截图`12-final-desktop.png`。真值与最终截图均为1551×1014，CSS视口1551×1014、DPR1，无缩放归一化。初次截图1552×1013，已纠正尺寸；`08-final-desktop.png`处于滑入中且查询区展开，明确排除于最终视觉验收。

已实际打开同图比较：`comparison-final-full.png`（3118×1042）、可读账户局部`comparison-final-details.png`（1312×838）及显式入口`comparison-final-entry.png`（658×66）；左为修订真值，右为浏览器。没有分别看图后凭记忆判断。

五项检查：

- 字体：沿用既有系统字体，标题22px、分区16px、输入13px、密集明细12px及账号11px；完整金额使用等宽数字规则。为同时保留三条可读明细与底栏，表格采用共享产品的紧凑密度。生成稿字形、较亮按钮及头像光学差异为P3，不引入第二套字体、按钮或自制图标。
- 布局：648px连续右侧抽屉，标题52px、账户身份、两列资料/图谱设置、三列统计、本案件TOP20明细、共享横滚、48px底栏。桌面正文无需纵向滚动，底栏准确到y1014，三条明细可读，剩余两条在表格内部纵向滚动。小屏正文纵滚，标题/关闭可达，表格独立横滚。
- 颜色/token：使用既有surface、raised、border、muted、primary、selection、warning和focus token。金额保留共享中性色；稿中金色未建立独立资金语义。抽屉z30、缩略图面板z12，实际缩略图中央elementFromPoint命中drawer子元素；模态层仍高于抽屉。
- 资产：头像缺省复用本地Tabler user-search；真实照片继续走原上传、原图及裁剪接口，无生成图片作为产品界面，也无新运行时依赖。
- 内容/事实：身份证、照片、主账号、重点展示、案件/图谱统计、首末时间、交易对手、查看流水、展开邻域与受控移出均保留。金额字符串与币种分别展示。合成账户DEMO-0021转入8笔：CNY510000.00、USD3500.00；转出4笔CNY248000.00，时间与稿一致。当前源流水12、已追踪12、真实交易对手5；稿的顶栏5笔、示意图几何、案件副标题及三条示意排序不作为业务事实。保留API交易对手顺序，不为像素匹配对混合币种做错误合计排序。

### Comparison history

1. 首次组合比较`comparison-initial.png`发现P2：头像滤镜把背景也染为实心蓝，底栏落到视口下方。修复图标包裹层及分区密度，保留整体结构与统一控件。
2. `comparison-intermediate-details.png`发现P2：第三条多币种明细被裁切。调整ledger最小高度、单元行距和底栏上间距；最终同视口比较三条明细与底栏均完整。
3. 独立状态复核发现P2：A账户设置保存时可在B开启裁剪，随后图谱重建使裁剪绑定失效；另有新建/另存为画布未经过草稿离开确认。跨账户/过期裁剪用例保留真实RED，再补上串行门禁与失效裁剪恢复；画布离开用例在草稿保护加入后验证，不宣称该用例有修复前RED。

### Browser evidence

使用现有IAB与一次性隔离合成服务，`browser-checks.json`保存公开DOM观测及明确排除的动画中间帧。没有读取或修改原业务数据库。

实际走通：草稿关闭重开保留；重点大小保存不覆盖身份证草稿；非法合成身份证被服务端拒绝并保留输入；合法全零合成身份证保存/清空；手动主账号标记/取消；焦点回到对应控件。跨案件离开草稿确认选择继续编辑后，原case4与输入均保留。各项临时修改已恢复。

照片实际选择160px合成位图，键盘调整裁剪圆及缩放并确认上传；重新裁剪读取原图，Escape取消；移除确认取消保留照片，再明确确认移除。模态时背景inert，单次提交有忙态。测试图片不是产品资产。证据`02-photo-crop.png`。

预先留下旧金额/关键词，再点击华盛建材的查看流水，实际账户DEMO-0021、对手DEMO-0012、旧条件清空、仅已上图关闭、4笔结果，查询区自动展开，证据`03-counterparty-query.png`。统计范围分段按钮实际切换，本案件TOP20标题仍明确；当前全12笔均已追踪，两范围结果相同，不冒称浏览器证明了部分追踪差异，该差异由加载真实函数的行为回归覆盖。

实际节点单击只选择，原生拖动不打开详情，Undo显示移动选中节点；连续粗体/斜体保持同一选择且不打开详情。随后撤销临时样式/移动。双击仍由未修改处理器及既有图形交互回归覆盖，本轮没有宣称再次浏览器验证全部双击/折点组合。显式按钮及图内Enter打开，关闭保留选择并还焦图谱。证据`04-node-drag.png`。

更多操作确认移出12笔当前相关已追踪流水；完成后源流水仍12、已上图0，撤销恢复已上图12。证据`05-remove-confirm.png`、`06-remove-complete.png`。照片移除与图谱移出均使用应用确认，无浏览器系统confirm阻塞。

动画实测开始非零translate、结束matrix(1,0,0,1,0,0)，位移280ms cubic-bezier(.2,.75,.25,1)；减少动态效果时duration0s。599×640：抽屉宽599、文档无横向溢出，表格横滚范围56px，range End/Home达到56/0；操作列固定可达，菜单滚动即关闭，Escape先关闭菜单并还焦触发按钮，第二次关闭抽屉。正文滚动后底栏y592.09至640.09可达，截图`10-narrow-scroll.png`。390×740：资料表单堆叠、关闭按钮right374、drawerBody宽/scrollWidth均389；截图`11-small-390.png`。既有功能区收起按钮在390px时right415形成25px文档溢出，属于未变更的顶栏边界，本轮不作全局移动端通过声明。正常流程console error为空。

### Verification and scope

新增账户公共行为harness34/34；覆盖读失败重试、乱序/关闭/账户/案件/画布A–B–A、图谱revision、草稿与规范化回写、所有离开绑定入口、同范围迟到成功、跨账户互斥、裁剪失效/防重/资源回收、菜单、确认去重与成员变化、受控移出false/异常、完整金额及筛选清理。实际IO由合成stub隔离，加载真实app/workflow/dialog函数；不能把harness当真实浏览器证据。

完整`.venv/bin/python scripts/test.py`：**477 passed，1条既有Starlette/httpx弃用warning，144.01秒，exit0**。最终16个源/测试/runner文件运行前后hash一致，完整日志`final-regression.txt`。第一次全量472通过/5失败的日志保留；失败来自partial VM缺失新状态重置边界，以及legacy fake DOM的classList.contains恒false；修正后补上缺失的drawer.contains。按真实DOM补齐观测，没有删除旧业务断言，也未涉及本轮动态tbody修改。针对原5个失败wrapper已通过，之后完整477通过。

15项保护清单最终hash与接管基线相同：graph-selection、图谱渲染/布局/路由、G6、canvas-editor/history、table-workspace及workspace-shell。使用共享横滚、受控命令、货币格式和dialog层级，没有新建替代系统。`candidate.patch`仅记录本轮接管基线后差异，`final-candidate.json`记录最终候选hash；JS语法与`git diff --check`通过。

这是一轮源代码、公共行为和合成浏览器验收，不代表Windows安装包或GUI/DPI、真实案件或发行验收。未提交、push、merge、正式打包或发布。

### Open Questions

无本轮阻塞项。

### Implementation Checklist

- [x] 图1账户右侧抽屉、平滑位移、减少动态效果与覆盖缩略图。
- [x] 单击/拖动/样式编辑/原双击不冲突，显式入口、图内Enter及关闭还焦。
- [x] 身份、照片、主账号、重点展示、统计、对手查询和确认移出/撤销完整保留。
- [x] 统一token、横滚、金额、dialog、草稿和异步绑定处理。
- [x] 同尺寸全景/局部比较、窄屏实际操作及完整477项回归。

### Follow-up Polish

P3：生成稿的字体光学细节、明亮按钮与头像和共享产品控件略异，保持统一规则。390px既有顶栏25px横向边界如需调整，应按全局功能区任务单独处理；账户正文无该溢出。无账户设计的开放P0/P1/P2。

final result: passed


## 2026-10-17 · 全案分析图1与功能展示

### Findings

本节限定用户选定的全案分析图1，替换旧独立分析页并保留四算法、案件、分析对象、参数、结果、六列明细和HTML报告流程。视觉、功能与完整回归已收敛，当前无开放P0/P1/P2。没有修改分析后端、G6、画布命令与图形交互。

视觉真值为`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-analysis-delivery/selected-target.png`，绑定最新4个方案中的图1，原图及生成结果ID见同目录`selected-target.json`。实际页面`http://127.0.0.1:56346/analysis?case_id=4&algorithm=paths`，最终运行截图`12-final-desktop.png`。真值与实际均为1536×1024、DPR1；没有缩放归一化后冒称同尺寸。

已实际打开并在同一图中审阅真值/实际：`comparison-final-full.png`（3088×1052）、`comparison-final-results.png`（2316×738）及原尺寸可读`comparison-final-parameters.png`（788×992）。左为真值，右为浏览器；不是分别看图后凭记忆比较。

五项检查：

- 字体：保留共享系统字体，页标题30px、配置20px、分区16px、标签13px、输入14px、结果16px/24px、表头14px。金额沿用共享完整币种格式与等宽数字，右对齐便于逐行比较。生成稿的字形光学和发光为P3，不建立第二套字体。
- 布局：桌面60px顶栏、386px配置栏、右侧结果工作区；参数区独立纵滚、执行按钮固定在配置栏底部。四算法多选和四结果标签职责分开。6条路径完整可读，表格底798px、共用水平条顶805.5px，水平条紧邻内容。599px和390px采用单列，正文纵滚、表格局部横滚，无文档横向溢出。
- token：沿用surface、raised、table-header、border、muted、selection、accent、success、warning、danger、focus和required token；主按钮、输入、焦点、Tabler图标、完整金额组件及品牌资产复用既有规则。完成态绿色勾选、工作态真实旋转，系统减少动态效果时停止旋转。路径必填标签用既有强调色和星号。
- 资产：复用本地logo与file-export/check/refresh/chevron等图标；无新增栅格产品资产，无生成图片代替实际可操作界面，无新运行时依赖。
- 事实和能力：样稿中的对象下拉改为既有多账号/户名输入，保留逗号和换行输入及路径不受对象过滤的真实约束；路径保留账号输入，不虚构不存在的账户选择服务。默认前三算法、参数区按所选算法显隐，快捷路径首开即显示起终点。禁用且无实现的拆分分析不作为可用功能。结果标题/单位/币种来自真实返回；交易边合计明确非去重本金，空结果不作合法性或穷尽检出结论。报告明确按本次已完成参数重新计算，不声称导出屏幕结果快照。

### Comparison history

1. 首次同图比较发现P2：表格字体/列宽密度偏离、水平条远离表格底。修正结果16px密度、路径104/204/116px前三列及自然高度；最终路径栏起点约840px，水平条紧邻第6行，没有遮挡或内容裁切。
2. 独立只读审阅发现3项P2：离群非金额维度缺少币种；重跑/失败残留上次快照笔数；深夜交易笔数误称占比。均先记录真实行为RED，再增加币种列、统一清理scope并按实际统计维度显示笔数/对手数单位；复核闭合。
3. 浏览器真实延迟响应发现P2：工作中标签仍显示“未运行”。先设置工作phase再渲染标签，所选算法显示“待结果”，失败或切案恢复未运行；新增第48项测试在修复后验证，不冒称该测试有修复前RED。`06-working-state.png`为修复前证据，最终工作态用`08-working-reduced-motion.png`。
4. 最终参数复核发现P2：12.5%容差传给API为0.125，但旧结果标题用toFixed(0)显示13%。先捕获真实行为RED和浏览器RED，再使用原生Intl百分比格式（15有效位）并将输入step设为any。第49项覆盖12.5%与0.29%避免取整及浮点尾数；浏览器重载后12.5通过原生validity且标题准确显示±12.5%，见13-fractional-tolerance.png。

### Browser evidence

所有实际操作使用现有IAB和新建的一次性隔离合成预览，读取/导入数据均为明确生成的合成样例，没有接触原业务数据库。`browser-checks.json`记录公开DOM观测。

通过真实导入界面向合成case4导入12笔/6账户；四算法返回0/0/0/6。完整账号DEMO-0012→DEMO-0035，最大4跳，实际6条两跳路径：CNY129500.00、129000.00、83500.00、83000.00、51500.00、51000.00。故意缺少账号连字符时返回0，没有错误匹配为同一账号；恢复完整字符串后返回6。

向合成case2导入27条正例，加既有3条种子，源30笔/50账户，四类分别1环路、2配对、5离群、1路径。环路3条原始流水合计CNY1000.06；配对分别CNY2000.02和USD1000.50。实际展开环路3条、首个配对2条，六列时间/付款方/收款方/金额/余额/摘要齐全；空余额为—、非空0.01保持分位。离群独立币种列，大金额9999999999999999.99分位完整。截图02/03/04保留验证时实际状态，之前的配置密度不是最终视觉真值。

修改快进快出30→90分钟后，已完成结果保留；报告确认列出实际案件与四算法，并明确草稿尚未运行、使用已完成参数。取消后焦点回报告按钮且90草稿保留。真实下载HTML，解析其中结果JSON，确认case2、源30/50、窗口仍30、容差0.25、路径4跳、计数1/2/5/1及大金额精度；`report-download-check.json`与`synthetic-analysis-report.html`为实物证据。未宣称报告直接冻结了旧数据，源数据变化仍会重新计算。

有草稿切案点继续编辑，原case2/90/结果保留；恢复30后切case4，起终点、对象、旧结果和旧快照统计清空。真实断网重跑：显示未完成，参数保留、旧结果及笔数不残留、报告禁用；恢复网络点击重跑得到6路径，证据`07-network-error.png`。

真实网络延迟1800ms观察工作态：运行/报告/参数禁用、无重复运行、无伪百分比或每算法进度，结果清空，标签待结果；减少动态效果下computed animation为none。正常设置仍用1秒旋转。浏览器override均在交付前恢复。正常流程dev error/warn观测为空。

599×760：文档宽599，水平条End到193、表格scrollLeft193；390×760：文档宽390，Home回0，表头/参数堆叠和返回入口可达。结果标签ArrowRight→环路、End→离群、Home→路径，算法勾选仍全部4项，完成状态不变。单列正文通过正常滚动访问全部配置和结果；并不强求在一个窄屏视口同时显示整页。最终窄屏截图09/10/11。

### Verification and scope

新增分析行为harness覆盖案件读取失败/重试、空案件、统计失败、A–B–A、请求乱序、算法与参数选择、路径快捷入口、百分比归一化、精确金额/多币种/真实单位、结果分类、草稿离开、报告确认和冻结参数、重复请求与迟到blob、明细六列/500上限/旧DOM/旧run/分类切换、真实共用横滚绑定及销毁。IO由合成stub隔离，生产render、参数收集、状态和金额/横滚代码真实加载；不能将harness当真实浏览器或Windows证据。

`static/table-scroll.js`从主页原有bindTableHorizontalScroll精确迁移；主页同名边界只转发，共用同一实现；分析表及嵌套明细绑定/销毁同一组件。`scripts/build_desktop_backend.py`仅增加analysis.css和table-scroll.js静态资源闭包，未执行正式打包。

首次完整回归478 collected、477通过/1失败，失败wrapper workflow含3个旧模拟fixture错误：缺setAttribute、旧代码片段缺analysisMethods、未适配报告原生确认。保留原日志`final-regression.txt`；仅更新workflow末尾3项fixture，前38项字节不变，转义、冻结算法及范围边界断言保留。定向workflow41/41通过，RED/GREEN见workflow-fixture-red/green.txt。此前一次全量被中止，343通过后exit2，明确不作为最终GREEN。

最终`.venv/bin/python scripts/test.py`：**478 passed，1条既有Starlette/httpx弃用warning，151.00秒，exit0**；28个候选源/测试/runner文件before/after hash一致，完整原始日志`final-regression-final.txt`。新增分析行为49/49，含12.5%及0.29%小数容差API与结果标题，日志`analysis-workspace-final-directed.txt`。容差修复前另一次完整478通过日志`final-regression-after-fixture.txt`保留，不冒称该次覆盖了最后修复。18项保护模块（G6、图谱选择/渲染/布局/路由、canvas-editor/history、table-workspace/workspace-shell及analytics/analysis_routes/report）与接管基线hash一致，见protected-final.json。JS语法与git diff --check通过；最终`final-candidate.json`记录本轮候选指纹、真实检查范围及截图绑定，`candidate.patch`只包含接管基线后的本轮差异。

这是源代码、公共行为和隔离合成浏览器验收，不代表Windows安装包、GUI/DPI、真实案件或正式发行验收。未提交、push、merge、打包或发布。

### Open Questions

无本轮阻塞项。

### Implementation Checklist

- [x] 图1整体布局、真实内容、统一token/图标/控件。
- [x] 四算法及全部参数、对象过滤、路径快捷入口、正确比例/金额/币种/单位。
- [x] 多选算法与只读分类、草稿离开、案件/结果代次、工作/失败重试状态。
- [x] 六列流水明细、500上限、共享横滚绑定销毁及键盘操作。
- [x] 报告确认、完成参数冻结、真实下载、迟到响应与blob防护。
- [x] 同尺寸全景/局部对照，599/390实际操作，49项行为及完整478项回归。

### Follow-up Polish

P3：生成稿光效、字体光学、胶囊成功徽标与共享控件略异；遵循现有token/控件规则，不另造视觉系统。

final result: passed

## Export workspace — merged revision 1 (2026-10-17)

Selected target: `fundstrace-export-design/revision-1.png`, Image Gen result `exec-8df6f181-de32-47e4-be69-468dce84fa1f`, SHA256 `05b05f1b019545a9cf664f448c481907cdc2e7db92779635e6541f6b70f99dc0`. Current user explicitly approved implementation and requested colored selected SVG icons. The central modal from option 2 and vertical four-format selection from option 3 remain the visual contract.

Evidence root: `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-export-delivery/`.

### Visual comparison and shared components

Opened and reviewed `qa-full-comparison.png` (1536×1024 source and same-state browser capture), `qa-focus-comparison.png` (same 664×710 modal region), and `qa-natural-full-comparison.png` together with the exact source. Final natural-window capture is `26-final-natural.png`; selected HTML, six accounts, five relations. The forced viewport is useful for modal geometry but produced stale Canvas/compositor captures after resize. Those captures are not accepted as evidence for Canvas restoration; natural-window PNG/PDF operations and pixel comparison below are the accepted evidence. Natural full-page viewport is 716×777; modal dimensions remain 664×710 without scale changes.

The implementation uses the existing raised surface, accent, muted text, action, focus, success/error, radius and reduced-motion tokens. Selected SVG masks resolve to `rgb(54,124,255)` (`--accent`); three unselected masks resolve to `rgb(168,182,199)` (`--muted`). All icons are existing Tabler assets. Native radio semantics, arrow keys, Space, Tab, Escape, focus return and native dialog isolation are retained. Source optical highlights and exact icon glyphs are P3 differences; the shared product tokens and icon family take precedence over inventing separate visual assets.

Desktop composition: four 82 px rows, single selection, shared context line, three scope rows, fixed action footer. `390×720` viewport: modal 366×696 at (12,12), footer bottom 707, scrolling is inside the body. Reduced-motion spinner animation resolves to `none`. No extra format or backend capability is implied by the SVG icon treatment.

### Functionality and conflicts

- PNG 2× and PDF 3× use the current logical graph, including offscreen members of that graph. HTML retains the current layout, styling and current label visibility. Excel always contains all tracked flows in the current canvas, independent of graph filters.
- Actual USD filter changes graph summary to two accounts / one relation while Excel remains twelve tracked flows; the temporary filter was undone. Empty graph and empty tracked-flow conditions independently disable their relevant action.
- One captured case/canvas generation owns each task. Repeated submission and format changes while pending cannot create duplicates. Old responses, photo collection, file bodies, errors and callbacks cannot download into a newer case/canvas binding.
- PNG/PDF sampling locks close buttons, Escape and backdrop dismissal while the mutable Canvas is sampled. HTML photo preparation and file generation are hideable; closing and reopening keeps the same task. Hidden completion does not force the dialog open.
- Failure remains in the selected format and enables explicit retry. Original native network errors receive a short Chinese message. Error/busy/success status is outside the scroll body and always above the footer. `13-visible-error.png` confirms error bottom 779 before footer top 789; the actual retry downloaded successfully.
- RED cases retained: original HTML late error after A–B–A; backdrop dismissal during capture; footer clipping/overlaps in dynamic geometry. GREEN covers the public controller, real adapters, real shared dialog handler and dynamic geometry, not substituted business logic.

### Actual exports and restoration

All four formats were downloaded through the actual browser UI using the isolated synthetic runtime, with no business database or Provider access. `graph.html` rendered locally and its node was dragged (`04-html-before-drag.png` / `05-html-after-drag.png`). `tracked.xlsx` has two sheets, twelve flows and eight acquisition rows; identifiers and amounts remain text, no formulas. CNY eleven flows total `758000.00`, USD one flow `3500.00`.

`graph-final.png` is 1812×1640; `graph-final.pdf` contains one 2718×2460 image. Actual raster and PDF rendering both retain six accounts, five amount groups and the complete bottom account DEMO-0066 above the footer. Temporary export-only edge text relocation and measured label bounds prevent the observed collisions/cropping; saved node coordinates, styles and manual labels are not changed. The sampling zoom ceiling temporarily scales 4→8/12 and restores 4 on success, exception, cancellation and stale callbacks. Final PDF relative content coverage is within 1.5 percentage points of PNG, correcting the old ceiling-induced shrinkage.

Natural-window PNG and PDF operations restore the main Canvas. `23-natural-stable-before.png` and `24-natural-stable-after.png` have identical pixels across the entire Canvas region `(0,204)–(716,777)`, including labels, status, minimap and legend (`camera-pixel-verification.json`). A full-repaint hypothesis used while investigating forced-viewport captures was disproved and completely reverted; it is absent from the final source.

PDF retains the existing single-page raster backend. This local environment uses the existing 256-color fallback; no RGB-lossless, vector-PDF, or Windows GUI/DPI claim is made. Large/dense graphs may still have unavoidable text conflicts; the bounded relocation helper retains text and reports unresolved geometry rather than hiding money or changing graph facts.

### Verification

- Complete isolated synthetic suite: **480 passed**, one existing Starlette/httpx deprecation warning, 144.99 s (`final-regression.txt`). App, dialog behavior, geometry, tests and runner hashes stayed stable. The final status placement HTML/CSS change during that run is explicitly recorded in `status-placement-change-during-full.txt`.
- Final HTML/CSS candidate: **10 passed** in targeted UI, export, navigation and desktop-resource wrappers, before/after hashes identical (`final-status-placement-followup.txt`). Controller 23, legacy 122 and workflow 41 behavioral cases pass through their real wrappers.
- Export geometry: actual RED retained; six behavioral cases / one pytest wrapper GREEN. Actual PNG/PDF visual inspection separately completed.
- Final source hash equals the tested candidate after temporary investigation edits were reverted. Source syntax and whitespace checks pass. New geometry asset is included in the desktop resource closure; no packaging/release was performed.
- Existing G6 vendor, normal graph/layout/label/routing modules, persisted business facts, backend algorithms, import flow and other approved surfaces remain unchanged relative to this iteration's saved baseline.

P0/P1/P2 open issues: none within this export scope. P3: exact generated-image optical effects/icon glyphs, existing raster-PDF fallback, and density-dependent unavoidable conflicts as described above.

final result: passed


## 2026-10-17 — 案件管理图 1 生产落地

- Exact selection: result `exec-2202e73a-1815-4be8-a448-bd7a90291a03`, 1536×1024; selected source SHA256 `76174f84bd4dde4796ce298261e213ee607e8bd60c351d5ec6238750aa7ec23b`. Delivery: `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-case-management-delivery/`.
- Initial finding P2: first real panel was vertically centered at y236.5 rather than the selected workspace center at y268. Fixed top64px with low-height/narrow recovery. Post-fix panel x478/y268.5/580×551 versus source x478/y268/580×552.
- Source and actual were opened together in `comparison-full.png` and `comparison-panel.png` after actual unfocused idle capture. Full view is 1536×1024 at deviceScaleFactor1; focused comparison uses source580×552 and actual580×551 without stretching. Live imported-case suffix and visible graph differ from the generated background because these are real synthetic case records.
- Fonts/typography: retained existing UI family and fallback; 26px/600 title, 18px/600 action names, 14px descriptions and13px context. Labels and hints wrap naturally. Small-context optical weight is consistent with existing product controls.
- Spacing/layout:580px native modal, three direct rows, dividers, quiet red destructive row and fixed footer;12px dialog/6px controls use shared tokens. No card grid or new navigation. 400×440 has body-only vertical scroll and visible footer; no local horizontal overflow.
- Colors/tokens: surface-raised/border/text-primary/muted/accent/action/state/focus and required-label/required-asterisk reused; export SVG uses accent and delete uses state-danger. Flat shared surface replaces the ImageGen illumination artifact intentionally.
- Assets: existing Tabler file-plus/upload/file-export/trash/chevron/x/refresh SVGs; no generated raster text, custom icon drawing, new font dependency or duplicate icon set. File-plus retains the established new-document semantic; its library padding differs slightly from the mock paper silhouette (P3, accepted system consistency).
- Copy/content: actual current case, canvas, flow/account counts; package scope states saved DB records/canvases and excludes raw uploads; account drafts stay unsaved; default canvas can be named/saved before export. No invented remote sync/permissions/encryption claims.
- Interaction/accessibility: native modal top layer covers minimap and makes graph inert; focus returns, Enter/IME handling, Escape/backdrop, pending controls, real error/status live regions, default-case delete protection, required tokens and reduced-motion tested. Pending deletion blocks closure until its request settles; other writes retain their mutex when hidden.
- Functional issues fixed and covered: no loadCases reset after a mutation; explicit result Open with draft guard; late-context revocation; unknown writes reconcile read-only; real Blob download/error reporting; saving before export does not clear drafts on list GET failure; export retry does not repeat a successful canvas clone; completed export/delete reopen on fresh home state.
- Backend defects reproduced before repair: raw snapshot IDs copied without remap, invalid snapshot array shape accepted, fractional BIGINT IDs rounded by CAST; corrected with public regression coverage. Stale deleted-batch display references are filtered, geometry retained, format1/2 compatibility preserved. Storage faults no longer report duplicate-name400.
- Browser evidence: final screenshots17/18, narrow19/20/21, draft12, unknown13, retry14, pending delete15, default protection16; real exported/imported package checks in `browser-package-roundtrip.json`; detailed scope/limitations in `browser-verification.json`. Zero unhandled pageerror events; expected injected network faults separately recorded.
- Final stable candidate:514 pytest items passed,1 existing Starlette/httpx deprecation warning. New controller has33 behavioral cases inside its pytest wrapper.16 source/test hashes unchanged throughout final full run; API contract and packaged-resource closure passed. This is synthetic local Chrome/API evidence, not installed Windows validation.
- No outstanding P0/P1/P2. Accepted P3: library icon silhouette/padding and actual fixture name/background graph differ from raster reference.

final result: passed


## 2026-10-18 · 图 4 顶栏统一二级菜单

- Source visual truth: `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-toolbar-menu-design/design-4.png` (actual fourth displayed image, exec-480a670b-37a8-4bb7-b0fc-53a3d0fed2b7).
- Implementation screenshot: `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-toolbar-menu-delivery/menu.png`; companion `name-form.png`, `delete-confirm.png`, `menu-selected.png`.
- Viewport:1280×720 CSS px, reported DPR2; IAB screenshot API returns normalized1280×720 pixels. Reference1536×1024 pixels is an optically magnified desktop region, not a CSS viewport specification. Compare scoped menu, three command rows and trigger at matched optical scale; retain agreed real CSS248px width,40px rows,18px SVG,12px outer/6px row radius and6px gap. No claim of full background pixel identity.
- State: named synthetic canvas; canvas-management menu open with first command keyboard-focused. Full-view source and implementation were opened together in the same image comparison call; focused menu review used readable source menu(x362,y280,w426,h278) and actual menu(x131,y181.5,w248,h147). Background case/graph content and density differ intentionally; G6 presentation is outside this menu redesign.
- Fonts/typography: existing Segoe UI/Microsoft YaHei stack,14px rows,1.5 line-height; concise three command labels with ellipses. No new font. Optical source font enlargement is normalized to existing desktop controls.
- Spacing/layout: same button-attached flat command list; two standard rows, one separator and one danger row. No menu heading, close button, central modal or side drawer. Input/confirmation are separate compact native dialogs positioned at the same anchor. Keyboard focus ring is deliberately explicit and distinct from hover. Shared placement clamps/flips and scrolls within the viewport; synthetic geometry harness covers tiny windows; actual browser capture is1280×720.
- Colors/tokens: existing surface-raised(#1b2530),border(#2e3b4a),text-primary(#e8eef6),muted,accent,state-danger,focus-ring and selection-surface reused. New menu size/motion/elevation tokens reference the same system. Flat surface preserves existing product materials instead of ImageGen lighting artifacts.160ms entrance and reduced-motion rule.
- Assets: existing Tabler pencil/copy/trash/settings/chevron SVGs; all three command images loaded. No custom drawn icons, rasterized UI, new icon dependency or image placeholder.
- Copy/content: rename, save-as and delete use actual bound canvas; deletion confirmation states source flows/other canvases remain. Default canvas cannot rename/delete in UI or API. Name/shape operations, complete tracked sets, account drafts and independent case-export pre-save semantics retained.
- Interaction/accessibility: menu/menuitem/aria-expanded/aria-disabled; disabled rows focusable but inert; trigger keyboard open, arrows/Home/End, logical Tab, Escape return; SVG click invokes once. One menu open; ribbon handoff closes previous; first graph outside gesture consumed without graph state changes. Floating menu is body-level,z120 above minimap12; native forms in top layer.
- Business fixes and evidence: lock begins before name/draft await; captured case+canvas generations, late results isolated; default deletion backend guard reproduced RED then GREEN; save-as uses full snapshot and full tracked membership; delete retains original flows. Unknown writes never automatically retry; list-only recovery and explicit acknowledgement. Deleted current canvas is marked unavailable until explicit switch, including case-export pre-save. Rename success retains submitted name even if following list read fails. Departure writes hold native input/confirm surface until response, preventing new account drafts from being silently lost; pending same-canvas rename can close while write lock stays held.
- Browser verification: default protection; clone; rename+reload; blank create+source preservation; named save; delete confirmation/cancel; keyboard; full selection preserved on first graph outside click, later click selects; one-click handoff; Tab order; SVG/placement. Console log read returned zero entries. Exact scope in `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-toolbar-menu-delivery/browser-verification.json`.
- Automated validation:32 targeted pytest items passed; final shared-write availability follow-up passed3 wrappers (27 canvas controller/view,37 account/adapter and125 legacy behavior cases). Shared-menu10 scenarios and API canvas14 cases are included in the targeted run. API contract and desktop static-resource closure pass. Only existing Starlette/httpx deprecation warning. `candidate.json` records final product-source hashes.
- Findings/history: P1 default delete0 and duplicate-write hazards fixed; P2 stale publication, rename-refresh name loss, missing deleted canvas and pending-draft closure fixed with regressions. Initial broader run exposed outdated synthetic harness setup and missing desktop asset-list entries; setup now loads actual new controller, business assertions retained, resource list repaired. No actionable P0/P1/P2 remains.
- Accepted P3: optical generated background differs from actual synthetic G6 graph; stock library icon padding retained. Native form adds necessary input/confirmation states absent from the selected menu mock.
- Limits: no installed Windows/DPI test, no packaging/publishing or business-data access. Browser destructive-delete submission was not performed; delete semantics use disposable API fixtures. Network-fault/unknown/pending paths use deferred behavioral harnesses, not browser fault injection.

final result: passed


## 2026-10-18 · 数据工具图3与侧栏镜头联动

- Selected source: `fundstrace-data-tools-design/design-3.png`, Image Gen `exec-dad85911-ffed-4f80-acbb-13299492944f`, SHA256 `7c55fc76ccb5e1582d88715cdabe1906e0e072b304b694b6072827cbc6d3a129`. Exact displayed option3 was verified against display-order.json. Scope: source implementation only.
- Evidence root: `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-data-tools-delivery/`; review.html, browser-verification.json, adaptive-camera-pixel-verification.json and final-regression.txt.
- Visual comparison: exact1536×1024 reference and actual1280×720 ready state were opened together, then716×777 natural panel was inspected. Compare the button-attached file row, required labels, scope ledger, template and action footer. Actual420px native dialog/18px inset/12px radius/16px heading/13px body/12px metadata reuse product controls; the magnified generated desktop is not a CSS viewport spec. Width and an additional true format-conversion line accommodate real content. Stock list/download/x/check/refresh SVGs replace raster glyph details; generated lighting and Excel branding are not added.
- Unified surfaces, border, muted/primary/accent/state, required-label and asterisk tokens retained. Shared FundsToolbarMenu supplies3 SVG command rows, focus/keyboard behavior and placement. Real native top-layer form covers minimap; opening changes neither graph origin nor size:1280 viewport=(524.796875,204,755.203125,516),716 viewport=(390,204,326,573). Narrow panel has418px client/scroll width and visible34px footer. No node reflow or forced Fit on open.
- Import semantics: current-case account identities across all canvases, same-account ID replacement only, name optional/unmodified. No original transaction or tracked-set edits. Pick first, explicitly submit later;2 processed accounts/1 invalid/1 blank-account observed. Count labels describe actual processing. XLSX/CSV/TSV/TXT supported; missing XLS dependency is handled with clear conversion error before upload persistence. API fixtures cover first-valid duplicate, precision, case-wide/cross-case scope, transaction rollback/temp cleanup and deleted-case parse race.
- Draft/write conflicts: unsaved account draft prevents bulk overwrite without silent discard. Actual browser draft stayed intact and Save remained enabled. Independent per-network write leases survive drawer/session reset; account edits/crop, bulk identities, flow import, case writes/export, graph loading/layout/history ownership are guarded. Repeated click sends once; picker cancellation/same-file/stale callback and all success/error/count paths are case-generation scoped.
- Unknown writes retain lock, never automatically retry, and can only read current authoritative facts and explicitly finish reconciliation. A fresh reader may apply current facts without claiming the original unknown operation succeeded. Detached/late A–B–A replies cannot publish into new UI. Reciprocal flow-import leases are independent of its visible session; read-only progress probes coalesce and only terminal matching receipts release the corresponding lease.
- Identity completion updates node metadata/labels in place; preserves geometry, styles, manual paths, camera, selection, filters and LOD. Demo has a separate confirmation/range; actual empty-case run added1866,0 duplicates,0 skipped, leaving0 graph nodes/edges and1866 query rows. Template download succeeded.
- Latest user clarification supersedes the earlier stationary-screen interpretation: explicit sidebar collapse composes the existing layout using camera zoom/pan. Complete content grows with viewport-width ratio, constrained by actual dimensions,24px padding, overlays and maxZoom. Right overflow keeps reading scale and shifts into freed space without forcing a dense graph Fit or sacrificing content that can remain visible. Only actual drawn bounds decide completeness; canonical hidden-label envelope limits growth. Nodes/edges/facts remain unchanged.
- Shared220ms CSS geometry drives camera frames through existing ResizeObserver; no independent animation clock/layout job. Rapid reverse retains one baseline, uninterrupted expansion is reversible, reduced motion settles immediately. Manual pan/zoom/fit/find, width/window/toolbar changes, layout/rebuild/binding changes interrupt composition. Flush pending geometry before user camera commands. Group-drag preview ends before coordinate-origin changes while keeping selection.
- Export borrows camera ownership: both export→sidebar and sidebar-midway→export retain the pending journey; restoration precedes resume. History uses current affine × inverse(captured affine), including zoom, and remains compatible with old offset-only snapshots; covers undo/redo, async membership and failed rollback. Sidebar itself creates no history item.
- P2 found in browser: hidden long ID/LOD labels made a fully visible graph take the overflow path. Fixed with separate visible/canonical bounds and0.5px Fit tolerance; public adapter regression includes hidden labels, clipped nodes and complete curved paths. P2 found in transition: pinned Canvas changeSize clears bitmap while paint queues next rAF, causing a blank frame. The current-instance, non-export, G6 4.8.19 Canvas adapter checks both methods then calls _clearFrame/_drawAll synchronously after size/matrix updates. Pinned vendor remains byte-identical and hash verification passes. Future renderer updates must revalidate this narrow private seam.
- Actual camera evidence:326→716px,6 node bodies retained, span grows approximately716/326; re-expansion body pixels exactly match the starting sample. Overflow sample expands3 visible bodies to6, with original matched body heights unchanged; rapid reverse endpoint matches.6 collapse frames plus overflow/reversal samples retain visible node bodies. This is observed local UI evidence, not a60fps/dense-graph benchmark. Earlier sidebar-* fixed-screen evidence is retained only as superseded history; adaptive-fit-collapsing.png records the pre-fix blank frame.
- Final scoped regression:85 pytest items passed in49.97s,1 existing Starlette/httpx deprecation warning; includes real app adapters,23 camera/10 shell/44 bulk-controller/52 workflow/41 account behavior cases and10 identity API cases through their wrappers. API contracts current; pinned G6 hash verified; desktop resource closure includes both new scripts; JavaScript syntax and git diff whitespace checks passed. Network/case/write races are deterministic harness evidence, not browser fault injection.
- No remaining actionable P0/P1/P2 within this scope after source review and browser checks. Accepted P3: source optical magnification/stock SVG padding and real synthetic names/background. No business-data access, installed Windows/DPI test, packaging, Git write or publication.

final result: passed


## 2026-10-18 · 主账号管理 · 修订图 2

**Source visual truth**: `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-main-accounts-revision/design-2.png`，display order 第 2 个结果 `exec-4793e7f3-eee6-431a-89ea-e4e0b75a6635`。源图 SHA256 `abaab30b59547a4f1cc7fc4d1f43008261970e93cec2f3f13b324c0ea0333c49`。

**Implementation**: `http://127.0.0.1:56354/?case_id=2`，仅一次性合成数据。截图 `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-main-accounts-delivery/main-accounts-final-menu.png`。状态：全部 3、自动 2、手动 1，第二行菜单展开。

**Viewport and normalization**: 源图为生成的 1536×1024 位图，未提供 CSS viewport 或 DPR；实现为实测 1280×720 CSS px、1280×720 截图像素。全图按同宽 1280 比较，源图缩为 1280×853；不同高度仅作为环境构图上下文，不声称整页逐像素克隆。聚焦比较源图账户浮层及菜单裁剪，按约 1.2 的原图视觉字号比例归一；实现沿用现有 420px 浮层、16px 标题、13px 表格、12px 账号及辅助文字。原型中的账号名称来自实际合成数据。独立响应检查使用真实产品 iframe，CSS viewport 716×777，截图包含外层 32px 标题和滚动上下文；不把外层 1280×720 截图误称为产品窗口尺寸。

**Comparison evidence**:

- `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-main-accounts-delivery/comparison-before-full.png`、`comparison-before-focused.png`：第一次比较，blocked。
- `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-main-accounts-delivery/comparison-final-full.png`、`comparison-final-focused.png`：修复后，完整环境和浮层细节放在同一图片内对照并已打开检查。
- `/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-main-accounts-delivery/narrow-716.png`、`narrow-716-footer.png`：716px 产品窗口，真实键盘打开，头部、三行列表、完整底部动作可见，浮层覆盖缩略图。

**Findings and iteration history**:

1. [P2, fixed] 初版宽 456px、顶部 y177，浮层遮到工具带分组标题；筛选标签过度平均铺满，行密度偏松。改为现有 420px 浮层宽度，从 ribbon 当前底边生长（实测 y209），标签 100px、行 padding 8px，沿用共享 placement、token 与动画。修复后实测 420×484.79px；参考构图中的浮层高度归一后约 463px，额外留白用于避免菜单遮盖范围说明，属于有意的交互留白。
2. [P2, fixed] 行菜单与范围说明交叠。底部说明留白校准，最终菜单底边约 y600、说明文字约 y610，互不遮盖。
3. [P1, fixed] 浏览器实际点击发现 toolbar-menu 全局处理器拦截复用样式的本地行菜单。共享组件现在只处理自己登记的面板；真实浏览器已执行转手动与取消，并补充回归验证未打开的顶栏菜单仍不能触发。
4. [Correctness, fixed] 抽屉快捷添加可能被模糊查询前 30 条遮住。改走 case-bound 完整账号精确候选查询，覆盖 35 个先排序包含匹配项的回归。

**Required fidelity surfaces**:

- Typography: 保留产品 Segoe UI / Microsoft YaHei / sans-serif 和账号等宽字体，标题、主体、辅助文字层级清晰；长账号换行，既有名称保持真实。
- Spacing/layout: 统一 18px 浮层内边距、现有圆角、边框和阴影；本体悬浮而非占用画布布局。右边界空间不足时共享 placement 将菜单收回可视范围，这是对原图越出面板菜单的有意适配。
- Colors/tokens: 复用 surface-raised `#1b2530`、border `#2e3b4a`、accent `#367cff`、muted、selection-surface、state-danger、focus-ring；实测浮层背景 rgb(27,37,48)。来源有文字和 SVG，不依赖颜色单独辨识。
- Images/icons: 全部使用现有 Tabler 本地 SVG，未引入截图替代 UI、手绘图标或新栅格资产。共享菜单没有原图的装饰性尖角，维持已批准的统一组件。
- Copy/content: 自动识别/手动设置、全部/自动/手动数量、精确取消范围、自动识别范围与真实回执一致。原图之外补齐搜索、空态、确认、读取失败、工作中、未知回执核对等必要状态。

**Interactions and validation**:

- 浏览器实际执行：搜索添加；真实 CSV 单账户导入后手动来源保留；自动→手动；再次识别新增 0 / 已有自动 1 / 保留手动 2；筛选；清除自动后手动保留；搜索并添加画布外账号；逐账号取消后其余三账号保留；菜单焦点、Escape 关闭恢复 `btnMainActions`；716px 窄窗口通过键盘找到入口、打开及检查完整底部。
- 控制台本次检查 warn/error 返回 `[]`，见 `browser-console.json`。
- 170 项定向 pytest 通过，包含真实 JS harness；详见 `verification-final.txt`。API 契约检查、G6 锁定补丁校验、diff 空白检查通过。
- 覆盖人工来源在导入/识别中的持久化、同名多账号精确取消、同账号跨案件、全部流水账号分页/全部户名别名、重复提交、未知结果只读核对、不自动重发、切案 A–B–A 迟到响应、与账户编辑/导入/画布写入互斥、角色刷新保留坐标/相机/选择/筛选/追踪/样式/历史。

**Implementation checklist**: 已完成选定图 2、统一 token/SVG/菜单复用、完整入口和业务写入、必要状态及互斥、合成浏览器验收、当前候选回归。未进行发布或打包；以上是源码和本地浏览器证据。

**Follow-up polish**: 无阻塞项。系统中文字体的光学差异随平台变化；Windows GUI/DPI 不在本次本地浏览器证据范围。

final result: passed


---

# 2026-10-18 关系筛选 · 图 2

final result: passed

## 对照证据

- 用户澄清选定图 2，最初“图 5”不作为方案编号。
- 源图：/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-relation-filter-design/design-2.png。
- 最终实现截图：同交付目录 final-draft.png；源图与实现都是 1536 × 1024 px、浏览器 CSS 视口 1536 × 1024、截图密度 1。源图为生成概念图，未缩放真实界面截图。
- full-view comparison：同一次图像输入打开源图和 final-draft.png，状态均为 CNY 0.00–0.10 待应用、预计 3 条关系/5 个账户，底图实际仍 5 条关系/6 个账户。
- focused comparison：review.html 顶部的双列原尺寸局部裁切，浏览器截图 focused-comparison.png；源图裁切起点(18,206)、实现(12,181.5)，不重绘原始图像。
- 真实语义核对：applied-desktop.png、range-error.png、zero-results.png、account-draft-blocked.png；完整 DOM/几何与控制台证据 browser-evidence.json。
- 大数额外需求：独立真实组件 fixture（large-count-fixture.html）仅返回合成计数字符串，不读写案件；large-count-desktop.png 为 1536 × 1024，large-count-narrow.png 为 560 × 800。

## 五项视觉检查

1. 字体：复用 Segoe UI/Microsoft YaHei；保留现有产品密度，标题17、标签12、输入14、统计30px。长数字分别调整为24/20px，使用tabular-nums，完整千分位、title和aria文本。与生成图整体较大的光学比例差异为沿用现有商业工具密度的明确取舍。
2. 布局：图2的条件/预计双列、单一主操作与分割线保留；增加已应用摘要、待应用状态及真实范围说明。统计数字/单位改为纵向，便于大数。620px浮层通过同一菜单portal固定定位，开启前后查询区和画布矩形完全相同；不强制fit，不改节点几何。<=620px改上下布局。
3. 颜色/token：面材、文本、语义色、圆角、阴影、焦点和动效均来自现有CSS变量；未添加新色板。共享工具菜单表单variant复用原组件而非新增独立外层。
4. 图像/SVG：复用产品filter/chevron-down/x/refresh.svg；源图清除按钮的垃圾桶改为重置SVG，避免删除含义。没有新图标集或位图化UI。
5. 文案：明确当前画布已追踪有向关系的单币种累计金额、包含边界/空白不限、保留其他范围/左侧查询。单位为条关系/个账户。未把生成图中的查询区下移或装饰圆点转成实现要求。

## 迭代记录与发现

- 首轮大数压力视图出现数字中间断行，不利于核对（P2）。修复为长数字字号分级，并在千分组之间添加软换行机会；最终窄屏截图保留全部数字且不会在三位组内部断行，panel及数字scrollWidth未超过clientWidth。
- 打包资源检查初次发现relation-filter.js未登记（功能P1），已补STATIC_NAMES并通过资源闭包检查；未执行正式打包。
- 最终同屏完整/局部比较无可操作的P0/P1/P2问题。额外的已应用摘要、完整显示范围与纵向统计单位属于用户后续要求；生成图的背景位移、垃圾桶和黄色点不属于产品要求。

## 交互与验证范围

真实浏览器完成：草稿预计3/5而底图仍5/6、显式应用后实际3/5、反向金额范围就近拒绝、关闭丢弃草稿、0结果与清除恢复、左侧9笔流水保持、撤销恢复、案件返回后独立状态、未保存账户身份证草稿时禁用应用、X关闭返回触发器。临时身份证输入为合成字符串并已恢复空白，没有提交资料写入。

自动回归覆盖：实际组合范围预计/提交、精确超安全整数金额、仅清关系条件、相机与tracked/raw保持、重复提交与单条历史、busy、案件/画布A-B-A迟到预计/提交、IME/Enter、原生输入撤销与快捷键隔离、完整画布关闭手势；其中共享form新增测试先RED后GREEN。

66项针对性pytest通过（含Node行为套件）；语法与差异空白检查通过。真实浏览器error/warn为空。仅一个既有TestClient依赖弃用警告，非产品失败。

## 验收清单

- [x] 图2的信息组织与统一菜单/颜色/SVG
- [x] 浮层不挤动查询区或画布
- [x] 已应用与待应用隔离、范围/币种/精确金额/清除语义
- [x] 大数字完整显示及窄屏恢复
- [x] 重复/忙态/账户草稿/切换绑定与键盘冲突保护
- [x] 针对性回归、浏览器核对及静态资源依赖闭包

未执行正式打包、发布或Windows安装包GUI验收；本结论对应当前本地源码与一次性合成浏览器。


## 2026-10-18 连线回头弧与箭头状态修复

本轮截图缺陷验收通过；不将极长文字压力场景声明为全图零碰撞。下一处“邻域追踪”仅提供四张概念设计，尚未实施。

- 同坐标实际G6对照：自动控制点从固定屏幕右侧改为局部方向短弧，右上回头弧消失。节点坐标、尺寸和缩放固定；金额/账号采用合成数据。正常六星场景七类实际碰撞均为0，加入反向关系后箭头分道。
- 箭头状态：带金额标签可见路径副本的有效箭头，旧版禁用后5，新版0，恢复5，加入反向关系6。明确区分不可见的空path缓存与实际画出的残箭头。
- 实际完整应用：长账号、超过安全整数的金额字符串保持。最终实际edgeNodeLabel 4→2、edgeLabel 1→0、labelOverlap 2→1、labelNode 1→0；其他类别没有增加，也没有新增冲突对象。仍有2处路径穿文字及1处金额/账号文字重叠，产品保留“标签空间不足”提示。本轮不截断文字或移动节点掩盖这个边界。
- 手势：实际拖动节点后，撤销和重做精确恢复节点及控制点，相机不动；没有弹出账户抽屉。路由只在合法移动提交后规划一次，与节点移动进入同一历史。单击、取消、50节点预览和异常回归覆盖；超过10节点光圈静态规则未修改。
- HTML：当前候选真实自包含导出使用同源路由/质量模块；旧screen-right迁移后五个箭头清楚，拖动仍可用。部分拓扑、手工路径与初始已接纳障碍路径受保护；没有通过计数抵消转移冲突。实际浏览器error/warn为空。
- 回归：69项定向pytest通过（内含Node行为集合）；几何与真实质量模块独立82项通过。语法、差异空白及固定G6补丁指纹通过。一个既有Starlette/httpx弃用警告。未正式打包/发布，不代表Windows安装包GUI或大图性能计时验收。

证据：`/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-edge-route-repair/` 中 review.html、research.md、validation.md、real-g6-final.json、full-app-final-diagnostic.json、drag-browser.json、export-legacy-browser.png。生成设计参考与真实回归截图分开存放。


# 2026-10-18 邻域追踪图2与明确搜索定位

final result: passed

- 源图：fundstrace-neighborhood-design/design-2.png，交付目录selected.png。实现：fundstrace-neighborhood-delivery/final-draft.png；均1536×1024、CSS视口一致，当前中心000GRAPH-00001双向3账户2关系，草稿000GRAPH-00003出向预计2/1。已同一图像输入打开两图进行完整比较，review.html另提供原始比例局部对照。
- 保留图2两列、锚点浮层、统一token/SVG、当前/待应用状态及单一主操作。沿用现有商业工具密度和角色色；概念图“预计新增”修正为显示规模，不修改背景数据匹配图稿。
- 首轮P2：鼠标hover覆盖方向蓝色选中态；已补选中hover规则。收紧头部/行间距，使实际浮层约940×400。复验蓝色在hover/选中/禁用状态正确，窄窗单列且内部滚动、无横向溢出。长账号完整换行、千分位计数按分组提供换行点。
- 实际搜索发现大图全览低倍率的目标难读、LOD可能隐藏目标标签；补显式可读缩放和临时标签优先级，保持选中/几何/事实/历史独立。真实G6报告目标完整可读、41个不同矩阵平滑收敛、坐标/tracked/范围/选中保持；取消与减少动态效果有独立回归。
- 浏览器核对浮层不挤画布、预计不提交、范围外提示不清条件、实际应用/撤销/重做、流水查询不变、主账号定位与添加分离、多结果选择，以及未保存合成资料草稿阻止邻域应用。合成草稿已恢复，未提交资料。最终error/warn为空。
- 本轮19个定向pytest入口完成验证，受影响路径在后续改动后复验；8份源码语法、定向空白、资源闭包和固定G6指纹通过。具体轮次、限制与真实相机JSON见交付目录validation.md。
- 最终完整与局部对照无本轮可操作P0/P1/P2。未正式打包/发布，不代表Windows安装包GUI/DPI或大图性能验收。

交付证据：/Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-neighborhood-delivery/。


## 查找定位 · 图 1 落地验收（2026-10-18）

- source visual truth: /Users/sun/.codex/generated_images/01a147c6-5ec9-7781-8a18-d09c37f8532e/exec-f927c126-74cb-47ea-96a9-e0a30e5cf007.png
- implementation screenshot: /Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-find-delivery/final-located.png
- viewport: 1536 × 1024 CSS px; source/implementation: 1536 × 1024 px, density 1; no density normalization.
- state: 常用 → 查找定位，查询“合成节点”，10 个匹配，合成节点 1 定位成功，未改变选择。概念示例为 1–10，真实合成夹具为 0–9；保留真实账号及顺序。
- full-view comparison evidence: /Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-find-delivery/comparison-final.png（同一张图左侧选定图稿，右侧实现）。
- focused region comparison evidence: /Users/sun/.codex/visualizations/2026/10/17/01a147c6-5ec9-7781-8a18-d09c37f8532e/fundstrace-find-delivery/detail-final.png，原比例检查搜索框、结果行、完整账号、当前行与操作区。

### 发现与修正历史

1. [P2，已修] 第一版操作区按钮宽度不足、账号偏小，且 hover 优先级覆盖了已定位行的蓝色底色。证据 comparison-before.png、detail-before.png。修正为 120px 主要操作、13px 完整等宽账号，并明确已定位行 hover/focus 颜色优先级。再次实际捕获并对照 detail-final.png；当前行底色读取为 color(srgb 0.120706 0.192863 0.301882)。
2. [P1，已修] 1024 × 768 下完整列表遮挡定位空间。小窗口结果区独立滚动，优先留出图谱阅读空间；small-window.png 显示完整目标、户名和账号。
3. [P1，已修] 极小概览的临时标签偏移会放大定位包络，导致本可阅读的节点被过度缩小。单节点查找按目标阅读比例测量本体及完整文字，不写实时节点几何；公共行为回归覆盖巨大临时标签包围框和 0.12 初始缩放。

### 五项视觉核对

- Fonts/typography: 复用产品 Segoe UI / Microsoft YaHei 字体栈及等宽账号，18px 标题、14px 户名、13px 账号，完整换行；系统字体渲染与概念图的细微字形差异为 P3。
- Spacing/layout rhythm: 600px 单列锚定浮层，12px/6px 圆角、18–20px 内容边距、64px 结果行、42px 操作按钮；成功状态增加一行，面板可随结果数量收缩；不挤动画布。该状态行与合成数据顺序差异是完整功能所需。
- Colors/tokens: 统一 surface-raised/border/selection-surface/action-fill/focus-ring/state-success；使用产品现有纯色按钮，概念稿局部渐变不引入另一套色值。当前定位行的 hover 不再丢失状态色。
- Image quality/assets: 复用现有 search/x/target SVG，无新光栅资产、无替代图标；到达强调复用 graph-selection-ring，但与选中状态分离，有限两次、1250ms 清理，减少动态效果设置下保持静态。
- Copy/content: 户名、完整字符串账号、当前显示范围、匹配数量与新增数量均来自当前绑定；成功、空结果和冲突提示准确。输入/hover/箭头导航不移动画布，多结果不自动全选。

### 实际浏览器交互

内置浏览器 + 本地 disposable synthetic preview（http://127.0.0.1:56357/?case_id=2）；1536 × 1024、1024 × 768。
单项定位、完整账号唯一搜索、定位全部、快速点击结果 2 → 3 最后落在 3、显式选中、未保存身份资料下定位与选择冲突、空结果、定位期间切案件并 A–B–A 返回清空旧查询、Escape 返回 btnFindNode。草稿填入后已恢复为空，未提交资料；仅使用合成数据。
控制台 error/warn 查询为空。迟到响应、IME、重复提交、offscreen/filtered、更多结果、相机取消和 >10 目标静态标签策略另有行为回归；不把单元证据冒认为逐项浏览器证据。

### 实施检查

- [x] 来源与实现以同一输入对照；P0/P1/P2 已修正并复拍。
- [x] 保留当前选择、编辑草稿、源数据和历史；显式选择才走资料保护。
- [x] 320ms 共享相机、无遮挡区域、完整标签、减少动态效果设置和中断所有权。
- [x] Node 386/386 集成回归、33 项 finder 控制器回归；相关工具栏/相机/邻域/资源检查通过。
- [x] G6 固定文件哈希未变；资源闭包包含 node-finder.js。未打包、未提交或发布。

final result: passed

## 2026-10-22 · 三种工作区视图

用户确认方案1／4是同一布局和详情状态。本轮在原主页增加“工作区视图”：标准、专注、上下分栏；原节点“布局算法／重新布局”保持独立。

- 复用原查询、真实七列表格和图谱；分栏不进入全览的即时上图模式。详情在三态独立；模式切换不调用布局、适应全图、追踪写入或历史命令。
- 收起后保留四个Tab、案件／画布、保存／撤销／重做、视图及详情入口。Chrome实测工具区66px（顶行35px＋快捷带31px），快捷带内容1182.875px，低于既有1280px构成宽度；未新增一层顶栏。
- 修复实际浏览器窄表→宽分栏→窄表造成横滚归零；在外壳改变前捕获模式位置，结束后恢复，切案／切画布清旧快照。查询草稿和锚点保留。全览关闭后回到原tab-search、七列和“选为追踪锚点”语义。
- 修复分栏最大高度不随功能区展开收敛：实际浏览器工具区展开后底区408px／图谱140px，再收起恢复底区490px／图谱140px；aria上限同步。筛选使用完整内容行和外层滚动，展开后queryBottom与bulkTop相接，无区域重叠。
- 全量入口：`.venv/bin/python scripts/test.py -q --tb=short`，987 passed、108 subtests passed；最终CSS收敛后6个相关Python入口再验15 passed。新增shell 26／Ribbon及命令50／table 23项Node行为回归包含尺寸夹取、模式回切、原生滚动夹取模拟、全览及绑定失效；JavaScript语法与diff空白检查通过。
- Chrome使用一次性合成数据库，视口1512×728；完整金额和CNY／USD来自合成夹具。分栏截图曾显式点击“适应画布”方便阅读，模式本身不触发该动作。截图位于本地交付目录workspace-views的01-standard、02-focus、03-split、04-standard-details.png。

交付目录：`/Users/sun/.codex/visualizations/2026/10/22/01a162c9-3f33-7a63-9750-e3fb5f7d63bb/workspace-views/`。本轮没有修改G6绘制／布局／业务事实、API、React或桌面原生宿主；没有执行正式打包、发布或Windows原生GUI／DPI验收。
