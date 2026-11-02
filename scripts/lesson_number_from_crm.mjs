/**
 * CRM's no_free_sort/course_number is an internal sequence and can diverge
 * from the visible course number after inserted training lessons.  The number
 * at the start of course_name is the course number shown to teachers.
 */
export function lessonNumberFromCrm(item) {
  const name = String(item?.course_name || "");
  const match = name.match(/^\s*(?:第\s*)?(\d+)\s*(?:[-－]|课)/);
  if (match) return Number(match[1]);

  const raw = Number(item?.no_free_sort || item?.course_number || 0);
  return Number.isFinite(raw) && raw > 0 ? raw : 0;
}

/**
 * Weekly scheduling in CRM follows its internal sequence.  The visible title
 * number is content-facing and can differ after inserted training lessons.
 */
export function weeklyCourseNumberFromCrm(item) {
  const raw = Number(item?.no_free_sort || item?.course_number || 0);
  return Number.isFinite(raw) && raw > 0 ? raw : lessonNumberFromCrm(item);
}
