# FundsTrace 独立桌面交付

状态：2026-10-15，MIT 开源 [v0.1.0-alpha.3 预览已发布](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.0-alpha.3)。 [Windows 原生 Actions](https://github.com/Eysn0130/FundsTrace/actions/runs/38733405640) 与发布标签绑定源码 `d0d569fe415d7798584f3207d0e8817a7916c75f`，包含客户端与独立 Issuer 两个未签名 x64 安装器；后续仅更新交付文档。Windows GUI 未实机验收，真实 Analytix 尚未接入。原目录与业务数据不随源码上传，Analytix 保持不动。常驻工作区重排见 [graph-workspace-plan.md](graph-workspace-plan.md)。

本轮客户端内置分发公钥，公开指纹为 **`FEF434964FE89178`**。该候选的 53 项定向 Python、16 项桌面 Node、前端类型检查／构建、24 项包内资源校验与 10 项原生后端烟测均通过；新增 `bundledPublicKeyRecognized` 检查首次启动／重开时的公钥模式与指纹，合成 FT2 激活重开、错误机器码和更换公钥拒绝仍通过。macOS 源码签发 GUI 仅检查窗口加载和取消，不代表 Windows GUI；公钥识别也不证明生产授权已验收。[alpha.2 预览](https://github.com/Eysn0130/FundsTrace/releases/tag/v0.1.0-alpha.2) 未内置生产公钥，其证据仅属于历史候选。

## 运行与数据边界

[desktop/main.cjs](../desktop/main.cjs) 只负责窗口、受限导航／下载和自己创建的服务；无 preload、无通用 IPC。G6 `/` 保留完整业务界面，`/analysis` 为分析，`/workspace/` 为共享 React 试点；React 尚未覆盖所有旧功能，不因桌面化删除旧入口。

[desktop_backend.py](../desktop_backend.py) 先从 stdin 读取严格的 `{protocol:1, token, dataDir, synthetic}`，再导入应用；token 为每次运行随机 64 位十六进制值，不进入 URL、argv、页面存储或日志。服务仅绑定 `127.0.0.1:0`，返回 `{protocol:1, port}`；Main 经认证健康检查后才加载页面，不接管固定端口上的既有服务。

认证中间件覆盖页面、API 和静态资源，核对 token、精确 Host、Origin／Fetch Metadata，并附加 CSP、no-store 等响应头。Main 的独立 session 只向自有窗口的精确 origin 注入认证头，拒绝未知网络、导航、窗口和权限；renderer 保持 sandbox、contextIsolation、webSecurity 开启，nodeIntegration 关闭。[Electron 安全规则](https://www.electronjs.org/docs/latest/tutorial/security)

正常运行的数据位于 Electron 自身 `userData/data`，不写安装资源，也不使用 Analytix 数据根。Main 通过 stdin EOF 请求自己的 sidecar 停止，超时仅终止持有句柄的子进程；服务崩溃显示故障，不自动重放导入。后台持久任务恢复仍由 Python 服务负责，退出 hook 不能代替断电／Windows 注销恢复。[app 生命周期](https://www.electronjs.org/docs/latest/api/app)

FT2 签发私钥仅由独立 Issuer 管理，客户端仅包含或读取公开验签材料；旧 HMAC 只在正常模式的受保护配置中兼容。缺少配置保持未激活，显示激活页并拒绝业务 API；桌面运行认证不是产品激活许可。激活脚本位于 `static/license.js` 以适配脚本 CSP。不在包内埋入共享 HMAC 秘密。

普通用户安装 alpha.3 预构建 FundsTrace 后，无需设置公钥，只需提供激活页机器码并输入维护者签发的授权码。维护者使用独立 FundsTrace Issuer 加载对应加密私钥，再按机器码签发；私钥与口令不进入客户端或 Actions。源码和自行构建不自动获得此配置：可设置 Actions 非秘密变量 `FUNDS_TRACE_LICENSE_PUBLIC_KEY`、本地构建公钥文件，或由维护者设置绝对路径 `FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE`，完全退出后重开并核对指纹。未配置时仍保持未激活，合成测试授权码不能用于正式分发公钥。[配置与换钥操作](license-activation.md)

## 本地开发

使用 Python 3.11、Node 22.22.1；下列命令在候选根目录执行。Python 桌面锁含 PyInstaller 6.22.3，NPM 锁固定 Electron 44.7.2、electron-builder 26.15.3。首次网页构建仍按 README 安装前端依赖。

```sh
uv pip install --python .venv/bin/python --require-hashes -r requirements-desktop.lock
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix desktop ci
npm --prefix desktop run synthetic
```

`synthetic` 只在源码开发模式创建一次性目录：两组各 200 节点／199 条关系及空案件，正常退出后删除。该模式不继承业务数据根或真实授权配置，冻结后端和已封装壳都拒绝此模式。正常运行用 `npm --prefix desktop start`，不会自动激活或种入示例，也不因预构建包已内置公钥而自动配置源码环境；开发 Python 可通过绝对路径 `FUNDS_TRACE_PYTHON` 指定。

本机 Electron 下载曾遇官方 GitHub TLS 失败，维护者按官方安装方式临时使用镜像并保留 Electron 内嵌官方校验和校验；未关闭 TLS／checksum 或修改系统代理。此为本机安装记录，不是默认镜像依赖或未来网络保证。[官方安装说明](https://www.electronjs.org/docs/latest/tutorial/installation)

## 原生构建与 Windows

[build_desktop_backend.py](../scripts/build_desktop_backend.py) 使用 PyInstaller onedir、控制台管道及显式资源白名单，包含 G6／Worker 等静态资源和 `frontend/dist`，排除合成种子入口；生成平台、架构和资源 SHA-256 清单。它不复制数据、授权或旧打包目录。[electron-builder.cjs](../desktop/electron-builder.cjs) 仅打包壳文件和原生后端，校验后端平台／架构与目标完全匹配，不自动发布。

```sh
.venv/bin/python scripts/build_desktop_backend.py
node desktop/tests/backend-smoke.cjs build/desktop/backend/funds-trace-backend
npm --prefix desktop run pack
```

此前 macOS arm64 已生成并启动未签名 `.app` 目录候选，另有 21 项资源版本的原生后端增强烟测。增加根许可和第三方通知后为 23 项资源，alpha.3 再加入分发公钥后为 24 项，本轮 Windows Actions 已重新构建并检查；没有为证明 Windows 而再次封装 Mac 应用。旧 `.app` 不能证明当前源码或 Windows 候选。

Windows x64 本地 PowerShell 7 入口如下；脚本核对 Node／Python 进程为 x64，准备独立构建环境、按哈希锁同步、构建前端与原生 sidecar、运行桌面测试并生成目录候选，随后对**包内** backend 运行烟测。需已安装 Node 22.22.1、uv 与可用的 Python 3.11，目标用户安装运行不要求系统 Python。

```powershell
.\scripts\build-windows.ps1
.\scripts\build-windows.ps1 -Installer
```

客户端输出在 `build/desktop/electron`，独立签发工具输出在 `build/desktop/issuer`；默认是两个未签名目录，`-Installer` 明确生成名称含 `unsigned` 的两个 NSIS 安装候选，普通用户安装且卸载保留各自应用数据。签发工具具有独立 appId／运行目录，不包含 Python 后端或案件数据。未配置签名或更新服务。PyInstaller 不是跨操作系统编译器，必须在 Windows 原生构建 Windows sidecar；macOS 的脚本检查／包目录不能证明 Windows 可运行。[PyInstaller 平台约束](https://pyinstaller.org/en/stable/index.html)、[electron-builder v26](https://www.electron.build/v26/docs/features/multi-platform-build/)

脚本校对包内 manifest 与每项静态资源，再以中文／空格临时资源路径启动包内程序，验证包内公钥模式和指纹、未激活入口、强制终止持有的子进程、同一空库重开和 stdin EOF 正常退出；使用公开 RFC 测试材料验证 FT2 激活／重开、错误机器码及更换公钥拒绝。测试公钥只写入隔离临时状态，不替换分发公钥。它不读业务库、不继承测试外的私钥或口令，不宣称公钥识别等于生产授权验收、空库重开等于导入恢复。`native-backend-smoke.json`、`windows-candidate.json`／`.sha256` 仅记录版本、相对产物文件名、内容哈希和检查范围；中途失败不会生成新的 passed 报告。

清单记录 Windows 实际检出／封装字节。`.gitattributes` 未强制 LF 的许可证、SVG 和锁文件可能由 Windows `core.autocrlf` 转为 CRLF，不能直接拿 macOS 工作区文件哈希判为源码漂移；本轮已逐项验证差异仅为既定检出换行，安装器与随附清单按实际字节严格核对。

首轮原生 CI 完成编译和 NSIS，但包内后端烟测失败。排查发现 Node 向 stdin 写 UTF-8 JSON，而冻结 Python 忽略 `PYTHONUTF8`；Windows 管道的本地编码会破坏中文数据目录。现于读取启动参数前明确配置 stdin UTF-8；现有进程回归改用 cp1252 管道和未转义中文，修复前两例失败、修复后两例通过。烟测仅增加固定阶段名和错误码，仍不输出 token、路径或子进程 stderr。打包命令明确 `--publish never`，发布继续独立执行。[PyInstaller 解释器选项](https://pyinstaller.org/en/stable/spec-files.html#specifying-python-interpreter-options)

[windows-candidate.yml](../.github/workflows/windows-candidate.yml) 只允许手动触发：单 Windows job、中文源码目录、53 项定向 source 进程／恢复／授权／资源回归、上述原生构建入口、3 天产物留存。选择 `installer=true` 生成并留存两个未签名 NSIS；默认保留两个 `win-unpacked` 目录。工作流可接收非秘密验证公钥，不接收签发私钥或口令；不用业务数据、真实授权、签名、Release 或仓库写权限。只上传所选候选和证据文件，避免重复保留安装器与解包目录，也不能上传整个 build／运行目录。预览发布由维护者核对成功运行、候选 SHA 和产物哈希后单独执行。[GitHub 手动运行条件](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

## Windows 实机验收顺序

本机为 macOS arm64，无交互式 Windows 主机或 VM。Windows 原生构建和 sidecar 检查由 Actions 承担；下表 GUI、系统 DPI、安装／卸载与原生保存交互保持 **NOT_EXECUTED（Windows GUI）**，不作为本次预览发布的通过标记。以后具备实机时按受影响流程补充，避免反复重跑全部矩阵。

| 顺序 | 实机步骤与通过条件 | 证据边界 |
| --- | --- | --- |
| 1. 原生与中文路径 | Windows x64／PowerShell 7，在如 `C:\FundsTrace 验收\源码 项目` 的路径构建；运行包内 `.exe`，核对 native manifest、资源、烟测和目录候选哈希 | CI 可验证构建及 sidecar；不能据此填写 Electron GUI 已通过。 |
| 2. DPI 与密集表格 | 首先在 100% 系统缩放完成查询、勾选、前后流水、上图、邻域；再看 150%／200%，125% 在确有目标设备时补测。记录分辨率、系统缩放、应用缩放；确认底部滑轨、分页、焦点和完整金额可达，文字实际可辨 | 系统 DPI 与应用菜单缩放分别记录；CSS 视口、DPR 模拟或尺寸计算测试不替代 Windows 字体／Canvas 实测。 |
| 3. 原生保存／取消 | 合成 XLSX 和图像保存到中文／空格目录，XLSX 回读金额与币种，图像可打开；分别取消保存，不产生新文件并显示取消反馈 | React／G6、分析 HTML 和案件包各用其真实入口；不把“开始下载”当落盘完成，不上传导出文件或完整用户目录。 |
| 4. 退出与恢复 | 隔离测试用户下，导入进行中结束该测试实例及其归属服务后重开；中断任务显示错误、没有部分写入，重新预览确认后仅写一次；另查完成回执重读不重复导入 | 源码 `--synthetic` 每次创建新临时目录，不能用于跨启动保留任务的验收。封装业务页须具备维护者批准的隔离合成数据及测试授权；缺少时只验激活页，其余保持未执行。不得强杀按名称猜到的其他 Python 进程。 |
| 5. 安装与离线 | 有目录候选证据后，才按需生成 NSIS；干净测试用户安装、断网启动、卸载保留合成数据、重装重开；有第二版本时再验升级。混合 DPI 拖窗在具备双屏设备时执行 | 不安装到现有业务用户环境；未签名预览不代表正式版验收，升级无候选时保持未执行。 |

Main 现在按目标显示器 `workArea` 的 DIP 限制初始窗口、最小宽高和居中位置，修复小逻辑工作区可能被 1440×940／最小 960×640 撑出屏幕的问题；不再次乘除 `scaleFactor`。这只覆盖窗口创建，运行中切换 DPI／移动显示器仍需上表实测。保留系统及应用缩放；本轮表格字体提升到 11 px，提供行距与完整文本显示选项，不继续靠缩小字号压缩。[Electron screen](https://www.electronjs.org/docs/latest/api/screen)、[Display 工作区单位](https://www.electronjs.org/docs/latest/api/structures/display)

## 当前证据与待验收

| 证据层 | 已有结果 | 明确限制 |
| --- | --- | --- |
| 本轮定向回归 | alpha.3 Windows CI：53 项 Python、16 项桌面 Node、前端类型检查／构建通过 | Python 283、React 37、旧版 Node 62 及 cp1252 中文启动回归属于此前候选，本轮不重复全套；未改 React、G6 金额／导出契约。 |
| 本轮 Windows 原生后端 | Windows x64、Python 3.11.9、Node 22.22.1：24 项资源经 CI 校对；10 项烟测通过，包括首次启动／重开识别包内公钥模式与指纹，以及中文／空格路径、未激活入口、归属子进程强制退出、同空库重开、EOF、合成 FT2 激活重开及错误机器／换公钥拒绝 | 不是 GUI、生产授权、业务导入或任意断电恢复保证。 |
| 本轮源码 Issuer GUI | macOS 源码窗口加载与原生对话框取消已检查 | 不证明 Windows GUI、真实密钥创建或生产签发。 |
| 此前 macOS 后端／源码 Electron | 21 项资源版本后端烟测、合成 200／199 G6 和密集表格加载、正常退出；此前分析、React、原生保存及取消 | 仅为各自当时的候选，不代表当前 Windows GUI。Worker 专项成功与回退仍未独立区分。 |
| 上轮 macOS `.app` 目录 | 上一候选 20 项资源和壳代码已核对，独立启动／激活 JS／退出通过 | 不是本轮新增许可资源与窗口规则后的包；本轮不为证明 Windows 而重复封装 Mac 应用。缺授权未解锁业务，未做签名、公证或 Windows 验收。 |
| 本轮 Windows 双安装器 | Actions 原生生成 `FundsTrace-0.1.0-alpha.3-windows-x64-unsigned.exe` 与 `FundsTrace-Issuer-0.1.0-alpha.3-windows-x64-unsigned.exe`；客户端已内置分发公钥，发布后 GitHub 资产摘要和大小均相符 | 未签名、无私钥或口令；GUI、系统 DPI、实际安装／卸载／升级、系统保存交互与生产授权／已激活业务恢复仍未实机验收。 |

本轮已下载核对 Actions ZIP 摘要、24 项资源、两个安装器与四份清单；额外公开的 `activation-public-key.pem` 与包内公钥摘要及指纹一致，共七份发布资产。本地严格核对五条校验记录；解包目录的 1587 条摘要由 CI 生成，未在 macOS 下载解包目录复核。两个安装器摘要如下：

| 安装器 | 字节数 | SHA-256 |
| --- | ---: | --- |
| FundsTrace 0.1.0-alpha.3 x64 | 168295048 | `0e0454af6907912cfd628849bced6740f02f333f9ad5350e14466cc4f4e22e20` |
| FundsTrace Issuer 0.1.0-alpha.3 x64 | 111159382 | `6cc8b5bd416a629eee31404a19e3b5f0b4382ca70cd2143738ebeba900abae69` |

已逐项核对 GitHub 七项资产的上传完成状态、摘要和大小，发布标签指向上述 green 源码。构建报告中的 `published=false` 表示构建脚本未执行发布；GitHub 预览由维护者核对后单独发布，报告保持原样。

下载保存须区分服务生成成功和磁盘落盘完成；Electron 使用原生保存对话框，并处理取消／中断，不允许任意远程 URL 或自动打开下载的 HTML。[DownloadItem](https://www.electronjs.org/docs/latest/api/download-item)

## 未来成为 Analytix 的一个导航项

长期入口是 [共享 React 面板](frontend-host-adaptation.md)＋宿主 adapter，不是把独立 Electron 或第二套数据库搬入 Analytix。`funds-panel.v1` 目前是 UI port；浏览器 `File`、`AbortSignal`、原值 DTO 不是已获接受的 IPC、权限或 protected-display 契约。G6 先保留独立页，后续按有界数据、创建／更新／销毁的控制器渐进抽离，不复制另一套资金聚合。

侧栏由**有效 contribution＋Core 已确认 enabled/available＋当前案件 session**派生；启停后重读状态，换案、同案重绑或撤销先清旧显示／回调。真实 Host 必须在用例入口核验能力、案件和代次，提供有限来源／保存动作及受保护显示；缺能力不回退独立 HTTP。保持一个 Go Harness、案件／快照 authority 和每个用例的一个数据所有者，不双写、不开共享 active-case 库。现有宿主缺口与固定 commit 研究保留在 [windows-and-plugin-plan.md](windows-and-plugin-plan.md) 和 [electron-boundary.md](electron-boundary.md)，未来实施前须重新核对实际 Host。
