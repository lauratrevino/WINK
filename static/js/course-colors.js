/**
 * Shared course-color utility.
 *
 * Documents, Calendar, and Dashboard all need to show the same course in
 * the same color, that color needs to stay the same across sessions, AND
 * no two of a student's active courses can ever share a color. That's more
 * than a stateless function (hash or list-position) can promise on its
 * own — a hash can collide, and list-position reshuffles every time a
 * course is added or removed. So the actual assignment now lives in the
 * database (see services/course_colors.py): a color is assigned once, the
 * first time a course is seen, and freed for reuse only when that course's
 * last document is deleted. Every page passes down that same
 * server-computed {course: color} map (`courseColors` below) — this file
 * just looks the course up in it.
 *
 * The hash function is kept only as a last-resort fallback for a course
 * that somehow isn't in the map yet (e.g. a stale page that hasn't
 * reloaded since a new course was added) — not as the primary mechanism.
 */

// A fixed palette of visually distinct colors, chosen to read clearly
// against WINK's light page background (#f4f6fb) and navy nav — avoids
// near-white and near-navy tones that would blend in or clash.
const WINK_COURSE_COLOR_PALETTE = [
  '#FF8200', // UTEP orange
  '#0EA5E9', // sky blue
  '#22C55E', // green
  '#A855F7', // purple
  '#EF4444', // red
  '#14B8A6', // teal
  '#EAB308', // amber
  '#EC4899', // pink
  '#6366F1', // indigo
  '#84CC16', // lime
  '#F97316', // burnt orange
  '#06B6D4', // cyan
];

/**
 * Deterministic string hash (djb2 variant) — same input always produces the
 * same output, which is what makes "same course = same color" hold true
 * across page loads, sessions, and even the Calendar vs. Documents pages
 * without either page needing to know about the other's state.
 */
function winkHashString(str) {
  let hash = 5381;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) + hash) + str.charCodeAt(i); // hash * 33 + charCode
    hash = hash >>> 0; // keep it a positive 32-bit integer
  }
  return hash;
}

/**
 * Returns a hex color string for a given course name. Case/whitespace are
 * normalized first so "CS 2302", "cs 2302", and " CS 2302 " all land on the
 * same color instead of being treated as different courses.
 *
 * `courseColorMap` (optional but expected): the {course: color} object each
 * page renders server-side from services/course_colors.py — pass it in and
 * this is a straight, guaranteed-consistent lookup. Only falls back to the
 * hash below if the course genuinely isn't in the map (shouldn't normally
 * happen — every course the server knows about gets an entry).
 */
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

/**
 * Returns '#ffffff' or '#111827' — whichever reads better as text/icon
 * color placed on top of the given background hex color. Uses the standard
 * relative-luminance formula so this works correctly for any color in the
 * palette (or any future addition to it) without needing a hand-picked
 * text color per swatch.
 */
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
