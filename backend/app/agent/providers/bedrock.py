from .anthropic import AnthropicProvider


def BedrockProvider(model=None):  # noqa: N802
    return AnthropicProvider(model, bedrock=True)
