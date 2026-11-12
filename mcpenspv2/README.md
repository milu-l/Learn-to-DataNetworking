# eNSP Telnet Manager

基于 Python 全栈开发的 eNSP 网络模拟器管理工具，提供 Web 界面和 MCP 协议两种交互方式，支持端口扫描、Telnet 设备连接、命令执行及拓扑文件解析分析。

## 功能概览

### Web 界面

| 模块 | 功能 |
|------|------|
| 端口扫描 | 扫描指定 IP 的端口范围，发现 eNSP 设备（默认起始端口 2000） |
| 设备连接 | 通过 Telnet 连接设备，每个设备拥有独立的终端标签页 |
| 命令执行 | 向已连接设备发送命令（如 `display version`）并实时查看返回结果 |
| 拓扑分析 | 上传 eNSP 拓扑文件，自动解析生成报告和拓扑图 |

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

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```
## MCP 客户端配置

在 MCP 客户端（如 Claude Desktop）配置文件中添加：

```json
{
  "mcpServers": {
    "ensp-manager": {
      "command": "python",
      "args": ["mcp_server.py"],
      "cwd": "e:\\Trae CN\\code\\code\\ensp"
    }
  }
}
```

具体配置可参考项目根目录下的 `mcp.json` 文件。
### 2. 启动服务

```bash
python app.py
```

启动后同时运行两个服务：

```
============================================================
  eNSP Telnet Manager
  Web 界面: http://0.0.0.0:5000
  MCP SSE:  http://0.0.0.0:5001/sse
============================================================
```

浏览器打开 `http://localhost:5000` 即可使用 Web 界面。

### 3. 单独启动 MCP（可选）

```bash
# stdio 模式（供 Claude Desktop 等本地 MCP 客户端使用）
python mcp_server.py

# SSE 模式（供远程 MCP 客户端使用）
python mcp_server.py sse
```

## 项目结构

```
ensp/
├── app.py                 # 入口：Flask Web + MCP SSE 一体启动
├── mcp_server.py          # MCP 独立启动入口（stdio / sse）
├── mcp_tools.py           # MCP 工具注册
├── mcp.json               # MCP 客户端配置文件
├── requirements.txt       # Python 依赖
│
├── scan_service.py        # 端口扫描服务
├── telnet_service.py      # Telnet 连接管理
├── session_manager.py     # 会话生命周期管理
├── topo_service.py        # 拓扑解析、分析、缓存
│
├── static/
│   ├── app.js             # 前端交互逻辑
│   └── style.css          # 前端样式
│
├── templates/
│   └── index.html         # Web 页面模板
│
└── uploads/               # 上传目录（如有）
```

## 使用流程

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

### 拓扑流量监控

安装 Wireshark 和 Npcap，启动 eNSP 设备并上传拓扑，打开「拓扑图」，选择 eNSP 使用的 VirtualBox Host-Only 网卡，点击「开始监控」。页面显示每条物理连线的每秒报文数和速率，再次点击可停止；上传新拓扑也会停止采集。

程序按拓扑设备 ID 从本机 VirtualBox 注册配置识别 eNSP AR 克隆虚拟机的网卡 MAC，不支持未识别的设备类型。采集依据当前环境观察到的 UDP 10000–10099 封装，过滤 4 字节载荷的周期报文。数值是封装报文的估算速率，并非设备接口精确统计；其他 eNSP 版本或设备的封装方式可能不同。

拓扑图下方的数据包分析面板显示最近 200 条已匹配连线的报文，可按协议筛选或清空。解析基于观察到的 UDP 载荷中的以太网帧，显示地址、协议、端口或协议类型；鼠标悬停可查看外层 UDP、VLAN 和 TTL。记录只保存在内存中，启动新监控或更换拓扑会清空；未识别或截断的数据以未知信息显示。



## 跨进程数据共享

拓扑与报告只保存在 Web 服务的内存中，重启后清空。独立的 MCP 进程通过本机 5000 端口读取当前 Web 服务中的报告，因此需要先启动 Web 服务并上传拓扑。上传新拓扑时报告缓存自动清空。

## 技术要点

- **默认扫描端口**：2000-2099（eNSP 设备 Telnet 端口默认范围）
- **无连接数限制**：可同时连接任意数量的设备
- **多 Tab 终端**：每个设备独立的输出区域和输入框
- **拓扑报告**：仅在 Web 服务运行期间保存，MCP 通过本机 HTTP 接口读取
- **Python 全栈**：Flask 后端 + 原生 HTML/CSS/JS 前端，零前端构建工具
