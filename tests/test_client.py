import httpx

from fakes import completion_body, transport
from routewise.client import TokenFactoryClient


def make_client(chat_handler) -> TokenFactoryClient:
    sleeps = []
    client = TokenFactoryClient(
        api_key="test",
        base_url="https://fake.tokenfactory/v1/",
        http_client=httpx.Client(transport=transport(chat_handler)),
        sleep=sleeps.append,
    )
    client.sleeps = sleeps
    return client


def test_rate_limit_is_retried_and_counted():
    calls = []

    def chat(body):
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "1.5"}, json={"error": {"message": "slow down"}})
        return httpx.Response(200, headers={"x-ratelimit-remaining-requests": "59"}, json=completion_body('{"category":"other"}'))

    client = make_client(chat)
    result = client.chat(
        model="nvidia/x",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=64,
        response_format={"type": "json_object"},
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    assert result.error is None
    assert result.attempts == 2
    assert client.sleeps == [1.5]
    assert result.content == '{"category":"other"}'
    assert (result.prompt_tokens, result.completion_tokens, result.cached_tokens) == (500, 60, 0)
    assert result.ratelimit == {"x-ratelimit-remaining-requests": "59"}
    assert result.latency_ms is not None and result.total_ms >= result.latency_ms
    # request options reach the wire unchanged
    assert calls[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert calls[-1]["response_format"] == {"type": "json_object"}


def test_client_error_is_not_retried():
    calls = []

    def chat(body):
        calls.append(body)
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    result = make_client(chat).chat(model="nvidia/x", messages=[{"role": "user", "content": "hi"}], max_tokens=8)
    assert len(calls) == 1
    assert result.attempts == 1 and result.content is None
    assert result.error.startswith("HTTP 400")


def test_gives_up_after_max_attempts():
    client = make_client(lambda body: httpx.Response(503, json={"error": "down"}))
    result = client.chat(model="nvidia/x", messages=[{"role": "user", "content": "hi"}], max_tokens=8)
    assert result.attempts == 4
    assert result.error.startswith("gave up after 4 attempts")
    assert len(client.sleeps) == 3


def test_streaming_records_ttft_decode_and_usage():
    from fakes import sse_body

    seen = []

    def chat(body):
        seen.append(body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=sse_body('{"category":"other"}', completion_tokens=7))

    result = make_client(chat).chat(model="nvidia/x", messages=[{"role": "user", "content": "hi"}], max_tokens=8, stream=True)
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True}
    assert result.error is None and result.streamed
    assert result.content == '{"category":"other"}' and result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens) == (500, 7)
    assert result.ttft_ms is not None and result.decode_ms is not None
    assert result.ttft_ms + result.decode_ms <= result.latency_ms + 1
