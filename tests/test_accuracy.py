import copy
import json
import tempfile
import unittest
from unittest.mock import patch

import agent
import alerts
import core
import db
import evidence
import lifecycle
import recipes

URL = 'https://example.org/report'


class AccuracyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patch('db.DB_PATH', self.tmp.name + '/test.db')
        self.patch('db.now', return_value='2026-11-18T12:00:00Z')
        self.notify = self.patch('core.notify')
        self.patch('socket.socket.connect', side_effect=AssertionError('Network forbidden in tests'))
        self.patch('llm.ask_json', side_effect=AssertionError('LLM must be mocked'))
        db.init()
        mid, _ = db.run("INSERT INTO metrics(question,title,kind,unit,source_url,created_at) VALUES(?,?,?,?,?,?)", ('Acme employee count', 'Acme headcount', 'direct', 'employees', URL, db.now()))
        self.m = core.metric(mid)

    def patch(self, *args, **kwargs):
        p = patch(*args, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def result(self, value=120, quote='Acme has 120 employees.'):
        return dict(found=True, on_topic=True, value=value, quote=quote, evidence_url=URL, history=[])

    def review(self, **kw):
        return dict(entity='Acme', scope='employees', entity_matches=True, scope_matches=True, supported=True,
                    qualifier='exact', source_date=None, effective_date=None, current=True, **kw)

    def seed(self, value=125, date='2026-11-17', scope='employees'):
        db.run('INSERT INTO observations(metric_id,value,as_of,evidence_url,evidence_json) VALUES(?,?,?,?,?)',
               (self.m['id'], value, date + 'T00:00:00Z', URL, json.dumps({'scope': scope})))

    def verify(self, res, review=None, page=None):
        with patch('llm.ask_json', return_value=review if review is not None else self.review()):
            return recipes._verify(self.m, res, {URL: page or res['quote']})

    def test_tavily_old_fact_never_replaces_current(self):
        self.seed()
        res = self.result(30, 'On 2025-08-06 Acme had 30 employees.')
        r = self.review(); r.update(source_date='2025-08-06', effective_date='2025-08-06', date_quote='2025-08-06')
        checks, value, _ = self.verify(res, r)
        self.assertIsNone(value)
        self.assertFalse(core.record_direct(self.m, res, 'test'))
        self.assertEqual(core.latest_obs(self.m['id'])['value'], 125)
        self.assertFalse(db.q('SELECT * FROM changes'))
        self.notify.assert_not_called()

    def test_qualified_facts_preserved_only_in_review(self):
        for qualifier, phrase, value in [('less_than', 'fewer than 120', 120), ('at_least', '100+', 100), ('approximate', 'about 300', 300), ('range', '201-500', 201)]:
            with self.subTest(qualifier=qualifier):
                res = self.result(value, f'Acme has {phrase} employees.')
                r = self.review(); r['qualifier'] = qualifier
                self.assertIsNone(self.verify(res, r)[1])
                self.assertFalse(core.record_direct(self.m, res, 'test'))
                payload = json.loads(db.q('SELECT * FROM evidence_reviews')[-1]['payload'])
                self.assertEqual(payload['_review']['qualifier'], qualifier)
        self.assertFalse(db.q('SELECT * FROM observations'))

    def test_llm_cannot_remove_explicit_qualifier(self):
        self.assertIsNone(self.verify(self.result(120, 'Acme has fewer than 120 employees.'))[1])

    def test_unread_citation_and_wrong_quote_rejected(self):
        self.assertIsNone(self.verify(self.result(), page='Other Corp has 120 employees.')[1])

    def test_missing_or_failed_llm_is_closed(self):
        for response in [None, {}, {'supported': True}]:
            with patch('llm.ask_json', return_value=response):
                self.assertFalse(evidence.assess(self.m, self.result(), self.result()['quote']))
        with patch('llm.ask_json', side_effect=RuntimeError('offline')):
            self.assertFalse(evidence.assess(self.m, self.result(), self.result()['quote']))

    def test_mismatched_scope_and_legacy_scope_held(self):
        r = self.review(); r['scope_matches'] = False
        self.assertIsNone(self.verify(self.result(), r)[1])
        self.seed(scope=None)
        self.assertIsNone(self.verify(self.result())[1])

    def test_effective_date_and_collection_date_are_separate(self):
        res = self.result(120, 'As of 2026-10-14 Acme has 120 employees.')
        r = self.review(); r.update(source_date='2026-10-14', effective_date='2026-10-14', date_quote='2026-10-14')
        self.assertEqual(self.verify(res, r)[1], 120)
        core.record_direct(self.m, res, 'test')
        obs = core.latest_obs(self.m['id'])
        self.assertEqual(obs['as_of'], '2026-10-14T00:00:00Z')
        self.assertEqual(obs['collected_at'], db.now())
        self.assertEqual(len(db.q('SELECT * FROM observations')), 1)

    def test_invalid_future_unknown_or_invented_dates(self):
        for dates in [dict(effective_date='tomorrow'), dict(effective_date='2027-01-01'), dict(current=False), dict(effective_date='2026-10-14', date_quote='invented')]:
            r = self.review(); r.update(dates)
            self.assertIsNone(self.verify(self.result(), r)[1])

    def test_valid_comparable_change_alerts_once(self):
        self.seed()
        res = self.result()
        self.assertEqual(self.verify(res)[1], 120)
        self.assertTrue(core.record_direct(self.m, res, 'test'))
        self.assertFalse(core.record_direct(self.m, res, 'test'))
        self.assertEqual(len(db.q('SELECT * FROM changes')), 1)
        self.notify.assert_called_once()

    def test_repair_resource_and_revision_cannot_waive_jump(self):
        self.seed(451)
        res = self.result()
        run = dict(result=res, pages={URL: res['quote']}, steps=[], model='mock', usage={})
        with patch('agent.run_recipe', return_value=copy.deepcopy(run)), patch('agent.create', return_value=copy.deepcopy(run)), patch('llm.ask_json', return_value=self.review()):
            self.assertEqual(lifecycle.repair(self.m), 'failed')
            self.assertTrue(lifecycle.resource(self.m, 'official source')[0].startswith('kept'))
            report = recipes.revise({'id': 'test'}, self.m, '## Source\n' + URL + '\n## Read\nRead employees.', 'test')
            self.assertFalse(report['adopted'])
        self.assertEqual(core.latest_obs(self.m['id'])['value'], 451)
        self.assertFalse(db.q("SELECT * FROM recipes WHERE version>1 AND status='live'"))
        self.notify.assert_not_called()

    def test_publish_rechecks_failed_draft(self):
        did, _ = db.run('INSERT INTO drafts(user,question,title,status,payload) VALUES(?,?,?,?,?)', ('test', 'headcount', 'draft', 'ready', json.dumps({'checks': [{'ok': False}]})))
        with self.assertRaises(lifecycle.Refused):
            lifecycle.publish({'id': 'test'}, did)
        self.assertEqual(len(db.q('SELECT * FROM metrics')), 1)

    def test_unchanged_max_round_does_not_create_change(self):
        db.run("UPDATE metrics SET kind='count',reducer='max',unit='USD' WHERE id=?", (self.m['id'],))
        m = core.metric(self.m['id'])
        core.upsert_entries(m, [{'title':'Alpha Series A', 'url': URL+'/a', 'value':300000000}], 'test', record_changes=False)
        core.upsert_entries(m, [{'title':'Beta Series B', 'url': URL+'/b', 'value':200000000}], 'test')
        self.assertEqual(core.count_value(m['id']), 300000000)
        self.assertFalse(db.q('SELECT * FROM changes'))
        self.assertFalse(core.is_major('entity_added', '300000000', '300000000', 'count'))
        self.notify.assert_not_called()

    def test_unpaced_monitor_requires_verified_read(self):
        db.run("UPDATE metrics SET telem_monitor_id='mock', monitor_cursor=1 WHERE id=?", (self.m['id'],))
        feed = dict(changes=[dict(seq=2, kind='value_changed', after=30, before=125)], next_cursor=2, watermark=2)
        with patch('telem.monitor_changes', return_value=feed), patch('core.resolve_direct', return_value=None) as read:
            core.sync_metric(core.metric(self.m['id']))
            read.assert_called_once()
        self.assertFalse(db.q('SELECT * FROM observations'))
        self.assertFalse(db.q('SELECT * FROM changes'))

    def test_zero_and_nonfinite_fail_jump_or_validation(self):
        self.seed()
        self.assertIsNone(self.verify(self.result(0, 'Acme has 0 employees.'))[1])
        for value in [float('nan'), float('inf'), True]:
            self.assertIsNone(self.verify(self.result(value))[1])

    def test_daily_read_searches_beyond_pinned_source_and_checks_citation(self):
        new_url = 'https://official.example/about'
        res = self.result(); res['evidence_url'] = new_url
        pages = [{'url': URL, 'ok': True, 'content': 'Old report: 30 employees'},
                 {'url': new_url, 'ok': True, 'content': res['quote']}]
        with patch('telem.search', return_value=([dict(url=new_url, title='About', summary=res['quote'])], {})) as search, patch('telem.fetch', return_value=pages), patch('llm.extract_direct', return_value=res), patch('llm.ask_json', return_value=self.review()):
            found = core.resolve_direct(self.m)
            self.assertEqual(found['evidence_url'], new_url)
            search.assert_called_once()
        res['evidence_url'] = URL
        with patch('telem.search', return_value=([], {})), patch('telem.fetch', return_value=pages), patch('llm.extract_direct', return_value=res):
            self.assertIsNone(core.resolve_direct(self.m))

    def test_failed_history_check_also_blocks_adoption_and_writer(self):
        res = self.result()
        res['history'] = [dict(date='2025-01-01', value=99, url=URL, quote='99 employees')]
        checks, value, _ = self.verify(res)
        self.assertIsNone(value)
        self.assertFalse(res['_review']['accepted'])
        self.assertFalse(core.record_direct(self.m, res, 'test'))

    def test_legacy_unchanged_changes_filtered_from_email_and_feed(self):
        import public_pages
        core.follow('test', self.m['id'])
        db.run("INSERT INTO changes(metric_id,before,after,at,kind) VALUES(?,?,?,?,?)", (self.m['id'], '300000000', '300000000.0', db.now(), 'entity_added'))
        self.assertEqual(alerts._moves('test', '2026-01-01'), [])
        rows = db.q('SELECT * FROM changes')
        self.assertNotIn('<entry>', public_pages.atom(rows, 'test', '/feed.xml'))

    def test_restoration_must_pass_a_fresh_trial(self):
        cur = recipes.live(self.m)
        with patch('recipes.revise', return_value={'adopted': False}) as trial:
            with self.assertRaises(ValueError):
                recipes.revert({'id':'test'}, self.m, cur['version'])
            trial.assert_called_once()

    def test_public_tile_uses_fact_date_not_last_check(self):
        import sys
        import types
        with patch.dict(sys.modules, {'env': types.ModuleType('env')}):
            import main
        self.seed(date='2026-10-14')
        db.run('UPDATE metrics SET last_checked_at=? WHERE id=?', (db.now(), self.m['id']))
        with patch('main.is_live', return_value=True), patch('core.read_every', return_value=None):
            tile = main.tile(core.metric(self.m['id']))
        self.assertEqual(tile['as_of'], '2026-10-14T00:00:00Z')
        self.assertEqual(tile['last_checked'], db.now())

    def test_valid_publish_keeps_verified_fact_date(self):
        res = self.result(120, 'As of 2026-10-14 Acme has 120 employees.')
        r = self.review(); r.update(effective_date='2026-10-14', source_date='2026-10-14', date_quote='2026-10-14')
        checks, value, _ = self.verify(res, r)
        payload = dict(evidence=res, checks=checks, value=value, url=URL, quote=res['quote'], unit='employees', body='## Source\n' + URL + '\n## Read\nRead employees.', history=[])
        did, _ = db.run('INSERT INTO drafts(user,question,title,status,payload) VALUES(?,?,?,?,?)', ('test', self.m['question'], 'New verified metric', 'ready', json.dumps(payload)))
        published = lifecycle.publish({'id':'test'}, did)
        self.assertEqual(core.latest_obs(published['id'])['as_of'], '2026-10-14T00:00:00Z')
        self.assertEqual(db.one('SELECT status FROM drafts WHERE id=?', (did,))['status'], 'published')

    def test_text_model_cannot_claim_structured_api_exemption(self):
        res = self.result(120, 'Acme has fewer than 120 employees.')
        res['_structured'] = True
        self.assertIsNone(self.verify(res)[1])
        self.assertNotIn('_structured', res)
        self.assertFalse(core.record_direct(self.m, res, 'test'))

    def test_historical_bounds_cannot_become_exact_chart_points(self):
        point = dict(value=120, quote='As of 2026-02-24 Acme has fewer than 120 employees.', url=URL, date='2026-02-24')
        r = self.review(); r.update(qualifier='less_than', effective_date=point['date'], date_quote='2026-02-24')
        with patch('llm.ask_json', return_value=r):
            self.assertFalse(evidence.historical(self.m, point, point['quote']))
        point['quote'] = 'As of 2026-02-24 Acme has 120 employees.'
        r['qualifier'] = 'exact'
        with patch('llm.ask_json', return_value=r):
            self.assertTrue(evidence.historical(self.m, point, point['quote']))
            point['date'] = '2026-11-18'
            self.assertFalse(evidence.historical(self.m, point, point['quote']))


if __name__ == '__main__':
    unittest.main()
