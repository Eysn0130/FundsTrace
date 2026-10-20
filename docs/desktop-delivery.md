# FundsTrace 独立桌面交付

## v0.1.x 发布规则

FundsTrace v0.1.x 正常启动**无需公钥或授权码**。每个实际版本 Tag 对应 GitHub Release，附 Windows x64 NSIS `.exe`，从 [Releases](https://github.com/Eysn0130/FundsTrace/releases/latest) 下载并安装。原生构建与安装结果以对应版本的候选清单和发布报告为准；下方历史记录仅对应各自源码。安装包未签名，不能声称完成代码签名或 Windows 全面兼容验收。

Python 已移除产品许可门卫，`/api/license/info` 明确返回 `activation_required:false`，如实保留历史 `activated` 状态；`/license` 可直接进入系统。没有公钥、没有授权文件或存在损坏的历史授权都不阻塞业务，不生成测试授权来解锁。FT2／HMAC 验签接口、独立 `desktop/issuer/` 工具和已有材料保留，见 [历史授权维护](license-activation.md)。已发布 alpha.3 不随源码改变。

## 安装、升级与卸载

- 保留 `com.eysn.fundstrace`、FundsTrace 产品名、appId 派生的注册表键及**当前用户**安装范围。标准 NSIS 引导显示已安装版本；安装目录可以选择。使用新版 EXE 覆盖安装，由同一注册信息找到旧卸载器；不另建 Analytix 安装身份。
- 安装替换、静默／交互卸载前，检查选定及注册的旧安装目录。仅向该目录中精确 `FundsTrace.exe` 路径的窗口请求正常关闭，等待所属进程退出；Main 通过 stdin EOF 停止自己的 Python 后端。占用或检查失败时交互 Retry／Cancel，静默返回非零；不按 Python／Electron 名称强杀。
- Windows“已安装的应用”和程序目录提供卸载入口。`deleteAppDataOnUninstall:false`，默认保留 `%APPDATA%\FundsTrace` 中的案件、上传、设置及历史授权。不要把用户数据放进安装目录；卸载后重装会继续使用同一数据目录。删除真实案件须由用户另行明确操作。
- 本轮没有应用内自动更新器。新版本通过同项目 Release 下载 EXE 覆盖安装；后续再做“检查更新 → 版本说明 → 用户确认下载／安装”。保留包完整性、任务安全停止及失败反馈边界；只有 EXE 附件不构成自动更新 feed，不套用 Analytix 的更新源。

[installer.nsh](../desktop/installer.nsh) 和 [installer-processes.ps1](../desktop/installer-processes.ps1) 按需适配 Analytix 安装根进程范围和生命周期设计，使用 FundsTrace 自身身份、per-user 范围和正常关窗策略；未引入其大型 React＋WebView2／WinForms 引导器。来源基于只读 commit `c7d2a370855a8d371c61c32cc2422515b9404ef2` 的 [NSIS hook](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/build/standard-win-installer.nsh)、[原生引导器](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/installer/winforms-bootstrapper/AnalytixWinFormsBootstrapper.cs) 和 [更新器](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/gui-updater.ts)。Apache-2.0 版权、修改通知及许可证随包保留；本轮不修改 Analytix。

## 运行与数据边界

[desktop/main.cjs](../desktop/main.cjs) 负责窗口、受限导航／下载和自己创建的服务。受 sandbox 和 contextIsolation 保护的 [preload](../desktop/preload.cjs) 仅暴露展示偏好 get/set/remove；Main 校验窗口归属、主框架及当前精确 origin，只将案件选择、线型、标签、证件／照片显示开关写入 `userData/preferences.json`。随机端口和临时 session 不变；不持久化 token、流水、快照或任意 renderer 键，不提供通用 IPC。G6 `/` 保留完整业务，`/analysis` 为分析，`/workspace/` 为共享 React 试点。业务数据在 Electron 自身 `userData/data`，不写安装资源，不打开 Analytix 数据根。保留单实例和原有按显示器 DIP 工作区创建窗口的规则。

[desktop_backend.py](../desktop_backend.py) 从 stdin 读取严格的 `{protocol:1, token, dataDir, synthetic}` 后才导入应用。随机 token 只经父进程管道和 Main 持有的精确 origin 请求头传递，不进入 URL、argv、renderer、日志或导出。后端绑定 `127.0.0.1:0`，Main 健康检查后才加载页面；冻结后端拒绝 source-only synthetic 模式。

桌面认证仍覆盖页面、API、静态资源，核对 token、精确 Host、Origin／Fetch Metadata，并设置 CSP／no-store。独立 session 拒绝未知网络、导航、窗口和权限；renderer 保持 sandbox、contextIsolation、webSecurity，关闭 nodeIntegration。免激活不削弱本地服务归属认证。[Electron 安全规则](https://www.electronjs.org/docs/latest/tutorial/security)

正常退出走 Main → stdin EOF → 后端关闭，超时只终止所持句柄的归属子进程。服务崩溃显示故障，不自动重放导入。持久任务恢复仍由 Python 服务负责；窗口退出 hook、普通案件崩溃重开与导入进行中恢复是不同证据。[app 生命周期](https://www.electronjs.org/docs/latest/api/app)

下载使用原生保存对话框，处理取消／中断，拒绝任意远程 URL；服务生成成功和磁盘落盘完成分别处理，不自动打开下载 HTML。[DownloadItem](https://www.electronjs.org/docs/latest/api/download-item)

## Windows 10／11 安装与启动边界

当前目标是 Windows 10／11 **x64**。锁定的 [Electron 44.7.2](https://github.com/electron/electron/blob/v44.7.2/README.md#platform-support) 支持 Windows 10 及以后，Electron 44 已移除 Windows 32 位；本仓库不发布 32 位或原生 ARM64 包，也不凭上游声明推断最低 Win10 build 或任意机器兼容。

| 链路 | 本项目处理与实际验证边界 |
| --- | --- |
| 下载与安装 | 单个 NSIS EXE，当前用户范围，不要求另装 Python、配置公钥或授权码；使用相同安装身份覆盖升级。Windows hosted runner 是管理员且关闭 UAC，因此不能用 CI 证明普通无管理员用户的全部交互路径。 |
| 安全系统拦截 | 当前仍未签名。SmartScreen 可能告警；Win11 Smart App Control 还可能阻止没有信誉的未签名安装器或 backend。它与业务激活无关，不要求用户关闭系统防护来获得通过标记。签名和信誉需独立处理。 |
| 启动反馈 | 先显示不含业务数据或脚本的本地启动提示，后端健康后同窗进入业务；准备中关闭会取消并清理本次归属服务，二次双击聚焦同一窗口。超时给出安全错误说明，不输出 token 或内部异常。 |
| 首次报告 | 普通启动、案件和分析入口不再提前初始化 Matplotlib；首次报告才加载。实际报告附件仍由源码回归和包内请求验证，不通过删除依赖制造启动收益。 |
| 体积 | 保留 Python onedir、Electron asar 和宿主文件白名单；Electron 语言资源保留 en-US、zh-CN、zh-TW。构建报告区分安装器字节、安装后字节、backend／语言资源与最大文件，不凭依赖名称裁剪动态模块。 |
| 运行中升级 | 只向已核对路径的本应用请求正常关闭；疑似本应用进程路径不可读时返回检查失败，不能静默当空闲或按名称强杀。 |
| 安装后启用 | 自动链使用固定摘要的 v0.1.5 作为旧版，由其安装版后端通过公开 API 创建合成案件、金额、画布和追踪，再升级到当前候选。比较原数据、旧案件包迁移和原件摘要，并启动真正的 FundsTrace.exe 检查业务 DOM、快捷键、免激活、单实例、正常关闭和端口释放，再卸载回读合成数据。实际执行结果绑定候选 SHA 记录。 |

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

[Windows Actions](../.github/workflows/windows-candidate.yml) 只手动运行，使用中文源码路径；先执行定向进程／导入恢复／历史验签／资源回归，再复用同一构建入口。`installer=true` 额外传入 `-VerifyInstallerLifecycle`：仅在 GitHub Actions 的全新隔离用户下，以已公开且固定 SHA-256 的 v0.1.5 客户端作为旧版本，验证静默安装 → 旧安装版后端写入并重开合成案件 → 当前候选版覆盖升级 → 比对旧流水／金额／币种／画布／追踪和旧案件包往返 → 真正 EXE 启动／快捷键／单实例／正常关闭 → 卸载保留数据和移除注册入口。实际报告为 `native-installer-smoke.json` 和 `native-installed-app-smoke.json`，前者的 `priorDataChecks`、`upgradedDataChecks` 记录旧数据兼容链。原件仅依据已注册的导入来源核对摘要，不扫描业务目录。该模式拒绝已有 FundsTrace 安装或数据；普通本地构建不自动安装候选。

旧数据验证使用一次性的公开合成样例，包含超出 JavaScript 安全整数范围的带分金额、独立 CNY／USD 流水、同账号跨案隔离、具名画布快照和追踪成员。升级前案件包由旧安装版导出，升级后导入为新案件并按流水号比较事实及重映射后的关联；新增路径、时序环路与原子上图在导入副本验证。它证明这些具体旧数据结构和流程的兼容性，不代表已读取或验证维护者的真实案件库。

构建 job 只有仓库读取权限，不接收生产授权／私钥，不执行 Release 写入。所选 EXE 或目录、安全报告及合成窗口截图仅留存 3 天，不上传整个 build 或运行目录。维护者核对成功运行、源码 SHA 和产物摘要后，可显式调度独立 transfer job；该 job 核对 Tag 和全部报告后，仅向已存在的空草稿上传原候选资产，不重新构建或公开发布。维护者重新核对上传摘要后单独发布。发布包与 Tag 必须绑定同一 green 源码；构建报告的 `published:false` 表示构建脚本本身没有发布。

清单记录 Windows 实际检出字节。未强制 LF 的许可证、SVG、锁文件可能由 `core.autocrlf` 转 CRLF；本地对照须只允许已核实的检出换行转换，安装器和报告按实际字节严格核对。

手动 Windows Actions 的 `prior_version` 可明确选择固定摘要的 `0.1.5`（历史默认）或 `0.1.7`。该值贯穿管理员与普通账户的真实安装生命周期，并写入 `windows-candidate.json` 的 `installerUpgradeFrom`；转存草稿时两个账户的报告必须与该值一致，`0.1.7` 还必须携带已发布安装器的固定 SHA-256。选择输入本身不代表升级通过，仍以对应运行的安装、旧数据和资源完整性报告为准。此 EXE 覆盖安装验收也不代表已完成软件内下载／重启更新循环。

`in_app_update=true` 另在这两个隔离账户运行真实安装版的更新胶囊：中断下载、损坏校验、重试、圆圈进度、稍后、确认重启、NSIS 静默安装和自动重新打开，再检查安装资源与原有案件。仅在 CI 内构建下一补丁号的私有测试安装器，通过当前测试用户临时信任的本地 CA 和进程级 DNS 规则承接程序固定 HTTPS 地址；不关闭 TLS 验证，不修改生产 feed、系统 hosts 或客户端源码中的地址。临时证书、私钥、服务及私有安装器不作为 Release 资产。该结果须标明私有 HTTPS 测试环境，不能代替国内 CDN 的实际下载验证或未来公开版本的发布链验收。

## 当前证据与尚未执行的边界

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
