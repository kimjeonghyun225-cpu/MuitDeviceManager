from __future__ import annotations

"""
ai_visual_analyzer
------------------

스크린샷 기반 Visual QA (텍스트 잘림/겹침/깨짐 탐지) 모듈.

- GPT‑4o(비전)으로 이미지 안의 UI 시각적 결함을 찾아 JSON으로 받고
- Pillow로 해당 영역에 빨간색 박스를 그린 주석 이미지를 생성한다.

의존성:
    pip install openai pillow

환경변수:
    OPENAI_VISION_API_KEY  (없으면 OPENAI_API_KEY 사용)
"""

import base64
import json
import os
import shutil
import subprocess
from typing import Dict, List, Optional

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont


def _get_vision_client() -> OpenAI:
    """
    비전 전용 OpenAI 클라이언트.
    - 우선 OPENAI_VISION_API_KEY, 없으면 OPENAI_API_KEY 사용.
    """
    api_key = os.getenv("OPENAI_VISION_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_VISION_API_KEY 또는 OPENAI_API_KEY 환경변수가 설정되지 않았습니다.")
    return OpenAI(api_key=api_key)


def _encode_image_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def extract_first_frame_to_image(video_path: str) -> str:
    """
    동영상 파일에서 첫 번째 프레임을 PNG 이미지로 추출한다.
    ffmpeg 필요.
    반환: 추출된 PNG 파일 경로
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg 명령을 찾을 수 없습니다. 영상 분석을 위해 ffmpeg를 설치해 주세요.")

    base, _ext = os.path.splitext(video_path)
    out_path = base + "_frame.png"

    cmd = [
        "ffmpeg",
        "-y",  # 덮어쓰기
        "-i",
        video_path,
        "-frames:v",
        "1",
        out_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffmpeg 실행 실패: {e.stderr or e.stdout}") from e

    if not os.path.exists(out_path):
        raise RuntimeError("ffmpeg 실행은 완료되었으나 출력 이미지가 생성되지 않았습니다.")

    return out_path


def analyze_ui_issues(image_path: str) -> List[Dict]:
    """
    GPT‑4o Vision으로 UI 시각적 결함(텍스트 잘림, 겹침, 깨진 그래픽)을 탐지.
    반환: [{"label": "...", "description": "...", "severity": "...",
           "box": {"x": int, "y": int, "w": int, "h": int}}]
    """
    client = _get_vision_client()
    img_b64 = _encode_image_to_base64(image_path)

    system_msg = (
        "당신은 모바일 앱의 UI 시각적 결함을 찾는 Visual QA 어시스턴트입니다. "
        "다음과 같은 이슈만 탐지하세요:\n"
        "- 잘린 텍스트 (문장이 박스 밖으로 나감, ... 으로 잘린 경우 등)\n"
        "- UI 요소 겹침 (텍스트/아이콘/버튼이 서로 겹치는 경우)\n"
        "- 깨진 그래픽 (아이콘/이미지 일부가 깨지거나 비정상적으로 보이는 경우)\n\n"
        "각 이슈에 대해 바운딩 박스를 픽셀 좌표로 반환하세요. "
        "이미지 좌상단이 (0,0)이고, x는 가로, y는 세로입니다.\n"
        "반드시 아래 JSON 형식으로만 답변하세요. 다른 텍스트는 포함하지 마세요.\n"
        '[{\"label\": \"TextClipped | Overlap | BrokenGraphics\", '
        '\"description\": \"한국어 설명\", '
        '\"severity\": \"minor | major | critical\", '
        '\"box\": {\"x\": 100, \"y\": 200, \"w\": 300, \"h\": 80}}]'
    )

    user_msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "이 이미지에서 위에서 정의한 UI 시각적 결함이 있다면 모두 찾아서 JSON으로만 반환해 주세요.",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            },
        ],
    }

    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_msg},
            user_msg,
        ],
        temperature=0.0,
    )

    content = resp.choices[0].message.content.strip()
    try:
        issues = json.loads(content)
        if not isinstance(issues, list):
            return []
        return issues
    except Exception:
        # JSON 파싱 실패 시 빈 목록
        return []


def draw_issues_on_image(
    image_path: str,
    issues: List[Dict],
    output_path: Optional[str] = None,
) -> str:
    """
    감지된 이슈들의 바운딩 박스를 빨간색으로 그려서 저장.
    output_path 미지정 시 *_annotated.png 로 저장.
    """
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("Arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for idx, issue in enumerate(issues, start=1):
        box = issue.get("box", {})
        x = int(box.get("x", 0))
        y = int(box.get("y", 0))
        w = int(box.get("w", 0))
        h = int(box.get("h", 0))
        label = issue.get("label", "Issue")

        # 빨간 박스
        draw.rectangle(
            [(x, y), (x + w, y + h)],
            outline="red",
            width=4,
        )

        # 상단 라벨
        text = f"{idx}. {label}"
        tw, th = draw.textsize(text, font=font)
        text_bg = (x, max(0, y - th - 2), x + tw + 4, y)
        draw.rectangle(text_bg, fill="red")
        draw.text((x + 2, y - th - 1), text, fill="white", font=font)

    if not output_path:
        if image_path.lower().endswith(".png"):
            output_path = image_path[:-4] + "_annotated.png"
        else:
            output_path = image_path + "_annotated.png"

    img.save(output_path)
    return output_path


