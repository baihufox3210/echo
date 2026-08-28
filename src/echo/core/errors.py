class EchoError(Exception):
    """Base exception for expected application failures."""

class AIServiceError(EchoError):
    pass

class MemoryServiceError(EchoError):
    pass

class StorageError(EchoError):
    pass

class PromptError(EchoError):
    pass