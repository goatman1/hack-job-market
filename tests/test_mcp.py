from fastmcp import Client

from job_market.server import create_server


async def test_protocol_tools_and_validation(tmp_path):
    async with Client(create_server(tmp_path)) as client:
        tools = await client.list_tools()
        assert {t.name for t in tools} == {
            'start_run', 'collect_posts', 'get_author_evidence', 'get_analysis_batch',
            'save_assessments', 'list_leads', 'update_lead', 'sync_workbook', 'get_run_status'}
        result = await client.call_tool('list_leads', {})
        assert result.data['total'] == 0
        result = await client.call_tool('sync_workbook', {})
        assert result.data['path'].endswith('.xlsx')
