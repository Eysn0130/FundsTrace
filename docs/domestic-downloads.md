# 国内下载与 Windows 应用内更新

此候选新增 FundsTrace 独立通道，保留 Analytix 的下载、身份和更新通道。

## 唯一最新版记录

- 官网与更新器共同读取 `https://analytix.top/fundstrace/latest.yml`。内容是 JSON（也是合法 YAML），包含产品 ID、版本、不可变安装包 URL、字节数、SHA-256、SHA-512 和 GitHub Release/asset ID。
- OSS 对象：`analytix-hub-downloads-prod/releases/windows/site/fundstrace/latest.yml`。
- 安装包独立前缀：`releases/fundstrace/windows/<version>/`。仅复制 GitHub 已正式发布的同一个 Windows x64 NSIS EXE，不重新构建。
- GitHub Release 发布后触发 `Domestic Windows downloads`；`workflow_dispatch` 可补同步已有最新版或重试。Draft、prerelease、没有 GitHub SHA-256 digest 的资产和旧版本均拒绝。
- 上传前校验 GitHub 的字节数和 SHA-256，上传后从 OSS 读回完整文件再次校验，然后用一次 PutObject 切换共同记录。失败保留旧指针和旧安装包；同版本内容改变必须提升版本。无自动删除旧包。
- 所有发布必须通过同一个不取消的 GitHub concurrency group。OSS PutObject 不提供此处可依赖的 If-Match CAS，禁止另起并发手动发布器。脚本在切换前复核当前 ETag 和 GitHub 最新资产，发现外部改动停止。

## 阿里云与 GitHub 接线

沿用已有私有 OSS bucket、中国内地 CDN、`analytix.top` 和 `download.analytix.top`，不需要 ECS、数据库或新购服务器。检查官网回源目录仍为 `releases/windows/site/`，下载域名可访问新产品前缀。最新版记录必须不缓存或短缓存，安装包长期缓存、支持 Range。

使用 GitHub OIDC 的临时 STS 凭据，不向 GitHub 存放阿里云长期 AccessKey。创建/扩大 RAM 信任关系前由维护者确认具体权限。

1. OIDC issuer `https://token.actions.githubusercontent.com`；audience `sts.aliyuncs.com`。
2. RAM role 只信任 `repo:Eysn0130/FundsTrace:environment:production-downloads`。同时限制 issuer、audience 和 subject，禁止通配其他仓库。
3. 权限只给 `oss:GetObject`、`oss:PutObject`：资源为 `acs:oss:*:*:analytix-hub-downloads-prod/releases/fundstrace/windows/*` 和上面的一个 latest.yml 对象。不给 Analytix 其他安装包、站点 HTML、删除、ACL 或 Bucket 管理权限。
4. GitHub `production-downloads` environment 配置 `FUNDS_TRACE_DOWNLOAD_ROLE_ARN`、`FUNDS_TRACE_DOWNLOAD_OIDC_PROVIDER_ARN`。ARN 不是秘密。必须限制 environment 只接受 main 分支及维护者正式发布的 v* 标签，不能允许任意开发分支使用云角色。发布工作流仅从受保护 main 读取脚本。权限 JSON 见 deploy/fundstrace-download-publisher-policy.json。
5. 先同步已发布 v0.1.7，验证完整 CDN 下载哈希；再发布带更新器的新候选。候选合并与版本发布遵守原有维护者审阅流程。

## 客户端

NSIS + `electron-updater`，固定 HTTPS feed，关闭自动下载、退出自动安装、预发布和降级。Main 独占检查、下载缓存和安装操作；renderer 只有固定动作 IPC，验证所属顶层 frame 和精确本机 origin，不接收远程 URL、文件路径或安装参数。下载仅接受 FundsTrace 产品及本域不可变路径，先由 updater 校验 SHA-512，再允许用户显式重启。

顶部右侧胶囊显示检查、更新、圆环下载进度、重试和重启状态。重启前再次读取最新版，离线、缓存版本被取代或散列改变均停止安装。点击重启先出现默认“稍后”的确认框，提示保存所有窗口画布、等待导入和导出结束；随后先关闭所属 sidecar，再启动 NSIS。普通退出不会安装更新。数据、appId、每用户安装身份和卸载默认保留数据不变。网络失败不阻止使用；仍可从官网手动覆盖安装。

现有 v0.1.7 没有更新器，需要先手动覆盖安装一次带更新器的版本。完整自动更新验收需要两份不同版本的真实 Windows 安装包，不能用同版本重装或本机合成 UI 代替。未签名预览沿用现状；HTTPS 与散列保护传输和完整性，不等同于 Authenticode 发布者签名，不关闭未来签名校验。

## 验收与费用

必须分别记录：发布器中断/降级/同版本替换回归；官网产品入口与实际下载哈希；Windows 安装后真实检查→下载→保存提示→sidecar 正常退出→NSIS→重启后新版本，并核对合成案件、画布、偏好与旧资源清除。低磁盘、断网重试、多窗口、普通退出和失败安装也需覆盖。UI/源检查不能冒充已安装 Windows 更新证据。

按当前约 160 MB 安装包，100 次完整下载约 16 GB/月；复用现有资源只增加存储、CDN 流量、请求和少量回源。实际价格及流量包以当前账号控制台为准。100 次是预算模型，不是服务强制限额；配置费用提醒与 CDN 带宽告警后仍可能产生超额费用，不能承诺固定月费。HTTPS 证书续期、发布失败告警、当前/上一版保留和人工修复入口需要持续维护。

参考：[electron-builder v26 更新](https://www.electron.build/v26/docs/features/auto-update/)、[阿里云官方 OIDC Action](https://github.com/aliyun/configure-aliyun-credentials-action)、[OSS PutObject](https://help.aliyun.com/zh/oss/developer-reference/putobject)。
