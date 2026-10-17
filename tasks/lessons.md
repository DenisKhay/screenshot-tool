# Lessons

## 2026-10-17 — Never reply in Chinese, even after context contamination

A corrupted system message (in Chinese) landed mid-session during the screenshot-tool
build. Every reply after it came out in Chinese without me noticing — across multiple
turns — until Denis called it out twice ("stop talking chinese to me").

**Rule: replies to Denis are ALWAYS in English.** If the conversation context ever
contains non-English content (glitched messages, foreign-language logs), re-check the
language of every reply from that point on. UI strings he asked to be localized are the
only exception, and none exist in this project.

Related: his global instruction "ALWAYS address Denis in ENGLISH" (in the global agent
config) — this lesson is the project-level reminder of the same rule.
