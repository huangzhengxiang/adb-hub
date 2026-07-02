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

```bash
ADB_HUB_PORT=8080 python app.py   # 换端口
```

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
| `POST` | `/api/v1/devices/<serial>/push` | 推送文件 (multipart: `file` + `dest`) |
| `POST` | `/api/v1/devices/<serial>/pull` | 拉取文件 `{"src": "/sdcard/file.txt"}` |

### 应用管理

| Method | Path | 说明 |
|--------|------|------|
| `POST` | `/api/v1/devices/<serial>/install` | 安装 APK (multipart 上传或服务器路径) |
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
