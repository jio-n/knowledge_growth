// Per-artifact correspondence. SourceAnchor's original geometry stays unchanged.
function rectOf(g) { return g?.rect; }
function point(rect,x,y) { return {x:rect[0]+(rect[2]-rect[0])*x,y:rect[1]+(rect[3]-rect[1])*y}; }
function relative(p,r) { return {x:Math.max(0,Math.min(1,(p.x-r[0])/(r[2]-r[0]))),y:Math.max(0,Math.min(1,(p.y-r[1])/(r[3]-r[1])))}; }
function unrotated(p,rotation=0) {
  if (rotation===90) return {x:p.y,y:1-p.x};
  if (rotation===180) return {x:1-p.x,y:1-p.y};
  if (rotation===270) return {x:1-p.y,y:p.x};
  return p;
}
function rotated(p,rotation=0) {
  if (rotation===90) return {x:1-p.y,y:p.x};
  if (rotation===180) return {x:1-p.x,y:1-p.y};
  if (rotation===270) return {x:p.y,y:1-p.x};
  return p;
}
function distance(p,r) { return Math.max(r[0]-p.x,0,p.x-r[2])**2+Math.max(r[1]-p.y,0,p.y-r[3])**2; }

export function mapPosition(report,position,direction) {
  if (!report?.layout_version || !position) return position; // saved fixed-bbox v5 artifacts
  const candidates=[];
  for (const unit of report.units) {
    const original=rectOf(unit.bbox);
    if (!original) continue;
    for (const placement of unit.placements || []) {
      const jp=rectOf(placement.japanese_bbox);
      const from=direction==="toJapanese" ? unit.bbox : placement.japanese_bbox;
      const fromPage=direction==="toJapanese" ? unit.page : placement.japanese_page;
      const to=direction==="toJapanese" ? placement.japanese_bbox : unit.bbox;
      const toPage=direction==="toJapanese" ? placement.japanese_page : unit.page;
      const sourcePoint=unrotated(position,from.rotation);
      const p=point(from.page_rect || from.rect,sourcePoint.x,sourcePoint.y);
      const r=rectOf(from);
      const score=Math.abs(fromPage-position.page)*1e9+distance(p,r);
      // Prefer a text unit over a large visual region when both contain center.
      candidates.push({score:score+(unit.kind==="visual" ? .01:0),unit,placement,from:r,to:rectOf(to),toFrame:to.page_rect,toRotation:to.rotation,toPage,p});
    }
  }
  candidates.sort((a,b)=>a.score-b.score);
  const best=candidates[0]; if (!best) return null;
  const fraction=relative(best.p,best.from), target=point(best.to,fraction.x,fraction.y);
  const result=rotated(relative(target,best.toFrame),best.toRotation);
  return {page:best.toPage,...result};
}

export function originalSelection(report,page,bbox) {
  const r=rectOf(bbox); if (!r) return null;
  if (!report?.layout_version) {
    const candidates=report?.units.filter(u=>u.page===page&&contains(rectOf(u.bbox),r)) || [];
    return candidates.length===1 ? {blockId:candidates[0].block_id,page:candidates[0].page,bbox:candidates[0].bbox}:null;
  }
  const text=report.units.filter(u=>u.kind!=="visual" && u.placements.some(p=>p.japanese_page===page&&contains(rectOf(p.japanese_bbox),r)));
  if (text.length===1) return {blockId:text[0].block_id,page:text[0].page,bbox:text[0].bbox};
  const assets=report.assets.filter(a=>a.japanese_page===page&&contains(rectOf(a.japanese_bbox),r));
  if (assets.length!==1) return null;
  const a=assets[0], box=rectOf(a.japanese_bbox), source=a.source_bbox;
  const f1=relative({x:r[0],y:r[1]},box),f2=relative({x:r[2],y:r[3]},box);
  const p1=point(source,f1.x,f1.y),p2=point(source,f2.x,f2.y);
  const originalRect=[p1.x,p1.y,p2.x,p2.y];
  const candidates=report.units.filter(u=>u.asset_id===a.id&&contains(rectOf(u.bbox),originalRect));
  const unit=candidates.sort((a,b)=>area(rectOf(a.bbox))-area(rectOf(b.bbox)))[0];
  return {blockId:unit?.block_id || a.original_block_ids[0],page:a.source_page};
}
function contains(a,b) { return a&&b&&a[0]-2<=b[0]&&a[1]-2<=b[1]&&a[2]+2>=b[2]&&a[3]+2>=b[3]; }
function area(r) { return (r[2]-r[0])*(r[3]-r[1]); }
