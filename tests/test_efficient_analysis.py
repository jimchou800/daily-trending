import json
import tempfile
import unittest
from pathlib import Path

from daily_trending.analyzer import analyze_live_snapshot
from daily_trending.collector import collect_snapshot
from daily_trending.dashscope import DashScopeClient, DashScopeConfig
from daily_trending.persistence import ArtifactStore


class EfficientAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.calls = []
        self.details_failed = set()
        self.verdict = True
        self.classification_transform = lambda values: values

    def snapshot(self, count=25):
        entries = [{'repository': f'acme/p{i}', 'repository_url': f'https://github.com/acme/p{i}', 'rank': i+1, 'stars_today': 1, 'stars_total': 10} for i in range(count)]
        path = self.root / 'snapshot.json'
        path.write_text(json.dumps({'complete': True, 'entries': entries}), encoding='utf-8')
        return collect_snapshot(path, '2026-11-09', '2026-11-09T00:30:00Z')

    def transport(self, payload, _):
        prompt = payload['messages'][0]['content']
        data = json.loads(prompt.split('\n', 1)[1])
        self.calls.append((prompt, data))
        if 'classifications' in prompt.split('\n', 1)[0]:
            result = {'classifications': self.classification_transform([{'repository': e['repository'], 'is_ai': self.verdict, 'ai_relevance_evidence': 'AI workflow' if self.verdict is not None else ''} for e in data])}
        else:
            e = data[0]
            if e['repository'] in self.details_failed:
                return {'choices': [{'message': {'content': '{}'}}]}
            result = {'repository': e['repository'], 'is_ai': True, 'ai_relevance_evidence': 'AI workflow', 'project_type': 'tool', 'introduction': 'intro', 'problem_solved': 'problem', 'audience': 'developers', 'characteristics': 'simple', 'reason_has_evidence': False, 'sources': [e['repository_url']]}
        return {'choices': [{'message': {'content': json.dumps(result)}}]}

    def run_analysis(self, snapshot, **kwargs):
        client = DashScopeClient(api_key='test', transport=self.transport, config=DashScopeConfig(max_retries=0))
        return analyze_live_snapshot(snapshot, client, kwargs.pop('loader', lambda _: {'readme': 'AI workflow'}), **kwargs)

    def test_all_candidates_classified_then_repeat_filter_before_five_details(self):
        snapshot = self.snapshot()
        history = [{'status': 'published', 'report_date': '2026-11-08', 'entries': [snapshot['snapshot']['entries'][0]]}]
        result = self.run_analysis(snapshot, history=history)
        self.assertEqual([e['repository'] for e in result['analyses']], ['acme/p1','acme/p2','acme/p3','acme/p4','acme/p5'])
        self.assertEqual(len(self.calls), 6)
        self.assertEqual(len(self.calls[0][1]), 25)

    def test_failed_detail_continues_to_sixth_eligible_without_reordering(self):
        self.details_failed = {'acme/p0'}
        result = self.run_analysis(self.snapshot(8))
        self.assertEqual([e['rank'] for e in result['analyses']], [2,3,4,5,6])
        self.assertEqual(len(self.calls), 7)
        self.assertEqual(result['stage']['status'], 'succeeded')
        self.assertTrue(any('acme/p0' in error for error in result['stage']['errors']))

    def test_unknown_is_excluded_but_incomplete_classification_is_failure(self):
        self.verdict = None
        snapshot = self.snapshot(2)
        result = self.run_analysis(snapshot)
        self.assertEqual(result['stage']['status'], 'succeeded')
        self.assertEqual([d['reason'] for d in result['selection']], ['unknown_relevance', 'unknown_relevance'])
        self.classification_transform = lambda values: values[:1]
        result = self.run_analysis(snapshot)
        self.assertEqual(result['stage']['status'], 'failed')
        self.assertEqual(result['analyses'], [])
        self.assertEqual(len(self.calls), 2)

    def test_all_detail_failures_are_not_true_zero(self):
        self.details_failed = {'acme/p0', 'acme/p1'}
        result = self.run_analysis(self.snapshot(2))
        self.assertEqual(result['stage']['status'], 'failed')
        self.assertEqual(result['analyses'], [])
        self.assertEqual(len(self.calls), 3)

    def test_later_repositories_beyond_any_assumed_list_size_are_classified(self):
        self.verdict = None
        result = self.run_analysis(self.snapshot(63))
        self.assertEqual(result['stage']['status'], 'succeeded')
        self.assertEqual(len(result['classifications']), 63)
        self.assertEqual([len(data) for _, data in self.calls], [25,25,13])
        self.assertEqual(result['classifications'][-1]['repository'], 'acme/p62')

    def test_relevance_requires_actual_source_quote_not_repository_name(self):
        self.classification_transform = lambda values: [dict(v, ai_relevance_evidence=v['repository']) for v in values]
        result = self.run_analysis(self.snapshot(2))
        self.assertEqual(result['stage']['status'], 'failed')
        self.assertEqual(result['analyses'], [])
        self.assertEqual(len(self.calls), 1)

    def test_source_and_successes_are_reused_after_restart_without_refetch(self):
        snapshot = self.snapshot(7)
        store = ArtifactStore(self.root / 'cache', snapshot['period_id'])
        self.details_failed = {'acme/p0'}
        result = self.run_analysis(snapshot, cache_store=store)
        self.assertEqual([e['rank'] for e in result['analyses']], [2,3,4,5,6])
        self.details_failed = set()
        self.calls.clear()
        result = self.run_analysis(snapshot, cache_store=store, loader=lambda _: self.fail('sources must be reused'))
        self.assertEqual([e['rank'] for e in result['analyses']], [1,2,3,4,5])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1][0]['repository'], 'acme/p0')

    def test_large_multibyte_sources_fit_both_input_byte_budgets(self):
        result = self.run_analysis(self.snapshot(), loader=lambda _: {'readme': 'AI workflow' + ('中文\\"' * 20000)})
        self.assertEqual(len(result['analyses']), 5)
        self.assertLessEqual(len(self.calls[0][0].encode('utf-8')), 8000)
        self.assertTrue(all(len(prompt.encode('utf-8')) <= 6000 for prompt, _ in self.calls[1:]))
        self.assertTrue(all(len(data[0]['readme']) < 20000 for _, data in self.calls[1:]))

    def test_source_failure_is_incomplete_even_when_other_repository_is_non_ai(self):
        self.verdict = None
        def loader(entry):
            if entry['rank'] == 1:
                raise OSError('GitHub unavailable')
            return {'readme': 'AI workflow'}
        result = self.run_analysis(self.snapshot(2), loader=loader)
        self.assertEqual(result['stage']['status'], 'failed')
        self.assertEqual(result['analyses'], [])

    def test_all_recent_duplicates_need_no_detailed_model_calls(self):
        snapshot = self.snapshot(2)
        history = [{'status': 'published', 'report_date': '2026-11-08', 'entries': snapshot['snapshot']['entries']}]
        result = self.run_analysis(snapshot, history=history)
        self.assertEqual(result['stage']['status'], 'succeeded')
        self.assertEqual(result['analyses'], [])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([d['reason'] for d in result['selection']], ['recent_duplicate','recent_duplicate'])
