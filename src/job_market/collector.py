"""Read-only browser adapter using the pinned upstream browser and URL builder."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from linkedin_mcp_server.core.browser import BrowserManager
from linkedin_mcp_server.scraping.extractor import LinkedInExtractor


def validate_post_url(value: str) -> str:
    """Reject stale clipboard values, external redirects, and non-post links."""
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname not in {'linkedin.com', 'www.linkedin.com'}
            or not parsed.path.startswith(('/posts/', '/feed/update/'))):
        raise ValueError('Copy-link action did not return a LinkedIn post permalink')
    return value


def infer_author_position(header_lines: list[str], author_name: str) -> str:
    """Ignore profile CTA/connection labels when SDUI has no actor description."""
    excluded = {
        'visit my website', 'view my services', 'book an appointment', 'view my newsletter',
        'contact me', 'shop now', 'learn more', 'follow', 'following', 'connect', 'message',
        'view profile', 'promoted', 'edited', 'verified', 'premium',
    }
    candidates = []
    for line in header_lines:
        cleaned = line.strip(' \t\n•·')
        if not cleaned or cleaned.casefold() in excluded:
            continue
        if cleaned == author_name.strip() or re.fullmatch(r'(?:1st|2nd|3rd\+?)', cleaned):
            continue
        if re.fullmatch(r'(?:\d[\d,.]*\s+)?followers?', cleaned, re.IGNORECASE):
            continue
        candidates.append(cleaned)
    return candidates[-1] if candidates else ''


class LinkedInCollector:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.manager = None
        self.search_url = None
        self.page_location = None
        cache = self.data_dir / 'post-links.json'
        self.links = json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else {}

    async def start(self, visible=False):
        if self.manager is None:
            self.manager = BrowserManager(user_data_dir=self.data_dir / 'browser-profile', headless=not visible)
            await self.manager.start()
        return self.manager.page

    async def close(self):
        if self.manager:
            await self.manager.close()
            self.manager = None
        self.search_url = None

    async def login(self):
        page = await self.start(visible=True)
        await page.goto('https://www.linkedin.com/login')
        await page.wait_for_url('**/feed/**', timeout=180000)
        return {'authenticated': True, 'profile': str(self.data_dir / 'browser-profile')}

    async def barrier(self, page):
        url = page.url.lower()
        if any(x in url for x in ['/login', '/checkpoint', '/authwall', '/challenge']):
            return 'manual_login_required'
        text = (await page.locator('body').inner_text()).lower()
        if any(x in text for x in ['temporarily restricted', 'unusual activity', 'too many requests']):
            return 'restricted_or_rate_limited'
        return None

    async def _copy_post_link(self, browser, root):
        """Resolve one card with bounded retries and a fresh clipboard capture."""
        menu = root.locator('button[aria-label^="Open control menu for post by"]').first
        last_error = None
        for _ in range(2):
            try:
                await browser.keyboard.press('Escape')
                await menu.scroll_into_view_if_needed(timeout=3000)
                await browser.wait_for_timeout(350)
                await browser.evaluate("window.__jobPostLink = ''", isolated_context=False)
                await menu.evaluate('(el) => el.click()')
                copy = browser.get_by_text('Copy link to post', exact=True).first
                await copy.wait_for(state='visible', timeout=4000)
                await copy.click(timeout=4000)
                copied = ''
                # Patchright wait_for_function has no main-world option; poll
                # explicitly in the same world as the clipboard hook.
                for _poll in range(10):
                    copied = await browser.evaluate('window.__jobPostLink', isolated_context=False)
                    if copied:
                        break
                    await browser.wait_for_timeout(200)
                parsed = urlsplit(copied)
                if parsed.scheme == 'https' and parsed.hostname == 'lnkd.in':
                    response = await browser.context.request.get(copied, timeout=10000)
                    copied = response.url
                return validate_post_url(copied)
            except Exception as exc:  # noqa: BLE001 - retry external menu/clipboard failures
                last_error = exc
            finally:
                await browser.keyboard.press('Escape')
        raise ValueError(f'Could not copy post link after two attempts: {last_error}')

    async def collect_page(self, query, sort_by, date_posted, page=1, scroll=0):
        browser = await self.start()
        url = LinkedInExtractor._build_post_search_url(query, date_posted=date_posted,
                                                       sort_by=sort_by, page=page)
        if self.search_url != url or browser.url != self.page_location:
            await browser.goto(url, wait_until='domcontentloaded', timeout=30000)
            await browser.wait_for_timeout(2000)
            # Restore scroll position after an interrupted process or another query.
            for _ in range(scroll):
                await browser.mouse.move(400, 550)
                await browser.mouse.wheel(0, 1600)
                await browser.wait_for_timeout(500)
        self.search_url = url
        self.page_location = browser.url
        blocked = await self.barrier(browser)
        if blocked:
            return {'posts': [], 'blocked': blocked}
        posts = await browser.evaluate('''() => {
            const candidates = [...document.querySelectorAll('[role="listitem"][componentkey^="update-card"], [data-urn*="activity"], .feed-shared-update-v2, .occludable-update')];
            const roots = candidates.filter(root => !candidates.some(other => other !== root && other.contains(root)));
            return roots.map((root, index) => {
                root.dataset.jobMarketPost = String(index);
                const text = root.querySelector('.update-components-text, .feed-shared-update-v2__description, [data-testid="expandable-text-box"]');
                const beforeText = el => text && !!(el.compareDocumentPosition(text) & Node.DOCUMENT_POSITION_FOLLOWING);
                const author = root.querySelector('a.update-components-actor__meta-link') || [...root.querySelectorAll('a[href*="/in/"]')].find(beforeText);
                const link = [...root.querySelectorAll('a[href*="/feed/update/"], a[href*="/posts/"]')].find(beforeText);
                const urn = root.getAttribute('data-urn') || '';
                const menu = root.querySelector('button[aria-label^="Open control menu for post by"]');
                const lines = root.innerText.split('\\n').map(s=>s.trim()).filter(Boolean);
                const date = lines.find(s=>/^\\d+[hdwmo](\\s|$)/.test(s)) || '';
                const di = lines.indexOf(date);
                return {container_key: root.getAttribute('componentkey'),dom_key: String(index),url: link?.href || (urn ? 'https://www.linkedin.com/feed/update/' + urn : ''),
                    content: text?.innerText || '', author_name: menu?.getAttribute('aria-label').replace('Open control menu for post by ', '') || author?.innerText || '',
                    author_url: author?.href || '',
                    author_position: root.querySelector('.update-components-actor__description')?.innerText || '',
                    header_lines: di > 0 ? lines.slice(0, di) : [],
                    posted_date: date, posted_date_raw: date};
            }).filter(p => p.content);
        }''')
        resolved = []
        new_links = 0
        incomplete = False
        warnings = []
        hook_ready = False
        stop_after_batch = None
        seen = set()
        for post in posts:
            key = post.pop('container_key', None)
            dom_key = post.pop('dom_key', None)
            header_lines = post.pop('header_lines', [])
            if not post['author_position']:
                post['author_position'] = infer_author_position(header_lines, post['author_name'])
            if not post['url'] and key in self.links:
                post['url'] = self.links[key]
            if not post['url'] and new_links >= 5:
                incomplete = True
                break
            try:
                selector = ('[componentkey=' + json.dumps(key) + ']') if key else ('[data-job-market-post=' + json.dumps(dom_key) + ']')
                root = browser.locator(selector).first
                # A card re-render changes button indices. Never enumerate all
                # buttons and retain nth locators across a DOM mutation.
                try:
                    expand = root.locator('[data-testid="expandable-text-button"], button.feed-shared-inline-show-more-text__see-more-less-toggle').first
                    if await expand.count():
                        await expand.evaluate('(el) => el.click()', timeout=1000)
                        await browser.wait_for_timeout(200)
                except Exception as exc:  # noqa: BLE001 - keep initial text when card re-renders
                    post['content_truncated'] = True
                    warnings.append({'container_key': key, 'phase': 'expand', 'error': str(exc)})
                try:
                    full_text = await root.evaluate('''root => root.querySelector('.update-components-text, .feed-shared-update-v2__description, [data-testid="expandable-text-box"]')?.innerText || '' ''', timeout=1000)
                    if full_text:
                        post['content'] = full_text
                except Exception as exc:  # noqa: BLE001 - cached snapshot is preferable to losing the card
                    post['content_truncated'] = True
                    warnings.append({'container_key': key, 'phase': 'read_expanded_text', 'error': str(exc)})
                if not post['url']:
                    if not hook_ready:
                        await browser.context.grant_permissions(['clipboard-read', 'clipboard-write'])
                        await browser.evaluate('''() => { window.__jobPostLink = ''; navigator.clipboard.writeText = async text => { window.__jobPostLink = text; }; if (!window.__jobOriginalExecCommand) { window.__jobOriginalExecCommand = document.execCommand.bind(document); document.execCommand = (command, ...args) => { if (command === 'copy') { const active = document.activeElement; window.__jobPostLink = active?.value || window.getSelection()?.toString() || ''; } return window.__jobOriginalExecCommand(command, ...args); }; } }''', isolated_context=False)
                        hook_ready = True
                    post['url'] = await self._copy_post_link(browser, root)
                    new_links += 1
                    if key:
                        self.links[key] = post['url']
                        self.data_dir.mkdir(parents=True, exist_ok=True)
                        cache = self.data_dir / 'post-links.json'
                        temporary = cache.with_suffix('.tmp')
                        temporary.write_text(json.dumps(self.links), encoding='utf-8')
                        temporary.replace(cache)
                validate_post_url(post['url'])
                if post['url'] not in seen:
                    resolved.append(post)
                    seen.add(post['url'])
            except Exception as exc:  # noqa: BLE001 - return successfully copied cards before failure
                incomplete = True
                warnings.append({'container_key': key, 'error': str(exc)})
                stop_after_batch = await self.barrier(browser)
                break
        body = (await browser.locator('body').inner_text()).lower()
        if not posts and not any(x in body for x in ['no results found', 'no results for']):
            return {'posts': [], 'blocked': 'unsupported_page_markup', 'error': 'No post containers identified; inspect selectors before continuing.'}
        if not incomplete:
            await browser.mouse.move(400, 550)
            await browser.mouse.wheel(0, 1800)
            await browser.wait_for_timeout(1500)
        result = {'posts': resolved, 'exhausted': not posts, 'next_page': page,
                  'next_scroll': scroll if incomplete else scroll + 1,
                  'incomplete': incomplete, 'warnings': warnings,
                  'observed_count': len(posts)}
        if stop_after_batch:
            result['stop_after_batch'] = stop_after_batch
        return result

    async def author_evidence(self, profile_url, company_url=None):
        sources = []
        page = await self.start()
        for url in [profile_url, company_url]:
            if not url:
                continue
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or parsed.hostname not in {'www.linkedin.com', 'linkedin.com'}:
                raise ValueError('Only HTTPS LinkedIn profile/company URLs are supported')
            if not parsed.path.startswith(('/in/', '/company/')):
                raise ValueError('Expected profile/company URL')
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)
            await page.wait_for_timeout(1500)
            blocked = await self.barrier(page)
            if blocked:
                return {'blocked': blocked}
            main = page.locator('main').first
            text = await main.inner_text() if await main.count() else await page.locator('body').inner_text()
            # Profile recommendations describe other people, not this author.
            text = re.split(r'\n(?:More profiles for you|People also viewed|People you may know)\s*\n', text, maxsplit=1)[0]
            sources.append({'url': url, 'text': text})
        return {'profile_url': profile_url, 'sources': sources}
