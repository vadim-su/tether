import asyncio
import base64
import re
import struct
import zlib

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.test import TestModel

from tether.api.events import ApprovalRequested
from tether.host.session import Session
from tether_plugin_tui import kitty
from tether_plugin_tui.app import TetherApp
from tether_plugin_tui.dialogs import ApprovalDialog
from tether_plugin_tui.widgets import ImageView


def make_png(width: int = 40, height: int = 20) -> bytes:
    raw = b"".join(b"\x00" + bytes(3 * width) for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_detects_kitty_and_ghostty_only():
    assert kitty.supported(env={"KITTY_WINDOW_ID": "1"})
    assert kitty.supported(env={"TERM": "xterm-ghostty"})
    assert kitty.supported(env={"TERM_PROGRAM": "ghostty"})
    assert not kitty.supported(env={"TERM": "xterm-256color", "TERM_PROGRAM": "iTerm.app"})
    assert kitty.supported("on", env={}) and not kitty.supported("off", env={"KITTY_WINDOW_ID": "1"})


def test_png_size_and_fit():
    assert kitty.png_size(make_png(40, 20)) == (40, 20)
    assert kitty.png_size(b"GIF89a....") is None
    assert kitty.fit(100, 100, max_cols=80, max_rows=20, cell=(10, 20)) == (10, 5)  # native size
    assert kitty.fit(4000, 1000, max_cols=80, max_rows=20, cell=(10, 20)) == (80, 10)  # shrunk to width
    assert kitty.fit(1000, 4000, max_cols=80, max_rows=20, cell=(10, 20)) == (10, 20)  # shrunk to height


def test_transmit_chunks_and_placeholders():
    png = make_png() + bytes(10_000)  # big enough for several chunks
    image = kitty.KittyImage(png, cols=3, rows=2, id=0x123456)
    seqs = re.findall(r"\x1b_G([^;]*);([^\x1b]*)\x1b\\", image.transmit())
    assert seqs[0][0] == "a=T,U=1,f=100,t=d,i=1193046,c=3,r=2,q=2,m=1"
    assert all(keys == "q=2,m=1" for keys, _ in seqs[1:-1]) and seqs[-1][0] == "q=2,m=0"
    assert all(len(data) <= kitty.CHUNK for _, data in seqs)
    assert base64.b64decode("".join(data for _, data in seqs)) == png
    assert image.delete() == "\x1b_Ga=d,d=I,i=1193046,q=2\x1b\\"

    lines = image.lines()
    assert len(lines) == 2
    d = kitty.DIACRITICS
    assert lines[1].plain == "".join(kitty.PLACEHOLDER + d[1] + d[col] for col in range(3))
    assert str(lines[0].style.color.triplet.hex) == "#123456"
    assert lines[0].cell_len == 3


def test_image_view_falls_back_to_a_caption():
    async def go():
        app = TetherApp(Session(lambda: Agent(TestModel())), images="off")
        async with app.run_test() as pilot:
            view = ImageView(make_png(), enabled=False, caption="shot")
            await app.query_one("#log").mount(view)
            await pilot.pause()
            assert "image shot 40×20" in str(view.render())
            assert view.image is None

    asyncio.run(go())


def test_tool_result_image_is_shown_and_survives_a_dialog():
    png = make_png()

    def factory():
        agent = Agent(TestModel())

        @agent.tool_plain
        def screenshot() -> BinaryContent:
            return BinaryContent(png, media_type="image/png")

        return agent

    session = Session(factory)

    async def go():
        app = TetherApp(session, images="on")
        async with app.run_test(size=(100, 30)) as pilot:
            app.query_one("#prompt").value = "look"
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause()
                if not session.busy:
                    break
            views = list(app.query(ImageView))
            assert len(views) == 1 and views[0].image is not None
            color = f"#{views[0].image.id:06x}"
            assert color in app.export_screenshot()
            # A dialog must not tint the screen below: the colour *is* the image id.
            app.push_screen(ApprovalDialog(ApprovalRequested("shell", {"command": "ls"}, "x")))
            await pilot.pause()
            assert color in app.export_screenshot()

    asyncio.run(go())
