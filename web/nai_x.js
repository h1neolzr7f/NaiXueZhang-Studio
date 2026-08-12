(function(){
  function safeObj(val){
    if(!val) return {};
    if(typeof val==='object') return val;
    try{ return JSON.parse(String(val)); }catch{ return {}; }
  }
  function coerceInt(x){
    if(typeof x==='number') return Math.floor(x);
    if(typeof x==='string'){ const s=x.trim(); if(!s) return null; try{ return parseInt(s,10); }catch{ try{ return parseInt(parseFloat(s)); }catch{ return null; } } }
    return null;
  }
  function coerceFloat(x){
    if(typeof x==='number') return x;
    if(typeof x==='string'){ const s=x.trim(); if(!s) return null; try{ return parseFloat(s); }catch{ return null; } }
    return null;
  }
  function extractSteps(obj){
    const o=safeObj(obj);
    function pick(d){ if(!d||typeof d!=='object') return null; for(const k of ['steps','Steps','step','Step']){ if(k in d){ const v=coerceInt(d[k]); if(v!=null) return v; } } return null; }
    const t=pick(o); if(t!=null) return t;
    const c=o.Comment;
    if(typeof c==='object') return pick(c);
    if(typeof c==='string'){ try{ const cj=JSON.parse(c); return pick(cj); }catch{} }
    return null;
  }
  function isSeedEmpty(obj){
    const o=safeObj(obj);
    function emptySeed(d){ if(!d||typeof d!=='object') return false; if('seed' in d){ const sv=d.seed; return sv==null || (typeof sv==='string' && sv.trim()===''); } if('Seed' in d){ const sv=d.Seed; return sv==null || (typeof sv==='string' && sv.trim()===''); } return false; }
    if(emptySeed(o)) return true;
    const c=o.Comment;
    if(typeof c==='object') return emptySeed(c);
    if(typeof c==='string'){ try{ const cj=JSON.parse(c); return emptySeed(cj); }catch{} }
    return false;
  }
  function extractDimensionMax(obj){
    const o=safeObj(obj);
    function pick(d){ if(!d||typeof d!=='object') return null; const w=coerceFloat(d.width ?? d.Width); const h=coerceFloat(d.height ?? d.Height); const vals=[]; if(w!=null) vals.push(w); if(h!=null) vals.push(h); return vals.length? Math.max.apply(null,vals): null; }
    const t=pick(o); if(t!=null) return t;
    const c=o.Comment;
    if(typeof c==='object') return pick(c);
    if(typeof c==='string'){ try{ const cj=JSON.parse(c); return pick(cj); }catch{} }
    return null;
  }
  function coreNaix(json){
    const o=safeObj(json);
    const title=String(o.Title||'');
    let noise='';
    const c=o.Comment;
    if(typeof c==='object') noise=String(c.noise_schedule||'');
    return title==='AI generated image' && noise==='exponential';
  }
  function countPromptTags(json){
    const o = safeObj(json);
    const desc = String(o.Description||'');
    const tagsDesc = desc.split(',').map(s=>s.trim()).filter(Boolean);
    let extra = 0;
    let c = o.Comment;
    if (typeof c === 'string'){
      try{ c = JSON.parse(c); }catch{ c = null; }
    }
    try{
      if (c && typeof c === 'object'){
        const v4p = c.v4_prompt;
        const cap = v4p && v4p.caption;
        const ccList = cap && cap.char_captions;
        if (Array.isArray(ccList) && ccList.length){
          for (const it of ccList){
            const cs = String((it && it.char_caption) || '');
            if (cs){
              const arr = cs.split(',').map(s=>s.trim()).filter(Boolean);
              extra += arr.length;
            }
          }
        }
      }
    }catch{}
    return tagsDesc.length + extra;
  }
  function suspect(json){
    if(coreNaix(json) && countPromptTags(json) < 4) return true;
    const dim=extractDimensionMax(json); if(dim!=null && dim>5000) return true;
    const st=extractSteps(json); if(st!=null && st>51) return true;
    if(isSeedEmpty(json)) return true;
    try{
      if (window.NAI && typeof window.NAI.detect==='function'){
        const det = window.NAI.detect(json);
        const kind = det && typeof det.kind === 'string' ? det.kind : '';
        const typeLower = det && typeof det.type === 'string' ? det.type.toLowerCase() : '';
        const isInpaint = kind === 'inpaint' || typeLower.includes('inpainting');
        const isImg2Img = kind === 'img2img' || typeLower.includes('image to image') || typeLower.includes('img2img');
        if (isInpaint || isImg2Img){
          const total = countPromptTags(json);
          if (total < 12) return true;
        }
      }
    }catch{}
    return false;
  }
  function suspectWork(workData){
    if(!workData||!Array.isArray(workData.images)) return false;
    const imgs = workData.images;
    if(!imgs.length) return false;
    let suspectCount = 0;
    for(const img of imgs){
      const j = img && (typeof img.ai_json==='string' ? (function(){ try{ return JSON.parse(img.ai_json);}catch{return {}; } })() : img.ai_json);
      if(suspect(j)) suspectCount++;
    }
    const ratio = suspectCount / imgs.length;
    return ratio >= 0.8;
  }
  window.NAIX = { suspect, suspectWork };
})();
