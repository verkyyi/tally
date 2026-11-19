"""Public holds must not leak scalars or destroy the original evidence."""
import os
os.environ['TALLY_AUTOSYNC'] = '0'

import csv
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
import alerts
import core
import db
import lifecycle
import main
import publication
import public_pages as pp
import recipes


class PublicationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.patch('db.DB_PATH', tmp.name + '/test.db')
        self.patch('main._bulk', {'at': 0.0})
        self.patch('main._ranks', {'at': 0.0, 'rows': []})
        self.patch('main._week', {'at': 0.0, 'html': ''})
        self.patch('public_pages._PATHS', {'at': 0.0, 'by_id': {}, 'by_path': {}})
        self.patch('socket.socket.connect', side_effect=AssertionError('No network in tests'))
        self.patch('llm.ask_json', side_effect=AssertionError('No LLM in tests'))
        self.patch('core.post_webhook', side_effect=AssertionError('No webhook in tests'))
        self.patch('alerts.send', side_effect=AssertionError('No mail in tests'))
        db.init()
        for mid, title, value, skill in ((235, 'Tavily · headcount', 30, 'headcount'),
                                       (314, 'Profound · employee count', 120, None),
                                       (178, 'Tavily · npm downloads, weekly', 446257, 'free|npm|@tavily/core'),
                                       (400, 'Acme · headcount', 100, None),
                                       (401, 'Beta · headcount', 200, None),
                                       (402, 'Gamma · headcount', 300, None)):
            db.run('INSERT INTO metrics(id,title,question,kind,unit,source_url,skill,curated,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                   (mid,title,title,'direct','employees' if mid != 178 else 'downloads','https://example.org/report',skill,1,'2026-01-01'))
            for value0, day in ((125 if mid == 235 else 451 if mid == 314 else value + 1, '2026-10-01'), (value, '2026-11-18')):
                db.run('INSERT INTO observations(metric_id,value,as_of,evidence_url,quote,origin) VALUES(?,?,?,?,?,?)',
                       (mid,value0,day+'T00:00:00Z','https://example.org/report','Original quoted evidence','agent'))
            db.run('INSERT INTO recipes(metric_id,version,body,status,created_at,trial_json) VALUES(?,?,?,?,?,?)',
                   (mid,1,'## Source\nhttps://example.org/report\n## Read\nOriginal recipe.','live','2026-11-18','{"checks":[{"ok":false,"name":"jump"}]}'))
            core.follow(main.FEATURED,mid)
            core.follow('test@example.org',mid)
        for cid, mid, old, new in ((490,235,'125','30'),(495,314,'451','120'),(600,178,'455140','446257')):
            db.run('INSERT INTO changes(id,metric_id,before,after,kind,at) VALUES(?,?,?,?,?,?)',(cid,mid,old,new,'value_changed',db.now()))
        db.run('INSERT INTO users(id,webhook_url) VALUES(?,?)',('test@example.org','https://hooks.slack.com/test'))
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.original = self.snapshot()

    def patch(self, *a, **kw):
        p = patch(*a, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def snapshot(self):
        return {t: db.q(f'SELECT * FROM {t} WHERE metric_id IN (235,314) ORDER BY id')
                for t in ('observations','recipes','changes')}

    def get(self, url):
        r = self.client.get(url)
        self.assertEqual(r.status_code,200,(url,r.text[:300]))
        return r

    def test_detail_api_and_open_data_never_fall_back_to_linkedin(self):
        for mid in (235,314):
            with self.subTest(mid=mid):
                data = self.get(f'/api/metrics/{mid}').json()
                self.assertIsNone(data['metric']['value'])
                self.assertIsNone(data['metric']['previous'])
                self.assertIsNone(data['metric']['as_of'])
                self.assertIsNone(data['observation'])
                self.assertEqual(data['changes'],[])
                self.assertEqual(data['metric']['spark'],[])
                self.assertEqual(data['publication_status'],'pending_verification')
                data = self.get(f'/n/{mid}.json?on=2026-11-18').json()
                self.assertIsNone(data['value'])
                self.assertIsNone(data['on']['value'])
                self.assertEqual(data['history'],[])
                self.assertEqual(data['publication_label'],'Pending verification')
        self.assertEqual(self.snapshot(),self.original)

    def test_detail_html_recipe_markdown_and_images_explain_hold(self):
        for mid in (235,314):
            for suffix in ('', '/recipe', '.md', '/chart.svg'):
                r=self.get(f'/n/{mid}{suffix}')
                self.assertIn('Pending verification',r.text)
            self.assertIn('Pending verification',self.get(f'/embed/n/{mid}').text)
            state=self.get(f'/api/recipes/{mid}').json()
            self.assertFalse(state['editable'])
            self.assertIn('Original recipe.',state['body'])
        self.assertIn('Original quoted evidence',json.dumps(self.snapshot()))
        self.assertIn('Pending verification',self.get('/corrections').text)

    def test_boards_search_rankings_and_exports_keep_control(self):
        tiles=self.get('/api/board').json()['tiles']
        self.assertEqual(next(t for t in tiles if t['id']==178)['value'],446257)
        for mid in (235,314):
            self.assertIsNone(next(t for t in tiles if t['id']==mid)['value'])
            self.assertEqual(len(list(csv.reader(io.StringIO(self.get(f'/n/{mid}/history.csv').text)))),1)
        for path in ('/data/numbers.csv','/data/history.csv'):
            ids={int(r[0]) for r in list(csv.reader(io.StringIO(self.get(path).text)))[1:]}
            self.assertTrue({235,314}.isdisjoint(ids))
            self.assertIn(178,ids)
        for _,_,rows in main._rankings():
            self.assertTrue({235,314}.isdisjoint(t['id'] for t in rows))
        self.assertIn('Pending verification',self.get('/search.json?q=Tavily+headcount').text)
        data=self.get('/c/tavily.json').json()['numbers']
        self.assertIsNone(next(t for t in data if t['id']==235)['value'])
        self.assertEqual(next(t for t in data if t['id']==178)['value'],446257)
        self.assertEqual(main._mcp_number({'id':235},history=True)['history'],[])
        self.assertIsNone(main._mcp_on({'id':314,'date':'2026-11-18'})['value'])

    def test_changes_feeds_and_fresh_alerts_suppress_only_held_metrics(self):
        rows=self.get('/api/changes').json()['changes']
        self.assertEqual([r['id'] for r in rows],[600])
        self.assertEqual([r['id'] for r in main._major_changes([235,314,178])],[600])
        feed=self.get('/feed.xml').text
        self.assertNotIn('#c490',feed)
        self.assertNotIn('#c495',feed)
        self.assertIn('#c600',feed)
        self.assertEqual([r['metric_id'] for r in alerts._moves('test@example.org','2026-01-01')],[178])
        core.notify(235,'125','30','value_changed',None)
        core.notify(314,'451','120','value_changed',None)
        self.assertEqual(self.snapshot(),self.original)

    def test_paused_upkeep_preserves_raw_evidence_and_is_reversible(self):
        for mid in (235,314):
            m=core.metric(mid)
            self.assertFalse(core.run_now(mid)['found'])
            self.assertEqual(core.sync_metric(m),0)
            self.assertEqual(lifecycle.repair(m),'pending verification')
            self.assertEqual(lifecycle.resource(m,'test'),('kept pending verification',0))
            self.assertFalse(core.retire(mid,'/wanted'))
            with self.assertRaisesRegex(ValueError,'Pending verification'):
                recipes.revise({'id':'test'},m,'anything','test')
        self.assertEqual(self.snapshot(),self.original)
        with patch.dict(publication.PENDING,{},clear=True):
            self.assertEqual(main.tile(core.metric(235))['value'],30)
            self.assertTrue(core.series(235))
            self.assertFalse(publication.visible_change({'id':490,'metric_id':235}))
        self.assertIsNone(main.tile(core.metric(235))['value'])

    def test_unrelated_npm_updates_and_upstream_valuation_names_still_work(self):
        with patch('core.notify') as notify:
            self.assertTrue(core.record_direct(core.metric(178),
                {'value': 440000, 'evidence_url': 'https://www.npmjs.com/package/@tavily/core',
                 'quote': '440000 weekly downloads', 'as_of': '2026-11-19'}, 'test'))
            notify.assert_called_once()
        self.assertEqual(main.tile(core.metric(178))['value'],440000)
        self.assertEqual(self.snapshot(),self.original)
        for title in ('Acme · valuation at IPO', 'Acme · valuation at acquisition'):
            self.assertEqual(core.round_kind({'title':title}),'valuation')
        with patch('main.cards.number', return_value=b'png') as render:
            self.get('/n/235/card.png')
            self.assertEqual(render.call_args.args[1],'Pending verification')
            self.assertEqual(render.call_args.args[3],[])


if __name__ == '__main__':
    unittest.main()
