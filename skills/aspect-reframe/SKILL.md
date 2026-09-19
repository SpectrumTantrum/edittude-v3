---
name: aspect-reframe
description: Aspect and reframing. Load for 16:9, 9:16, 1:1, vertical, square, or crop-to-fill.
---

# aspect-reframe

16:9 unless they asked for vertical or square. Reframe by crop, not letterbox, unless they asked for pad.

```
edittude-v3 media reframe final.mp4 --out vertical.mp4 --aspect 9:16
edittude-v3 media reframe final.mp4 --out square.mp4 --aspect 1:1
```

Or set `"aspect": "9:16"` on the EDL and assemble again so every event is cropped on the way in. Center crop is the default. If a face sits on the edge, move `in`/`out` or pick another shot rather than padding.

Completion: the file is the named aspect, no black bars unless requested.
