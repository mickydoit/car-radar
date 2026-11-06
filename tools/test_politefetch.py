import threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
import politefetch as pf

HITS = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        HITS[self.path] = HITS.get(self.path, 0) + 1
        n = HITS[self.path]
        code = 200
        if self.path == '/flaky' and n <= 2: code = 403
        if self.path == '/dead': code = 403
        if self.path == '/gone': code = 404
        if self.path == '/slow' and n == 1: code = 429
        self.send_response(code)
        if code == 429: self.send_header('Retry-After', '7')
        self.end_headers(); self.wfile.write(b'hello' if code == 200 else b'')


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(('127.0.0.1', 0), H); cls.base = f'http://127.0.0.1:{cls.srv.server_port}'
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()
    def setUp(self):
        HITS.clear(); self.slept = []
        self.fx = pf.Fetcher(min_interval=0, sleep=self.slept.append, jitter=0, timeout=5)

    def test_flaky_recovers_after_backoff(self):
        r = self.fx.get(self.base + '/flaky')
        self.assertTrue(r.ok); self.assertEqual(r.attempts, 3)
        self.assertEqual(self.slept, [20, 60])
        self.assertEqual(self.fx.stats['127.0.0.1:%d' % self.srv.server_port]['retried_ok'], 1)

    def test_permanent_block_gives_up_without_endless_retry(self):
        r = self.fx.get(self.base + '/dead')
        self.assertFalse(r.ok); self.assertEqual(r.code, 403); self.assertEqual(r.attempts, 4)

    def test_gone_not_retried(self):
        r = self.fx.get(self.base + '/gone'); self.assertEqual(r.attempts, 1); self.assertEqual(r.code, 404)

    def test_retry_after_honoured(self):
        r = self.fx.get(self.base + '/slow'); self.assertTrue(r.ok); self.assertEqual(self.slept, [20])  # max(20, 7)

    def test_breaker_cools_down_then_gives_up_host(self):
        fx = pf.Fetcher(min_interval=0, sleep=self.slept.append, jitter=0, breaker_after=3, max_cooldowns=1, backoffs=())
        rs = [fx.get(self.base + '/dead') for _ in range(6)]
        self.assertIn(300, self.slept)                       # one cool-down
        self.assertTrue(any(r.error and 'given up' in r.error for r in rs))
        n = HITS['/dead']; fx.get(self.base + '/dead'); self.assertEqual(HITS['/dead'], n)  # no more traffic once dead

    def test_rounds_requeue_failures(self):
        import tempfile
        d, stats = pf.run([self.base + '/flaky', self.base + '/gone'], tempfile.mkdtemp(), rounds=2, round_wait=1,
                          fetcher=self.fx, log=lambda s: None)
        self.assertTrue(d[self.base + '/flaky']['f'])
        self.assertIsNone(d[self.base + '/gone']['f'])

if __name__ == '__main__': unittest.main()
