# ADB Hub

HTTP API 包装的 ADB 中间层服务。局域网内的程序无需安装 Android SDK，通过 REST/WebSocket 即可透明调用本机 ADB。

## 快速启动

```bash
# 1. 环境
conda activate llm           # 或任意 Python 3.10+ 环境
pip install -r requirements.txt

# 2. 启动 (确保 adb 在 PATH 中)
python app.py
# → ADB Hub starting on http://0.0.0.0:5000
```

## 配置

全部通过环境变量控制：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ADB_HUB_HOST` | `0.0.0.0` | 监听地址 |
| `ADB_HUB_PORT` | `5000` | 监听端口 |
| `ADB_PATH` | `adb` | adb 可执行文件路径 |
| `ADB_HUB_DEBUG` | `false` | Flask debug 模式 |
| `ADB_HUB_AUTH_SECRET` | 空 | `.env` 中的共享密钥，启用认证时必填 |
| `ADB_HUB_AUTH_REQUIRED` | `true` | 是否要求加密 token |
| `ADB_HUB_REQUIRE_ENCRYPTED_PAYLOAD` | `true` | 是否要求 JSON 请求体使用加密 envelope |
| `ADB_HUB_SESSION_ROOT` | `session_workdirs` | A 机器上的 session 工作目录根路径，默认位于项目内被忽略目录 |
| `ADB_HUB_DEVICE_SESSION_ROOT` | `/data/local/tmp/adb-hub` | 手机端 session 工作目录根路径 |
| `ADB_HUB_SCP_HOST` | 空 | 返回给客户端的 scp 目标 host |
| `ADB_HUB_SCP_USER` | 空 | 返回给客户端的 scp 目标 user |

```bash
ADB_HUB_PORT=8080 python app.py   # 换端口
```

## 认证与加密封包

`adb-hub` 默认要求认证。服务从 `adb-hub/.env` 读取共享密钥，`.env` 不应提交：

```bash
ADB_HUB_AUTH_SECRET=<replace-with-random-local-secret>
ADB_HUB_AUTH_REQUIRED=true
ADB_HUB_REQUIRE_ENCRYPTED_PAYLOAD=true
```

认证 token 的明文是 `security.py` 里硬编码的长随机串。客户端和服务端都用 `.env` 里的 `ADB_HUB_AUTH_SECRET` 对该明文 token 做加密，实际 HTTP 交互只传加密后的 token：

```bash
X-ADB-Hub-Token: v1.<nonce>.<ciphertext>.<tag>
```

JSON 请求体也使用同一个密钥加密，格式如下：

```json
{
  "v": "adb-hub-enc-v1",
  "nonce": "...",
  "ciphertext": "...",
  "tag": "..."
}
```

可以在客户端复用 `security.py` 生成 token 和 payload：

```python
import sys
sys.path.insert(0, "path/to/adb-hub")
from security import encrypt_json_payload, encrypt_token

headers = {"X-ADB-Hub-Token": encrypt_token()}
body = encrypt_json_payload({"serial": "<device-serial>", "name": "mnn-run"})
```

当前实现不引入第三方包，使用标准库 HMAC-SHA256 构造带完整性校验的对称加密 envelope。服务暴露到不可信网络时，仍建议在外层加 TLS 和访问控制。

启用 `ADB_HUB_REQUIRE_ENCRYPTED_PAYLOAD=true` 时，raw multipart 文件上传会被拒绝；模型、二进制和大文件应通过 scp 放入 session workdir，再使用 session API 推送到手机。

## Session + SCP 工作流

机器 A 运行 `adb-hub` 并连接手机；本机通过 scp 把模型和二进制上传到 A 机器上的 session 工作目录。`ADB_HUB_SESSION_ROOT=session_workdirs` 会解析为 `adb-hub/session_workdirs`，不是调用方当前目录下的 `session_workdirs`。

### Session 生命周期

1. `create-session`
   创建 `adb-hub/session_workdirs/<session_id>/`，写入 `session.json`，返回 `host_workdir`、`device_workdir` 和可选 `scp_target`。服务重启时会扫描仍存在的 `session.json` 恢复未关闭 session。

2. scp 上传
   本机把模型、二进制和脚本上传到返回的 `host_workdir`。这些文件不走 HTTP multipart；目录内容被 `.gitignore` 忽略。

3. `open-session`
   在手机上创建 `/data/local/tmp/adb-hub/<session_id>/`，并锁定该 device serial。同一台设备同时只能被一个 open session 使用。

4. `push`
   只允许从 host session workdir 内的相对路径推送到手机端 session workdir 内的相对路径，防止路径逃逸。

5. `shell`
   在手机端 session workdir 下执行命令。

6. `close-session`
   删除 A 机器上的 `session_workdirs/<session_id>/`，删除手机端 `/data/local/tmp/adb-hub/<session_id>/`，释放设备锁，并从当前进程的 active session 列表移除。

### Session API

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/api/v1/sessions` | 创建 session，返回 A 机器上的 `host_workdir` 和可选 `scp_target` |
| `GET` | `/api/v1/sessions` | 列出当前 active/recovered sessions |
| `GET` | `/api/v1/sessions/<session_id>` | 查看单个 active/recovered session |
| `POST` | `/api/v1/sessions/<session_id>/open` | 打开 session，并在手机端创建工作目录 |
| `POST` | `/api/v1/sessions/<session_id>/push` | 将 A 机器 session 目录内文件推送到手机端 session 目录 |
| `POST` | `/api/v1/sessions/<session_id>/shell` | 在手机端 session 目录下执行 shell 命令 |
| `DELETE` | `/api/v1/sessions/<session_id>` | 关闭 session，删除 A 机器和手机端工作目录 |

### Python Client

`client/adb_hub_client.py` 提供无第三方依赖的加密客户端。它从 `--secret`、`ADB_HUB_AUTH_SECRET`，或 `adb-hub/.env` 读取密钥。

```bash
# 查看设备
python client/adb_hub_client.py --base-url http://A:5000 devices

# 创建 session，返回 host_workdir/scp_target
python client/adb_hub_client.py --base-url http://A:5000 create-session   --serial <device-serial> --name mnn-run

# 本机通过 scp 把文件放入 A 机器 adb-hub/session_workdirs/<session_id>/
scp inference_runner model.bin user@A:/path/to/adb-hub/session_workdirs/<session_id>/

# 打开 session、推送到手机、执行、关闭
python client/adb_hub_client.py --base-url http://A:5000 open-session <session_id>
python client/adb_hub_client.py --base-url http://A:5000 push <session_id> inference_runner inference_runner
python client/adb_hub_client.py --base-url http://A:5000 shell <session_id> -- chmod +x inference_runner '&&' ./inference_runner
python client/adb_hub_client.py --base-url http://A:5000 close-session <session_id>
```

### Curl/自定义客户端

控制面请求必须带加密 token；启用 `ADB_HUB_REQUIRE_ENCRYPTED_PAYLOAD=true` 时，带 JSON 请求体的控制面请求还必须发送加密 envelope。

```bash
curl -X POST http://A:5000/api/v1/sessions   -H "Content-Type: application/json"   -H "X-ADB-Hub-Token: $TOKEN"   -d "$ENCRYPTED_CREATE_SESSION_BODY"
```

启用 `ADB_HUB_REQUIRE_ENCRYPTED_PAYLOAD=true` 时，raw multipart 文件上传会被拒绝；模型、二进制和大文件应通过 scp 放入 session workdir，再使用 `/sessions/<id>/push` 推送到手机。

## API 参考

所有接口返回统一格式：

```json
{"success": true, "data": {...}, "error": null}
```

### 设备

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/api/v1/devices` | 设备列表（含型号、Android 版本、品牌） |
| `GET` | `/api/v1/devices/<serial>` | 单设备详情 |

### Shell & 透传

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/api/v1/devices/<serial>/shell` | 执行 shell 命令 `{"cmd": "ls /sdcard"}` |
| `POST` | `/api/v1/devices/<serial>/exec` | 透传 adb -s 参数 `{"args": ["push", ...]}` |
| `POST` | `/api/v1/raw` | 透传全局 adb 参数 `{"args": ["connect", "ip:5555"]}` |

### 文件

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/api/v1/devices/<serial>/push` | legacy multipart 推送；强制加密 payload 时禁用，优先使用 session push |
| `POST` | `/api/v1/devices/<serial>/pull` | 拉取文件 `{"src": "/sdcard/file.txt"}` |

### 应用管理

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/api/v1/devices/<serial>/install` | 安装 APK；强制加密 payload 时禁用 raw multipart |
| `POST` | `/api/v1/devices/<serial>/uninstall` | 卸载 `{"package": "com.example"}` |
| `GET` | `/api/v1/devices/<serial>/packages` | 包列表 `?filter=-3` 过滤第三方应用 |

### 其他

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/api/v1/devices/<serial>/screenshot` | 截图（返回 PNG 流） |
| `POST` | `/api/v1/connect` | TCP/IP 连接 `{"address": "ip:5555"}` |
| `POST` | `/api/v1/disconnect` | 断开 TCP/IP 连接 |
| `POST` | `/api/v1/devices/<serial>/tcpip` | 设备端 adbd 切 TCP 模式 `{"port": 5555}` |
| `GET` | `/api/v1/health` | 服务健康检查 |

### WebSocket

| Path | 说明 |
|------|------|
| `ws://host:5000/ws/v1/shell/<serial>` | 交互式 shell：发 `{"cmd": "ls"}` 收 `{"stdout": "..."}` |
| `ws://host:5000/ws/v1/logcat/<serial>` | logcat 实时流：接收 `{"line": "..."}` |

## 调用示例

```bash
# 列出设备
curl http://10.0.0.5:5000/api/v1/devices

# 执行 shell
curl -X POST http://10.0.0.5:5000/api/v1/devices/R5CT1234/shell \
  -H 'Content-Type: application/json' \
  -d '{"cmd": "dumpsys battery | grep level"}'

# 安装 APK
curl -X POST http://10.0.0.5:5000/api/v1/devices/R5CT1234/install \
  -F "file=@app.apk" -F "opts=-r"

# 截图
curl http://10.0.0.5:5000/api/v1/devices/R5CT1234/screenshot -o screen.png
```

## 前端仪表盘

浏览器访问 `http://<服务器IP>:5000` 可查看设备连接状态和健康信息。仅此一页，所有操作通过 API 完成。

## 项目结构

```
adb-hub/
├── app.py              # Flask 入口
├── config.py           # 配置（环境变量）
├── requirements.txt    # flask, flask-sock
├── adb_utils/
│   ├── client.py       # ADBClient — 所有 adb 命令封装
│   └── parser.py       # adb 输出解析
├── routes/
│   ├── api.py          # REST API 端点
│   └── ws.py           # WebSocket 端点
├── templates/
│   └── index.html      # 仪表盘页面
└── static/
```

## 平台兼容性

Windows / Linux / macOS 通用。代码无平台硬编码，仅依赖 Python 标准库 + Flask + flask-sock。

## 安全提示

此服务监听 `0.0.0.0`，局域网内任意机器可调用。生产环境建议：
- 绑定 VPN 内网 IP 而非 `0.0.0.0`
- 前置 nginx 反向代理 + 认证
- 或直接改为 `127.0.0.1` 仅本机访问
