<div align="center">

<img src="docs/assets/banner.svg" width="100%" alt="MiniAgent × Spinverse — Spin Up Your Universe" />

### 一个可以读懂、跑通、验证的 Coding Agent

**从模型回复，到真实执行。让 Agent 工程不止停留在概念里。**

[![CI](https://github.com/zypher2424/spinverse-miniagent/actions/workflows/ci.yml/badge.svg)](https://github.com/zypher2424/spinverse-miniagent/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-3cca79)](LICENSE)
[![Spinverse](https://img.shields.io/badge/Community-Spinverse-111111)](https://spinverse.cn/)

[English](README.en.md) · [快速开始](#五分钟跑起来) · [架构](docs/ARCHITECTURE.md) · [社区](COMMUNITY.md) · [参与共建](CONTRIBUTING.md)

</div>

MiniAgent v1.2 是一份面向学习与实践的 Python Coding Agent：全屏终端对话、计划执行、工具权限、代码搜索、精确 Patch、真实检查与会话恢复，全部连接到同一套可读的 Runtime。

这是 **MiniAgent × 自旋宇宙（Spinverse）** 的开源工程实践。我们关心 AI 怎么真正跑起来、怎么进入业务、怎么创造真实价值，欢迎一起读代码、做实验、共建真实项目。

![MiniAgent 全屏终端演示：工具状态来自真实执行](docs/assets/terminal.png)

> 截图来自离线演示：决策使用明确标记的脚本模型；文件搜索、Patch、pytest 和会话记录是真实执行。自由任务连接你自己的模型。

## 五分钟跑起来

支持 **macOS / Linux、Python 3.12+**。先不配置任何模型或密钥：

```bash
git clone https://github.com/zypher2424/spinverse-miniagent.git
cd spinverse-miniagent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./miniagent --demo
```

进入全屏界面后输入 `/demo`，确认 Patch 和命令执行，即可看到：

```text
发现文件 → 搜索函数 → 分段读取与哈希 → Diff 确认 → Patch → pytest → 保存会话
```

也可以跑一次自动化演示：

```bash
./miniagent --demo --yes --task "修复加法函数并运行独立测试"
```

`--yes` 是显式自动确认选项，硬路径/命令策略仍生效。演示复制五文件夹具到独立目录；原始练习文件保留故意错误。

## 连接真实模型

```bash
cp .env.example .env
# 编辑 .env，填写实际服务商配置与真实模型 ID：
# OPENAI_BASE_URL / OPENAI_API_KEY / MODEL_NAME
./miniagent --workspace /你的项目目录
```

支持提供 Chat Completions 与 Tool Calling 的 OpenAI-compatible 服务。进程环境变量优先；程序只读取当前项目自己的 `.env`，没有预置密钥，也不会自动读取其他项目配置。

为修改任务指定独立验收文件：

```bash
./miniagent --workspace /你的项目目录 \
  --check tests/test_feature.py \
  --task "修复该模块，保留验收文件，并运行真实检查"
```

## 已经实现

| 能力 | 实际行为 |
| --- | --- |
| 全屏 TUI | You / MiniAgent 对话、真实工具状态、固定输入框、授权弹窗；小窗口自动压缩间距 |
| 工具执行 | 九个工具、统一 ToolResult、参数和资源校验、执行前复检 |
| 计划执行 | 1–6 步线性计划，步骤状态和工具证据关联，有限修正 |
| 代码定位 | 文件发现、带路径与行号的字面量搜索、分段读取与完整 SHA-256 |
| 精确修改 | old_text 必须唯一匹配，全文哈希须一致，预览 Diff 后确认 |
| 真实检查 | stdout / stderr / returncode 入记录；验收失败不能报告完成 |
| 会话恢复 | 原子 Checkpoint、行动日志、进程锁；结果未知时暂停核对 |
| 取消 | Ctrl+C 阻止迟到的模型工具调用，终止运行中的 Shell，保留已知结果 |
| 上下文预算 | 完整历史与请求上下文分离，按整轮裁剪，保留原任务约束 |
| 自动化接口 | 单次任务、JSONL 事件与明确退出码；离线测试不调用真实模型 |

工具：`calculator`、`read_file`、`write_file`、`system_info`、`shell`、`list_files`、`search_text`、`read_file_range`、`apply_patch`。

长期 Memory、Skills、MCP、子代理和 OS 级隔离属于后续路线，当前没有实现。详见 [学习路线](docs/LEARNING.md) 和 [架构与边界](docs/ARCHITECTURE.md)。

## 在终端里使用

| 操作 | 行为 |
| --- | --- |
| Enter | 发送任务 |
| Ctrl+C | 取消当前任务 |
| 点击工具行 / 聚焦后 Enter | 展开真实工具结果 |
| `/help`、`/status`、`/plan` | 帮助、运行状态、计划与证据 |
| `/actions`、`/history` | 行动日志和最近对话 |
| `/sessions`、`/resume ID` | 查看与恢复会话，不自动重放历史工具 |
| `/retry`、`/resolve ID 结论` | 显式继续、记录未知行动核对结果 |
| `/new`、`/exit` | 新建会话、保存退出 |
| `/community` | 自旋宇宙介绍与共创入口 |

```bash
./miniagent --tui                         # 强制全屏
./miniagent --no-tui                      # 逐行输出
./miniagent --demo --yes --task "修复加法" --json
./miniagent --workspace /原项目目录 --resume 会话ID
```

一次性 `--task`、`--json`、`--plain` 和非 TTY 管道保持逐行模式。成功 exit=0，任务未完成 exit=2，启动/记录失败 exit=3。

## 验证与边界

```bash
.venv/bin/python -m pytest tests -q
python3 scripts/check_public_files.py
```

当前套件包含 **110 项测试**，覆盖工具、权限、搜索、Patch、预算、会话故障窗口、独立验收和全屏交互。GitHub CI 的实际状态见顶部徽章。演示模型与真实 API 验证分开记录，见 [验证说明](docs/VERIFICATION.md)。

Shell 白名单、固定 cwd、`shell=False`、超时和环境白名单 **不是 OS 沙箱**；获准运行的项目代码仍可能产生副作用。Session 不提供恰好执行一次保证；未知行动必须先核对。上下文 token 使用估计值，模型回答目前按完整响应显示。

## 自旋宇宙 · Spinverse

**自旋不息，宇宙无界。**<br>
**Spin Up Your Universe.**

这里聚集 AI 工程师、产品人、创业者、研究者，以及所有正在探索 AI 边界的人。

- **AI 落地实战**：Agent 工程、大模型部署、业务交付、真实项目复盘。
- **工具与范式**：MCP、RAG、Agent、评测与 Workflow，让 AI 真正干活。
- **前沿雷达**：论文、新模型、新产品和开源项目，讨论真正值得关注的东西。
- **连接与合作**：项目共创、人才连接、内推机会与线下 Meetup。

官网：**[spinverse.cn](https://spinverse.cn/)**<br>
合作 / 共创 / 入群交流：**TC_bjtu**<br>
Slogan：**Spin Your Intelligence.**

[认识社区与群规](COMMUNITY.md) · [参与项目共建](CONTRIBUTING.md) · [分享给同频的人](docs/SHARE.md)

愿你在这里，找到自己的轨道。

## License

[MIT](LICENSE) · 欢迎学习、使用与贡献；优质社区内容保留贡献者署名。
