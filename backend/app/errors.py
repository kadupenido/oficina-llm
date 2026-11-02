class AppError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class NothingToRegenerate(AppError):
    def __init__(self) -> None:
        super().__init__(422, "Não há mensagem do usuário para tentar de novo.")


class MessageTooLong(AppError):
    def __init__(self) -> None:
        super().__init__(
            413,
            "A mensagem excede o limite de contexto do modelo.",
        )


class ConversationBusy(AppError):
    def __init__(self) -> None:
        super().__init__(409, "Já existe uma geração em andamento nesta conversa.")


class LLMUnavailable(AppError):
    def __init__(self, detail: str = "O servidor do modelo está indisponível.") -> None:
        super().__init__(503, detail)


class LLMTimeout(AppError):
    def __init__(self) -> None:
        super().__init__(504, "O modelo não respondeu dentro do tempo limite.")


class LLMCapacity(AppError):
    def __init__(self) -> None:
        super().__init__(
            503,
            "O servidor do modelo está no limite de conversas simultâneas.",
        )
