"""Contract and isolation checks against a real local HTTP server."""
import json
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

from server import make_server, clarify, APIError


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0, delay=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def get(self, path, **params):
        with urlopen(self.base + path + ('?' + urlencode(params) if params else '')) as response:
            return json.load(response)

    def payload(self, **changes):
        return {"company_id": "retail", "role": "cfo", "event_id": "rate", "view": "overview",
                "insight_id": "retail:rate:v2", "question": "Почему это важно?", "request_id": "test-1", **changes}

    def test_context_echo(self):
        ctx = {k: v for k, v in self.payload().items() if k in ("company_id", "role", "event_id", "view")}
        self.assertEqual(self.get('/api/insight', **ctx)['context'], ctx)

    def test_empty_feed(self):
        self.assertEqual(self.get('/api/feed', company_id='empty', role='cfo')['items'], [])

    def test_all_card_sources_open(self):
        for event in ['rate', 'route']:
            card = self.get('/api/insight', company_id='retail', role='cfo', event_id=event, view='overview')
            for source in card['sources']:
                with urlopen(self.base + '/sources/' + source['id']) as response:
                    self.assertIn(source['title'], response.read().decode())

    def test_view_changes_blocks(self):
        card = self.get('/api/insight', company_id='retail', role='cfo', event_id='rate', view='recommendations')
        self.assertEqual([b['kind'] for b in card['blocks']], ['open', 'bank', 'ai'])
        self.assertTrue(all([s['kind'] for s in b['sections']] == ['recommendations'] for b in card['blocks']))

    def test_company_changes_explanation(self):
        answers = [clarify(self.payload(company_id=c, insight_id=f'{c}:rate:v2'))['text'] for c in ['retail', 'energy']]
        self.assertNotEqual(*answers)

    def test_role_changes_action(self):
        answers = [clarify(self.payload(role=r, question='Что проверить?'))['text'] for r in ['ceo', 'cfo']]
        self.assertNotEqual(*answers)

    def test_insufficient_data(self):
        self.assertEqual(clarify(self.payload(question='Сколько составит эффект?'))['status'], 'no_answer')

    def test_unknown_question(self):
        result = clarify(self.payload(question='Напиши рецепт супа'))
        self.assertEqual(result['status'], 'no_answer')
        self.assertEqual(result['sources'], [])

    def test_stale_insight_rejected(self):
        with self.assertRaises(APIError) as exc:
            clarify(self.payload(company_id='energy'))
        self.assertEqual(exc.exception.status, 409)

    def test_invalid_context_rejected(self):
        with self.assertRaises(HTTPError) as exc:
            self.get('/api/feed', company_id='someone_else', role='cfo')
        self.assertEqual(exc.exception.code, 400)

    def test_no_bank_assessment_in_logistics(self):
        card = self.get('/api/insight', company_id='retail', role='cfo', event_id='route', view='overview')
        block = next(b for b in card['blocks'] if b['kind'] == 'bank')
        self.assertEqual(block['source_ids'], [])

    def test_http_clarification(self):
        payload = self.payload()
        req = Request(self.base + '/api/clarifications', json.dumps(payload).encode(),
                      headers={'Content-Type': 'application/json', 'X-Demo-Client': 'd09-d10'})
        with urlopen(req) as response:
            result = json.load(response)
        self.assertEqual(result['request_id'], payload['request_id'])
        self.assertEqual(result['insight_id'], payload['insight_id'])

    def test_cross_origin_post_rejected(self):
        req = Request(self.base + '/api/clarifications', json.dumps(self.payload()).encode(),
                      headers={'Content-Type': 'application/json', 'X-Demo-Client': 'd09-d10', 'Origin': 'https://other.example'})
        with self.assertRaises(HTTPError) as exc:
            urlopen(req)
        self.assertEqual(exc.exception.code, 403)

    def test_question_limit(self):
        with self.assertRaises(APIError):
            clarify(self.payload(question='x' * 1001))

    def test_invalid_types(self):
        for payload in [[], {**self.payload(), 'role': []}, {**self.payload(), 'extra': 'x'}]:
            with self.subTest(payload=payload), self.assertRaises(APIError):
                clarify(payload)


    def test_bootstrap_profile_and_views(self):
        result = self.get('/api/bootstrap')
        self.assertEqual(result['schema_version'], '2.0')
        self.assertEqual(result['profile']['company_id'], 'retail')
        self.assertEqual(list(result['views']), ['overview', 'consequences', 'risks', 'recommendations'])

    def test_overview_has_fact_and_all_three_analysis_sections(self):
        card = self.get('/api/insight', company_id='retail', role='cfo', event_id='rate', view='overview')
        self.assertEqual([b['kind'] for b in card['blocks']], ['fact', 'open', 'bank', 'ai'])
        for block in card['blocks'][1:]:
            self.assertEqual([s['kind'] for s in block['sections']], ['consequences', 'risks', 'recommendations'])
        self.assertIn('не является инвестиционной рекомендацией', card['disclaimer'])

    def test_tabs_preserve_all_analysis_origins_and_evidence(self):
        for company in ['retail', 'energy']:
            for event in ['rate', 'route']:
                overview = self.get('/api/insight', company_id=company, role='cfo', event_id=event, view='overview')
                for view in ['consequences', 'risks', 'recommendations']:
                    card = self.get('/api/insight', company_id=company, role='cfo', event_id=event, view=view)
                    self.assertEqual([b['kind'] for b in card['blocks']], ['open', 'bank', 'ai'])
                    for block in card['blocks']:
                        original = next(b for b in overview['blocks'] if b['kind'] == block['kind'])
                        self.assertEqual(block['sections'], [s for s in original['sections'] if s['kind'] == view])
                        for section in block['sections']:
                            self.assertTrue(set(section['source_ids']) <= {s['id'] for s in card['sources']})
                            self.assertIn('certainty', section)

    def test_missing_bank_evidence_is_explicit_in_each_tab(self):
        for view in ['overview', 'consequences', 'risks', 'recommendations']:
            card = self.get('/api/insight', company_id='retail', role='ceo', event_id='route', view=view)
            bank = next(b for b in card['blocks'] if b['kind'] == 'bank')
            self.assertTrue(all(s['certainty'] == 'unavailable' and s['source_ids'] == [] for s in bank['sections']))

    def test_clarification_available_in_all_views(self):
        for view in ['overview', 'consequences', 'risks', 'recommendations']:
            result = clarify(self.payload(view=view, question='Какие рекомендации?'))
            self.assertEqual(result['context']['view'], view)
            self.assertIn('Проверьте', result['text'])


if __name__ == '__main__':
    unittest.main()
