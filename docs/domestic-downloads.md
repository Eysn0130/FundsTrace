# 国内下载与 Windows 应用内更新

此候选新增 FundsTrace 独立更新通道。维护者已授权官网现有 Windows 下载替换为 FundsTrace；其他平台保留。FundsTrace 与 Analytix 的安装身份、数据目录和更新通道保持独立。

## 唯一最新版记录

- 官网与更新器共同读取 `https://analytix.top/fundstrace/latest.yml`。内容是 JSON（也是合法 YAML），包含产品 ID、版本、不可变安装包 URL、字节数、SHA-256、SHA-512 和 GitHub Release/asset ID。可变 latest 记录还携带 GitHub Release 正文 `releaseNotes`；下载中心从同一份响应显示版本和纯文本说明，旧版无此字段时仍可下载。
- OSS 对象：`analytix-hub-downloads-prod/releases/windows/site/fundstrace/latest.yml`。
- 安装包独立前缀：`releases/fundstrace/windows/<version>/`。仅复制 GitHub 已正式发布的同一个 Windows x64 NSIS EXE，不重新构建。
- GitHub Release 发布或修改说明后触发 `Domestic Windows downloads`；`workflow_dispatch` 可补同步已有最新版或重试。只同步当前最新稳定版；旧版的说明修改不切换下载入口。Draft、prerelease、没有 GitHub SHA-256 digest 的资产和旧版本均拒绝。
- Release 事件读取该标签中的 workflow 配置；`v0.1.8` 等已发布旧标签没有 `edited` 监听，修正其说明后需在 `main` 手动运行同一 `workflow_dispatch`，不能移动旧标签。后续新标签包含监听后，发布与说明修改均可自动同步。[GitHub 触发规则](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows)。
- 上传前校验 GitHub 的字节数和 SHA-256，上传后从 OSS 读回完整文件再次校验，然后用一次 PutObject 切换共同记录。失败保留旧指针和旧安装包；同版本安装包或版本清单改变必须提升版本。修正当前版说明只更新 latest 的 `releaseNotes`，不重写不可变版本清单或 EXE；其余元数据必须完全一致。无自动删除旧包。
- 安装器通过锁定的 ali-oss 6.23.0 `multipartUpload` 直接传入已校验 Buffer，每片 8 MiB、最多 3 片并行、单请求 600 秒，不增加自动重试或延长请求超时。仅安装器的分片初始化、传片和完成使用固定官方 `https://oss-accelerate.aliyuncs.com`，同一 bucket、`oss-cn-heyuan` 和当前 STS 凭据不变；普通读取、整包读回、版本 manifest、latest 和预热回执仍用区域端点，CDN 流程保持原有配置。不自动切换端点。初始化保留 `application/octet-stream` 与一年 immutable 缓存头，初始化及完成阶段都保留 `x-oss-forbid-overwrite: true`；完成后仍执行整包读回校验。SDK 仅对小于 100 KiB 的文件回退到 PutObject，实际 Windows 安装器走分片。[SDK 实现](https://github.com/ali-sdk/ali-oss/blob/v6.23.0/lib/managed-upload.js)、[完成阶段头处理](https://github.com/ali-sdk/ali-oss/blob/v6.23.0/lib/common/multipart.js#L175-L207)、[Node.js endpoint 配置](https://www.alibabacloud.com/help/en/oss/user-guide/access-oss-via-bucket-domain-name)。
- 分片初始化、上传和完成均使用现有 `oss:PutObject`，读回使用 `oss:GetObject`，不调用分片列举或云端 Abort。上传失败只取消本地传输流；已完成分片可能留存并产生存储费用，下次运行重新上传，不提供跨运行断点续传。清理残片需要维护者另行授权；发布器不增加 `oss:AbortMultipartUpload` 或 Bucket 生命周期权限，也不自动配置清理规则。[初始化权限](https://www.alibabacloud.com/help/en/oss/developer-reference/initiatemultipartupload)、[上传权限](https://www.alibabacloud.com/help/en/oss/developer-reference/uploadpart)、[完成权限](https://www.alibabacloud.com/help/en/oss/developer-reference/completemultipartupload)、[中止与残片](https://www.alibabacloud.com/help/en/oss/developer-reference/abortmultipartupload)。
- 所有发布必须通过同一个不取消的 GitHub concurrency group。OSS PutObject 不提供此处可依赖的 If-Match CAS，禁止另起并发手动发布器。脚本在切换前复核当前 ETag、GitHub 最新资产与说明，发现外部改动停止。

### 全新安装包上传诊断

2026-10-20 的两次 Actions 上传分别在单次 PutObject、8 MiB 分片的首批传输处约 600 秒超时。分片初始化成功、首批分片没有完成；已有日志不能确定发送停滞的底层原因。人工暂存 v0.1.8 后的成功运行跳过了安装包上传，不能作为自动上传恢复的证据。

随后诊断运行 `39465539844` 使用普通区域端点：8 MiB 首批三片约 90 秒超时，TLS 验证成功、无 `Expect`，但未记录请求 `finish`；累计 socket 写入字节不能证明 OSS 已收到整片。1 MiB 组在 300.038 秒预算终止前收到 22 片 HTTP 200，终止后另有两片返回 200，合计 24 片；没有完成合并或整包读回。160,337,859 字节需要 153 个 1 MiB 分片，按预算内样本速率外推上传约 34.76 分钟，已超过 25 分钟 job 与 30 分钟 STS，尚未包含合并和读回。该外推只描述此次样本，不能认定恒定带宽或 MTU 等底层根因。

在受保护 `main` 手动运行同一工作流、选中 `verify_upload`，会下载并校验 GitHub 当前真实安装器，依次按 8 MiB、1 MiB（均 3 片并行）上传到 `_verification/<run-id>-<attempt>/<profile>/`。每个对象必须原先不存在；完成后完整读回核对大小、SHA-256 和 SHA-512，不能复用已存在的生产对象冒充成功。测试不写 manifest/latest、不预热、不删除；测试对象及失败残片保留，需要维护者另行清理授权。新运行或 rerun 使用新 run/attempt，不手动覆盖测试对象。

诊断两组都使用同一实际加速 uploader，存在性检查和完整读回仍用区域 client；传输观察器挂在实际 uploader 上。诊断将单请求超时缩为 90 秒，在进度回调处检查每组 5 分钟预算，另受现有 25 分钟 job 上限约束；生产默认 8 MiB 和 600 秒不变。小分片是[OSS 官方超时处理建议](https://www.alibabacloud.com/help/en/oss/network-connection-timeout-handling)中的对比条件，尚不等于已证明根因或选定修复。记录请求发送时间、连接复用、累计 socket 字节和 TLS 状态；这些不能替代 OSS 成功响应及整包读回。至少一组成功仅表示该组完成验证，两组结果需分别读取。默认 8 MiB 组必须实际完成全新对象上传、合并、区域端点完整读回，大小及 SHA-256/SHA-512 与 GitHub 原包相同，且原 latest 指针不变，才能作为默认上传路径的通过证据；只有 1 MiB 成功不够。稳定性还需独立新 run/key 再次通过。

开启加速后，同一候选 `545e8f0` 的独立运行 `39469686098`、`39470102496` 均完成两组全新对象上传和区域端点完整读回：8 MiB 组分别 13.985、15.881 秒，1 MiB 组分别 38.752、35.484 秒，均为 160,337,859 字节且 SHA-256/SHA-512 与 GitHub 原包一致，原指针未变。两次工作流末尾的公开核验均在 `Promise.all(index 1)` 对应的官网 latest JSON 连接处约 10 秒超时，报 `UND_ERR_CONNECT_TIMEOUT`，没有 HTTP 响应，工作流整体仍失败；此处不能判定元数据错误或两个公共入口均不可用，也不撤销已通过的上传证据。既有 Chrome 完整下载哈希通过仅证明该浏览器下载路径。

公开核验由 `verify-public.mjs` 保留 GitHub、官网和下载域名三个固定 HTTPS URL。每个入口最多三次完整请求，依次使用 fetch、独立原生 HTTPS／系统 DNS、独立原生 HTTPS／Google DoH；只有明确的暂时网络错误或本次自有截止时间到期才能进入下一种方式，间隔仍为 2、5 秒。每次 30 秒总预算覆盖解析、TCP、TLS、响应头和响应体，不因切换阶段延长；第三次失败后结束。不会在第一次超时后直接放弃系统 DNS 路径，也不修改系统 DNS、代理或全局 Agent。

原生 HTTPS 使用原域名、SNI、证书及主机名验证，拒绝重定向；DoH 仅查询三个固定公开域名并校验响应。HTTP 非 2xx、证书、JSON、未知异常、元数据、说明或摘要错误立即失败，外来取消不冒充本次超时。所有原字段和两个公共文档均须通过核对。日志只含固定入口、次数、传输、阶段与错误类别，不输出原异常。Node 22.22.1 的 fetch 还有独立的连接时限；原生 HTTPS 允许使用完整的本次预算，但 [autoSelectFamily](https://nodejs.org/download/release/v22.22.1/docs/api/net.html#socketconnectoptions-connectlistener) 只解决 TCP 建连选择，不能保证连接后的 TLS 或响应成功。

[只读回读工作流](../.github/workflows/public-download-check.yml) 分别验证正常流程、受控 fetch 失败后的完整恢复链，以及强制前两级失败后的 DoH 路径；每条仍读取真实公开 JSON 并执行完整元数据校验，各自结果不能相互替代。恢复链必须证明两个 CDN 入口均实际进入原生系统 DNS，记录它的成功或失败，以及最终成功的传输方式；不要求每一级外部网络路径单独永远可达。它不下载或发布安装器，不访问云凭据或修改下载指针。所有当次网络路径均不可达时必须失败；通过一次回读不证明 CDN／路由根因已消除，也不保证以后没有网络故障。

工作流通过 `run.mjs` 启动发布器子进程，只转发固定 schema 的 IPC 诊断。锁定 urllib 在重复回调时会直接输出原始 URL/异常，绕过普通 reporter；父进程不转发依赖 stdout/stderr，并关闭 DEBUG 类日志，避免诊断输出认证信息或任意原始异常。不更换 SDK、代理、agent 或 TLS 验证策略。

## 阿里云与 GitHub 接线

沿用已有私有 OSS bucket、中国内地 CDN、`analytix.top` 和 `download.analytix.top`，不需要 ECS、数据库或新购服务器。检查官网回源目录仍为 `releases/windows/site/`，下载域名可访问新产品前缀。最新版记录必须不缓存或短缓存，安装包长期缓存、支持 Range。

使用 GitHub OIDC 的临时 STS 凭据，不向 GitHub 存放阿里云长期 AccessKey。创建/扩大 RAM 信任关系前由维护者确认具体权限。

使用加速端点前，维护者必须取得用户对开启传输加速及其流量费用的明确授权，并确认同一 bucket 已开通且生效，最长可能需要 30 分钟。开启本身不收费，通过加速端点上传会产生额外加速流量费用；当前候选不会开通云功能。开启需要单独的 `oss:PutBucketTransferAcceleration` 管理权限，不授予发布角色；安装器上传仍仅需原前缀的 `oss:PutObject`，读回仍仅需 `oss:GetObject`，不扩大前缀、凭据或权限。[官方机制与费用](https://help.aliyun.com/zh/oss/user-guide/transfer-acceleration)、[开启 API 权限](https://www.alibabacloud.com/help/en/oss/developer-reference/putbuckettransferacceleration)。

1. OIDC issuer `https://token.actions.githubusercontent.com`；audience `sts.aliyuncs.com`。
2. RAM 信任策略 Action 为 `sts:AssumeRole`；通过 OIDC 调用 STS 的接口名称并不是此处的 Action。此仓库启用了 GitHub 不可变 subject，当前精确值为 `repo:Eysn0130@103180203/FundsTrace@1420540807:environment:production-downloads`。使用 `gh api repos/Eysn0130/FundsTrace/actions/oidc/customization/sub` 核对当前前缀，不能凭仓库名称猜测 subject。同时限制 issuer、audience 和 subject，禁止通配其他仓库。
3. 权限只给 `oss:GetObject`、`oss:PutObject`：资源为 `acs:oss:*:*:analytix-hub-downloads-prod/releases/fundstrace/windows/*` 和上面的一个 latest.yml 对象。不给 Analytix 其他安装包、站点 HTML、删除、ACL 或 Bucket 管理权限。
4. GitHub `production-downloads` environment 配置 `FUNDS_TRACE_DOWNLOAD_ROLE_ARN`、`FUNDS_TRACE_DOWNLOAD_OIDC_PROVIDER_ARN`。ARN 不是秘密。必须限制 environment 只接受 main 分支及维护者正式发布的 v* 标签，不能允许任意开发分支使用云角色。发布工作流仅从受保护 main 读取脚本。权限 JSON 见 deploy/fundstrace-download-publisher-policy.json。
5. 先同步已发布 v0.1.7，验证完整 CDN 下载哈希；再发布带更新器的新候选。候选合并与版本发布遵守原有维护者审阅流程。

## 下载速度与预热

下载域名使用中国内地大文件 CDN，启用 Range 回源；不可变安装包长期缓存，latest 文档不缓存。官网和软件更新器下载同一 CDN 文件，不把 GitHub 下载速度带给国内客户端。

维护者确认后，为同一发布角色附加 `deploy/fundstrace-cdn-prewarm-policy.json`，仅允许对 `download.analytix.top` 调用 `cdn:PushObjectCache`。发布器在安装包、最新版指针校验成功后，对该版本唯一安装包提交中国内地预热，包含 L2，不预热整个目录或每次客户端检查。写入校验绑定版本与散列的回执，重复运行复用，减少重复回源。预热失败会让工作流失败并保留已校验的下载入口，重试可补齐；预热提交成功只表示进入队列，按阿里云说明可能需要 5–30 分钟，不能宣称全部节点已完成。

2026-10-20 当前维护机对 v0.1.7 的 160,011,997 字节文件测得：首次完整下载 35.54 秒，缓存命中后 3.76 秒；两次 SHA-256 与 GitHub 资产一致。首个 1 MiB Range 请求 0.105 秒、HTTP 206、内容与完整文件对应字节一致。此为该机器该次网络结果，不等于国内所有运营商或 Windows 实机测速。控制台该版本人工预热任务随后显示完成 100%。

交互借鉴 [GitHub Desktop 的更新状态与重启边界](https://github.com/desktop/desktop/blob/development/app/src/ui/lib/update-store.ts) 和 [VS Code 的 Windows 更新服务](https://github.com/microsoft/vscode/blob/main/src/vs/platform/update/electron-main/updateService.win32.ts)：在后台处理下载、区分检查/下载/就绪、校验缓存、让用户选择重启。FundsTrace 保持 NSIS 身份；当前显式点击后后台下载，普通退出不会自动安装。未启用未经验证的差分更新：后续要先发布与镜像 blockmap，验证真实跨版本差分和完整下载回退。当前完整安装包可通过 CDN 加速，不能声称已有跨进程断点续传或已完成真实差分验收。

## 客户端

NSIS + `electron-updater`，固定 HTTPS feed，关闭自动下载、退出自动安装、预发布和降级。Main 独占检查、下载缓存和安装操作；renderer 只有固定动作 IPC，验证所属顶层 frame 和精确本机 origin，不接收远程 URL、文件路径或安装参数。下载仅接受 FundsTrace 产品及本域不可变路径，先由 updater 校验 SHA-512，再允许用户显式重启。

顶部右侧胶囊显示检查、更新、圆环下载进度、重试和重启状态。重启前再次读取最新版，离线、缓存版本被取代或散列改变均停止安装。点击重启先出现默认“稍后”的确认框，提示保存所有窗口画布、等待导入和导出结束；随后先关闭所属 sidecar，再启动 NSIS。普通退出不会安装更新。数据、appId、每用户安装身份和卸载默认保留数据不变。网络失败不阻止使用；仍可从官网手动覆盖安装。

现有 v0.1.7 没有更新器，需要先手动覆盖安装一次带更新器的版本。完整自动更新验收需要两份不同版本的真实 Windows 安装包，不能用同版本重装或本机合成 UI 代替。未签名预览沿用现状；HTTPS 与散列保护传输和完整性，不等同于 Authenticode 发布者签名，不关闭未来签名校验。

## 验收与费用

必须分别记录：发布器中断/降级/同版本替换回归；官网产品入口与实际下载哈希；Windows 安装后真实检查→下载→保存提示→sidecar 正常退出→NSIS→重启后新版本，并核对合成案件、画布、偏好与旧资源清除。低磁盘、断网重试、多窗口、普通退出和失败安装也需覆盖。UI/源检查不能冒充已安装 Windows 更新证据。

2026-10-20 官网 v0.1.8 下载对照：同一受控 Chrome profile、同一线上 Windows 按钮，网站代码及浏览器安全、权限设置均未改变；前次未预先登记下载事件时出现 `ERR_BLOCKED_BY_CLIENT`，点击前先注册正式的 `playwright.waitForEvent('download')` 后，实际下载完成，文件为 160,337,859 字节，SHA-256 为 `7c568bc4ca8f0cd4fa9f86ebeb876e234ab46f5aa51f5d1f92a1245f4190ba0e`。该对照支持操作或工具下载处理差异这一解释，不能确定内部具体拦截机制，也不能保证所有 Chrome 用户的结果；此次无需改变网站下载代码或浏览器安全设置。官网浏览器下载验收必须等待实际下载完成并核对文件大小、SHA-256，不能以页面跳转、单独的错误页或 curl 下载成功替代。

按当前约 160 MB 安装包，100 次完整下载约 16 GB/月；复用现有资源只增加存储、CDN 流量、请求和少量回源。实际价格及流量包以当前账号控制台为准。100 次是预算模型，不是服务强制限额；配置费用提醒与 CDN 带宽告警后仍可能产生超额费用，不能承诺固定月费。HTTPS 证书续期、发布失败告警、当前/上一版保留和人工修复入口需要持续维护。

官网新安装与应用内更新都计入下载流量：若有 100 次官网完整下载，另有 100 次应用完整更新，合计约 32 GB，不能只算前一半。预热也会产生 OSS 回源流量和请求。不要用会拒绝空 Referer 的防盗链规则保护费用，否则会误伤桌面更新器和直接下载；先用费用/流量异常告警，出现真实滥用再评估针对性限制。当前完整安装包更新不对移动计费网络静默自动下载，用户点击后才下载。

参考：[electron-builder v26 更新](https://www.electron.build/v26/docs/features/auto-update/)、[阿里云官方 OIDC Action](https://github.com/aliyun/configure-aliyun-credentials-action)、[OSS PutObject](https://help.aliyun.com/zh/oss/developer-reference/putobject)。
