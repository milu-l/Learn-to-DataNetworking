import io
import asyncio
import json
import socket
import ssl
import unittest
from urllib.error import HTTPError, URLError
from types import SimpleNamespace
from unittest.mock import patch

import chat_service
import app


class DeepSeekTemplateTests(unittest.TestCase):
    def test_missing_deepseek_key_is_reported_before_network_request(self):
        old = dict(chat_service.config)
        try:
            chat_service.set_config({'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'clear_api_key': True})
            with patch.object(chat_service, 'build_opener') as opener:
                with self.assertRaisesRegex(ValueError, '尚未设置 DeepSeek API 密钥'):
                    chat_service.model_request([{'role': 'user', 'content': 'hello'}], [])
            opener.assert_not_called()
        finally:
            with chat_service.config_lock:
                chat_service.config.clear()
                chat_service.config.update(old)

    def test_direct_mode_explicitly_disables_proxy(self):
        old = dict(chat_service.config)
        class FakeOpener:
            def open(self, request, timeout):
                return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')
        try:
            chat_service.set_config({'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'api_key': 'test', 'proxy_mode': 'direct'})
            with patch.object(chat_service, 'build_opener', return_value=FakeOpener()) as build:
                chat_service.model_request([{'role': 'user', 'content': 'hello'}], [])
            handlers = build.call_args.args
            self.assertTrue(any(isinstance(handler, chat_service.ProxyHandler) and handler.proxies == {} for handler in handlers))
            self.assertEqual(chat_service.get_config()['proxy_mode'], 'direct')
            with self.assertRaises(ValueError):
                chat_service.set_config({'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'proxy_mode': 'unknown'})
        finally:
            with chat_service.config_lock:
                chat_service.config.clear()
                chat_service.config.update(old)

    def test_http_errors_are_not_retried(self):
        old = dict(chat_service.config)
        opener = SimpleNamespace(open=lambda request, timeout: (_ for _ in ()).throw(HTTPError(request.full_url, 401, 'Unauthorized', None, None)))
        try:
            chat_service.set_config({'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'api_key': 'test'})
            with patch.object(chat_service, 'build_opener', return_value=opener) as build, patch.object(chat_service.time, 'sleep') as wait:
                with self.assertRaisesRegex(ValueError, '密钥认证失败（HTTP 401）'):
                    chat_service.model_request([{'role': 'user', 'content': 'hello'}], [])
            build.assert_called_once()
            wait.assert_not_called()
        finally:
            with chat_service.config_lock:
                chat_service.config.clear()
                chat_service.config.update(old)

    def test_connection_test_endpoint_does_not_invoke_mcp(self):
        client = app.app.test_client()
        with patch.object(chat_service, 'model_request', return_value={'content': '连接成功'}) as model, \
                patch.object(chat_service, 'Client') as mcp:
            response = client.post('/api/chat/test')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json['success'])
        self.assertEqual(model.call_args.args[1], [])
        mcp.assert_not_called()
        self.assertEqual(client.post('/api/chat/test', environ_base={'REMOTE_ADDR': '192.0.2.1'}).status_code, 403)

    def test_retry_once_after_connection_reset(self):
        old = dict(chat_service.config)
        calls = []
        class FakeOpener:
            def open(self, request, timeout):
                calls.append(request)
                if len(calls) == 1:
                    raise URLError(ConnectionResetError('connection reset'))
                return io.BytesIO(json.dumps({'choices': [{'message': {'content': 'ok'}}]}).encode())
        try:
            chat_service.set_config({'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'api_key': 'test'})
            with patch.object(chat_service, 'build_opener', return_value=FakeOpener()), patch.object(chat_service.time, 'sleep') as wait:
                self.assertEqual(chat_service.model_request([{'role': 'user', 'content': 'hello'}], [])['content'], 'ok')
            self.assertEqual(len(calls), 2)
            wait.assert_called_once_with(0.5)
        finally:
            with chat_service.config_lock:
                chat_service.config.clear()
                chat_service.config.update(old)

    def test_connection_diagnostics_do_not_expose_sensitive_exception_text(self):
        cases = [(URLError(socket.timeout()), '超时'),
                 (URLError(socket.gaierror(-2, 'test')), '域名解析'),
                 (URLError(ssl.SSLCertVerificationError('test')), '证书验证'),
                 (URLError(ConnectionResetError('test')), '中断'),
                 (URLError('Tunnel connection failed: proxy credential-secret'), '代理隧道')]
        for error, expected in cases:
            message = chat_service.connection_error_message(error)
            self.assertIn(expected, message)
            self.assertNotIn('credential-secret', message)

    def test_deepseek_config_uses_chat_completions_and_tools(self):
        old = dict(chat_service.config)
        requests = []

        class FakeOpener:
            def open(self, request, timeout):
                requests.append(request)
                return io.BytesIO(json.dumps({'choices': [{'message': {'role': 'assistant', 'content': 'ok'}}]}).encode())

        try:
            chat_service.set_config({'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash', 'api_key': 'test-key'})
            with patch.object(chat_service, 'build_opener', return_value=FakeOpener()):
                response = chat_service.model_request([{'role': 'user', 'content': 'hello'}], [])
            self.assertEqual(response['content'], 'ok')
            self.assertEqual(requests[0].full_url, 'https://api.deepseek.com/chat/completions')
            self.assertEqual(requests[0].get_header('Authorization'), 'Bearer test-key')
            body = json.loads(requests[0].data)
            self.assertEqual(body['model'], 'deepseek-flash')
            self.assertEqual(body['thinking'], {'type': 'disabled'})
            self.assertNotIn('parallel_tool_calls', body)
            self.assertNotIn('api_key', chat_service.get_config())
        finally:
            with chat_service.config_lock:
                chat_service.config.clear()
                chat_service.config.update(old)

    def test_multiple_tool_calls_are_processed_with_individual_approvals(self):
        tool_names = ['list_sessions', 'connect_device', 'send_command']
        called, model_messages = [], []
        calls = [{'id': f'id{i}', 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}
                 for i, name in enumerate(tool_names)]

        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def list_tools(self):
                return [SimpleNamespace(name=name, description='', input_schema={}) for name in tool_names]
            async def call_tool(self, name, args, **kwargs):
                called.append(name)
                return SimpleNamespace(content=[SimpleNamespace(text='ok')])

        def respond(messages, schemas):
            model_messages.append(list(messages))
            return {'content': None, 'tool_calls': calls} if len(model_messages) == 1 else {'content': '完成'}

        with patch.object(chat_service, 'Client', FakeClient), patch.object(chat_service, 'model_request', side_effect=respond):
            first = asyncio.run(chat_service.run_chat([{'role': 'user', 'content': '测试'}]))
            self.assertEqual(first['pending']['name'], 'connect_device')
            self.assertEqual(called, ['list_sessions'])
            second = chat_service.confirm(first['pending']['token'], True)
            self.assertEqual(second['pending']['name'], 'send_command')
            self.assertEqual(called, ['list_sessions', 'connect_device'])
            last = chat_service.confirm(second['pending']['token'], True)
        self.assertEqual(last['reply'], '完成')
        self.assertEqual(called, tool_names)
        self.assertEqual([m['tool_call_id'] for m in model_messages[-1] if m.get('role') == 'tool'], ['id0', 'id1', 'id2'])


if __name__ == '__main__':
    unittest.main()
