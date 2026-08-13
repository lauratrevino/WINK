






















const WINK_COURSE_COLOR_PALETTE = [
  '#FF8200', 
  '#0EA5E9', 
  '#22C55E', 
  '#A855F7', 
  '#EF4444', 
  '#14B8A6', 
  '#EAB308', 
  '#EC4899', 
  '#6366F1', 
  '#84CC16', 
  '#F97316', 
  '#06B6D4', 
];







function winkHashString(str) {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) + hash) + str.charCodeAt(i); 
    hash = hash >>> 0; 
  }
  return hash;
}












function winkColorForCourse(courseName, courseColorMap) {
  const normalized = String(courseName || '').trim().toLowerCase();
  if (!normalized) return WINK_COURSE_COLOR_PALETTE[0];
  if (courseColorMap && typeof courseColorMap === 'object') {
    for (const key of Object.keys(courseColorMap)) {
      if (String(key || '').trim().toLowerCase() === normalized) {
        return courseColorMap[key];
      }
    }
  }
  const index = winkHashString(normalized) % WINK_COURSE_COLOR_PALETTE.length;
  return WINK_COURSE_COLOR_PALETTE[index];
}

function winkTextColorForBg(hexColor) {
  const hex = String(hexColor || '').replace('#', '');
  if (hex.length !== 6) return '#111827';
  const r = parseInt(hex.slice(0, 2), 16) / 255;
  const g = parseInt(hex.slice(2, 4), 16) / 255;
  const b = parseInt(hex.slice(4, 6), 16) / 255;
  const linear = (c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
  const luminance = 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
  return luminance > 0.5 ? '#111827' : '#ffffff';
}
