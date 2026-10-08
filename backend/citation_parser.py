"""从模型增量文本中提取受限引用编号，并移除正文标记。"""

from __future__ import annotations


class CitationStreamParser:
    """跨模型分块解析 ``[[n]]``，只登记本轮证据集合里的编号。"""

    def __init__(self, allowed_indices: set[int]) -> None:
        self.allowed_indices = allowed_indices
        self.references: list[int] = []
        self.invalid_reference = False
        self._buffer = ""

    def feed(self, text: str, *, final: bool = False) -> str:
        """返回可展示正文；引用标记留在服务端，不进入聊天正文。"""
        # SSE 可能把 [[1]] 拆成几段发送；暂存未收全的标记再一起解析。
        self._buffer += text
        output: list[str] = []
        while self._buffer:
            marker_at = self._buffer.find("[[")
            if marker_at < 0:
                if not final and self._buffer.endswith("["):
                    output.append(self._buffer[:-1])
                    self._buffer = "["
                else:
                    output.append(self._buffer)
                    self._buffer = ""
                break
            if marker_at:
                output.append(self._buffer[:marker_at])
                self._buffer = self._buffer[marker_at:]

            marker_end = self._buffer.find("]]", 2)
            if marker_end < 0:
                if not final:
                    break
                self.invalid_reference = True
                self._buffer = ""
                break

            raw_index = self._buffer[2:marker_end]
            # 只认可本轮检索实际给模型的编号，不能引用旧回答或编造编号。
            if raw_index.isdigit() and int(raw_index) in self.allowed_indices:
                citation_index = int(raw_index)
                if citation_index not in self.references:
                    self.references.append(citation_index)
            else:
                self.invalid_reference = True
            self._buffer = self._buffer[marker_end + 2 :]

        return "".join(output)
