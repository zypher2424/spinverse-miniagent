# MiniAgent — contributor instructions

Independent Python coding-agent teaching project, cumulative v1.2 capabilities. Keep modules readable and the terminal UI connected to the same Runtime.

```bash
.venv/bin/python -m pytest tests -q
./miniagent --demo --yes --task "Fix the addition and run the independent check"
python3 scripts/check_public_files.py
```

- Never commit .env, API credentials, private provider endpoints, runtime sessions, caches, or developer-specific verification records.
- Demo fixtures contain an intentional bug. Copy them into a disposable workspace; do not fix the fixture to make the exercise disappear.
- main/tui/ui/config assemble and display; commands is the shared router. agent → runtime → context/llm/session/planner/task_state/execution.
- Tool modules, permissions and execution do not import main/agent/runtime.
- Validate parameters/resources before permission; recheck before execution. Unknown tools default to deny.
- Use structured results and real evidence. Empty/truncated responses, rejected checks, and failed tests cannot be reported as completion.
- Pair all tool calls with real results or explicit interruption records. Preserve actual side effects after model or logging failures.
- Journal started before execution; stop on log failure. Unknown outcomes pause recovery instead of being replayed.
- Keep independent acceptance files protected. Patch requires an unchanged full-file hash and one unique match.
- UI updates happen in the main event loop; the single background worker communicates through a queue. Cancellation must block late tool calls and preserve logs.
- Shell restrictions are not an OS sandbox; session persistence does not imply exactly-once execution.
- Spinverse community links and contact in public docs are intentional, user-authorized project promotion. Do not send unsolicited messages to people.
