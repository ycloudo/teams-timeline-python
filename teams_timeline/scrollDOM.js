(function scrollDOM({selectors,direction}) {
  const roots = [...document.querySelectorAll(selectors.root)].filter(el=>el.getClientRects().length);
  const root = roots[0]; if (!root) throw new Error('訊息清單已消失');
  // Prefer scrollable descendant, then root/ancestors. Never scroll chat sidebar.
  const scrollable = e => /auto|scroll/.test(getComputedStyle(e).overflowY) && e.scrollHeight > e.clientHeight + 2;
  let target = [...root.querySelectorAll('*')].find(scrollable) || (scrollable(root) ? root : null);
  let parent = root.parentElement;
  while (!target && parent && parent !== document.body) {if (scrollable(parent)) target=parent; parent=parent.parentElement;}
  if (!target) return {moved:false,noScroll:true,atStart:true};
  const before=target.scrollTop;
  target.scrollTop = direction==='bottom' ? target.scrollHeight : Math.max(0,target.scrollTop-target.clientHeight*0.8);
  target.dispatchEvent(new Event('scroll',{bubbles:true}));
  return {moved:before!==target.scrollTop,position:target.scrollTop};
})
