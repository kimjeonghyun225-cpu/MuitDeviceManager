import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import re
import time
import os
import sys
import threading
from pathlib import Path
from shutil import which

# ================== 상수 및 유틸리티 (기존 로직 유지) ==================
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

APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
if sys.platform.startswith("darwin"):
    _TOOLS_SUBDIR = os.path.join("tools", "mac")
elif sys.platform.startswith("win"):
    _TOOLS_SUBDIR = os.path.join("tools", "win")
else:
    _TOOLS_SUBDIR = os.path.join("tools", "linux")
LOCAL_BIN_DIR = os.path.join(APP_DIR, _TOOLS_SUBDIR)


def tool_path(cmd: str) -> str:
    """내장 tools 폴더 우선 사용 (MultiDeviceManager와 동일 컨셉)."""
    if not cmd:
        return cmd
    # 이미 경로 형태면 그대로 반환
    if os.path.isabs(cmd) or (os.path.sep in cmd) or (os.path.altsep and os.path.altsep in cmd):
        return cmd
    candidates = [cmd + ".exe", cmd] if sys.platform.startswith("win") else [cmd]
    for name in candidates:
        local = os.path.join(LOCAL_BIN_DIR, name)
        if os.path.exists(local):
            return local
    return cmd

def run2(cmd):
    """공용 subprocess 실행 (윈도우 창 숨김 처리 포함)."""
    try:
        # 윈도우 창이 뜨지 않도록 설정 (CREATE_NO_WINDOW)
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        completed = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=False,
            startupinfo=startupinfo,
        )
        return completed.returncode, completed.stdout.strip()
    except Exception as e:
        return 1, f"ERROR: {e}"


def get_android_devices_info(adb_path: str):
    """
    MultiDeviceManager의 get_android_devices와 동일한 정보 구조를 반환.
    return: [{"platform","id","name","os_version","arch"}, ...]
    """
    devices = []
    if not adb_path or not Path(adb_path).exists():
        return devices

    code, out = run2([adb_path, "devices"])
    if code != 0 or not out:
        return devices

    lines = out.splitlines()
    if len(lines) <= 1:
        return devices

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serial = parts[0]

            # 모델명, OS 버전
            code_m, model_out = run2([adb_path, "-s", serial, "shell", "getprop", "ro.product.model"])
            model = model_out.strip() if code_m == 0 else ""
            code_v, ver_out = run2([adb_path, "-s", serial, "shell", "getprop", "ro.build.version.release"])
            version = ver_out.strip() if code_v == 0 else ""

            # ABI / 아키텍처
            code_abi, abi_out = run2([adb_path, "-s", serial, "shell", "getprop", "ro.product.cpu.abi"])
            abi = abi_out.strip() if code_abi == 0 else ""
            code_abil, abilist_out = run2(
                [adb_path, "-s", serial, "shell", "getprop", "ro.product.cpu.abilist"]
            )
            abilist = abilist_out.strip() if code_abil == 0 else ""

            if abilist:
                primary_abi = abilist.split(",")[0].strip()
            else:
                primary_abi = abi

            arch_label = "Unknown"
            if "64" in (abilist or abi):
                arch_label = "64-bit"
            elif abi or abilist:
                arch_label = "32-bit"

            os_version = version or "unknown"
            arch_info = arch_label
            if primary_abi:
                arch_info = primary_abi

            devices.append(
                {
                    "platform": "Android",
                    "id": serial,
                    "name": model if model else serial,
                    "os_version": os_version,
                    "arch": arch_info,
                }
            )
    return devices


def get_ios_devices_info():
    """
    MultiDeviceManager의 get_ios_devices와 동일한 정보 구조를 반환.
    return: [{"platform","id","name","os_version","arch"}, ...]
    """
    devices = []
    idevice_id_path = tool_path("idevice_id")
    if not idevice_id_path or not Path(idevice_id_path).exists():
        return devices

    code, out = run2([idevice_id_path, "-l"])
    if code != 0 or not out:
        return devices

    lines = [l.strip() for l in out.splitlines() if l.strip()]
    for udid in lines:
        ideviceinfo_path = tool_path("ideviceinfo")
        if not ideviceinfo_path or not Path(ideviceinfo_path).exists():
            continue
        code_name, name_out = run2([ideviceinfo_path, "-u", udid, "-k", "DeviceName"])
        code_ver, ver_out = run2([ideviceinfo_path, "-u", udid, "-k", "ProductVersion"])
        name = name_out.strip() if code_name == 0 else ""
        ver = ver_out.strip() if code_ver == 0 else ""

        devices.append(
            {
                "platform": "iOS",
                "id": udid,
                "name": name or udid,
                "os_version": ver or "unknown",
                "arch": "",
            }
        )
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

def list_installed_apps(adb_path, serial):
    """
    MultiDeviceManager의 get_android_packages 로직과 유사하게,
    user 0 기준으로 패키지 목록을 가져오되, 실패 시 기본 pm list packages 로 fallback.
    """
    pkgs = []

    # user 0 기준 조회 (권한 문제 회피)
    code, out = run2([adb_path, "-s", serial, "shell", "pm", "list", "packages", "--user", "0"])

    # 에러 발생 시 user 옵션 없이 재시도
    if code != 0 or "[ERROR]" in out or "Exception" in out or "SecurityException" in out:
        code2, out2 = run2([adb_path, "-s", serial, "shell", "pm", "list", "packages"])
        if code2 != 0:
            return []
        out = out2

    for line in out.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            pkgs.append(line[len("package:") :])

    return sorted(pkgs)


def get_foreground_package(adb_path, serial):
    """현재 전면(포그라운드)에 떠 있는 앱의 패키지명 감지 (여러 패턴/명령어 시도)."""
    # 기기/OS 버전에 따라 출력이 조금씩 달라서 여러 명령/패턴을 순차적으로 시도
    cmds = [
        [adb_path, "-s", serial, "shell", "dumpsys", "window", "windows"],
        [adb_path, "-s", serial, "shell", "dumpsys", "window"],
    ]

    patterns = [
        r"mCurrentFocus=Window\{[^\}]*\s+([A-Za-z0-9._]+)/",
        r"mFocusedApp=.*\s+([A-Za-z0-9._]+)/",
        r"mCurrentFocus=.*\s+([A-Za-z0-9._]+)/[A-Za-z0-9._]+\s*\}",
        r"Window\{[^\}]*\s+([A-Za-z0-9._]+)/[A-Za-z0-9._]+\s*\}",
    ]

    for cmd in cmds:
        code, out = run2(cmd)
        if code != 0 or not out:
            continue
        for pat in patterns:
            m = re.search(pat, out)
            if m:
                pkg = m.group(1)
                # 시스템/런처 계열은 제외
                if pkg and not pkg.startswith("com.android.") and pkg not in [
                    "android",
                    "com.google.android.apps.nexuslauncher",
                ]:
                    return pkg
    return ""


def get_pkg_version(adb_path, serial, user_id, pkg):
    """dumpsys package 를 이용해 versionName / versionCode 조회."""
    code, out = run2([adb_path, "-s", serial, "shell", "dumpsys", "package", pkg])
    if code != 0 or not out:
        return "", ""
    vname = ""
    vcode = ""
    m_code = re.search(r"\bversionCode=(\d+)", out)
    if m_code:
        vcode = m_code.group(1)
    m_name = re.search(r"\bversionName=([^\s]+)", out)
    if m_name:
        vname = m_name.group(1)
    return vname, vcode

# ================== GUI 클래스 (Tkinter) ==================
class CleanerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("🧹 FB Logout Cleaner (GUI)")
        self.root.geometry("700x650")

        # 상태 변수
        self.adb_path = tk.StringVar(value=which("adb") or "")
        self.devices_list = []  # MultiDeviceManager와 동일한 dict 구조 리스트 사용
        self.fixed_packages = []
        self.is_running = False

        # UI 초기화
        self.setup_ui()
        
        # 3초마다 기기 목록 갱신 시작
        self.auto_refresh_devices()

    def log(self, msg):
        """로그 창에 메시지 추가"""
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, msg + "\n")
        self.log_area.see(tk.END)  # 자동 스크롤
        self.log_area.config(state='disabled')

    def setup_ui(self):
        # 1. ADB 설정 영역
        frame_adb = tk.LabelFrame(self.root, text="ADB 설정", padx=5, pady=5)
        frame_adb.pack(fill="x", padx=10, pady=5)
        
        tk.Entry(frame_adb, textvariable=self.adb_path).pack(side="left", fill="x", expand=True, padx=5)
        tk.Button(frame_adb, text="자동 감지", command=self.detect_adb).pack(side="right")

        # 2. 기기 목록 영역
        frame_dev = tk.LabelFrame(self.root, text="연결된 기기 (MultiDeviceManager 스타일)", padx=5, pady=5)
        frame_dev.pack(fill="x", padx=10, pady=5)
        
        # MultiDeviceManager는 플랫폼/ID/이름/OS/아키텍처를 컬럼으로 보여줌
        # Tkinter에서는 간단히 한 줄 문자열로 같은 정보를 표시
        self.listbox_dev = tk.Listbox(frame_dev, selectmode="multiple", height=6)
        self.listbox_dev.pack(side="left", fill="x", expand=True, padx=5)
        
        # 스크롤바
        scrollbar_dev = tk.Scrollbar(frame_dev, orient="vertical")
        scrollbar_dev.config(command=self.listbox_dev.yview)
        scrollbar_dev.pack(side="right", fill="y")
        self.listbox_dev.config(yscrollcommand=scrollbar_dev.set)

        # 기기 관련 기능 버튼 (실행 중 앱 버전 표시)
        dev_btn_frame = tk.Frame(frame_dev)
        dev_btn_frame.pack(fill="x", pady=3)
        tk.Button(
            dev_btn_frame,
            text="실행 중 앱 버전 표시",
            command=self.show_foreground_app_versions,
        ).pack(side="left", padx=5)

        # 3. 앱 선택 영역
        frame_app = tk.LabelFrame(self.root, text="설치된 앱 (선택 후 고정)", padx=5, pady=5)
        frame_app.pack(fill="both", expand=True, padx=10, pady=5)

        btn_frame = tk.Frame(frame_app)
        btn_frame.pack(fill="x", pady=2)
        tk.Button(btn_frame, text="앱 목록 불러오기", command=self.load_apps).pack(side="left", padx=2)
        tk.Button(btn_frame, text="선택 고정", command=self.fix_apps).pack(side="left", padx=2)
        tk.Button(btn_frame, text="초기화", command=self.reset_apps).pack(side="left", padx=2)
        
        self.lbl_fixed = tk.Label(btn_frame, text="고정된 앱: 없음", fg="blue")
        self.lbl_fixed.pack(side="left", padx=10)

        self.listbox_app = tk.Listbox(frame_app, selectmode="multiple")
        self.listbox_app.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        scrollbar_app = tk.Scrollbar(frame_app)
        scrollbar_app.pack(side="right", fill="y")
        self.listbox_app.config(yscrollcommand=scrollbar_app.set)
        scrollbar_app.config(command=self.listbox_app.yview)

        # 4. 옵션 및 실행
        frame_opt = tk.LabelFrame(self.root, text="초기화 옵션", padx=5, pady=5)
        frame_opt.pack(fill="x", padx=10, pady=5)

        self.var_fb = tk.BooleanVar(value=True)
        self.var_ms = tk.BooleanVar(value=True)
        self.var_br = tk.BooleanVar(value=True)

        tk.Checkbutton(frame_opt, text="Facebook", variable=self.var_fb).pack(side="left", padx=10)
        tk.Checkbutton(frame_opt, text="Messenger", variable=self.var_ms).pack(side="left", padx=10)
        tk.Checkbutton(frame_opt, text="Browsers", variable=self.var_br).pack(side="left", padx=10)

        tk.Button(frame_opt, text="🚀 완전 초기화 실행", bg="#ffcccc", command=self.start_cleanup_thread).pack(side="right", padx=10)

        # 5. 로그 영역
        frame_log = tk.LabelFrame(self.root, text="로그", padx=5, pady=5)
        frame_log.pack(fill="both", expand=True, padx=10, pady=5)
        self.log_area = scrolledtext.ScrolledText(frame_log, height=10, state='disabled')
        self.log_area.pack(fill="both", expand=True)

    # --- 로직 ---
    def detect_adb(self):
        path = tool_path("adb")
        if not path or not Path(path).exists():
            path = which("adb") or ""
        self.adb_path.set(path)
        if path:
            messagebox.showinfo("ADB 감지", f"ADB 경로: {path}")
        else:
            messagebox.showwarning("실패", "ADB를 찾을 수 없습니다.")

    def get_adb(self):
        p = self.adb_path.get().strip()
        if not p:
            # 내장 혹은 시스템 감지 시도
            p = tool_path("adb")
            if not os.path.exists(p): p = which("adb") or ""
        return p

    def auto_refresh_devices(self):
        """3초마다 기기 목록 갱신 (MultiDeviceManager와 동일 정보 구조)."""
        adb = self.get_adb()

        android = get_android_devices_info(adb) if adb else []
        ios = get_ios_devices_info()
        devices = android + ios

        # 목록 갱신
        self.devices_list = devices
        self.update_device_listbox()

        # 3초 뒤 재호출
        self.root.after(3000, self.auto_refresh_devices)

    def update_device_listbox(self):
        # 기존 선택 상태 저장 (인덱스 기준)
        selected_indices = set(self.listbox_dev.curselection())

        self.listbox_dev.delete(0, tk.END)
        for idx, d in enumerate(self.devices_list):
            # MultiDeviceManager의 컬럼 순서: platform / id / name / os / arch
            display = f"{d['platform']:6} | {d['id']} | {d['name']} | {d['os_version']} | {d['arch']}"
            self.listbox_dev.insert(tk.END, display)
            if idx in selected_indices:
                self.listbox_dev.selection_set(idx)

    def get_selected_devices(self):
        """선택된 디바이스 dict 리스트 반환."""
        indices = self.listbox_dev.curselection()
        return [self.devices_list[i] for i in indices]

    def load_apps(self):
        adb = self.get_adb()
        devs = [d for d in self.get_selected_devices() if d["platform"] == "Android"]
        if not devs:
            messagebox.showwarning("주의", "Android 기기를 하나 선택해주세요.")
            return

        # 첫 번째 선택 Android 기기 기준
        dev = devs[0]
        serial = dev["id"]
        name = dev["name"]
        self.log(f"[{name} ({serial})] 앱 목록 불러오는 중...")
        apps = list_installed_apps(adb, serial)
        
        self.listbox_app.delete(0, tk.END)
        for app in apps:
            self.listbox_app.insert(tk.END, app)
        self.log(f"-> {len(apps)}개 앱 로드 완료.")

    def fix_apps(self):
        indices = self.listbox_app.curselection()
        if not indices:
            messagebox.showwarning("주의", "앱을 선택해주세요.")
            return
        self.fixed_packages = [self.listbox_app.get(i) for i in indices]
        self.lbl_fixed.config(text=f"고정된 앱: {len(self.fixed_packages)}개")
        self.log(f"선택 고정됨: {', '.join(self.fixed_packages)}")

    def reset_apps(self):
        self.fixed_packages = []
        self.lbl_fixed.config(text="고정된 앱: 없음")
        self.listbox_app.selection_clear(0, tk.END)
        self.log("앱 선택 초기화됨.")

    def start_cleanup_thread(self):
        """GUI 멈춤 방지를 위해 별도 스레드에서 실행"""
        if self.is_running: return
        t = threading.Thread(target=self.run_cleanup)
        t.start()

    def run_cleanup(self):
        adb = self.get_adb()
        if not adb:
            messagebox.showerror("오류", "ADB 경로가 없습니다.")
            return

        # Android 기기만 대상으로 사용
        targets = [d for d in self.get_selected_devices() if d["platform"] == "Android"]
        if not targets:
            messagebox.showwarning("주의", "초기화할 Android 기기를 선택하세요.")
            return

        self.is_running = True
        self.log("=" * 30)
        self.log("🚀 초기화 작업 시작")

        for dev in targets:
            serial = dev["id"]
            name = dev["name"]
            self.log(f"\n>>> [{name} ({serial})] 작업 시작")
            user_ids = get_user_ids(adb, serial)
            self.log(f"    사용자 IDs: {user_ids}")

            # 1. 고정된 사용자 앱 삭제
            if self.fixed_packages:
                self.log("    [고정 앱 데이터 삭제]")
                for pkg in self.fixed_packages:
                    for uid in user_ids:
                        self.clear_pkg(adb, serial, uid, pkg)

            # 2. 페이스북
            if self.var_fb.get():
                for pkg in FACEBOOK_PKGS:
                    for uid in user_ids:
                        self.clear_pkg(adb, serial, uid, pkg)
            
            # 3. 메신저
            if self.var_ms.get():
                for pkg in MESSENGER_PKGS:
                    for uid in user_ids:
                        self.clear_pkg(adb, serial, uid, pkg)

            # 4. 브라우저
            if self.var_br.get():
                for fam, pkgs in BROWSER_FAMILIES.items():
                    for pkg in pkgs:
                        for uid in user_ids:
                            self.clear_pkg(adb, serial, uid, pkg)
            
            self.log(f">>> [{serial}] 완료 ✅")

        self.log("\n✨ 모든 작업 종료 ✨")
        self.log("="*30)
        messagebox.showinfo("완료", "모든 작업이 완료되었습니다.")
        self.is_running = False

    def clear_pkg(self, adb, serial, uid, pkg):
        if package_exists_for_user(adb, serial, uid, pkg):
            code, out = run2([adb, "-s", serial, "shell", "pm", "clear", "--user", uid, pkg])
            if code == 0:
                self.log(f"    - {pkg} (user {uid}): 삭제 완료")
            else:
                # fallback
                code2, _ = run2([adb, "-s", serial, "shell", "pm", "clear", pkg])
                if code2 == 0:
                    self.log(f"    - {pkg} (user {uid}): 삭제 완료(fb)")
                else:
                    self.log(f"    - {pkg} (user {uid}): 실패")

    def show_foreground_app_versions(self):
        """선택/또는 전체 Android 기기의 현재 실행 중인 앱 패키지 + 버전 표시."""
        adb = self.get_adb()
        if not adb:
            messagebox.showerror("오류", "ADB 경로가 없습니다.")
            return

        # 우선 선택된 Android 기기, 없으면 전체 Android 기기
        selected_android = [d for d in self.get_selected_devices() if d["platform"] == "Android"]
        if not selected_android:
            selected_android = [d for d in self.devices_list if d["platform"] == "Android"]

        if not selected_android:
            messagebox.showwarning("주의", "Android 기기가 없습니다.")
            return

        self.log("=" * 30)
        self.log("📱 실행 중 앱 버전 표시")

        for dev in selected_android:
            serial = dev["id"]
            name = dev["name"]
            self.log(f"[{name} ({serial})] 전면 앱 감지 중...")
            pkg = get_foreground_package(adb, serial)
            if not pkg:
                self.log(f"[{name} ({serial})] ❌ 전면 앱을 감지하지 못했습니다.")
                continue

            user_ids = get_user_ids(adb, serial)
            uid = user_ids[0] if user_ids else "0"
            vname, vcode = get_pkg_version(adb, serial, uid, pkg)

            if not vname and not vcode:
                self.log(f"[{name} ({serial})] 📱 현재 앱: {pkg}")
            else:
                info = []
                if vname:
                    info.append(f"v{vname}")
                if vcode:
                    info.append(f"빌드:{vcode}")
                self.log(f"[{name} ({serial})] 📱 현재 앱: {pkg} ({', '.join(info)})")

if __name__ == "__main__":
    root = tk.Tk()
    app = CleanerApp(root)
    root.mainloop()