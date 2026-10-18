"""统一工具 Schema：告诉模型有哪些工具可用、各自的参数结构。

五个工具：shell / calculator / read_file / system_info / write_file。
shell 使用结构化 command + args，不支持整段命令字符串。
本模块是最底层，不 import 其他项目模块。
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "shell",
            "description": (
                "在工作区目录运行受限命令，返回 stdout/stderr/returncode。"
                "只读 pwd、ls、git status/diff/log 自动允许；"
                "python/python3 项目内脚本、pytest 项目内测试需确认。"
                "15 秒超时，输出截断。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                                "description": "程序名，例如 python；不是整段命令字符串"},
                    "args": {"type": "array", "items": {"type": "string"},
                             "description": "参数列表，例如 [demo/check.py]；不支持命令组合"},
                },
                "required": ["command", "args"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "对两个数做四则运算：a <op> b。operation 取值 add/subtract/multiply/divide。",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "第一个操作数"},
                    "b": {"type": "number", "description": "第二个操作数"},
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "要执行的运算",
                    },
                },
                "required": ["a", "b", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取工作区内的 UTF-8 文本文件内容，path 为相对工作区根的路径"
                "（如 config.py）。需要查看某个文件内容时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根的文件路径"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "system_info",
            "description": (
                "返回本机系统与硬件环境信息：操作系统、hostname、机器型号、芯片、"
                "CPU 核数与负载、内存、磁盘、Python 版本、加速器/GPU。"
                "需要了解运行环境时使用。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "写入工作区内的 UTF-8 文本文件。path 为相对工作区根的目标路径"
                "（如 notes.txt），content 为完整文本。这是写入操作，执行前会确认。"
                "需要新建或修改文本文件时使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "相对工作区根的目标文件路径"},
                    "content": {"type": "string", "description": "要写入的完整文本内容"},
                },
                "required": ["path", "content"],
            },
        },
    },
]


def _schema(name, description, properties, required):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required,
                       "additionalProperties": False}}}

_PATH = {"type": "string", "description": "相对工作区路径"}
_INT = {"type": "integer"}
TOOLS.extend([
    _schema("list_files", "发现工作区内文件，跳过隐藏目录、依赖及构建目录", {"path": _PATH, "limit": _INT}, []),
    _schema("search_text", "搜索字面量，返回文件路径、行号和命中片段", {"query": {"type": "string"}, "path": _PATH, "limit": _INT}, ["query"]),
    _schema("read_file_range", "读取指定行范围，返回完整文件 sha256 供 Patch 校验", {"path": _PATH, "start_line": _INT, "end_line": _INT}, ["path"]),
    _schema("apply_patch", "单文件精确替换，要求最新 sha256、old_text 唯一；权限确认展示 diff", {"path": _PATH, "old_text": {"type": "string"}, "new_text": {"type": "string"}, "expected_sha256": {"type": "string"}}, ["path", "old_text", "new_text", "expected_sha256"]),
])
