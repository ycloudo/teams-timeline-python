(function sidebarDOM() {
  const tree=document.querySelector('[role=tree]');
  if(!tree)return {error:'找不到聊天清單',items:[],controls:[],collapsed:[]};
  const nodes=[...tree.querySelectorAll('[role=treeitem]')].filter(e=>e.getClientRects().length);
  const items=[],controls=[],collapsed=[];
  for(const el of nodes){
    const value=el.getAttribute('data-fui-tree-item-value')||'';
    const type=el.getAttribute('data-item-type')||'';
    const text=(el.innerText||'').trim();
    const isChat=['chat','muted-chat'].includes(type) || /OneGQL_\w*ChatConversation\|/.test(value);
    const isChannel=type==='channel'||/OneGQL_ChannelConversation\|/.test(value);
    if(isChat||isChannel){
      const part=value.split('/').at(-1),id=part?.includes('|')?part.split('|').at(-1):null;
      const titleId=(el.getAttribute('aria-labelledby')||'').split(/\s+/).find(x=>x.startsWith('title-chat-list-item_')||x.startsWith('title-channel-list-item'));
      const title=(titleId && document.getElementById(titleId)?.textContent?.trim())||text;
      const p=el.parentElement?.closest('[role=treeitem]'),section=p?(p.getAttribute('aria-label')||(p.innerText||'').split('\n')[0]).trim():'';
      // Channels are collected only when pinned into a custom sidebar section (e.g. 我的最愛, IQ); the full Teams and channels tree is not.
      if(isChannel && !el.parentElement?.closest('[data-item-type=custom-folder]'))continue;
      items.push(isChannel?{id,label:title,title,section,kind:'channel',treeValue:value,itemType:type}:{id,label:title,section,treeValue:value,kind:/MeetingChat/.test(value)?'meeting':/GroupChat/.test(value)?'group':'personal',itemType:type});
    }else if(!el.querySelector('[role=treeitem]') && /^(查看更多|顯示更多|Show more|See more)$/i.test(text)){
      controls.push({text,value});
    }else if(el.getAttribute('aria-expanded')==='false' && el.getAttribute('aria-level')==='1' && !/TeamsAndChannels|QuickViews|Communit/i.test(value) && !/Teams 和頻道|Teams and channels|社群|快速檢視|Quick views|Communities/i.test(text)){
      collapsed.push({text,value});
    }else if(!isChannel && !el.querySelector('[role=treeitem]') && !/查看更多|檢視所有頻道|查看您所有的團隊|加入社群|提及|已追蹤|探索|草稿|已儲存|Copilot|Mentions|Saved|Drafts|Discover|See all channels|View all/i.test(text) && !value.includes('SliceConversation')){
      // Unknown entries are retained in the report, not silently dropped.
      if(type && !['slice','team','channel'].includes(type))controls.push({text,value,unknown:true,type});
    }
  }
  const filters=[...document.querySelectorAll('[aria-pressed=true]')].map(e=>e.getAttribute('aria-label')||e.textContent.trim()).filter(Boolean);
  return {items,controls,collapsed,filters};
})
