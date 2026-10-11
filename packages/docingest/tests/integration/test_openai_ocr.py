"""OpenAICompatibleOcr against a scripted, local OpenAI-compatible server.

The fake server parses every request the way a strict server would (endpoint, model
name, a PNG data URL no larger than the profile's max_side, the prompt) and answers
from a script, so retries, the finish_reason="length" ladder and auth failures are
exercised over real HTTP without any model.
"""

from __future__ import annotations

import base64
import io
import json
import socket
import threading
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from PIL import Image, ImageDraw

from docingest.adapters.ocr.openai_compat import OcrServerError, OpenAICompatibleOcr
from docingest.adapters.ocr.profiles import OcrProfile, profile_for
from docingest.bootstrap import Container, build
from docingest.domain.errors import DocingestError, OcrUnavailableError
from docingest.domain.models import ModelRef, PageMethod

MODEL = ModelRef(repo_id="org/vl-model", revision="a" * 40)
SERVED = "/models/org--vl-model/snapshots/aaaa"  # e.g. mlx_vlm.server's local path
MAX_SIDE = 800
PROFILE = profile_for("markdown", max_side=MAX_SIDE)
KEY_ENV = "DOCINGEST_TEST_OCR_KEY"


# ----------------------------------------------------------------- fake server
@dataclass
class Reply:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    delay_s: float = 0.0


def completion(text: str, finish: str = "stop", tokens: int = 7) -> Reply:
    return Reply(
        body={
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "model": SERVED,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish,
                }
            ],
            "usage": {
                "prompt_tokens": 90,
                "completion_tokens": tokens,
                "total_tokens": 90 + tokens,
            },
        }
    )


def error(status: int, message: str = "nope", **headers: str) -> Reply:
    return Reply(status, {"error": {"message": message, "type": "test_error"}}, headers)


@dataclass
class Seen:
    body: dict[str, Any]
    headers: dict[str, str]
    image_size: tuple[int, int]


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):  # client gave up (timeout test)
        pass


class FakeOpenAIServer:
    """127.0.0.1:<free port>; validates each request, then pops the next scripted reply."""

    def __init__(
        self,
        *replies: Reply,
        model: str = SERVED,
        profile: OcrProfile = PROFILE,
        api_key: str | None = None,
    ):
        self.replies = list(replies)
        self.model = model
        self.profile = profile
        self.api_key = api_key
        self.seen: list[Seen] = []
        self.rejections: list[str] = []  # malformed requests (answered with HTTP 400)
        self._lock = threading.Lock()

    def __enter__(self) -> FakeOpenAIServer:
        self.httpd = _QuietServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/v1"

    def validate(self, path: str, headers: dict[str, str], raw: bytes) -> Seen:
        assert path == "/v1/chat/completions", path
        assert headers.get("Content-Type") == "application/json"
        body = json.loads(raw)
        assert body["model"] == self.model, body["model"]
        assert isinstance(body["temperature"], int | float)
        assert isinstance(body["max_tokens"], int) and body["max_tokens"] > 0
        (message,) = body["messages"]
        assert message["role"] == "user"
        parts = {p["type"]: p for p in message["content"]}
        assert set(parts) == {"image_url", "text"}, set(parts)
        assert parts["text"]["text"] == self.profile.prompt
        url = parts["image_url"]["image_url"]["url"]
        prefix = "data:image/png;base64,"
        assert url.startswith(prefix), url[:40]
        with Image.open(io.BytesIO(base64.b64decode(url[len(prefix) :], validate=True))) as img:
            assert img.format == "PNG" and img.mode == "RGB", (img.format, img.mode)
            size = img.size
        assert max(size) <= self.profile.max_side, size
        return Seen(body, headers, size)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # keep pytest output clean
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                headers = dict(self.headers.items())
                try:
                    seen = server.validate(self.path, headers, raw)
                except Exception as e:
                    server.rejections.append(f"{type(e).__name__}: {e}")
                    self.reply(error(400, f"malformed request: {e}"))
                    return
                with server._lock:
                    server.seen.append(seen)
                    if (
                        server.api_key
                        and headers.get("Authorization") != f"Bearer {server.api_key}"
                    ):
                        reply = error(401, "Incorrect API key provided")
                    elif server.replies:
                        reply = server.replies.pop(0)
                    else:
                        reply = error(418, "script exhausted")
                time.sleep(reply.delay_s)
                self.reply(reply)

            def reply(self, reply: Reply) -> None:
                payload = json.dumps(reply.body).encode()
                self.send_response(reply.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                for name, value in reply.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(payload)

        return Handler


@pytest.fixture
def serve():
    with ExitStack() as stack:
        yield lambda *replies, **kw: stack.enter_context(FakeOpenAIServer(*replies, **kw))


def engine(base_url: str, profile: OcrProfile = PROFILE, **kw) -> OpenAICompatibleOcr:
    kw.setdefault("served_model", SERVED)
    kw.setdefault("backoff_s", 0)
    return OpenAICompatibleOcr(MODEL, profile, base_url=base_url, **kw)


def page(size: tuple[int, int] = (2400, 1600), mode: str = "RGB") -> Image.Image:
    img = Image.new(mode, size, "white")
    ImageDraw.Draw(img).text((100, 100), "A mathematical theory of communication", fill="black")
    return img


# ----------------------------------------------------------------------- tests
def test_transcribes_and_maps_the_reply(serve):
    server = serve(completion("```markdown\n# Title\n\nBody text\n```", tokens=12))
    res = engine(server.base_url).transcribe(page((2400, 1600)))
    assert server.rejections == []
    assert res.text == "# Title\n\nBody text"  # profile post-processing applied
    assert (res.gen_tokens, res.finish_reason) == (12, "stop")
    assert res.seconds >= 0 and res.peak_memory_gb is None
    (req,) = server.seen
    assert req.image_size == (800, 533)  # fit_image: longest side = profile max_side
    assert req.body["temperature"] == 0.0
    assert req.body["max_tokens"] == PROFILE.max_tokens
    assert req.body["repetition_penalty"] == PROFILE.repetition_penalty
    assert "Authorization" not in req.headers


def test_config_overrides_reach_the_request(serve):
    server = serve(completion("ok"))
    ocr = engine(server.base_url, max_tokens=123, temperature=0.1, repetition_penalty=1.0)
    ocr.transcribe(page((300, 200), mode="RGBA"))  # transparent input is flattened to RGB
    assert server.rejections == []
    (req,) = server.seen
    assert (req.body["max_tokens"], req.body["temperature"]) == (123, 0.1)
    assert "repetition_penalty" not in req.body  # 1.0 = off: not sent to strict APIs
    assert req.image_size == (300, 200)  # never upscaled


def test_served_model_defaults_to_repo_id(serve):
    server = serve(completion("ok"), model=MODEL.repo_id)
    assert engine(server.base_url, served_model=None).transcribe(page()).text == "ok"
    assert server.rejections == []


def test_profile_chat_kwargs_are_forwarded(serve):
    glm = profile_for("glm-ocr", max_side=640)
    server = serve(completion("<think>reading</think>Plain text"), profile=glm)
    res = engine(server.base_url, profile=glm).transcribe(page())
    assert server.rejections == [] and res.text == "Plain text"
    body = server.seen[0].body
    assert body["chat_template_kwargs"] == {"enable_thinking": True}  # vLLM / SGLang
    assert body["enable_thinking"] is True  # mlx_vlm.server
    assert (body["max_tokens"], body["repetition_penalty"]) == (8192, 1.1)


def test_429_and_5xx_are_retried_with_backoff(serve):
    server = serve(
        error(503, "loading model"),
        error(429, "slow down", **{"Retry-After": "0"}),
        completion("ok"),
    )
    res = engine(server.base_url, retries=2).transcribe(page())
    assert res.text == "ok" and len(server.seen) == 3


def test_gives_up_after_the_retry_budget(serve):
    server = serve(error(503), error(502), error(500, "CUDA out of memory"))
    with pytest.raises(OcrServerError, match=r"HTTP 500.*CUDA out of memory") as info:
        engine(server.base_url, retries=2).transcribe(page())
    assert info.value.status == 500 and isinstance(info.value, DocingestError)
    assert len(server.seen) == 3


def test_auth_errors_fail_fast_without_leaking_the_key(serve, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-wrong-key")
    server = serve(completion("never sent"), api_key="sk-right-key")
    with pytest.raises(OcrServerError, match=r"HTTP 401.*api_key_env") as info:
        engine(server.base_url, api_key_env=KEY_ENV, retries=3).transcribe(page())
    assert info.value.status == 401 and len(server.seen) == 1  # 4xx: no retry
    assert "sk-wrong-key" not in str(info.value)


def test_bearer_key_comes_from_the_named_env_var(serve, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-right-key")
    server = serve(completion("ok"), api_key="sk-right-key")
    ocr = engine(server.base_url, api_key_env=KEY_ENV)
    assert ocr.transcribe(page()).text == "ok"
    assert server.seen[0].headers["Authorization"] == "Bearer sk-right-key"
    assert "sk-right-key" not in ocr.fingerprint + repr(vars(ocr))


def test_unset_key_env_fails_before_sending(serve, monkeypatch):
    monkeypatch.delenv(KEY_ENV, raising=False)
    server = serve(completion("never sent"))
    with pytest.raises(OcrServerError, match=KEY_ENV):
        engine(server.base_url, api_key_env=KEY_ENV).transcribe(page())
    assert server.seen == []


def test_length_finish_climbs_the_retry_ladder(serve):
    server = serve(completion("loop " * 100, "length", 4096), completion("# Done", "stop", 20))
    res = engine(server.base_url).transcribe(page())
    assert (res.text, res.finish_reason, res.gen_tokens) == ("# Done", "stop", 20)
    sent = [(r.body["temperature"], r.body["repetition_penalty"]) for r in server.seen]
    assert sent == [(0.0, 1.05), (0.2, 1.15)]


def test_ladder_stops_after_three_attempts(serve):
    # The third rung (0.5, 1.25) rescued 3 of the 6 corpus pages that reached it.
    server = serve(*[completion("loop " * 100, "length", 4096)] * 4)
    res = engine(server.base_url).transcribe(page())
    assert res.finish_reason == "length" and res.gen_tokens == 4096 and res.attempts == 3
    sent = [(r.body["temperature"], r.body["repetition_penalty"]) for r in server.seen]
    assert sent == [(0.0, 1.05), (0.2, 1.15), (0.5, 1.25)]
    assert len(server.replies) == 1  # the fourth scripted reply was never requested


def test_a_dense_page_that_ran_out_of_tokens_is_asked_again_with_more_room(serve):
    table = "".join(f"| {i} | {i * 3} |\n" for i in range(400))
    server = serve(completion(table, "length", 4096), completion(table + "| end |", "stop", 5000))
    res = engine(server.base_url).transcribe(page())
    assert res.text.endswith("| end |") and res.finish_reason == "stop"
    assert [r.body["max_tokens"] for r in server.seen] == [4096, 8192]
    assert {(r.body["temperature"], r.body["repetition_penalty"]) for r in server.seen} == {
        (0.0, 1.05)
    }  # the same setting: the text was fine, it just did not fit
    assert (res.attempts, res.first_finish_reason, res.total_gen_tokens) == (2, "length", 9096)


def test_a_loop_keeps_the_token_budget(serve):
    server = serve(completion("loop " * 100, "length", 4096), completion("# Done", "stop", 20))
    engine(server.base_url).transcribe(page())
    assert [r.body["max_tokens"] for r in server.seen] == [4096, 4096]


def test_a_page_still_cut_off_with_the_larger_limit_ends_the_ladder_with_the_longer_answer(serve):
    # A stricter sampling at half the room cannot finish a table that did not fit in 8192;
    # a third request would cost another 115 s and answer with less.
    short = "".join(f"| {i} | {i * 3} |\n" for i in range(400))
    long = "".join(f"| {i} | {i * 3} |\n" for i in range(800))
    server = serve(
        completion(short, "length", 4096), completion(long, "length", 8192), completion("x", "stop")
    )
    res = engine(server.base_url).transcribe(page())
    assert res.text.strip() == long.strip() and res.attempts == 2
    assert [r.body["max_tokens"] for r in server.seen] == [4096, 8192]
    assert len(server.replies) == 1  # the third reply was never requested


def test_a_server_that_cannot_hold_the_raised_limit_keeps_the_truncated_page(serve):
    # Old risk: HTTP 400 on max_tokens 8192 failed the whole document; before the raise the
    # page was kept, truncated.
    table = "".join(f"| {i} | {i * 3} |\n" for i in range(400))
    server = serve(
        completion(table, "length", 4096), error(400, "context length is 4096, you asked for 9000")
    )
    res = engine(server.base_url).transcribe(page())
    assert res.text.startswith("| 0 | 0 |") and res.finish_reason == "length"
    assert res.attempts == 1 and [r.body["max_tokens"] for r in server.seen] == [4096, 8192]


def test_a_400_at_the_configured_limit_is_still_an_error(serve):
    server = serve(error(400, "bad request"))
    with pytest.raises(OcrServerError, match="HTTP 400"):
        engine(server.base_url).transcribe(page())


def test_a_profile_near_the_ceiling_keeps_its_stricter_retry(serve):
    # Nanonets runs at 8000 tokens: a raise to 8192 would gain 2% and spend the only retry.
    cut = "".join(f"| {i} | {i * 3} |\n" for i in range(400))
    profile = profile_for("nanonets", max_side=MAX_SIDE)
    server = serve(
        completion(cut, "length", 8000), completion("# Done", "stop", 20), profile=profile
    )
    engine(server.base_url, profile).transcribe(page())
    assert [(r.body["max_tokens"], r.body["temperature"]) for r in server.seen] == [
        (8000, 0.0),
        (8000, 0.2),
    ]


def test_an_image_without_a_size_has_no_length_bound(serve):
    long = " ".join(f"word{i}" for i in range(2000))
    server = serve(completion(long, "stop", 3000))
    res = engine(server.base_url).transcribe(page((300, 100)))
    assert res.attempts == 1 and res.text.endswith("word1999")


def test_text_too_long_for_a_small_image_is_retried(serve):
    server = serve(completion("word " * 400, "stop", 500), completion("# Done", "stop", 20))
    small = page((300, 100))
    small.info["dpi"] = (150, 150)  # 2 x 0.7 inch
    res = engine(server.base_url).transcribe(small)
    assert res.text == "# Done" and res.attempts == 2


@pytest.mark.parametrize("body", [{"object": "error"}, {"choices": []}, "not an object"])
def test_malformed_reply_is_a_domain_error(serve, body):
    server = serve(Reply(200, body))
    with pytest.raises(OcrServerError, match="choices"):
        engine(server.base_url).transcribe(page())


def test_list_content_parts_are_joined(serve):
    reply = completion("")
    reply.body["choices"][0]["message"]["content"] = [
        {"type": "text", "text": "# Part one"},
        {"type": "text", "text": "\n\npart two"},
    ]
    server = serve(reply)
    assert engine(server.base_url).transcribe(page()).text == "# Part one\n\npart two"


def test_unreachable_server_is_a_domain_error():
    with socket.socket() as s:  # a port that was free a moment ago: connection refused
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(OcrServerError, match="unreachable after 2 attempt"):
        engine(f"http://127.0.0.1:{port}/v1", retries=1).transcribe(page())


def test_timeout_is_reported_and_not_retried(serve):
    slow = completion("too late")
    slow.delay_s = 1.0
    server = serve(slow, completion("never"))
    with pytest.raises(OcrServerError, match=r"timed out after 0\.2s"):
        engine(server.base_url, timeout_s=0.2, retries=3).transcribe(page())
    assert len(server.seen) == 1


def test_redirects_are_not_followed(serve):
    server = serve(Reply(307, {}, {"Location": "http://127.0.0.1:9/v1/chat/completions"}))
    with pytest.raises(OcrServerError) as info:
        engine(server.base_url).transcribe(page())
    assert info.value.status == 307


def test_fingerprint_is_stable_and_tracks_output_settings():
    base: dict[str, Any] = {"base_url": "http://gpu:8000/v1", "served_model": "m"}
    fp = OpenAICompatibleOcr(MODEL, PROFILE, **base).fingerprint
    assert fp.startswith("openai-compatible ")
    assert f"{MODEL.repo_id}@{MODEL.revision}" in fp and "http://gpu:8000/v1" in fp
    # transport settings and a trailing slash cannot change the text
    same = {"base_url": "http://gpu:8000/v1/", "timeout_s": 5, "retries": 0, "api_key_env": "X"}
    assert OpenAICompatibleOcr(MODEL, PROFILE, **{**base, **same}).fingerprint == fp
    for change in (
        {"base_url": "http://other:8000/v1"},
        {"served_model": "m2"},
        {"temperature": 0.3},
        {"max_tokens": 10},
        {"repetition_penalty": 1.2},
        {"dpi": 200},
    ):
        assert OpenAICompatibleOcr(MODEL, PROFILE, **{**base, **change}).fingerprint != fp
    other_prompt = profile_for("markdown", "Only the title.", MAX_SIDE)
    assert OpenAICompatibleOcr(MODEL, other_prompt, **base).fingerprint != fp
    other_rev = ModelRef(repo_id=MODEL.repo_id, revision="b" * 40)
    assert OpenAICompatibleOcr(other_rev, PROFILE, **base).fingerprint != fp


def test_rejects_non_http_base_url():
    with pytest.raises(ValueError, match="http"):
        OpenAICompatibleOcr(MODEL, PROFILE, base_url="file:///etc/passwd")


def test_config_alone_swaps_the_ocr_engine(cfg, serve, tmp_path):
    server = serve(completion("# Scanned page\n\nHello from the server", tokens=9))
    cfg.adapters.ocr = "openai-compatible"
    cfg.ocr.base_url = server.base_url
    cfg.ocr.served_model = SERVED
    cfg.ocr.max_side = MAX_SIDE
    ocr = build("ocr", cfg)
    assert isinstance(ocr, OpenAICompatibleOcr)
    assert ocr.model == ModelRef(repo_id=cfg.ocr.repo_id, revision=cfg.ocr.revision)
    assert ocr.dpi == cfg.ocr.dpi

    png = tmp_path / "scan.png"
    page((1200, 900)).save(png)
    svc = Container(cfg, log=lambda _: None).ingest
    doc = svc.ingest(png)
    assert server.rejections == []
    rec = doc.manifest.pages[0]
    assert rec.method == PageMethod.VLM_OCR and rec.engine == ocr.fingerprint
    assert (rec.gen_tokens, rec.finish_reason) == (9, "stop")
    assert "Hello from the server" in svc.store.markdown(doc)


# ------------------------------------------------------------------------ check()
class _Status(BaseHTTPRequestHandler):
    status = 401

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.send_response(self.status)
        self.send_header("Content-Length", "0")
        self.end_headers()


def _status_server(status: int):
    handler = type("H", (_Status,), {"status": status})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    return httpd


def test_check_passes_when_the_server_answers_whatever_it_says(serve):
    server = serve()  # the scripted server only knows POST: GET /models is a 501
    engine(server.base_url).check()
    for status in (200, 404):
        httpd = _status_server(status)
        try:
            engine(f"http://127.0.0.1:{httpd.server_address[1]}/v1").check()
        finally:
            httpd.shutdown()
            httpd.server_close()


def test_check_names_the_setting_and_the_way_out_when_nobody_answers():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # closed again: nothing listens here
    with pytest.raises(OcrUnavailableError) as err:
        engine(f"http://127.0.0.1:{port}/v1").check()
    message = str(err.value)
    assert f"127.0.0.1:{port}" in message and "[ocr] base_url" in message
    assert "-c" in message and "mlx" in message


def test_check_reports_a_refused_key_and_an_unset_key_variable(monkeypatch):
    httpd = _status_server(401)
    try:
        ocr = engine(f"http://127.0.0.1:{httpd.server_address[1]}/v1")
        with pytest.raises(OcrUnavailableError, match="api_key_env"):
            ocr.check()
    finally:
        httpd.shutdown()
        httpd.server_close()
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(OcrUnavailableError, match=KEY_ENV):
        engine("http://127.0.0.1:9/v1", api_key_env=KEY_ENV).check()


def test_check_sends_the_key_and_loads_nothing(serve, monkeypatch):
    monkeypatch.setenv(KEY_ENV, "sk-test")
    seen = []

    class Auth(_Status):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            super().do_GET()

    Auth.status = 200
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Auth)
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
    ).start()
    try:
        engine(f"http://127.0.0.1:{httpd.server_address[1]}/v1", api_key_env=KEY_ENV).check()
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert seen == [("/v1/models", "Bearer sk-test")]
