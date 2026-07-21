# ADB Hub Agent Usage

本文档面向 eval/engine agent。agent 不应直接调用本机 `adb`，也不应假设手机与当前机器直连；端侧测试统一通过 `adb-hub` session API 完成。agent 可以选择计划式 runner，也可以用 single-session low-level tool 在一个托管 session 内逐步调用 adb-hub API。

## 职责边界

- agent 负责在 engine 目录内生成可执行文件、模型产物、运行脚本和 `adb_hub_plan.json`。
- `adb-hub` 负责远端设备租约、scp 拉取、adb push/shell/pull、结果下载和 session 清理。
- runner `client/agent_session_runner.py` 负责按 plan 执行完整 session 生命周期，并生成 JSON 报告；它推荐读取 `sessions[0].actions[]` 结构，并会在 client 层归一化为内部 flat plan 后执行。
- low-level tool `client/agent_adb_hub_tool.py` 允许 agent 逐步调用 `start/fetch/open/push/shell/pull/download/finish`；一个 ledger 只允许一个 session，创建和关闭由 tool 托管。
- agent 不能在 plan 中写入密码、token 或 `.env` 内容；client 会自动读取 `adb-hub/.env` 或环境变量。

## 使用模式

### 模式 A：计划式 runner

适合已经明确文件和命令的稳定测试。agent 写完整 `adb_hub_plan.json`，一次性执行：

```bash
python eval/hub/adb-hub/client/agent_session_runner.py \
  --plan path/to/adb_hub_plan.json \
  --output path/to/adb_hub_report.json
```

完整的 SCP 模型拉取、推送到手机、设备端启动脚本、输出 pull/download 示例见
[`examples/scp_model_phone_run/`](examples/scp_model_phone_run/README.md)。其中包含
可直接运行的 `adb_hub_plan.json` 和离线流程校验脚本。

### 模式 B：开放式 low-level tool

适合调试、逐步验证和需要多轮探索的端侧测试。一个 ledger 只允许一个 session：agent 开始时 `start`，后续所有命令隐式使用这个 session，结束时 `finish`。如果运行被中断，最后用 `cleanup` 兜底关闭。

```bash
LEDGER=path/to/adb_hub_session_ledger.json
python eval/hub/adb-hub/client/agent_adb_hub_tool.py --ledger "$LEDGER" devices
python eval/hub/adb-hub/client/agent_adb_hub_tool.py --ledger "$LEDGER" start --name <run-name>
python eval/hub/adb-hub/client/agent_adb_hub_tool.py --ledger "$LEDGER" fetch /local/file.bin file.bin
python eval/hub/adb-hub/client/agent_adb_hub_tool.py --ledger "$LEDGER" open-session
python eval/hub/adb-hub/client/agent_adb_hub_tool.py --ledger "$LEDGER" push file.bin file.bin
python eval/hub/adb-hub/client/agent_adb_hub_tool.py --ledger "$LEDGER" shell -- ls -l
python eval/hub/adb-hub/client/agent_adb_hub_tool.py --ledger "$LEDGER" finish
```

规则：

- `start` 创建唯一 session；如果不传 `--serial`，会自动选择第一个 `state == "device"` 的在线设备；如果 ledger 中已有未关闭 session，会失败。
- `fetch/open-session/push/shell/pull/download` 不接受 session id，始终使用 ledger 中的唯一 active session。
- `finish` 正常关闭该 session。
- `cleanup` 是兜底清理，用于中断或失败后确保该 session 被关闭。
- 正式评测必须保留 ledger 和每条命令的 JSON 输出作为证据。

## 推荐流程

1. 在 engine 自己的 workdir 内准备端侧运行所需文件：native executable、shared libraries、model files、prompt/input files、运行脚本。
2. 写一个 `adb_hub_plan.json`，推荐使用 `sessions[0].actions[]`：每个 action 带 `type`，可用 `fetch/open/push/shell/pull/download/close`。旧的顶层 `fetch/push/shell/pull/download` flat plan 仍兼容。
   - `fetch`: adb-server 需要从 remote client scp 拉取哪些文件；`src` 可以是绝对路径，也可以是相对 plan 文件的路径。
   - `push`: host session workdir 中哪些文件需要推到手机 session workdir。
   - `shell`: 手机上要执行的命令。命令默认在手机 session workdir 下运行。
   - `pull`: 手机端输出文件拉回 adb-server host session workdir。
   - `download`: remote client 从 adb-server 下载哪些输出文件。
3. 如果流程稳定，调用 `agent_session_runner.py`；如果需要逐步调试，调用 `agent_adb_hub_tool.py start` 创建单 session，并在同一个 ledger 下执行后续操作。
4. 把 `adb_hub_report.json` 或 low-level tool 的 JSON 输出、ledger、端侧 stdout/stderr、logcat 或生成的 tensor artifact 一起交给 verifier/auditor。

## Plan 字段

推荐 schema：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | session 名称，建议包含 engine/backend/model/test 名称 |
| `serial` | string | 可选；为空时 runner 选择第一个 online device |
| `fetch_timeout` | number | 默认 fetch 超时秒数 |
| `shell_timeout` | number | 默认 shell 超时秒数 |
| `sessions` | list | 只允许一个 session；session 内放 `actions` |
| `sessions[].actions[].type` | string | 支持 `fetch/open/push/shell/pull/download/close`，未知类型会失败 |
| `close` | bool | 是否自动关闭并清理 session，默认 true；`close` action 也会归一化为该字段 |

兼容 schema：旧的顶层 `fetch/open/push/shell/pull/download/close` flat plan 仍可执行。action 字段支持 `src/source`、`dest/destination`、`cmd/command`、`timeout/timeout_seconds` 这些别名。

## Shell 输出规则

`adb-hub` 会把 shell 的 stdout/stderr 放进 JSON report。对于长输出或 verifier artifact，agent 应在 shell 命令里显式重定向到文件，然后用 `pull` + `download` 取回，例如：

```json
{
  "type": "shell",
  "command": "export LD_LIBRARY_PATH=. && ./runner model/config.json input.json > run_stdout.txt 2> run_stderr.txt",
  "timeout": 1200
}
```

然后：

```json
"pull": [
  {"src": "run_stdout.txt", "dest": "outputs/run_stdout.txt"},
  {"src": "run_stderr.txt", "dest": "outputs/run_stderr.txt"},
  {"src": "engine_outputs/case_000.npz", "dest": "outputs/case_000.npz"}
],
"download": [
  {"src": "outputs/run_stdout.txt", "dest": "./run_stdout.txt"},
  {"src": "outputs/run_stderr.txt", "dest": "./run_stderr.txt"},
  {"src": "outputs/case_000.npz", "dest": "./case_000.npz"}
]
```

## 最小 MNN LLM 示例

```json
{
  "name": "mnn-qwen3-smoke",
  "fetch_timeout": 900,
  "shell_timeout": 900,
  "fetch": [
    {"src": "engines/MNN/project/android/build_64/llm_demo", "dest": "llm_demo"},
    {"src": "engines/MNN/project/android/build_64/libMNN.so", "dest": "libMNN.so"},
    {"src": "path/to/model/config.json", "dest": "model/config.json"},
    {"src": "path/to/model/llm.mnn", "dest": "model/llm.mnn"},
    {"src": "path/to/model/llm.mnn.weight", "dest": "model/llm.mnn.weight"},
    {"src": "path/to/model/tokenizer.mtok", "dest": "model/tokenizer.mtok"},
    {"src": "path/to/model/prompt.txt", "dest": "model/prompt.txt"}
  ],
  "push": [
    {"src": "llm_demo"},
    {"src": "libMNN.so"},
    {"src": "model/config.json"},
    {"src": "model/llm.mnn"},
    {"src": "model/llm.mnn.weight"},
    {"src": "model/tokenizer.mtok"},
    {"src": "model/prompt.txt"}
  ],
  "shell": [
    {"cmd": "chmod +x llm_demo && export LD_LIBRARY_PATH=. && ./llm_demo model/config.json model/prompt.txt > run_stdout.txt 2> run_stderr.txt", "timeout": 900}
  ],
  "pull": [
    {"src": "run_stdout.txt", "dest": "outputs/run_stdout.txt"},
    {"src": "run_stderr.txt", "dest": "outputs/run_stderr.txt"}
  ],
  "download": [
    {"src": "outputs/run_stdout.txt", "dest": "./run_stdout.txt"},
    {"src": "outputs/run_stderr.txt", "dest": "./run_stderr.txt"}
  ]
}
```

## 失败处理

- `agent_session_runner.py` 任一步失败都会把失败 step、异常、HTTP error data、stdout/stderr/exit_code 写入 report。
- `agent_adb_hub_tool.py` 每次调用都会输出 JSON；agent 应将这些 JSON 追加保存到自己的运行日志。
- 计划式 runner 默认一定尝试 `close-session`，避免占用设备锁和残留手机端目录。
- 开放式调用必须在开始时运行 `agent_adb_hub_tool.py --ledger <ledger> start`，结束时运行 `finish`；失败或中断时运行 `cleanup`。
- 如果需要人工调试，可以临时使用 `--keep-session`，但正式评测不应使用。
- 如果设备 offline、scp 失败、输出文件缺失，agent 应把 report 作为失败证据提交，不要安装包、切换 git 分支或绕过端侧运行。
