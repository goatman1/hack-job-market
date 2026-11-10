from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from linkedin_mcp_server.scraping.extractor import LinkedInExtractor

from job_market.collector import LinkedInCollector, infer_author_position, validate_post_url
from job_market.store import post_identity


def test_search_filters_and_identity():
    url = LinkedInExtractor._build_post_search_url('hiring quant', date_posted='past_week', sort_by='relevance', page=2)
    assert 'past-week' in url and 'relevance' in url and 'page=2' in url
    assert post_identity({'url': 'https://www.linkedin.com/posts/example-activity-1234567890123456789-x'}) == post_identity({'url': 'https://www.linkedin.com/feed/update/urn:li:activity:1234567890123456789/'})


async def test_reject_non_linkedin_evidence(tmp_path):
    collector = LinkedInCollector(tmp_path)
    async def fake_start():
        return object()
    collector.start = fake_start
    with pytest.raises(ValueError):
        await collector.author_evidence('https://example.com/profile')


@pytest.mark.parametrize('url', [
    '', 'https://example.com/posts/fake', 'https://linkedin.com.evil.test/posts/fake',
    'https://www.linkedin.com/in/person', 'http://www.linkedin.com/posts/fake',
])
def test_validate_post_url_rejects_non_permalink(url):
    with pytest.raises(ValueError):
        validate_post_url(url)


def test_header_cta_is_not_poster_position():
    assert infer_author_position([
        'Pulkit Agrawal', '3rd+', 'Associate Professor at MIT & Co-Founder of Eka Robotics',
        'Visit my website', 'Follow'], 'Pulkit Agrawal') == 'Associate Professor at MIT & Co-Founder of Eka Robotics'
    assert infer_author_position(['A Person', '2nd', 'Follow'], 'A Person') == ''


def fixture_post(index, direct=False):
    return {'container_key': f'card-{index}', 'dom_key': str(index),
            'url': f'https://www.linkedin.com/posts/post-{index}' if direct else '',
            'content': f'Hiring role {index}', 'author_name': f'Author {index}',
            'author_url': f'https://www.linkedin.com/in/author-{index}',
            'author_position': 'Engineering Manager', 'posted_date': '1d', 'posted_date_raw': '1d'}


def fake_collection_page(posts):
    root = MagicMock()
    root.first = root
    expand = MagicMock()
    expand.first = expand
    expand.count = AsyncMock(return_value=0)
    expand.evaluate = AsyncMock()
    root.locator.return_value = expand
    root.evaluate = AsyncMock(return_value='')
    body = SimpleNamespace(inner_text=AsyncMock(return_value='Search results'))
    page = MagicMock()
    page.url = ''
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.mouse.wheel = AsyncMock()
    page.context.grant_permissions = AsyncMock()
    page.locator.side_effect = lambda selector: body if selector == 'body' else root

    async def evaluate(script, **kwargs):
        if 'const candidates' in script:
            return [dict(post) for post in posts]
        return None

    page.evaluate = AsyncMock(side_effect=evaluate)
    return page


async def test_direct_posts_above_initial_page_without_clipboard(tmp_path):
    collector = LinkedInCollector(tmp_path)
    page = fake_collection_page([fixture_post(i, direct=True) for i in range(25)])
    collector.start = AsyncMock(return_value=page)
    collector.barrier = AsyncMock(return_value=None)
    result = await collector.collect_page('hiring', 'relevance', 'past_week')
    assert len(result['posts']) == 25
    assert result['observed_count'] == 25
    assert result['next_scroll'] == 1
    assert not result['incomplete']
    page.context.grant_permissions.assert_not_awaited()


async def test_detached_expand_button_keeps_post_and_reports_partial_content(tmp_path):
    collector = LinkedInCollector(tmp_path)
    page = fake_collection_page([fixture_post(1, direct=True)])
    root = page.locator('[componentkey="card-1"]').first
    root.locator.return_value.count.return_value = 1
    root.locator.return_value.evaluate.side_effect = TimeoutError('Detached expandable button')
    root.evaluate.side_effect = TimeoutError('Card re-rendered')
    collector.start = AsyncMock(return_value=page)
    collector.barrier = AsyncMock(return_value=None)
    result = await collector.collect_page('hiring', 'relevance', 'past_week')
    assert len(result['posts']) == 1
    assert result['posts'][0]['content'] == 'Hiring role 1'
    assert result['posts'][0]['content_truncated']
    assert [w['phase'] for w in result['warnings']] == ['expand', 'read_expanded_text']
    assert result['next_scroll'] == 1
    root.locator.return_value.evaluate.assert_awaited_once_with('(el) => el.click()', timeout=1000)
    assert root.evaluate.await_args.kwargs['timeout'] == 1000


async def test_copy_budget_checkpoints_then_uses_cached_links(tmp_path):
    collector = LinkedInCollector(tmp_path)
    page = fake_collection_page([fixture_post(i) for i in range(6)])
    collector.start = AsyncMock(return_value=page)
    collector.barrier = AsyncMock(return_value=None)
    collector._copy_post_link = AsyncMock(side_effect=[
        f'https://www.linkedin.com/posts/post-{i}' for i in range(6)])
    result = await collector.collect_page('hiring', 'relevance', 'past_week')
    assert len(result['posts']) == 5
    assert result['incomplete'] and result['next_scroll'] == 0
    assert collector._copy_post_link.await_count == 5
    assert (tmp_path / 'post-links.json').exists()
    result = await collector.collect_page('hiring', 'relevance', 'past_week')
    assert len(result['posts']) == 6
    assert not result['incomplete'] and result['next_scroll'] == 1
    assert collector._copy_post_link.await_count == 6


async def test_menu_failure_preserves_partial_batch_and_cursor(tmp_path):
    collector = LinkedInCollector(tmp_path)
    page = fake_collection_page([fixture_post(1), fixture_post(2)])
    collector.start = AsyncMock(return_value=page)
    collector.barrier = AsyncMock(return_value=None)
    collector._copy_post_link = AsyncMock(side_effect=[
        'https://www.linkedin.com/posts/post-1', ValueError('Menu detached')])
    result = await collector.collect_page('hiring', 'relevance', 'past_week', scroll=2)
    assert len(result['posts']) == 1
    assert result['next_scroll'] == 2
    assert result['incomplete']
    assert result['warnings'][0]['container_key'] == 'card-2'
    assert 'blocked' not in result
    assert LinkedInCollector(tmp_path).links['card-1'].endswith('post-1')


async def test_mid_batch_challenge_preserves_prior_cards(tmp_path):
    collector = LinkedInCollector(tmp_path)
    page = fake_collection_page([fixture_post(1), fixture_post(2)])
    collector.start = AsyncMock(return_value=page)
    collector.barrier = AsyncMock(side_effect=[None, 'manual_login_required'])
    collector._copy_post_link = AsyncMock(side_effect=[
        'https://www.linkedin.com/posts/post-1', ValueError('Challenge')])
    result = await collector.collect_page('hiring', 'relevance', 'past_week')
    assert len(result['posts']) == 1
    assert result['stop_after_batch'] == 'manual_login_required'


def fake_copy_page(copied_value):
    state = {'captured': 'https://www.linkedin.com/posts/stale-prior-post', 'resets': 0}
    menu = MagicMock()
    menu.first = menu
    menu.scroll_into_view_if_needed = AsyncMock()
    menu.evaluate = AsyncMock()
    root = MagicMock()
    root.locator.return_value = menu
    copy = MagicMock()
    copy.first = copy
    copy.wait_for = AsyncMock()

    async def click(**kwargs):
        if copied_value:
            state['captured'] = copied_value

    copy.click = AsyncMock(side_effect=click)
    page = MagicMock()
    page.keyboard.press = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.get_by_text.return_value = copy

    async def evaluate(script, **kwargs):
        assert kwargs['isolated_context'] is False
        if script == "window.__jobPostLink = ''":
            state['captured'] = ''
            state['resets'] += 1
        else:
            return state['captured']

    page.evaluate = AsyncMock(side_effect=evaluate)
    page.context.request.get = AsyncMock(return_value=SimpleNamespace(
        url='https://www.linkedin.com/posts/resolved-post'))
    return page, root, state


async def test_stale_clipboard_never_reuses_prior_post_url(tmp_path):
    page, root, state = fake_copy_page(None)
    collector = LinkedInCollector(tmp_path)
    with pytest.raises(ValueError, match='two attempts'):
        await collector._copy_post_link(page, root)
    assert state['resets'] == 2
    page.context.request.get.assert_not_awaited()


async def test_copy_resolves_linkedin_short_url(tmp_path):
    page, root, state = fake_copy_page('https://lnkd.in/abc')
    collector = LinkedInCollector(tmp_path)
    assert await collector._copy_post_link(page, root) == 'https://www.linkedin.com/posts/resolved-post'
    assert state['resets'] == 1
    page.context.request.get.assert_awaited_once_with('https://lnkd.in/abc', timeout=10000)
