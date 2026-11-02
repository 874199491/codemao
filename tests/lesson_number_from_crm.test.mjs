import test from "node:test";
import assert from "node:assert/strict";

import {
  lessonNumberFromCrm,
  weeklyCourseNumberFromCrm,
} from "../scripts/lesson_number_from_crm.mjs";

test("uses the numeric prefix in the course title before CRM internal ordering", () => {
  const lesson = lessonNumberFromCrm(
    { course_name: "27-for循环认识1", no_free_sort: 33, course_number: 33 },
    34,
  );

  assert.equal(lesson, 27);
});

test("falls back to the internal ordering when the course title has no prefix", () => {
  const lesson = lessonNumberFromCrm(
    { course_name: "for循环认识1", no_free_sort: 33, course_number: 33 },
    34,
  );

  assert.equal(lesson, 33);
});

test("uses CRM internal ordering for weekly completion matching", () => {
  const lesson = weeklyCourseNumberFromCrm(
    { course_name: "27-for循环认识1", no_free_sort: 33, course_number: 33 },
  );

  assert.equal(lesson, 33);
});
