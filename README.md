# Learn to eNSP 2.5

Learn to eNSP 是面向 eNSP 网络模拟环境的学习工具，提供网页界面和 MCP 接口，帮助用户连接设备、分析拓扑并辅助网络实验。

## 项目来源

本项目基于 [llz-2002/ensptomcpfortrae](https://github.com/llz-2002/ensptomcpfortrae/tree/main) 的原有内容修改、扩展而来。感谢原项目作者的工作。

当前版本定义为 **2.5**，项目代码位于 [LearnToeNSP2.5](LearnToeNSP2.5/) 目录。近期改动见 [更新记录](CHANGELOG.md)。

## 2.5 功能

- **设备扫描与管理**：扫描目标 IP 的端口范围，通过 Telnet 连接 eNSP 设备，在多个终端标签中发送命令；可查看和清空扫描历史。
- **拓扑分析**：上传 eNSP 拓扑文件，显示设备和连线，生成设备清单、文本摘要及 Mermaid 图代码。设备列表可根据拓扑显示名称与端口。
- **流量监控**：在拓扑图上显示已匹配连线的估算报文速率，并支持查看、筛选和清空最近捕获的数据包。
- **数据包分析与溯源**：解析常见协议字段，提示部分截断或长度异常；可根据已捕获报文推测路径并在拓扑图上慢放。
- **AI 对话**：在页面右侧使用自然语言调用 MCP 工具，支持根据已上传拓扑检测本机设备，并可配置兼容 OpenAI Chat Completions 的 AI 服务。
- **界面设置**：支持调整 AI 对话栏宽度，以及切换深色和浅色主题。

## 快速开始

在 Windows PowerShell 中进入项目目录，安装依赖并启动服务：

```powershell
cd LearnToeNSP2.5
python -m pip install -r requirements.txt
python app.py
```

浏览器访问 <http://127.0.0.1:5000>，本机 MCP SSE 地址为 <http://127.0.0.1:5001/sse>。完整的依赖、使用步骤和 MCP 客户端配置见 [使用文档](LearnToeNSP2.5/README.md)。

## 使用提示

- 流量监控需要 Wireshark、Npcap 和可访问的 eNSP VirtualBox Host-Only 网卡。图中速率是基于观察到的封装报文计算的估算值，溯源路径也可能受捕获缺失或重传影响。
- AI 对话的「自动执行」默认开启，设备配置命令等 MCP 操作会直接运行；可在对话栏取消勾选，改为逐项确认。对话和工具结果会发送给所配置的 AI 服务。
- 拓扑和分析报告保存在运行中的 Web 服务内存中，重启后需要重新上传。AI 网页设置同样只在当前服务进程中保留。
