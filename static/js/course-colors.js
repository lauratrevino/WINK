/**
 * Shared course-color utility.
 *
 * Both the Calendar and Documents pages need to show the same course in the
 * same color — this is the single source of truth for that mapping so they
 * can never drift apart. Deliberately a pure hash (course name -> color)
 * rather than colors assigned in registration order: a hash needs no shared
 * server-side state, works identically whether it's called from the
 * Calendar page or the Documents page, and gives the same course the same
 * color today as it will next semester.
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
 */
function winkColorForCourse(courseName) {
  const normalized = String(courseName || '').trim().toLowerCase();
  if (!normalized) return WINK_COURSE_COLOR_PALETTE[0];
  const index = winkHashString(normalized) % WINK_COURSE_COLOR_PALETTE.length;
  return WINK_COURSE_COLOR_PALETTE[index];
}
