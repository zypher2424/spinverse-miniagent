# 参与共建

欢迎围绕真实的 Agent 工程问题提交 Issue 或 Pull Request。MiniAgent 是教学项目，优先保持模块边界清楚、例子可运行、证据可检查。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest tests -q
./miniagent --demo
```

提交前请说明问题、改动、复现方法和验证结果。行为变更增加对应的回归覆盖；文案和样式调整可使用截图或检查现有输出。

工程约束：

- 工具调用先校验资源，再询问权限，执行前复检。
- 失败、拒绝、超时必须保留真实结果；不能把模型的话当作执行证据。
- Context 不拆分工具调用与结果；取消和恢复不丢弃真实副作用记录。
- 验收文件保持独立；修改后运行真实检查。
- `.env`、`.miniagent`、私密会话、实际服务商地址和 API key 不进入提交。
- 不将命令白名单称为 OS 沙箱，不声称会话提供恰好执行一次保证。

使用 AI 辅助贡献时，请自己检查 diff 和运行结果。贡献者会出现在 Git 历史与社区知识沉淀中，优质内容保留署名。

加入项目共创：[自旋宇宙](https://spinverse.cn/) · 联系 **TC_bjtu**。
