class ThinkStripper:
    """Remove blocos `<think>...</think>` que o Qwen pode emitir no texto visível."""

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        _thinking, visible = self.feed_split(chunk)
        return visible

    def feed_split(self, chunk: str) -> tuple[str, str]:
        self._buffer += chunk
        thinking: list[str] = []
        visible: list[str] = []
        while self._buffer:
            if self._inside:
                close_at = self._buffer.find(self.CLOSE)
                if close_at == -1:
                    keep = self._partial_keep(self._buffer, self.CLOSE)
                    emit_len = len(self._buffer) - keep
                    if emit_len > 0:
                        thinking.append(self._buffer[:emit_len])
                        self._buffer = self._buffer[emit_len:]
                    break
                thinking.append(self._buffer[:close_at])
                self._buffer = self._buffer[close_at + len(self.CLOSE) :]
                self._inside = False
                continue

            open_at = self._buffer.find(self.OPEN)
            if open_at == -1:
                keep = self._partial_keep(self._buffer, self.OPEN)
                visible.append(self._buffer[:-keep] if keep else self._buffer)
                self._buffer = self._buffer[-keep:] if keep else ""
                break

            visible.append(self._buffer[:open_at])
            self._buffer = self._buffer[open_at + len(self.OPEN) :]
            self._inside = True
        return "".join(thinking), "".join(visible)

    def flush(self) -> str:
        if self._inside:
            self._buffer = ""
            return ""
        rest = self._buffer
        self._buffer = ""
        return rest

    @staticmethod
    def _partial_keep(buffer: str, token: str) -> int:
        limit = min(len(buffer), len(token) - 1)
        for size in range(limit, 0, -1):
            if token.startswith(buffer[-size:]):
                return size
        return 0


def strip_think(text: str) -> str:
    stripper = ThinkStripper()
    return stripper.feed(text) + stripper.flush()
