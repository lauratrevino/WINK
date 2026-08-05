// Shared by calendar.html and documents.html — save as static/js/course-colors.js
//
// Both pages used to build up a course→color map in the order courses
// happened to appear on that page, so the same course could get a
// different color on Documents vs. the Calendar. Hashing the course name
// itself means the color only depends on the name, not render order, so
// it's identical everywhere this file is included.
const WINK_COURSE_COLORS = ['#002855', '#FF8200', '#166534', '#7c3aed', '#0891b2', '#be123c', '#4d7c0f', '#a16207'];

function winkColorForCourse(course) {
  const str = String(course || '').trim().toLowerCase();
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = (hash * 31 + str.charCodeAt(i)) >>> 0;
  }
  return WINK_COURSE_COLORS[hash % WINK_COURSE_COLORS.length];
}
