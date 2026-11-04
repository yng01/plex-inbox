"""Small fakes shared by the test-suite (no network, no real subprocesses)."""

from __future__ import annotations


# --- ytdlp manager (``lines()``-style process) -------------------------------


class FakeLineProc:
    """Scripted process: yields fixed lines, then a return code.

    ``on_finish`` (sync callable) runs once on the first ``wait()`` so tests can
    simulate yt-dlp writing the output file right before exit.
    """

    def __init__(self, lines=None, returncode=0, stderr="", on_finish=None):
        self._lines = list(lines or [])
        self._rc = returncode
        self._stderr = stderr
        self._on_finish = on_finish
        self._finished = False
        self.returncode: int | None = None
        self.killed = False

    async def lines(self):
        for ln in self._lines:
            yield ln

    @property
    def stderr_tail(self) -> str:
        return self._stderr.strip()

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        if not self._finished:
            self._finished = True
            if self._on_finish:
                self._on_finish()
        self.returncode = self._rc
        return self._rc


class RecordingSpawner:
    """Spawner that records commands and returns scripted processes in order."""

    def __init__(self, procs=None):
        self.procs = list(procs or [])
        self.commands: list[list[str]] = []

    def push(self, proc: FakeLineProc) -> None:
        self.procs.append(proc)

    async def __call__(self, cmd):
        self.commands.append(list(cmd))
        if self.procs:
            return self.procs.pop(0)
        return FakeLineProc(returncode=0)


# --- updater (``communicate()``-style process) -------------------------------


class FakeCommProc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self._out = stdout
        self._err = stderr
        self._rc = returncode
        self.returncode: int | None = None

    async def communicate(self):
        self.returncode = self._rc
        return self._out.encode(), self._err.encode()

    async def wait(self) -> int:
        self.returncode = self._rc
        return self._rc


class RecordingRunner:
    """Updater runner: records commands, returns scripted (stdout, stderr, rc)."""

    def __init__(self, results=None):
        # each result: tuple(stdout, stderr, rc)
        self.results = list(results or [])
        self.commands: list[list[str]] = []

    def push(self, stdout: str, stderr: str = "", rc: int = 0) -> None:
        self.results.append((stdout, stderr, rc))

    def __call__(self, cmd):
        self.commands.append(list(cmd))
        if self.results:
            out, err, rc = self.results.pop(0)
        else:
            out, err, rc = "", "", 0
        return FakeCommProc(out, err, rc)


# --- http (thumbnails, posters, plex refresh) --------------------------------


class FakeResponse:
    def __init__(self, status_code=200, content=b"imgbytes"):
        self.status_code = status_code
        self.content = content

    @property
    def text(self):
        if isinstance(self.content, (bytes, bytearray)):
            return self.content.decode("utf-8", "replace")
        return self.content


class FakeHttp:
    def __init__(self, get_status=200, get_content=b"imgbytes", post_status=200):
        self.get_status = get_status
        self.get_content = get_content
        self.post_status = post_status
        self.gets: list[dict] = []
        self.posts: list[dict] = []

    async def get(self, url, timeout=None, follow_redirects=False, headers=None):
        self.gets.append({"url": url, "timeout": timeout, "headers": dict(headers or {})})
        return FakeResponse(self.get_status, self.get_content)

    async def post(self, url, headers=None, timeout=None):
        self.posts.append({"url": url, "headers": dict(headers or {})})
        return FakeResponse(self.post_status, b"")


# --- PTB update / context / message ------------------------------------------


class FakeUser:
    def __init__(self, id=111, username=None):
        self.id = id
        self.username = username


class FakeMessage:
    def __init__(self, text=None, id=1, video=None, video_note=None):
        self.text = text
        self.message_id = id
        self.video = video
        self.video_note = video_note
        self.replies: list[str] = []
        self.edits: list[str] = []
        # the ``reply_markup`` kwarg of each edit (``None`` = kept the old one)
        self.edit_markups: list[object | None] = []
        self.last_reply: "FakeMessage | None" = None

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        reply = FakeMessage(id=self.message_id + 1)
        self.last_reply = reply
        return reply

    async def edit_text(self, text, **kw):
        self.edits.append(text)
        self.edit_markups.append(kw.get("reply_markup"))


class FakeVideo:
    """Stand-in for ``telegram.Video`` / ``telegram.VideoNote``.

    ``get_file()`` returns the supplied :class:`telegram.File`, so the handler's
    ``download_to_drive(custom_path=...)`` path runs against a local file (no net).
    """

    def __init__(
        self,
        tg_file,
        file_name="clip.mp4",
        duration=10.0,
        width=320,
        height=180,
        file_size=1234,
        file_id="AgADfakefileid",
        file_unique_id="uniq-123",
        mime_type="video/mp4",
        get_file_error: Exception | None = None,
    ):
        self._tg_file = tg_file
        self._get_file_error = get_file_error
        self.get_file_calls = 0
        self.file_name = file_name
        self.duration = duration
        self.width = width
        self.height = height
        self.file_size = file_size
        self.file_id = file_id
        self.file_unique_id = file_unique_id
        self.mime_type = mime_type

    async def get_file(self, **kw):
        self.get_file_calls += 1
        if self._get_file_error is not None:
            raise self._get_file_error
        return self._tg_file


class FakeVideoNote(FakeVideo):
    """Stand-in for ``telegram.VideoNote`` (no file_name/mime_type/width/height)."""

    def __init__(
        self,
        tg_file,
        duration=10.0,
        length=10,
        file_size=1234,
        file_id="AgADfakevn",
        file_unique_id="uniq-vn",
    ):
        super().__init__(
            tg_file,
            file_name=None,
            duration=duration,
            width=None,
            height=None,
            file_size=file_size,
            file_id=file_id,
            file_unique_id=file_unique_id,
            mime_type=None,
        )
        self.length = length


class FakeBot:
    def __init__(self):
        self.edits: list[tuple[int, int, str]] = []

    async def edit_message_text(self, chat_id, message_id, text, **kw):
        self.edits.append((chat_id, message_id, text))
        return True


class FakeCallbackQuery:
    def __init__(self, data="", message=None):
        self.data = data
        self.message = message or FakeMessage()
        self.answered = 0

    def answer(self, text=None):
        self.answered += 1

    async def edit_message_text(self, text, **kw):
        await self.message.edit_text(text, **kw)


class FakeUpdate:
    def __init__(self, user=None, message=None, callback_query=None):
        self.effective_user = user or FakeUser()
        self.effective_message = message
        self.callback_query = callback_query

    @property
    def effective_chat(self):
        return None


class FakeContext:
    def __init__(self, bot=None):
        self.user_data: dict = {}
        self.bot_data: dict = {}
        self.bot = bot or FakeBot()


# --- probe spawner (``communicate()``-style) ---------------------------------


class FakeProbeSpawner:
    """``run(cmd) -> proc`` where proc.communicate() returns (stdout, stderr)."""

    def __init__(self, stdout="", stderr="", returncode=0, delay=0.0):
        self._stdout = stdout
        self._stderr = stderr
        self._rc = returncode
        self._delay = delay
        self.cmds: list[list[str]] = []

    async def run(self, cmd):
        self.cmds.append(list(cmd))
        if self._delay:
            import asyncio

            await asyncio.sleep(self._delay)

        outer = self

        class P:
            returncode = None

            def __init__(self, o):
                self._out = o._stdout
                self._err = o._stderr
                self._rc = o._rc

            async def communicate(self):
                self.returncode = self._rc
                return self._out.encode(), self._err.encode()

            def kill(self):
                pass

        return P(outer)
