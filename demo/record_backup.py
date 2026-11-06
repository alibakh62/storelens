"""make backup-video: drive the exact §11.2 flow at human pace with on-screen captions; record → MP4 (1920×1080)."""
import shutil
import subprocess
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("Playwright is not installed. Run:  uv sync --extra dev --extra llm && uv run playwright install chromium")

sys.path.insert(0, str(Path(__file__).parent))
from flow import Flow  # noqa: E402
from pw import launch  # noqa: E402
from server import ROOT, server  # noqa: E402

OUT = ROOT / "demo" / "out"


def ffmpeg_exe() -> str:
    """System ffmpeg if installed, else the static build bundled with the imageio-ffmpeg package (dev extra)."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        raise SystemExit("ffmpeg not found. Run:  uv sync --extra dev --extra llm   (or: brew install ffmpeg)")


def main():
    """Frames come from the Chrome DevTools screencast (no Playwright ffmpeg needed); system ffmpeg encodes them."""
    import base64
    import time
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = OUT / "video_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    frames: list[tuple[float, Path]] = []
    with server(port=8766, provider="replay", pacing="1") as base, sync_playwright() as p:
        b = launch(p)
        ctx = b.new_context(viewport={"width": 1536, "height": 864})
        pg = ctx.new_page()
        cdp = ctx.new_cdp_session(pg)

        def on_frame(ev):
            f = tmp / f"f{len(frames):06d}.jpg"
            f.write_bytes(base64.b64decode(ev["data"]))
            frames.append((time.time(), f))
            try:
                cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]})
            except Exception:  # noqa: BLE001
                pass
        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 85, "maxWidth": 1536, "maxHeight": 864})
        Flow(pg, base, pace=2.6, captions=True, check=False).run()
        pg.wait_for_timeout(1500)
        cdp.send("Page.stopScreencast")
        frames.append((time.time(), frames[-1][1]))
        b.close()
    # concat demuxer with real per-frame durations (screencast only emits frames when the page changes)
    lst = tmp / "frames.txt"
    with open(lst, "w") as fh:
        for (t0, f), (t1, _) in zip(frames, frames[1:]):
            fh.write(f"file '{f.name}'\nduration {max(t1 - t0, 0.001):.3f}\n")
        fh.write(f"file '{frames[-1][1].name}'\n")
    mp4 = OUT / "storelens_demo.mp4"
    subprocess.run([ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-vf", "scale=1920:1080:flags=lanczos,fps=30", "-c:v", "libx264", "-preset", "veryfast",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True, cwd=tmp)
    shutil.rmtree(tmp, ignore_errors=True)
    print("wrote", mp4)


if __name__ == "__main__":
    main()
