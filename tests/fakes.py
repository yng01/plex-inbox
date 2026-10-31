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
    def __init__(self, text=None, id=1):
        self.text = text
        self.message_id = id
        self.replies: list[str] = []
        self.edits: list[str] = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)
        return FakeMessage(id=self.message_id + 1)

    async def edit_text(self, text, **kw):
        self.edits.append(text)


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
        self.message.edits.append(text)


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
