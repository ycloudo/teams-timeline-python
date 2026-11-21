import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from playwright.async_api import async_playwright
from teams_timeline.storage import Store, parse_date, timeline_range
from teams_timeline.collector import Collector

ROOT=Path(__file__).resolve().parent.parent

class CoreTest(unittest.TestCase):
    def test_dates_and_store(self):
        self.assertEqual(parse_date('2026年11月9日 上午12:01.'),'2026-11-08T16:01:00.000Z')
        self.assertIsNone(parse_date('昨天 上午9:17'))
        r=timeline_range('2026-11-09','2026-11-15'); self.assertEqual(r['endISO'],'2026-11-15T16:00:00.000Z')
        with self.assertRaises(ValueError): timeline_range('2026-11-09T10:00:00','2026-11-10')
        s=Store(':memory:'); a=s.start('A'); b=s.start('B')
        m={'id':'1','author':'A','body':'x','timestamp':'2026-11-09T02:00:00.000Z','rawTime':'t'}
        s.save(a,'A',[m,{**m,'id':'2','timestamp':None}]); s.save(a,'A',[{**m,'body':'edited'}]); s.save(b,'B',[m])
        x=s.query([a],r,0,1); self.assertEqual(x['total'],2); self.assertEqual(x['undated'],1); self.assertEqual(x['nextOffset'],1)
        self.assertEqual(s.query([a],r,0,100,False)['messages'][0]['body'],'edited'); s.close()
    def test_mcp(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                env={**os.environ,'TEAMS_DATA_DIR':tmp,'PYTHONPATH':str(ROOT)}
                async with stdio_client(StdioServerParameters(command=sys.executable,args=['-m','teams_timeline.server'],env=env)) as (read,write):
                    async with ClientSession(read,write) as client:
                        await client.initialize(); tools=await client.list_tools(); self.assertEqual(len(tools.tools),7)
                        out=await client.call_tool('teams_job_status',{}); self.assertEqual(out.structuredContent,{'jobs':[]})
                        out=await client.call_tool('teams_collect_messages',{'from':'bad','to':'bad'}); self.assertTrue(out.isError)
        anyio.run(run)
    def test_browser_fixture(self):
        async def run():
            async with async_playwright() as p:
                browser=await p.chromium.launch(channel='chrome',headless=True); page=await browser.new_page(); s=Store(':memory:')
                with tempfile.TemporaryDirectory() as tmp:
                    c=Collector(s,Path(tmp)); c.page=page
                    await page.set_content('''<div role="tree"><div role="treeitem" data-item-type="chat" data-fui-tree-item-value="OneGQL_GroupChatConversation|1">A</div><div role="treeitem" data-item-type="chat" data-fui-tree-item-value="OneGQL_GroupChatConversation|1">A</div><div role="treeitem" data-item-type="channel">Channel</div></div><div role="log"><article data-message-id="old"><span data-tid="message-author-name">A</span><time datetime="2026-11-08T15:00:00Z"></time><div data-tid="chat-pane-message">Before</div></article></div><div contenteditable="true">draft</div>''')
                    inv=await c.discover(10,50,lambda:False); self.assertEqual(len(inv['items']),1); self.assertEqual(inv['reason'],'sidebar_exhausted')
                    result=await c.capture('A','2026-11-09','2026-11-15',1,lambda:False); self.assertEqual(result['status'],'timeline_boundary_reached')
                    self.assertEqual(s.db.execute('SELECT body FROM messages').fetchone()[0],'Before')
                    s.close()
                await browser.close()
        anyio.run(run)
if __name__=='__main__': unittest.main()

class JobTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_resume_and_single_worker(self):
        import asyncio
        from teams_timeline.jobs import Jobs
        class FakeCollector:
            log=[]
            def __init__(self): self.gate=asyncio.Event()
            async def open(self): await self.gate.wait()
            async def list(self): pass
            async def discover(self,*args): return {'items':[{'id':'1','label':'A','kind':'personal'}],'reason':'sidebar_exhausted','warnings':[]}
            async def select(self,*args): pass
            async def capture(self,chat,start,end,limit,stop,channel=False): return {'status':'timeline_boundary_reached','run':None}
            def stop(self): self.gate.set()
            async def close(self): pass
        with tempfile.TemporaryDirectory() as tmp:
            s=Store(':memory:'); c=FakeCollector(); j=Jobs(c,s,Path(tmp)); params={'from':'2026-11-09','to':'2026-11-15','maxChats':500,'maxSidebarPages':200,'maxMessagePages':500}
            first=j.start(params); id=first['jobId']
            with self.assertRaises(RuntimeError): j.start(params)
            await asyncio.sleep(0); j.cancel(id); await j.task
            self.assertEqual(j.status(id)['status'],'cancelled')
            self.assertEqual(j.resume(id,{})['status'],'queued'); await j.task
            self.assertEqual(j.status(id)['status'],'completed'); self.assertFalse(j.status(id)['completeness']['allMessagesVerified'])
            await j.close(); s.close()
