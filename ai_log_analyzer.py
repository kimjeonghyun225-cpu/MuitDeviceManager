
from __future__ import annotations

"""
ai_log_analyzer
----------------

MultiDeviceManager_2에서 사용하는 로그/크래시 GPT 요약 모듈.

역할:
- 파일/디렉터리 경로를 받아서 텍스트를 읽고,
- GPT API(OpenAI)를 통해 한국어로 요약된 분석 결과를 반환.

사용 예:
    from ai_log_analyzer import summarize_path_with_gpt

    summary = summarize_path_with_gpt(path, history_entry_dict)
"""

import os
from typing import Dict, List, Optional

from openai import OpenAI


_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    """지연 초기화된 OpenAI 클라이언트 반환 (OPENAI_API_KEY 필요)."""
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def _read_text_files_from_dir(
    directory: str,
    max_files: int = 5,
    per_file_max_chars: int = 4000,
) -> str:
    """
    디렉터리 안의 텍스트 파일들을 모아서 하나의 문자열로 합친다.
    - iOS crash 리포트 폴더 등에 사용.
    """
    parts: List[str] = []
    try:
        entries = sorted(
            f
            for f in os.listdir(directory)
            if os.path.isfile(os.path.join(directory, f))
        )
    except Exception as e:
        raise RuntimeError(f"크래시 디렉터리 읽기 실패: {e}")

    if not entries:
        raise RuntimeError("크래시 디렉터리에 요약할 파일이 없습니다.")

    for fname in entries[:max_files]:
        fpath = os.path.join(directory, fname)
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            continue

        # 너무 길면 뒷부분(최근 로그 기준)만 사용
        if len(content) > per_file_max_chars:
            content = content[-per_file_max_chars:]
        parts.append(f"===== {fname} =====\n{content}")

    if not parts:
        raise RuntimeError("크래시 디렉터리에서 읽을 수 있는 텍스트 파일을 찾지 못했습니다.")

    return "\n\n".join(parts)


def build_log_context_from_path(path: str, max_chars: int | None = None) -> str:
    """
    경로가 파일이면: 해당 파일 내용을 사용.
    경로가 디렉터리이면: 내부 텍스트 파일 몇 개를 합쳐서 사용.
    """
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            raise RuntimeError(f"로그 파일을 여는 중 오류: {e}")
    elif os.path.isdir(path):
        text = _read_text_files_from_dir(path)
    else:
        raise RuntimeError(f"파일/디렉터리를 찾을 수 없습니다: {path}")

    # max_chars가 지정된 경우에만 길이 제한을 적용
    if max_chars is not None and len(text) > max_chars:
        text = text[-max_chars:]
    return text


def summarize_log_with_gpt(log_text: str, meta: Dict[str, str]) -> str:
    """
    GPT를 사용해 로그/크래시 내용을 한국어로 분석.
    - 로그에 실제로 나타난 정보만 기반으로 판단하고,
      로그에 근거가 없는 추측은 절대 하지 않도록 프롬프트를 설계한다.
    - meta: {time, action, platform, id, name, file, result} 같은 히스토리 정보.
    """
    client = _get_client()

    prompt = (
        "다음은 모바일 앱의 로그/크래시 데이터입니다.\n"
        "오로지 로그 내용에 기반해서만, 아래 형식으로 한국어로 분석해 주세요.\n"
        "추측으로 채우지 말고, 로그에 명시되지 않은 부분은 '로그에서 확인 불가'라고 적어 주세요.\n\n"
        "1) 에러 유형 분류 (하나 선택):\n"
        "   - Graphics/Rendering 문제 (UI 깨짐, GPU/Surface/Renderer 관련 에러 등)\n"
        "   - Crash/Exception (Fatal signal, FATAL EXCEPTION, uncaught exception 등)\n"
        "   - Network/서버 통신 문제 (timeout, 4xx/5xx, DNS, 연결 실패 등)\n"
        "   - Performance/메모리 문제 (OutOfMemory, ANR, GC 관련 등)\n"
        "   - 권한/보안/설정 문제 (permission denied, 인증 실패 등)\n"
        "   - 기타(Other)\n"
        "   → '에러 유형: ...' 형태로 한 줄로 요약해 주세요.\n\n"
        "2) 핵심 에러/이벤트 요약 (Bullet 3~5개 이내):\n"
        "   - 실제 로그에 등장하는 에러 메시지/스택/코드만 사용\n"
        "   - 각 Bullet 끝에 관련 로그 키워드나 클래스/메서드명을 괄호로 함께 적기\n\n"
        "3) 추정 원인 (2~3개, 로그에 근거해서만 작성):\n"
        "   - 각 항목에 대해, 로그의 어떤 부분(에러 메시지, 스택 등)에 근거했는지 함께 설명\n"
        "   - 로그에 근거가 약하면 '신뢰도: 낮음' 같이 표시\n"
        "   - 근거가 없으면 '로그에서 명확한 원인을 알 수 없음'이라고 명시\n\n"
        "4) QA용 버그 티켓 초안:\n"
        "   - 제목: 한 줄 요약 (에러 유형 + 핵심 현상)\n"
        "   - 재현 단계: 로그에서 유추 가능한 범위에서만 서술 (모르면 '로그에서 재현 단계 추정 불가')\n"
        "   - 실제 결과: 실제 결과는 로그/크래시 내용에 기반해서만 작성\n\n"
        "5) 추가로 확인해야 할 로그/정보:\n"
        "   - 로그만으로 부족한 부분이 있다면, 어떤 추가 로그나 정보가 필요할지 간단히 제안\n\n"
        "[메타 정보]\n"
        f"- time: {meta.get('time')}\n"
        f"- action: {meta.get('action')}\n"
        f"- platform: {meta.get('platform')}\n"
        f"- device: {meta.get('name')} ({meta.get('id')})\n"
        f"- file: {meta.get('file')}\n"
        f"- result: {meta.get('result')}\n\n"
        "[로그 시작]\n"
        f"{log_text}\n"
        "[로그 끝]\n"
    )

    resp = client.chat.completions.create(
        model="gpt-5.1",
        messages=[
            {
                "role": "system",
                "content": "당신은 모바일 앱 QA/개발을 돕는 한국어 로그 분석 어시스턴트입니다.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def summarize_path_with_gpt(path: str, meta: Dict[str, str], max_chars: int | None = None) -> str:
    """
    외부에서 가장 많이 쓸 진입점:
    - path: LOG/CRASH 히스토리에서 넘어온 파일 또는 폴더 경로
    - meta: 히스토리 한 행(dict)
    """
    text = build_log_context_from_path(path, max_chars=max_chars)
    return summarize_log_with_gpt(text, meta)


def generate_issue_ticket_from_log(
    log_text: str,
    meta: Dict[str, str],
    attachments: List[Dict[str, str]],
) -> str:
    """
    로그 텍스트 + 메타 정보 + 첨부(스크린샷/동영상) 목록을 기반으로
    QA용 이슈 티켓 초안을 생성한다.
    attachments 예시:
        [{"action": "SCREENSHOT", "file": "..."},
         {"action": "SCREENRECORD", "file": "..."}]
    """
    client = _get_client()

    attach_str = ""
    if attachments:
        lines = []
        for a in attachments:
            atype = a.get("action", "")
            fname = a.get("file", "")
            lines.append(f"- {atype}: {fname}")
        attach_str = "\n[첨부 파일]\n" + "\n".join(lines) + "\n"

    prompt = (
        "다음 정보를 바탕으로 QA용 이슈 티켓 초안을 작성해 주세요.\n"
        "오로지 로그 내용과 메타/첨부 정보에 기반해서만 작성하고, 추측은 최소화해 주세요.\n\n"
        "[메타 정보]\n"
        f"- time: {meta.get('time')}\n"
        f"- action: {meta.get('action')}\n"
        f"- platform: {meta.get('platform')}\n"
        f"- device: {meta.get('name')} ({meta.get('id')})\n"
        f"- log file: {meta.get('file')}\n"
        f"- result: {meta.get('result')}\n"
        f"{attach_str}\n"
        "[로그 내용]\n"
        f"{log_text}\n"
        "[로그 끝]\n\n"
        "출력 형식은 아래와 같이 해 주세요.\n"
        "1) 제목(Title): [Compatibility] 를 맨 앞에 붙이고, 에러 유형과 핵심 현상을 한 줄로 요약\n"
        "2) 에러 유형(Category): Graphics / Crash / Network / Performance / Permission / Other 중 하나\n"
        "3) 현재 상황(Current Situation): 사용자가 실제로 겪는 문제 상황을 로그/크래시/스크린샷을 근거로 자세하게 설명\n"
        "4) 기대 상황(Expected Behavior): 사용자가 기대했을 정상 동작을 간단하고 명확하게 설명\n"
        "5) 재현 방법(Steps to Reproduce): 실제 테스트 관점에서 따라 하기 쉽게 단계별로 정리. 로그에서 알 수 없는 부분은 '로그에서 재현 단계 추정 불가'라고 명시\n"
    )

    resp = client.chat.completions.create(
        model="gpt-5.1",
        messages=[
            {
                "role": "system",
                "content": "당신은 모바일 앱 QA/개발 팀을 돕는 버그 티켓 작성 어시스턴트입니다.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def generate_issue_ticket_from_path(
    path: str,
    meta: Dict[str, str],
    attachments: List[Dict[str, str]],
    max_chars: int | None = None,
) -> str:
    """
    로그/크래시 파일 경로와 메타/첨부 정보를 받아 이슈 티켓 초안을 생성하는 헬퍼.
    """
    log_text = build_log_context_from_path(path, max_chars=max_chars)
    return generate_issue_ticket_from_log(log_text, meta, attachments)


def answer_question_about_log(
    log_text: str,
    question: str,
    meta: Dict[str, str],
    history: List[Dict[str, str]] | None = None,
) -> str:
    """
    단일 로그에 대해 Q&A 형식으로 질문에 답변.
    history: [{"role": "user"/"assistant", "content": "..."}] 형태의 이전 대화 목록.
    """
    client = _get_client()

    # 이전 Q&A를 텍스트로 간략히 정리
    history_text = ""
    if history:
        chunks = []
        for turn in history[-6:]:  # 최근 6개 턴만 사용
            role = turn.get("role", "user")
            prefix = "사용자" if role == "user" else "AI"
            chunks.append(f"{prefix}: {turn.get('content', '')}")
        if chunks:
            history_text = "[이전 Q&A]\n" + "\n".join(chunks) + "\n\n"

    prompt = (
        "당신은 모바일 앱 로그 분석을 도와주는 어시스턴트입니다.\n"
        "아래에 제공된 로그와 메타 정보, 이전 Q&A, 그리고 사용자의 질문만을 근거로 답변하세요.\n"
        "로그에 근거가 없는 추측은 하지 말고, 모르는 것은 '로그에서 확인 불가'라고 명시해 주세요.\n\n"
        "[메타 정보]\n"
        f"- time: {meta.get('time')}\n"
        f"- action: {meta.get('action')}\n"
        f"- platform: {meta.get('platform')}\n"
        f"- device: {meta.get('name')} ({meta.get('id')})\n"
        f"- file: {meta.get('file')}\n"
        f"- result: {meta.get('result')}\n\n"
        f"{history_text}"
        "[로그 내용]\n"
        f"{log_text}\n"
        "[로그 끝]\n\n"
        f"[사용자 질문]\n{question}\n"
    )

    resp = client.chat.completions.create(
        model="gpt-5.1",
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 모바일 앱 QA/개발 팀을 돕는 한국어 로그 분석 어시스턴트입니다. "
                    "로그에 실제로 나타난 정보만 사용하고, 과도한 추측은 피하세요."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    return resp.choices[0].message.content.strip()


def answer_question_about_path(
    path: str,
    question: str,
    meta: Dict[str, str],
    history: List[Dict[str, str]] | None = None,
    max_chars: int | None = None,
) -> str:
    """
    로그/크래시 파일 경로 + 질문을 받아 Q&A 답변을 생성.
    """
    log_text = build_log_context_from_path(path, max_chars=max_chars)
    return answer_question_about_log(log_text, question, meta, history or [])




