from __future__ import annotations

import httpx
import pytest

import demo


def test_select_sample_returns_only_the_chosen_video() -> None:
    sample = demo.select_sample("3")

    assert sample.name.startswith("B 站长视频")
    assert "BV1FM4y1c7yG" in sample.url
    with pytest.raises(demo.DemoError):
        demo.select_sample("5")


def test_sse_parser_handles_multiple_data_lines_and_final_event() -> None:
    lines = [
        "event: stage\n",
        'data: {"message":\n',
        'data: "正在处理"}\n',
        "\n",
        "event: done\n",
        'data: {"status":"ready"}\n',
    ]

    assert list(demo.iter_sse_events(lines)) == [
        ("stage", {"message": "正在处理"}),
        ("done", {"status": "ready"}),
    ]


def test_ensure_backend_reuses_healthy_service_without_starting_process() -> None:
    class HealthyClient:
        def get(self, *_args, **_kwargs):
            return type("Response", (), {"status_code": 200, "json": lambda self: {"status": "ok"}})()

    def forbidden_start(*_args, **_kwargs):
        raise AssertionError("不应启动第二个后端")

    assert demo.ensure_backend(
        HealthyClient(), demo.DEFAULT_BASE_URL, popen=forbidden_start
    ) is None


def test_ensure_backend_starts_and_returns_its_own_process() -> None:
    class StartingClient:
        calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            status = 200 if self.calls > 1 else 503
            return type("Response", (), {"status_code": status, "json": lambda self: {"status": "ok"}})()

    process = type("Process", (), {"poll": lambda self: None})()
    commands = []

    def start_process(command, **kwargs):
        commands.append((command, kwargs))
        return process

    assert demo.ensure_backend(
        StartingClient(),
        demo.DEFAULT_BASE_URL,
        popen=start_process,
        startup_timeout=1,
        poll_interval=0,
    ) is process
    assert commands[0][0][1:4] == ["-m", "uvicorn", "backend.main:app"]


def test_stop_backend_only_terminates_the_owned_process() -> None:
    events = []

    class RunningProcess:
        stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            events.append("terminate")
            self.stopped = True

        def wait(self, timeout):
            events.append(("wait", timeout))

    process = RunningProcess()
    demo.stop_backend(process)

    assert events == ["terminate", ("wait", 5)]


def test_api_failure_shows_backend_message_without_headers() -> None:
    response = httpx.Response(
        502,
        json={"detail": "视频平台解析失败，请确认链接可公开访问"},
    )

    with pytest.raises(demo.DemoError, match="视频平台解析失败"):
        demo._raise_for_api_error(response)


def test_answer_reset_replaces_unvalidated_stream_text(capsys: pytest.CaptureFixture[str]) -> None:
    class FakeResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self):
            return iter(
                [
                    "event: answer_delta",
                    'data: {"text":"临时回答"}',
                    "",
                    "event: answer_reset",
                    'data: {"text":"固定拒答"}',
                    "",
                    "event: done",
                    'data: {"conversation_id":"conv_test"}',
                    "",
                ]
            )

    class FakeClient:
        def stream(self, *_args, **_kwargs):
            return FakeResponse()

    success, _ = demo.ask_question(
        FakeClient(), demo.DEFAULT_BASE_URL, "video_test", "deep", "问题", None
    )
    output = capsys.readouterr().out

    assert success
    assert "固定拒答" in output
    assert "临时回答" not in output


def test_run_demo_only_parses_the_selected_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    selected_urls = []
    question_modes = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, url, json=None):
            if url.endswith("/api/session"):
                return FakeResponse({"session_id": "test"})
            selected_urls.append(json["url"])
            return FakeResponse(
                {"video_id": "video_test", "title": "测试样例", "duration": 30, "status": "ready"}
            )

    monkeypatch.setattr(demo, "ensure_backend", lambda *_args, **_kwargs: None)

    def fake_ask(_client, _base_url, _video_id, mode, _question, conversation_id):
        question_modes.append((mode, conversation_id))
        return True, "conv_test"

    monkeypatch.setattr(demo, "ask_question", fake_ask)

    result = demo.run_demo(
        input_fn=lambda _prompt: "2",
        client_factory=lambda **_kwargs: FakeClient(),
    )

    assert result == 0
    assert selected_urls == [demo.VIDEO_SAMPLES[1].url]
    assert [mode for mode, _ in question_modes] == ["summary", "deep"]
    assert question_modes[1][1] == "conv_test"
