# 源码与私有数据边界

## 交接方式

接管时通过明确的文件白名单建立 `funds-trace` 源码候选。原工作目录没有改名、搬移或清理；候选没有连接原数据库。原 Git 历史和远端尚未取得，不凭空补造作者或发布历史。

| 分类 | 本轮处理 | 后续要求 |
| --- | --- | --- |
| `app/*.py`、`static` 文本源码、本地 G6、启动入口 | 复制到候选；文本统一 UTF-8/LF | 按业务职责逐步维护；确认来源与许可证 |
| 原数据库、uploads、photos、license.dat | 留在原目录，不复制 | 私有存储、访问控制和备份；测试使用合成数据 |
| backup、WAL 恢复副本、隔离文件 | 留在原目录，不复制 | 后续恢复需独立任务和副本，不能当测试样本 |
| 旧 tests 的诊断/修补脚本、输出和打包副本 | 不复制 | 按实际需要迁移可复现行为，不批量执行 |
| build、dist、运行日志和缓存 | 不复制 | 构建生成；正式分发须另验包内内容 |
| 授权生成器、内嵌签名材料 | 不复制签名值及生成器 | 真实材料由维护者在受保护运行环境管理 |
| 原单位图标、标识及模板身份示例 | 不复制图片；文本改为中性品牌及合成示例 | 公开前确认所有资源来源与使用权 |

当前应用图标来自维护者确认的 PNG 方案，批准原图、透明母版与派生资源的边界见 [品牌资产](brand/README.md)；不使用原单位图标或标识。已有 `static/vendor/g6.min.js` 保留为本地锁定的 G6 4.8.19 及校验补丁，来源与哈希见 [G6-PATCH.md](../static/vendor/G6-PATCH.md)；完整 MIT 文本保留为 `static/vendor/G6-LICENSE` 并列入桌面资源。补丁前本地文件未被冒认为已独立核验的上游分发包。公开维护快照的来源和第三方通知统一记于 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)，后续新增依赖须继续保留通知。

## 运行目录

`app/paths.py` 定义数据根，`FUND_DATA_DIR` 同时适用于数据库、上传、照片、授权、日志及缓存。默认 `data/` 被 `.gitignore` 排除。私有目录可以位于源码外部；不要对原业务目录直接进行升级试验。

`.gitignore` 是提交边界的一部分，不能替代已跟踪文件或历史检查，也不能阻止应用读写被指定的目录。公开导入只包含维护源码、合成测试、锁文件和文档；原目录、Git 历史、运行数据、旧包及本地验收输出不随之上传。

案件删除清理该案件的数据库记录和照片。注册上传按待确认 7 天、完成／失败 30 天留存，并提供案件内清理预览及确认；活动任务、来源引用和队列重试边界见 [import-lifecycle.md](import-lifecycle.md)。这些规则不等于删除案件时擦除运行目录中的所有原件，也不授权扫描未知文件。

数据库事务与文件补偿覆盖本轮测试的可处理异常。它们不是数据库和文件系统之间的断电原子事务；进程被强杀、磁盘不可写或恢复操作本身失败时，仍需恢复策略和专项故障验证。

## 授权兼容边界

2026-10-15 维护者已调整后续分发策略：`FundsTrace v0.1.x` 暂不要求授权码启动。正常源码和包内启动已移除产品许可门卫。下述仅为已发布 alpha.3 与保留的历史验签接口；见 [分发与验证](desktop-delivery.md)。密钥、签发工具和已有授权记录保留私有；桌面请求认证、案件隔离和更新包完整性检查仍需保持。

历史独立版采用 FT2 Ed25519 公钥验签；维护者使用独立 Electron Issuer 持有签发私钥，客户端只包含公钥。公钥可在构建时配置，或由部署维护者显式设置绝对路径 `FUNDS_TRACE_LICENSE_PUBLIC_KEY_FILE`。缺少或损坏时保持未激活。授权文件继续随 `FUND_DATA_DIR` 隔离，操作步骤见 [授权配置](activation-history.md)。

旧 HMAC 授权继续要求维护者提供与原部署一致的 `FUNDS_TRACE_LICENSE_SECRET`；本轮没有读取、迁移、生成或轮换真实材料，也没有读取旧授权记录。测试仅使用公开标注的合成配置。公开安装器不含共享 HMAC 密钥、签发私钥或默认受信测试公钥；无需让普通用户持有签发秘密。后续 Analytix 接入仍须使用宿主权限契约，当前免激活不授予访问其数据库的权限。

## 许可与发行边界

1. 2026-10-15 维护者授权公开 `Eysn0130/FundsTrace` 并选用 MIT，该决定保留为历史。自定义的[循迹 FundsTrace 社区使用与源码许可协议 1.0](../LICENSE)已于 2026-10-21（America/Los_Angeles）通过 [PR #36](https://github.com/Eysn0130/FundsTrace/pull/36) 合并 main；首次采用及合并提交见[许可说明](licensing.md)。这是源码可见许可，非 OSI 标准开源；同一文件附完整英文参考译文，以中文为准。本次源码许可采用不代表发布新安装包，也不替换既有发行物的许可。
2. 本仓库从源码维护快照开始，来源记于 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)；未导入原历史，不冒称初始快照作者独创全部原代码。历史 [MIT 文本](../LICENSES/MIT.txt)、原作者版权及第三方许可保留，历史代码和当前快照沿用的 MIT 部分，其既有权利不因新协议追溯回收。
3. 核对 G6、图标、字体及未来引入组件的具体来源、版本、许可证和通知要求。
4. 审查全部待提交文件及后续取得的历史，确保没有业务数据、授权材料或私有环境信息。
5. 稳定发行前补充 Windows 安装后数据目录、升级／恢复和断网用户入口证据；本次未签名预览单独记录原生构建及 sidecar 检查，不使用 Windows GUI 已验收标记。

社区许可仅适用于团队拥有或有权授权、并明确标注采用它的材料；材料位于同一仓库或安装包并不自动改变原许可。维护者已明确确认最初接手的原同事开发代码属于 CodeHaven969、linzijie6524 已确认的完整授权范围。此项确认不将原代码改称为维护者独创，不撤销历史 MIT 或第三方权利，也不推定其他未确认材料的授权；来源、版权和适用通知继续保留。具体覆盖边界及授权留证要求见[许可说明](licensing.md)（[English](licensing.en.md)）。

一般使用与分发规则、授权联系人及实际许可主体要求统一见[许可说明](licensing.md)。保留 CodeHaven969、linzijie6524 的本项目研发、Agent 开发及各自其他产品闭源商业集成特许，包括在线服务、必要客户使用和相应源码公开豁免；适用通知与实际授权范围仍须遵守。不得将该特许扩展为向无关第三方单独换皮转售或独立商业再许可的权利。新增贡献的权利确认与留证按源码仓库的 `CONTRIBUTING.md` 执行。软件许可不改变上文的免激活策略、桌面请求认证或历史密钥保留要求。

## GitHub 与轻量治理

2026-10-15，维护者已授权创建公开 [Eysn0130/FundsTrace](https://github.com/Eysn0130/FundsTrace)、采用 MIT、手动运行 Windows Actions 并尝试发布未签名预览。该历史授权替代当时“先私有”的建议；2026-10-21 采用社区许可的当前边界见上文，不改写这项历史决定。源码公开与二进制预览发布分别记录，不以 Windows GUI 已验收为标记。原目录及业务数据仍在上传边界之外。[GitHub 许可说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository)

仓库将自动 PR 源码检查与手动 Windows 候选工作流分开，复用既有脚本和锁文件，固定 Action 完整 SHA、只读 token、取消过时运行并限制超时；不调用真实 Provider、不注入授权材料、不自动发布。跨 Harness 分工、main 保护与 Eysn0130 审阅边界统一见 [开发与贡献](../CONTRIBUTING.md#分工和-pr)，运行与预览发布记录见 [桌面交付](desktop-delivery.md)。工作流和合成回归不能替代 Windows 的真实 DPI、系统保存和任务恢复 UI 验收。[手动触发要求](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)、[Actions 权限与依赖固定](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)

治理参考及适用取舍：

- [OpenAI Codex 工作流策略](https://github.com/openai/codex/blob/main/.github/workflows/README.md) 将快速反馈与较昂贵的跨平台验证分开；[repo-checks.yml](https://github.com/openai/codex/blob/main/.github/workflows/repo-checks.yml) 复用脚本。FundsTrace 后续 PR 默认使用现有定向检查，Windows 打包先手动运行，避免每次改文档都完整封装。
- [Anthropic Python SDK CI](https://github.com/anthropics/anthropic-sdk-python/blob/main/.github/workflows/ci.yml) 明确检查职责并取消过时任务；[贡献说明](https://github.com/anthropics/anthropic-sdk-python/blob/main/CONTRIBUTING.md) 提供可复现命令。本项目沿用 README／AGENTS／锁文件维护这些入口，不复制 SDK 生成、API 兼容或自动发布链。
- 维护者采用单一 main、短分支与小范围 PR，由 Eysn0130 审阅后合并；先启用 PR／自动检查保护，有独立贡献身份后再启用强制 Code Owner 批准。暂不增加 CLA、固定双审、复杂工作流框架或额外架构层。接受新的外部贡献前，明确并记录贡献者有权按社区许可提供材料，以及团队另行授予机构／商业许可和指定特别许可所需的权限；普通 PR 和社区许可本身不包含版权转让或无限再许可。协作细则见 [CONTRIBUTING.md](../CONTRIBUTING.md)。同时明确维护范围，补真实可用的私密问题报告渠道；不冒用上游的安全邮箱或赏金承诺。[OpenAI 当前贡献政策](https://github.com/openai/codex/blob/main/docs/contributing.md)
