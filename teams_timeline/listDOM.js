(function listDOM() {
  return [...document.querySelectorAll('[role="treeitem"], [role="tree"] [role="row"]')].filter(el=>el.getClientRects().length && !el.querySelector('[role=treeitem], [role=row]'))
    .map(el=>({label:el.getAttribute('aria-label') || el.innerText.trim(),selected:el.getAttribute('aria-selected')==='true'}))
    .filter(x=>x.label);
})
