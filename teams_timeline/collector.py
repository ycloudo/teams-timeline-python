import os
import re
from pathlib import Path
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright
from .storage import parse_date, timeline_range, digest, compact, TAIPEI

PANE='[data-tid="right-rail-message-pane-body"] [data-tid="message-pane-list-runway"]'
WEEKDAY={'一':0,'二':1,'三':2,'四':3,'五':4,'六':5,'日':6,'天':6,'mon':0,'tue':1,'wed':2,'thu':3,'fri':4,'sat':5,'sun':6}

def reply_since(label,start_iso):
    """Whether a channel post's 'last reply' label (今天/昨天/星期三/M/D/YYYY/M/D) falls on or after the start date. Unparseable labels return True so a thread is never skipped silently."""
    now=datetime.now(TAIPEI); today=now.date(); s=(label or '').strip(); start=datetime.fromisoformat(start_iso.replace('Z','+00:00')).astimezone(TAIPEI).date()
    if re.match(r'^(今天|today)',s,re.I): day=today
    elif re.match(r'^(昨天|yesterday)',s,re.I): day=today-timedelta(days=1)
    elif m:=re.match(r'^星期([一二三四五六日天])|^(mon|tue|wed|thu|fri|sat|sun)',s,re.I): day=today-timedelta(days=(today.weekday()-WEEKDAY[(m[1] or m[2]).lower()])%7 or 7)
    elif m:=re.match(r'^(\d{4})/(\d{1,2})/(\d{1,2})',s): day=datetime(int(m[1]),int(m[2]),int(m[3])).date()
    elif m:=re.match(r'^(\d{1,2})/(\d{1,2})',s):
        try: day=datetime(today.year,int(m[1]),int(m[2])).date()
        except ValueError: return True
        if day>today: day=day.replace(year=today.year-1)
    else: return True
    return day>=start

HERE=Path(__file__).parent
SCRIPTS={name:(HERE/(name+'.js')).read_text() for name in ('readDOM','scrollDOM','listDOM','sidebarDOM','scrollSidebarDOM')}
DEFAULTS={'root':'[data-tid="message-pane-list-runway"], [data-tid="chat-pane-list"], [role="log"], [aria-label="訊息清單"], [aria-label="Message list"]','message':'[data-message-id], [data-mid], [data-tid="chat-pane-item"]','body':'[data-tid="chat-pane-message"], [data-tid="message-body"], .message-body','author':'[data-tid="message-author-name"], [data-tid="message-author"], .message-author','time':'time, [data-tid="timestamp"], [data-tid="message-timestamp"], [data-tid="message-time"]'}

class Collector:
    def __init__(self,store,profile):
        self.store,self.profile=store,profile
        self.context=self.page=self.playwright=None; self.stop_requested=False
    async def open(self):
        if self.context and self.page and not self.page.is_closed():
            await self.page.bring_to_front(); return
        if not self.playwright: self.playwright=await async_playwright().start()
        self.context=await self.playwright.chromium.launch_persistent_context(str(self.profile),channel='chrome',headless=os.getenv('TEAMS_HEADLESS')=='1',viewport={'width':1360,'height':900},locale='zh-TW',timezone_id='Asia/Taipei',accept_downloads=False)
        self.page=self.context.pages[0] if self.context.pages else await self.context.new_page()
        await self.page.goto('https://teams.microsoft.com/v2/',wait_until='domcontentloaded',timeout=60000)
        try: await self.page.get_by_role('button',name=re.compile('^(聊天|Chat)$')).click(timeout=15000)
        except Exception: pass
    async def list(self):
        if not self.page or not self.page.url.startswith(('https://teams.microsoft.com/','https://teams.cloud.microsoft/')): raise RuntimeError('請先完成 Teams 登入')
        await self.page.locator('[role=treeitem], [role=tree] [role=row]').first.wait_for(state='visible',timeout=20000)
        return await self.page.evaluate(SCRIPTS['listDOM'])
    async def discover(self,max_pages,max_chats,stop):
        found={}; warnings=[]; stable=0; previous=''; reason='page_limit'; passes=0
        await self.page.evaluate(SCRIPTS['scrollSidebarDOM'],'top')
        for passes in range(1,max_pages+1):
            if stop(): reason='cancelled'; break
            view=await self.page.evaluate(SCRIPTS['sidebarDOM'])
            if view.get('error'): raise RuntimeError(view['error'])
            for item in view['items']:
                if not item['id']: warnings.append('缺少穩定聊天 ID：'+item['label']); continue
                found.setdefault(item['id'],item)
            for x in view['controls']:
                if x.get('unknown'): warnings.append(f"未識別清單項目：{x['type']} {x['text']}")
            if any(x in ('未讀取','Unread','頻道','Channels') for x in view['filters']): warnings.append('聊天清單篩選仍啟用：'+', '.join(view['filters']))
            if len(found)>=max_chats: reason='chat_limit'; break
            expand=(view['collapsed'] or [x for x in view['controls'] if not x.get('unknown')] or [None])[0]
            if expand:
                rows=self.page.locator('[role=treeitem]'); clicked=False
                for i in range(await rows.count()):
                    row=rows.nth(i)
                    if (expand['value'] and await row.get_attribute('data-fui-tree-item-value')==expand['value']) or (not expand['value'] and (await row.inner_text()).strip()==expand['text']):
                        await row.click(); clicked=True; break
                if not clicked: warnings.append('無法展開：'+expand['text']); reason='expand_failed'; break
                await self.page.wait_for_timeout(650); after=await self.page.evaluate(SCRIPTS['sidebarDOM'])
                fingerprint=compact([[x['id'] for x in after['items']],after['collapsed'],after['controls']])
                stable=stable+1 if fingerprint==previous else 0; previous=fingerprint
                if stable>=4: warnings.append('查看更多連續未新增項目'); reason='expand_stalled'; break
                continue
            scroll=await self.page.evaluate(SCRIPTS['scrollSidebarDOM'],'down'); await self.page.wait_for_timeout(450)
            fingerprint=compact(list(found)); stable=stable+1 if fingerprint==previous else 0; previous=fingerprint
            if not scroll['moved'] and scroll['atEnd'] and stable>=2: reason='sidebar_exhausted'; break
        return {'items':list(found.values())[:max_chats], 'reason':reason,'passes':passes,'warnings':list(dict.fromkeys(warnings)),'exhaustiveForAccessibleTenant':False,'scope':'UI-enumerable personal, group, and meeting chats, plus channels pinned in custom sidebar sections (main posts and reply threads); other team channels and hidden chats are not included'}
    async def select(self,item,max_pages,stop):
        await self.page.evaluate(SCRIPTS['scrollSidebarDOM'],'top'); stale=0; previous=''
        for _ in range(max_pages):
            if stop(): raise RuntimeError('已取消')
            rows=self.page.locator('[role=treeitem][data-fui-tree-item-value]')
            for i in range(await rows.count()):
                row=rows.nth(i); value=await row.get_attribute('data-fui-tree-item-value') or ''
                if value.split('/')[-1].split('|')[-1]==item['id']:
                    await row.click()
                    await self.page.wait_for_function("label => [...document.querySelectorAll('h1,h2,[role=heading]')].some(e=>e.getClientRects().length && e.innerText.trim()===label)",arg=item.get('title') or item['label'],timeout=15000)
                    await self.page.wait_for_timeout(1000); return
            view=await self.page.evaluate(SCRIPTS['sidebarDOM']); more=next((x for x in view['controls'] if not x.get('unknown')),None)
            if more:
                rows=self.page.get_by_role('treeitem')
                for i in range(await rows.count()):
                    row=rows.nth(i)
                    if (await row.inner_text()).strip()==more['text']: await row.click(); break
            else:
                scroll=await self.page.evaluate(SCRIPTS['scrollSidebarDOM'],'down'); finger=compact([x['id'] for x in view['items']]); stale=stale+1 if finger==previous else 0; previous=finger
                if not scroll['moved'] and stale>=2: break
            await self.page.wait_for_timeout(350)
        raise RuntimeError(f"找不到聊天 ID：{item['id']} ({item['label']})")
    async def open_thread(self,url):
        u=urlparse(url)
        if u.scheme!='https' or u.hostname not in ('teams.microsoft.com','teams.cloud.microsoft') or not u.path.startswith('/l/message/'): raise ValueError('請提供 Teams 訊息連結（https://teams.cloud.microsoft/l/message/...）')
        q=parse_qs(u.query); await self.open()
        if not self.page.url.startswith(('https://teams.microsoft.com/','https://teams.cloud.microsoft/')): raise RuntimeError('請先完成 Teams 登入')
        await self.page.goto(url,wait_until='domcontentloaded',timeout=60000)
        try: await self.page.locator('[data-tid="message-pane-list-runway"]').first.wait_for(state='visible',timeout=60000)
        except Exception: raise RuntimeError('找不到討論串訊息清單；請確認已登入且有該頻道存取權。')
        await self.page.wait_for_timeout(3000)
        return {'team':(q.get('teamName') or [''])[0],'channel':(q.get('channelName') or [''])[0],'rootId':'timestamp-'+(q.get('parentMessageId') or q.get('createdTime') or [''])[0]}
    async def read_replies(self,run,chat,config,post,max_pages,stop):
        """Open one channel post's reply pane, scroll it to the top and store every message. complete=True only when the top was reached and every announced reply was seen."""
        page=self.page; seen={}; out={'id':post['id'],'replies':post['replies'],'complete':False}
        try:
            item=page.locator('[data-tid="message-pane-list-runway"]').first.locator('[data-tid="chat-pane-item"]').filter(has=page.locator(f'[id="{post["id"]}"]')).first
            await item.locator('button',has_text=re.compile(r'\d+\s*(個回覆|repl)',re.I)).first.click(timeout=10000)
            pane={**config,'root':PANE}
            await page.locator(PANE).first.wait_for(state='visible',timeout=15000); await page.wait_for_timeout(1500)
            await page.evaluate(SCRIPTS['scrollDOM'],{'selectors':pane,'direction':'bottom'}); await page.wait_for_timeout(800)
            stale=0; previous=''; top=0
            for _ in range(max_pages):
                if self.stop_requested or stop(): break
                result=await page.evaluate(SCRIPTS['readDOM'],pane)
                if result.get('error'): raise RuntimeError(result['error'])
                messages=result['messages']
                for m in messages:
                    m['timestamp']=next((v for t in m['timeCandidates'] if (v:=parse_date(t))),None)
                    seen[m.get('id') or digest(compact([m['author'],m['rawTime'],m['body']]))]=m
                self.store.save(run,chat,messages)
                finger=digest(compact([[m['id'],m['rawTime'],m['body']] for m in messages])); stale=stale+1 if finger==previous else 0; previous=finger
                if stale>=6: break
                await page.evaluate(SCRIPTS['scrollDOM'],{'selectors':pane,'direction':'up'}); await page.wait_for_timeout(850)
            # The root post is already stored from the main timeline; complete means every announced reply was seen once the pane stopped changing.
            out['seen']=len(seen); out['complete']=stale>=6 and len([k for k in seen if k!=post['id']])>=post['replies']
        except Exception as e: out['error']=str(e)
        return out
    async def capture(self,chat,start,end,max_pages,stop,thread=False,channel=False):
        r=timeline_range(start,end); config=DEFAULTS; run=self.store.start(chat); pages=0; status='page_limit'; detail=''; stale=0; previous=''; skipped=0; seen={}; expanded=set(); threads=[]; self.stop_requested=False
        try:
            initial=await self.page.evaluate(SCRIPTS['readDOM'],config)
            if initial.get('error'):
                if await self.page.locator('[data-tid="channel-pane-runway"]').count(): raise RuntimeError('此頻道使用舊式貼文版面（channel-pane-runway），回覆內嵌於貼文，尚未支援，已略過以免漏讀回覆。')
                raise RuntimeError(initial['error'])
            identity=initial['selected'] or initial['headings']
            await self.page.evaluate(SCRIPTS['scrollDOM'],{'selectors':config,'direction':'bottom'}); await self.page.wait_for_timeout(1000)
            while pages<max_pages:
                if self.stop_requested or stop(): status='stopped'; break
                result=await self.page.evaluate(SCRIPTS['readDOM'],config)
                if result.get('error'): raise RuntimeError(result['error'])
                if (result['selected'] or result['headings'])!=identity: raise RuntimeError('收集中聊天室變更，已停止以避免混入其他對話')
                pages+=1; skipped=max(skipped,result.get('skippedNodeCount',0)); messages=result['messages']
                if not messages: raise RuntimeError('未辨識到訊息；不會將空結果當作成功。')
                for m in messages:
                    m['timestamp']=next((v for t in m['timeCandidates'] if (v:=parse_date(t))),None)
                    seen[m.get('id') or digest(compact([m['author'],m['rawTime'],m['body']]))]=m
                self.store.save(run,chat,messages)
                if channel:
                    for m in messages:
                        if not (m.get('replies') and m.get('id')) or m['id'] in expanded: continue
                        if m['timestamp'] and r['startISO']<=m['timestamp']<r['endISO'] or reply_since(m.get('lastReply'),r['startISO']):
                            expanded.add(m['id']); threads.append(await self.read_replies(run,chat,config,m,max_pages,stop))
                if any(m['timestamp'] and m['timestamp']<r['startISO'] for m in messages):
                    status='boundary_needs_review' if skipped or any(not x['timestamp'] or not x['author'] for x in seen.values()) else 'timeline_boundary_reached'
                    detail='已看到期間起點以前的訊息；僅代表目前主時間軸。'; break
                finger=digest(compact([[m['id'],m['rawTime'],m['body']] for m in messages])); stale=stale+1 if finger==previous else 0; previous=finger
                if stale>=4:
                    status='thread_start_reached' if thread else 'stalled'; detail='已捲到討論串開頭' if thread else '連續 4 次捲動未取得新訊息'; break
                await self.page.evaluate(SCRIPTS['scrollDOM'],{'selectors':config,'direction':'up'}); await self.page.wait_for_timeout(850)
            if status=='page_limit': detail='已達頁數上限，可重新收集。'
        except Exception as e: status='error'; detail=str(e)
        incomplete=[t for t in threads if not t['complete']]
        if incomplete and status=='timeline_boundary_reached': status='boundary_needs_review'
        detail+=f" 累計 {len(seen)} 則；{sum(not x['timestamp'] for x in seen.values())} 則日期未辨識；單頁最多 {skipped} 個未支援節點。"
        if channel: detail+=f" 已展開 {len(threads)} 個回覆串，其中 {len(incomplete)} 個未確認完整。"
        self.store.finish(run,status,detail)
        latest=max((x['timestamp'] for x in seen.values() if x['timestamp']),default=None)
        return {'run':run,'status':status,'pages':pages,'detail':detail,'maxSkippedNodesOnPage':skipped,'latest':latest,'threads':threads}
    def stop(self): self.stop_requested=True
    async def close(self):
        self.stop()
        if self.context: await self.context.close()
        if self.playwright: await self.playwright.stop()
