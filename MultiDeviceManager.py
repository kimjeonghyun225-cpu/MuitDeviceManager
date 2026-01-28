#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-Device QA Tool (PySide6 v2)

주요 기능:
- Android + iOS 디바이스 목록 (다중 선택)
- 설치 시 기본적으로 "기존 앱 삭제 → 새 설치" 시나리오 지원
- 삭제 전용 버튼(Android / iOS) 별도 제공
- APK/IPA 단일 및 폴더(여러 파일) 배치 설치
- Android: 패키지 목록 조회, 로그캡처(logcat -d), 스크린샷
- iOS: 앱 목록 조회, syslog 캡처, crashreport 추출, 스크린샷
- 히스토리(UNINSTALL / INSTALL / RUN / LOG / CRASH / SCREENSHOT) CSV 저장
"""

import os
import sys
import csv
import time
import subprocess
import threading
import webbrowser
import shutil

from PySide6 import QtCore, QtWidgets
from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QFileDialog,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QPlainTextEdit,
    QMessageBox,
    QCheckBox,
    QSpinBox,
    QGroupBox,
    QHeaderView,
    QListWidget,
    QDialog,
    QMenu,
)


# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------

# ------------------------------------------------------------
# 설정
# ------------------------------------------------------------

CFGUTIL_PATH = "/Applications/Apple Configurator.app/Contents/MacOS/cfgutil"


# ------------------------------------------------------------
# 공통 유틸 함수
# ------------------------------------------------------------

# PyInstaller로 패키징된 경우(_MEIPASS)와 개발 환경 모두에서
# 동일하게 동작하도록, 현재 앱 디렉터리와 내장 CLI 경로를 계산한다.
APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

if sys.platform.startswith("darwin"):
    _TOOLS_SUBDIR = os.path.join("tools", "mac")
elif sys.platform.startswith("win"):
    _TOOLS_SUBDIR = os.path.join("tools", "win")
else:
    _TOOLS_SUBDIR = os.path.join("tools", "linux")

LOCAL_BIN_DIR = os.path.join(APP_DIR, _TOOLS_SUBDIR)


def tool_path(cmd: str) -> str:
    """
    내장 tools 폴더에 포함된 CLI 바이너리가 있으면 그 경로를 반환하고,
    없으면 원래 cmd 그대로 돌려준다.
    - adb, idevice_id, ideviceinfo, ideviceinstaller, idevicesyslog 등 bundling 용도.
    """
    if not cmd:
        return cmd

    # 이미 경로 형태(절대/상대)라면 그대로 사용
    if os.path.isabs(cmd) or (os.path.sep in cmd) or (os.path.altsep and os.path.altsep in cmd):
        return cmd

    # 플랫폼별 후보 이름 구성
    candidates = []
    if sys.platform.startswith("win"):
        # .exe 확장자 우선 탐색
        if not cmd.lower().endswith(".exe"):
            candidates.append(cmd + ".exe")
        candidates.append(cmd)
    else:
        candidates.append(cmd)

    for name in candidates:
        local = os.path.join(LOCAL_BIN_DIR, name)
        if os.path.exists(local):
            return local

    # 내장 바이너리가 없으면 원래 이름 사용 (시스템 PATH 의존)
    return cmd


def run_cmd(args, timeout=60) -> str:
    """커맨드 실행 후 stdout 반환 (에러도 stdout으로 합침)."""
    try:
        # 리스트/튜플인 경우 첫 번째 토큰을 tool_path로 해석해 내장 CLI 우선 사용
        if isinstance(args, (list, tuple)) and args:
            args = list(args)
            args[0] = tool_path(str(args[0]))

        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        return proc.stdout
    except Exception as e:
        return f"[ERROR] {args}: {e}"

def get_android_devices():
    """adb devices 기반 Android 디바이스 정보 수집."""
    devices = []
    try:
        out = run_cmd(["adb", "devices"])
        print(f"[DEBUG] adb devices 결과: {repr(out)}")
        lines = out.splitlines()
        if len(lines) <= 1:
            print("[DEBUG] Android 기기 없음")
            return devices

        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serial = parts[0]
                print(f"[DEBUG] Android 기기 발견: {serial}")

                model = run_cmd(["adb", "-s", serial, "shell", "getprop", "ro.product.model"]).strip()
                version = run_cmd(["adb", "-s", serial, "shell", "getprop", "ro.build.version.release"]).strip()

                # 아키텍처 정보 수집
                abi = run_cmd(["adb", "-s", serial, "shell", "getprop", "ro.product.cpu.abi"]).strip()
                abilist = run_cmd(["adb", "-s", serial, "shell", "getprop", "ro.product.cpu.abilist"]).strip()
                
                # 주요 ABI 추출 (abilist가 있으면 첫 번째 것 사용)
                if abilist:
                    primary_abi = abilist.split(",")[0].strip()
                else:
                    primary_abi = abi
                
                # 64-bit / 32-bit 판별
                arch_label = "Unknown"
                if "64" in (abilist or abi):
                    arch_label = "64-bit"
                elif abi or abilist:
                    arch_label = "32-bit"
                
                # OS 버전과 아키텍처 분리
                os_version = version or "unknown"
                
                # 아키텍처 정보: 비트 + ABI
                arch_info = arch_label
                if primary_abi:
                    arch_info = f"{primary_abi}"

                devices.append({
                    "platform": "Android",
                    "id": serial,
                    "name": model if model else serial,
                    "os_version": os_version,
                    "arch": arch_info,
                })
                print(f"[DEBUG] Android 기기 추가: {model} - OS: {os_version}, Arch: {arch_info}")
        print(f"[DEBUG] Android 기기 총 {len(devices)}대 발견")
    except Exception as e:
        print(f"[DEBUG ERROR] get_android_devices 실패: {e}")
        import traceback
        traceback.print_exc()
    return devices


def get_ios_devices():
    """idevice_id + ideviceinfo 기반 iOS 디바이스 리스트."""
    devices = []
    try:
        out = run_cmd(["idevice_id", "-l"])
        print(f"[DEBUG] idevice_id -l 결과: {repr(out)}")
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        print(f"[DEBUG] iOS UDID 목록: {lines}")
        
        for udid in lines:
            print(f"[DEBUG] iOS 기기 정보 조회 중: {udid}")
            name = run_cmd(["ideviceinfo", "-u", udid, "-k", "DeviceName"]).strip()
            ver = run_cmd(["ideviceinfo", "-u", udid, "-k", "ProductVersion"]).strip()
            
            # iOS는 아키텍처 정보가 없으므로 빈 문자열
            print(f"[DEBUG] Name: {name}, Version: {ver}")
            devices.append({
                "platform": "iOS",
                "id": udid,
                "name": name or udid,
                "os_version": ver or "unknown",
                "arch": "",  # iOS는 아키텍처 정보 없음
            })
        print(f"[DEBUG] iOS 기기 총 {len(devices)}대 발견")
    except Exception as e:
        print(f"[DEBUG ERROR] get_ios_devices 실패: {e}")
        import traceback
        traceback.print_exc()
    return devices


def get_android_packages(serial: str):
    """Android 패키지 목록 (멀티 유저 대응)."""
    pkgs = []
    
    # user 0 기준으로 조회 (권한 문제 회피)
    out = run_cmd(["adb", "-s", serial, "shell", "pm", "list", "packages", "--user", "0"])
    
    # 에러 발생 시 user 옵션 없이 재시도
    if "[ERROR]" in out or "Exception" in out or "SecurityException" in out:
        print(f"[DEBUG] user 0 조회 실패, 기본 조회 재시도")
        out = run_cmd(["adb", "-s", serial, "shell", "pm", "list", "packages"])
    
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.append(line[len("package:"):])
    
    print(f"[DEBUG] 패키지 {len(pkgs)}개 발견")
    return sorted(pkgs)


def get_ios_apps(udid: str):
    """
    ideviceinstaller -u UDID list 결과를 파싱하여 (bundle_id, app_name) 리스트 반환.
    ideviceinstaller 1.2.0+ 버전 대응
    """
    # 새 버전 명령어: ideviceinstaller -u UDID list --user
    out = run_cmd(["ideviceinstaller", "-u", udid, "list", "--user"])
    
    # 구버전 fallback
    if "[ERROR]" in out or "invalid option" in out:
        print(f"[DEBUG] 새 명령어 실패, 구버전 시도")
        out = run_cmd(["ideviceinstaller", "-u", udid, "-l"])
    
    apps = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith("CFBundleIdentifier"):
            continue
        parts = [p.strip().strip('"') for p in line.split(",")]
        if len(parts) >= 1:
            bundle_id = parts[0]
            name = parts[2] if len(parts) >= 3 else bundle_id
            apps.append((bundle_id, name))
    
    print(f"[DEBUG] iOS 앱 {len(apps)}개 발견")
    return apps


def android_screenshot(serial: str, out_path: str) -> tuple[bool, str]:
    """
    Android 스크린샷 (PNG 깨짐 방지: exec-out screencap -p)
    Returns: (success, error_message)
    """
    try:
        proc = subprocess.run(
            ["adb", "-s", serial, "exec-out", "screencap", "-p"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="ignore").strip()
            print(f"[Android][{serial}] screencap 실패: {err}")
            return False, err if err else "screencap 명령 실패"
        
        data = proc.stdout
        if not data:
            error_msg = "screencap 결과 비어있음 (화면이 꺼져있거나 잠금 상태일 수 있음)"
            print(f"[Android][{serial}] {error_msg}")
            return False, error_msg

        # PNG 시그니처 확인 (깨진 데이터 방지)
        png_sig = b"\x89PNG\r\n\x1a\n"
        idx = data.find(png_sig)

        # 멀티 디스플레이 경고 등 텍스트가 앞에 붙은 경우, 시그니처가 중간에 있을 수 있음
        if idx > 0:
            print(f"[Android][{serial}] screencap 출력 앞부분에 경고/텍스트가 포함됨, PNG 시그니처 이후 데이터만 사용")
            data = data[idx:]
        elif idx == -1:
            # PNG 시그니처 자체가 없으면 실패로 간주
            head = data[:200].decode(errors="ignore")
            error_msg = "스크린샷 데이터에서 PNG 시그니처를 찾을 수 없습니다. (adb screencap 출력: 앞 200바이트)\n" + head
            print(f"[Android][{serial}] {error_msg}")
            return False, error_msg
        
        with open(out_path, "wb") as f:
            f.write(data)
        return True, ""
        
    except Exception as e:
        error_msg = f"스크린샷 예외: {e}"
        print(f"[Android][{serial}] {error_msg}")
        return False, error_msg


def ios_screenshot_via_xcode(device_name: str) -> tuple[bool, str]:
    """
    macOS + Xcode의 Devices & Simulators 창에서 'Take Screenshot' 버튼을
    AppleScript(osascript)로 눌러서 스크린샷을 촬영하는 우회 방법.

    실제 파일 저장 경로는 Xcode 설정(기본값: 데스크톱)을 따르며,
    이 함수는 스크린샷 트리거 성공 여부만 반환한다.
    """
    import sys
    import shutil

    if sys.platform != "darwin":
        return False, "이 기능은 macOS에서만 지원됩니다."

    if shutil.which("osascript") is None:
        return False, "osascript 명령을 찾을 수 없습니다. (macOS 기본 쉘 필요)"

    safe_name = device_name.replace('"', '\\"')

    # Xcode UI 구조는 버전/언어에 따라 조금씩 다를 수 있으므로
    # 최소한의 가정 하에 'Devices and Simulators' 메뉴와 'Take Screenshot' 버튼을 찾는다.
    applescript = f'''
    tell application "Xcode"
        activate
    end tell

    delay 1

    tell application "System Events"
        if not (exists process "Xcode") then
            error "Xcode 프로세스를 찾을 수 없습니다."
        end if
        tell process "Xcode"
            -- Devices and Simulators 창 열기
            try
                click menu item "Devices and Simulators" of menu "Window" of menu bar 1
            on error
                try
                    click menu item "Devices and Simulators" of menu 5 of menu bar 1
                on error errMsg
                    error "Devices and Simulators 메뉴를 찾을 수 없습니다: " & errMsg
                end try
            end try

            delay 1

            -- 사이드바에서 디바이스 이름 선택
            set targetDevice to "{safe_name}"
            try
                tell window 1
                    tell outline 1 of scroll area 1
                        set foundDevice to false
                        repeat with r in rows
                            try
                                if (value of static text 1 of r as string) is equal to targetDevice then
                                    select r
                                    set foundDevice to true
                                    exit repeat
                                end if
                            end try
                        end repeat
                    end tell

                    if foundDevice is false then
                        error "Xcode Devices 목록에서 디바이스 '" & targetDevice & "' 를 찾을 수 없습니다."
                    end if

                    delay 0.5

                    -- 툴바의 'Take Screenshot' 버튼 클릭
                    try
                        click button "Take Screenshot" of toolbar 1
                    on error
                        try
                            click button 1 of toolbar 1
                        on error err2
                            error "Take Screenshot 버튼을 클릭할 수 없습니다: " & err2
                        end try
                    end try
                end tell
            on error errMsg2
                error errMsg2
            end try
        end tell
    end tell
    '''

    proc = subprocess.run(
        ["osascript", "-e", applescript],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if proc.returncode == 0:
        msg = proc.stdout.strip()
        return True, msg or ""

    msg = (proc.stdout + "\n" + proc.stderr).strip()
    return False, msg or "osascript 실행 실패"


def ios_quicktime_start_recording() -> tuple[bool, str]:
    """
    QuickTime Player를 사용해 iOS 기기 화면 녹화를 시작하는 우회 방법.
    - USB로 연결된 iPhone을 QuickTime의 '새로운 동영상 녹화'에서
      카메라/마이크로 한 번 선택해두었다고 가정.
    - 이 함수는 QuickTime을 활성화하고, 필요 시 '새로운 동영상 녹화'를 연 뒤,
      녹화 버튼(빨간 동그라미)을 눌러 녹화를 시작한다.
    """
    import sys
    import shutil

    if sys.platform != "darwin":
        return False, "이 기능은 macOS에서만 지원됩니다."

    if shutil.which("osascript") is None:
        return False, "osascript 명령을 찾을 수 없습니다. (macOS 기본 쉘 필요)"

    applescript = '''
    tell application "QuickTime Player"
        activate
    end tell

    delay 1

    tell application "System Events"
        if not (exists process "QuickTime Player") then
            error "QuickTime Player 프로세스를 찾을 수 없습니다."
        end if
        tell process "QuickTime Player"
            -- 창이 없다면 '새로운 동영상 녹화' 생성
            if (count windows) = 0 then
                try
                    click menu item "새로운 동영상 녹화" of menu "파일" of menu bar 1
                on error
                    try
                        click menu item "New Movie Recording" of menu "File" of menu bar 1
                    on error errMsg
                        error "QuickTime의 '새로운 동영상 녹화' 메뉴를 찾을 수 없습니다: " & errMsg
                    end try
                end try
                delay 1
            end if

            -- 현재 전면 창의 녹화 버튼 클릭 (토글)
            tell window 1
                try
                    -- 일반적으로 툴바의 첫 번째 버튼이 녹화 버튼
                    click button 1
                on error err2
                    error "녹화 버튼을 클릭할 수 없습니다: " & err2
                end try
            end tell
        end tell
    end tell
    '''

    proc = subprocess.run(
        ["osascript", "-e", applescript],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if proc.returncode == 0:
        msg = proc.stdout.strip()
        return True, msg or ""

    msg = (proc.stdout + "\n" + proc.stderr).strip()
    return False, msg or "QuickTime 녹화 시작 AppleScript 실행 실패"




# ------------------------------------------------------------
# 앱 리스트 선택용 팝업 다이얼로그
# ------------------------------------------------------------

class ListSelectDialog(QDialog):
    """앱 리스트 / 패키지 리스트를 보여주고 선택하게 하는 팝업 (단일/다중 선택 지원)."""

    def __init__(self, title: str, items, parent=None, multi_select=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 500)

        self.selected_value = None  # 단일 선택용 (하위 호환)
        self.selected_values = []   # 다중 선택용
        self.multi_select = multi_select

        layout = QVBoxLayout(self)

        # 다중 선택 안내
        if multi_select:
            info_label = QLabel("💡 Ctrl/Cmd 키를 누르고 클릭하면 여러 개 선택 가능!", self)
            info_label.setStyleSheet("color: blue; font-weight: bold; padding: 5px;")
            layout.addWidget(info_label)

        self.list_widget = QListWidget(self)
        
        # 다중 선택 모드 설정
        if multi_select:
            self.list_widget.setSelectionMode(QListWidget.MultiSelection)
        else:
            self.list_widget.setSelectionMode(QListWidget.SingleSelection)
        
        for it in items:
            self.list_widget.addItem(it)
        layout.addWidget(self.list_widget)

        # 선택된 개수 표시
        if multi_select:
            self.count_label = QLabel("선택: 0개", self)
            self.count_label.setStyleSheet("padding: 5px; font-weight: bold;")
            layout.addWidget(self.count_label)
            self.list_widget.itemSelectionChanged.connect(self.update_count)

        btn_row = QHBoxLayout()
        
        if multi_select:
            btn_select_all = QPushButton("전체 선택", self)
            btn_select_all.clicked.connect(self.select_all)
            btn_clear = QPushButton("선택 해제", self)
            btn_clear.clicked.connect(self.clear_selection)
            btn_row.addWidget(btn_select_all)
            btn_row.addWidget(btn_clear)
        
        btn_row.addStretch(1)
        btn_ok = QPushButton("확인", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("취소", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def update_count(self):
        """선택된 항목 개수 업데이트"""
        count = len(self.list_widget.selectedItems())
        self.count_label.setText(f"선택: {count}개")

    def select_all(self):
        """전체 선택"""
        self.list_widget.selectAll()

    def clear_selection(self):
        """선택 해제"""
        self.list_widget.clearSelection()

    def accept(self):
        items = self.list_widget.selectedItems()
        if items:
            if self.multi_select:
                self.selected_values = [item.text() for item in items]
                self.selected_value = self.selected_values[0] if self.selected_values else None
            else:
                self.selected_value = items[0].text()
                self.selected_values = [self.selected_value]
        super().accept()


class AndroidTextDialog(QDialog):
    """Android 기기에 전송할 텍스트를 입력/파일로 불러오는 다이얼로그."""

    # 슬롯(1~10)별 텍스트를 바로 전송하기 위한 시그널 (텍스트 한 덩어리)
    macro_send = QtCore.Signal(str)

    def __init__(self, parent=None, macros=None):
        super().__init__(parent)
        self.setWindowTitle("Android 텍스트 전달")
        self.resize(600, 500)

        layout = QVBoxLayout(self)

        info = QLabel(
            "줄 단위로 텍스트를 입력하거나, 텍스트 파일을 불러와서\n"
            "선택된 Android 기기에 순서대로 전송합니다.\n"
            "(각 줄마다 adb shell input text + Enter)"
        , self)
        info.setWordWrap(True)
        layout.addWidget(info)

        self.text_edit = QTextEdit(self)
        layout.addWidget(self.text_edit, 1)

        file_row = QHBoxLayout()
        self.edit_file = QLineEdit(self)
        btn_browse = QPushButton("텍스트 파일 불러오기", self)
        btn_browse.clicked.connect(self.browse_file)
        file_row.addWidget(self.edit_file, 1)
        btn_apply_to_macros = QPushButton("번호로 저장", self)
        btn_apply_to_macros.setToolTip(
            "상단 입력 영역의 각 줄이 '1. 내용', '2 내용' 처럼 번호로 시작하면\n"
            "해당 번호에 맞는 텍스트 미리값(1~0 슬롯)에 자동으로 저장합니다."
        )
        btn_apply_to_macros.clicked.connect(self.apply_input_to_macros)
        file_row.addWidget(btn_apply_to_macros)
        file_row.addWidget(btn_browse)
        layout.addLayout(file_row)

        # ------- 텍스트 미리값(매크로) 슬롯 -------
        # macros: 길이 10의 리스트(없으면 공백으로 채움)
        if macros is None:
            macros = ["" for _ in range(10)]
        else:
            # 방어 코드: 길이가 다르면 맞춰줌
            macros = list(macros) + [""] * (10 - len(macros))
            macros = macros[:10]

        macro_group = QGroupBox("텍스트 미리값 (숫자 1~0 눌러 전송):", self)
        macro_layout = QGridLayout(macro_group)
        self.macro_edits = []

        for idx in range(10):
            # 왼쪽: 1,3,5,7,9 / 오른쪽: 2,4,6,8,0
            row = idx // 2
            # 각 슬롯은 [번호 레이블, 입력칸, 전송 버튼] 총 3칸 사용
            col = (idx % 2) * 3
            slot_num = (idx + 1) if idx < 9 else 0  # 1~9, 0

            lbl = QLabel(str(slot_num), self)
            macro_layout.addWidget(lbl, row, col)

            edit = QLineEdit(self)
            edit.setText(macros[idx])
            macro_layout.addWidget(edit, row, col + 1)
            self.macro_edits.append(edit)

            btn_send = QPushButton("전송", self)
            btn_send.setFixedWidth(60)
            # 슬롯별 전송 버튼 → macro_send 시그널로 텍스트 전달
            btn_send.clicked.connect(lambda _, i=idx: self._on_macro_send(i))
            macro_layout.addWidget(btn_send, row, col + 2)

        layout.addWidget(macro_group)

        # 하단에는 별도 '전송' 버튼 대신, 변경 내용을 저장하고 닫는 용도의 버튼만 둔다.
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_close = QPushButton("닫기", self)
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "텍스트 파일 선택",
            "",
            "Text Files (*.txt);;All Files (*)"
        )
        if not path:
            return
        self.edit_file.setText(path)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            self.text_edit.setPlainText(content)
            # 파일 내용이 "1. text", "2. text" 형태라면 자동으로 텍스트 미리값에 매핑
            self._fill_macros_from_numbered_lines(content)
        except Exception as e:
            QMessageBox.critical(self, "에러", f"파일을 읽는 중 오류 발생:\n{e}")

    def get_lines(self):
        """공백이 아닌 줄 목록 반환."""
        text = self.text_edit.toPlainText()
        lines = []
        for ln in text.splitlines():
            stripped = ln.rstrip("\n\r")
            if stripped.strip():
                lines.append(stripped)
        return lines

    def get_macro_texts(self):
        """각 슬롯(1~10)의 텍스트 리스트 반환."""
        return [e.text() for e in self.macro_edits]

    def _on_macro_send(self, idx: int):
        """특정 슬롯 텍스트를 바로 전송 요청 (시그널로 부모에 전달)."""
        if 0 <= idx < len(self.macro_edits):
            text = self.macro_edits[idx].text().strip()
            if text:
                self.macro_send.emit(text)

    def _on_macro_save(self, idx: int):
        """
        상단 입력 영역의 내용을 선택 슬롯에 저장.
        - 여러 줄일 경우, 첫 번째 공백이 아닌 줄만 사용.
        """
        if not (0 <= idx < len(self.macro_edits)):
            return
        text = self.text_edit.toPlainText()
        first_line = ""
        for ln in text.splitlines():
            if ln.strip():
                first_line = ln.strip()
                break
        if first_line:
            self.macro_edits[idx].setText(first_line)

    def _fill_macros_from_numbered_lines(self, text: str):
        """
        "1. 내용", "2 내용", "3) 내용" 등 번호로 시작하는 각 줄을
        텍스트 미리값 슬롯(1~0)에 매핑한다.
        """
        import re

        lines = text.splitlines()
        for ln in lines:
            stripped = ln.strip()
            if not stripped:
                continue
            m = re.match(r"^([0-9])\s*[.)]?\s*(.*)$", stripped)
            if not m:
                continue
            idx_str, value = m.group(1), m.group(2)
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            # 1~9 → 슬롯 0~8, 0 → 슬롯 9
            if idx == 0:
                slot = 9
            elif 1 <= idx <= 9:
                slot = idx - 1
            else:
                continue
            if 0 <= slot < len(self.macro_edits):
                self.macro_edits[slot].setText(value.strip())

    def apply_input_to_macros(self):
        """
        상단 입력 영역의 내용을 번호 패턴(1.텍스트 등) 기준으로 파싱하여
        하단 미리값 슬롯(1~0)에 한 번에 저장한다.
        """
        text = self.text_edit.toPlainText()
        if not text.strip():
            return
        self._fill_macros_from_numbered_lines(text)


class IosLogLiveDialog(QDialog):
    """iOS 실시간 syslog를 보여주는 팝업 다이얼로그."""

    line_appended = QtCore.Signal(str)
    state_changed = QtCore.Signal(bool)  # True: 실행 중, False: 중지 상태

    def __init__(self, udid: str, name: str, parent=None):
        super().__init__(parent)
        self.udid = udid
        self.dev_name = name
        self._proc = None
        self._running = False

        self.setWindowTitle(f"iOS 실시간 로그 - {name}")
        self.resize(900, 500)

        layout = QVBoxLayout(self)

        info = QLabel(
            "idevicesyslog 를 사용해 실시간 로그를 표시합니다.\n"
            "중지 버튼으로 일시 중지/재시작을 할 수 있고, 저장 버튼으로 현재 로그를 파일로 저장합니다.",
            self,
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.text_edit = QTextEdit(self)
        self.text_edit.setReadOnly(True)
        layout.addWidget(self.text_edit, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_stop = QPushButton("중지", self)
        self.btn_stop.clicked.connect(self.toggle_logging)
        btn_row.addWidget(self.btn_stop)

        # 중지 상태에서 활성화되는 저장 버튼
        self.btn_save = QPushButton("저장", self)
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.save_logs)
        btn_row.addWidget(self.btn_save)

        layout.addLayout(btn_row)

        self.line_appended.connect(self._append_line_ui)
        self.state_changed.connect(self._on_state_changed)

        # 자동으로 로깅 시작
        self.start_logging()

    def start_logging(self):
        if self._running:
            return
        self._running = True
         # 실행 시작 상태를 UI에 반영
        self.state_changed.emit(True)

        def worker():
            try:
                proc = subprocess.Popen(
                    ["idevicesyslog", "-u", self.udid],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
            except Exception as e:
                QtWidgets.QMessageBox.critical(
                    self,
                    "에러",
                    f"idevicesyslog 시작 실패:\n{e}",
                )
                self._running = False
                self.state_changed.emit(False)
                return

            self._proc = proc
            try:
                while self._running:
                    line = proc.stdout.readline()
                    if not line:
                        if proc.poll() is not None:
                            break
                        continue
                    self.line_appended.emit(line.rstrip("\n"))
            finally:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                self._proc = None
                self._running = False
                # 상태 변경을 UI(메인 스레드)에 전달
                self.state_changed.emit(False)

        threading.Thread(target=worker, daemon=True).start()

    def toggle_logging(self):
        """
        - 실행 중일 때: 중지 요청
        - 중지 상태일 때: 다시 idevicesyslog 를 시작
        """
        if self._running:
            # 루프가 빠져나가도록 플래그만 내리고, 실제 종료는 worker에서 처리
            self._running = False
        else:
            self.start_logging()

    def stop_logging(self):
        if not self._running:
            return
        self._running = False

    def _append_line_ui(self, line: str):
        self.text_edit.append(line)

    def _on_state_changed(self, running: bool):
        """
        상태에 따라 버튼 라벨/활성화를 변경.
        """
        if running:
            self.btn_stop.setText("중지")
            self.btn_stop.setEnabled(True)
            self.btn_save.setEnabled(False)
        else:
            self.btn_stop.setText("재시작")
            self.btn_stop.setEnabled(True)
            self.btn_save.setEnabled(True)

    def save_logs(self):
        """
        현재까지의 텍스트를 iOS syslog 파일로 저장.
        - 메인 윈도우의 save_dir, _build_device_filename 등을 활용.
        """
        parent = self.parent()
        save_dir = getattr(parent, "save_dir", "") if parent is not None else ""
        if not save_dir:
            QtWidgets.QMessageBox.warning(
                self,
                "경고",
                "먼저 메인 창에서 저장 루트 폴더를 선택하세요.",
            )
            return

        # OS 버전 조회 (실패해도 계속 진행)
        try:
            os_ver = run_cmd(["ideviceinfo", "-u", self.udid, "-k", "ProductVersion"]).strip()
        except Exception:
            os_ver = ""
        if not os_ver:
            os_ver = "unknown"

        arch = ""  # iOS는 아키텍처 정보 사용 안 함
        ts = time.strftime("%Y%m%d_%H%M%S")
        suffix = f"_{ts}_syslog_live.txt"

        if parent is not None and hasattr(parent, "_build_device_filename"):
            filename = parent._build_device_filename("iOS", self.dev_name, os_ver, arch, suffix)
        else:
            # 혹시라도 parent 없음 대비한 기본 이름
            safe_name = self.dev_name.replace(" ", "_")
            filename = f"iOS_{safe_name}_{suffix}"

        path = os.path.join(save_dir, filename)
        text = self.text_edit.toPlainText()

        try:
            with open(path, "w", encoding="utf-8", errors="ignore") as f:
                f.write(text)

            if parent is not None:
                parent.log(f"[iOS][{self.dev_name}] 실시간 syslog 저장 완료: {path}")
                if hasattr(parent, "add_history"):
                    parent.add_history("LOG", "iOS", self.udid, self.dev_name, filename, "OK")
                if hasattr(parent, "_reveal_path"):
                    parent._reveal_path(path)
            else:
                QtWidgets.QMessageBox.information(self, "저장 완료", f"로그가 저장되었습니다:\n{path}")
        except Exception as e:
            if parent is not None:
                parent.log(f"[iOS][{self.dev_name}] 실시간 syslog 저장 실패: {e}")
                if hasattr(parent, "add_history"):
                    parent.add_history("LOG", "iOS", self.udid, self.dev_name, filename, f"FAIL: {e}")
                if hasattr(parent, "show_error"):
                    parent.show_error(f"[iOS][{self.dev_name}] syslog 저장 실패: {e}")
            else:
                QtWidgets.QMessageBox.critical(self, "에러", f"로그 저장 실패:\n{e}")

    def closeEvent(self, event):
        # 창 닫힐 때 로깅 중지
        self.stop_logging()
        super().closeEvent(event)


# ------------------------------------------------------------
# 메인 윈도우
# ------------------------------------------------------------

class MainWindow(QMainWindow):
    # Signal 정의 (클래스 레벨)
    devices_updated = QtCore.Signal(list, int, int)  # devices, android_count, ios_count
    android_pkgs_loaded = QtCore.Signal(list, str)  # pkgs, device_name
    ios_apps_loaded = QtCore.Signal(list, str)  # apps, device_name
    error_occurred = QtCore.Signal(str)  # error message
    log_appended = QtCore.Signal(str)  # log message
    batch_progress_changed = QtCore.Signal(str, int)  # label, percent
    android_record_state_changed = QtCore.Signal(bool)  # is_recording
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Multi-Device QA Tool (PySide6 v2)")
        self.resize(1350, 850)

        # 설정
        self.settings = QSettings("ADBTool", "MultiDeviceManager")

        # 히스토리 저장 루트 폴더
        self.save_dir = ""
        self.history = []

        # Android 텍스트 기본값
        self.android_text_default = ""

        # Android 텍스트 매크로(슬롯 1~10)
        self.android_text_macros = [
            self.settings.value(f"android_text_macro_{i+1}", "", str)
            for i in range(10)
        ]

        # 시나리오 옵션
        # chk_delete_before(삭제만) 체크 상태와 맞추기 위해 기본값 False
        self.opt_delete_before = False   # 삭제 전용 모드
        self.opt_install = True         # 설치 수행 (기본 ON)
        self.opt_run_after = False      # 설치 후 실행

        # APK/IPA 폴더
        self.apk_folder = None
        self.ipa_folder = None

        # 로그/스크린샷/크래시 파일명 시퀀스 관리 (디바이스별 _1, _2 ...)
        # key: (platform, name, os_version, arch, suffix) -> count
        self.file_seq_map = {}

        # Android 화면 녹화 상태
        self._android_record_proc = None
        self._android_record_serial = None
        self._android_record_name = None

        self._build_ui()

        # 설정 로드
        self._load_settings()

        # Signal 연결
        self.devices_updated.connect(self._update_device_ui)
        self.android_pkgs_loaded.connect(self._show_android_pkg_dialog)
        self.ios_apps_loaded.connect(self._show_ios_app_dialog)
        self.error_occurred.connect(self._show_error_dialog)
        self.log_appended.connect(self._append_log_ui)
        self.batch_progress_changed.connect(self._update_batch_progress_ui)
        self.android_record_state_changed.connect(self._update_android_record_ui)

        # 디바이스 초기 새로고침
        self.refresh_devices()
        # iOS 등 이벤트 감지가 어려운 부분을 위해 주기적 폴링은 유지 (간격 15초)
        self.refresh_timer = QtCore.QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_devices)
        self.refresh_timer.start(30000)

        # Android는 adb track-devices 기반 변경 감지 스레드 추가
        self._start_adb_device_watcher()

    def _load_settings(self):
        """QSettings에서 최근 설정(저장 경로, Android 텍스트 기본값) 로드"""
        save_dir = self.settings.value("save_dir", "", str)
        if save_dir:
            self.save_dir = save_dir
            self.edit_save_dir.setText(save_dir)

        self.android_text_default = self.settings.value("android_text_default", "", str)

    def _save_settings(self):
        """현재 설정을 QSettings에 저장"""
        self.settings.setValue("save_dir", self.save_dir or "")
        self.settings.setValue("android_text_default", self.android_text_default or "")
        # Android 텍스트 매크로 저장
        if hasattr(self, "android_text_macros"):
            for i, v in enumerate(self.android_text_macros):
                self.settings.setValue(f"android_text_macro_{i+1}", v or "")

    def closeEvent(self, event):
        """창 종료 시 설정 저장"""
        try:
            self._save_settings()
        except Exception:
            pass
        super().closeEvent(event)

    # ---------------- UI 구성 ----------------

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)

        # 1) 디바이스 리스트
        dev_group = QGroupBox("디바이스 목록 (Android + iOS)", self)
        dev_layout = QVBoxLayout(dev_group)

        self.tree_devices = QTreeWidget(self)
        self.tree_devices.setColumnCount(5)
        self.tree_devices.setHeaderLabels(["Platform", "ID/UDID", "Name", "OS Ver", "Arch"])
        self.tree_devices.setSelectionMode(QTreeWidget.ExtendedSelection)
        
        # 컬럼 너비 조정
        self.tree_devices.setColumnWidth(0, 80)   # Platform
        self.tree_devices.setColumnWidth(1, 240)  # ID
        self.tree_devices.setColumnWidth(2, 150)  # Name
        self.tree_devices.setColumnWidth(3, 80)   # OS Ver
        self.tree_devices.setColumnWidth(4, 150)  # Arch
        
        # 우클릭 컨텍스트 메뉴 설정
        self.tree_devices.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_devices.customContextMenuRequested.connect(self.show_device_context_menu)
        
        # 더블클릭으로 셀 복사
        self.tree_devices.itemDoubleClicked.connect(self.on_device_item_double_clicked)
        
        dev_layout.addWidget(self.tree_devices)

        # 디바이스 하단 버튼
        dev_btn_row = QHBoxLayout()
        btn_refresh = QPushButton("새로고침", self)
        btn_refresh.clicked.connect(self.refresh_devices)
        dev_btn_row.addWidget(btn_refresh)

        btn_copy_id = QPushButton("선택 ID 복사", self)
        btn_copy_id.clicked.connect(self.copy_selected_id)
        dev_btn_row.addWidget(btn_copy_id)

        btn_copy_name = QPushButton("선택 Name 복사", self)
        btn_copy_name.clicked.connect(self.copy_selected_name)
        dev_btn_row.addWidget(btn_copy_name)

        btn_copy_os = QPushButton("선택 OS 복사", self)
        btn_copy_os.clicked.connect(self.copy_selected_os)
        dev_btn_row.addWidget(btn_copy_os)

        btn_history = QPushButton("히스토리 보기", self)
        btn_history.clicked.connect(self.show_history_window)
        dev_btn_row.addWidget(btn_history)

        dev_btn_row.addStretch(1)
        dev_layout.addLayout(dev_btn_row)

        main_layout.addWidget(dev_group, 2)

        # 2) 설치/삭제/패키지 선택 영역
        control_group = QGroupBox("설치 / 삭제 / 패키지 선택", self)
        control_layout = QVBoxLayout(control_group)

        # 2-1) 저장 루트 폴더 선택
        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("저장 루트(로그, 스크린샷, 히스토리):", self))
        self.edit_save_dir = QLineEdit(self)
        save_row.addWidget(self.edit_save_dir)
        btn_save_dir = QPushButton("폴더 선택", self)
        btn_save_dir.clicked.connect(self.choose_save_dir)
        save_row.addWidget(btn_save_dir)
        control_layout.addLayout(save_row)

        # 2-2) 패키지명 / 번들ID (저장루트 바로 아래)
        pkg_row = QHBoxLayout()
        pkg_row.addWidget(QLabel("Android 패키지명:", self))
        self.edit_android_pkg = QLineEdit(self)
        pkg_row.addWidget(self.edit_android_pkg, 1)
        btn_android_pkg_list = QPushButton("앱 리스트", self)
        btn_android_pkg_list.clicked.connect(self.show_android_pkg_list_dialog)
        pkg_row.addWidget(btn_android_pkg_list)
        
        pkg_row.addWidget(QLabel("  iOS 번들ID:", self))
        self.edit_ios_bundle = QLineEdit(self)
        pkg_row.addWidget(self.edit_ios_bundle, 1)
        btn_ios_app_list = QPushButton("앱 리스트", self)
        btn_ios_app_list.clicked.connect(self.show_ios_app_list_dialog)
        pkg_row.addWidget(btn_ios_app_list)
        
        control_layout.addLayout(pkg_row)

        # 2-3) APK/IPA 파일 선택 (좌우 분리)
        file_row = QHBoxLayout()
        
        # 왼쪽: Android
        android_group = QGroupBox("Android (APK)", self)
        android_layout = QVBoxLayout(android_group)
        
        apk_single = QHBoxLayout()
        apk_single.addWidget(QLabel("단일 APK:", self))
        self.edit_apk = QLineEdit(self)
        apk_single.addWidget(self.edit_apk, 1)
        btn_apk = QPushButton("찾기", self)
        btn_apk.clicked.connect(self.browse_apk)
        apk_single.addWidget(btn_apk)
        android_layout.addLayout(apk_single)
        
        apk_folder = QHBoxLayout()
        apk_folder.addWidget(QLabel("APK 폴더:", self))
        self.edit_apk_folder = QLineEdit(self)
        apk_folder.addWidget(self.edit_apk_folder, 1)
        btn_apk_folder = QPushButton("폴더 선택", self)
        btn_apk_folder.clicked.connect(self.browse_apk_folder)
        apk_folder.addWidget(btn_apk_folder)
        android_layout.addLayout(apk_folder)
        
        file_row.addWidget(android_group)
        
        # 오른쪽: iOS
        ios_group = QGroupBox("iOS (IPA)", self)
        ios_layout = QVBoxLayout(ios_group)
        
        ipa_single = QHBoxLayout()
        ipa_single.addWidget(QLabel("단일 IPA:", self))
        self.edit_ipa = QLineEdit(self)
        ipa_single.addWidget(self.edit_ipa, 1)
        btn_ipa = QPushButton("찾기", self)
        btn_ipa.clicked.connect(self.browse_ipa)
        ipa_single.addWidget(btn_ipa)
        ios_layout.addLayout(ipa_single)
        
        ipa_folder = QHBoxLayout()
        ipa_folder.addWidget(QLabel("IPA 폴더:", self))
        self.edit_ipa_folder = QLineEdit(self)
        ipa_folder.addWidget(self.edit_ipa_folder, 1)
        btn_ipa_folder = QPushButton("폴더 선택", self)
        btn_ipa_folder.clicked.connect(self.browse_ipa_folder)
        ipa_folder.addWidget(btn_ipa_folder)
        ios_layout.addLayout(ipa_folder)
        
        file_row.addWidget(ios_group)
        
        control_layout.addLayout(file_row)

        # 2-4) 시나리오 옵션 + 실행 버튼
        scenario_frame = QHBoxLayout()
        
        # 시나리오 체크박스
        opt_group = QGroupBox("시나리오 옵션 (체크된 작업만 실행)", self)
        opt_layout = QHBoxLayout(opt_group)
        
        self.chk_delete_before = QCheckBox("삭제만", self)
        self.chk_delete_before.setChecked(False)
        self.chk_delete_before.stateChanged.connect(self._update_options)
        self.chk_delete_before.setToolTip("설치 없이 삭제만 실행 (설치 시 자동 삭제됨)")
        opt_layout.addWidget(self.chk_delete_before)

        self.chk_install = QCheckBox("설치", self)
        self.chk_install.setChecked(True)
        self.chk_install.stateChanged.connect(self._update_options)
        self.chk_install.setToolTip("자동으로 기존 앱 삭제 후 새로 설치 (데이터 초기화)")
        opt_layout.addWidget(self.chk_install)

        self.chk_run_after = QCheckBox("실행", self)
        self.chk_run_after.stateChanged.connect(self._update_options)
        # iOS 실행 기능은 현재 환경 제약(iOS 18.x + idevicedebug 제약)으로 비활성화.
        # 이 체크박스는 Android 실행에만 사용되며, iOS 쪽에서는 실행 옵션을 무시한다.
        self.chk_run_after.setToolTip("설치 후 앱 자동 실행 (Android 전용, iOS 실행은 비활성화)")
        opt_layout.addWidget(self.chk_run_after)
        
        opt_layout.addStretch(1)
        scenario_frame.addWidget(opt_group, 3)
        
        # 실행 버튼
        btn_execute = QPushButton("✅ 확인", self)
        btn_execute.clicked.connect(self.execute_batch)
        btn_execute.setMinimumHeight(60)
        btn_execute.setStyleSheet("font-size: 14pt; font-weight: bold; background-color: #4CAF50; color: white;")
        scenario_frame.addWidget(btn_execute, 1)
        
        control_layout.addLayout(scenario_frame)

        main_layout.addWidget(control_group, 3)

        # 3) 로그/스크린샷/크래시 (좌우 분리)
        op_group = QGroupBox("로그 / 스크린샷 / 크래시 리포트", self)
        op_layout = QHBoxLayout(op_group)
        
        # 왼쪽: Android
        android_op_group = QGroupBox("Android", self)
        android_op_layout = QVBoxLayout(android_op_group)
        
        and_btn_row = QHBoxLayout()
        btn_and_log = QPushButton("로그캣 추출", self)
        btn_and_log.setToolTip("logcat -d를 실행하여 선택된 패키지 로그를 저장합니다.")
        btn_and_log.clicked.connect(self.android_log_dump)
        and_btn_row.addWidget(btn_and_log)
        
        btn_and_ss = QPushButton("스크린샷", self)
        btn_and_ss.clicked.connect(self.android_screenshot_selected)
        and_btn_row.addWidget(btn_and_ss)

        btn_and_text = QPushButton("텍스트 전달", self)
        btn_and_text.setToolTip(
            "선택한 Android 기기에 줄 단위 텍스트를 자동 입력합니다.\n"
            "새 창에서 텍스트를 직접 입력하거나 텍스트 파일을 불러올 수 있습니다."
        )
        btn_and_text.clicked.connect(self.android_text_send_dialog)
        and_btn_row.addWidget(btn_and_text)

        android_op_layout.addLayout(and_btn_row)

        # Android 화면 녹화 (adb shell screenrecord)
        and_rec_row = QHBoxLayout()
        and_rec_row.addWidget(QLabel("화면 녹화 (시작/중지):", self))
        self.btn_and_rec = QPushButton("화면 녹화", self)
        self.btn_and_rec.setToolTip(
            "adb shell screenrecord 를 사용해 선택된 Android 기기의 화면을 녹화합니다.\n"
            "시간이 길수록 파일 용량이 커질 수 있습니다. 다시 누르면 조기 중지됩니다."
        )
        self.btn_and_rec.clicked.connect(self.android_record_selected)
        and_rec_row.addWidget(self.btn_and_rec)
        android_op_layout.addLayout(and_rec_row)

        # ADB 무선 연결 (기존 tcpip 방식)
        adb_wifi_row = QHBoxLayout()
        adb_wifi_row.addWidget(QLabel("ADB 무선(IP:포트):", self))
        self.edit_adb_wifi = QLineEdit(self)
        self.edit_adb_wifi.setPlaceholderText("예: 192.168.0.10:5555")
        adb_wifi_row.addWidget(self.edit_adb_wifi, 1)

        btn_tcpip = QPushButton("선택 기기 TCPIP 5555", self)
        btn_tcpip.setToolTip("USB로 연결된 선택 Android 기기를 adb tcpip 5555 모드로 전환합니다.")
        btn_tcpip.clicked.connect(self.android_enable_tcpip_for_selected)
        adb_wifi_row.addWidget(btn_tcpip)

        btn_adb_connect = QPushButton("무선 연결", self)
        btn_adb_connect.setToolTip("입력한 IP:포트로 adb connect를 수행합니다.")
        btn_adb_connect.clicked.connect(self.android_connect_wifi)
        adb_wifi_row.addWidget(btn_adb_connect)

        btn_adb_disconnect = QPushButton("연결 해제", self)
        btn_adb_disconnect.setToolTip("입력한 IP:포트(비워두면 전체)에 대해 adb disconnect를 수행합니다.")
        btn_adb_disconnect.clicked.connect(self.android_disconnect_wifi)
        adb_wifi_row.addWidget(btn_adb_disconnect)

        android_op_layout.addLayout(adb_wifi_row)

        # ADB 무선 디버깅(pair) - Android 11+
        adb_pair_row = QHBoxLayout()
        adb_pair_row.addWidget(QLabel("무선 디버깅 pair:", self))
        self.edit_adb_pair_host = QLineEdit(self)
        self.edit_adb_pair_host.setPlaceholderText("예: 192.168.0.10:37099 (폰에 표시된 IP:포트)")
        adb_pair_row.addWidget(self.edit_adb_pair_host, 1)

        self.edit_adb_pair_code = QLineEdit(self)
        self.edit_adb_pair_code.setPlaceholderText("페어링 코드 (6자리)")
        adb_pair_row.addWidget(self.edit_adb_pair_code, 1)

        btn_adb_pair = QPushButton("페어링", self)
        btn_adb_pair.setToolTip(
            "Android 11+ '무선 디버깅' 화면에서 '페어링 코드로 기기 페어링'을 선택한 뒤,\n"
            "표시되는 IP:포트와 페어링 코드를 입력하고 눌러주세요.\n"
            "내부적으로 'adb pair IP:PORT CODE' 를 실행합니다."
        )
        btn_adb_pair.clicked.connect(self.android_pair_wifi)
        adb_pair_row.addWidget(btn_adb_pair)

        android_op_layout.addLayout(adb_pair_row)
        
        op_layout.addWidget(android_op_group)
        
        # 오른쪽: iOS
        ios_op_group = QGroupBox("iOS", self)
        ios_op_layout = QVBoxLayout(ios_op_group)
        
        ios_log_row = QHBoxLayout()
        lbl_ios_log = QLabel("실시간 로그 추출:", self)
        ios_log_row.addWidget(lbl_ios_log)
        btn_ios_log = QPushButton("로그 추출", self)
        btn_ios_log.setToolTip("선택된 iOS 기기의 syslog 를 실시간으로 표시하는 팝업을 엽니다.")
        btn_ios_log.clicked.connect(self.ios_log_capture_selected)
        ios_log_row.addWidget(btn_ios_log)
        ios_op_layout.addLayout(ios_log_row)
        
        ios_btn_row = QHBoxLayout()
        btn_ios_crash = QPushButton("크래시 리포트", self)
        btn_ios_crash.clicked.connect(self.ios_crash_export_selected)
        ios_btn_row.addWidget(btn_ios_crash)
        
        btn_ios_ss = QPushButton("스크린샷", self)
        btn_ios_ss.setToolTip("Xcode Devices & Simulators 창의 'Take Screenshot' 버튼을 osascript로 눌러 스크린샷을 촬영합니다.")
        btn_ios_ss.clicked.connect(self.ios_screenshot_selected)
        ios_btn_row.addWidget(btn_ios_ss)

        btn_ios_qt_rec = QPushButton("화면 녹화 (QuickTime)", self)
        btn_ios_qt_rec.setToolTip(
            "QuickTime Player의 '새로운 동영상 녹화' 기능을 이용해\n"
            "현재 연결된 iOS 기기 화면 녹화를 시작/중지합니다.\n"
            "처음 한 번은 QuickTime에서 카메라/마이크를 iPhone으로 수동 선택해야 합니다."
        )
        btn_ios_qt_rec.clicked.connect(self.ios_quicktime_record_selected)
        ios_btn_row.addWidget(btn_ios_qt_rec)

        btn_snapdrop = QPushButton("텍스트파일 전송 (Snapdrop)", self)
        btn_snapdrop.setToolTip(
            "브라우저에서 Snapdrop을 열어 텍스트/파일을\n"
            "iOS/Android/PC 간에 전송합니다.\n"
            "각 기기에서 동일한 네트워크로 접속한 뒤 사용하세요."
        )
        btn_snapdrop.clicked.connect(self.open_snapdrop)
        ios_btn_row.addWidget(btn_snapdrop)

        ios_op_layout.addLayout(ios_btn_row)
        
        op_layout.addWidget(ios_op_group)
        
        main_layout.addWidget(op_group, 1)

        # 4) 로그 콘솔
        log_group = QGroupBox("콘솔 로그", self)
        log_layout = QVBoxLayout(log_group)
        self.txt_log = QPlainTextEdit(self)
        self.txt_log.setReadOnly(True)
        log_layout.addWidget(self.txt_log)
        # 배치 진행률 표시 바
        from PySide6.QtWidgets import QProgressBar
        self.batch_progress_bar = QProgressBar(self)
        self.batch_progress_bar.setRange(0, 100)
        self.batch_progress_bar.setValue(0)
        self.batch_progress_bar.setFormat("대기 중")
        log_layout.addWidget(self.batch_progress_bar)
        main_layout.addWidget(log_group, 3)

    # ---------------- 로그/알림/히스토리 ----------------

    def log(self, msg: str):
        """콘솔 로그 출력 (스레드 안전: Signal 통해 UI 스레드에서 처리)"""
        self.log_appended.emit(msg)

    def _append_log_ui(self, msg: str):
        """UI 스레드에서 로그 추가 (QPlainTextEdit, 라인 수 제한)"""
        if not msg:
            return
        # 너무 길면 앞부분만 남기기
        if len(msg) > 4000:
            msg = msg[:4000] + " ... (생략)"
        # 라인 수가 너무 많아지면 전체 클리어
        if self.txt_log.blockCount() > 2000:
            self.txt_log.clear()
            self.txt_log.appendPlainText("=== 로그가 너무 많아 초기화되었습니다 ===")
        self.txt_log.appendPlainText(msg)

    def _update_batch_progress_ui(self, label: str, value: int):
        """배치 진행률 ProgressBar 업데이트 (UI 스레드)"""
        value = max(0, min(100, value))
        self.batch_progress_bar.setValue(value)
        if label:
            self.batch_progress_bar.setFormat(f"{label} - {value}%")
        else:
            self.batch_progress_bar.setFormat(f"{value}%")

    def _update_android_record_ui(self, is_recording: bool):
        """Android 화면 녹화 버튼 상태 업데이트 (UI 스레드)"""
        if is_recording:
            self.btn_and_rec.setText("화면 녹화 중지")
        else:
            self.btn_and_rec.setText("화면 녹화")

    def show_error(self, msg: str):
        """에러 메시지 표시 (스레드 안전)"""
        # Signal을 통해 UI 스레드에서 표시
        self.error_occurred.emit(msg)

    # ---------------- 파일명/경로 유틸 ----------------

    def _sanitize_for_filename(self, text: str) -> str:
        """파일명에 사용할 수 있도록 텍스트 정리"""
        if not text:
            return "NA"
        invalid = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
        s = str(text)
        for ch in invalid:
            s = s.replace(ch, "_")
        s = s.replace(" ", "_")
        return s

    def _build_device_filename(self, platform: str, name: str, os_version: str, arch: str, suffix: str) -> str:
        """
        플랫폼/디바이스네임/OS/아키텍처 형태의 베이스에
        동일 디바이스 + suffix 조합 기준으로 _1, _2 ... 를 붙여 고유 파일명 생성.
        예) Android_GalaxyS23_14_arm64_logcat.txt, Android_GalaxyS23_14_arm64_logcat_1.txt
        """
        platform_s = self._sanitize_for_filename(platform)
        name_s = self._sanitize_for_filename(name)
        os_s = self._sanitize_for_filename(os_version or "")
        arch_s = self._sanitize_for_filename(arch or "NA")

        base = f"{platform_s}_{name_s}_{os_s}_{arch_s}"
        key = (platform_s, name_s, os_s, arch_s, suffix)

        count = self.file_seq_map.get(key, 0)
        self.file_seq_map[key] = count + 1

        if count == 0:
            # 첫 파일은 _번호 없이
            return base + suffix
        else:
            # 동일 디바이스 + suffix 조합의 두 번째부터 _1, _2 ...
            return f"{base}_{count}{suffix}"

    def _reveal_path(self, path: str):
        """
        생성된 파일/폴더를 Finder 등에서 자동으로 열어줌.
        - 파일: 해당 파일을 Finder에서 선택 상태로 표시
        - 폴더: 폴더를 Finder에서 오픈
        (macOS 기준 구현)
        """
        try:
            if not os.path.exists(path):
                return
            if sys.platform == "darwin":
                if os.path.isfile(path):
                    subprocess.run(["open", "-R", path], check=False)
                else:
                    subprocess.run(["open", path], check=False)
        except Exception as e:
            # 자동 열기 실패는 치명적이지 않으므로 로그만 남김
            self.log(f"[WARN] 파일/폴더 열기 실패: {e}")


    def _start_adb_device_watcher(self):
        """
        adb track-devices 를 사용해 Android 디바이스 목록 변경을 감지하고,
        변화가 감지되면 refresh_devices()를 호출하여 전체 목록을 갱신한다.
        (iOS는 별도의 이벤트 스트림이 없으므로 기존 타이머 폴링 유지)
        """
        def worker():
            try:
                proc = subprocess.Popen(
                    ["adb", "track-devices"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
            except Exception as e:
                self.log(f"[WARN] adb track-devices 시작 실패: {e}")
                return

            try:
                for line in proc.stdout:
                    if not line:
                        break
                    s = line.strip()
                    # 헤더나 빈 줄은 무시
                    if not s or s.startswith("List of devices"):
                        continue
                    # 어떤 상태 변경이든 감지되면 전체 디바이스 새로고침
                    self.log("[INFO] adb track-devices: Android 디바이스 변경 감지 → 새로고침")
                    self.refresh_devices()
            except Exception as e:
                self.log(f"[WARN] adb track-devices 모니터링 중 오류: {e}")
            finally:
                try:
                    proc.terminate()
                except Exception:
                    pass

        th = threading.Thread(target=worker, daemon=True)
        th.start()
    
    def _show_error_dialog(self, msg: str):
        """UI 스레드에서 실행: 에러 다이얼로그 표시"""
        QMessageBox.critical(self, "에러", msg)

    def open_snapdrop(self):
        """브라우저에서 Snapdrop 사이트 열기 (텍스트/파일 전송용)"""
        url = "https://snapdrop.net/"
        try:
            webbrowser.open(url)
            self.log(f"[INFO] Snapdrop 열기: {url}")
        except Exception as e:
            self.show_error(f"Snapdrop 사이트를 여는 중 오류 발생:\n{e}")

    def add_history(self, action, platform, dev_id, dev_name, filename, result):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "time": ts,
            "action": action,
            "platform": platform,
            "id": dev_id,
            "name": dev_name,
            "file": filename or "",
            "result": result,
        }
        self.history.append(entry)

        if self.save_dir:
            date_str = time.strftime("%Y%m%d")
            day_dir = os.path.join(self.save_dir, date_str)
            os.makedirs(day_dir, exist_ok=True)
            csv_path = os.path.join(day_dir, "history.csv")
            new_file = not os.path.exists(csv_path)
            try:
                with open(csv_path, "a", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    if new_file:
                        w.writerow(["time", "action", "platform", "id", "name", "file", "result"])
                    w.writerow([ts, action, platform, dev_id, dev_name, filename or "", result])
            except Exception as e:
                self.log(f"[WARN] 히스토리 CSV 저장 실패: {e}")

    def show_history_window(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("히스토리")
        dlg.resize(900, 400)
        layout = QVBoxLayout(dlg)

        table = QTreeWidget(dlg)
        table.setColumnCount(7)
        table.setHeaderLabels(["time", "action", "platform", "id", "name", "file", "result"])
        table.header().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(table)

        for h in self.history:
            item = QTreeWidgetItem([
                h["time"],
                h["action"],
                h["platform"],
                h["id"],
                h["name"],
                h["file"],
                h["result"],
            ])
            table.addTopLevelItem(item)

        lbl = QLabel("※ history.csv 는 저장 루트 하위 YYYYMMDD/history.csv 로 저장됩니다.", dlg)
        layout.addWidget(lbl)

        dlg.exec()

    # ---------------- 옵션/저장 폴더 ----------------

    def choose_save_dir(self):
        d = QFileDialog.getExistingDirectory(self, "저장 루트 폴더 선택")
        if d:
            self.save_dir = d
            self.edit_save_dir.setText(d)
            self.log(f"[INFO] 저장 루트: {d}")

    def _update_options(self):
        self.opt_delete_before = self.chk_delete_before.isChecked()
        self.opt_install = self.chk_install.isChecked()
        self.opt_run_after = self.chk_run_after.isChecked()

    def execute_batch(self):
        """통합 실행 버튼: Android와 iOS 배치 작업을 모두 실행"""
        # 선택된 기기 확인
        devices = self.get_selected_devices()
        if not devices:
            QMessageBox.information(self, "알림", "실행할 기기를 선택하세요.")
            return
        
        android_devices = [d for d in devices if d[0] == "Android"]
        ios_devices = [d for d in devices if d[0] == "iOS"]
        
        executed = False
        
        # Android 실행
        if android_devices:
            pkg = self.edit_android_pkg.text().strip()
            
            # APK 파일 확인
            apk_files = []
            single_apk = self.edit_apk.text().strip()
            if single_apk and os.path.isfile(single_apk):
                apk_files.append(single_apk)
            if self.apk_folder and os.path.isdir(self.apk_folder):
                folder_apks = [os.path.join(self.apk_folder, f) for f in os.listdir(self.apk_folder) if f.lower().endswith(".apk")]
                folder_apks.sort()
                apk_files.extend(folder_apks)
            apk_files = list(dict.fromkeys(apk_files))
            
            # 삭제만 체크된 경우: 파일 없이도 실행
            if self.opt_delete_before and not self.opt_install:
                if not pkg:
                    QMessageBox.warning(self, "경고", "Android: 삭제를 위해 패키지명이 필요합니다.")
                else:
                    self.log(f"[Android] 삭제 전용 실행: {len(android_devices)}대 기기")
                    threading.Thread(target=self._android_delete_only_thread, args=(android_devices, pkg), daemon=True).start()
                    executed = True
            # 설치 또는 실행이 체크된 경우: APK 파일 필요
            elif apk_files:
                # 실행(앱 자동 실행)이 체크된 경우에만 패키지명 필수
                if self.opt_run_after and not pkg:
                    QMessageBox.warning(self, "경고", "Android: 실행을 위해 패키지명이 필요합니다.")
                else:
                    self.log(f"[Android] 배치 실행: {len(apk_files)}개 파일 → {len(android_devices)}대 기기")
                    threading.Thread(target=self._android_batch_thread, args=(android_devices, apk_files, pkg), daemon=True).start()
                    executed = True
            # 실행만 체크된 경우: APK 없이도 실행 (이미 설치된 앱 실행)
            elif self.opt_run_after and not self.opt_install and not self.opt_delete_before:
                if not pkg:
                    QMessageBox.warning(self, "경고", "Android: 실행을 위해 패키지명이 필요합니다.")
                else:
                    self.log(f"[Android] 실행 전용: {len(android_devices)}대 기기")
                    threading.Thread(target=self._android_run_only_thread, args=(android_devices, pkg), daemon=True).start()
                    executed = True
            elif self.opt_install:
                QMessageBox.warning(self, "경고", "Android: 설치할 APK 파일을 선택하세요.")
        
        # iOS 실행
        if ios_devices:
            bundle = self.edit_ios_bundle.text().strip()
            # iOS 자동 실행 기능은 현재 환경 제약(iOS 18.x + idevicedebug 불안정)으로 비활성화.
            # 실행 체크박스가 켜져 있어도 설치/삭제만 수행하고 실행은 시도하지 않는다.
            ios_run_enabled = False
            if self.opt_run_after and not ios_run_enabled:
                self.log("[iOS] 실행 옵션은 현재 비활성화 상태입니다. (설치/삭제만 수행)")
            
            # IPA 파일 확인
            ipa_files = []
            single_ipa = self.edit_ipa.text().strip()
            if single_ipa and os.path.isfile(single_ipa):
                ipa_files.append(single_ipa)
            if self.ipa_folder and os.path.isdir(self.ipa_folder):
                folder_ipas = [os.path.join(self.ipa_folder, f) for f in os.listdir(self.ipa_folder) if f.lower().endswith(".ipa")]
                folder_ipas.sort()
                ipa_files.extend(folder_ipas)
            ipa_files = list(dict.fromkeys(ipa_files))
            
            # 삭제만 체크된 경우: 파일 없이도 실행
            if self.opt_delete_before and not self.opt_install:
                if not bundle:
                    QMessageBox.warning(self, "경고", "iOS: 삭제를 위해 번들ID가 필요합니다.")
                else:
                    self.log(f"[iOS] 삭제 전용 실행: {len(ios_devices)}대 기기")
                    threading.Thread(target=self._ios_delete_only_thread, args=(ios_devices, bundle), daemon=True).start()
                    executed = True
            # 설치 또는 실행이 체크된 경우: IPA 파일 필요
            elif ipa_files:
                # 실행(앱 자동 실행)이 체크된 경우에만 번들ID 필수
                if ios_run_enabled and self.opt_run_after and not bundle:
                    QMessageBox.warning(self, "경고", "iOS: 실행을 위해 번들ID가 필요합니다.")
                else:
                    self.log(f"[iOS] 배치 실행: {len(ipa_files)}개 파일 → {len(ios_devices)}대 기기")
                    threading.Thread(target=self._ios_batch_thread, args=(ios_devices, ipa_files, bundle), daemon=True).start()
                    executed = True
            # 실행만 체크된 경우: IPA 없이도 실행 (이미 설치된 앱 실행)
            # ※ iOS 실행 기능 비활성화로 인해 아래 실행 전용 분기는 사용하지 않음.
            # elif ios_run_enabled and self.opt_run_after and not self.opt_install and not self.opt_delete_before:
            #     if not bundle:
            #         QMessageBox.warning(self, "경고", "iOS: 실행을 위해 번들ID가 필요합니다.")
            #     else:
            #         self.log(f"[iOS] 실행 전용 실행: {len(ios_devices)}대 기기")
            #         threading.Thread(target=self._ios_run_only_thread, args=(ios_devices, bundle), daemon=True).start()
            #         executed = True
            elif self.opt_install:
                QMessageBox.warning(self, "경고", "iOS: 설치할 IPA 파일을 선택하세요.")
        
        if not executed:
            QMessageBox.warning(self, "경고", "실행할 작업이 없습니다.\n\n체크박스를 확인하고, 필요한 경우 APK/IPA 파일을 선택하세요.")

    # ---------------- 디바이스 ----------------

    def refresh_devices(self):
        self.log("[INFO] 디바이스 새로고침 중...")
        threading.Thread(target=self._refresh_devices_thread, daemon=True).start()

    def _refresh_devices_thread(self):
        try:
            print("[DEBUG] 디바이스 검색 시작")
            android = get_android_devices()
            print(f"[DEBUG] Android 검색 완료: {len(android)}대")
            ios = get_ios_devices()
            print(f"[DEBUG] iOS 검색 완료: {len(ios)}대")
            devices = android + ios
            print(f"[DEBUG] 총 {len(devices)}대 발견, Signal emit 준비")

            # Signal로 UI 스레드에 전달
            android_count = len(android)
            ios_count = len(ios)
            print(f"[DEBUG] devices_updated signal emit: {len(devices)}개")
            self.devices_updated.emit(devices, android_count, ios_count)
            print("[DEBUG] Signal emit 완료")
        except Exception as e:
            print(f"[DEBUG ERROR] 디바이스 검색 실패: {e}")
            self.log(f"[ERROR] 디바이스 검색 실패: {e}")
            import traceback
            traceback.print_exc()
            self.log(traceback.format_exc())

    def _update_device_ui(self, devices, android_count, ios_count):
        """UI 스레드에서 실행되는 디바이스 목록 업데이트"""
        try:
            print(f"[DEBUG] UI 업데이트 시작: {len(devices)}개 디바이스")
            self.tree_devices.clear()
            for i, d in enumerate(devices):
                print(f"[DEBUG] 디바이스 추가: {i+1}/{len(devices)} - {d['platform']} {d['name']}")
                item = QTreeWidgetItem([
                    d["platform"],
                    d["id"],
                    d["name"],
                    d["os_version"],
                    d["arch"],
                ])
                self.tree_devices.addTopLevelItem(item)
            self.log(f"[INFO] 디바이스 새로고침 완료: Android {android_count}대, iOS {ios_count}대 (총 {len(devices)}대)")
            print("[DEBUG] UI 업데이트 완료")
        except Exception as e:
            print(f"[DEBUG ERROR] UI 업데이트 실패: {e}")
            self.log(f"[ERROR] 디바이스 목록 UI 업데이트 실패: {e}")
            import traceback
            traceback.print_exc()

    def get_selected_devices(self):
        """[(platform, id, name, os, arch), ...]"""
        result = []
        for item in self.tree_devices.selectedItems():
            result.append([
                item.text(0),  # platform
                item.text(1),  # id
                item.text(2),  # name
                item.text(3),  # os
                item.text(4),  # arch
            ])
        return result

    def on_device_item_double_clicked(self, item, column):
        """디바이스 목록 셀 더블클릭 시 해당 셀 내용 복사"""
        text = item.text(column)
        if text:
            QApplication.clipboard().setText(text)
            column_names = ["플랫폼", "ID", "이름", "OS버전", "아키텍처"]
            self.log(f"[복사] {column_names[column]}: {text}")

    def show_device_context_menu(self, position):
        """디바이스 목록에서 우클릭 시 컨텍스트 메뉴 표시"""
        item = self.tree_devices.itemAt(position)
        if not item:
            return
        
        menu = QMenu(self)
        
        # 각 컬럼별 복사 액션
        copy_platform = menu.addAction(f"플랫폼 복사: {item.text(0)}")
        copy_id = menu.addAction(f"ID 복사: {item.text(1)[:20]}...")
        copy_name = menu.addAction(f"이름 복사: {item.text(2)}")
        copy_os = menu.addAction(f"OS버전 복사: {item.text(3)}")
        
        # 아키텍처가 있는 경우만 메뉴 추가
        if item.text(4):
            copy_arch = menu.addAction(f"아키텍처 복사: {item.text(4)}")
        else:
            copy_arch = None
            
        menu.addSeparator()
        copy_all = menu.addAction("전체 정보 복사")
        
        # 메뉴 실행
        action = menu.exec(self.tree_devices.viewport().mapToGlobal(position))
        
        if action == copy_platform:
            QApplication.clipboard().setText(item.text(0))
            self.log(f"[복사] 플랫폼: {item.text(0)}")
        elif action == copy_id:
            QApplication.clipboard().setText(item.text(1))
            self.log(f"[복사] ID: {item.text(1)}")
        elif action == copy_name:
            QApplication.clipboard().setText(item.text(2))
            self.log(f"[복사] 이름: {item.text(2)}")
        elif action == copy_os:
            QApplication.clipboard().setText(item.text(3))
            self.log(f"[복사] OS버전: {item.text(3)}")
        elif copy_arch and action == copy_arch:
            QApplication.clipboard().setText(item.text(4))
            self.log(f"[복사] 아키텍처: {item.text(4)}")
        elif action == copy_all:
            all_text = f"Platform: {item.text(0)}\nID: {item.text(1)}\nName: {item.text(2)}\nOS: {item.text(3)}"
            if item.text(4):
                all_text += f"\nArch: {item.text(4)}"
            QApplication.clipboard().setText(all_text)
            self.log(f"[복사] 전체 정보")

    def copy_selected_field(self, col_idx):
        items = self.tree_devices.selectedItems()
        if not items:
            QMessageBox.information(self, "알림", "선택된 디바이스가 없습니다.")
            return
        vals = [i.text(col_idx) for i in items]
        text = "\n".join(vals)
        QApplication.clipboard().setText(text)
        self.log(f"[INFO] 복사: {text}")

    def copy_selected_id(self):
        self.copy_selected_field(1)

    def copy_selected_name(self):
        self.copy_selected_field(2)

    def copy_selected_os(self):
        self.copy_selected_field(3)

    # ---------------- 파일 선택 ----------------

    def browse_apk(self):
        path, _ = QFileDialog.getOpenFileName(self, "APK 선택", "", "APK Files (*.apk);;All Files (*)")
        if path:
            self.edit_apk.setText(path)

    def browse_apk_folder(self):
        d = QFileDialog.getExistingDirectory(self, "APK 폴더 선택")
        if d:
            self.apk_folder = d
            self.edit_apk_folder.setText(d)

    def browse_ipa(self):
        path, _ = QFileDialog.getOpenFileName(self, "IPA 선택", "", "IPA Files (*.ipa);;All Files (*)")
        if path:
            self.edit_ipa.setText(path)

    def browse_ipa_folder(self):
        d = QFileDialog.getExistingDirectory(self, "IPA 폴더 선택")
        if d:
            self.ipa_folder = d
            self.edit_ipa_folder.setText(d)

    # ---------------- Android 패키지 리스트 팝업 ----------------

    def show_android_pkg_list_dialog(self):
        devices = self.get_selected_devices()
        androids = [d for d in devices if d[0] == "Android"]
        if not androids:
            QMessageBox.information(self, "알림", "먼저 Android 기기를 하나 선택하세요.")
            return
        # 첫 번째 Android 기준으로 패키지 조회
        platform, serial, name, *_ = androids[0]
        self.log(f"[Android][{name}] 패키지 목록 조회 중...")
        
        # 백그라운드에서 조회 후 Signal emit
        def fetch():
            try:
                print(f"[DEBUG] Android 패키지 조회 시작: {serial}")
                pkgs = get_android_packages(serial)
                print(f"[DEBUG] Android 패키지 조회 완료: {len(pkgs)}개")
                
                # Signal로 UI 스레드에 전달
                self.android_pkgs_loaded.emit(pkgs, name)
                print(f"[DEBUG] android_pkgs_loaded signal emit 완료")
            except Exception as e:
                print(f"[DEBUG ERROR] Android 패키지 조회 실패: {e}")
                self.log(f"[ERROR] Android 패키지 조회 실패: {e}")
                import traceback
                traceback.print_exc()
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def _show_android_pkg_dialog(self, pkgs, name):
        """UI 스레드에서 실행: Android 패키지 선택 다이얼로그 표시 (다중 선택 지원)"""
        try:
            print(f"[DEBUG] _show_android_pkg_dialog 호출: {len(pkgs)}개")
            if not pkgs:
                QMessageBox.warning(self, "경고", f"[{name}] 패키지를 가져오지 못했습니다.\n\n- USB 디버깅이 허용되었는지 확인하세요.\n- adb devices 명령으로 기기가 보이는지 확인하세요.")
                return
            
            self.log(f"[Android][{name}] 패키지 {len(pkgs)}개 로드 완료")
            dlg = ListSelectDialog(f"Android 앱 리스트 ({name}) - {len(pkgs)}개", pkgs, self, multi_select=True)
            print(f"[DEBUG] ListSelectDialog 생성 완료")
            
            if dlg.exec() == QDialog.Accepted and dlg.selected_values:
                # 다중 선택된 패키지를 콤마로 구분해서 표시
                selected_str = ", ".join(dlg.selected_values)
                self.edit_android_pkg.setText(selected_str)
                self.log(f"[Android] 선택 패키지 ({len(dlg.selected_values)}개): {selected_str}")
            print(f"[DEBUG] 다이얼로그 닫힘")
        except Exception as e:
            print(f"[DEBUG ERROR] _show_android_pkg_dialog 실패: {e}")
            import traceback
            traceback.print_exc()

    def copy_android_pkg(self):
        pkg = self.edit_android_pkg.text().strip()
        if not pkg:
            QMessageBox.information(self, "알림", "패키지명이 비어 있습니다.")
            return
        QApplication.clipboard().setText(pkg)
        self.log(f"[Android] 패키지명 복사: {pkg}")

    # ---------------- iOS 앱 리스트 팝업 ----------------

    def show_ios_app_list_dialog(self):
        devices = self.get_selected_devices()
        ios = [d for d in devices if d[0] == "iOS"]
        if not ios:
            QMessageBox.information(self, "알림", "먼저 iOS 기기를 하나 선택하세요.")
            return
        platform, udid, name, *_ = ios[0]
        self.log(f"[iOS][{name}] 앱 목록 조회 중...")
        
        # 백그라운드에서 조회 후 Signal emit
        def fetch():
            try:
                print(f"[DEBUG] iOS 앱 조회 시작: {udid}")
                apps = get_ios_apps(udid)
                print(f"[DEBUG] iOS 앱 조회 완료: {len(apps)}개")
                
                # Signal로 UI 스레드에 전달
                self.ios_apps_loaded.emit(apps, name)
                print(f"[DEBUG] ios_apps_loaded signal emit 완료")
            except Exception as e:
                print(f"[DEBUG ERROR] iOS 앱 조회 실패: {e}")
                self.log(f"[ERROR] iOS 앱 조회 실패: {e}")
                import traceback
                traceback.print_exc()
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def _show_ios_app_dialog(self, apps, name):
        """UI 스레드에서 실행: iOS 앱 선택 다이얼로그 표시 (다중 선택 지원)"""
        try:
            print(f"[DEBUG] _show_ios_app_dialog 호출: {len(apps)}개")
            if not apps:
                QMessageBox.warning(self, "경고", f"[{name}] 앱 목록을 가져오지 못했습니다.\n\n- iOS 기기가 신뢰되었는지 확인하세요.\n- idevice_id -l 명령으로 기기가 보이는지 확인하세요.\n- ideviceinstaller가 설치되었는지 확인하세요.")
                return
            
            self.log(f"[iOS][{name}] 앱 {len(apps)}개 로드 완료")
            items = [f"{b}  -  {n}" for (b, n) in apps]
            dlg = ListSelectDialog(f"iOS 앱 리스트 ({name}) - {len(apps)}개", items, self, multi_select=True)
            print(f"[DEBUG] ListSelectDialog 생성 완료")
            
            if dlg.exec() == QDialog.Accepted and dlg.selected_values:
                # 다중 선택된 번들ID를 추출해서 콤마로 구분
                bundles = []
                for text in dlg.selected_values:
                    bundle = text.split("  -  ")[0].strip()
                    bundles.append(bundle)
                
                selected_str = ", ".join(bundles)
                self.edit_ios_bundle.setText(selected_str)
                self.log(f"[iOS] 선택 번들ID ({len(bundles)}개): {selected_str}")
            print(f"[DEBUG] 다이얼로그 닫힘")
        except Exception as e:
            print(f"[DEBUG ERROR] _show_ios_app_dialog 실패: {e}")
            import traceback
            traceback.print_exc()

    def copy_ios_bundle(self):
        bundle = self.edit_ios_bundle.text().strip()
        if not bundle:
            QMessageBox.information(self, "알림", "번들ID가 비어 있습니다.")
            return
        QApplication.clipboard().setText(bundle)
        self.log(f"[iOS] 번들ID 복사: {bundle}")

    # ---------------- Android 로그 / 스크린샷 ----------------

    def android_log_dump(self):
        if not self.save_dir:
            QMessageBox.warning(self, "경고", "먼저 저장 루트 폴더를 선택하세요.")
            return
        pkg = self.edit_android_pkg.text().strip()
        if not pkg:
            QMessageBox.warning(self, "경고", "패키지명을 입력하거나 앱 리스트에서 선택하세요.")
            return

        targets = [d for d in self.get_selected_devices() if d[0] == "Android"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 Android 디바이스가 없습니다.")
            return

        self.log(f"[Android] logcat 캡처 시작 (패키지: {pkg}, 대상: {len(targets)}대)")
        threading.Thread(target=self._android_log_thread, args=(targets, pkg), daemon=True).start()

    def _android_log_thread(self, targets, pkg):
        for dev in targets:
            platform, serial, name, os_ver = dev[:4]
            arch = dev[4] if len(dev) > 4 else ""
            ts = time.strftime("%Y%m%d_%H%M%S")
            suffix = f"_{ts}_logcat.txt"
            filename = self._build_device_filename(platform, name, os_ver, arch, suffix)
            path = os.path.join(self.save_dir, filename)
            self.log(f"[Android][{name}] logcat -d 실행 중...")
            out = run_cmd(["adb", "-s", serial, "logcat", "-d"], timeout=60)
            lines = [ln for ln in out.splitlines() if pkg in ln]
            try:
                with open(path, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(lines))
                self.log(f"[Android][{name}] 로그 저장 완료: {path}")
                self.add_history("LOG", "Android", serial, name, filename, "OK")
                self._reveal_path(path)
            except Exception as e:
                self.log(f"[Android][{name}] 로그 저장 실패: {e}")
                self.add_history("LOG", "Android", serial, name, filename, f"FAIL: {e}")
                self.show_error(f"[Android][{name}] 로그 저장 실패: {e}")

    def android_text_send_dialog(self):
        """Android 텍스트 전달 다이얼로그 표시"""
        # 현재 매크로 텍스트와 함께 다이얼로그 표시
        dlg = AndroidTextDialog(self, macros=self.android_text_macros)

        # 매크로 슬롯 전송 시그널 연결: 슬롯 텍스트 한 덩어리를 바로 전송
        def on_macro_send(text: str):
            # 매크로 전송 시점에 현재 선택된 디바이스 기준으로 전송
            devices = self.get_selected_devices()
            androids = [d for d in devices if d[0] == "Android"]
            self._android_send_text_immediately(androids, text)

        dlg.macro_send.connect(on_macro_send)

        if dlg.exec() != QDialog.Accepted:
            return

        # 하단 '닫기' 버튼을 누르고 나왔을 때:
        # 상단 텍스트와 미리값을 항상 저장 (디바이스 연결 여부와 무관)
        self.android_text_default = dlg.text_edit.toPlainText()
        self.android_text_macros = dlg.get_macro_texts()
        # 여기서는 별도의 일괄 전송은 하지 않는다.
        # 실제 전송은 각 슬롯의 '전송' 버튼을 통해 즉시 수행된다.

    def _android_send_text_immediately(self, targets, text: str):
        """
        매크로 슬롯에서 한 번에 전송할 때 사용하는 헬퍼.
        - 줄바꿈 기준으로 분리해서 기존 _android_text_send_thread 로 전달.
        """
        if not text:
            return
        if not targets:
            QMessageBox.information(self, "알림", "선택된 Android 디바이스가 없습니다.")
            return

        # 줄 단위로 분리 (공백 줄 제거)
        lines = []
        for ln in text.splitlines():
            stripped = ln.rstrip("\n\r")
            if stripped.strip():
                lines.append(stripped)
        if not lines:
            QMessageBox.information(self, "알림", "전송할 텍스트가 없습니다.")
            return

        self.log(f"[Android] 매크로 텍스트 전달 시작: {len(lines)}줄 → {len(targets)}대 기기")
        threading.Thread(
            target=self._android_text_send_thread,
            args=(targets, lines),
            daemon=True,
        ).start()

    # ---------------- Android ADB 무선 연결 ----------------

    def android_enable_tcpip_for_selected(self):
        """USB로 연결된 선택 Android 기기를 tcpip 5555 모드로 전환"""
        devices = self.get_selected_devices()
        androids = [d for d in devices if d[0] == "Android"]
        if not androids:
            QMessageBox.information(self, "알림", "먼저 Android 기기를 하나 선택하세요.")
            return
        platform, serial, name, *_ = androids[0]

        def worker():
            self.log(f"[Android][{name}] adb tcpip 5555 전환 시도...")
            out = run_cmd(["adb", "-s", serial, "tcpip", "5555"], timeout=30)
            self.log(f"[Android][{name}] adb tcpip 결과: {out.strip()}")

        threading.Thread(target=worker, daemon=True).start()

    def android_connect_wifi(self):
        """IP:포트로 adb connect"""
        host = self.edit_adb_wifi.text().strip()
        if not host:
            QMessageBox.warning(self, "경고", "먼저 IP:포트 형식으로 주소를 입력하세요. (예: 192.168.0.10:5555)")
            return

        def worker():
            self.log(f"[Android] adb connect {host} 시도...")
            out = run_cmd(["adb", "connect", host], timeout=30)
            self.log(f"[Android] adb connect 결과: {out.strip()}")

        threading.Thread(target=worker, daemon=True).start()

    def android_disconnect_wifi(self):
        """IP:포트(또는 전체)에 대해 adb disconnect"""
        host = self.edit_adb_wifi.text().strip()

        def worker():
            if host:
                self.log(f"[Android] adb disconnect {host} 시도...")
                out = run_cmd(["adb", "disconnect", host], timeout=30)
            else:
                self.log("[Android] adb disconnect (전체) 시도...")
                out = run_cmd(["adb", "disconnect"], timeout=30)
            self.log(f"[Android] adb disconnect 결과: {out.strip()}")

        threading.Thread(target=worker, daemon=True).start()

    def android_pair_wifi(self):
        """Android 11+ 무선 디버깅용 adb pair 실행"""
        host = self.edit_adb_pair_host.text().strip()
        code = self.edit_adb_pair_code.text().strip()

        if not host or ":" not in host:
            QMessageBox.warning(self, "경고", "먼저 핸드폰 '무선 디버깅' 화면에 표시된 IP:포트 값을 입력하세요.\n예: 192.168.0.10:37099")
            return
        if not code:
            QMessageBox.warning(self, "경고", "무선 디버깅 '페어링 코드'를 입력하세요.")
            return

        def worker():
            self.log(f"[Android] adb pair {host} 시도...")
            out = run_cmd(["adb", "pair", host, code], timeout=60)
            self.log(f"[Android] adb pair 결과: {out.strip()}")

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _escape_for_adb_input(text: str) -> str:
        """
        adb shell input text용 간단 이스케이프.
        - 공백은 %s로 치환
        - 일부 셸 특수문자는 백슬래시로 이스케이프
        ※ 한글/특수문자는 기기 키보드 설정에 따라 동작이 다를 수 있음.
        """
        mapping = {
            " ": "%s",
            "&": r"\&",
            "|": r"\|",
            "<": r"\<",
            ">": r"\>",
            ";": r"\;",
            "(": r"\(",
            ")": r"\)",
            "'": r"\'",
            '"': r"\"",
        }
        out = []
        for ch in text:
            out.append(mapping.get(ch, ch))
        return "".join(out)

    def _android_text_send_thread(self, targets, lines):
        """선택된 Android 기기에 줄 단위 텍스트를 전송 (adb shell input text)"""
        for platform, serial, name, *_ in targets:
            for ln in lines:
                escaped = self._escape_for_adb_input(ln)
                self.log(f"[Android][{name}] 텍스트 전송: {ln}")
                # 텍스트 입력
                out = run_cmd(["adb", "-s", serial, "shell", "input", "text", escaped], timeout=30)
                self.add_history("SEND_TEXT", "Android", serial, name, "TEXT", out.strip() or ln)
                # 줄 바꿈(Enter) 입력
                run_cmd(["adb", "-s", serial, "shell", "input", "keyevent", "66"], timeout=10)


    def android_screenshot_selected(self):
        if not self.save_dir:
            QMessageBox.warning(self, "경고", "먼저 저장 루트 폴더를 선택하세요.")
            return
        targets = [d for d in self.get_selected_devices() if d[0] == "Android"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 Android 디바이스가 없습니다.")
            return

        self.log(f"[Android] 스크린샷 캡처 시작 ({len(targets)}대)")
        threading.Thread(target=self._android_screenshot_thread, args=(targets,), daemon=True).start()

    def _android_screenshot_thread(self, targets):
        for dev in targets:
            platform, serial, name, os_ver = dev[:4]
            arch = dev[4] if len(dev) > 4 else ""
            ts = time.strftime("%Y%m%d_%H%M%S")
            suffix = f"_{ts}_screenshot.png"
            filename = self._build_device_filename(platform, name, os_ver, arch, suffix)
            path = os.path.join(self.save_dir, filename)
            ok, error_msg = android_screenshot(serial, path)

            if ok:
                self.log(f"[Android][{name}] ✅ 스크린샷 저장: {path}")
                self.add_history("SCREENSHOT", "Android", serial, name, filename, "OK")
                self._reveal_path(path)
            else:
                self.log(f"[Android][{name}] ❌ 스크린샷 실패: {error_msg}")
                self.add_history("SCREENSHOT", "Android", serial, name, filename, f"FAIL: {error_msg}")
                self.show_error(f"[Android][{name}] 스크린샷 실패\n\n{error_msg}")

    def android_record_selected(self):
        """
        Android 화면 녹화 시작/중지 토글.
        - 시작: 현재 선택된 첫 번째 Android 기기에 대해 screenrecord 실행
        - 중지: 다시 누르면 즉시 녹화 중지 요청 (최대 시간은 스핀박스로 제한)
        """
        # 이미 녹화 중이면 중지 (기기 쪽 screenrecord에 SIGINT 전달해서 정상 종료 유도)
        if self._android_record_proc is not None:
            serial = self._android_record_serial
            name = self._android_record_name or serial
            self.log(f"[Android][{name}] 화면 녹화 중지 요청 (SIGINT)")

            def stopper():
                # 기기에서 실행 중인 screenrecord에 SIGINT 전달
                if serial:
                    run_cmd(["adb", "-s", serial, "shell", "pkill", "-INT", "screenrecord"], timeout=5)
                # 프로세스가 종료될 때까지 잠시 대기
                try:
                    self._android_record_proc.wait(timeout=10)
                except Exception:
                    pass

            threading.Thread(target=stopper, daemon=True).start()
            # UI 정리는 record 스레드의 finally 블록에서 처리
            return

        # 시작
        if not self.save_dir:
            QMessageBox.warning(self, "경고", "먼저 저장 루트 폴더를 선택하세요.")
            return
        targets = [d for d in self.get_selected_devices() if d[0] == "Android"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 Android 디바이스가 없습니다.")
            return

        # 토글 방식은 첫 번째 기기만 지원 (UI 단순화를 위해)
        dev = targets[0]
        platform, serial, name, os_ver = dev[:4]
        arch = dev[4] if len(dev) > 4 else ""

        # 시간 제한 없이 screenrecord 실행 (기기 기본 제한까지만).
        # 사용자가 버튼으로 중간에 중지한다.
        self.log(f"[Android][{name}] 화면 녹화 시작 (시간 제한 없음, 다시 누르면 중지)")
        self.android_record_state_changed.emit(True)
        threading.Thread(target=self._android_record_thread, args=(platform, serial, name, os_ver, arch), daemon=True).start()

    def _android_record_thread(self, platform, serial, name, os_ver, arch):
        """adb shell screenrecord 를 사용해 화면 녹화 (단일 기기, 토글 지원)"""
        ts = time.strftime("%Y%m%d_%H%M%S")
        suffix = f"_{ts}_screenrecord.mp4"
        filename = self._build_device_filename(platform, name, os_ver, arch, suffix)
        local_path = os.path.join(self.save_dir, filename)

        # 기기 내부 임시 경로
        remote_name = f"adb_screenrecord_{ts}.mp4"
        remote_path = f"/sdcard/{remote_name}"

        self._android_record_serial = serial
        self._android_record_name = name

        try:
            # screenrecord 실행 (시간 제한 없이, 기기 기본 제한까지 동작)
            cmd = [
                "adb", "-s", serial, "shell",
                "screenrecord",
                remote_path,
            ]
            self.log(f"[Android][{name}] screenrecord 실행: {' '.join(cmd)}")

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            self._android_record_proc = proc

            out_lines = []
            try:
                while True:
                    line = proc.stdout.readline()
                    if not line:
                        if proc.poll() is not None:
                            break
                        continue
                    out_lines.append(line.rstrip("\n"))
            finally:
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()

            out = "\n".join(out_lines)
            if out.strip():
                self.log(f"[Android][{name}] screenrecord 출력:\n{out.strip()}")

            # 파일 pull
            self.log(f"[Android][{name}] 녹화 파일 pull: {remote_path} → {local_path}")
            pull_out = run_cmd(["adb", "-s", serial, "pull", remote_path, local_path], timeout=60)

            if "pulled" in pull_out.lower() or os.path.exists(local_path):
                self.log(f"[Android][{name}] ✅ 화면 녹화 저장: {local_path}")
                self.add_history("SCREENRECORD", "Android", serial, name, filename, "OK")
                self._reveal_path(local_path)
            else:
                self.log(f"[Android][{name}] ❌ 화면 녹화 pull 실패: {pull_out.strip()}")
                self.add_history("SCREENRECORD", "Android", serial, name, filename, f"FAIL: {pull_out.strip()}")
                self.show_error(
                    f"[Android][{name}] 화면 녹화 파일을 PC로 가져오지 못했습니다.\n\n"
                    f"screenrecord 출력:\n{out.strip()}\n\npull 출력:\n{pull_out.strip()}"
                )
        finally:
            # 기기 쪽 임시 파일 삭제
            run_cmd(["adb", "-s", serial, "shell", "rm", "-f", remote_path], timeout=10)
            # 상태 정리
            self._android_record_proc = None
            self._android_record_serial = None
            self._android_record_name = None
            self.android_record_state_changed.emit(False)

    # ---------------- iOS 로그 / 크래시 / 스크린샷 ----------------

    def ios_log_capture_selected(self):
        targets = [d for d in self.get_selected_devices() if d[0] == "iOS"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 iOS 디바이스가 없습니다.")
            return
        # 첫 번째 iOS 기기 기준으로 실시간 로그 팝업 표시
        platform, udid, name, *_ = targets[0]
        self.log(f"[iOS][{name}] 실시간 syslog 팝업 열기")
        dlg = IosLogLiveDialog(udid, name, self)
        dlg.exec()

    def _ios_log_thread(self, targets, sec):
        for dev in targets:
            platform, udid, name, os_ver = dev[:4]
            arch = dev[4] if len(dev) > 4 else ""
            ts = time.strftime("%Y%m%d_%H%M%S")
            suffix = f"_{ts}_syslog.txt"
            filename = self._build_device_filename(platform, name, os_ver, arch, suffix)
            path = os.path.join(self.save_dir, filename)
            self.log(f"[iOS][{name}] syslog {sec}초 캡처 중...")

            try:
                proc = subprocess.Popen(
                    ["idevicesyslog", "-u", udid],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                )
            except Exception as e:
                self.log(f"[iOS][{name}] idevicesyslog 시작 실패: {e}")
                self.add_history("LOG", "iOS", udid, name, filename, f"FAIL: {e}")
                self.show_error(f"[iOS][{name}] syslog 시작 실패: {e}")
                continue

            lines = []
            start = time.time()
            try:
                while time.time() - start < sec:
                    line = proc.stdout.readline()
                    if not line:
                        break
                    lines.append(line.rstrip("\n"))
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()

            try:
                with open(path, "w", encoding="utf-8", errors="ignore") as f:
                    f.write("\n".join(lines))
                self.log(f"[iOS][{name}] syslog 저장 완료: {path}")
                self.add_history("LOG", "iOS", udid, name, filename, "OK")
                self._reveal_path(path)
            except Exception as e:
                self.log(f"[iOS][{name}] syslog 저장 실패: {e}")
                self.add_history("LOG", "iOS", udid, name, filename, f"FAIL: {e}")
                self.show_error(f"[iOS][{name}] syslog 저장 실패: {e}")

    def ios_crash_export_selected(self):
        if not self.save_dir:
            QMessageBox.warning(self, "경고", "먼저 저장 루트 폴더를 선택하세요.")
            return
        targets = [d for d in self.get_selected_devices() if d[0] == "iOS"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 iOS 디바이스가 없습니다.")
            return

        self.log(f"[iOS] 크래시 리포트 추출 시작 ({len(targets)}대)")
        threading.Thread(target=self._ios_crash_thread, args=(targets,), daemon=True).start()

    def _ios_crash_thread(self, targets):
        for dev in targets:
            platform, udid, name, os_ver = dev[:4]
            arch = dev[4] if len(dev) > 4 else ""
            dirname = self._build_device_filename(platform, name, os_ver, arch, "_crash")
            out_dir = os.path.join(self.save_dir, dirname)
            os.makedirs(out_dir, exist_ok=True)
            self.log(f"[iOS][{name}] crashreport 추출 중... (dir={out_dir})")
            out = run_cmd(["idevicecrashreport", "-u", udid, "-e", out_dir], timeout=120)
            self.add_history("CRASH", "iOS", udid, name, out_dir, out.strip())
            if "Error" in out or "failed" in out.lower():
                self.show_error(f"[iOS][{name}] crashreport 실패: {out}")
            else:
                self.log(f"[iOS][{name}] crashreport 완료:\n{out}")
                self._reveal_path(out_dir)

    def ios_screenshot_selected(self):
        targets = [d for d in self.get_selected_devices() if d[0] == "iOS"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 iOS 디바이스가 없습니다.")
            return

        self.log(f"[iOS] 스크린샷 캡처 시작 ({len(targets)}대, Xcode Devices 사용)")
        threading.Thread(target=self._ios_screenshot_thread, args=(targets,), daemon=True).start()

    def _ios_screenshot_thread(self, targets):
        for platform, udid, name, *_ in targets:
            # idevicescreenshot 대신 항상 Xcode Devices & Simulators의
            # 'Take Screenshot' 버튼을 osascript로 눌러 스크린샷을 촬영한다.
            self.log(f"[iOS][{name}] Xcode Devices 창을 통해 스크린샷 시도 (osascript)...")
            ok2, msg2 = ios_screenshot_via_xcode(name)
            if ok2:
                # 실제 파일 경로는 Xcode 설정(보통 데스크톱)에 따라 달라지므로,
                # 여기서는 트리거 성공만 기록한다.
                self.log(f"[iOS][{name}] ✅ Xcode에서 스크린샷 트리거 완료 (저장 위치: Xcode 설정)")
                self.add_history("SCREENSHOT", "iOS", udid, name, "XcodeScreenshot", "OK (Xcode)")
            else:
                detailed_error = (
                    f"[iOS][{name}] 스크린샷 실패 (Xcode Devices)\n\n"
                    "QuickTime / Xcode / iOS 환경에 따라 자동 조작이 실패할 수 있습니다.\n\n"
                    "✅ 수동 캡처 방법:\n"
                    "  1. Xcode → Window → Devices and Simulators\n"
                    "  2. 좌측에서 디바이스 선택\n"
                    "  3. 우측 하단 'Take Screenshot' 버튼 클릭\n\n"
                    f"🔧 osascript 에러:\n{msg2}"
                )
                # 팝업은 띄우지 않고 로그에만 남긴다.
                self.log(f"[iOS][{name}] ❌ 스크린샷 실패 (Xcode Devices): {msg2}")

    def ios_quicktime_record_selected(self):
        """
        QuickTime Player를 사용해 iOS 화면 녹화를 시작/중지 트리거.
        - QuickTime의 '새로운 동영상 녹화' 창에서 iPhone을 카메라/마이크로 선택해둔 상태라고 가정.
        - 이 버튼을 누르면 QuickTime의 전면 창에서 녹화 버튼을 눌러 (토글) 녹화를 시작/중지한다.
        """
        targets = [d for d in self.get_selected_devices() if d[0] == "iOS"]
        if not targets:
            QMessageBox.information(self, "알림", "먼저 iOS 기기를 하나 선택하세요.")
            return

        platform, udid, name, *_ = targets[0]
        self.log(f"[iOS][{name}] QuickTime 화면 녹화 토글 요청")

        def worker():
            ok, msg = ios_quicktime_start_recording()
            if ok:
                self.log(f"[iOS][{name}] ✅ QuickTime 녹화 버튼 클릭 완료 (녹화 시작/중지 토글)")
                # QuickTime 쪽에서 파일 저장은 사용자가 수동으로 수행
            else:
                self.log(f"[iOS][{name}] ❌ QuickTime 녹화 제어 실패: {msg}")
                self.show_error(
                    f"[iOS][{name}] QuickTime 녹화 제어 실패\n\n"
                    "QuickTime Player가 설치/실행되어 있고,\n"
                    "한 번 이상 '새로운 동영상 녹화'에서 iPhone을 카메라로 선택했는지 확인하세요.\n\n"
                    f"세부 오류:\n{msg}"
                )

        threading.Thread(target=worker, daemon=True).start()

    # ---------------- 설치/배치 ----------------

    def android_batch_run_single_or_folder(self):
        targets = [d for d in self.get_selected_devices() if d[0] == "Android"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 Android 디바이스가 없습니다.")
            return

        files = []
        single = self.edit_apk.text().strip()
        folder = self.apk_folder

        if single and os.path.isfile(single):
            files.append(single)
        if folder and os.path.isdir(folder):
            apk_list = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".apk")]
            apk_list.sort()
            files.extend(apk_list)

        files = list(dict.fromkeys(files))
        if not files:
            QMessageBox.warning(self, "경고", "설치할 APK 파일이 없습니다.")
            return

        pkg = self.edit_android_pkg.text().strip()
        # 실행(앱 자동 실행)이 체크된 경우에만 패키지명 필수
        if self.opt_run_after and not pkg:
            QMessageBox.warning(self, "경고", "실행을 위해 패키지명이 필요합니다. Android 앱 리스트에서 선택해주세요.")
            return

        self.log(f"[Android] 배치 실행 시작: 파일 {len(files)}개, 대상 {len(targets)}대")
        threading.Thread(target=self._android_batch_thread, args=(targets, files, pkg), daemon=True).start()

    def _android_delete_only_thread(self, targets, pkg_str):
        """Android 앱 삭제 전용 (설치 없이, 다중 패키지 지원)"""
        # 콤마로 구분된 패키지들 파싱
        pkgs = [p.strip() for p in pkg_str.split(",") if p.strip()]
        
        for platform, serial, name, *_ in targets:
            for pkg in pkgs:
                self.log(f"[Android][{name}] 앱 삭제: {pkg}")
                out = run_cmd(["adb", "-s", serial, "uninstall", pkg], timeout=120)
                self.add_history("UNINSTALL_ONLY", "Android", serial, name, pkg, out.strip())
                if "Success" in out:
                    self.log(f"[Android][{name}] ✅ 삭제 성공: {pkg}")
                elif "Failure" in out or "failed" in out.lower():
                    self.log(f"[Android][{name}] ❌ 삭제 실패: {pkg} - {out.strip()}")
                else:
                    self.log(f"[Android][{name}] 삭제 결과: {pkg} - {out.strip()}")
        
        self.log(f"[Android] 삭제 완료: {len(pkgs)}개 패키지 × {len(targets)}대 기기")

    def _android_batch_thread(self, targets, files, pkg_str):
        # 콤마로 구분된 패키지들 파싱
        pkgs = [p.strip() for p in pkg_str.split(",") if p.strip()] if pkg_str else []
        first_pkg = pkgs[0] if pkgs else ""

        # 진행률/요약 집계
        total_install_steps = len(targets) * len(files) if self.opt_install and files else 0
        done_install_steps = 0
        summary = {}
        for dev in targets:
            platform, serial, name, *_ = dev
            summary[serial] = {"name": name, "install_ok": 0, "install_fail": 0, "run_ok": 0, "run_fail": 0}

        for platform, serial, name, *_ in targets:
            # 1) 설치 전 항상 삭제 (패키지명이 있고, 설치할 예정이면)
            if self.opt_install and pkgs:
                for pkg in pkgs:
                    self.log(f"[Android][{name}] 기존 앱 삭제 (설치 준비): {pkg}")
                    out = run_cmd(["adb", "-s", serial, "uninstall", pkg], timeout=120)
                    # 이미 없는 경우는 에러 무시
                    if "Success" in out:
                        self.log(f"[Android][{name}] ✅ 삭제 성공: {pkg}")
                    elif "not installed" in out.lower():
                        self.log(f"[Android][{name}] ℹ️  앱이 없음 (신규 설치): {pkg}")
                    else:
                        self.log(f"[Android][{name}] ⚠️  삭제 결과: {out.strip()}")
            # 1-1) 삭제만 체크된 경우 (설치 없이 삭제만)
            elif self.opt_delete_before and pkgs:
                for pkg in pkgs:
                    self.log(f"[Android][{name}] 기존 앱 삭제: {pkg}")
                    out = run_cmd(["adb", "-s", serial, "uninstall", pkg], timeout=120)
                    self.add_history("UNINSTALL", "Android", serial, name, pkg, out.strip())
                    if "Success" in out:
                        self.log(f"[Android][{name}] ✅ 삭제 성공: {pkg}")
                    elif "Failure" in out or "failed" in out.lower():
                        self.log(f"[Android][{name}] ❌ 삭제 실패: {pkg}")
            
            # 2) 설치 및 실행 (각 APK 파일마다)
            for apk in files:
                filename = os.path.basename(apk)
                
                # 설치 (이미 삭제했으므로 -r 옵션 불필요하지만 안전하게 유지)
                if self.opt_install:
                    self.log(f"[Android][{name}] APK 설치: {filename}")
                    out = run_cmd(["adb", "-s", serial, "install", "-r", apk], timeout=300)
                    self.add_history("INSTALL", "Android", serial, name, filename, out.strip())
                    if "Success" in out:
                        self.log(f"[Android][{name}] ✅ 설치 성공: {filename}")
                        if serial in summary:
                            summary[serial]["install_ok"] += 1
                    elif "Failure" in out or "failed" in out.lower():
                        self.log(f"[Android][{name}] ❌ 설치 실패: {filename}")
                        if serial in summary:
                            summary[serial]["install_fail"] += 1

                    if total_install_steps:
                        done_install_steps += 1
                        percent = int(done_install_steps / total_install_steps * 100)
                        self.log(f"[Android] 배치 진행률: {done_install_steps}/{total_install_steps} ({percent}%)")
                        self.batch_progress_changed.emit("Android 배치 설치", percent)
                
                # 실행 (첫 번째 패키지만)
                if self.opt_run_after and first_pkg:
                    self.log(f"[Android][{name}] 앱 실행: {first_pkg}")
                    out = run_cmd([
                        "adb", "-s", serial, "shell", "monkey",
                        "-p", first_pkg, "-c", "android.intent.category.LAUNCHER", "1"
                    ], timeout=30)
                    self.add_history("RUN", "Android", serial, name, first_pkg, out.strip())
                    if "Error" in out or "Exception" in out:
                        self.log(f"[Android][{name}] ❌ 실행 실패: {first_pkg}")
                        if serial in summary:
                            summary[serial]["run_fail"] += 1
                    else:
                        self.log(f"[Android][{name}] ✅ 실행 성공: {first_pkg}")
                        if serial in summary:
                            summary[serial]["run_ok"] += 1

        if summary:
            self.log("[Android] 배치 실행 요약:")
            for serial, info in summary.items():
                name = info["name"]
                self.log(
                    f" - {name} ({serial}): "
                    f"설치 성공 {info['install_ok']} / 실패 {info['install_fail']}, "
                    f"실행 성공 {info['run_ok']} / 실패 {info['run_fail']}"
                )
        # Android 쪽 배치 끝나면 진행률 바를 100%로 맞추고 표시만 '완료'로 변경
        if total_install_steps:
            self.batch_progress_changed.emit("Android 배치 완료", 100)

    def _android_run_only_thread(self, targets, pkg_str):
        """설치 없이 이미 설치된 Android 앱만 실행 (다중 패키지 지원)"""
        pkgs = [p.strip() for p in pkg_str.split(",") if p.strip()]
        if not pkgs:
            return
        first_pkg = pkgs[0]

        for platform, serial, name, *_ in targets:
            self.log(f"[Android][{name}] 실행 전용: {first_pkg}")
            out = run_cmd([
                "adb", "-s", serial, "shell", "monkey",
                "-p", first_pkg, "-c", "android.intent.category.LAUNCHER", "1"
            ], timeout=30)
            self.add_history("RUN_ONLY", "Android", serial, name, first_pkg, out.strip())
            if "Error" in out or "Exception" in out:
                self.log(f"[Android][{name}] ❌ 실행 실패: {first_pkg}")
            else:
                self.log(f"[Android][{name}] ✅ 실행 성공: {first_pkg}")

    def ios_batch_run_single_or_folder(self):
        targets = [d for d in self.get_selected_devices() if d[0] == "iOS"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 iOS 디바이스가 없습니다.")
            return

        files = []
        single = self.edit_ipa.text().strip()
        folder = self.ipa_folder

        if single and os.path.isfile(single):
            files.append(single)
        if folder and os.path.isdir(folder):
            ipa_list = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".ipa")]
            ipa_list.sort()
            files.extend(ipa_list)

        files = list(dict.fromkeys(files))
        if not files:
            QMessageBox.warning(self, "경고", "설치할 IPA 파일이 없습니다.")
            return

        bundle = self.edit_ios_bundle.text().strip()
        # 실행(앱 자동 실행)이 체크된 경우에만 번들ID 필수
        if self.opt_run_after and not bundle:
            QMessageBox.warning(self, "경고", "실행을 위해 번들ID가 필요합니다. iOS 앱 리스트에서 선택해주세요.")
            return

        self.log(f"[iOS] 배치 실행 시작: 파일 {len(files)}개, 대상 {len(targets)}대")
        threading.Thread(target=self._ios_batch_thread, args=(targets, files, bundle), daemon=True).start()

    def _ios_delete_only_thread(self, targets, bundle_str):
        """iOS 앱 삭제 전용 (설치 없이, 다중 번들 지원)"""
        # 콤마로 구분된 번들ID들 파싱
        bundles = [b.strip() for b in bundle_str.split(",") if b.strip()]
        
        for platform, udid, name, *_ in targets:
            for bundle in bundles:
                self.log(f"[iOS][{name}] 앱 삭제: {bundle}")
                # ideviceinstaller 1.2.0+ 명령어 형식
                out = run_cmd(["ideviceinstaller", "-u", udid, "uninstall", bundle], timeout=120)
                self.add_history("UNINSTALL_ONLY", "iOS", udid, name, bundle, out.strip())
                if "Complete" in out or "Uninstalled" in out or "Removed" in out:
                    self.log(f"[iOS][{name}] ✅ 삭제 성공: {bundle}")
                elif "Error" in out or "failed" in out.lower():
                    self.log(f"[iOS][{name}] ❌ 삭제 실패: {bundle} - {out.strip()}")
                else:
                    self.log(f"[iOS][{name}] 삭제 결과: {bundle} - {out.strip()}")
        
        self.log(f"[iOS] 삭제 완료: {len(bundles)}개 번들 × {len(targets)}대 기기")

    def _ios_batch_thread(self, targets, files, bundle_str):
        # 콤마로 구분된 번들ID들 파싱
        bundles = [b.strip() for b in bundle_str.split(",") if b.strip()] if bundle_str else []
        first_bundle = bundles[0] if bundles else ""

        # 진행률/요약 집계
        total_install_steps = len(targets) * len(files) if self.opt_install and files else 0
        done_install_steps = 0
        summary = {}
        for dev in targets:
            platform, udid, name, *_ = dev
            summary[udid] = {"name": name, "install_ok": 0, "install_fail": 0, "run_ok": 0, "run_fail": 0}

        for platform, udid, name, *_ in targets:
            # 1) 설치 전 항상 삭제 (번들ID가 있고, 설치할 예정이면)
            if self.opt_install and bundles:
                for bundle in bundles:
                    self.log(f"[iOS][{name}] 기존 앱 삭제 (설치 준비): {bundle}")
                    # ideviceinstaller 1.2.0+ 명령어 형식
                    out = run_cmd(["ideviceinstaller", "-u", udid, "uninstall", bundle], timeout=120)
                    # 이미 없는 경우는 에러 무시
                    if "Complete" in out or "Uninstalled" in out or "Removed" in out:
                        self.log(f"[iOS][{name}] ✅ 삭제 성공: {bundle}")
                    elif "not found" in out.lower() or "not installed" in out.lower():
                        self.log(f"[iOS][{name}] ℹ️  앱이 없음 (신규 설치): {bundle}")
                    else:
                        self.log(f"[iOS][{name}] ⚠️  삭제 결과: {out.strip()}")
            # 1-1) 삭제만 체크된 경우 (설치 없이 삭제만)
            elif self.opt_delete_before and bundles:
                for bundle in bundles:
                    self.log(f"[iOS][{name}] 기존 앱 삭제: {bundle}")
                    # ideviceinstaller 1.2.0+ 명령어 형식
                    out = run_cmd(["ideviceinstaller", "-u", udid, "uninstall", bundle], timeout=120)
                    self.add_history("UNINSTALL", "iOS", udid, name, bundle, out.strip())
                    if "Complete" in out or "Uninstalled" in out or "Removed" in out:
                        self.log(f"[iOS][{name}] ✅ 삭제 성공: {bundle}")
                    elif "Error" in out or "failed" in out.lower():
                        self.log(f"[iOS][{name}] ❌ 삭제 실패: {bundle}")
            
            # 2) 설치 및 실행 (각 IPA 파일마다)
            for ipa in files:
                filename = os.path.basename(ipa)
                
                # 설치 (이미 삭제했으므로 항상 새로 설치)
                if self.opt_install:
                    self.log(f"[iOS][{name}] IPA 설치: {filename}")
                    # 우선순위: ideviceinstaller → (없으면) cfgutil
                    cmd = None
                    if shutil.which("ideviceinstaller") is not None:
                        cmd = ["ideviceinstaller", "-u", udid, "install", ipa]
                    elif os.path.exists(CFGUTIL_PATH):
                        # cfgutil 전역 옵션(--udid ...)은 서브커맨드 앞에 와야 함
                        # 예: cfgutil --udid <udid> install-app <ipa>
                        cmd = [CFGUTIL_PATH, "--udid", udid, "install-app", ipa]
                    else:
                        self.log("[iOS] ❌ 설치 도구를 찾을 수 없습니다. ideviceinstaller 또는 Apple Configurator(cfgutil)가 필요합니다.")
                        self.add_history("INSTALL", "iOS", udid, name, filename, "NO_INSTALL_TOOL")
                        continue
                    out = run_cmd(cmd, timeout=300)
                    self.add_history("INSTALL", "iOS", udid, name, filename, out.strip())
                    if "Complete" in out or "Installed" in out:
                        self.log(f"[iOS][{name}] ✅ 설치 성공: {filename}")
                        if udid in summary:
                            summary[udid]["install_ok"] += 1
                    elif "Error" in out or "failed" in out.lower():
                        # 실패 사유 전체 로그 출력
                        self.log(f"[iOS][{name}] ❌ 설치 실패: {filename}\n{out.strip()}")
                        self.show_error(f"[iOS][{name}] 설치 실패:\n\n{out.strip()}")
                        if udid in summary:
                            summary[udid]["install_fail"] += 1

                    if total_install_steps:
                        done_install_steps += 1
                        percent = int(done_install_steps / total_install_steps * 100)
                        self.log(f"[iOS] 배치 진행률: {done_install_steps}/{total_install_steps} ({percent}%)")
                        self.batch_progress_changed.emit("iOS 배치 설치", percent)
                
                # 실행 (첫 번째 번들만)
                # NOTE: iOS 자동 실행 기능은 현재 환경 제약(iOS 18.x + idevicedebug)으로 비활성화.
                # 설치는 정상 진행되며, 앱 실행은 기기에서 직접 실행하거나
                # 환경이 정리된 후 아래 코드를 다시 활성화해서 사용할 수 있습니다.
                # if self.opt_run_after and first_bundle:
                #     self.log(f"[iOS][{name}] 앱 실행: {first_bundle}")
                #     out = run_cmd(["idevicedebug", "-u", udid, "run", first_bundle], timeout=60)
                #     self.add_history("RUN", "iOS", udid, name, first_bundle, out.strip())
                #     out_lower = out.lower() if out else ""
                #     # idevicedebug 결과를 조금 더 엄격하게 판정:
                #     # - 출력이 완전히 비어있거나
                #     # - error/failed/usage/could not run 등의 문자열을 포함하면 실패로 간주
                #     if (not out.strip() or
                #         "error" in out_lower or
                #         "failed" in out_lower or
                #         "could not" in out_lower or
                #         "usage:" in out_lower):
                #         self.log(f"[iOS][{name}] ❌ 실행 실패: {first_bundle}\n{out.strip()}")
                #         if udid in summary:
                #             summary[udid]["run_fail"] += 1
                #     else:
                #         self.log(f"[iOS][{name}] ✅ 실행 시도 완료 (idevicedebug run): {first_bundle}\n{out.strip()}")
                #         if udid in summary:
                #             summary[udid]["run_ok"] += 1

        if summary:
            self.log("[iOS] 배치 실행 요약:")
            for udid, info in summary.items():
                name = info["name"]
                self.log(
                    f" - {name} ({udid}): "
                    f"설치 성공 {info['install_ok']} / 실패 {info['install_fail']}, "
                    f"실행 성공 {info['run_ok']} / 실패 {info['run_fail']}"
                )

    # def _ios_run_only_thread(self, targets, bundle_str):
    #     """설치 없이 이미 설치된 iOS 앱만 실행 (다중 번들 지원)
    #     현재는 iOS 자동 실행 기능 비활성화로 인해 사용하지 않는다.
    #     필요 시 idevicedebug 환경이 정리된 후 주석을 해제하고 사용할 수 있다.
    #     """
    #     bundles = [b.strip() for b in bundle_str.split(",") if b.strip()]
    #     if not bundles:
    #         return
    #     first_bundle = bundles[0]
    #
    #     for platform, udid, name, *_ in targets:
    #         self.log(f"[iOS][{name}] 실행 전용: {first_bundle}")
    #         out = run_cmd(["idevicedebug", "-u", udid, "run", first_bundle], timeout=60)
    #         self.add_history("RUN_ONLY", "iOS", udid, name, first_bundle, out.strip())
    #         out_lower = out.lower() if out else ""
    #         if (not out.strip() or
    #             "error" in out_lower or
    #             "failed" in out_lower or
    #             "could not" in out_lower or
    #             "usage:" in out_lower):
    #             self.log(f"[iOS][{name}] ❌ 실행 실패: {first_bundle}\n{out.strip()}")
    #         else:
    #             self.log(f"[iOS][{name}] ✅ 실행 시도 완료 (idevicedebug run): {first_bundle}\n{out.strip()}")

    # ---------------- 삭제 전용 버튼 ----------------

    def android_delete_only(self):
        pkg = self.edit_android_pkg.text().strip()
        if not pkg:
            QMessageBox.warning(self, "경고", "삭제할 패키지명을 입력하거나 Android 앱 리스트에서 선택하세요.")
            return
        targets = [d for d in self.get_selected_devices() if d[0] == "Android"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 Android 디바이스가 없습니다.")
            return
        self.log(f"[Android] 삭제 전용 실행: 패키지={pkg}, 대상={len(targets)}대")
        threading.Thread(target=self._android_delete_thread, args=(targets, pkg), daemon=True).start()

    def _android_delete_thread(self, targets, pkg):
        for platform, serial, name, *_ in targets:
            out = run_cmd(["adb", "-s", serial, "uninstall", pkg], timeout=120)
            self.add_history("UNINSTALL_ONLY", "Android", serial, name, pkg, out.strip())
            if "Failure" in out or "failed" in out.lower():
                self.show_error(f"[Android][{name}] 삭제 실패: {out}")
            else:
                self.log(f"[Android][{name}] 삭제 성공: {pkg}")

    def ios_delete_only(self):
        bundle = self.edit_ios_bundle.text().strip()
        if not bundle:
            QMessageBox.warning(self, "경고", "삭제할 번들ID를 입력하거나 iOS 앱 리스트에서 선택하세요.")
            return
        targets = [d for d in self.get_selected_devices() if d[0] == "iOS"]
        if not targets:
            QMessageBox.information(self, "알림", "선택된 iOS 디바이스가 없습니다.")
            return
        self.log(f"[iOS] 삭제 전용 실행: 번들ID={bundle}, 대상={len(targets)}대")
        threading.Thread(target=self._ios_delete_thread, args=(targets, bundle), daemon=True).start()

    def _ios_delete_thread(self, targets, bundle):
        for platform, udid, name, *_ in targets:
            # ideviceinstaller 1.2.0+ 명령어 형식
            out = run_cmd(["ideviceinstaller", "-u", udid, "uninstall", bundle], timeout=120)
            self.add_history("UNINSTALL_ONLY", "iOS", udid, name, bundle, out.strip())
            if "Error" in out or "failed" in out.lower():
                self.show_error(f"[iOS][{name}] 삭제 실패: {out}")
            else:
                self.log(f"[iOS][{name}] 삭제 성공: {bundle}")


# ------------------------------------------------------------
# main
# ------------------------------------------------------------

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
