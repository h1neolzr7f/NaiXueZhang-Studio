// NAI 版本与类型判定（参考 /nvmeA/gpt5_1/nai.html 的逻辑）
// 暴露为 window.NAI.detect(jsonObj) 使用
(function(){
  function safeObj(val) {
    if (!val) return {};
    if (typeof val === 'object') return val;
    try { return JSON.parse(String(val)); } catch { return {}; }
  }

  function getLang() {
    try {
      const v = String(window.GALLERY_LANG || '').trim().toLowerCase();
      if (v === 'zh' || v === 'en') return v;
    } catch {}
    try {
      const v = String(document.documentElement && document.documentElement.lang ? document.documentElement.lang : '').trim().toLowerCase();
      if (v.includes('zh')) return 'zh';
      if (v) return 'en';
    } catch {}
    return 'zh';
  }

  function formatVersion(source, usedDescriptionFallback, lang) {
    let label = 'NAI3';
    if (source.includes('NovelAI Diffusion V4.5 B5A2A797')) {
      label = (lang === 'en') ? 'NAI4.5 beta' : 'NAI4.5 测试版';
    } else if (source.includes('NovelAI Diffusion V4.5 B9F340FD') || source.includes('NovelAI Diffusion V4.5')) {
      label = 'NAI4.5';
    } else if (source.includes('NovelAI Diffusion V4 F6E18726') || source.includes('NovelAI Diffusion V4 C1CCBA86')) {
      label = (lang === 'en') ? 'NAI4 beta' : 'NAI4 测试版';
    } else if (source.includes('NovelAI Diffusion V4 4F49EC75') || source.includes('NovelAI Diffusion V4 79F47848') || source.includes('NovelAI Diffusion V4')) {
      label = 'NAI4';
    }
    if (usedDescriptionFallback) {
      label = (lang === 'en') ? 'NAI v1' : 'NAI初代';
    }
    return label;
  }

  function formatType(hasCharRef, hasVibeTransfer, request_type, lang) {
    const kind = (request_type === 'NativeInfillingRequest') ? 'inpaint' : ((request_type === 'Img2ImgRequest') ? 'img2img' : 'txt2img');
    if (lang === 'en') {
      let label = '';
      if (hasCharRef) label += '[Character Reference]';
      if (hasVibeTransfer) label += '[Vibe Transfer]';
      if (kind === 'img2img') label += '[Image to Image]';
      else if (kind === 'inpaint') label = '[Inpainting]';
      if (!label) label = '[Text to Image]';
      return { kind, label };
    }
    let label = '';
    if (hasCharRef) label = '[角色参考(Character Reference)]';
    if (hasVibeTransfer) label += '[氛围转移(Vibe Transfer)]';
    if (kind === 'img2img') {
      label += '[以图画图(Image to Image)]';
    } else if (kind === 'inpaint') {
      label = '[局部重绘(Inpainting)]';
    }
    if (!label) label = '[文生图(Text to Image)]';
    return { kind, label };
  }

  function detect(jsonDataIn) {
    try {
      const jsonData = safeObj(jsonDataIn);
      let effectiveComment;
      if (jsonData.Comment && typeof jsonData.Comment === 'object') {
        effectiveComment = jsonData.Comment;
      } else {
        effectiveComment = jsonData;
      }

      let usedDescriptionFallback = false;
      let prompt = effectiveComment.prompt || '';
      if (!prompt && jsonData.Description) {
        prompt = jsonData.Description;
        usedDescriptionFallback = true;
      }

      const source = String(jsonData.Source || effectiveComment.Source || '').trim();
      const vt = effectiveComment.reference_strength_multiple;
      const hasVibeTransfer = Array.isArray(vt) && vt.length > 0;
      const directorRef = effectiveComment.director_reference_strengths;
      const hasCharRef = Array.isArray(directorRef) && directorRef.length > 0;
      const request_type = effectiveComment.request_type || '';

      const lang = getLang();
      const naiVersion = formatVersion(source, usedDescriptionFallback, lang);
      const tp = formatType(hasCharRef, hasVibeTransfer, request_type, lang);
      return { version: naiVersion, type: tp.label, kind: tp.kind, requestType: request_type };
    } catch {
      return null;
    }
  }

  window.NAI = window.NAI || {};
  window.NAI.detect = detect;

  // 将 NAI 元数据 JSON 转换为“AI绘画机器人指令”文本（参考 /nvmeA/gpt5_1/nai.html 的 formatMetadataToText）
  function convert(jsonDataIn) {
    try {
      const jsonData = safeObj(jsonDataIn);
      // 提取有效的参数对象（NAI 常见为 Comment 嵌套；否则用顶层）
      let effectiveComment;
      if (jsonData.Comment && typeof jsonData.Comment === 'object') {
        effectiveComment = jsonData.Comment;
      } else {
        effectiveComment = jsonData;
      }

      let usedDescriptionFallback = false;
      let prompt = effectiveComment.prompt || '';
      if (!prompt && jsonData.Description) {
        prompt = jsonData.Description;
        usedDescriptionFallback = true;
      }

      const steps = effectiveComment.steps;
      const uc = effectiveComment.uc || '';
      const vt = effectiveComment.reference_strength_multiple;
      const hasVibeTransfer = Array.isArray(vt) && vt.length > 0;
      const directorRef = effectiveComment.director_reference_strengths;
      const hasCharRef = Array.isArray(directorRef) && directorRef.length > 0;
      const request_type = effectiveComment.request_type || '';
      let sampler = effectiveComment.sampler || 'k_euler';
      const seed = effectiveComment.seed ?? 'N/A';
      const height = effectiveComment.height ?? 'N/A';
      const width = effectiveComment.width ?? 'N/A';
      const scale = effectiveComment.scale;
      let cfg_rescale = effectiveComment.cfg_rescale;
      const ns = effectiveComment.noise_schedule;
      const dynamic_thresholding = effectiveComment.dynamic_thresholding;
      const skip_cfg_above_sigma = effectiveComment.skip_cfg_above_sigma;
      const prefer_brownian = effectiveComment.prefer_brownian;
      const source = String(jsonData.Source || effectiveComment.Source || '').trim();

      const lang = getLang();

      const colMapRev = { 'A': 0.1, 'B': 0.3, 'C': 0.5, 'D': 0.7, 'E': 0.9 };
      const rowMapRev = { '1': 0.1, '2': 0.3, '3': 0.5, '4': 0.7, '5': 0.9 };
      const toleranceRev = 1e-6;
      const defaultCoordsRev = { x: 0.5, y: 0.5 };
      const isClose = (a, b, tol) => Math.abs(a - b) < tol;
      function getPositionCodeRev(x, y, useCoordsFlag = true) {
        if (!useCoordsFlag) return '';
        const isDefault = isClose(x, defaultCoordsRev.x, toleranceRev) && isClose(y, defaultCoordsRev.y, toleranceRev);
        if (isDefault) return '';
        let foundCol = null, foundRow = null;
        for (const k in colMapRev) { if (isClose(x, colMapRev[k], toleranceRev)) { foundCol = k; break; } }
        for (const k in rowMapRev) { if (isClose(y, rowMapRev[k], toleranceRev)) { foundRow = k; break; } }
        return (foundCol && foundRow) ? `#${foundCol}${foundRow}` : '';
      }
      function listsToStringStrictPairingV3(v4Prompt, v4Negative = null, baseString = '', useCoords = true) {
        if (!Array.isArray(v4Prompt)) return baseString;
        const pairParts = [];
        const negativeList = Array.isArray(v4Negative) ? v4Negative : [];
        for (let i = 0; i < v4Prompt.length; i++) {
          const promptItem = v4Prompt[i];
          try {
            const pCaption = promptItem?.char_caption || '';
            const centers = promptItem?.centers;
            let coords = defaultCoordsRev;
            if (centers && Array.isArray(centers) && centers.length > 0 && typeof centers[0] === 'object' && centers[0] !== null) {
              coords = { x: centers[0]?.x ?? defaultCoordsRev.x, y: centers[0]?.y ?? defaultCoordsRev.y };
            }
            const positionCode = getPositionCodeRev(coords.x, coords.y, useCoords);
            const cPart = ` c=${pCaption}${positionCode}`;
            let ucPart = '';
            if (i < negativeList.length) {
              const negativeItem = negativeList[i];
              if (typeof negativeItem === 'object' && negativeItem !== null) {
                const nCaption = (negativeItem?.char_caption || '').trim();
                if (nCaption) ucPart = ` uc=${nCaption}`;
              }
            }
            pairParts.push(cPart + ucPart);
          } catch { continue; }
        }
        const addedString = pairParts.join('');
        let finalString = baseString || '';
        const trailerMarkers = [' ntags=', ' negprompt=', ' neg=', ' negative=', ' #', ' /'];
        let insertPos = finalString.length;
        for (const marker of trailerMarkers) {
          const pos = finalString.indexOf(marker);
          if (pos !== -1) insertPos = Math.min(insertPos, pos);
        }
        finalString = finalString.substring(0, insertPos) + addedString + finalString.substring(insertPos);
        finalString = finalString.replace(/\s\s+/g, ' ').trim();
        return finalString;
      }

      // 提取 v4 的角色 captions
      let charCaptionsTxt = '';
      const v4Prompt = effectiveComment?.v4_prompt;
      const v4NegativePrompt = effectiveComment?.v4_negative_prompt;
      try {
        let charCaptionsN = [];
        if (v4NegativePrompt && v4NegativePrompt.caption && Array.isArray(v4NegativePrompt.caption.char_captions)) charCaptionsN = v4NegativePrompt.caption.char_captions;
        if (v4Prompt && v4Prompt.caption && Array.isArray(v4Prompt.caption.char_captions)) {
          const charCaptions = v4Prompt.caption.char_captions;
          const useCoords = v4Prompt.use_coords ?? true;
          charCaptionsTxt = listsToStringStrictPairingV3(charCaptions, charCaptionsN, '', useCoords);
          charCaptionsTxt = charCaptionsTxt.replace(/(?<!u)c=/g, '\nc=');
        }
      } catch {}

      // 采样器名称映射
      const samplerMap = {
        'k_euler': 'Euler',
        'k_euler_ancestral': 'Euler a',
        'k_dpmpp_2s_ancestral': 'DPM++ 2S a',
        'k_dpmpp_2m': 'DPM++ 2M',
        'k_dpmpp_sde': 'DPM++ SDE',
        'k_dpmpp_2m_sde': 'DPM++ 2M SDE',
        'ddim': 'DDIM'
      };
      sampler = samplerMap[sampler] || 'Euler';

      const sm = effectiveComment.sm;
      const sm_dyn = effectiveComment.sm_dyn;

      const naiVersion = formatVersion(source, usedDescriptionFallback, lang);
      const tp = formatType(hasCharRef, hasVibeTransfer, request_type, lang);
      const naiType = tp.label;

      // 参数短语（allFIf）与信息片段（allIf）
      let allIf = '';
      let allFIf = '';
      if (source.includes('NovelAI Diffusion V4.5 B5A2A797')) { allFIf += ' nai4.5c'; }
      else if (source.includes('NovelAI Diffusion V4.5 B9F340FD') || source.includes('NovelAI Diffusion V4.5')) { allFIf += ' nai4.5'; }
      else if (source.includes('NovelAI Diffusion V4 F6E18726') || source.includes('NovelAI Diffusion V4 C1CCBA86')) { allFIf += ' nai4c'; }
      else if (source.includes('NovelAI Diffusion V4 4F49EC75') || source.includes('NovelAI Diffusion V4 79F47848') || source.includes('NovelAI Diffusion V4')) { allFIf += ' nai4'; }

      // 常见分辨率命名
      const scaleL = {
        '长图': { height: 1536, width: 512 },
        '长幅': { height: 1728, width: 576 },
        '宽图': { height: 832, width: 1216 },
        '横图': { height: 512, width: 1536 },
        '横幅': { height: 576, width: 1728 },
        '方图': { height: 1024, width: 1024 },
      };
      for (const name in scaleL) {
        if (height === scaleL[name].height && width === scaleL[name].width) { allFIf += ` ${name}`; break; }
      }

      if (sm && sm_dyn) { allFIf += ' SMEA'; allIf += (lang === 'en') ? ' | SMEA+DYN enabled' : ' | 已开启 SMEA+DYN'; }
      else if (sm && !sm_dyn) { allFIf += ' SMEAnoDYN'; allIf += (lang === 'en') ? ' | SMEA enabled' : ' | 已开启 SMEA'; }

      if (sampler !== 'Euler') allFIf += ` ${sampler}`;
      if (typeof scale === 'number' && scale !== 5) { allIf += ` | scale:${scale}`; allFIf += ` scale=${scale}`; }
      function isDivisibleBy002(number) {
        try { const scaled = number * 100; return Math.abs(scaled - Math.round(scaled)) < 1e-9 && Math.round(scaled) % 2 === 0; } catch { return false; }
      }
      if (cfg_rescale !== undefined && cfg_rescale !== null) {
        let numCfgRescale = Number(cfg_rescale);
        if (!isNaN(numCfgRescale) && numCfgRescale !== 0) {
          if (!isDivisibleBy002(numCfgRescale)) numCfgRescale = parseFloat((numCfgRescale + 0.01).toFixed(2));
          allIf += ` | cfg_rescale:${numCfgRescale}`;
          allFIf += ` cfg_rescale=${numCfgRescale}`;
        }
      }
      if (ns && ns !== 'native') {
        allIf += ` | noise_schedule:${ns}`;
        allFIf += ` ns=${ns}`;
        if (!prefer_brownian) allFIf += ' prefer_brownian=false';
      }
      if (dynamic_thresholding) allIf += ' ot=decrisp';
      if (typeof skip_cfg_above_sigma === 'number' && skip_cfg_above_sigma > 0) allIf += ' ot=variety';
      if (typeof steps === 'number' && steps !== 28) allIf += ` steps=${steps}`;

      allFIf = (allFIf || '').trim();
      let txt = '';
      const baseTail = (lang === 'en')
        ? `Generation ${naiVersion}: ${naiType} | Sampler:${sampler} | ${width}x${height}${allIf} | seed=${seed}`
        : `生成类型${naiVersion}:${naiType} | 采样器:${sampler} | ${width}X${height}${allIf} | seed=${seed}`;
      if (allFIf) {
        txt = `/绘画 ${lang === 'en' ? 'Params' : '参数'}=${allFIf}\n\nprompt=${prompt}${charCaptionsTxt ? '\n' + charCaptionsTxt : ''}\nntags=${uc}\n\n${baseTail}`;
      } else {
        txt = `/绘画\n\nprompt=${prompt}${charCaptionsTxt ? '\n' + charCaptionsTxt : ''}\nntags=${uc}\n\n${baseTail}`;
      }
      txt = String(txt).replace(/\n\n+/g, '\n\n').trim();
      return { txt, details: {} };
    } catch (error) {
      const lang = getLang();
      const head = (lang === 'en')
        ? `Error formatting NAI text: ${error && error.message ? error.message : String(error)}\nRaw JSON:\n`
        : `格式化 NAI 文本时出错: ${error && error.message ? error.message : String(error)}\n原始 JSON:\n`;
      return { txt: head + `${JSON.stringify(safeObj(jsonDataIn), null, 2)}`, details: {} };
    }
  }

  window.NAI.convert = convert;
})();
