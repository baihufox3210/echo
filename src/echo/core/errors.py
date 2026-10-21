class EchoError(Exception):
    """Base exception for expected application failures."""

class AIServiceError(EchoError):
    pass

class AIOverloadedError(AIServiceError):
    """AI 服務暫時過載（429／5xx），稍後重試通常即可恢復。"""

class MemoryServiceError(EchoError):
    pass

class StorageError(EchoError):
    pass

class PromptError(EchoError):
    pass