## MiniAgent v1.2.0 × Spinverse

**自旋不息，宇宙无界 · Spin Up Your Universe**

一个可以读懂、跑通、验证的 Python Coding Agent，面向 Agent 工程学习与小型真实项目实践。

### 这次发布

- 黑灰底色的全屏终端界面，固定输入框、真实工具状态和权限弹窗。
- 计划执行、上下文预算、统一 ToolResult、九个受限工具。
- 文件发现、带行号搜索、分段读取、全文哈希与唯一匹配 Patch。
- 真实命令输出、独立 pytest 验收、会话与故障窗口恢复。
- Ctrl+C 协作取消、JSONL 自动化、离线演示。
- 中英文 README、架构与学习路线、贡献指南、MIT 许可证和 GitHub CI。

### 快速体验

```bash
git clone https://github.com/zypher2424/spinverse-miniagent.git
cd spinverse-miniagent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./miniagent --demo
```

离线演示的决策来自明确标记的脚本模型，工具、修改、检查和记录真实执行。真实任务使用你自己的模型配置。

### 验证与适用范围

110 项离线测试；公开 CI 状态见仓库 Actions。适用于 macOS/Linux。命令限制不是 OS 沙箱，会话不承诺恰好执行一次；长期 Memory、MCP、Skills、子代理与真正运行隔离尚未实现。

### 来自自旋宇宙

我们聚集 AI 工程师、产品人、创业者、研究者和正在探索 AI 边界的人，分享 AI 落地实战、工具范式、前沿研究与项目共创。

官网：[spinverse.cn](https://spinverse.cn/)

合作 / 共创 / 入群：**TC_bjtu**

**Spin Your Intelligence.** 欢迎一起把想法做成能运行的工程。
