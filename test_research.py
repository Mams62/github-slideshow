"""Release regression checks; no external API calls or real credentials."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import install_research
import research_extension as research

ROOT = Path(__file__).resolve().parent


class APIError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        research._quota.update({'day': '', 'all': 0, 'users': {}})

        class Handler:
            def __init__(self, role='admin'):
                self.role = role

            def require(self, con):
                if self.role is None:
                    raise APIError(401, 'Please sign in')
                return {'id': 1, 'role': self.role}

            def json_reply(self, data):
                return data

            def post_api(self, con, path, data):
                return 'original-post'

            def get_api(self, con, path):
                return 'original-get'

        research.install(Handler, APIError)
        self.handler = Handler

    def tearDown(self):
        self.environment.stop()

    def assert_status(self, code, function):
        with self.assertRaises(APIError) as result:
            function()
        self.assertEqual(result.exception.status, code)

    def test_authentication_required(self):
        h = self.handler(None)
        self.assert_status(401, lambda: h.get_api(None, '/api/research/status'))
        self.assert_status(401, lambda: h.post_api(None, '/api/research/web', {'query': 'public research'}))

    def test_status_never_contacts_provider_or_uses_quota(self):
        with patch.object(research, 'search_tavily') as provider:
            missing = self.handler().get_api(None, '/api/research/status')
            self.assertFalse(missing['ready'])
            self.assertIn('TAVILY_API_KEY', missing['notice'])
            os.environ['TAVILY_API_KEY'] = 'local-test-value'
            configured = self.handler().get_api(None, '/api/research/status')
            self.assertTrue(configured['ready'])
            self.assertNotIn('local-test-value', str(configured))
            provider.assert_not_called()
            self.assertEqual(research._quota['all'], 0)

    def test_default_role_restrictions(self):
        os.environ['TAVILY_API_KEY'] = 'local-test-value'
        for role in ['brand', 'creator']:
            h = self.handler(role)
            self.assertFalse(h.get_api(None, '/api/research/status')['allowed'])
            self.assert_status(403, lambda: h.post_api(None, '/api/research/web', {'query': 'public research'}))

    def test_missing_key_does_not_call_provider(self):
        with patch.object(research, 'search_tavily') as provider:
            self.assert_status(503, lambda: self.handler().post_api(None, '/api/research/web', {'query': 'public research'}))
            provider.assert_not_called()

    def test_page_and_query_validation(self):
        h = self.handler()
        for data in [{'query': 'a'}, {'query': 'x' * 251}, {'query': 'private@example.test'},
                     {'query': 'public research', 'page': 'unknown'}, {'query': 'public research', 'page': []}]:
            self.assert_status(400, lambda: h.post_api(None, '/api/research/web', data))

    def test_every_page_search_retains_context(self):
        os.environ['TAVILY_API_KEY'] = 'local-test-value'
        sample = [{'title': 'Public reference', 'url': 'https://example.org', 'snippet': 'Evidence', 'published_date': ''}]
        with patch.object(research, 'search_tavily', return_value=sample) as provider:
            for page in research.PAGE_GUIDES:
                result = self.handler().post_api(None, '/api/research/web', {'query': 'public research', 'page': page})
                self.assertEqual(result['page'], page)
                self.assertEqual(result['sources'], sample)
                self.assertEqual(result['mode'], 'live-web')
            self.assertEqual(provider.call_count, len(research.PAGE_GUIDES))

    def test_configured_roles_keep_page_boundaries(self):
        os.environ['WEB_SEARCH_ROLES'] = 'admin,brand,creator'
        os.environ['TAVILY_API_KEY'] = 'local-test-value'
        for role, page in [('brand', 'agents'), ('brand', 'workshop'), ('creator', 'agents'), ('creator', 'outreach')]:
            self.assert_status(403, lambda: self.handler(role).post_api(None, '/api/research/web', {'query': 'public research', 'page': page}))

    def test_quota_enforced_and_invalid_configuration_safe(self):
        os.environ['WEB_SEARCH_DAILY_LIMIT'] = '1'
        research._take_quota(1, APIError)
        self.assert_status(429, lambda: research._take_quota(1, APIError))
        os.environ['WEB_SEARCH_DAILY_LIMIT'] = 'invalid'
        research._take_quota(2, APIError)
        self.assertEqual(research._limit('WEB_SEARCH_DAILY_LIMIT', 20, 100), 20)

    def test_unrelated_routes_and_repeat_install(self):
        before = self.handler.post_api
        research.install(self.handler, APIError)
        self.assertIs(self.handler.post_api, before)
        self.assertEqual(self.handler().get_api(None, '/api/health'), 'original-get')
        self.assertEqual(self.handler().post_api(None, '/api/ai/assist', {}), 'original-post')

    def test_sources_reject_unsafe_urls(self):
        result = research._sources({'results': [{'url': 'javascript:alert(1)'}, {'url': 'https://user:secret@example.org'},
                    {'url': 'https://example.org/article', 'title': 'A', 'content': 'x' * 1000}]})
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]['snippet']), 650)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ['server.py', 'knowledge.py', 'research_extension.py', 'web/index.html', 'web/app.js', 'web/web_search_ui.js', 'web/sw.js']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def tearDown(self):
        self.temp.cleanup()

    def test_install_is_idempotent_and_updates_pwa(self):
        install_research.install(self.root)
        before = {name: (self.root / name).read_bytes() for name in ['server.py', 'web/index.html', 'web/sw.js']}
        install_research.install(self.root)
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in before})
        self.assertEqual((self.root / 'server.py').read_text().count('install_research(Handler, APIError)'), 1)
        self.assertEqual((self.root / 'web/index.html').read_text().count('src="/web_search_ui.js"'), 1)
        self.assertIn('shell-v4-research', (self.root / 'web/sw.js').read_text())

    def test_missing_build_input_fails_before_modifying_source(self):
        server = self.root / 'server.py'
        before = server.read_bytes()
        (self.root / 'web/web_search_ui.js').unlink()
        with self.assertRaisesRegex(SystemExit, 'Research build inputs missing'):
            install_research.install(self.root)
        self.assertEqual(server.read_bytes(), before)

    def test_missing_ai_hook_fails_build(self):
        page = self.root / 'web/index.html'
        page.write_text(page.read_text().replace('id="ai-form"', 'id="renamed-form"'))
        with self.assertRaisesRegex(SystemExit, 'Missing page AI control'):
            install_research.install(self.root)


if __name__ == '__main__':
    unittest.main()
