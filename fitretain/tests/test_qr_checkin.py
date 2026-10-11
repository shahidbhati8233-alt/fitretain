import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
SERVER = BASE / 'server.py'


class QRCheckinFeatureTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix='fitretain-qr-')
        self.db_path = Path(self.tmpdir) / 'fitretain.db'
        env = os.environ.copy()
        env['FITRETAIN_DB'] = str(self.db_path)
        env['PORT'] = '8181'
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            cwd=str(BASE),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.base = 'http://127.0.0.1:8181'
        self.wait_for_server()

    def wait_for_server(self, timeout=20):
        start = time.time()
        while time.time() - start < timeout:
            try:
                with urllib.request.urlopen(f'{self.base}/api/me', timeout=2) as resp:
                    if resp.status in (200, 401):
                        return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError('Server did not start in time')

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def http_json(self, path, method='GET', payload=None, headers=None, cookie_jar=None):
        request_headers = {'Content-Type': 'application/json'}
        if headers:
            request_headers.update(headers)
        req = urllib.request.Request(
            self.base + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                body = resp.read()
                return resp.status, json.loads(body.decode() if body else '{}'), dict(resp.headers)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode() if exc.fp else ''
            try:
                return exc.code, json.loads(body or '{}'), dict(exc.headers)
            except Exception:
                return exc.code, {'error': body}, dict(exc.headers)

    def test_qr_generation_and_public_checkin_flow(self):
        register = self.http_json('/api/register', 'POST', {
            'name': 'Owner One',
            'gym': 'North Gym',
            'email': 'owner@example.com',
            'password': 'thisisaverylongpassword'
        })
        self.assertEqual(register[0], 200, register)
        owner = register[1]['user']
        self.assertIn('csrf', owner)

        cookie = register[2].get('Set-Cookie', '').split(';', 1)[0].split('=', 1)[1]
        qr = self.http_json('/api/qr', headers={'Cookie': f'fitretain_session={cookie}'})
        self.assertEqual(qr[0], 200, qr)
        self.assertIn('public_url', qr[1])
        self.assertIn('/checkin/', qr[1]['public_url'])

        with urllib.request.urlopen(f'{self.base}/checkin/{qr[1]["token"]}', timeout=8) as resp:
            page_html = resp.read().decode('utf-8')
        self.assertEqual(resp.status, 200)
        self.assertIn('North Gym', page_html)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            'INSERT INTO members(gym_id,name,phone,plan,start_date,expiry_date,fee,status,notes,checkin_code,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (1, 'Asha Singh', '919876543210', 'Monthly', '2026-10-01', '2027-10-01', 1500, 'active', '', '432198', '2026-10-11T00:00:00+00:00')
        )
        conn.commit()
        conn.close()

        valid = self.http_json('/api/public/checkin', 'POST', {
            'gym_token': qr[1]['token'],
            'phone': '919876543210',
            'checkin_code': '432198'
        })
        self.assertEqual(valid[0], 200, valid)
        self.assertIn('success', valid[1]['status'])

        duplicate = self.http_json('/api/public/checkin', 'POST', {
            'gym_token': qr[1]['token'],
            'phone': '919876543210',
            'checkin_code': '432198'
        })
        self.assertEqual(duplicate[0], 409, duplicate)

        invalid = self.http_json('/api/public/checkin', 'POST', {
            'gym_token': qr[1]['token'],
            'phone': '919876543210',
            'checkin_code': '000000'
        })
        self.assertEqual(invalid[0], 401, invalid)


if __name__ == '__main__':
    unittest.main()
