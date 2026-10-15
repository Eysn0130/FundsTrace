# G6 4.8.19 本地校验性能补丁

当前图引擎继续使用 G6 4.8.19，仅修改关系图数据校验中的端点成员查询。未替换引擎、未跳过校验，未改变金额、关系或账户角色。

## 来源与许可

- 上游固定版本：[AntV G6 4.8.19](https://github.com/antvis/G6/tree/4.8.19)。
- 对应源码：[dataValidation](https://github.com/antvis/G6/blob/4.8.19/packages/core/src/util/validation.ts)。
- 上游许可：[MIT，Copyright (c) 2018 Alipay.inc](https://github.com/antvis/G6/blob/4.8.19/LICENSE)。本文件记录本地修改，不替代上游版权与完整许可文本，也不证明整个 FundsTrace 项目已满足公开条件。
- 完整上游许可随源码保留于 [G6-LICENSE](G6-LICENSE)，并纳入原生后端资源白名单。其来源为固定 `4.8.19` 标签的 `LICENSE`，不是整个应用的许可证，也不代表已完成所有打包依赖的通知审查。
- 补丁前本地文件 SHA-256：`9832ed1b47589504cb6da481ae385a43a51f0cece60815cf5bfeaeefac3b21d7`。
- 补丁后完整文件 SHA-256：`c1fd44126c37e95f372f7fcf75734e7294e247d976595b95b3120eae9b7f56b0`。

补丁前哈希标识本地保存的既有文件；不是对其与某个远端分发包逐字节一致性的额外声明。

## 唯一修改

原校验先收集节点与 Combo 的 ID，随后逐边使用两次 `Array.includes`。本地补丁只将该集合构造为一次 `Set`，将该函数的两个查询改为 `Set.has`。保留首次非法边的 `find`、遍历顺序、告警内容及其余所有分支。二者均采用 SameValueZero 成员语义。

保留上游边界：节点 ID 的字符串检查；树数据分支；节点与 Combo 端点；重复 ID 在后续渲染阶段处理；该校验不单独要求 Combo ID 或边端点为字符串。异常输入原有的异常不会被吞掉。

固定版本的两个调用方行为不同：`Graph.data` 调用校验后仍设置数据，`Graph.changeData` 在校验失败时返回而不修改。补丁保留此差异；调用方仍需遵守 FundsTrace 的数据契约，不应以该差异作为接收非法数据的功能。

## 复现与验证

在项目根目录执行：

```sh
.venv/bin/python scripts/patch_g6_validation.py --check
.venv/bin/python -m pytest tests/test_g6_validation.py -q
```

还原到**上述精确补丁前文件**后，可显式运行 `--apply`。脚本不联网，只接受两个完整哈希；补丁仅替换一个精确片段。已应用时幂等，未知文件、其他 G6 版本及符号链接会被拒绝，不能把此补丁盲套到升级后的引擎。

Node 回归从当前 vendor 中提取真实校验函数执行，并仅反向替换这一片段，比较原版和补丁版的返回、完整告警、异常及输入不变性；冻结的基线目录不是 CI 依赖。另以禁用 `Array.prototype.includes` 和 `Set.has` 操作计数检查目标扫描路径，不使用耗时阈值冒充性能证明。

## 性能证据的边界

本轮仅含合成数据的 Chrome CPU 诊断记录中，5000 个节点、4999 条关系的校验路径里，原版逐边成员扫描约占 204 ms（采样归因，非正式交互延迟样本）。这支持定位复杂度问题，不能直接推导补丁后的首绘收益；最终效果须由同一环境、同一图规模、冻结源码的交替 A/B 样本判断。诊断产物位于本地忽略的 `output/graph-render/baseline-profile.json`，不含业务数据，不是发布验收依据。
