from .openai import OpenAIProvider


def AzureOpenAIProvider(model=None):  # noqa: N802
    return OpenAIProvider(model, azure=True)
