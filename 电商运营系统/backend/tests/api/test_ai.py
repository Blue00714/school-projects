"""M5 AI assistant / brief contract tests (no real LLM key configured).

Rules under test (``API契约.md`` 5.7):
* status must clearly report "not configured" and never expose a key;
* the read-only tool list is declared and non-destructive;
* answers are produced by the local rule planner, say explicitly that no LLM was
  called, carry range/batch labels and only repeat numbers that the tools produced;
* an unmappable question triggers one specific follow-up instead of an invented answer;
* briefs are deterministic Markdown, downloadable, and missing ids are 404.

The 30-question sample lives in ``fixtures.AI_QUESTIONS``; numbers are cross-checked
against a fresh ``/api/dashboard`` response for the same range.
"""
from __future__ import annotations

import json
import unittest
from urllib.parse import urlparse

from apiharness import (ADMIN_PASSWORD, ADMIN_USER, ApiClient, ApiTestCase,
                        extract_numbers, flatten_text, jdump)
from fixtures import (AI_QUESTIONS, MUST_CLARIFY_IDS, NO_PROFIT_QUESTION_IDS,
                      all_numbers, extra_numbers_from_batch, number_approved)

CANARY_KEY = 'sk-qa-CANARY-must-never-leak-0001'
ALLOWED_STATUS = {'ok', 'need_clarification', 'failed', 'not_configured'}
DANGEROUS_TOOL_WORDS = ('sql', 'shell', 'exec', 'subprocess', 'file_read', 'write_file',
                        'delete', 'eval')


def _login_admin(base: str) -> ApiClient:
    client = ApiClient(base)
    response = client.login(ADMIN_USER, ADMIN_PASSWORD)
    if response.status != 200:
        raise RuntimeError(f'管理员登录失败：{response.status} {response.text()[:300]}')
    return client


class AiAssistantTests(ApiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)
        # A deterministic simulated inventory scenario so inventory tool questions have
        # real data (initial 100, lead-time 8, safety 30, daily demand 10 -> candidate 10).
        scenario = cls.admin.post('/api/inventory/scenarios', {
            'name': 'QA模拟场景', 'note': 'AI 测试用模拟场景', 'stock_codes': ['QA-ITEM-1']})
        if scenario.status == 201:
            scenario_id = scenario.json()['scenario']['id']
            cls.admin.put(f'/api/inventory/scenarios/{scenario_id}/items/QA-ITEM-1', {
                'name': '测试商品', 'initial_qty': 100, 'lead_time_days': 8,
                'safety_stock': 30, 'daily_demand': 10})

    # -- helpers -----------------------------------------------------------
    def ask(self, question: str, scope: dict | None = None) -> dict:
        body = {'question': question}
        if scope:
            body['scope'] = scope
        return self.expect_json(self.admin.post('/api/ai/ask', body), 200)

    def _batch_numbers(self) -> set[float]:
        payload = self.expect_json(self.admin.get('/api/imports/batches'), 200)
        approved: set[float] = set()
        for batch in payload.get('batches') or []:
            if batch.get('is_active'):
                approved |= extra_numbers_from_batch(batch)
        return approved

    COUNTRY_ALIASES = {'英国': 'United Kingdom', '法国': 'France', '德国': 'Germany',
                       '西班牙': 'Spain', '荷兰': 'Netherlands', '爱尔兰': 'EIRE'}

    def _approved_numbers(self, scope: dict, question: str, cache: dict) -> set[float]:
        """Numbers that the read-only tools may legitimately return for this question.

        Built from the real API: dashboard for the range (plus per-country dashboards
        when the question names a country), product search for a product scope, the
        first page of the order list (order tools quote order keys/quantities) and the
        active batch manifest.  Anything outside this set is a fabricated number.
        """
        approved: set[float] = set()

        def dashboard_numbers_for(params: dict) -> set[float]:
            key = json.dumps(params, sort_keys=True, ensure_ascii=False)
            if key not in cache:
                cache[key] = self.dashboard(self.admin, **params)
            return all_numbers(cache[key])

        approved |= dashboard_numbers_for(scope)
        for chinese, english in self.COUNTRY_ALIASES.items():
            if chinese in question:
                approved |= dashboard_numbers_for({**scope, 'country': english})
        product = scope.get('product')
        if product:
            approved |= all_numbers(self.expect_json(
                self.admin.get('/api/products', {'q': product}), 200))
        orders = self.expect_json(self.admin.get('/api/orders', {
            **scope, 'page': 1, 'page_size': 200}), 200)
        approved |= all_numbers(orders)
        approved |= self._batch_numbers()
        return approved

    # -- tests -------------------------------------------------------------
    def test_01_status_reports_unconfigured_and_no_secret(self):
        response = self.admin.get('/api/ai/status')
        payload = self.expect_json(response, 200)
        for field in ('llm_configured', 'provider', 'model', 'key_present',
                      'timeout_seconds', 'fallback', 'tools_count'):
            self.assertIn(field, payload, f'AI 状态缺少字段 {field}')
        self.assertIs(payload.get('llm_configured'), False,
                      '未配置密钥时不得声称已接入真实大模型')
        self.assertIs(payload.get('key_present'), False)
        self.assertEqual(payload.get('fallback'), 'local-rule-planner')
        self.assertGreaterEqual(int(payload.get('tools_count') or 0), 5,
                                '只读工具层至少应声明看板/商品/订单/预测/库存工具')
        text = response.text().lower()
        for forbidden in ('api_key', 'sk-', 'authorization'):
            self.assertNotIn(forbidden, text, f'AI 状态响应不得包含密钥相关字段：{forbidden}')
        health = self.expect_json(self.admin.get('/api/health'), 200)
        self.assertIs(health.get('ai_configured'), False)

    def test_02_tools_are_declared_read_only(self):
        payload = self.expect_json(self.admin.get('/api/ai/tools'), 200)
        tools = payload.get('tools')
        self.assertIsInstance(tools, list)
        self.assertGreaterEqual(len(tools), 5)
        names = set()
        for tool in tools:
            for field in ('name', 'description', 'params', 'read_only'):
                self.assertIn(field, tool, f'工具声明缺少 {field}：{jdump(tool)}')
            self.assertIs(tool.get('read_only'), True, '工具层必须全部只读')
            self.assertIsInstance(tool.get('params'), dict)
            names.add(tool['name'])
            lowered = tool['name'].lower()
            for word in DANGEROUS_TOOL_WORDS:
                self.assertNotIn(word, lowered, f'禁止声明危险工具：{tool["name"]}')
        self.assertIn('只读', payload.get('policy') or '',
                      f'工具策略必须声明只读边界：{payload.get("policy")!r}')

    def test_03_thirty_question_sample(self):
        dashboards: dict[str, dict] = {}
        stats = {'ok': 0, 'need_clarification': 0, 'failed': 0, 'not_configured': 0}
        checked_numbers = 0
        failures: list[str] = []

        for case in AI_QUESTIONS:
            qid = case['id']
            with self.subTest(qid=qid, question=case['question']):
                payload = self.ask(case['question'], case.get('scope'))
                status = payload.get('status')
                self.assertIn(status, ALLOWED_STATUS,
                              f'状态必须属于契约枚举：{status!r}（{jdump(payload)[:400]}）')
                stats[status] += 1
                for field in ('status', 'provider', 'llm_used', 'answer', 'tool_calls',
                              'range', 'batch', 'warnings', 'elapsed_seconds'):
                    self.assertIn(field, payload, f'AI 响应缺少字段 {field}')
                self.assertIs(payload.get('llm_used'), False,
                              '没有密钥时 llm_used 必须为 false，不得伪造大模型调用')
                if status != 'not_configured':
                    self.assertEqual(payload.get('provider'), 'local-rule-planner')
                self.assertIsInstance(payload.get('tool_calls'), list)
                self.assertIsInstance(payload.get('warnings'), list)
                self.assertIsInstance(payload.get('batch'), dict)
                self.assertIn('id', payload['batch'], '回答必须带批次标识')
                self.assertIsInstance(payload.get('range'), dict)

                text = json.dumps(payload, ensure_ascii=False)
                self.assertNotIn(CANARY_KEY, text)
                if status == 'ok':
                    self.assertIsInstance(payload.get('answer'), str)
                    self.assertTrue(payload['answer'].strip(), 'ok 回答不能为空')
                    self.assertIn('未调用大模型', text,
                                  '本地规则答案必须显式注明未调用大模型')
                    calls = payload['tool_calls']
                    self.assertTrue(calls, f'ok 回答必须至少调用一个只读工具：{text[:600]}')
                    for call in calls:
                        self.assertIn('name', call)
                        self.assertIn('ok', call)
                        self.assertIn('summary', call)
                        self.assertIs(call.get('ok'), True,
                                      f'已返回答案时工具调用不应失败：{jdump(call)}')
                    # Numbers quoted by the answer/tool trace must come from the tools.
                    scope = case.get('scope')
                    if scope and case.get('expect') == 'ok':
                        approved = self._approved_numbers(scope, case['question'], dashboards)
                        quoted = flatten_text({'answer': payload['answer'],
                                               'tool_calls': payload['tool_calls']})
                        unapproved = sorted({
                            value for value in extract_numbers(quoted)
                            if abs(value) >= 1000 and not number_approved(value, approved)
                        })
                        checked_numbers += 1
                        if unapproved:
                            failures.append(
                                f'#{qid} 出现工具结果之外的数字 {unapproved}: '
                                f'{payload["answer"][:200]!r}')
                elif status == 'need_clarification':
                    self.assertTrue((payload.get('clarification') or '').strip(),
                                    f'追问必须给出具体问题：{jdump(payload)[:400]}')
                    self.assertIsNone(payload.get('answer'),
                                      '需要澄清时不应同时给出编造答案')
                elif status == 'failed':
                    self.assertTrue((payload.get('error') or '').strip(),
                                    '失败回答必须说明原因')

        total = len(AI_QUESTIONS)
        if MUST_CLARIFY_IDS:
            answers = {case['id']: case for case in AI_QUESTIONS}
            for qid in MUST_CLARIFY_IDS:
                payload = self.ask(answers[qid]['question'], answers[qid].get('scope'))
                self.assertEqual(payload.get('status'), 'need_clarification',
                                 f'#{qid} 无法映射到只读工具，必须追问：{jdump(payload)[:400]}')

        # No profit figure may be invented: there is no cost data in the system.
        profit_asked = [case for case in AI_QUESTIONS if case['id'] in NO_PROFIT_QUESTION_IDS]
        for case in profit_asked:
            payload = self.ask(case['question'], case.get('scope'))
            text = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn('利润为', text, '无成本数据时不得给出利润结论')
            self.assertNotIn('利润率', text, '无成本数据时不得给出利润率')

        self.assertGreaterEqual(stats['ok'] / total, 0.6,
                                f'本地规则可回答问题占比过低：{stats}（共 {total} 题）')
        self.assertEqual(stats['failed'] + stats['not_configured'], 0,
                         f'本地规则层不应失败或未配置：{stats}')
        self.assertGreater(checked_numbers, 10,
                           '至少应有 10 个带明确日期范围的题目做数字核对')
        self.assertFalse(failures, 'AI 回答出现工具结果之外的数字：\n' + '\n'.join(failures))

    def test_04_brief_generation_and_download(self):
        created = self.expect_json(self.admin.post('/api/ai/brief', {
            'scope': {'start': '2011-11-10', 'end': '2011-12-09'}}), 201)
        brief = created.get('brief') or {}
        self.assertTrue(brief.get('id'))
        self.assertTrue(brief.get('title'))
        self.assertTrue(brief.get('created_at'))
        url = brief.get('url') or ''
        self.assertTrue(url.endswith('.md'), f'简报应提供 Markdown 下载地址：{url!r}')
        self.assertEqual(created.get('provider'), 'template')
        markdown = created.get('markdown') or ''
        self.assertGreater(len(markdown), 200, '简报内容过短')
        self.assertIn('2011-11-10', markdown, '简报必须写明查询范围')
        self.assertIn('2011-12-09', markdown)
        for label in ('模拟', '预测'):
            self.assertIn(label, markdown, f'简报应标注 {label} 边界')
        self.assertNotIn(CANARY_KEY, json.dumps(created, ensure_ascii=False))

        path = urlparse(url).path if url.startswith('http') else url
        download = self.admin.get(path)
        self.assertEqual(download.status, 200, download.text()[:300])
        self.assertIn('text/markdown', download.header('Content-Type'))
        self.assertIn('attachment', download.header('Content-Disposition'))
        self.assertGreater(len(download.text()), 200)
        self.assertIn('2011-11-10', download.text())

        self.expect_error(self.admin.get('/api/ai/briefs/does-not-exist.md'),
                          404, 'not_found')

    def test_05_bad_question_is_400(self):
        self.expect_error(self.admin.post('/api/ai/ask', {}), 400, 'bad_request')
        self.expect_error(self.admin.post('/api/ai/ask', {'question': '   '}),
                          400, 'bad_request')


class AiKeyLeakTests(ApiTestCase):
    """A configured-but-unusable key must never be echoed back to a client."""

    extra_env = {'ECE_AI_API_KEY': CANARY_KEY}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = _login_admin(cls.base)

    def test_key_is_present_but_never_exposed(self):
        status = self.expect_json(self.admin.get('/api/ai/status'), 200)
        self.assertIs(status.get('key_present'), True, '注入密钥后 key_present 应为 true')
        self.assertIs(status.get('llm_configured'), False,
                      '只有 key 没有 base_url/model 时不得视为已配置')
        self.assertEqual(status.get('fallback'), 'local-rule-planner')

        for path in ('/api/ai/status', '/api/ai/tools', '/api/health', '/api/options'):
            with self.subTest(path=path):
                response = self.admin.get(path)
                self.assertEqual(response.status, 200, response.text()[:300])
                self.assertNotIn(CANARY_KEY, response.text(), f'{path} 泄露了密钥')
                self.assertNotIn('api_key', response.text().lower())

        answer = self.admin.post('/api/ai/ask', {'question': '2011-11-10到2011-12-09的销量是多少？'})
        self.assertEqual(answer.status, 200, answer.text()[:300])
        self.assertNotIn(CANARY_KEY, answer.text(), '/api/ai/ask 泄露了密钥')
        self.assertIs(answer.json().get('llm_used'), False)


if __name__ == '__main__':
    unittest.main()
