---
name: iterate-recut
description: Recut after QC. Load when the render failed review, feels long, or they asked to try again.
---

# iterate-recut

Change the EDL. Do not grade your way out of a bad cut.

```
edittude-v3 media recut edl.json --out edl2.json --drop-longest
```

Then open the new EDL. Drop a duplicate wide. Shorten the longest hold. If loudness failed, remake the mix only. Assemble and `finish` again. QC once more.

Stop after one honest recut unless they asked to keep going.

A reply that only lists commands is a failed turn. The new file has to exist.

Completion: a new `final.mp4` and a QC that you can explain.
