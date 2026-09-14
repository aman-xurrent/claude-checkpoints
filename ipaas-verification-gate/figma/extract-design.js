// Run through use_figma to measure the design side. Returns every text node and every frame
// with an absolute box, normalised to the frame origin.
// `hugs` says whether the text node is sized to its own glyph run. Only a hugging node has a
// width worth comparing: a fixed-width text box reports the box, not the text.
const frame = await figma.getNodeByIdAsync(FRAME_ID);
if (!frame) return { error: "frame not found: " + FRAME_ID };
const origin = frame.absoluteBoundingBox;
const relative = (node) => {
  const box = node.absoluteBoundingBox;
  if (!box) return null;
  return { x: +(box.x - origin.x).toFixed(1), y: +(box.y - origin.y).toFixed(1),
           w: +box.width.toFixed(1), h: +box.height.toFixed(1) };
};
const texts = [];
for (const node of frame.findAllWithCriteria({ types: ["TEXT"] })) {
  const box = relative(node);
  if (!box || !node.visible) continue;
  texts.push({ id: node.id, text: (node.characters || "").replace(/\s+/g, " ").trim(),
               hugs: node.textAutoResize === "WIDTH_AND_HEIGHT",
               size: node.fontSize, ...box });
}
const boxes = [];
for (const node of frame.findAllWithCriteria({ types: ["FRAME", "INSTANCE", "COMPONENT"] })) {
  const box = relative(node);
  if (!box || !node.visible) continue;
  boxes.push({ id: node.id, name: node.name, ...box });
}
return { frame: { w: origin.width, h: origin.height }, texts, boxes };
