# Learn to eNSP

Learn to eNSP 是一款面向 eNSP 网络模拟环境的管理工具，通过网页和 MCP 接口提供设备连接、命令执行、拓扑分析、流量监控及 AI 辅助操作。

## 功能概览

### Web 界面

| 模块 | 功能 |
|------|------|
| 端口扫描 | 扫描指定 IP 的端口范围，发现 eNSP 设备（默认起始端口 2000） |
| 设备连接 | 通过 Telnet 连接设备，每个设备拥有独立的终端标签页 |
| 命令执行 | 向已连接设备发送命令（如 `display version`）并实时查看返回结果 |
| 拓扑分析 | 上传 eNSP 拓扑文件，自动解析生成报告和拓扑图 |
| 流量和报文 | 在拓扑图上查看估算流量、数据包分析和慢放溯源 |
| AI 对话 | 使用自然语言调用 MCP，辅助查询和操作设备 |
| 界面设置 | 调整 AI 对话栏宽度，切换深色或浅色主题 |

### MCP 工具

| 工具名 | 功能 |
|--------|------|
| `scan_ports` | 扫描目标 IP 的端口范围 |
| `connect_device` | Telnet 连接设备 |
| `send_command` | 向设备发送命令 |
| `disconnect_device` | 断开设备连接 |
| `list_sessions` | 列出当前所有活跃会话 |
| `get_device_info` | 获取设备基本信息 |
| `get_topo_report` | 获取已上传拓扑的分析报告 |
| `detect_topology_devices` | 检测已上传拓扑中的本机设备端口和会话 |

## 快速开始

### 1. 安装依赖

```powershell
python -m pip install -r requirements.txt
```

在本目录执行命令。使用设备功能前，请先启动 eNSP 中的目标设备；流量监控还需要 Wireshark（包含 `tshark`）、Npcap 和可访问的 VirtualBox Host-Only 网卡。

### 2. 启动服务

```bash
python app.py
```

启动后同时运行两个服务：

```
============================================================
  Learn to eNSP
  Web 界面: http://0.0.0.0:5000
  MCP SSE:  http://0.0.0.0:5001/sse
============================================================
```

浏览器打开 `http://localhost:5000` 即可使用 Web 界面。

## 项目结构

```
mcpenspv2/
├── app.py                 # 入口：Flask Web + MCP SSE 一体启动
├── mcp_server.py          # MCP 独立启动入口（stdio / sse）
├── mcp_tools.py           # MCP 工具注册
├── chat_service.py        # AI 对话与 MCP 工具调用
├── mcp.json               # MCP 客户端配置文件
├── requirements.txt       # Python 依赖
│
├── scan_service.py        # 端口扫描服务
├── telnet_service.py      # Telnet 连接管理
├── session_manager.py     # 会话生命周期管理
├── topo_service.py        # 拓扑解析、分析、缓存
├── traffic_service.py     # 流量采集与数据包分析
│
├── static/
│   ├── app.js             # 前端交互逻辑
│   └── style.css          # 前端样式
│
└── templates/
    └── index.html         # Web 页面模板
```

## 使用流程

### AI 对话与 MCP

AI 对话支持兼容 OpenAI Chat Completions 且支持工具调用的模型。可以在网页右侧「AI 设置」中填写 API 基础地址、模型名称和密钥。

网页设置只保存在当前 Web 服务进程的内存中，重启后恢复为环境变量的值；密钥不会在页面中回显。地址与模型为必填，密钥留空保留当前值，也可勾选清除；更改设置会取消待确认的工具调用。远程服务必须使用 HTTPS，本机服务可使用 HTTP。

也可以在启动服务前，通过 PowerShell 设置环境变量：

```powershell
$env:ENSP_AI_BASE_URL = 'https://你的服务地址/v1'
$env:ENSP_AI_MODEL = '你的模型名称'
$env:ENSP_AI_API_KEY = '你的密钥'
python app.py
```

密钥可按服务商方式安全设置为环境变量，不要写入仓库；无需密钥的本地服务可省略。地址应是 API 的基础路径，程序会追加 `/chat/completions`。打开「AI 对话」后输入需求，模型通过本机 MCP SSE 工具查询或操作设备，并可根据已上传拓扑的端口自动检测设备。**「自动执行」默认开启**：操作类工具（包括设备配置命令）会直接运行；取消勾选后须逐次确认，取消或 5 分钟未确认即失效。对话仅保存在当前网页内存中，刷新后清空。

对话内容、已调用的工具结果和设备命令输出会发送给所配置的 AI 服务。AI 对话接口仅允许本机访问，Web 与 MCP SSE 服务都须正常运行。不同服务商对工具调用的兼容程度可能不同。

### 扫描与连接设备

1. 在左侧"端口扫描"面板输入目标 IP 和端口范围
2. 点击"开始扫描"发现开放的设备端口
3. 在"设备列表"中点击"连接"建立 Telnet 会话
4. 在底部终端输入命令（如 `display version`），按回车发送
5. 每个已连接设备有独立标签页，可随时切换

### 拓扑分析

1. 在 eNSP 中导出拓扑文件（`.topo` / `.txt`）
2. 在左侧"拓扑分析"面板点击"上传拓扑文件"
3. 点击"完整报告"查看 Markdown 格式的设备清单、连接表、VLAN/子网规划
4. 点击"文本摘要"查看纯文本汇总
5. 点击"Mermaid图"查看可渲染的拓扑图代码
6. 分析结果在服务运行期间缓存，MCP 工具 `get_topo_report` 可读取；重启 Web 服务后需重新上传拓扑

报告中的接口名称和网络层级包含推断结果，执行设备配置前应以设备实际信息为准。可使用「删除」清除当前拓扑及报告。

### 拓扑流量监控

安装 Wireshark 和 Npcap，启动 eNSP 设备并上传拓扑，打开「拓扑图」，选择 eNSP 使用的 VirtualBox Host-Only 网卡，点击「开始监控」。页面显示每条物理连线的每秒报文数和速率，再次点击可停止；上传新拓扑也会停止采集。

程序按拓扑设备 ID 从本机 VirtualBox 注册配置识别 eNSP AR 克隆虚拟机的网卡 MAC，不支持未识别的设备类型。采集依据当前环境观察到的 UDP 10000–10099 封装，过滤 4 字节载荷的周期报文。数值是封装报文的估算速率，并非设备接口精确统计；其他 eNSP 版本或设备的封装方式可能不同。

拓扑图下方的数据包分析面板显示最近 200 条已匹配连线的报文，可按协议筛选或清空。解析基于观察到的 UDP 载荷中的以太网帧，显示地址、协议、端口或协议类型；鼠标悬停可查看外层 UDP、VLAN 和 TTL。记录只保存在内存中，启动新监控或更换拓扑会清空；未识别或截断的数据以未知信息显示。

选择报文可查看自动分析：包含内层帧、IP 和常见协议首部信息，并提示可识别的截断或长度错误。TCP RST、ICMP 差错等只描述单个报文的含义，不能据此单独判断故障；IPv6 扩展首部和应用负载不作详细解析。



## 跨进程数据共享

拓扑与报告只保存在 Web 服务的内存中，重启后清空。独立的 MCP 进程通过本机 5000 端口读取当前 Web 服务中的报告，因此需要先启动 Web 服务并上传拓扑。上传新拓扑时报告缓存自动清空。独立 stdio 进程与 Web 服务的 Telnet 会话不共享。

## 技术要点

- **默认扫描端口**：2000-2099（eNSP 设备 Telnet 端口默认范围）
- **多设备会话**：可同时管理多个设备，实际数量受本机资源限制
- **多 Tab 终端**：每个设备独立的输出区域和输入框
- **拓扑报告**：仅在 Web 服务运行期间保存，MCP 通过本机 HTTP 接口读取
- **Python 全栈**：Flask 后端 + 原生 HTML/CSS/JS 前端，零前端构建工具

## MCP 客户端配置

使用 `python app.py` 时，MCP SSE 与 Web 服务一同启动。支持 SSE 的客户端可连接 `http://127.0.0.1:5001/sse`。

也可在本机使用 stdio 模式。以下配置仅作示例，请将 `cwd` 修改为你的 `mcpenspv2` 目录，并确保客户端能找到安装了项目依赖的 Python：

```json
{
  "mcpServers": {
    "ensp-manager": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "E:\\Documents\\GitHub\\Learn-to-ensp-mcp\\mcpenspv2"
    }
  }
}
```

项目中的 `mcp.json` 可作为格式参考，但其路径可能来自其他环境，使用前请检查并修改。也可单独启动 MCP：

```powershell
python mcp_server.py       # stdio
python mcp_server.py sse   # SSE，监听 5001 端口
```

独立 stdio 进程与 Web 服务的设备会话不共享；需要时请在同一 MCP 进程中连接设备。拓扑报告和拓扑设备检测通过本机 Web 服务读取，使用前需启动 `app.py` 并上传拓扑。`app.py` 已启动 MCP SSE 时，不要再启动独立 SSE 服务占用同一端口。
