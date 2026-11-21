import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from .storage import timeline_range, iso

# Recents are time-sorted; pinned/custom sections are not. Stop after this many consecutive time-sorted chats with no activity in range.
CUTOFF_STREAK=3
RECENTS=re.compile(r'^(聊天|Chats?|最近|Recent)$',re.I)

class Jobs:
    def __init__(self,collector,store,path):
        self.collector,self.store,self.dir=collector,store,path; self.dir.mkdir(parents=True,exist_ok=True,mode=0o700); self.active=None; self.task=None; self.cancelled=False
    def path(self,id): return self.dir/(str(uuid.UUID(id))+'.json')
    def load(self,id): return json.loads(self.path(id).read_text())
    def save(self,job):
        job['updatedAt']=iso(datetime.now(timezone.utc)); path=self.path(job['id']); tmp=path.with_suffix('.json.tmp'); tmp.write_text(json.dumps(job,ensure_ascii=False,indent=2)); os.chmod(tmp,0o600); tmp.replace(path)
    def summary(self,job,offset=0,limit=50):
        outcomes=job['outcomes']; inv=job.get('inventory'); active=job['status'] in ('queued','discovering','collecting')
        return {'jobId':job['id'],'status':job['status'],'range':job['range'],'discoveredChats':len(inv['items']) if inv else 0,'processedChats':len(outcomes),'currentChat':job.get('currentChat'),'discovery':{'reason':inv['reason'],'warnings':inv['warnings']} if inv else None,'totalOutcomes':len(outcomes),'outcomes':outcomes[offset:offset+limit],'nextOutcomeOffset':offset+limit if offset+limit<len(outcomes) else None,'completeness':job['completeness'],'error':job.get('error'),'manifestPath':str(self.path(job['id'])),'dataPath':str(self.dir/(job['id']+'.jsonl')),'next':'Poll teams_job_status; call teams_read_messages for paginated evidence.' if active else None}
    def launch(self,job):
        self.active=job['id']; self.cancelled=False; self.task=asyncio.create_task(self.work(job))
    def start(self,params):
        if self.active: raise RuntimeError('已有工作執行中：'+self.active)
        r=timeline_range(params['from'],params['to']); id=str(uuid.uuid4()); job={'id':id,'input':params,'range':r,'status':'queued','createdAt':iso(datetime.now(timezone.utc)),'inventory':None,'outcomes':[],'currentChat':None,'completeness':{'allAccessibleChatsVerified':False,'allMessagesVerified':False,'scope':'personal/group/meeting chats plus channels pinned in custom sidebar sections (posts with reply threads), as discoverable in Teams UI','excluded':['channels not pinned in a custom sidebar section','hidden chats not exposed by sidebar','channel replies on posts older than from whose last reply could not be dated','attachment contents','deleted messages']}}
        self.save(job); self.launch(job); return self.summary(job)
    def resume(self,id,overrides):
        if self.active: raise RuntimeError('另一個工作執行中')
        job=self.load(id)
        if job['status']=='completed': raise RuntimeError('此工作已結束，請建立新工作以重新擷取')
        job['input'].update({k:v for k,v in overrides.items() if v is not None}); job['status']='queued'; self.save(job); self.launch(job); return self.summary(job)
    def status(self,id=None,offset=0,limit=50):
        if not id:
            jobs=[json.loads(p.read_text()) for p in self.dir.glob('*.json')]; jobs.sort(key=lambda x:x['createdAt'],reverse=True)
            return {'jobs':[{'jobId':j['id'],'status':j['status'],'from':j['input']['from'],'to':j['input']['to'],'processedChats':len(j['outcomes'])} for j in jobs[:30]]}
        j=self.load(id)
        if self.active!=id and j['status'] in ('queued','discovering','collecting'): j['status']='interrupted'; j['error']='MCP 程序已中斷；呼叫 teams_resume_collection 繼續。'
        return self.summary(j,offset,limit)
    def cancel(self,id):
        if self.active!=id: raise RuntimeError('此工作未執行中')
        self.cancelled=True; self.collector.stop(); return {'jobId':id,'status':'cancelling'}
    def read(self,id,offset=0,limit=100,include_undated=True):
        j=self.load(id); return {'jobId':id,'range':j['range'],**self.store.query([x['run'] for x in j['outcomes'] if x.get('run')],j['range'],offset,limit,include_undated),'completeness':j['completeness']}
    async def read_thread(self,url,max_pages=200,limit=200):
        if self.active: raise RuntimeError('收集中，請稍後或先取消：'+self.active)
        self.active='thread'; self.cancelled=False
        try:
            c=self.collector; meta=await c.open_thread(url)
            result=await c.capture(f"thread:{meta['team']}/{meta['channel']}/{meta['rootId']}",'1970-01-01','2100-01-01',max_pages,lambda:False,thread=True)
            r=timeline_range('1970-01-01','2100-01-01'); q=self.store.query([result['run']],r,0,limit)
            ok=result['status']=='thread_start_reached'
            return {**meta,'status':result['status'],'detail':result['detail'],'rootMessageFound':any(m['native_id']==meta['rootId'] for m in q['messages']),'completeThread':ok and not result['maxSkippedNodesOnPage'],'total':q['total'],'nextOffset':q['nextOffset'],'messages':q['messages'],'excluded':['attachment contents','deleted messages']}
        finally: self.active=None
    async def work(self,job):
        c=self.collector; stop=lambda:self.cancelled
        try:
            job['error']=None; job['status']='discovering'; self.save(job); await c.open(); await c.list(); i=job['input']
            job['inventory']=await c.discover(i['maxSidebarPages'],i['maxChats'],stop)
            if not job['inventory']['items']: raise RuntimeError('未找到可識別的聊天；請確認登入、聊天分頁與清單篩選。')
            job['status']='collecting'; self.save(job); passed={o['chatId'] for o in job['outcomes'] if o['status']=='timeline_boundary_reached'}
            streak=0; early=False
            for item in job['inventory']['items']:
                if stop(): break
                if item['id'] in passed: continue
                job['currentChat']={'id':item['id'],'label':item['label']}; self.save(job)
                try:
                    await c.select(item,i['maxSidebarPages'],stop)
                    result=await c.capture(f"{item['label']} [{item['id']}]",i['from'],i['to'],i['maxMessagePages'],stop,channel=item['kind']=='channel')
                    outcome={'chatId':item['id'],'label':item['label'],'kind':item['kind'],**result}
                except Exception as e:
                    if stop(): break
                    outcome={'chatId':item['id'],'label':item['label'],'status':'error','detail':str(e)}
                job['outcomes']=[o for o in job['outcomes'] if o['chatId']!=item['id']]+[outcome]; self.save(job)
                sorted_item=item['kind']!='channel' and (not item.get('section') or bool(RECENTS.match(item['section'])))
                if sorted_item and outcome['status']=='timeline_boundary_reached' and outcome.get('latest') and outcome['latest']<job['range']['startISO']:
                    streak+=1
                    if streak>=CUTOFF_STREAK: early=True; break
                elif sorted_item: streak=0
            job['completeness']['recencyCutoff']=early
            inv=job['inventory']; failed=any(o['status']!='timeline_boundary_reached' for o in job['outcomes']); limited=inv['reason']!='sidebar_exhausted' or bool(inv['warnings'])
            job['status']='cancelled' if stop() else 'partial' if limited or failed else 'completed'
            job['completeness']['inventoryReachedSidebarEnd']=inv['reason']=='sidebar_exhausted'; job['completeness']['discoveredTimelinesReachedBoundary']=(early or len(job['outcomes'])==len(inv['items'])) and not failed
        except Exception as e:
            job['status']='needs_login' if any(x in str(e).lower() for x in ('登入','login','sign in')) else 'error'; job['error']=str(e)
        finally:
            try:
                job['currentChat']=None; self.save(job); target=self.dir/(job['id']+'.jsonl'); tmp=target.with_suffix('.jsonl.tmp'); offset=0
                with tmp.open('w') as f:
                    os.chmod(tmp,0o600)
                    while True:
                        result=self.store.query([o['run'] for o in job['outcomes'] if o.get('run')],job['range'],offset,500)
                        for row in result['messages']: f.write(json.dumps(row,ensure_ascii=False)+'\n')
                        offset=result['nextOffset']
                        if offset is None: break
                tmp.replace(target)
            finally: self.active=None; self.task=None
    async def close(self):
        if self.active:
            self.cancelled=True; self.collector.stop()
            if self.task: await self.task
        await self.collector.close()
