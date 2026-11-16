# quantaframe-warper

Experimental video editor that deforms independent PNG layers with decoded codec
motion vectors. Different blocks of one PNG can move in opposite directions,
shear, duplicate or disappear. The RGBA mask follows the same deformation as the
color. The background uses the original decoded video frames.

This MVP provides a Python API, a video inspection CLI and an Electron workbench sharing
one Python engine. No optical flow or object tracker is used.

## Run the desktop editor

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 22.12+ and npm.
The PyAV wheel supplies FFmpeg libraries and libx264 on the tested Linux setup.
A separate `ffmpeg` executable is unnecessary for normal operation.

```bash
uv sync
npm ci --prefix desktop
npm start --prefix desktop
```

On Linux, if startup aborts with a `chrome-sandbox` ownership or mode error,
use this explicit development fallback:

```bash
npm run start:unsandboxed --prefix desktop
```

This disables Chromium's process sandbox for this application launch. Use it only
for local development with trusted media. The normal `start` command keeps
sandboxing enabled. No root permissions or system configuration changes are
needed for the fallback. See [Electron's sandbox documentation](https://www.electronjs.org/docs/latest/tutorial/sandbox)
for the implications of `--no-sandbox`.

1. Import a short video. Initial preparation decodes the entire clip and builds
   the codec analysis stream. Loading progress appears over the preview. A 4K
   phone clip can take several minutes to prepare.
2. Import one or more PNGs, then scrub to the desired starting frame.
3. Drag an asset from the left panel onto the video. The drop position is its center.
4. Drag the selected layer’s box to move it, its corners to scale, or its top
   handle to rotate. Numeric controls provide precise transforms.
5. Play or scrub forward to see the deformation. Select a layer in the left panel
   to return to its start frame. Editing a transform also returns to that frame.
6. Adjust motion strength and duration, then export an MP4. Export progress appears below the preview.

Frame indexes start at zero. Left and right arrows step one frame. Duration includes
the placement frame and the layer disappears at `start + duration`. Layers composite
in list order, with later placements on top. Positive rotation is clockwise.
Strength 1 applies the full codec displacement, 0 freezes the layer, and negative
values reverse displacement. Higher strengths amplify block artifacts.

The desktop starts the repository's `.venv` Python directly. Set
`QUANTAFRAME_PYTHON` to override the interpreter. For a browser fallback, run
`uv run quantaframe-warper ui` and open the token-bearing URL printed to stdout.
The browser fallback uses local path prompts instead of native file dialogs.

### Development logs

Both desktop launch commands send logs to the terminal automatically during
source development. Prefixes identify each layer:

- `[electron:main]`: startup, backend lifecycle, preload and window failures.
- `[electron:renderer]`: UI status, console messages at every severity, uncaught
  errors and rejected promises, with source locations.
- `[python]`: unbuffered backend stdout and stderr, import/export progress and
  exception tracebacks. The private startup handshake is consumed by Electron.

Session tokens are redacted from forwarded output. Request bodies and preview
image data are not logged. Set `QUANTAFRAME_LOG_LEVEL=DEBUG` to include Python HTTP
request diagnostics, which omit URL query strings:

```bash
QUANTAFRAME_LOG_LEVEL=DEBUG npm run start:unsandboxed --prefix desktop
```

## Reproducible experiment

```bash
uv run python scripts/make_demo.py
```

Import `outputs/demo/motion.mp4` and `outputs/demo/eye.png` in the editor. The generated video has upper and
lower regions moving in opposite directions. A single transparent eye straddles
the boundary, so it splits into independently moving regions as playback advances.
All generated media stays in ignored `outputs/`.

## CLI

```bash
uv run quantaframe-warper inspect local_inputs/video.mp4
```

## Python API

```python
from quantaframe_warper import Engine, Placement, Project

project = Project(
    video="local_inputs/video.mp4",
    assets={"eye": "local_inputs/eye.png"},
    placements=(Placement("eye", start=24, duration=120,
                          x=320, y=240, scale=0.75, rotation=15, strength=1),),
)
with Engine(project.video) as engine:
    print(engine.info)
    rgb = engine.render(project, 40)  # RGB uint8 NumPy array
    engine.export(project, "outputs/warped.mp4")
```

`Engine(..., progress=callback)` and
`engine.export(..., progress=callback)` report `(phase, completed_frames)`.
The initial `source_frames` callback reports the estimated total instead of a
completed count. `engine.info["timestamps"]` contains presentation times in seconds
relative to the first decoded frame.
An engine is synchronous and owns temporary disk files. Use a context manager,
and serialize calls to each session. Assets are assumed unchanged during a session.

## Motion-vector decisions

FFmpeg's [`AVMotionVector`](https://ffmpeg.org/doxygen/trunk/structAVMotionVector.html)
exports block sizes, destination coordinates and fractional displacements. The
reference coordinate is `destination + motion / motion_scale`. Its `source` field
identifies past/future direction, while the exact reference-frame identity is
explicitly absent. Treating all such vectors as previous-frame motion would give
incorrect results on arbitrary B-frames and multi-reference P-frames.

For a reliable first experiment, every input is decoded in presentation order and
encoded into a temporary **H.264 P-only analysis stream with one reference**.
Right-angle display rotation is applied to both background and analysis frames.
Every input frame retains its index, and original presentation timestamps are
preserved for playback and export, including variable-frame-rate phone video.
The encoder uses CRF 18, disables scene-cut keyframes and weighted prediction, and
uses a long GOP. PyAV enables `flags2=+export_mvs` before decoding that stream and
reads the actual motion-vector side data. See [FFmpeg encoder options](https://ffmpeg.org/ffmpeg-codecs.html#libx264_002c-libx264rgb)
and [PyAV side data](https://pyav.basswood.io/docs/17.0/api/sidedata.html).

**These are vectors from the analysis encode, not the input bitstream's original
vectors.** Input B-frames are supported through display-order decoding and this
normalization. Native arbitrary-reference vector propagation is deliberately not
implemented. It would require additional codec-specific reference metadata that
the public side-data interface does not expose.

Each codec block supplies a destination-to-previous-frame pull map. The engine
samples all four premultiplied RGBA channels through it using bilinear interpolation
and transparent borders. It does not smooth the block field. Intra-coded and
missing blocks use zero displacement, which holds the previous layer at those
locations. This policy allows the layer to persist across I-frames, but can leave
parts behind near disocclusions or scene changes. Overlapping pull regions can
copy pixels and divergent regions can tear holes. Repeated interpolation gradually
softens detail. These artifacts are part of the experiment.

## Architecture and limitations

Following Luma Fattal's basic structure, `src/quantaframe_warper/` contains the
configuration, engine, CLI and UI server. `desktop/` hosts a small Electron shell.
By default, a sandboxed renderer calls an authenticated loopback Python HTTP server, and a
narrow preload bridge opens native file dialogs. The canvas only displays PNGs
rendered by Python. Preview and export call the identical `Engine.render()` path.

- Start with short, modest-resolution clips. Decoded RGB PNGs and sparse vector
  arrays live in a temporary directory until the session closes. Long clips can
  consume substantial disk space. Preparation is repeated when reopening the app.
- Forward playback advances frame by frame at the engine's achievable speed.
  It can be slower than real time. Backward seeks and edits replay propagation
  from the earliest placement. There are no persistent checkpoints yet.
- The MVP accepts even-sized, square-pixel video with increasing presentation
  timestamps. Constant and variable frame rates and right-angle display rotation
  are supported. Variable dimensions or orientation and interlacing are rejected.
  The first video stream is used. HDR and color-managed workflows are not supported.
- Export is **silent H.264 MP4**, re-encoded at CRF 18. The source file is never
  modified. Original decoded backgrounds are preserved before compositing, but
  RGB conversion and output encoding can change final pixel values.
- Preparation and export block other engine operations. Import runs in a
  background job with a visible phase and frame counter. Export reports a busy
  state. There is no cancellation control yet.
- Edits are static initial transforms. There are no transform keyframes, undo,
  occlusion handling, audio preview or conventional tracking controls.
- Native desktop verification has covered headless Linux. Other platforms and
  physical mouse/trackpad interactions need further testing.

## Verification

```bash
uv run pytest
uv run ruff check src tests scripts
npm test --prefix desktop
uv run python scripts/make_demo.py
# Linux headless integration, after npm ci:
desktop/node_modules/.bin/electron tests/desktop-smoke.cjs --no-sandbox \
  --ozone-platform=headless --ozone-override-screen-size=1600,1000
```

Tests cover codec vector sign and fractional precision, independent deformation
of opposing regions, premultiplied alpha, input B-frame handling, deterministic
seeks, layer expiration, settings validation, preview/export agreement, session cleanup,
authenticated UI transport, rotated VFR import and export timing, and pollable
import progress with failure recovery. The Electron smoke exercises visible import
errors and retry, the editor controls and export using generated
inputs, writing a screenshot and output
under `outputs/demo/`. The headless test and the explicit `start:unsandboxed`
development fallback disable Chromium's process sandbox. Normal desktop startup
uses Electron's renderer sandbox.

Project code is MIT licensed. Dependencies retain their own licenses, including
FFmpeg and libx264. There are no bundled application releases in this source MVP.
