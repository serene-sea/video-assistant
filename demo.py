"""交互式体验一个视频样例的总结、普通问答和 Agent。"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import httpx


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_QUESTION = "这个视频主要讲了什么？"
DEFAULT_AGENT_QUESTION = "请结合视频内容梳理核心观点，并说明它们之间的关系。"


@dataclass(frozen=True)
class VideoSample:
    """菜单展示信息和真实视频链接集中放在一起，方便以后维护样例。"""

    name: str
    detail: str
    url: str


VIDEO_SAMPLES = (
    VideoSample(
        "B 站短视频（有字幕，8:43）",
        "展示平台字幕获取和短视频总结",
        "https://www.bilibili.com/video/BV1Nv411u7bW/",
    ),
    VideoSample(
        "B 站短视频（无字幕，5:21）",
        "没有平台字幕时会尝试本地语音识别",
        "https://www.bilibili.com/video/BV1qdHe6EEZC/",
    ),
    VideoSample(
        "B 站长视频（有字幕，40:41）",
        "长视频处理耗时和模型调用可能更多",
        "https://www.bilibili.com/video/BV1FM4y1c7yG/",
    ),
    VideoSample(
        "抖音视频（无字幕，7:52）",
        "使用现有通用解析流程，平台可访问性以实际结果为准",
        "https://www.douyin.com/jingxuan?modal_id=7674500414829628678",
    ),
)


class DemoError(RuntimeError):
    """把可读的体验脚本错误与底层异常区分开。"""


def select_sample(
    choice: str,
    samples: tuple[VideoSample, ...] = VIDEO_SAMPLES,
) -> VideoSample:
    """把菜单序号转换成唯一一个样例，避免误跑整组视频。"""
    normalized = choice.strip()
    if not normalized.isdigit():
        raise DemoError("请输入菜单中的数字。")
    index = int(normalized) - 1
    if index < 0 or index >= len(samples):
        raise DemoError(f"请选择 1 到 {len(samples)} 之间的数字。")
    return samples[index]


def iter_sse_events(lines: Iterable[str]) -> Iterable[tuple[str, object]]:
    """按空行切分 SSE；网络分块可能截断一行，所以事件要逐行累积。"""
    event_name = "message"
    data_lines: list[str] = []

    for raw_line in lines:
        line = raw_line.rstrip("\r\n")
        if not line:
            if data_lines:
                payload = "\n".join(data_lines)
                try:
                    data: object = json.loads(payload)
                except json.JSONDecodeError:
                    data = payload
                yield event_name, data
            event_name = "message"
            data_lines = []
            continue

        # SSE 注释通常是心跳，不属于业务事件。
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)

    # 某些响应结束时没有额外空行，最后一条仍需交给调用方。
    if data_lines:
        payload = "\n".join(data_lines)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = payload
        yield event_name, data


def _health_check(client: httpx.Client, base_url: str) -> bool:
    """只把项目健康检查成功的本地服务视为可复用后端。"""
    try:
        response = client.get(f"{base_url}/api/health", timeout=2)
        return response.status_code == 200 and response.json().get("status") == "ok"
    except (httpx.HTTPError, ValueError):
        return False


def ensure_backend(
    client: httpx.Client,
    base_url: str,
    *,
    project_root: Path = PROJECT_ROOT,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    startup_timeout: float = 45,
    poll_interval: float = 0.5,
) -> subprocess.Popen | None:
    """复用已运行服务；只有本脚本新启动的后端才会在退出时关闭。"""
    if _health_check(client, base_url):
        print("检测到本地后端已运行，将直接复用。")
        return None

    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(httpx.URL(base_url).port or 8000),
    ]
    try:
        # 不开启 reload，确保子进程句柄对应唯一服务进程，退出时可以准确清理。
        process = popen(command, cwd=project_root)
    except OSError as exc:
        raise DemoError("后端启动失败，请先安装 backend/requirements.txt 中的依赖。") from exc

    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if _health_check(client, base_url):
            print("本地后端已启动。")
            return process
        if process.poll() is not None:
            raise DemoError("后端进程提前退出，请检查终端中的启动错误。")
        time.sleep(poll_interval)

    stop_backend(process)
    raise DemoError("等待本地后端启动超时；请确认依赖已安装且端口未被占用。")


def stop_backend(process: subprocess.Popen | None) -> None:
    """仅结束 ensure_backend 返回的脚本自启动进程。"""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _raise_for_api_error(response: httpx.Response) -> None:
    """把后端 JSON 错误转换成简短中文提示，不显示请求头或密钥。"""
    if response.status_code < 400:
        return
    try:
        detail = response.json().get("detail") or response.json().get("message")
    except (ValueError, AttributeError):
        detail = None
    raise DemoError(str(detail or f"后端请求失败（HTTP {response.status_code}）。"))


def _read_sse_response(
    client: httpx.Client,
    url: str,
    *,
    json_body: dict,
    on_event: Callable[[str, object], None],
) -> None:
    """读取一个 SSE 请求直到结束，逐条把事件交给显示逻辑。"""
    try:
        with client.stream("POST", url, json=json_body, timeout=None) as response:
            if response.status_code >= 400:
                response.read()
                _raise_for_api_error(response)
            for event_name, data in iter_sse_events(response.iter_lines()):
                on_event(event_name, data)
    except httpx.HTTPError as exc:
        raise DemoError("连接后端失败；请检查后端是否仍在运行。") from exc


def _event_object(data: object) -> dict:
    return data if isinstance(data, dict) else {}


def process_video(client: httpx.Client, base_url: str, video_id: str) -> bool:
    """显示处理阶段和总结增量；返回值表示后端是否成功完成。"""
    succeeded = False

    def show_event(name: str, data: object) -> None:
        nonlocal succeeded
        payload = _event_object(data)
        if name == "stage":
            message = payload.get("message")
            if message:
                print(f"\n{message}")
        elif name == "summary_delta":
            text = payload.get("text", "")
            if text:
                print(text, end="", flush=True)
        elif name == "done":
            succeeded = payload.get("status") == "ready"
            print("\n总结已保存。" if succeeded else "\n处理没有完成。")
        elif name == "error":
            print(f"\n处理失败：{payload.get('message', '请检查后端状态和视频链接。')}")

    _read_sse_response(
        client,
        f"{base_url}/api/videos/{video_id}/process",
        json_body={"language": "zh"},
        on_event=show_event,
    )
    return succeeded


def ask_question(
    client: httpx.Client,
    base_url: str,
    video_id: str,
    mode: str,
    question: str,
    conversation_id: str | None,
) -> tuple[bool, str | None]:
    """调用普通问答或 Agent，并在两次体验间延续同一会话。"""
    result = {"ok": False, "conversation_id": conversation_id}
    answer_parts: list[str] = []
    citations: list[dict] = []

    def show_event(name: str, data: object) -> None:
        payload = _event_object(data)
        if name == "conversation":
            result["conversation_id"] = payload.get("conversation_id")
        elif name == "stage":
            message = payload.get("message")
            stage = payload.get("stage")
            if stage == "agent_tools":
                print("\n正在查找视频资料")
            elif stage == "agent_planning":
                print("\n正在分析问题")
            elif stage == "agent_answer":
                print("\n正在整理回答")
            elif message and mode != "deep":
                print(f"\n{message}")
        elif name == "tool_trace":
            # 工具开始事件会重复刷屏，只展示完成后的简短结果。
            if payload.get("status") not in {"started", "limit_reached"}:
                message = payload.get("message")
                if message:
                    print(f"\n资料查找：{message}")
        elif name == "answer_delta":
            text = payload.get("text", "")
            if text:
                answer_parts.append(text)
        elif name == "answer_reset":
            # 暂存到结束再显示，确保拒答重置时不会把无效的临时回答留在终端。
            answer_parts[:] = [str(payload.get("text", ""))]
        elif name == "citations" and isinstance(data, list) and data:
            citations[:] = [item for item in data if isinstance(item, dict)]
        elif name == "done":
            result["ok"] = True
            print(f"\n回答：\n{''.join(answer_parts).strip()}\n回答已保存。")
            if citations:
                print(f"答案已关联 {len(citations)} 处经后端校验的字幕依据。")
        elif name == "error":
            print(f"\n回答失败：{payload.get('message', '请检查模型配置后重试。')}")

    _read_sse_response(
        client,
        f"{base_url}/api/videos/{video_id}/chat",
        json_body={
            "question": question,
            "conversation_id": conversation_id,
            "mode": mode,
        },
        on_event=show_event,
    )
    return bool(result["ok"]), result["conversation_id"]


def _format_time(value: object) -> str:
    """把引用时间显示成易读的分秒格式。"""
    try:
        seconds = max(0, int(float(value)))
    except (TypeError, ValueError):
        return "--:--"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def run_demo(
    *,
    base_url: str = DEFAULT_BASE_URL,
    input_fn: Callable[[str], str] = input,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
    popen: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> int:
    """一次只处理菜单选中的视频，完整展示总结、普通问答和 Agent。"""
    print("\n请选择一个视频样例进行体验（每次只处理所选视频）：")
    for index, sample in enumerate(VIDEO_SAMPLES, start=1):
        print(f"  {index}. {sample.name} — {sample.detail}")
    print("  q. 退出")
    choice = input_fn("请输入选项：").strip()
    if choice.lower() == "q":
        return 0

    try:
        sample = select_sample(choice)
    except DemoError as exc:
        print(exc)
        return 2

    print(f"\n已选择：{sample.name}\n")
    server_process = None
    try:
        # 本地请求不应被系统 HTTP_PROXY 转发；否则健康检查可能连到代理而非本机。
        with client_factory(
            timeout=httpx.Timeout(120, connect=15), trust_env=False
        ) as client:
            server_process = ensure_backend(
                client,
                base_url,
                popen=popen,
            )
            # 每次运行使用自己的匿名 Session，视频权限仍由后端按 Session 检查。
            session_response = client.post(f"{base_url}/api/session")
            _raise_for_api_error(session_response)
            parsed = client.post(
                f"{base_url}/api/videos/parse",
                json={"url": sample.url},
            )
            _raise_for_api_error(parsed)
            video = parsed.json()
            video_id = video["video_id"]
            print(f"视频：{video.get('title') or sample.name}")
            print(f"时长：{_format_time(video.get('duration'))}")

            if video.get("status") != "ready":
                if not process_video(client, base_url, video_id):
                    return 1
            else:
                print("该视频已有保存的总结，将直接复用。")

            question = input_fn(
                f"\n普通问答问题（直接回车使用“{DEFAULT_QUESTION}”）："
            ).strip() or DEFAULT_QUESTION
            print("\n普通问答：")
            _, conversation_id = ask_question(
                client, base_url, video_id, "summary", question, None
            )

            agent_question = input_fn(
                f"\nAgent 问题（直接回车使用“{DEFAULT_AGENT_QUESTION}”）："
            ).strip() or DEFAULT_AGENT_QUESTION
            print("\n深入分析 Agent：")
            ask_question(
                client,
                base_url,
                video_id,
                "deep",
                agent_question,
                conversation_id,
            )
        return 0
    except DemoError as exc:
        print(f"\n体验未完成：{exc}")
        return 1
    except KeyboardInterrupt:
        print("\n已停止本次体验。")
        return 130
    except (KeyError, ValueError, httpx.HTTPError) as exc:
        print(f"\n体验未完成：{exc}")
        return 1
    finally:
        stop_backend(server_process)


def main() -> int:
    parser = argparse.ArgumentParser(description="交互式体验一个 Video Assistant 视频样例")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="本地后端地址，默认 http://127.0.0.1:8000",
    )
    args = parser.parse_args()
    return run_demo(base_url=args.base_url.rstrip("/"))


if __name__ == "__main__":
    raise SystemExit(main())
