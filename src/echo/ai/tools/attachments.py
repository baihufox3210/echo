from agents import FunctionTool, RunContextWrapper, function_tool

from echo.ai.context import RunContext
from echo.ai.documents import paginate

CHUNK_CHARS = 12_000


def build_attachment_tool() -> FunctionTool:
    @function_tool
    async def read_attachment(ctx: RunContextWrapper[RunContext], file_id: str, start: int = 0) -> str:
        """讀取使用者本次上傳檔案的後續內容。訊息中只附上檔案開頭，需要更多內容時使用。

        Args:
            file_id: 檔案代號，例如 file1。
            start: 從第幾個字開始讀取，依先前提示填寫。
        """
        context = ctx.context
        text = context.attachments.get(file_id)
        if text is None:
            available = "、".join(context.attachments) or "無"
            return f"找不到檔案 {file_id}。可用的檔案：{available}"

        chunk, next_start = paginate(text, start, CHUNK_CHARS)
        note = f"（第 {max(0, start)} 字起，共 {len(text)} 字"
        note += f"；可用 start={next_start} 繼續讀取）" if next_start is not None else "；已讀到結尾）"
        return context.guard.wrap(f"attachment:{file_id}", f"{note}\n{chunk}")

    return read_attachment
