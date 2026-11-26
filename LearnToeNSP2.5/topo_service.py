import xml.etree.ElementTree as ET
import json
import re
import os


def parse_topology(file_path):
    for enc in ("gbk", "utf-8", "utf-16", "gb2312"):
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        raise RuntimeError("无法识别文件编码，请检查文件")

    return _parse_content(content)


def parse_topology_from_content(content):
    try:
        return _parse_content(content)
    except (ET.ParseError, KeyError, ValueError) as exc:
        raise RuntimeError(f"拓扑文件格式错误：{exc}") from exc


def _parse_content(content):
    content = content.replace('encoding="UNICODE"', 'encoding="UTF-8"')
    root = ET.fromstring(content)
    if root.tag == 'lab':
        return parse_unl(root)
    if root.tag != 'topo' or root.find('devices') is None:
        raise ValueError('不支持的拓扑格式，请上传 eNSP 或 EVE-NG 拓扑')

    result = {
        "version": root.attrib.get("version", ""),
        "device_inventory": {},
        "connections": [],
        "vlans": [],
        "subnets": [],
        "servers": [],
        "labels": [],
        "summary": {}
    }

    device_name_map = {}
    for dev in root.findall("devices/dev"):
        dev_id = dev.attrib["id"]
        dev_name = dev.attrib["name"]
        model = dev.attrib.get("model", "")
        mac = dev.attrib.get("system_mac", "")
        com_port = dev.attrib.get("com_port", "")
        settings = dev.attrib.get("settings", "")
        cx = dev.attrib.get("cx", "0")
        cy = dev.attrib.get("cy", "0")

        device_name_map[dev_id] = dev_name

        category = classify_device(model)

        interfaces = []
        global_offset = 0
        for slot in dev.findall("slot"):
            slot_num = slot.attrib.get("number", "")
            for iface in slot.findall("interface"):
                count = int(iface.attrib.get("count", "0"))
                interfaces.append({
                    "type": iface.attrib.get("sztype", ""),
                    "name": iface.attrib.get("interfacename", ""),
                    "count": count,
                    "slot": slot_num,
                    "_global_offset": global_offset
                })
                global_offset += count

        dev_info = {
            "id": dev_id,
            "name": dev_name,
            "model": model,
            "category": category,
            "mac": mac,
            "com_port": com_port,
            "interfaces": interfaces,
            "position": {"x": float(cx), "y": float(cy)}
        }

        if model == "Server" and settings:
            dev_info["server_config"] = parse_server_settings(settings)

        if model in ("PC", "Cellphone") and settings:
            dev_info["terminal_config"] = parse_terminal_settings(settings, model)

        if model == "AP4050" and settings:
            dev_info["ap_config"] = parse_ap_settings(settings)

        dev_info["port_name_map"] = build_port_name_map(interfaces)
        result["device_inventory"][dev_id] = dev_info

    for line in root.findall("lines/line"):
        src_id = line.attrib["srcDeviceID"]
        dst_id = line.attrib["destDeviceID"]

        pairs = []
        for pair in line.findall("interfacePair"):
            pairs.append({
                "line_type": pair.attrib.get("lineName", ""),
                "src_port_index": int(pair.attrib.get("srcIndex", "0")),
                "dst_port_index": int(pair.attrib.get("tarIndex", "0"))
            })

        result["connections"].append({
            "source": device_name_map.get(src_id, src_id),
            "source_id": src_id,
            "target": device_name_map.get(dst_id, dst_id),
            "target_id": dst_id,
            "aggregated": len(pairs) > 1,
            "link_count": len(pairs),
            "interface_pairs": pairs
        })

    resolve_interface_names(result["connections"], result["device_inventory"])

    for tip in root.findall("txttips/txttip"):
        content_text = tip.attrib.get("content", "").strip()
        left = float(tip.attrib.get("left", "0"))
        top = float(tip.attrib.get("top", "0"))

        if not content_text:
            continue

        if re.match(r'^vlan\s*\d+', content_text, re.IGNORECASE):
            result["vlans"].append({"name": content_text, "position": {"x": left, "y": top}})
        elif re.match(r'^\d+\.\d+\.\d+\.\d+/', content_text) or re.match(r'^\d+\.\d+\.\d+\.\d+$', content_text):
            result["subnets"].append({"subnet": content_text, "position": {"x": left, "y": top}})
        elif content_text not in ("DHCP-Server",) and not re.match(r'^\.\d+$', content_text):
            result["labels"].append({"text": content_text, "position": {"x": left, "y": top}})

    category_count = {}
    for dev in result["device_inventory"].values():
        cat = dev["category"]
        category_count[cat] = category_count.get(cat, 0) + 1

    model_count = {}
    for dev in result["device_inventory"].values():
        m = dev["model"]
        model_count[m] = model_count.get(m, 0) + 1

    result["summary"] = {
        "total_devices": len(result["device_inventory"]),
        "total_connections": len(result["connections"]),
        "aggregated_links": sum(1 for c in result["connections"] if c["aggregated"]),
        "by_category": category_count,
        "by_model": model_count,
        "vlan_count": len(result["vlans"]),
        "subnet_count": len(result["subnets"])
    }

    return result


def parse_unl(root):
    """Read EVE-NG network membership without inventing console addresses."""
    nodes, networks = root.find('./topology/nodes'), root.find('./topology/networks')
    if nodes is None or networks is None:
        raise ValueError('EVE-NG 拓扑缺少 nodes 或 networks')
    result = {'format': 'eve-ng', 'lab_name': root.get('name', ''), 'version': root.get('version', ''), 'device_inventory': {},
              'connections': [], 'vlans': [], 'subnets': [], 'servers': [], 'labels': [], 'summary': {}}
    attachments = {}
    for node in nodes.findall('node'):
        node_id = node.get('id')
        if not node_id or node_id in result['device_inventory']:
            raise ValueError('EVE-NG 节点 ID 缺失或重复')
        model = node.get('template') or node.get('type') or 'EVE-NG'
        name = node.get('name') or f'节点 {node_id}'
        normalized = model.lower()
        if any(tag in normalized for tag in ('switch', 'huaweice', 'viosl2', 'iol-l2', 'nxos', 'cat9', 'veos')):
            category = '交换机(三层)'
        elif any(tag in normalized for tag in ('huawei', 'vios', 'csr', 'ios', 'router', 'vmx', 'vjunos', 'mikrotik')):
            category = '路由器'
        elif normalized == 'vpcs':
            category = '终端PC'
        else:
            category = classify_device(model)
        dev = {'id': node_id, 'name': name, 'model': model, 'category': category,
               'mac': node.get('firstmac', ''), 'uuid': node.get('uuid', ''), 'com_port': '', 'interfaces': [], 'port_name_map': {},
               'position': {'x': float(node.get('left') or 0), 'y': float(node.get('top') or 0)}}
        result['device_inventory'][node_id] = dev
        seen = set()
        for iface in node.findall('interface'):
            index = int(iface.attrib['id'])
            if index < 0 or index in seen:
                raise ValueError(f'{name} 的接口 ID 无效或重复')
            seen.add(index)
            if iface.get('name'):
                dev['port_name_map'][index] = iface.get('name')
            net_id = iface.get('network_id')
            if net_id and net_id != '0':
                attachments.setdefault(net_id, []).append((node_id, index, iface.get('name', '')))
    network_ids = set()
    for net in networks.findall('network'):
        net_id = net.get('id')
        if not net_id or net_id in network_ids:
            raise ValueError('EVE-NG 网络 ID 缺失或重复')
        network_ids.add(net_id)
        members = attachments.get(net_id, [])
        direct = net.get('type', '').lower() == 'bridge' and len(members) == 2 and members[0][0] != members[1][0] and net.get('visibility', '0') == '0'
        if direct:
            edges = [(members[0], members[1])]
        elif members or net.get('visibility', '0') == '1' or net.get('type', '').lower().startswith('pnet'):
            synthetic = 'eve-network:' + net_id
            if synthetic in result['device_inventory']:
                raise ValueError('网络节点 ID 冲突')
            dev = {'id': synthetic, 'name': net.get('name') or f'网络 {net_id}', 'model': net.get('type', 'network'),
                   'category': '网络', 'mac': '', 'com_port': '', 'interfaces': [], 'port_name_map': {},
                   'position': {'x': float(net.get('left') or 0), 'y': float(net.get('top') or 0)}}
            result['device_inventory'][synthetic] = dev
            edges = [(member, (synthetic, i, '')) for i, member in enumerate(members)]
        else:
            edges = []
        for a, b in edges:
            pair = {'src_port_index': a[1], 'dst_port_index': b[1], 'line_type': 'EVE-NG ' + net.get('type', 'network'),
                    'src_port_name': a[2] or '网络', 'dst_port_name': b[2] or '网络',
                    'declared': {'source_interface': a[2], 'target_interface': b[2]}}
            result['connections'].append({'source_id': a[0], 'target_id': b[0],
                'source': result['device_inventory'][a[0]]['name'], 'target': result['device_inventory'][b[0]]['name'],
                'aggregated': False, 'link_count': 1, 'interface_pairs': [pair]})
    if set(attachments) - network_ids:
        raise ValueError('接口引用了不存在的 EVE-NG 网络')
    categories, models = {}, {}
    for device in result['device_inventory'].values():
        categories[device['category']] = categories.get(device['category'], 0) + 1
        models[device['model']] = models.get(device['model'], 0) + 1
    network_count = categories.get('网络', 0)
    result['summary'] = {'total_devices': len(result['device_inventory']) - network_count, 'total_networks': network_count, 'total_connections': len(result['connections']),
                         'aggregated_links': 0, 'by_category': categories, 'by_model': models, 'vlan_count': 0, 'subnet_count': 0}
    return result


def classify_device(model):
    mapping = {
        "S3700": "交换机(三层)",
        "S5700": "交换机(三层)",
        "AR2220": "路由器",
        "AR2240": "路由器",
        "USG5500": "防火墙",
        "AC6005": "无线控制器",
        "AC6605": "无线控制器",
        "AP4050": "无线AP",
        "PC": "终端PC",
        "Server": "服务器",
        "Cellphone": "手机终端"
    }
    return mapping.get(model, f"未知设备({model})")


def parse_server_settings(settings):
    config = {}
    patterns = {
        "ipaddr": r"-ipaddr\s+(\S+)",
        "ipmask": r"-ipmask\s+(\S+)",
        "gateway": r"-gateway\s+(\S+)",
        "ipv6addr": r"-ipv6addr\s+(\S+)",
        "ipv6gateway": r"-ipv6gateway\s+(\S+)",
        "prefixlen": r"-prefixlen\s+(\d+)",
        "ipv4dns": r"-ipv4dns\s+(\S+)",
        "ipv6dns": r"-ipv6dns\s+(\S+)",
        "dnslist": r"-dnslist\s+(\S+)",
        "eth": r"-eth\s+(\S+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, settings)
        if match:
            config[key] = match.group(1)
    return config


def parse_terminal_settings(settings, model):
    config = {}
    patterns = {
        "ip": r"-simpc_ip\s+(\S+)" if model == "PC" else r"-simsta_ip\s+(\S+)",
        "mask": r"-simpc_mask\s+(\S+)" if model == "PC" else r"-simsta_mask\s+(\S+)",
        "gateway": r"-simpc_gateway\s+(\S+)" if model == "PC" else r"-simsta_gateway\s+(\S+)",
        "mac": r"-simpc_mac\s+(\S+)" if model == "PC" else r"-simsta_mac\s+(\S+)",
        "dhcp": r"-simpc_dhcp_state\s+(\d+)" if model == "PC" else r"-simsta_check\s+(\d+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, settings)
        if match:
            config[key] = match.group(1)
    return config


def parse_ap_settings(settings):
    config = {}
    patterns = {
        "ap_mac": r"-apMac\s+(\S+)",
        "ap_sn": r"-apSN\s+(\S+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, settings)
        if match:
            config[key] = match.group(1)
    return config


def build_port_name_map(interfaces):
    port_map = {}
    port_num = 1
    for iface in interfaces:
        iface_name = iface["name"]
        for i in range(iface["count"]):
            global_idx = iface.get("_global_offset", 0) + i
            port_map[global_idx] = f"{iface_name}0/0/{port_num}"
            port_num += 1
    return port_map


def build_port_summary(interfaces):
    parts = []
    port_num = 1
    for iface in interfaces:
        count = iface["count"]
        if count == 1:
            parts.append(f"{iface['name']}0/0/{port_num}")
        else:
            parts.append(f"{iface['name']}0/0/{port_num}~{iface['name']}0/0/{port_num + count - 1}")
        port_num += count
    return ", ".join(parts)


def resolve_interface_names(connections, device_inventory):
    for conn in connections:
        src_dev = device_inventory.get(conn["source_id"], {})
        dst_dev = device_inventory.get(conn["target_id"], {})
        src_map = src_dev.get("port_name_map", {})
        dst_map = dst_dev.get("port_name_map", {})

        for pair in conn["interface_pairs"]:
            pair["src_port_name"] = src_map.get(pair["src_port_index"], f"?[{pair['src_port_index']}]")
            pair["dst_port_name"] = dst_map.get(pair["dst_port_index"], f"?[{pair['dst_port_index']}]")


LAYER_LABELS = {
    "core": "核心层",
    "aggregation": "汇聚层",
    "access": "接入层",
    "terminal": "终端层",
    "unknown": "未分层"
}


def infer_network_hierarchy(data):
    layer_ranges = detect_layer_ranges(data["device_inventory"])

    layers = {"core": [], "aggregation": [], "access": [], "terminal": [], "unknown": []}

    for dev in data["device_inventory"].values():
        y = dev["position"]["y"]
        assigned = False

        for layer_name, (y_min, y_max) in layer_ranges.items():
            if y_min <= y <= y_max:
                layers[layer_name].append(dev["name"])
                dev["_layer"] = layer_name
                assigned = True
                break

        if not assigned:
            dev["_layer"] = "unknown"
            layers["unknown"].append(dev["name"])

    assign_device_roles(data, layers)
    return layers


def detect_layer_ranges(data):
    y_values = [dev["position"]["y"] for dev in data.values()]

    if not y_values:
        return _default_ranges()

    clusters = cluster_1d(y_values, threshold=120)

    if len(clusters) <= 1:
        all_y = [y for y in y_values]
        return {"core": (min(all_y), max(all_y)),
                "aggregation": (0, 0),
                "access": (0, 0),
                "terminal": (0, 0),
                "unknown": (0, 0)}

    sorted_clusters = sorted(clusters, key=lambda c: c["center"])

    ranges = {}
    if len(sorted_clusters) >= 4:
        ranges["core"] = (sorted_clusters[0]["min"], sorted_clusters[0]["max"])
        ranges["aggregation"] = (sorted_clusters[1]["min"], sorted_clusters[1]["max"])
        ranges["access"] = (sorted_clusters[2]["min"], sorted_clusters[2]["max"])
        ranges["terminal"] = (sorted_clusters[3]["min"], sorted_clusters[3]["max"])
    elif len(sorted_clusters) == 3:
        ranges["core"] = (sorted_clusters[0]["min"], sorted_clusters[0]["max"])
        ranges["access"] = (sorted_clusters[1]["min"], sorted_clusters[1]["max"])
        ranges["terminal"] = (sorted_clusters[2]["min"], sorted_clusters[2]["max"])
    elif len(sorted_clusters) == 2:
        if sorted_clusters[0]["center"] < 500:
            ranges["core"] = (sorted_clusters[0]["min"], sorted_clusters[0]["max"])
            ranges["access"] = (sorted_clusters[1]["min"], sorted_clusters[1]["max"])
        else:
            ranges["access"] = (sorted_clusters[0]["min"], sorted_clusters[0]["max"])
            ranges["terminal"] = (sorted_clusters[1]["min"], sorted_clusters[1]["max"])

    for layer in ["core", "aggregation", "access", "terminal", "unknown"]:
        if layer not in ranges:
            ranges[layer] = (0, 0)

    return ranges


def _default_ranges():
    return {"core": (0, 0), "aggregation": (0, 0), "access": (0, 0), "terminal": (0, 0), "unknown": (0, 0)}


def cluster_1d(values, threshold):
    sorted_vals = sorted(set(v for v in values))
    if not sorted_vals:
        return []

    clusters = []
    current = [sorted_vals[0]]

    for v in sorted_vals[1:]:
        if v - current[-1] <= threshold:
            current.append(v)
        else:
            clusters.append({
                "center": sum(current) / len(current),
                "min": min(current),
                "max": max(current)
            })
            current = [v]

    clusters.append({
        "center": sum(current) / len(current),
        "min": min(current),
        "max": max(current)
    })
    return clusters


def assign_device_roles(data, layers):
    for dev in data["device_inventory"].values():
        model = dev["model"]
        name = dev["name"]
        layer = dev["_layer"]

        if model.startswith("AR"):
            if "AR5" in name or is_edge_router(dev, data):
                dev["_role"] = "出口路由器"
            else:
                dev["_role"] = "路由器"
        elif model == "USG5500":
            dev["_role"] = "防火墙"
        elif model.startswith("AC"):
            dev["_role"] = "无线控制器"
        elif model == "AP4050":
            dev["_role"] = "无线接入点"
        elif model == "Server":
            dev["_role"] = "服务器"
        elif model == "PC":
            dev["_role"] = "终端PC"
        elif model == "Cellphone":
            dev["_role"] = "手机终端"
        elif model.startswith("S"):
            if layer == "core":
                dev["_role"] = "核心层交换机"
            elif layer == "aggregation":
                dev["_role"] = "汇聚层交换机"
            elif layer == "access":
                dev["_role"] = "接入层交换机"
            else:
                dev["_role"] = "交换机"
        else:
            dev["_role"] = model


def is_edge_router(dev, data):
    connections = data["connections"]
    peer_count = 0
    for conn in connections:
        if dev["id"] in (conn["source_id"], conn["target_id"]):
            peer_count += 1
    return peer_count >= 4


def generate_ai_markdown(data):
    layers = infer_network_hierarchy(data)
    lines = []

    lines.append("# 网络拓扑分析报告")
    lines.append("")
    lines.append(_generate_architecture_summary(data, layers))
    lines.append("")

    lines.append("## 拓扑图 (Mermaid)")
    lines.append("")
    lines.append("```mermaid")
    lines.append(generate_mermaid_topology(data, layers))
    lines.append("```")
    lines.append("")

    lines.append("## 设备清单（按网络层次分组）")
    lines.append("")

    layer_order = ["core", "aggregation", "access", "terminal", "unknown"]
    for layer_name in layer_order:
        if layer_name == "unknown":
            continue
        label = LAYER_LABELS.get(layer_name, layer_name)
        devs_in_layer = []
        for dev in data["device_inventory"].values():
            if dev.get("_layer") == layer_name:
                devs_in_layer.append(dev)

        if not devs_in_layer:
            continue

        lines.append(f"### {label}")
        lines.append("")
        lines.append("| 设备名 | 型号 | 角色 | 接口 | MAC地址 | COM端口 | 关键配置 |")
        lines.append("|--------|------|------|------|---------|---------|----------|")

        for dev in devs_in_layer:
            ports = build_port_summary(dev["interfaces"]) if dev["interfaces"] else "-"
            extra = ""
            if "server_config" in dev:
                cfg = dev["server_config"]
                extra = f"IP={cfg.get('ipaddr','?')}/{cfg.get('ipmask','?')} GW={cfg.get('gateway','?')}"
            elif "ap_config" in dev:
                cfg = dev["ap_config"]
                extra = f"SN={cfg.get('ap_sn','?')}"
            elif "terminal_config" in dev:
                cfg = dev["terminal_config"]
                ip = cfg.get("ip", "0.0.0.0")
                extra = f"IP={ip if ip != '0.0.0.0' else 'DHCP'}"
            lines.append(
                f"| {dev['name']} | {dev['model']} | {dev.get('_role', dev['category'])} | "
                f"{ports} | {dev['mac'] or '-'} | {dev['com_port']} | {extra} |"
            )
        lines.append("")

    lines.append("## 网络连接")
    lines.append("")
    lines.append("| 源设备 | 源端口 | 目标设备 | 目标端口 | 链路类型 |")
    lines.append("|--------|--------|----------|----------|----------|")

    for conn in data["connections"]:
        for pair in conn["interface_pairs"]:
            src_port = pair.get("src_port_name", f"#{pair['src_port_index']}")
            dst_port = pair.get("dst_port_name", f"#{pair['dst_port_index']}")
            link_type = "聚合链路" if conn["aggregated"] else "单链路"
            lines.append(
                f"| {conn['source']} | {src_port} | "
                f"{conn['target']} | {dst_port} | {link_type} |"
            )
    lines.append("")

    if data["vlans"] or data["subnets"]:
        lines.append("## VLAN 与 IP 子网规划")
        lines.append("")
        lines.append("### VLAN")
        lines.append("")
        lines.append("| VLAN名称 | 位置X | 位置Y |")
        lines.append("|----------|-------|-------|")
        for v in data["vlans"]:
            lines.append(f"| {v['name']} | {v['position']['x']:.0f} | {v['position']['y']:.0f} |")
        lines.append("")

        lines.append("### 子网")
        lines.append("")
        lines.append("| 子网 | 位置X | 位置Y |")
        lines.append("|------|-------|-------|")
        for s in data["subnets"]:
            lines.append(f"| {s['subnet']} | {s['position']['x']:.0f} | {s['position']['y']:.0f} |")
        lines.append("")

    if data["labels"]:
        lines.append("## 拓扑标注")
        lines.append("")
        lines.append("| 标注内容 | 位置X | 位置Y |")
        lines.append("|----------|-------|-------|")
        for lb in data["labels"]:
            lines.append(f"| {lb['text']} | {lb['position']['x']:.0f} | {lb['position']['y']:.0f} |")
        lines.append("")

    summary = data["summary"]
    lines.append("## 统计摘要")
    lines.append("")
    lines.append(f"- 设备总数: **{summary['total_devices']}**")
    lines.append(f"- 链路总数: **{summary['total_connections']}**（含 {summary['aggregated_links']} 条聚合链路）")
    lines.append(f"- VLAN: **{summary['vlan_count']}** 个")
    lines.append(f"- 子网: **{summary['subnet_count']}** 个")
    lines.append("")
    lines.append("| 设备分类 | 数量 |")
    lines.append("|----------|------|")
    for cat, count in summary["by_category"].items():
        lines.append(f"| {cat} | {count} |")
    lines.append("")

    return "\n".join(lines)


def _generate_architecture_summary(data, layers):
    parts = []

    topo_name = "本网络"
    label_texts = [lb["text"] for lb in data["labels"]]
    if "总公司" in label_texts or "核心层" in label_texts:
        topo_name = "璐园科技公司网络"

    parts.append(f"{topo_name}采用经典的分层网络架构，共 **{data['summary']['total_devices']}** 台设备。")

    core_devs = layers.get("core", [])
    agg_devs = layers.get("aggregation", [])
    access_devs = layers.get("access", [])
    term_devs = layers.get("terminal", [])

    if core_devs:
        core_roles = _unique_roles(layers["core"], data)
        parts.append(f"**核心层**含 {len(core_devs)} 台设备（{', '.join(core_roles)}），负责高速转发和路由。")
    if agg_devs:
        agg_roles = _unique_roles(layers["aggregation"], data)
        parts.append(f"**汇聚层**含 {len(agg_devs)} 台设备（{', '.join(agg_roles)}），负责接入层流量汇聚和策略控制。")
    if access_devs:
        access_roles = _unique_roles(layers["access"], data)
        parts.append(f"**接入层**含 {len(access_devs)} 台设备（{', '.join(access_roles)}），负责终端接入。")
    if term_devs:
        term_roles = _unique_roles(layers["terminal"], data)
        parts.append(f"**终端层**含 {len(term_devs)} 台设备（{', '.join(term_roles)}）。")

    vlans = [v["name"] for v in data["vlans"]]
    if vlans:
        parts.append(f"规划了 {len(vlans)} 个 VLAN（{'、'.join(vlans)}）。")

    subnets = [s["subnet"] for s in data["subnets"]]
    if subnets:
        unique_subnets = sorted(set(
            s.split("/")[0] + "/24" if "/" in s else s + "/24" for s in subnets
        ))
        parts.append(f"使用子网段：{'、'.join(unique_subnets)}。")

    for dev in data["device_inventory"].values():
        if "server_config" in dev:
            cfg = dev["server_config"]
            parts.append(
                f"服务器 **{dev['name']}** IP={cfg.get('ipaddr','?')}/{cfg.get('ipmask','?')}，"
                f"网关={cfg.get('gateway','?')}。"
            )

    return "  ".join(parts)


def _unique_roles(layer_names, data):
    roles = set()
    for dev in data["device_inventory"].values():
        if dev["name"] in layer_names:
            role = dev.get("_role", dev["category"])
            roles.add(role)
    return sorted(roles)


def generate_mermaid_topology(data, layers):
    indent = "    "
    lines = []
    lines.append("graph TB")

    layer_order = ["core", "aggregation", "access", "terminal", "unknown"]
    layer_dev_map = {}
    for layer_name in layer_order:
        if layer_name == "unknown":
            continue
        devs = []
        for dev in data["device_inventory"].values():
            if dev.get("_layer") == layer_name:
                devs.append(dev)
        if devs:
            layer_dev_map[layer_name] = devs

    device_ids = {}
    for dev_id in data["device_inventory"]:
        safe_id = "D" + dev_id.replace("-", "")[:10]
        device_ids[dev_id] = safe_id

    lines.append(indent + f"subgraph terminal[{LAYER_LABELS['terminal']}]")
    term_devs = layer_dev_map.get("terminal", [])
    if term_devs:
        for dev in term_devs:
            icon = "🖥" if dev["model"] == "PC" else "📱" if dev["model"] == "Cellphone" else "📡" if dev["model"] == "AP4050" else "🖧" if dev["model"] == "Server" else ""
            label = f"{icon} {dev['name']}<br/>{dev['model']}"
            lines.append(indent * 2 + f"{device_ids[dev['id']]}[{label}]")
    else:
        lines.append(indent * 2 + "EMPTY[ ]")
    lines.append(indent + "end")

    if layer_dev_map.get("access"):
        lines.append(indent + f"subgraph access[{LAYER_LABELS['access']}]")
        for dev in layer_dev_map.get("access", []):
            lines.append(indent * 2 + f"{device_ids[dev['id']]}[🔹 {dev['name']}<br/>{dev['model']}]")
        lines.append(indent + "end")

    if layer_dev_map.get("aggregation"):
        lines.append(indent + f"subgraph aggregation[{LAYER_LABELS['aggregation']}]")
        for dev in layer_dev_map.get("aggregation", []):
            lines.append(indent * 2 + f"{device_ids[dev['id']]}[🔸 {dev['name']}<br/>{dev['model']}]")
        lines.append(indent + "end")

    if layer_dev_map.get("core"):
        lines.append(indent + f"subgraph core[{LAYER_LABELS['core']}]")
        for dev in layer_dev_map.get("core", []):
            lines.append(indent * 2 + f"{device_ids[dev['id']]}[🔴 {dev['name']}<br/>{dev['model']}]")
        lines.append(indent + "end")

    for conn in data["connections"]:
        src = device_ids.get(conn["source_id"], conn["source_id"][:12])
        dst = device_ids.get(conn["target_id"], conn["target_id"][:12])
        label = ""
        if conn["aggregated"]:
            label = f"|聚合x{conn['link_count']}|"
        lines.append(indent + f"{src} -->{label} {dst}")

    return "\n".join(lines)


def generate_ai_prompt_context(data):
    layers = infer_network_hierarchy(data)
    lines = []

    lines.append("# 网络拓扑上下文")
    lines.append("")
    lines.append("## 架构")
    lines.append("")
    lines.append(_generate_architecture_summary(data, layers))
    lines.append("")

    lines.append("## 设备角色")
    lines.append("")
    for layer_name in ["core", "aggregation", "access", "terminal"]:
        devs = [d for d in data["device_inventory"].values() if d.get("_layer") == layer_name]
        if not devs:
            continue
        label = LAYER_LABELS.get(layer_name, layer_name)
        names = ", ".join(
            f"{d['name']}({d.get('_role', d['model'])})" for d in devs
        )
        lines.append(f"- **{label}**: {names}")
    lines.append("")

    lines.append("## 关键连接")
    lines.append("")
    lines.append("```")
    for conn in data["connections"]:
        pairs_str = []
        for pair in conn["interface_pairs"]:
            s = pair.get("src_port_name", f"#{pair['src_port_index']}")
            d = pair.get("dst_port_name", f"#{pair['dst_port_index']}")
            pairs_str.append(f"[{s}↔{d}]")
        agg = f"(聚合x{conn['link_count']})" if conn["aggregated"] else ""
        lines.append(f"{conn['source']} --{'=' if conn['aggregated'] else '-'}- {conn['target']} {agg} {' '.join(pairs_str)}")
    lines.append("```")
    lines.append("")

    return "\n".join(lines)


def generate_readable_summary(data):
    lines = []
    lines.append("=" * 60)
    lines.append("  网络拓扑分析报告")
    lines.append("=" * 60)

    summary = data["summary"]
    lines.append(f"\n拓扑版本: {data['version']}")
    lines.append(f"设备总数: {summary['total_devices']}")
    lines.append(f"链路总数: {summary['total_connections']}")
    lines.append(f"聚合链路: {summary['aggregated_links']}")
    lines.append(f"VLAN数量: {summary['vlan_count']}")
    lines.append(f"子网数量: {summary['subnet_count']}")

    lines.append("\n--- 设备分类统计 ---")
    for cat, count in summary["by_category"].items():
        lines.append(f"  {cat}: {count}台")

    lines.append("\n--- 设备型号统计 ---")
    for model, count in summary["by_model"].items():
        lines.append(f"  {model}: {count}台")

    lines.append("\n--- 设备清单 ---")
    for dev in data["device_inventory"].values():
        extra = ""
        if "server_config" in dev:
            cfg = dev["server_config"]
            extra = f"  IP={cfg.get('ipaddr','?')}/{cfg.get('ipmask','?')}  GW={cfg.get('gateway','?')}"
        elif "terminal_config" in dev:
            cfg = dev["terminal_config"]
            extra = f"  IP={cfg.get('ip','DHCP')}  MAC={cfg.get('mac','?')}"
        elif "ap_config" in dev:
            cfg = dev["ap_config"]
            extra = f"  AP_MAC={cfg.get('ap_mac','?')}  SN={cfg.get('ap_sn','?')}"

        port_summary = build_port_summary(dev["interfaces"])

        lines.append(
            f"  [{dev['category']}] {dev['name']} ({dev['model']})  "
            f"MAC={dev['mac']}  COM={dev['com_port']}{extra}"
        )
        if port_summary:
            lines.append(f"      接口: {port_summary}")

    lines.append("\n--- 网络连接 ---")
    for conn in data["connections"]:
        parts = []
        for pair in conn["interface_pairs"]:
            src_port = pair.get("src_port_name", f"#{pair['src_port_index']}")
            dst_port = pair.get("dst_port_name", f"#{pair['dst_port_index']}")
            parts.append(f"{src_port} ↔ {dst_port}")
        ports_str = "  ".join(parts)
        agg = f"  [聚合 x{conn['link_count']}]" if conn["aggregated"] else ""
        lines.append(f"  {conn['source']} <--> {conn['target']}{agg}")
        lines.append(f"      {ports_str}")

    lines.append("\n--- VLAN 规划 ---")
    for v in data["vlans"]:
        lines.append(f"  {v['name']}")

    lines.append("\n--- IP子网规划 ---")
    for s in data["subnets"]:
        lines.append(f"  {s['subnet']}")

    lines.append("\n--- 拓扑标签 ---")
    for lb in data["labels"]:
        lines.append(f"  {lb['text']}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


_topo_data = None
_topo_filename = None
topo_report_cache = {}

_STORAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_TOPO_DATA_FILE = os.path.join(_STORAGE_DIR, "topo_data.json")
_TOPO_CACHE_FILE = os.path.join(_STORAGE_DIR, "topo_cache.json")


def get_topo_data():
    return _topo_data


def get_topo_filename():
    return _topo_filename


def set_topo_data(data, filename):
    global _topo_data, _topo_filename
    _topo_data = data
    _topo_filename = filename
    topo_report_cache.clear()


def clear_topo_data():
    global _topo_data, _topo_filename
    _topo_data = None
    _topo_filename = None
    topo_report_cache.clear()


def get_cached_report(report_type):
    return topo_report_cache.get(report_type)


def set_cached_report(report_type, content):
    topo_report_cache[report_type] = content


def _load_json_file(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, Exception):
        return None


def _save_json_file(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass
