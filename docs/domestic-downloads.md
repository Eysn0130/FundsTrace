# 国内下载与 Windows 应用内更新

此候选新增 FundsTrace 独立更新通道。维护者已授权官网现有 Windows 下载替换为 FundsTrace；其他平台保留。FundsTrace 与 Analytix 的安装身份、数据目录和更新通道保持独立。

## 唯一最新版记录

- 官网与更新器共同读取 `https://analytix.top/fundstrace/latest.yml`。内容是 JSON（也是合法 YAML），包含产品 ID、版本、不可变安装包 URL、字节数、SHA-256、SHA-512 和 GitHub Release/asset ID。可变 latest 记录还携带 GitHub Release 正文 `releaseNotes`；下载中心从同一份响应显示版本和纯文本说明，旧版无此字段时仍可下载。
- OSS 对象：`analytix-hub-downloads-prod/releases/windows/site/fundstrace/latest.yml`。
- 安装包独立前缀：`releases/fundstrace/windows/<version>/`。仅复制 GitHub 已正式发布的同一个 Windows x64 NSIS EXE，不重新构建。
- GitHub Release 发布或修改说明后触发 `Domestic Windows downloads`；`workflow_dispatch` 可补同步已有最新版或重试。只同步当前最新稳定版；旧版的说明修改不切换下载入口。Draft、prerelease、没有 GitHub SHA-256 digest 的资产和旧版本均拒绝。
- 上传前校验 GitHub 的字节数和 SHA-256，上传后从 OSS 读回完整文件再次校验，然后用一次 PutObject 切换共同记录。失败保留旧指针和旧安装包；同版本安装包或版本清单改变必须提升版本。修正当前版说明只更新 latest 的 `releaseNotes`，不重写不可变版本清单或 EXE；其余元数据必须完全一致。无自动删除旧包。
- 安装器通过锁定的 ali-oss 6.23.0 `multipartUpload` 直接传入已校验 Buffer，每片 8 MiB、最多 3 片并行，不增加自动重试或延长请求超时。初始化保留 `application/octet-stream` 与一年 immutable 缓存头，初始化及完成阶段都保留 `x-oss-forbid-overwrite: true`；完成后仍执行整包读回校验。SDK 仅对小于 100 KiB 的文件回退到 PutObject，实际 Windows 安装器走分片。[SDK 实现](https://github.com/ali-sdk/ali-oss/blob/v6.23.0/lib/managed-upload.js)、[完成阶段头处理](https://github.com/ali-sdk/ali-oss/blob/v6.23.0/lib/common/multipart.js#L175-L207)。
- 分片初始化、上传和完成均使用现有 `oss:PutObject`，读回使用 `oss:GetObject`，不调用分片列举或云端 Abort。上传失败只取消本地传输流；已完成分片可能留存并产生存储费用，下次运行重新上传，不提供跨运行断点续传。清理残片需要维护者另行授权；发布器不增加 `oss:AbortMultipartUpload` 或 Bucket 生命周期权限，也不自动配置清理规则。[初始化权限](https://www.alibabacloud.com/help/en/oss/developer-reference/initiatemultipartupload)、[上传权限](https://www.alibabacloud.com/help/en/oss/developer-reference/uploadpart)、[完成权限](https://www.alibabacloud.com/help/en/oss/developer-reference/completemultipartupload)、[中止与残片](https://www.alibabacloud.com/help/en/oss/developer-reference/abortmultipartupload)。
- 所有发布必须通过同一个不取消的 GitHub concurrency group。OSS PutObject 不提供此处可依赖的 If-Match CAS，禁止另起并发手动发布器。脚本在切换前复核当前 ETag、GitHub 最新资产与说明，发现外部改动停止。

## 阿里云与 GitHub 接线

沿用已有私有 OSS bucket、中国内地 CDN、`analytix.top` 和 `download.analytix.top`，不需要 ECS、数据库或新购服务器。检查官网回源目录仍为 `releases/windows/site/`，下载域名可访问新产品前缀。最新版记录必须不缓存或短缓存，安装包长期缓存、支持 Range。

使用 GitHub OIDC 的临时 STS 凭据，不向 GitHub 存放阿里云长期 AccessKey。创建/扩大 RAM 信任关系前由维护者确认具体权限。

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

按当前约 160 MB 安装包，100 次完整下载约 16 GB/月；复用现有资源只增加存储、CDN 流量、请求和少量回源。实际价格及流量包以当前账号控制台为准。100 次是预算模型，不是服务强制限额；配置费用提醒与 CDN 带宽告警后仍可能产生超额费用，不能承诺固定月费。HTTPS 证书续期、发布失败告警、当前/上一版保留和人工修复入口需要持续维护。

官网新安装与应用内更新都计入下载流量：若有 100 次官网完整下载，另有 100 次应用完整更新，合计约 32 GB，不能只算前一半。预热也会产生 OSS 回源流量和请求。不要用会拒绝空 Referer 的防盗链规则保护费用，否则会误伤桌面更新器和直接下载；先用费用/流量异常告警，出现真实滥用再评估针对性限制。当前完整安装包更新不对移动计费网络静默自动下载，用户点击后才下载。

参考：[electron-builder v26 更新](https://www.electron.build/v26/docs/features/auto-update/)、[阿里云官方 OIDC Action](https://github.com/aliyun/configure-aliyun-credentials-action)、[OSS PutObject](https://help.aliyun.com/zh/oss/developer-reference/putobject)。
