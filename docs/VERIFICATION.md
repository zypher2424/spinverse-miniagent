# 发布验证说明

## 可复现的离线验证

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest tests -q
./miniagent --demo --yes --task "修复加法函数并运行独立测试" --json
python3 scripts/check_public_files.py
```

发布基线为 **110 项离线测试**。Shell 使用真实临时进程，模型使用 Fake/明确标记的脚本模型；测试不需要 API key，也不访问模型服务。CI 在 Linux/macOS、Python 3.12/3.13 上运行同一套件；实际结果以 GitHub Actions 为准。

覆盖：参数与资源校验、权限、未知工具、非零退出与超时、路径/隐藏目录/符号链接、搜索行号与范围、Patch 唯一匹配和陈旧哈希、确认后内容变化、消息配对、预算、空/截断响应、独立验收、日志失败、崩溃窗口、未知行动暂停、会话锁/恢复，以及全屏发送、批准/拒绝、取消、切换会话和小窗口布局。

## 截图的来源

```bash
.venv/bin/python scripts/capture_demo.py
```

公开截图来自实际 Textual 界面和临时五文件项目。决策是离线脚本模型；搜索、读取、Patch、pytest 和 Session 都是真实实现。脚本不读取 .env，不包含实际服务商、用户主目录、私密会话或真实业务资料。

`docs/assets/terminal.svg` 是可重现的矢量版本，PNG 用于 README。截图不是实时 CI 结果，也不证明所有真实模型都有相同行为。

## 真实模型验证的范围

开发过程中，真实 OpenAI-compatible 模型曾完成代码结构检查与小型算术项目修复：初始测试失败，只修改实现后测试通过，验收文件未变，恢复没有重放行动。该结果是特定环境下的手动联调，不是对其他模型、网关或业务项目的自动保证。

实际网关、密钥、私密对话、完整运行会话与开发者目录不发布。需要在自己的配置和项目上重新验证，请通过 .env.example 配置模型，并用 --check 指定独立验收。

## 完成条件与边界

- 工具成功、模型给出文本、整个任务通过验收是不同状态。
- 拒绝、错误、超时、日志不确定和验证失败不能伪装为完成。
- Ctrl+C 保留真实行动结果；网络请求要等待当前调用返回或超时。
- Shell 不是 OS 沙箱；Session 不承诺外部操作恰好执行一次。
- 任意自然语言条件不自动等价于已验证语义正确，详见架构说明。
