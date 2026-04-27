export function normalizeStreamText(input, { final = false } = {}) {
  if (!input) return "";
  let s = String(input);
  s = s.normalize('NFKC').replace(/[\u200B-\u200D\u2060\uFEFF]/g, '');
  const lines = s.split(/\r?\n/);
  let inCode = false;

  const out = lines.map((raw) => {
    let line = raw;

    if (/^\s*```/.test(line)) {
      inCode = !inCode;
      return line;
    }
    if (inCode) return line;

    if (!final) return line;

    if (/^\s*\|.*\|\s*$/.test(line)) return line;
    if (/^\s*:?-{3,}\s*(\|\s*:?-{3,}\s*)+\|?\s*$/.test(line)) return line;
    if (/\]\(|<a\s/i.test(line)) return line;

    line = line.replace(/([A-Za-z가-힣])\n(?=[A-Za-z가-힣])/g, '$1');

    if (!/^\s*\|.*\|\s*$/.test(line)) {
      line = line.replace(/\s*\|+\s*$/g, '');
    }

    const boldMatches = line.match(/\*\*/g) || [];
    if (boldMatches.length % 2 === 1) {
      const i = line.lastIndexOf('**');
      if (i >= 0) line = line.slice(0, i) + '\\*\\*' + line.slice(i + 2);
    }

    if (!/^\s*\*\s/.test(line)) {
      const singles = line.match(/(?<!\*)\*(?!\*)/g) || [];
      if (singles.length % 2 === 1) {
        let idx = -1;
        const re = /(?<!\*)\*(?!\*)/g;
        let m;
        while ((m = re.exec(line)) != null) idx = m.index;
        if (idx >= 0) line = line.slice(0, idx) + '\\*' + line.slice(idx + 1);
      }
    }

    if (!/https?:\/\//i.test(line)) {
      if (!/^\s*#{1,6}\s/.test(line)) {
        line = line.replace(/(?<!#)#(?!#)/g, '\\#');
      }
    }

    line = line.replace(/\*\*\s+([^*]+?)\s+\*\*/g, '**$1**');
    return line;
  });

  return out.join('\n');
}
