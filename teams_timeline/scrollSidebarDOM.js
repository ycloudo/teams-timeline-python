(function scrollSidebarDOM(direction) {
  const tree=document.querySelector('[role=tree]');if(!tree)return {error:'找不到聊天清單'};
  let target=tree;
  const can=e=>/auto|scroll/.test(getComputedStyle(e).overflowY)&&e.scrollHeight>e.clientHeight+2;
  while(target && target!==document.body && !can(target))target=target.parentElement;
  if(!target || target===document.body)return {moved:false,atEnd:true,noScroll:true};
  const before=target.scrollTop;
  target.scrollTop=direction==='top'?0:target.scrollTop+Math.max(200,target.clientHeight*0.8);
  target.dispatchEvent(new Event('scroll',{bubbles:true}));
  return {moved:before!==target.scrollTop,atEnd:target.scrollTop+target.clientHeight>=target.scrollHeight-4};
})
