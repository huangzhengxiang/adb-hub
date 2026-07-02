# AGENTS.md — ADB Hub 项目指引

## 项目概述

ADB Hub 是一个 Flask Web 服务，将本机 ADB 能力通过 HTTP REST API 和 WebSocket 暴露给局域网内的其他程序。前端仅一个简单的设备状态仪表盘。

## 技术栈

- **Python 3.10** (`D:\conda\envs\llm`)，跨平台 (Win/Linux/macOS)
- **Flask 3.1** — HTTP 框架
- **flask-sock 0.7** — WebSocket（基于 simple-websocket + wsproto）
- **前端**: 原生 HTML + Tailwind CSS CDN + 极少量 fetch JS
- **ADB**: `subprocess` 调用外部 `adb` 命令

## 架构

```
局域网调用方 (curl / 脚本 / CI)
        │
        ▼
┌─────────────────────────┐
│  Flask app.py (0.0.0.0) │
│  ├─ /api/v1/*   REST API │
│  ├─ /ws/v1/*    WebSocket│
│  └─ /           仪表盘    │
└──────────┬──────────────┘
           │ subprocess
           ▼
       adb (PATH)
```

## 核心模块

### `adb_utils/client.py` — ADB 命令封装

`ADBClient` 类是唯一与 adb 交互的入口：

- `_run(args)` → 同步执行，返回 `ADBResult(stdout, stderr, exit_code, success)`
- `_spawn(args)` → 返回 `subprocess.Popen`，用于长时流
- 所有设备操作通过 `_run(["-s", serial, ...])` 完成
- 全局命令通过 `exec_global(args)` 完成
- 单例: `adb = ADBClient()`，各模块直接 `from adb_utils.client import adb` 使用

**添加新 ADB 功能的模式**：在 `ADBClient` 里加方法 → 在 `routes/api.py` 加端点。

### `adb_utils/parser.py` — 输出解析

纯字符串处理，将 adb 文本输出转成结构化的 list/dict：

- `parse_devices(text)` — 解析 `adb devices -l`
- `parse_packages(text)` — 解析 `pm list packages`
- `get_device_detail(serial)` — 组合多个 getprop 调用
- `get_devices_with_details()` — 设备列表 + 详情，一次调用返回完整数据

### `routes/api.py` — REST API（核心）

- Blueprint: `api_bp`，前缀 `/api/v1`
- 统一返回: `api_response(success, data, error, status)`
- `require_json` 装饰器确保请求带 JSON Content-Type
- 文件传输用 `tempfile.NamedTemporaryFile` 作为中转
- 截图用 `BytesIO` 直接返回，不落盘

### `routes/ws.py` — WebSocket

- 不是 Blueprint，通过 `register_ws_routes(sock)` 注册到 Flask-Sock 实例
- Shell WS: `ws.receive()` → `proc.stdin.write()` → 后台线程 `proc.stdout.readline()` → `ws.send()`
- Logcat WS: 连接即启动 `adb logcat`，逐行推送

### `config.py` — 配置

全部配置项通过 `os.environ.get()` 读取，有合理默认值。不要硬编码新配置，遵循现有模式。

### `templates/index.html` — 仪表盘

唯一页面。功能：
- 每 10 秒轮询 `/api/v1/devices` 刷新设备列表
- 右侧顶部显示 ADB 健康状态
- 统计卡：总数 / 在线 / 离线
- 无其他交互功能（所有操作通过 API 完成）

## 编码约定

1. **类型注解**: 使用 Python 3.10+ 的类型注解语法 (`list[str]`, `dict`, `| None`)
2. **日志**: `logging.getLogger(__name__)`，不要 `print()`
3. **错误处理**: ADB 命令失败抛出 `ADBError`，API 层 catch 后返回 `api_response(False, error=...)`
4. **跨平台**: 只用 stdlib 跨平台 API (`os.path`, `tempfile`, `subprocess`)，不写平台判断
5. **无硬编码**: 所有可变值进 `config.py`，通过环境变量覆盖

## 依赖

```
flask>=3.0
flask-sock>=0.7
```

安装: `pip install -r requirements.txt`，或用已有的 `D:\conda\envs\llm` 环境。

## 运行与测试

```bash
# 启动 (conda)
conda activate llm
python app.py

# 快速验证
curl http://127.0.0.1:5000/api/v1/health
curl http://127.0.0.1:5000/api/v1/devices
curl -X POST http://127.0.0.1:5000/api/v1/devices/<serial>/shell \
  -H 'Content-Type: application/json' -d '{"cmd":"echo hello"}'
```

## 常见修改指引

| 需求 | 改哪里 |
|------|--------|
| 新增 ADB 命令 | `adb_utils/client.py` 加方法 |
| 新增 REST 端点 | `routes/api.py` 加路由 |
| 新增 WebSocket 流 | `routes/ws.py` 的 `register_ws_routes()` 内加 handler |
| 调整配置默认值 | `config.py` |
| 改仪表盘样式 | `templates/index.html` |
| 解析新 adb 输出 | `adb_utils/parser.py` |
