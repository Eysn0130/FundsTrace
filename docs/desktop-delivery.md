# FundsTrace 独立桌面交付

## v0.1.x 发布规则

FundsTrace v0.1.x 正常启动**无需公钥或授权码**。每个实际版本 Tag 对应 GitHub Release，附 Windows x64 NSIS `.exe`，从 [Releases](https://github.com/Eysn0130/FundsTrace/releases/latest) 下载并安装。原生构建与安装结果以对应版本的候选清单和发布报告为准；下方历史记录仅对应各自源码。安装包未签名，不能声称完成代码签名或 Windows 全面兼容验收。

新版 Release 下载区仅上传 Windows `.exe`；Source code ZIP／tar.gz 由 GitHub 自动提供。验证报告、摘要清单和合成截图保存到本仓库专用证据分支 `codex/release-evidence` 的 `releases/<tag>/`；简短 Release 正文保留用户更新说明、兼容性提示和不可变归档链接，安装包名称、字节数、SHA-256 及验证明细在归档清单中核对。整理既有 Release 时，先核对长期归档，再移除非 EXE 附件；原安装包及版本 Tag 保持不变。

v0.1.6 已按此方式整理：[长期验证归档](https://github.com/Eysn0130/FundsTrace/tree/31c83a6baee8ff0a818f1c427babb526d3422a25/releases/v0.1.6) 保留原 12 个验证附件及整理前 Release 元数据。原 EXE 附件仍为 160013027 字节、SHA-256 `fc9c6513079b0ac456b0dabe47b0f8a175eaf9b3b62eceda77a8abddaf6776aa`，Tag 仍指向 `835d526f08901c03fb1d55c48791faeb8d4bea48`；整理没有重新构建或上传安装包。

Python 已移除产品许可门卫，`/api/license/info` 明确返回 `activation_required:false`，如实保留历史 `activated` 状态；`/license` 可直接进入系统。没有公钥、没有授权文件或存在损坏的历史授权都不阻塞业务，不生成测试授权来解锁。FT2／HMAC 验签接口、独立 `desktop/issuer/` 工具和已有材料保留，见 [历史授权维护](activation-history.md)。已发布 alpha.3 不随源码改变。

## 安装、升级与卸载

- 保留 `com.eysn.fundstrace`、FundsTrace 产品名、appId 派生的注册表键及**当前用户**安装范围。标准 NSIS 引导显示已安装版本；安装目录可以选择。使用新版 EXE 覆盖安装，由同一注册信息找到旧卸载器；不另建 Analytix 安装身份。
- 安装替换、静默／交互卸载前，检查选定及注册的旧安装目录。仅向该目录中精确 `FundsTrace.exe` 路径的窗口请求正常关闭，等待所属进程退出；Main 通过 stdin EOF 停止自己的 Python 后端。占用或检查失败时交互 Retry／Cancel，静默返回非零；不按 Python／Electron 名称强杀。
- Windows“已安装的应用”和程序目录提供卸载入口。`deleteAppDataOnUninstall:false`，默认保留 `%APPDATA%\FundsTrace` 中的案件、上传、设置及历史授权。不要把用户数据放进安装目录；卸载后重装会继续使用同一数据目录。删除真实案件须由用户另行明确操作。
- 顶部更新入口支持显式检查、版本说明、用户确认下载及重启安装；普通退出不会自动安装。使用 FundsTrace 独立 HTTPS 更新记录和安装包完整性检查，详见 [国内下载与应用内更新](domestic-downloads.md)。旧版也可从同项目 Release 或官网下载 EXE 覆盖安装；不套用 Analytix 的更新源。

[installer.nsh](../desktop/installer.nsh) 和 [installer-processes.ps1](../desktop/installer-processes.ps1) 按需适配 Analytix 安装根进程范围和生命周期设计，使用 FundsTrace 自身身份、per-user 范围和正常关窗策略；未引入其大型 React＋WebView2／WinForms 引导器。来源基于只读 commit `c7d2a370855a8d371c61c32cc2422515b9404ef2` 的 [NSIS hook](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/build/standard-win-installer.nsh)、[原生引导器](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/installer/winforms-bootstrapper/AnalytixWinFormsBootstrapper.cs) 和 [更新器](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/gui-updater.ts)。Apache-2.0 版权、修改通知及许可证随包保留；本轮不修改 Analytix。

## 运行与数据边界

[desktop/main.cjs](../desktop/main.cjs) 负责窗口、受限导航／下载和自己创建的服务。受 sandbox 和 contextIsolation 保护的 [preload](../desktop/preload.cjs) 暴露展示偏好 get/set/remove、固定更新动作及状态订阅，并仅转发固定的工作台 `ready`／`failed` 单向通知；Main 校验窗口归属、主框架及当前精确 origin，只将案件选择、线型、标签、证件／照片显示开关写入 `userData/preferences.json`。随机端口和临时 session 不变；不持久化 token、流水、快照或任意 renderer 键，不提供通用 IPC。G6 `/` 保留完整业务，`/analysis` 为分析，`/workspace/` 为共享 React 试点。业务数据在 Electron 自身 `userData/data`，不写安装资源，不打开 Analytix 数据根。保留单实例，窗口尺寸以当前显示器的 DIP 工作区为准。

[desktop_backend.py](../desktop_backend.py) 从 stdin 读取严格的 `{protocol:1, token, dataDir, synthetic}` 后才导入应用。随机 token 只经父进程管道和 Main 持有的精确 origin 请求头传递，不进入 URL、argv、renderer、日志或导出。后端绑定 `127.0.0.1:0`，Main 健康检查后才加载页面；冻结后端拒绝 source-only synthetic 模式。

桌面认证仍覆盖页面、API、静态资源，核对 token、精确 Host、Origin／Fetch Metadata，并设置 CSP／no-store。独立 session 拒绝未知网络、导航、窗口和权限；renderer 保持 sandbox、contextIsolation、webSecurity，关闭 nodeIntegration。免激活不削弱本地服务归属认证。[Electron 安全规则](https://www.electronjs.org/docs/latest/tutorial/security)

正常退出走 Main → stdin EOF → 后端关闭，超时只终止所持句柄的归属子进程。服务崩溃显示故障，不自动重放导入。持久任务恢复仍由 Python 服务负责；窗口退出 hook、普通案件崩溃重开与导入进行中恢复是不同证据。[app 生命周期](https://www.electronjs.org/docs/latest/api/app)

下载使用原生保存对话框，处理取消／中断，拒绝任意远程 URL；服务生成成功和磁盘落盘完成分别处理，不自动打开下载 HTML。[DownloadItem](https://www.electronjs.org/docs/latest/api/download-item)

## 启动显示与窗口尺寸

[desktop/startup.cjs](../desktop/startup.cjs) 在业务窗口上方创建独立、透明的本地 `WebContentsView`：先显示启动层，再在下方准备后端与真实工作台。启动层使用独立受限 session，页面资源请求仅允许本地 `startup/index.html`、`startup/style.css`、`startup/renderer.js` 和 `assets/icon.png` 四项；不接收后端 origin、token 或业务数据，不使用截图或模拟案件。它保留 sandbox、contextIsolation 和 CSP，禁止联网、权限、下载及新窗口；[专用 preload](../desktop/startup/preload.cjs) 只接收就绪通知并报告淡出完成。完成或关窗时显式关闭该层的 `webContents`，避免仅移除 View 后残留 renderer。

[启动画面](../desktop/startup/index.html) 使用原版 176px Logo、46px／600 字重的“循迹”和下排 21px／500 字重的 FundsTrace，保留副标题、原 A 扫光与琥珀呼吸。宽 360px 的品牌组通常按固定尺寸居中，加载反馈区固定高度；只有可用客户区不足时才整体等比缩小，按四周 24px 留白计算。实际 resize 只调整该比例，不重置时钟或淡出透明度。

常规动效先停留 1400ms，再于持续加载期间将深色遮罩从不透明缓慢降至 60%，到 6500ms 最多显露下方真实工作台的 40%；这只是等待装饰，不代表启动完成。加载反馈为不定进度，不显示虚构百分比。初始案件、统计、图谱数据和查询列表完成后，业务页才发出 `ready`；后端健康或文档加载结束均不能代替这个信号。该契约不承诺异步 G6 布局已完成，本轮不改变图渲染。真实就绪后立即从当前画面开始 1100ms 的统一淡出，Logo、字标、加载反馈和遮罩一同退出；减少动态效果时使用 140ms 收尾，不强制等待演示时长。初始化失效或超时会安全失败，准备中关闭仍清理本次所属窗口、启动层和服务，不输出 token 或内部异常。

[窗口策略](../desktop/host-policy.cjs) 默认按工作区四周 32 DIP 留白计算，默认创建尺寸上限为 1440×940，常规最小值为 960×640；小工作区会收紧窗口及最小值，使其落在工作区内。这是默认创建规则，用户仍可调整窗口大小。工作区坐标和窗口尺寸均使用 DIP，不乘 `devicePixelRatio`；启动层在实际客户区内居中，避免将原生边框和菜单高度当作内容空间。

## Windows 10／11 安装与启动边界

当前目标是 Windows 10／11 **x64**。锁定的 [Electron 44.7.2](https://github.com/electron/electron/blob/v44.7.2/README.md#platform-support) 支持 Windows 10 及以后，Electron 44 已移除 Windows 32 位；本仓库不发布 32 位或原生 ARM64 包，也不凭上游声明推断最低 Win10 build 或任意机器兼容。

| 链路 | 本项目处理与实际验证边界 |
| --- | --- |
| 下载与安装 | 单个 NSIS EXE，当前用户范围，不要求另装 Python、配置公钥或授权码；使用相同安装身份覆盖升级。Windows hosted runner 是管理员且关闭 UAC，因此不能用 CI 证明普通无管理员用户的全部交互路径。 |
| 安全系统拦截 | 当前仍未签名。SmartScreen 可能告警；Win11 Smart App Control 还可能阻止没有信誉的未签名安装器或 backend。它与业务激活无关，不要求用户关闭系统防护来获得通过标记。签名和信誉需独立处理。 |
| 启动反馈 | 先显示不含业务数据的受限本地启动层，后台准备真实工作台；初始案件、图谱数据和查询完成后统一淡出进入业务。准备中关闭会取消并清理本次归属服务，二次双击聚焦同一窗口；初始化失效或超时给出安全错误说明。 |
| 首次报告 | 普通启动、案件和分析入口不再提前初始化 Matplotlib；首次报告才加载。实际报告附件仍由源码回归和包内请求验证，不通过删除依赖制造启动收益。 |
| 体积 | 保留 Python onedir、Electron asar 和宿主文件白名单；Electron 语言资源保留 en-US、zh-CN、zh-TW。构建报告区分安装器字节、安装后字节、backend／语言资源与最大文件，不凭依赖名称裁剪动态模块。 |
| 运行中升级 | 只向已核对路径的本应用请求正常关闭；疑似本应用进程路径不可读时返回检查失败，不能静默当空闲或按名称强杀。 |
| 安装后启用 | 自动链按 `desktop/upgrade-baseline.json` 固定旧版、目标版、旧包摘要和字节数；由旧安装版后端通过公开 API 创建合成案件、金额、画布和追踪，再升级到当前候选。比较原数据、旧案件包迁移和原件摘要，并启动真正的 FundsTrace.exe 检查业务 DOM、快捷键、免激活、单实例、正常关闭和端口释放，再卸载回读合成数据。实际执行结果绑定候选 SHA 记录。 |

安装后 EXE 烟测仅在 Actions 专用合成用户运行，以 Electron 原生 loopback 调试接口观察自身页面；不增加生产 IPC、测试开关或共享授权。安全报告只含检查名与耗时，可保存的图片是 renderer 表面截图。它扩大了“直接启动 backend”的旧证据，但仍不是 Win10／11 普通账户、SmartScreen／SAC、断网首次使用、100%／150%／200% DPI、触屏或原生保存取消的实机验收。`guiAcceptance:false` 等限制继续保留。

依据：[Windows Server 2022 runner](https://github.com/actions/runner-images/blob/main/images/windows/Windows2022-Readme.md)、[hosted runner 管理员权限](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#administrative-privileges)、[Microsoft SmartScreen／SAC](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation)、[PyInstaller onedir／onefile](https://pyinstaller.org/en/stable/operating-mode.html)、[Electron 性能建议](https://www.electronjs.org/docs/latest/tutorial/performance)。

## 可重复 Windows 构建与验证

Windows x64／PowerShell 7、Node 22.22.1、uv 和 Python 3.11：

```powershell
.\scripts\build-windows.ps1
.\scripts\build-windows.ps1 -Installer
```

脚本准备独立构建环境，以锁文件哈希同步依赖，构建 React 与原生 PyInstaller sidecar、执行桌面 Node 回归。客户端输出在 `build/desktop/electron`；默认生成目录候选，`-Installer` 生成一个名称含 `unsigned` 的客户端 NSIS。目标用户不需要安装系统 Python。独立 Issuer 不再默认构建或分发；按需另行维护。PyInstaller 必须在 Windows 原生构建 Windows 后端。[平台约束](https://pyinstaller.org/en/stable/index.html)

未签名构建使用 `signExecutable:false` 保留应用EXE版本资源编辑；构建会读取PE文件属性并核对产品版本及文件主／次／补丁版本。脚本校对实际包内 manifest 与资源，再用包内程序验证中文／空格资源和数据路径、无授权访问业务、无 token 拒绝、一个合成案件崩溃后重开、无 `license.dat` 生成和 EOF 正常退出。`native-backend-smoke.json`、`windows-candidate.json`／`.sha256` 仅含版本、相对产物文件名、内容哈希及检查范围，不含业务数据或 token。

新图标候选另检查实际应用 EXE、NSIS 安装器和安装后卸载器的默认 PE 图标组，与批准 ICO 的全部尺寸和图像内容比对，记录为 `native-icon-resources.json` 及安装报告。安装生命周期读取隔离账户的桌面／开始菜单快捷方式，核对目标、图标位置及 Shell 解析的大小图标；实际应用烟测按所启动 PID 读取主窗口和分析窗口的原生图标句柄，与源 ICO 同尺寸渲染像素比对。它们补充 ASAR 内 PNG／ICO 文件摘要，不能以单纯 builder 配置或 renderer 截图代替。

快捷方式比较通过 `SHGFI_SYSICONINDEX` 和 `ImageList_GetIcon(ILD_NORMAL)` 读取 Shell 的基础图标，不把系统快捷箭头误认为应用图形变化。批准 ICO 也经过同一 Shell 流程，严格比较黑白背景下的完整像素与尺寸；PE 资源仍单独比对源 ICO 全部图像。原生回归使用临时资源 PE 与真实 `.lnk`，要求批准图标通过、DIB 与 PNG 两种错误图标均失败。失败报告保留固定检查名、错误码、尺寸、摘要及有界合成图标像素，方便区分快捷方式缺失、目标错误和图像不一致，不输出原始子进程日志。[Microsoft Shell 图标接口](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shgetfileinfow)

快捷方式通过原生 `IShellLinkW` 和只读 `IPersistFile.Load` 读取 Unicode 目标及图标位置，避免英文 Windows 上 WSH 属性将中文转为 `?`。目标、图标位置与窗口进程路径通过同时打开文件句柄后的卷号及完整 128 位文件标识核对，允许别名指向同一安装文件；相同字节的另一文件仍拒绝。报告仅记录路径文本和文件身份是否一致，不记录路径内容。

安装版主窗口启动后，若当前 CI 桌面存在可见 Shell 任务栏，会另保存 `native-installed-app.taskbar.png`，并记录截图尺寸与 SHA-256。它来自实际屏幕的任务栏矩形，不是 renderer 截图或拼合图。没有 Shell、截图不可用或尚未查看时不记录任务栏外观通过；脚本报告保留 `reviewed:false`，截图核对结果须在对应候选记录中单列。

上述自动检查仍仅覆盖 Windows hosted runner。它不证明 Windows 10／11 任务栏最终外观、既有固定图标缓存、真实系统 DPI 或 SmartScreen；没有可用交互桌面时不能把这些项目记为通过。候选构建默认不创建 Tag 或 Release，未获发布授权时仅保留 Actions 产物，现有 Latest 和下载入口保持原版本。

[Windows Actions](../.github/workflows/windows-candidate.yml) 只手动运行，使用中文源码路径；先执行定向进程／导入恢复／历史验签／资源回归，再复用同一构建入口。`installer=true` 额外传入 `-VerifyInstallerLifecycle`：仅在 GitHub Actions 的全新隔离用户下，以 `desktop/upgrade-baseline.json` 明确指定并固定 SHA-256／字节数的已公开客户端作为旧版本，验证静默安装到中文自定义目录 → 旧安装版后端写入并重开合成案件 → 新安装器不指定目录而自动找到旧版并覆盖升级 → 旧安装目录测试文件清除、设置字节与已安装设置模块读取 → 比对旧流水／金额／币种／画布／追踪和旧案件包往返 → 同版重装后重复资源／数据／设置核对 → 真正 EXE 启动／快捷键／单实例／正常关闭 → 卸载保留数据和移除注册入口。实际报告为 `native-installer-smoke.json` 和 `native-installed-app-smoke.json`，前者的 `priorDataChecks`、`upgradedDataChecks` 记录旧数据兼容链。原件仅依据已注册的导入来源核对摘要，不扫描业务目录。该模式拒绝已有 FundsTrace 安装或数据；普通本地构建不自动安装候选。

`desktop/upgrade-baseline.json` 的目标必须等于 `desktop/package.json`，旧版必须早于目标。当前候选固定为 **v0.1.8 → v0.1.9**；`prior_version` 输入和 `-PriorVersion` 参数保留，但只接受当前基线的旧版，取代过时的 `0.1.5`／`0.1.7` 选择。候选清单与两个账户的报告必须携带一致的基线、版本、旧包摘要／字节及当前候选摘要／字节。版本升级但未更新基线、旧包摘要或字节不符、报告属于其他版本对／安装包，均拒绝进入对应安装或候选转移步骤。设置样例由测试写入专用用户目录，再由旧／新已安装 asar 中的真实 store 读取；它不是旧版设置 UI 写入证据。旧目录中的待清理文件也是测试创建的程序文件，不是用户数据。

验证**已发布原包**时，手动调度同一 Windows workflow，指定 `published_target_sha256` 与 `published_target_bytes`。该模式从当前源码版本对应的 Release 下载目标 EXE，先核对固定摘要和长度，再将其中 x64 payload 解包作为资源比较参考；不构建、不替换公开资产。随后复用维护者和一次性普通账户的安装生命周期；安全报告及合成截图保留为独立 `fundstrace-published-upgrade-<run>` artifact。普通构建／候选转移模式继续独立，不把重新构建的同版包当成已发布包证据。该模式只支持与当前 package／baseline 目标相同的已发布版本，并按该版本源码图标和解包 manifest 核对；目标尚未发布时下载会失败，不回退或重建。必须在目标版本的正式 Release 资产存在后运行原包模式。已发布原包、候选构建、候选转移和诊断 probe 互斥。原包模式可同时指定 `in_app_update=true`：先安装固定的旧 Release，以旧版后端写入合成案件和偏好，再操作旧客户端的真实更新按钮，从其内置正式 HTTPS 更新源发现、下载、校验并确认重启安装。测试独立比对下载缓存的长度、SHA-256／SHA-512，要求自动重开目标版本、安装资源与公开 EXE 一致、案件与偏好保留；不改 DNS、证书或正式 feed。普通账户中仅后端保持出站阻断，Electron 为正式更新联网；离线启动证据仍来自独立生命周期。候选构建的 `in_app_update=true` 继续使用私有 HTTPS 失败夹具，二者证据分开记录。

旧数据验证使用一次性的公开合成样例，包含超出 JavaScript 安全整数范围的带分金额、独立 CNY／USD 流水、同账号跨案隔离、具名画布快照和追踪成员。升级前案件包由旧安装版导出，升级后导入为新案件并按流水号比较事实及重映射后的关联；新增路径、时序环路与原子上图在导入副本验证。它证明这些具体旧数据结构和流程的兼容性，不代表已读取或验证维护者的真实案件库。

构建 job 只有仓库读取权限，不接收生产授权／私钥，不执行 Release 写入。Actions 中的所选 EXE 或目录、安全报告及合成窗口截图仅留存 3 天，不上传整个 build 或运行目录。维护者核对成功运行、源码 SHA 和产物摘要后，可显式调度独立 transfer job；该 job 核对 Tag、候选资源及全部报告，先创建并核对长期证据归档，再仅向已存在的草稿上传原候选 EXE，并在正文写入不可变归档链接。该流程不重新构建或公开发布；归档失败不上传 EXE。维护者重新核对上传摘要后单独发布。发布包与 Tag 必须绑定同一 green 源码；构建报告的 `published:false` 表示构建脚本本身没有发布。

长期归档按版本保存原验证附件，包括后端／安装／应用／导入恢复报告、候选和资源清单、SHA-256 文件、两张合成 renderer 截图及普通账户证据 ZIP；`manifest.json` 记录来源运行、候选源码、Tag、安装包摘要及归档文件的大小和 SHA-256，`README.md` 说明范围与验收限制。整理既有 Release 时另保留 `release.original.json`，记录移除附件前的公开 Release 元数据。原安装包继续从 Release 下载，无需在证据分支重复保存。

归档集合由候选声明决定：历史 12 项保留兼容；图标声明要求独立 PE 图标报告和两账户嵌入的快捷方式／窗口报告；`inAppUpdateFixture=true` 要求两账户各自的下载中／就绪截图。各账户任务栏报告为 `captured` 时，必须同时归档 PNG，并核对报告与原候选 SHA 清单中的摘要；`unavailable` 仅保留固定原因，不表示已验收任务栏。0.1.9 起不得删除图标声明降级到历史证据集合。ZIP 拒绝重复、异常路径、NUL 截断、符号链接、目录和加密成员，不按目录扫描推定收录权限。

transfer 使用独立、不可取消的并发组。若上传完成后正文更新中断，可重跑同一候选：仅接受空草稿，或唯一同名、同长度、同 SHA-256 且已完成上传的原 EXE；后者跳过上传并补齐归档链接，不覆盖资产。不同或额外附件立即拒绝。正文完成后再次回读草稿、Tag、唯一 EXE 和归档链接，才报告转移成功。

证据分支只追加新版本，使用 fast-forward 更新，不 force push、不覆盖已有同版本目录，也不合并到产品 `main`。归档后重新读取 Git 提交中的文件并逐项核对摘要；Release 正文使用该提交的永久链接，避免后续分支变化影响追溯。Actions 的短期工件用于传递候选，不能替代该长期归档。安装、升级、旧案件兼容和其他原有检查继续完整执行，下载区简化不减少验收范围。

清单记录 Windows 实际检出字节。未强制 LF 的许可证、SVG、锁文件可能由 `core.autocrlf` 转 CRLF；本地对照须只允许已核实的检出换行转换，安装器和报告按实际字节严格核对。

## 覆盖升级研究与后续边界

2026-10-19 核对 v0.1.6 与 v0.1.7：安装身份、NSIS hook、Main／sidecar 退出、数据库、数据路径、偏好、导入／案件包服务及相关依赖未改变；此次标签修复不需要新增数据迁移。v0.1.7 原发布验收的旧版为 v0.1.5，不能推导出 v0.1.6 原包升级已经验证；单独的原包测试结果必须按运行记录判断。

2026-10-19 的历史验证曾用两个已发布原包补齐 [v0.1.6 → v0.1.7 原生验收](https://github.com/Eysn0130/FundsTrace/actions/runs/39363570681)，测试工具源码为 `f037ba2898bdf620036073d965f380c5d461adb9`，未重新构建应用。旧包 SHA-256 为 `fc9c6513079b0ac456b0dabe47b0f8a175eaf9b3b62eceda77a8abddaf6776aa`，目标包为 `52eabd3c6b0f77270c61066b8d1830b84cb5b316153d399625c7d21badfbeb7a`。维护者及普通账户各通过 33 项安装生命周期、61 项已安装应用检查、15 项冻结后端导入恢复；普通账户另通过 11 项环境／清理检查。升级／重装均自动找到原中文自定义目录；旧程序测试文件自然清除；EXE／asar／sidecar／manifest 资源一致；原有案件、精确金额、币种、画布、追踪、来源原件及设置的受测契约保留；升级后的实际节点标签回归通过。照片、关注节点、导入会话／回执尚未加入这条升级前后逐项比较，不能把其他独立测试等同于它们的跨版本证据。这份旧运行记录不构成 v0.1.8 → v0.1.9 的安装或更新验证。

现有会话采用每次启动的新内存 partition、`cache:false`，桌面后端为全部响应加 `no-store`。正常关闭并替换后，页面会重新从新 sidecar 读取资源。验收仍同时检查 EXE、asar、sidecar 和 manifest 中的静态资源摘要，并执行标签回归；仅看“安装成功”或版本号不足以证明业务页面更新。

| 尚需单独处理的条件 | 当前事实与后续原则 |
| --- | --- |
| 运行中升级、未保存编辑、活动导入 | 正常关窗与 owned sidecar EOF 已有；尚未证明安装器发起关窗时所有草稿／任务都已安全收敛。以后更新入口应先处理业务状态，再进入安装；不能把强杀视为保存。 |
| 旧卸载器丢失／被系统阻止、磁盘满／复制失败 | 锁定的 NSIS 模板有部分恢复及重试，但旧卸载器错误分支可能继续、复制最终回退路径不是完整原子替换。未做本轮故障注入，不能承诺任意失败后自动回滚。 |
| 数据格式演进、跨版本跳升及降级 | 当前无显式持久 schema 上限／最低可读版本、升级前完整备份或降级门。今后改变 schema／DuckDB／案件包时，应单独建立版本契约、事务迁移、失败恢复和最早支持版本的跳升验证。`.fundcase` 不含所有上传原件／导入回执，不能替代完整运行目录备份。 |
| 普通用户、Windows 10／11、安全系统 | 保持同一 Windows 用户的 per-user 安装与 AppData 身份。Server 2022 专用普通账户／程序出站阻断，与 Win10／11 交互向导、系统 DPI、SmartScreen／SAC、整机断网验收分别记录。当前安装器仍未签名。 |

官方文档分清两条更新架构：[Electron 内置 autoUpdater](https://www.electronjs.org/docs/latest/tutorial/updates) 在 Windows 文档中主要使用 Squirrel；FundsTrace 已采用 NSIS，应参考锁定版本的 [electron-builder v26 更新指引](https://www.electron.build/v26/docs/features/auto-update/)。应用内更新还需要更新元数据、客户端集成、包校验、用户选择和安全退出；仅发布 EXE 不会自动获得这些行为。保持 appId 的 [官方身份规则](https://www.electron.build/v26/docs/nsis/#guid-vs-application-name) 继续适用。默认站点部分内容属于尚未发布的 v27，不能当作当前 26.15.3 已支持功能。 每次修复应递增版本并绑定唯一候选包，避免同版本换包导致旧缓存与版本判断不一致。

可借鉴的公开项目机制，均按源码快照而非项目热度判断：

- [Joplin](https://github.com/laurent22/joplin/blob/cf57e5932584c0fbd894d8510f1a31a4d41f2251/packages/app-desktop/services/autoUpdater/AutoUpdaterService.ts#L50-L84) 禁止在普通退出时自动安装，由用户明确调用安装；其 [数据库版本门](https://github.com/laurent22/joplin/blob/cf57e5932584c0fbd894d8510f1a31a4d41f2251/packages/lib/JoplinDatabase.ts#L405-L415) 拒绝未知的新 profile。适合借鉴业务退出控制和独立的数据兼容版本。
- [Signal 安装器](https://github.com/signalapp/Signal-Desktop/blob/f3040f3e13b6dca5021c3db149320e0f7a771f0a/build/installer.nsh#L28-L55) 检查降级并默认取消；其 [更新签名](https://github.com/signalapp/Signal-Desktop/blob/f3040f3e13b6dca5021c3db149320e0f7a771f0a/ts/updater/signature.node.ts#L22-L42) 绑定包摘要和版本。适合借鉴明确降级政策及可信更新身份，不需要照搬其自建 updater。
- Joplin 的 [官方安全公告](https://github.com/laurent22/joplin/security/advisories/GHSA-9wp7-hr9m-3273) 说明安装器签名与更新客户端校验发布者是两项契约；以后增加 updater 时须同时验证，不能只检查 EXE 已签名。

本轮 `Invoke-WebRequest` 下载及 `/S` 安装未模拟浏览器下载后的 Mark of the Web 与 Shell 双击；[微软说明](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation) 指出签名与文件／发布者信誉共同影响防护，未签名包也可能被 Smart App Control 阻止。Win10／11 的浏览器下载→交互向导→启动验收仍需独立实机，不能从本轮无弹窗的静默安装推导。

这些项目没有为 FundsTrace 证明完整 NSIS 回滚；它们清理安装器缓存的代码也不授权扫描删除本项目用户数据。

`in_app_update=true` 另在这两个隔离账户运行真实安装版的更新胶囊：中断下载、损坏校验、重试、圆圈进度、稍后、确认重启、NSIS 静默安装和自动重新打开，再检查安装资源与原有案件。仅在 CI 内构建下一补丁号的私有测试安装器，通过临时测试 CA 和进程级 DNS 规则承接程序固定 HTTPS 地址。CA 由隔离虚拟机管理员统一加入该虚拟机的根信任，两个测试账户只读取加密叶证书夹具，结束后移除；不改变用户电脑或生产证书，不关闭 TLS 验证，不修改生产 feed、系统 hosts 或客户端源码中的地址。临时证书、私钥、服务及私有安装器不作为 Release 资产。该结果须标明私有 HTTPS 测试环境，不能代替国内 CDN 的实际下载验证或未来公开版本的发布链验收。

## 当前证据与尚未执行的边界

2026-10-21 启动页与窗口尺寸源码候选：桌面 Node 回归共 104 项，其中 98 项通过、6 项跳过；Python 定向回归 6 项通过。macOS 上使用隔离合成状态实际启动源码 Electron，已进入真实工作台，并将原生窗口从 1440×809 调至 960×640。浏览器以候选启动素材核对 1440×880、960×580、960×440 和 683×284 客户区：前三组保持 176px Logo，最后一组整体缩小，均居中且保留至少 24px 留白；淡出中缩放不重置进度。这些结果只覆盖该源码候选，不代表 Windows 安装版、原生 DPI／跨屏或 Win10／11 实机验收；本轮未打包或发布，历史 Windows 报告不覆盖本次新增启动层。

普通账户夹具显式绑定新建账户自己的 `LocalAppData`，并用一次性文件验证缓存基目录可写，避免带着调用者的环境访问其他用户的缓存目录。该准备只适用于 Windows Actions 新建的合成账户，随后仍由真实安装版创建和使用自己的更新缓存；失败时也由原账户清理私有临时文件，再移除测试 profile 和账户。

v0.1.3 的 [PR CI](https://github.com/Eysn0130/FundsTrace/actions/runs/38845930019) 完成 Python／G6 394 项及108个子测试、React 38项和 Windows 桌面 Node 34项；[Windows Actions](https://github.com/Eysn0130/FundsTrace/actions/runs/38845925970) 绑定源码 `2b8eda98bb6a3dae8c8fa0b91ade36a5e6342ef5`，完成59项定向Python、34项Node、55条资源和13项包内后端检查。维护者与普通账户各完成18项安装生命周期、46项实际EXE应用检查（内含27组CSS视口／DPR）、15项冻结后端导入中断恢复，普通账户另有11项环境和清理检查。旧版证据保留在各版本记录中。

本机 macOS arm64，无 Win10／11 交互式主机。Server 2022 上真实非管理员账户、中文profile／安装路径、程序外网阻断、安装后renderer／单实例／退出及包内导入进程恢复已执行；Win10／11实际系统100%／150%／200% DPI与跨屏、密集表格字体、交互安装向导、原生保存／取消、运行中安装器关窗和安装版任务中断后的人工恢复仍为 **NOT_EXECUTED（Win10／11 实机）**。程序出站阻断不等于整台主机物理断网，CSS／DPR模拟不等于系统缩放验收。

具备设备后只补上述受影响流程，使用专用合成用户；不连接业务实例，不按名称猜测终止进程。当前未签名，SmartScreen／SAC仍须实机验证，不使用Windows GUI全面验收标记。

## v0.1.3 交付记录

2026-10-15：[Release v0.1.3](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.3)、[PR #6](https://github.com/Eysn0130/FundsTrace/pull/6) 与上述 Windows Actions 绑定源码 `2b8eda98bb6a3dae8c8fa0b91ade36a5e6342ef5`。main 合并提交 `5660812a819d4c52d7a2b4ac355c5fbd27f85c54` 包含该候选且仓库tree完全一致，之后仅补发布说明；Tag固定成功构建的原SHA。

- 修复G6重载／重开后的持久导入接续，以及导入历史跳转丢任务ID。恢复同案原件、映射与账号选项，保留显式空主账号选择；活动／完成任务只读进度，重新预览后仍须用户确认。没有新队列、依赖或数据格式，详见[导入生命周期](import-lifecycle.md)。
- 初次上传先解析内存内容再写原件，新增两项真实进程退出回归；连同既有新库／升级库的写入中与回执后退出，共6项过程回归。不保证文件与数据库之间任意断电、迁移断电或磁盘损坏恢复。
- 空案隐藏无数据的图例和缩略图，保留跨案缩略图偏好；工作区变化、跨屏、拔屏、退出最大化／全屏时按DIP调整越界窗口，仍在工作区内的尺寸不重置。
- 普通账户由CI新建，确认token非管理员、中文用户目录与自己的AppData；复用固定SHA-256的v0.1.2安装升级，安装根为中文空格路径。执行前核实三个有效防火墙profile启用，对安装版Electron和backend出站阻断，保留loopback。安装与数据路径检查、页面导航、恢复、退出、卸载留数据均通过；结束后由创建者清理私有临时目录，再移除profile／账户、规则并恢复防火墙设置。工具仅在Windows Actions运行，不进入安装包。
- 两账户实际安装EXE均渲染G6、React与分析子窗；每页执行960×500、960×640、1280×640与DPR1／1.5／2的9组模拟，共每账户27组。真实指针点击导入／分析导航，确认单实例、正常关窗和backend端口释放。已检查[合成安装版renderer截图](https://github.com/Eysn0130/FundsTrace/releases/download/v0.1.3/native-installed-app.png)，不覆盖系统原生边框和对话框。
- 冻结后端以公开HTTP导入20,003行合成数据，观察活动后终止测试持有的进程；重开识别中断，核对无部分批次／流水／来源，再明确重新预览和提交。完成后20,000笔唯一流水、3笔重复，总额1,240,000.00；来源原件、源行与完成回执重开均核对，未重放写入。普通账户另复测同链；此证据是安装包后端，不是Windows人工恢复UI验收。
- macOS隔离源码Electron实际完成中文文件上传→导入历史→G6恢复同一任务→重新预览保留空主账号选择→手动确认，仅写1笔123.45；九组CSS／DPR下空态按钮可命中、图例／缩略图不遮挡，偏好跨案保持，无页面异常。源码UI证据不替代Windows交互实机。
- 下载的EXE、报告、截图和native manifest均重新计算SHA-256，与候选清单的完整相对路径核对。55条资源中23项与本地字节相同、29项仅有已核实的Windows检出CRLF转换、3项React生成物也与本地锁定构建摘要相同。完整win-unpacked目录由CI核对，未在macOS另行下载执行。

| 文件总字节 | v0.1.2 | v0.1.3 |
| --- | ---: | ---: |
| 安装器 | 159802454 | 159805306 |
| 安装后文件 | 500075954 | 500082615 |
| Electron语言资源 | 1762536（3个） | 1762536（3个） |

安装器仅增加2,852字节、安装后文件增加6,661字节，保留上版裁剪结果；文件总量不是磁盘分配空间。PE FileVersion为`0.1.3`，ProductVersion为`0.1.3.0`。同runner单次观测backend首次就绪1243ms、重开1092ms；安装EXE启动后工作区就绪维护者2822ms、普通账户1925ms。这些流程此前已启动过包内backend，不能作为冷启动或用户机器时限保证，也不能据此声明相对旧版提速。

EXE SHA-256：`62755d8018e4516f8e0a0a5a264fec235ab1a767943d780903b3a408575bc729`。

Release除原生报告外，提供[普通账户证据ZIP](https://github.com/Eysn0130/FundsTrace/releases/download/v0.1.3/standard-user-compatibility.zip)，包含6份已核对的报告／截图；ZIP是维护者对这6份原件的封装，其成员摘要仍对应候选清单。构建报告`published:false`仅表示脚本本身不发布。v0.1.2记录中的空态遮挡已由本版修复，其他实机边界继续保留。

## v0.1.2 交付记录

2026-10-15：[Release v0.1.2](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.2)、[PR #4](https://github.com/Eysn0130/FundsTrace/pull/4) 与 [Windows Actions 38840226553](https://github.com/Eysn0130/FundsTrace/actions/runs/38840226553) 绑定源码 `d19a238a935f5169d830bc8cab2df6e7f6303576`。main 合并提交 `dab01c307a1aa0bce0bdecf81761631483141392` 包含该候选且仓库 tree 完全一致；Tag 固定成功构建的原 SHA，后续仅补发布文档。

- 集成 Ctrl／Command 多选、1–10 个呼吸／更多静态、50 个起整体预览和撤销／切换修复；[交互证据](graph-interaction.md) 的46项可执行／样式资源指纹匹配，仅两项品牌 SVG 随已合并品牌更新变化。该浏览器证据不等于 Windows 交互验收。
- 修复桌面白名单漏 `graph-selection.js`；实际HTML引用回归先失败后通过。报告绘图库延迟加载有独立进程真实HTTP回归，报告附件和精确金额保持。
- 新启动提示先于后端准备，准备中取消和二次双击有回归；路径不可读的疑似本应用进程返回检查失败，未扩大终止权限。
- Windows包内55条资源中，52项源码资源与本地候选逐项核对，仅接受已知Windows检出CRLF；3项React生成产物由同候选锁定构建及安装后摘要核对。13项包内后端检查还实际请求本地脚本和延迟报告附件。
- 17项安装生命周期从固定摘要v0.1.1升级至0.1.2，包含实际安装EXE启动、免激活和新模块可用、单实例、正常关闭及后端端口释放，再卸载保留两个合成案件及哨兵；安装应用安全报告11项全通过。[合成安装版renderer截图](https://github.com/Eysn0130/FundsTrace/releases/download/v0.1.2/native-installed-app.png) 为1008×663，不覆盖原生边框、系统保存或Win10／11普通用户验收。
- PE FileVersion `0.1.2`、ProductVersion `0.1.2.0`。EXE、三份smoke报告、native manifest与候选摘要已在本机重新计算SHA-256；完整win-unpacked目录由CI核对，未在macOS另行下载执行。

| 文件总字节 | v0.1.1 | v0.1.2 |
| --- | ---: | ---: |
| 安装器 | 168336906 | 159802454 |
| 安装后文件 | 548923669 | 500075954 |
| Electron语言资源 | 50643501（55个） | 1762536（3个） |

安装器减少8534452字节（约5.1%），安装后文件减少48847715字节（约8.9%）。文件总字节不是磁盘分配空间。新包backend约164.1MB；最大项仍为Electron主体、DuckDB与数值运行库，不以删除所需DLL／许可文件或改onefile进一步压缩。

该runner单次观测：首次backend健康1561ms、同目录重开1210ms、首次空案报告742ms；安装版进程启动后工作区就绪2639ms，正常关闭499ms。没有旧版同条件启动基线，也没有真实Win10／11冷机样本，不能声明全体用户启动上限或相对旧版的提速幅度。

`FundsTrace-0.1.2-windows-x64-unsigned.exe` SHA-256：`667fbc6941f7f848d790f599ea0a73c837b46a46580b49b1942b60f340d12bbf`。

安装器仍未签名；SmartScreen／SAC、普通用户与DPI／原生保存等实机项保留未验收状态。另在上述窄窗口空案截图观察到缩略图占用部分引导文字区域，顶部导入入口可用；这是后续空态布局改进项，不据此宣称启动失败或全界面已验收。构建报告 `published:false` 仍仅表示脚本本身不发布，实际Release由维护者另行上传。

## v0.1.1 交付记录

2026-10-15：[Release v0.1.1](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.1) 与 [Windows Actions 38827843574](https://github.com/Eysn0130/FundsTrace/actions/runs/38827843574) 绑定源码 `e7be2f5675dcd2dc896a20d6d644d5aa05125a73`。[PR #1](https://github.com/Eysn0130/FundsTrace/pull/1) 已合并，Tag指向该成功构建；之后只补交付文档。

- 包含查询独立折叠、流水全览即时上图／撤销及流程修复，详见 [使用指南](workspace-guide.md)。原G6节点／边／标签／布局源码保留。
- 桌面版本0.1.1，应用PE文件属性为FileVersion `0.1.1`、ProductVersion `0.1.1.0`。只禁用签名，保留版本资源编辑；安装后EXE摘要与已验证目录候选相同。
- 375项源码、Windows定向Python56项、桌面Node22项、React类型检查／构建、54条资源与11项后端检查通过。
- 14项安装生命周期检查通过：固定摘要的v0.1.0安装 → 0.1.1覆盖升级 → 安装后backend读写／重开合成案件 → 静默卸载移除注册入口、保留两个合成案件及哨兵。测试仅使用runner新建状态。
- 下载并核对一个EXE与五份安全报告／校验文件；EXE和报告的SHA-256、源码入口、资源与锁文件均匹配。CI为1544个目录／安装器文件生成摘要，本机未独立下载并重验整个win-unpacked目录。

`FundsTrace-0.1.1-windows-x64-unsigned.exe`：168336906字节，SHA-256 `81f0e720dc35fa7ed902388cbbd17208881425d6828ae395195135cfcdb35b8d`。

安装器未签名；Windows GUI／DPI、原生保存取消、触屏及活动导入GUI恢复未实机验收。报告中的 `installerLifecycle:passed` 限于上述静默链；`guiAcceptance:false`／`installedAcceptance:false`保留；`published:false`表示构建脚本未执行GitHub发布，实际公开Release另行核对。

品牌展示支线新增的 Logo／桌面图标仅为后续源码候选，未包含在此 Tag 或已发布的 v0.1.1 安装器中；集成到后续安装包仍需另行构建与验证。

## 2026-10-15 工作台源码候选阶段（发布前记录）

以下是发布前的工作台源码候选记录，涉及查询折叠、流水全览、画布历史及流程修复；见 [设计验收](../design-qa.md) 和 [使用指南](workspace-guide.md)。静态资源白名单已加入六个工作台脚本和24个本地 Tabler SVG及许可；本机资源检查通过。

[Windows目录候选 38784969693](https://github.com/Eysn0130/FundsTrace/actions/runs/38784969693) 已成功，绑定源码 `02b6c303d8a4d9f0cf0b390999969a482efdb771`，非安装器模式。56项定向Python、22项桌面Node、React类型检查／构建、54项资源记录及11项包内后端检查通过。54条资源与本地对照：25项原字节、29项仅Windows检出CRLF；报告和锁文件摘要均核对。CI为1542个产物文件生成摘要，本机仅分段读取安全报告，没有重新下载／核验整个目录。

该阶段未生成新安装器、改变版本、打 Tag 或发布 Release，报告为 `installerLifecycle:not-run`、`guiAcceptance:false`、`installedAcceptance:false`、`published:false`。当时的 v0.1.0 绿色记录不覆盖这批 UI；后续 v0.1.1 已以新的源码 SHA 和安装器单独完成静默生命周期，见上文。该目录候选本身的 Windows GUI／DPI／原生保存取消仍未执行，后续文档不改变其原生构建所用源码与资源。

## v0.1.0 交付记录

2026-10-15：[Release v0.1.0](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.0) 与 [Windows Actions 38746367500](https://github.com/Eysn0130/FundsTrace/actions/runs/38746367500) 绑定源码 `d7547734f91546fb3b81b30a7e4993b644b77154`。Tag 指向该成功候选，后续仅补交付文档。

- 该阶段源码 API 21 条、React 37 条、前端构建及 API 契约检查通过；免激活回归在修复前失败、修复后通过，源码浏览器从 `/license` 进入业务成功。
- Windows x64：56 项定向 Python、22 项桌面 Node、React 类型检查／构建通过。
- 23 项包内资源与 11 项后端检查通过：无授权访问、token 拒绝、中文／空格路径、合成案件崩溃重开、无授权文件生成、EOF 停止。
- 14 项安装生命周期检查通过：固定 alpha.3 安装 → 0.1.0 覆盖升级 → 安装后真实 backend 读写和重开 → 静默卸载；注册入口与程序移除，保留的两个合成案件及哨兵可回读。测试结束后仅清理该 runner 自建状态。
- 发布包含一个 EXE 与五份报告／校验文件；GitHub 六项资产的上传完成状态、字节数和 SHA-256 已核对。Actions ZIP 摘要、五条下载文件记录及源码资源／锁文件已核对；解包目录的 1513 个文件摘要由 CI 生成，未在 macOS 下载整个目录重新核验。

`FundsTrace-0.1.0-windows-x64-unsigned.exe`：168295250 字节，SHA-256 `c57e357e8f76f4b4badecfe437a3943b116a71738b5d5a7ff6413a83cbe019fc`。

报告中 `installerLifecycle:passed` 限于上述静默流程；`guiAcceptance:false`／`installedAcceptance:false` 保留完整 GUI 和安装体验未验收的限制，`published:false` 表示构建脚本未执行发布。安装器未签名；DPI、原生保存／取消、标准控制面板交互和活动导入的 Windows 桌面恢复未验收，本次发布不使用 GUI 通过标记。

## 历史交付记录

[v0.1.0-alpha.3](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.0-alpha.3) 和 [Windows Actions 38733405640](https://github.com/Eysn0130/FundsTrace/actions/runs/38733405640) 绑定源码 `d0d569fe415d7798584f3207d0e8817a7916c75f`：两个未签名客户端／Issuer 安装器，客户端内置公钥指纹 `FEF434964FE89178`，仍要求历史激活。此前的 53 项 Python、16 项 Node、24 项资源和 10 项包内烟测仅属于该候选。alpha.3 客户端 168295048 字节，SHA-256 `0e0454af6907912cfd628849bced6740f02f333f9ad5350e14466cc4f4e22e20`，作为本轮覆盖升级的固定公开输入。

## 团队协作和 Analytix 接入

新成员从 [CONTRIBUTING.md](../CONTRIBUTING.md) 的一个合成用例开始。短分支、小 PR、预先认领模块，避免多人同时编辑 `app/main.py`／`static/app.js`；实际协作者到位后按模块安排一位 reviewer 和必要检查，不增加固定双审／重复全套门槛。发布、Tag 和公开资产由维护者执行。

长期入口为 [共享 React 面板＋宿主 adapter](frontend-host-adaptation.md)。`funds-panel.v1` 仍是 UI port；浏览器 DTO 不是已获接受的 IPC／权限契约。保持一个 Core Harness、案件／快照 authority 及每个用例一个数据所有者，不把独立 Electron 或第二套数据库搬进 Analytix。侧栏由有效 contribution、Core enabled/available 和当前案件 session 派生；换案／重绑／撤销先清旧显示和回调，缺宿主能力不回退独立 HTTP。实施前重新核对真实 Host，见 [windows-and-plugin-plan.md](windows-and-plugin-plan.md)。
