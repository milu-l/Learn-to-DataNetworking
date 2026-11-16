"""Enable global LLDP on a connected Huawei device without saving configuration."""
import re

from session_manager import session_manager
from telnet_service import send_command
from topology_resolver import command_body

LLDP_CHECK = 'display current-configuration | include lldp'


def _checked(output, command):
    body = command_body(output, command)
    if not body:
        detail = re.sub(r'[\r\n]+', ' ', str(output or '')).strip()[:200]
        raise RuntimeError(f'{command} 返回不完整或执行失败' + (f'：{detail}' if detail else '：没有设备回显'))
    prompt = re.search(r'(?m)^\s*(<[^\r\n<>]+>|\[[^\r\n\[\]]+\])\s*$', body)
    if not prompt:
        raise RuntimeError(f'{command} 未返回设备提示符')
    return body, prompt.group(1)


def _status(body):
    # The command echo is removed by command_body; a prompt alone is not output.
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    lines = [line for line in lines if not re.fullmatch(r'<[^<>]+>|\[[^\[\]]+\]', line)]
    return 'enabled' if lines else 'disabled'


def enable_lldp(ip, port):
    session = session_manager.get_session_by_port(ip, port)
    if not session:
        return {'ok': False, 'message': '设备未连接'}
    session_id = session['session_id']
    in_config = False
    with session['command_lock']:
        try:
            body, prompt = _checked(send_command(session_id, LLDP_CHECK), LLDP_CHECK)
            state = _status(body)
            if state == 'enabled':
                return {'ok': True, 'message': '全局 LLDP 已开启'}
            in_config = prompt.startswith('[')
            if in_config:
                _, prompt = _checked(send_command(session_id, 'return'), 'return')
                if not prompt.startswith('<'):
                    raise RuntimeError('无法返回用户视图')
                in_config = False
            # Attempt cleanup even if the transition reply is incomplete.
            in_config = True
            _, prompt = _checked(send_command(session_id, 'system-view'), 'system-view')
            if not prompt.startswith('['):
                raise RuntimeError('无法进入系统视图')
            _, prompt = _checked(send_command(session_id, 'lldp enable'), 'lldp enable')
            if not prompt.startswith('['):
                raise RuntimeError('启用 LLDP 后视图异常')
            _, prompt = _checked(send_command(session_id, 'return'), 'return')
            if not prompt.startswith('<'):
                raise RuntimeError('无法返回用户视图')
            in_config = False
            body, _ = _checked(send_command(session_id, LLDP_CHECK), LLDP_CHECK)
            if _status(body) != 'enabled':
                raise RuntimeError('全局 LLDP 未确认开启')
            return {'ok': True, 'message': '已开启全局 LLDP（未保存）'}
        except Exception as exc:
            if in_config:
                try:
                    _, prompt = _checked(send_command(session_id, 'return'), 'return')
                    if not prompt.startswith('<'):
                        raise RuntimeError('仍在配置视图')
                except Exception:
                    return {'ok': False, 'message': f'LLDP 操作失败：{exc}；无法确认已退出配置视图'}
            return {'ok': False, 'message': f'LLDP 操作失败：{exc}'}


def rename_topology_device(ip, port, name):
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}', name):
        return {'ok': False, 'message': '拓扑设备名无效，仅支持 1–63 位英文字母、数字、下划线、点和连字符'}
    session = session_manager.get_session_by_port(ip, port)
    if not session:
        return {'ok': False, 'message': '设备未连接'}
    sid = session['session_id']
    in_config = False
    with session['command_lock']:
        try:
            # An empty line requests a prompt without executing a device command.
            output = send_command(sid, '')
            prompts = re.findall(r'(?m)^\s*(<[^\r\n<>]+>|\[[^\r\n\[\]]+\])\s*$', output or '')
            if not prompts:
                raise RuntimeError('未收到设备提示符，无法确认当前视图')
            prompt = prompts[-1]
            in_config = prompt.startswith('[')
            if in_config:
                _, prompt = _checked(send_command(sid, 'return'), 'return')
                if not prompt.startswith('<'):
                    raise RuntimeError('无法返回用户视图')
                in_config = False
            command = 'screen-length 0 temporary'
            send_command(sid, command, wait_seconds=0.5)
            in_config = True
            _, prompt = _checked(send_command(sid, 'system-view'), 'system-view')
            if not prompt.startswith('['):
                raise RuntimeError('无法进入系统视图')
            if prompt[1:-1] != name:
                _, prompt = _checked(send_command(sid, 'sysname ' + name), 'sysname ' + name)
                if prompt != '[' + name + ']':
                    raise RuntimeError(f'设备名称未确认修改成功（当前提示符 {prompt}）')
            _, prompt = _checked(send_command(sid, 'return'), 'return')
            if prompt != '<' + name + '>':
                raise RuntimeError(f'设备名称未确认修改成功（当前提示符 {prompt}）')
            in_config = False
            return {'ok': True, 'message': f'设备名已设为 {name}（未保存）'}
        except Exception as exc:
            if in_config:
                try:
                    _, prompt = _checked(send_command(sid, 'return'), 'return')
                    if not prompt.startswith('<'):
                        raise RuntimeError('仍在配置视图')
                except Exception:
                    return {'ok': False, 'message': f'设备改名失败：{exc}；无法确认已退出配置视图'}
            return {'ok': False, 'message': f'设备改名失败：{exc}'}


def configure_topology_device(ip, port, name):
    """Enable global LLDP after the rename phase."""
    if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,62}', name):
        return {'ok': False, 'message': '拓扑设备名无效'}
    session = session_manager.get_session_by_port(ip, port)
    if not session:
        return {'ok': False, 'message': '设备未连接'}
    with session['command_lock']:
        enabled = enable_lldp(ip, port)
        if not enabled['ok']:
            return {'ok': False, 'message': enabled.get('message', 'LLDP 配置失败')}
        return {'ok': True, 'message': enabled.get('message', '全局 LLDP 已开启') + '；未检查接口级 LLDP'}
