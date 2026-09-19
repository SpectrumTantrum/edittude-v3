---
name: assembly
description: Assemble an EDL with ffmpeg. Load when rendering a picture cut, concat, or trim-and-join.
---

# assembly

Turn the EDL into a picture file. Do not hand-write concat lists.

```
edittude-v3 media assemble edl.json --out picture.mp4 --work OUT/work
```

The command re-encodes each event to the EDL aspect at 30fps, stereo 48k, then concat-copies. iPhone extra data streams stay off the map.

If the EDL already has title, look, and voiceover paths, prefer `finish` after assemble instead of stacking one-off encodes.

Completion: `picture.mp4` exists and ffprobe duration is within 0.3s of the EDL duration.
