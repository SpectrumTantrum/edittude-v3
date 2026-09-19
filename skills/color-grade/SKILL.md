---
name: color-grade
description: Color and look. Load when grading, correcting iPhone color, or picking warm/cool/teal-orange/neutral.
---

# color-grade

Correct first, look second. iPhone daylight often runs a little cool and green. Warm is the house default, not a LUT demo.

Looks: `warm`, `cool`, `teal-orange`, `neutral`.

```
edittude-v3 media grade picture.mp4 --out graded.mp4 --look warm
```

A regrade onto the same output needs `edittude-v3 media --force grade`.

Prefer baking the look in `finish` so you do not encode twice.

Completion: skin is plausible, whites are not cyan, and the look matches the EDL.
