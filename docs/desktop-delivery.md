# FundsTrace 独立桌面交付

## v0.1.x 发布规则

FundsTrace v0.1.0 正常启动**无需公钥或授权码**。每个实际版本 Tag 对应 GitHub Release，附 Windows x64 NSIS `.exe`，从 [Releases](https://github.com/Eysn0130/FundsTrace/releases) 下载并安装。[v0.1.0 已发布](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.0)，原生构建和安装结果见下文交付记录；Windows GUI 验收不作为发布标记。安装包未签名，不能声称完成代码签名或 Windows 全面兼容验收。

Python 已移除产品许可门卫，`/api/license/info` 明确返回 `activation_required:false`，如实保留历史 `activated` 状态；`/license` 可直接进入系统。没有公钥、没有授权文件或存在损坏的历史授权都不阻塞业务，不生成测试授权来解锁。FT2／HMAC 验签接口、独立 `desktop/issuer/` 工具和已有材料保留，见 [历史授权维护](license-activation.md)。已发布 alpha.3 不随源码改变。

## 安装、升级与卸载

- 保留 `com.eysn.fundstrace`、FundsTrace 产品名、appId 派生的注册表键及**当前用户**安装范围。标准 NSIS 引导显示已安装版本；安装目录可以选择。使用新版 EXE 覆盖安装，由同一注册信息找到旧卸载器；不另建 Analytix 安装身份。
- 安装替换、静默／交互卸载前，检查选定及注册的旧安装目录。仅向该目录中精确 `FundsTrace.exe` 路径的窗口请求正常关闭，等待所属进程退出；Main 通过 stdin EOF 停止自己的 Python 后端。占用或检查失败时交互 Retry／Cancel，静默返回非零；不按 Python／Electron 名称强杀。
- Windows“已安装的应用”和程序目录提供卸载入口。`deleteAppDataOnUninstall:false`，默认保留 `%APPDATA%\FundsTrace` 中的案件、上传、设置及历史授权。不要把用户数据放进安装目录；卸载后重装会继续使用同一数据目录。删除真实案件须由用户另行明确操作。
- 本轮没有应用内自动更新器。新版本通过同项目 Release 下载 EXE 覆盖安装；后续再做“检查更新 → 版本说明 → 用户确认下载／安装”。保留包完整性、任务安全停止及失败反馈边界；只有 EXE 附件不构成自动更新 feed，不套用 Analytix 的更新源。

[installer.nsh](../desktop/installer.nsh) 和 [installer-processes.ps1](../desktop/installer-processes.ps1) 按需适配 Analytix 安装根进程范围和生命周期设计，使用 FundsTrace 自身身份、per-user 范围和正常关窗策略；未引入其大型 React＋WebView2／WinForms 引导器。来源基于只读 commit `c7d2a370855a8d371c61c32cc2422515b9404ef2` 的 [NSIS hook](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/build/standard-win-installer.nsh)、[原生引导器](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/installer/winforms-bootstrapper/AnalytixWinFormsBootstrapper.cs) 和 [更新器](https://github.com/Eysn0130/analytix/blob/c7d2a370855a8d371c61c32cc2422515b9404ef2/src/main/gui-updater.ts)。Apache-2.0 版权、修改通知及许可证随包保留；本轮不修改 Analytix。

## 运行与数据边界

[desktop/main.cjs](../desktop/main.cjs) 只负责窗口、受限导航／下载和自己创建的服务；无 preload、无通用 IPC。G6 `/` 保留完整业务，`/analysis` 为分析，`/workspace/` 为共享 React 试点。用户数据在 Electron 自身 `userData/data`，不写安装资源，不打开 Analytix 数据根。保留单实例和原有按显示器 DIP 工作区创建窗口的规则。

[desktop_backend.py](../desktop_backend.py) 从 stdin 读取严格的 `{protocol:1, token, dataDir, synthetic}` 后才导入应用。随机 token 只经父进程管道和 Main 持有的精确 origin 请求头传递，不进入 URL、argv、renderer、日志或导出。后端绑定 `127.0.0.1:0`，Main 健康检查后才加载页面；冻结后端拒绝 source-only synthetic 模式。

桌面认证仍覆盖页面、API、静态资源，核对 token、精确 Host、Origin／Fetch Metadata，并设置 CSP／no-store。独立 session 拒绝未知网络、导航、窗口和权限；renderer 保持 sandbox、contextIsolation、webSecurity，关闭 nodeIntegration。免激活不削弱本地服务归属认证。[Electron 安全规则](https://www.electronjs.org/docs/latest/tutorial/security)

正常退出走 Main → stdin EOF → 后端关闭，超时只终止所持句柄的归属子进程。服务崩溃显示故障，不自动重放导入。持久任务恢复仍由 Python 服务负责；窗口退出 hook、普通案件崩溃重开与导入进行中恢复是不同证据。[app 生命周期](https://www.electronjs.org/docs/latest/api/app)

下载使用原生保存对话框，处理取消／中断，拒绝任意远程 URL；服务生成成功和磁盘落盘完成分别处理，不自动打开下载 HTML。[DownloadItem](https://www.electronjs.org/docs/latest/api/download-item)

## 可重复 Windows 构建与验证

Windows x64／PowerShell 7、Node 22.22.1、uv 和 Python 3.11：

```powershell
.\scripts\build-windows.ps1
.\scripts\build-windows.ps1 -Installer
```

脚本准备独立构建环境，以锁文件哈希同步依赖，构建 React 与原生 PyInstaller sidecar、执行桌面 Node 回归。客户端输出在 `build/desktop/electron`；默认生成目录候选，`-Installer` 生成一个名称含 `unsigned` 的客户端 NSIS。目标用户不需要安装系统 Python。独立 Issuer 不再默认构建或分发；按需另行维护。PyInstaller 必须在 Windows 原生构建 Windows 后端。[平台约束](https://pyinstaller.org/en/stable/index.html)

未签名构建使用 `signExecutable:false` 保留应用EXE版本资源编辑；构建会读取PE文件属性并核对产品版本及文件主／次／补丁版本。脚本校对实际包内 manifest 与资源，再用包内程序验证中文／空格资源和数据路径、无授权访问业务、无 token 拒绝、一个合成案件崩溃后重开、无 `license.dat` 生成和 EOF 正常退出。`native-backend-smoke.json`、`windows-candidate.json`／`.sha256` 仅含版本、相对产物文件名、内容哈希及检查范围，不含业务数据或 token。

[Windows Actions](../.github/workflows/windows-candidate.yml) 只手动运行，使用中文源码路径；先执行定向进程／导入恢复／历史验签／资源回归，再复用同一构建入口。`installer=true` 额外传入 `-VerifyInstallerLifecycle`：仅在 GitHub Actions 的全新隔离用户下，以已公开且固定 SHA-256 的 v0.1.0 客户端作为旧版本，验证静默安装 → 当前候选版覆盖升级 → 安装后后端读取合成案件 → 卸载保留数据和移除注册入口。实际报告为 `native-installer-smoke.json`。该模式拒绝已有 FundsTrace 安装或数据；普通本地构建不自动安装候选。

工作流只有仓库读取权限，不接收生产授权／私钥，不执行 Release 写入。所选 EXE 或目录及安全报告仅留存 3 天，不上传整个 build 或运行目录。维护者核对成功运行、源码 SHA、产物摘要后单独发布。发布包与 Tag 必须绑定同一 green 源码；构建报告的 `published:false` 表示构建脚本本身没有发布。

清单记录 Windows 实际检出字节。未强制 LF 的许可证、SVG、锁文件可能由 `core.autocrlf` 转 CRLF；本地对照须只允许已核实的检出换行转换，安装器和报告按实际字节严格核对。

## 当前证据与尚未执行的边界

源码 API 21 条、React 37 条、前端构建及 API 契约检查通过；免激活新增回归在修复前失败、修复后通过，源码浏览器从 `/license` 进入业务成功。当前 Windows 候选的定向 Python 56 条和桌面 Node 22 条均通过，包括 Windows 原生进程范围测试。完整结果以对应版本的 Actions 和报告为准。

本机 macOS arm64，无交互式 Windows 主机。Windows GUI、系统 100%／150%／200% DPI、密集表格实际字体、原生保存／取消、运行中安装器正常关窗和导入任务中断恢复仍为 **NOT_EXECUTED（Windows GUI）**。静默安装报告不把这些标成通过，也不作为 GUI 发布标签。

具备设备后只补受影响流程：查询／勾选／上图／邻域和完整金额可读；中文目录 XLSX／图像保存及取消；活动导入退出与回执重开；离线启动与混合 DPI。只用专用合成测试用户，不能连接原业务实例或按名称猜测终止进程。

## 2026-10-15 工作台源码候选

当前工作树的查询折叠、流水全览、画布历史及流程修复属于新的源码候选；见 [设计验收](../design-qa.md) 和 [使用指南](workspace-guide.md)。静态资源白名单已加入六个工作台脚本和24个本地 Tabler SVG及许可；本机资源检查通过。

[Windows目录候选 38784969693](https://github.com/Eysn0130/FundsTrace/actions/runs/38784969693) 已成功，绑定源码 `02b6c303d8a4d9f0cf0b390999969a482efdb771`，非安装器模式。56项定向Python、22项桌面Node、React类型检查／构建、54项资源记录及11项包内后端检查通过。54条资源与本地对照：25项原字节、29项仅Windows检出CRLF；报告和锁文件摘要均核对。CI为1542个产物文件生成摘要，本机仅分段读取安全报告，没有重新下载／核验整个目录。

本轮没有生成新安装器、改变版本、打Tag或发布Release。`installerLifecycle:not-run`、`guiAcceptance:false`、`installedAcceptance:false`、`published:false`。下方 v0.1.0 的绿色记录不覆盖本轮UI；后续选定版本后须绑定对应候选执行安装生命周期。Windows GUI／DPI／原生保存取消仍未执行。此后仅补文档记录，不改变此次原生构建所用运行源码与资源。

## v0.1.0 交付记录

2026-10-15：[Release v0.1.0](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.0) 与 [Windows Actions 38746367500](https://github.com/Eysn0130/FundsTrace/actions/runs/38746367500) 绑定源码 `d7547734f91546fb3b81b30a7e4993b644b77154`。Tag 指向该成功候选，后续仅补交付文档。

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
