# Learn to DataNetworking 2.6

Learn to DataNetworking 是面向 eNSP、EVE-NG 和 PNETLab 网络模拟环境的学习工具，提供网页界面和 MCP 接口，帮助用户连接设备、分析拓扑并辅助网络实验。

## 项目来源

本项目基于 [llz-2002/ensptomcpfortrae](https://github.com/llz-2002/ensptomcpfortrae/tree/main) 的原有内容修改、扩展而来。感谢原项目作者的工作。

当前版本为 **2.6**，项目代码位于 [应用代码](LearnToeNSP2.5/) 目录。为兼容已有启动配置，应用目录名保留 `LearnToeNSP2.5`。近期改动见 [更新记录](CHANGELOG.md)。

## 2.6 功能

- **设备扫描与管理**：扫描目标 IP 的端口范围，通过 Telnet 连接 eNSP 设备，在多个终端标签中发送命令。
- **命令补全**：终端支持常用命令和拓扑接口名的本地补全、缩写匹配、Tab 选择与本次会话历史。
- **拓扑分析**：上传 eNSP `.topo` 或 EVE-NG / PNETLab `.unl` 文件，显示设备、网络、连线和接口来源，支持保存与导入、导出互联信息。
- **EVE-NG / PNETLab 接入**：通过服务器 API 获取真实 Telnet 地址，支持一键连接、终端与 AI 操作；华为 VRP 模板支持 LLDP 验证。SSH 远程抓包可为最多 32 条用户映射链路显示流量和报文分析，方向未知时不播放方向动画。配置步骤见 [使用说明](LearnToeNSP2.5/README.md)。
- **流量监控**：在拓扑图上显示已匹配连线的估算报文速率，并支持查看、筛选和清空最近捕获的数据包。
- **数据包分析与溯源**：解析常见协议字段，提示部分截断或长度异常；可根据已捕获报文推测路径并在拓扑图上慢放。
- **AI 对话**：在页面右侧使用自然语言调用 MCP 工具，支持根据已上传拓扑检测本机或远程设备，并可配置兼容 OpenAI Chat Completions 的 AI 服务。
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

- eNSP 流量监控需要 Wireshark、Npcap 和可访问的 VirtualBox Host-Only 网卡，速率为封装报文估算值。EVE/PNET 远程监控需配置 SSH、tcpdump 和实际网卡映射；方向未知时不播放流向动画。
- AI 对话的「自动执行」默认开启，设备配置命令等 MCP 操作会直接运行；可在对话栏取消勾选，改为逐项确认。对话和工具结果会发送给所配置的 AI 服务。
- 拓扑和分析报告保存在运行中的 Web 服务内存中，重启后需要重新上传。AI 网页设置同样只在当前服务进程中保留。
