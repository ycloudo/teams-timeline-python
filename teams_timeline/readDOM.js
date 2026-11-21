(function readDOM(selectors) {
  const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
  const root = [...document.querySelectorAll(selectors.root)].find(visible);
  if (!root) return {error:'找不到訊息清單；請先開啟聊天室。', messages:[]};
  let nodes = [...root.querySelectorAll(selectors.message)].filter(visible);
  // Prefer the outer message wrapper when a wrapper and its body share IDs.
  nodes = nodes.filter(el => !nodes.some(other => other !== el && other.contains(el)));
  const messages = []; let separators = 0;
  for (const el of nodes) {
    const bodyNode = el.matches(selectors.body) ? el : el.querySelector(selectors.body);
    if (!bodyNode) { if (!el.querySelector(selectors.time) && !el.querySelector(selectors.author)) separators++; continue; } // Date dividers are not messages; buttons, editor and arbitrary page text are never ingested.
    const time = el.querySelector(selectors.time);
    const author = el.querySelector(selectors.author);
    const candidates = [time?.getAttribute('datetime'), time?.getAttribute('title'), time?.getAttribute('aria-label'), time?.textContent].filter(Boolean);
    // A full date sometimes sits on a parent tooltip container.
    if (time?.parentElement?.getAttribute('title')) candidates.push(time.parentElement.getAttribute('title'));
    const body = (bodyNode.innerText || '').trim();
    if (!body) continue;
    const anchor = [...el.querySelectorAll('a[href]')].find(a => /teams\.microsoft\.com\/l\/message\//.test(a.href));
    const replyText = [...el.querySelectorAll('button,[role="button"]')].map(b => b.innerText || '').find(t => /\d+\s*(個回覆|repl)/i.test(t)) || '';
    messages.push({id:el.getAttribute('data-message-id') || el.getAttribute('data-mid') || el.id || (time?.id?.startsWith('timestamp-') ? time.id : null),
      author:(author?.innerText || '').trim(),body,timeCandidates:candidates,
      rawTime:candidates[0] || '',url:anchor?.href || '',
      replies:+(replyText.match(/(\d+)\s*(?:個回覆|repl)/i)?.[1] || 0),lastReply:replyText.split(/[:：]/).slice(1).join(':').trim()});
  }
  // Only real heading tags left of the message list: post-level role=heading nodes change while scrolling, and a thread side pane sits to the right.
  const headings = [...document.querySelectorAll('h1,h2')].filter(x=>visible(x) && !root.contains(x) && x.getBoundingClientRect().left < root.getBoundingClientRect().right).map(x=>x.innerText).filter(Boolean);
  const selected = [...document.querySelectorAll('[role="treeitem"][aria-selected="true"], [role="row"][aria-selected="true"]')].filter(visible).map(x=>x.getAttribute('aria-label') || x.innerText);
  return {messages, skippedNodeCount:nodes.length-messages.length-separators, headings:headings.slice(-8), selected};
})
