// Paste into the page (chrome-devtools evaluate_script) to measure the built side.
// Text is measured with Range, not the element box: a Figma text node is sized to its glyph
// run, while a DOM element is sized to its layout box. An <h3> that reads 1272px wide holds
// a 182px glyph run, so comparing element boxes reports a difference that is not there.
() => {
  const texts = [];
  const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node;
  while ((node = walk.nextNode())) {
    const text = node.textContent.replace(/\s+/g, " ").trim();
    if (!text) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const box = range.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;
    texts.push({ text, tag: node.parentElement.tagName.toLowerCase(),
                 x: +box.x.toFixed(1), y: +box.y.toFixed(1),
                 w: +box.width.toFixed(1), h: +box.height.toFixed(1) });
  }
  const boxes = [];
  for (const element of document.querySelectorAll("[data-figma-node]")) {
    const box = element.getBoundingClientRect();
    boxes.push({ node: element.getAttribute("data-figma-node"),
                 x: +box.x.toFixed(1), y: +box.y.toFixed(1),
                 w: +box.width.toFixed(1), h: +box.height.toFixed(1) });
  }
  return { viewport: { w: window.innerWidth, h: window.innerHeight },
           dpr: window.devicePixelRatio, texts, boxes };
}
