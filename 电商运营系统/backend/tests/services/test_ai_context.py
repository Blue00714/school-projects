import json
import unittest
from email.message import Message
from io import BytesIO
from unittest.mock import patch

from app.services import assistant


class AiContextTests(unittest.TestCase):
    def test_stream_exposes_only_redacted_visible_text(self):
        cfg = {'base_url': 'https://example.invalid/v1', 'model': 'test',
               'timeout_seconds': 5, 'key_present': True}
        frames = [
            {'reasoning_content': 'private reasoning'},
            {'content': '答案 sk-sec'},
            {'content': 'ret-test，以及 unit-'},
            {'content': 'key.123456'},
            {'content': '，接着生成正文。'},
        ]
        payload = b''.join(
            b'data: ' + json.dumps({'choices': [{'delta': frame}]}).encode('utf-8') + b'\n\n'
            for frame in frames
        ) + b'data: [DONE]\n\n'
        reply = BytesIO(payload)
        reply.headers = Message()
        reply.headers['Content-Type'] = 'text/event-stream'
        timings = {}
        with patch.object(assistant.urlrequest, 'urlopen', return_value=reply), \
                patch.object(assistant.config, 'AI_API_KEY', 'unit-key.123456'):
            events = assistant._stream_llm('问题', [], {}, {'id': 'a', 'label': 'a'}, cfg, timings)
            self.assertEqual(next(events), {'type': 'delta', 'text': '答案 '})
            self.assertLess(reply.tell(), len(payload))
            self.assertEqual(''.join(event['text'] for event in events), '***，以及 ***，接着生成正文。')
        self.assertIn('first_text_seconds', timings)
        self.assertIn('model_seconds', timings)

    def test_compact_keeps_valid_json_and_declares_list_limits(self):
        result = {'name': 'dashboard_summary', 'params': {}, 'ok': True,
                  'summary': 'x' * 10000, 'data': {'trend': list(range(100)), 'totals': {'quantity': 42}}}
        payload = json.loads(assistant._llm_compact([result]))[0]
        self.assertEqual(payload['data']['totals']['quantity'], 42)
        self.assertEqual(len(payload['data']['trend']), 62)
        self.assertEqual(payload['omitted']['trend'], {'total': 100, 'included': 62})
        self.assertEqual(len(payload['summary']), 10000)

    def test_customer_identifiers_not_sent_to_model(self):
        item = {'name': 'orders_query', 'params': {}, 'ok': True,
                'summary': '订单 123，客户 456，国家 UK。',
                'data': {'rows': [{'customer_id': '456', 'quantity': 2}]}}
        payload = json.loads(assistant._llm_compact([item]))[0]
        self.assertNotIn('customer_id', payload['data']['rows'][0])
        self.assertNotIn('456', payload['summary'])

    @patch('app.services.assistant._parse_country', return_value=None)
    def test_overview_uses_current_batch_end_and_filters(self, _country):
        batch = {'manifest': {'date_min': '2011-01-01', 'date_max': '2011-12-09'}}
        plan = assistant.plan_question('最近30天经营情况', {'product': '23084', 'country': 'France'}, batch)
        self.assertEqual(plan['intent'], 'overview')
        self.assertEqual(plan['tools'][0]['params'], {'start': '2011-11-10', 'end': '2011-12-09',
                                                      'product': '23084', 'country': 'France'})

    @patch('app.services.assistant._inventory_service', return_value=(None, 'unavailable'))
    @patch('app.services.assistant.active_batch', return_value={'id': 'simulation_20111101'})
    def test_simulation_never_falls_back_to_other_batch_inventory(self, _batch, _service):
        result = assistant.inventory_overview(scenario_id='demo-original')
        self.assertIsNone(result['scenario'])
        self.assertIn('不能读取其他批次', result['error'])
        with self.assertRaises(ValueError):
            assistant._tool_inventory({'stock_code': '23084'}, {'id': 'simulation_20111101'})

    @patch('app.services.assistant.inventory_overview', return_value={'scenario': None, 'items': [], 'candidates': []})
    @patch('app.services.jobs.latest_jobs', return_value={'jobs': {}})
    def test_missing_saved_results_are_explicit(self, _jobs, _inventory):
        result = assistant._overview_results({'id': 'current'}, {})
        self.assertEqual([x['name'] for x in result],
                         ['saved_forecast', 'saved_backtest', 'simulated_inventory_overview'])
        self.assertTrue(all(not x['ok'] for x in result))
        self.assertTrue(result[-1]['data']['simulated'])


if __name__ == '__main__':
    unittest.main()
