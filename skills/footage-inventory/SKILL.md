---
name: footage-inventory
description: Inventory footage folders with ffprobe. Load when they name a clip folder, ask what they shot, or before any cut.
---

# footage-inventory

Know the bin before you cut.

## Steps

1. `ls` the folder. Skip `artifacts/`, `dataset/`, `.edittude/`.
2. Run:

```
edittude-v3 media inventory FOLDER --out OUT/inventory.json
```

3. Read the JSON. Note video count, total seconds, fps/resolution mix, and any audio-only file that looks like voiceover or music. A `rotation` field is iPhone display-matrix. ffmpeg applies it on decode. Do not transpose again.
4. If you need eyes:

```
edittude-v3 media thumbs OUT/inventory.json --out OUT/thumbs
```

Completion: a written inventory, and thumbs if you could not name the coverage without them.

Helper: `skills/footage-inventory/scripts/inventory.sh`
