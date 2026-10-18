# 学习路线

本仓库是独立的 v1.2 累计实现，不包含每个历史版本的源码快照。可以按下面的能力顺序读当前代码，每步都有可运行练习。

| 阶段 | 先读 | 练习与验收 |
| --- | --- | --- |
| 模型与消息 | llm.py → context.py | 区分模型文本、工具调用和真实工具结果；观察配对消息 |
| 工具注册 | results.py → tools/schemas.py → tools/registry.py | 触发除零、坏参数、未知工具；按结构化 status 判断 |
| 权限与执行 | permissions.py → execution.py | 对同一写入分别批准/拒绝；非法路径不能进入确认 |
| 上下文工程 | context.py | 注入长历史；按整轮裁剪，原任务保留，工具组不拆散 |
| 计划执行 | planner.py → task_state.py → runtime.py | 没有真实 read/edit/check 证据时不能完成步骤 |
| 会话恢复 | session.py → runtime.recover_inflight | 模拟 started 后无结果；恢复必须暂停，不重放 |
| 搜索与 Patch | tools/repository.py | 搜函数、读范围、改文件；重复匹配和陈旧哈希不落盘 |
| 全屏交互 | commands.py → tui.py → tui.tcss | 查看真实状态、权限弹窗和取消；界面不复制后端逻辑 |

先运行 `./miniagent --demo`。夹具中的错误是练习起点；运行时复制到独立工作区，检查脚本不由模型修改。随后在自己熟悉的小项目上使用 `--check` 做明确验收。

下一阶段可以依次研究：选择性长期 Memory、Skills 与工具的区别、MCP 协议、子代理通信、并发、评测与真正的进程隔离。这些是学习方向，不代表当前已经具备这些能力。

欢迎带着实验结果来 [自旋宇宙](https://spinverse.cn/) 共建：把“想法有道理”推进到“代码和结果能被复查”。
