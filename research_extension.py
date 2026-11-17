"""Opt-in web research endpoint for Agent Workshop.

Uses the Tavily basic search API. Requires a private server-side TAVILY_API_KEY.
No arbitrary-url fetching, bypassing site restrictions, private browsing or tracking.
"""
from datetime import datetime, timezone
import json
import os
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from knowledge import PAGE_GUIDES

_lock = threading.Lock()
_quota = {'day': '', 'all': 0, 'users': {}}


def _sources(payload):
    out = []
    for item in payload.get('results', [])[:6]:
        if not isinstance(item, dict):
            continue
        raw_url = str(item.get('url') or '').strip()
        try:
            url = urlsplit(raw_url)
        except ValueError:
            continue
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            continue
        title = str(item.get('title') or url.hostname).strip()[:180]
        snippet = str(item.get('content') or '').strip()[:650]
        published = str(item.get('published_date') or '').strip()[:35]
        out.append({'title': title, 'url': raw_url[:1800], 'snippet': snippet, 'published_date': published})
    return out[:5]


def _allowed(user_role):
    permitted = {s.strip() for s in os.getenv('WEB_SEARCH_ROLES', 'admin').split(',')}
    return user_role in permitted


def _limit(name, default, maximum):
    try:
        return min(max(int(os.getenv(name, str(default))), 1), maximum)
    except ValueError:
        return default


def _take_quota(user_id, APIError):
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    daily = _limit('WEB_SEARCH_DAILY_LIMIT', 20, 100)
    total = _limit('WEB_SEARCH_GLOBAL_DAILY_LIMIT', 80, 400)
    with _lock:
        if _quota['day'] != day:
            _quota.update({'day': day, 'all': 0, 'users': {}})
        n = _quota['users'].get(user_id, 0)
        if n >= daily or _quota['all'] >= total:
            raise APIError(429, 'Daily web research limit reached; try again tomorrow.')
        _quota['users'][user_id] = n + 1
        _quota['all'] += 1


def search_tavily(query, api_key, APIError):
    payload = json.dumps({
        'query': query, 'search_depth': 'basic', 'topic': 'general',
        'max_results': 5, 'include_answer': False,
        'include_raw_content': False, 'include_images': False,
    }).encode('utf-8')
    request = urllib.request.Request('https://api.tavily.com/search', data=payload,
        headers={'Authorization': 'Bearer ' + api_key,
                 'Content-Type': 'application/json',
                 'User-Agent': 'AgentWorkshopResearch/1.0'}, method='POST')
    try:
        with urllib.request.urlopen(request, timeout=14) as response:
            # Restrict response size, and reject oversize rather than parse huge content.
            raw = response.read(250_001)
        if len(raw) > 250_000:
            raise APIError(502, 'Search provider returned too much data.')
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result.get('results', []), list):
            raise ValueError('Unexpected provider response')
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise APIError(503, 'Web search provider credentials need attention.') from None
        if exc.code in (402, 429):
            raise APIError(429, 'Search provider free allowance is unavailable or exhausted.') from None
        raise APIError(502, 'Web search provider is temporarily unavailable.') from None
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        raise APIError(502, 'Unable to retrieve live search results right now.') from None
    return _sources(result)


def install(Handler, APIError):
    if getattr(Handler, '_research_installed', False):
        return
    original_post_api = Handler.post_api
    original_get_api = Handler.get_api

    def get_api_with_research(self, con, path):
        if path != '/api/research/status':
            return original_get_api(self, con, path)
        user = self.require(con)
        allowed = _allowed(user['role'])
        configured = bool(os.getenv('TAVILY_API_KEY', '').strip())
        return self.json_reply({
            'ok': True, 'provider': 'Tavily', 'allowed': allowed,
            'configured': configured if allowed else False,
            'ready': allowed and configured,
            'mode': 'opt-in', 'version': 'page-research-20261117',
            'notice': ('Web research is not enabled for your role.' if not allowed else
                       'Search is configured. Click Search the live web to check the provider.' if configured else
                       'Live web search needs TAVILY_API_KEY in the Render environment. The application build is working.'),
        })

    def post_api_with_research(self, con, path, data):
        if path != '/api/research/web':
            return original_post_api(self, con, path, data)
        user = self.require(con)
        if not _allowed(user['role']):
            raise APIError(403, 'Web research is not enabled for your role. Admin only by default during staging.')
        page = data.get('page', 'overview')
        if not isinstance(page, str) or page not in PAGE_GUIDES:
            raise APIError(400, 'Unknown workspace page')
        if page in ('agents', 'workshop') and user['role'] != 'admin':
            raise APIError(403, 'Admin only')
        if page == 'outreach' and user['role'] == 'creator':
            raise APIError(403, 'Company only')
        query = data.get('query', '')
        if not isinstance(query, str) or not (3 <= len(query.strip()) <= 250):
            raise APIError(400, 'Enter a public research question of 3–250 characters.')
        query = query.strip()
        # Do not transmit raw emails or contact details to an external search provider.
        import re
        if re.search(r'\b[^\s@]+@[^\s@]+\.[A-Za-z]{2,}\b', query):
            raise APIError(400, 'Remove private email addresses from web queries.')
        api_key = os.getenv('TAVILY_API_KEY', '').strip()
        if not api_key:
            raise APIError(503, 'Live web search is not connected yet. Add TAVILY_API_KEY in the Render environment.')
        _take_quota(user['id'], APIError)
        results = search_tavily(query, api_key, APIError)
        return self.json_reply({
            'ok': True, 'query': query, 'provider': 'Tavily', 'page': page,
            'mode': 'live-web', 'sources': results,
            'notice': 'Public search snippets are untrusted evidence, not verified facts or automatically approved leads.'
        })

    Handler.post_api = post_api_with_research
    Handler.get_api = get_api_with_research
    Handler._research_installed = True
