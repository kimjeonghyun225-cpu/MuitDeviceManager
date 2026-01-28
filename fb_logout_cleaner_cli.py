#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FB Logout Cleaner (CLI 버전)

기능:
- adb 자동 탐지 (내장 tools/mac/adb 우선, 없으면 PATH 의 adb)
- 연결된 Android 디바이스 목록 표시
- 선택한 디바이스들에 대해:
  - Facebook / Messenger / 주요 브라우저 데이터(pm clear) 삭제
  - 추가로 사용자가 지정한 패키지들도 pm clear 수행
"""

import os
import sys
import re
import subprocess
from pathlib import Path


# ================== 상수 정의 ==================
FACEBOOK_PKGS = ["com.facebook.katana"]
MESSENGER_PKGS = ["com.facebook.orca"]
BROWSER_FAMILIES = {
    "Chrome": ["com.android.chrome", "com.chrome.beta", "com.chrome.dev", "com.chrome.canary"],
    "SamsungInternet": ["com.sec.android.app.sbrowser", "com.sec.android.app.sbrowser.beta"],
    "Firefox": ["org.mozilla.firefox", "org.mozilla.firefox_beta", "org.mozilla.fenix", "org.mozilla.focus"],
    "Opera": ["com.opera.browser", "com.opera.mini.native", "com.opera.touch"],
    "Edge": ["com.microsoft.emmx", "com.microsoft.emmx.beta", "com.microsoft.emmx.dev"],
    "Brave": ["com.brave.browser"],
    "Kiwi": ["com.kiwibrowser.browser"],
    "Whale": ["com.naver.whale"],
}


# ================== adb / 도구 경로 탐지 (MultiDeviceManager와 동일 로직) ==================
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

if sys.platform.startswith("darwin"):
    _TOOLS_SUBDIR = os.path.join("tools", "mac")
elif sys.platform.startswith("win"):
    _TOOLS_SUBDIR = os.path.join("tools", "win")
else:
    _TOOLS_SUBDIR = os.path.join("tools", "linux")

LOCAL_BIN_DIR = os.path.join(APP_DIR, _TOOLS_SUBDIR)


def tool_path(cmd: str) -> str:
    """내장 tools 폴더 우선 사용 후, 없으면 원래 cmd 반환."""
    if not cmd:
        return cmd

    # 이미 경로 형태라면 그대로 사용
    if os.path.isabs(cmd) or (os.path.sep in cmd) or (os.path.altsep and os.path.altsep in cmd):
        return cmd

    candidates = []
    if sys.platform.startswith("win"):
        if not cmd.lower().endswith(".exe"):
            candidates.append(cmd + ".exe")
        candidates.append(cmd)
    else:
        candidates.append(cmd)

    for name in candidates:
        local = os.path.join(LOCAL_BIN_DIR, name)
        if os.path.exists(local):
            return local

    return cmd


def detect_adb(adb_entry: str = "") -> str:
    """
    adb 경로 우선순위:
    1) 인자로 받은 경로 (존재할 경우)
    2) 내장 tools 폴더의 adb
    3) PATH 에서 검색한 adb
    """
    adb_entry = adb_entry.strip()
    if adb_entry and Path(adb_entry).exists():
        return adb_entry

    local_adb = tool_path("adb")
    if local_adb and Path(local_adb).exists():
        return local_adb

    from shutil import which

    return which("adb") or ""


# ================== adb 관련 유틸 ==================
def run2(cmd):
    try:
        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
        )
        return completed.returncode, completed.stdout.strip()
    except Exception as e:
        return 1, f"ERROR: {e}"


def list_devices(adb_path: str):
    if not adb_path or not Path(adb_path).exists():
        print("[ADB] adb 경로가 유효하지 않습니다.")
        return []

    code, out = run2([adb_path, "devices"])
    if code != 0:
        print("[ADB] adb devices 실패:\n" + out)
        return []

    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
        else:
            print(f"[ADB] 연결 상태 점검 필요: {line}")

    if not devices:
        print("[ADB] 연결된 기기가 없습니다. USB 디버깅 허용 팝업을 확인하세요.")
    return devices


def get_user_ids(adb_path, serial):
    out_all = []
    code, out = run2([adb_path, "-s", serial, "shell", "pm", "list", "users"])
    if code == 0 and out:
        out_all.append(out)
    code2, out2 = run2([adb_path, "-s", serial, "shell", "cmd", "user", "list"])
    if code2 == 0 and out2:
        out_all.append(out2)
    text = "\n".join(out_all)
    ids = set()
    for tok in re.findall(r"\b(\d+)\b", text):
        ids.add(tok)
    return sorted(ids, key=lambda x: int(x)) or ["0"]


def package_exists_for_user(adb_path, serial, user_id, pkg):
    code, out = run2(
        [adb_path, "-s", serial, "shell", "cmd", "package", "list", "packages", "--user", user_id, pkg]
    )
    if code == 0 and ("package:" + pkg) in out:
        return True
    code2, out2 = run2(
        [adb_path, "-s", serial, "shell", "pm", "list", "packages", "--user", user_id, pkg]
    )
    return code2 == 0 and ("package:" + pkg) in out2


def clear_pkg_for_user(adb_path, serial, user_id, pkg):
    code, out = run2([adb_path, "-s", serial, "shell", "pm", "clear", "--user", user_id, pkg])
    if code == 0:
        print(f"  - {serial} (user {user_id}): pm clear {pkg} => 완료")
        return True
    code2, out2 = run2([adb_path, "-s", serial, "shell", "pm", "clear", pkg])
    if code2 == 0:
        print(f"  - {serial} (user {user_id}): pm clear {pkg} (fallback) => 완료")
        return True
    print(f"  - {serial} (user {user_id}): pm clear {pkg} => 실패 ({out or out2})")
    return False


# ================== 메인 로직 ==================
def main():
    print("=== FB Logout Cleaner (CLI) ===")
    print("이 도구는 Android 기기의 Facebook / Messenger / 브라우저 로그인을 초기화합니다.")
    print("주의: pm clear 는 해당 앱의 로컬 데이터(로그인, 캐시 등)를 모두 삭제합니다.\n")

    # 1) adb 자동 탐지
    adb_path = detect_adb()
    if not adb_path:
        print("[오류] adb 를 찾을 수 없습니다. PATH 또는 tools/mac/adb 를 확인하세요.")
        sys.exit(1)

    print(f"[ADB] 사용 경로: {adb_path}")

    # 2) 기기 목록 표시
    devices = list_devices(adb_path)
    if not devices:
        sys.exit(1)

    print("\n연결된 Android 기기:")
    for idx, serial in enumerate(devices, start=1):
        print(f"  {idx}. {serial}")

    # 3) 대상 기기 선택
    sel = input("\n초기화할 기기 번호를 입력하세요 (쉼표로 여러 개, 빈칸=전체): ").strip()
    target_serials = []
    if not sel:
        target_serials = devices[:]
    else:
        try:
            idx_list = [int(x.strip()) for x in sel.split(",") if x.strip()]
            for i in idx_list:
                if 1 <= i <= len(devices):
                    target_serials.append(devices[i - 1])
        except ValueError:
            print("[오류] 번호 입력 형식이 잘못되었습니다.")
            sys.exit(1)

    if not target_serials:
        print("[오류] 선택된 기기가 없습니다.")
        sys.exit(1)

    print("\n선택된 기기:")
    for s in target_serials:
        print(f"  - {s}")

    # 4) 옵션 선택
    def ask_yn(msg, default=True):
        base = "Y/n" if default else "y/N"
        ans = input(f"{msg} ({base}): ").strip().lower()
        if not ans:
            return default
        if ans in ["y", "yes", "ㅇ", "ㅛ"]:
            return True
        if ans in ["n", "no", "ㄴ", "ㅜ"]:
            return False
        return default

    opt_fb = ask_yn("Facebook 앱 데이터 삭제", default=True)
    opt_ms = ask_yn("Messenger 앱 데이터 삭제", default=True)
    opt_br = ask_yn("브라우저 데이터 삭제", default=True)

    extra_pkgs_input = input(
        "\n추가로 pm clear 할 패키지 이름을 입력하세요 (쉼표 구분, 예: com.facebook.lite,com.instagram.android / 없으면 Enter): "
    ).strip()
    extra_pkgs = [p.strip() for p in extra_pkgs_input.split(",") if p.strip()] if extra_pkgs_input else []

    print("\n=== 실행 옵션 요약 ===")
    print(f"  - Facebook:  {'ON' if opt_fb else 'OFF'} ({', '.join(FACEBOOK_PKGS)})")
    print(f"  - Messenger: {'ON' if opt_ms else 'OFF'} ({', '.join(MESSENGER_PKGS)})")
    print(f"  - 브라우저:  {'ON' if opt_br else 'OFF'}")
    if opt_br:
        fams = []
        for fam, pkgs in BROWSER_FAMILIES.items():
            fams.append(f"{fam}({len(pkgs)}개)")
        print("      " + ", ".join(fams))
    if extra_pkgs:
        print(f"  - 추가 패키지: {', '.join(extra_pkgs)}")
    else:
        print("  - 추가 패키지: 없음")

    ok = ask_yn("\n위 설정으로 초기화를 진행할까요?", default=True)
    if not ok:
        print("취소되었습니다.")
        sys.exit(0)

    # 5) 실제 pm clear 수행
    print("\n=== 초기화 시작 ===")
    for serial in target_serials:
        print(f"\n>>> [{serial}] 초기화 시작")
        user_ids = get_user_ids(adb_path, serial)
        print(f"  - 사용자 IDs: {', '.join(user_ids)}")

        # 추가 패키지 먼저
        for pkg in extra_pkgs:
            for uid in user_ids:
                if package_exists_for_user(adb_path, serial, uid, pkg):
                    clear_pkg_for_user(adb_path, serial, uid, pkg)
                else:
                    print(f"  - {serial} (user {uid}): {pkg} 설치 안됨")

        # Facebook
        if opt_fb:
            for uid in user_ids:
                for pkg in FACEBOOK_PKGS:
                    if package_exists_for_user(adb_path, serial, uid, pkg):
                        clear_pkg_for_user(adb_path, serial, uid, pkg)
                    else:
                        print(f"  - {serial} (user {uid}): {pkg} 설치 안됨")

        # Messenger
        if opt_ms:
            for uid in user_ids:
                for pkg in MESSENGER_PKGS:
                    if package_exists_for_user(adb_path, serial, uid, pkg):
                        clear_pkg_for_user(adb_path, serial, uid, pkg)
                    else:
                        print(f"  - {serial} (user {uid}): {pkg} 설치 안됨")

        # 브라우저
        if opt_br:
            for fam, pkgs in BROWSER_FAMILIES.items():
                for pkg in pkgs:
                    for uid in user_ids:
                        if package_exists_for_user(adb_path, serial, uid, pkg):
                            clear_pkg_for_user(adb_path, serial, uid, pkg)
                        else:
                            # 너무 로그가 많아질 수 있으므로, 설치 안된 패키지는 필요시만 출력하려면 주석 처리 가능
                            pass

        print(f">>> [{serial}] 완료 ✅")

    print("\n=== 모든 작업 완료 ===")
    print("주의: pm clear 로 인해 앱의 로컬 데이터가 모두 삭제되었습니다.")


if __name__ == "__main__":
    main()


