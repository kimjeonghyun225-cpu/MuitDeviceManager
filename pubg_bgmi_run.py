import streamlit as st
import json, os, platform, subprocess, re, time
from pathlib import Path
from shutil import which

st.set_page_config(page_title="통합 도구", layout="wide", page_icon="🛠️")

st.title("🛠️ 통합 도구 모음")
st.markdown("---")

# 좌우 2개 컬럼으로 분할
left_col, right_col = st.columns(2)

# ============= 왼쪽: Quick Text Sender =============
with left_col:
    st.header("Quick Text Sender")
    st.caption("🤖 Android: 자동 전송 | 🍎 iOS: 클립보드 복사")
    
    SLOTS = 10
    DATA_FILE = "quick_slots.json"
    IS_MAC = platform.system() == "Darwin"

    def run_cmd(cmd, input_text=None):
        try:
            p = subprocess.run(
                cmd,
                input=(input_text.encode("utf-8") if input_text is not None else None),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
            )
            return p.returncode, p.stdout.decode(errors="ignore"), p.stderr.decode(errors="ignore")
        except FileNotFoundError:
            return 127, "", "Command not found"
        except Exception as e:
            return 1, "", str(e)

    # ---------- Android ----------
    def adb_escape_text(s: str) -> str:
        s = s.replace("\\", "\\\\").replace(" ", "%s")
        for ch in ['&','|',';','<','>','(',')','$','"',"'",'*','!','#','?']:
            s = s.replace(ch, '\\'+ch)
        return s

    def adb_list_devices():
        code, out, err = run_cmd(["adb", "devices"])
        if code != 0:
            return []
        devs = []
        for line in out.splitlines()[1:]:
            parts = line.strip().split()
            if len(parts) >= 2 and parts[1] == "device":
                devs.append(parts[0])
        return devs

    def adb_send_text(serial: str, text: str):
        if not serial:
            devs = adb_list_devices()
            if not devs:
                return False
            serial = devs[0]
        payload = adb_escape_text(text)
        code, out, err = run_cmd(["adb", "-s", serial, "shell", "input", "text", payload])
        return code == 0

    # ---------- iOS Simulator ----------
    def sim_list_booted():
        if not IS_MAC:
            return []
        code, out, err = run_cmd(["xcrun", "simctl", "list", "--json"])
        if code != 0:
            return []
        booted = []
        try:
            data = json.loads(out)
            for arr in data.get("devices", {}).values():
                for d in arr:
                    if d.get("state") == "Booted":
                        booted.append((d.get("name"), d.get("udid")))
        except:
            pass
        return booted

    def sim_pbcopy(udid: str, text: str):
        if not IS_MAC:
            return False
        code, out, err = run_cmd(["xcrun", "simctl", "pbcopy", udid], input_text=text)
        return code == 0

    def mac_cmd_v():
        code, out, err = run_cmd(["osascript", "-e",
                                  'tell application "System Events" to keystroke "v" using command down'])
        return code == 0

    # ---------- iOS Real Device (클립보드 복사만) ----------
    def tidevice_list_devices():
        """연결된 iOS 실기기 목록 조회"""
        code, out, err = run_cmd(["tidevice", "list"])
        if code != 0:
            return []
        devices = []
        for line in out.splitlines():
            line = line.strip()
            if line and not line.startswith("List of") and not line.startswith("Total"):
                parts = line.split(None, 2)
                if len(parts) >= 1:
                    udid = parts[0]
                    name = parts[2] if len(parts) >= 3 else udid[:8]
                    devices.append((name, udid))
        return devices

    def mac_clipboard_copy(text: str):
        """Mac 클립보드에 복사"""
        try:
            # pbcopy 사용 (macOS 기본 명령어)
            process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
            process.communicate(text.encode('utf-8'))
            return True
        except:
            return False

    # ---------- 저장 ----------
    def load_slots():
        if not os.path.exists(DATA_FILE):
            return [""]*SLOTS
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list) and len(data) == SLOTS:
                    return data
        except:
            pass
        return [""]*SLOTS

    def save_slots(values):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(values, f, ensure_ascii=False, indent=2)

    # 세션 상태 초기화
    if 'slots_left' not in st.session_state:
        st.session_state.slots_left = load_slots()
    if 'last_device_check_left' not in st.session_state:
        st.session_state.last_device_check_left = 0

    # 3초마다 자동 기기 검색
    current_time = time.time()
    if current_time - st.session_state.last_device_check_left >= 3:
        st.session_state.android_devices_left = adb_list_devices()
        st.session_state.ios_devices_left = tidevice_list_devices()
        st.session_state.ios_sim_devices_left = sim_list_booted() if IS_MAC else []
        st.session_state.last_device_check_left = current_time
        st.rerun()

    # 연결된 기기 표시 (3초마다 자동 갱신)
    st.markdown("**연결된 기기 (3초마다 자동 갱신):**")
    
    android_devs = st.session_state.get('android_devices_left', [])
    ios_devs = st.session_state.get('ios_devices_left', [])
    ios_sim_devs = st.session_state.get('ios_sim_devices_left', [])
    
    col_info1, col_info2 = st.columns(2)
    with col_info1:
        if android_devs:
            st.success(f"🤖 Android: {len(android_devs)}대")
            for dev in android_devs:
                st.caption(f"  • {dev}")
        else:
            st.info("🤖 Android: 없음")
    
    with col_info2:
        total_ios = len(ios_devs) + len(ios_sim_devs)
        if total_ios > 0:
            st.success(f"🍎 iOS: {total_ios}대")
            for name, udid in ios_devs:
                st.caption(f"  • {name} (실기기)")
            for name, udid in ios_sim_devs:
                st.caption(f"  • {name} (시뮬레이터)")
        else:
            st.info("🍎 iOS: 없음")

    # 디바이스(비워두면 자동 탐색)
    st.markdown("**디바이스 지정 (선택사항):**")
    
    col_d1, col_d2 = st.columns([1, 2])
    with col_d1:
        st.write("Android serial:")
    with col_d2:
        android_serial_left = st.text_input("Android serial", key="serial_left", label_visibility="collapsed", placeholder="비워두면 자동")
    
    col_d3, col_d4, col_d5 = st.columns([1, 2, 1])
    with col_d3:
        st.write("iOS UDID:")
    with col_d4:
        ios_udid_left = st.text_input("iOS UDID", key="udid_left", label_visibility="collapsed", placeholder="비워두면 자동")
    with col_d5:
        auto_paste_left = st.checkbox("시뮬레이터 자동붙여넣기", key="paste_left")

    # 텍스트 미리값 (숫자 1~0 눌러 전송)
    st.markdown("**텍스트 미리값 (숫자 1~0 눌러 전송):**")
    
    for i in range(SLOTS):
        row = i // 2
        col = i % 2
        
        if col == 0:
            col_l1, col_l2, col_l3, col_r1, col_r2, col_r3 = st.columns([0.5, 4, 1, 0.5, 4, 1])
        
        lab_num = 10 if i == 9 else (i + 1)
        
        def send_text_logic(text):
            """텍스트 전송 통합 로직"""
            if not text:
                return
            
            success = False
            message = ""
            
            # 1순위: Android 시도
            if adb_send_text(android_serial_left, text):
                success = True
                message = "✅ Android에 전송 완료"
            
            # 2순위: iOS Simulator 시도
            elif IS_MAC:
                udid = ios_udid_left.strip()
                if not udid:
                    booted = sim_list_booted()
                    if booted:
                        udid = booted[0][1]
                if udid and sim_pbcopy(udid, text):
                    if auto_paste_left:
                        mac_cmd_v()
                    success = True
                    message = "✅ iOS Simulator 클립보드 복사 완료"
            
            # 3순위: iOS 실기기 - Mac 클립보드 복사
            if not success:
                ios_devs_list = tidevice_list_devices()
                if ios_devs_list:
                    if mac_clipboard_copy(text):
                        success = True
                        message = "📋 Mac 클립보드에 복사됨\n💡 iOS 기기에서 직접 붙여넣기 하세요"
            
            # 모든 방법 실패
            if not success and not android_devs and not ios_devs_list and not ios_sim_devs:
                message = "⚠️ 연결된 기기가 없습니다"
            
            if message:
                st.toast(message)
        
        if col == 0:
            with col_l1:
                st.write(f"{lab_num}")
            with col_l2:
                st.session_state.slots_left[i] = st.text_input(
                    f"slot_{i}",
                    value=st.session_state.slots_left[i],
                    key=f"slot_left_{i}",
                    label_visibility="collapsed"
                )
            with col_l3:
                if st.button("전송", key=f"send_left_{i}"):
                    text = st.session_state.slots_left[i].strip()
                    send_text_logic(text)
        else:
            with col_r1:
                st.write(f"{lab_num}")
            with col_r2:
                st.session_state.slots_left[i] = st.text_input(
                    f"slot_{i}",
                    value=st.session_state.slots_left[i],
                    key=f"slot_left_{i}",
                    label_visibility="collapsed"
                )
            with col_r3:
                if st.button("전송", key=f"send_left_{i}"):
                    text = st.session_state.slots_left[i].strip()
                    send_text_logic(text)

    # 모든 슬롯 저장
    col_save = st.columns([4, 1])
    with col_save[1]:
        if st.button("모든 슬롯 저장", key="save_left"):
            save_slots(st.session_state.slots_left)
            st.toast("✅ 슬롯 저장 완료")

    # iOS 안내
    with st.expander("💡 iOS 실기기 사용 팁"):
        st.markdown("""
        **iOS 실기기는 자동 전송이 불가능합니다** (Apple 보안 정책)
        
        **사용 방법:**
        1. 전송 버튼 클릭 → Mac 클립보드에 복사됨
        2. iOS 기기에서 직접 붙여넣기
        
        **Universal Clipboard 활용 (추천):**
        - Mac과 iPhone이 같은 Apple ID 로그인
        - Wi-Fi + Bluetooth 켜기
        - Mac에서 복사하면 iPhone에서도 자동으로 붙여넣기 가능!
        
        설정:
        - Mac: 시스템 설정 → Apple ID → iCloud → "클립보드 공유" 체크
        - iPhone: 설정 → 일반 → AirPlay 및 Handoff → "Handoff" 켜기
        """)

# ============= 오른쪽: FB Logout Cleaner =============
with right_col:
    st.header("FB Logout Cleaner (Android-only)")
    
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
        "Whale": ["com.naver.whale"]
    }

    def run2(cmd):
        try:
            completed = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, shell=False)
            return completed.returncode, completed.stdout.strip()
        except Exception as e:
            return 1, f"ERROR: {e}"

    def log_right(msg):
        if 'logs_right' not in st.session_state:
            st.session_state.logs_right = []
        st.session_state.logs_right.append(msg)

    def detect_adb_right(adb_entry):
        adb_path = adb_entry.strip()
        if adb_path and Path(adb_path).exists():
            return adb_path
        return which("adb") or ""

    def list_devices_right(adb_path):
        if not adb_path or not Path(adb_path).exists():
            log_right("[ADB] adb 경로가 유효하지 않습니다.")
            return []
        code, out = run2([adb_path, "devices"])
        if code != 0:
            log_right("[ADB] adb devices 실패:\n" + out)
            return []
        devices = []
        added = 0
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
                added += 1
            else:
                log_right(f"[ADB] 연결 상태 점검 필요: {line}")
        if added == 0:
            log_right("[ADB] 연결된 기기가 없습니다. USB 디버깅 허용 팝업을 확인하세요.")
        return devices

    def get_user_ids_right(adb_path, serial):
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

    def package_exists_for_user_right(adb_path, serial, user_id, pkg):
        code, out = run2([adb_path, "-s", serial, "shell", "cmd", "package", "list", "packages", "--user", user_id, pkg])
        if code == 0 and ("package:" + pkg) in out:
            return True
        code2, out2 = run2([adb_path, "-s", serial, "shell", "pm", "list", "packages", "--user", user_id, pkg])
        return code2 == 0 and ("package:" + pkg) in out2

    def clear_pkg_for_user_right(adb_path, serial, user_id, pkg):
        code, out = run2([adb_path, "-s", serial, "shell", "pm", "clear", "--user", user_id, pkg])
        if code == 0:
            log_right(f"  - {serial} (user {user_id}): pm clear {pkg} => 완료")
            return True
        code2, out2 = run2([adb_path, "-s", serial, "shell", "pm", "clear", pkg])
        if code2 == 0:
            log_right(f"  - {serial} (user {user_id}): pm clear {pkg} (fallback) => 완료")
            return True
        log_right(f"  - {serial} (user {user_id}): pm clear {pkg} => 실패 ({out or out2})")
        return False

    def list_installed_apps_right(adb_path, serial):
        code, out = run2([adb_path, "-s", serial, "shell", "pm", "list", "packages", "-3"])
        if code != 0:
            log_right(f"[ADB] 앱 목록 조회 실패: {out}")
            return []
        apps = sorted([line.replace("package:", "") for line in out.splitlines() if line.strip()])
        log_right(f"[{serial}] 사용자 설치 앱 {len(apps)}개 불러옴")
        return apps

    def get_connected_devices_right(adb_path):
        if not adb_path or not Path(adb_path).exists():
            return []
        code, out = run2([adb_path, "devices"])
        if code != 0 or not out:
            return []
        devs = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devs.append(parts[0])
        return devs

    def get_foreground_package_right(adb_path, serial):
        code, out = run2([adb_path, "-s", serial, "shell", "dumpsys", "window", "windows"])
        if code == 0 and out:
            patterns = [
                r"mCurrentFocus=Window\{[^\}]*\s+([A-Za-z0-9._]+)/",
                r"mFocusedApp=.*\s+([A-Za-z0-9._]+)/",
            ]
            for pat in patterns:
                m = re.search(pat, out)
                if m:
                    pkg = m.group(1)
                    if not pkg.startswith("com.android.") and pkg not in ["android"]:
                        return pkg
        return ""

    def get_pkg_version_right(adb_path, serial, user_id, pkg):
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

    if 'fixed_packages_right' not in st.session_state:
        st.session_state.fixed_packages_right = []
    if 'selection_locked_right' not in st.session_state:
        st.session_state.selection_locked_right = False
    if 'logs_right' not in st.session_state:
        st.session_state.logs_right = []

    st.markdown("**ADB 설정:**")
    col_adb1, col_adb2 = st.columns([1, 4])
    with col_adb1:
        st.write("adb 경로:")
    with col_adb2:
        adb_path_right = st.text_input("adb 경로", value=which("adb") or "", key="adb_right", label_visibility="collapsed")
    
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("자동 감지", key="detect_right"):
            path = which("adb") or ""
            if path:
                st.session_state.adb_right = path
                st.info(f"감지된 adb: {path}")
            else:
                st.warning("PATH에서 adb를 찾지 못했습니다.")

    st.markdown("**연결된 Android 기기 (3초마다 자동 갱신):**")
    
    if 'devices_list_right' not in st.session_state:
        st.session_state.devices_list_right = []
    if 'last_refresh_time_right' not in st.session_state:
        st.session_state.last_refresh_time_right = 0
    
    current_time = time.time()
    if current_time - st.session_state.last_refresh_time_right >= 3:
        if adb_path_right:
            st.session_state.devices_list_right = list_devices_right(detect_adb_right(adb_path_right))
            st.session_state.last_refresh_time_right = current_time
            st.rerun()
    
    col_dev1, col_dev2 = st.columns(2)
    with col_dev1:
        if st.button("수동 새로고침", key="refresh_right"):
            st.session_state.logs_right = []
            if adb_path_right:
                st.session_state.devices_list_right = list_devices_right(detect_adb_right(adb_path_right))
                st.session_state.last_refresh_time_right = time.time()
            st.rerun()
    
    with col_dev2:
        if st.button("실행 중 앱 버전 표시", key="show_ver_right"):
            st.session_state.logs_right = []
            adb_path = detect_adb_right(adb_path_right)
            if not adb_path:
                log_right("[ADB] adb 실행 파일을 찾을 수 없습니다.")
            else:
                sel_devices = st.session_state.get('devices_selected_right', [])
                target_serials = sel_devices if sel_devices else get_connected_devices_right(adb_path)
                
                for serial in target_serials:
                    log_right(f"[{serial}] 전면 앱 감지 중...")
                    pkg = get_foreground_package_right(adb_path, serial)
                    if not pkg:
                        log_right(f"[{serial}] ❌ 전면 앱을 감지하지 못했습니다.")
                    else:
                        user_ids = get_user_ids_right(adb_path, serial)
                        uid = user_ids[0] if user_ids else "0"
                        vname, vcode = get_pkg_version_right(adb_path, serial, uid, pkg)
                        if not vname and not vcode:
                            log_right(f"[{serial}] 📱 현재 앱: {pkg}")
                        else:
                            info = []
                            if vname: info.append(f"v{vname}")
                            if vcode: info.append(f"빌드:{vcode}")
                            log_right(f"[{serial}] 📱 현재 앱: {pkg} ({', '.join(info)})")

    if st.session_state.devices_list_right:
        st.session_state.devices_selected_right = st.multiselect(
            "기기 선택:",
            st.session_state.devices_list_right,
            key="devices_multi_right",
            label_visibility="collapsed"
        )
    else:
        st.info("ADB 경로를 설정하면 자동으로 기기를 검색합니다.")

    st.markdown("**설치된 앱 목록 (최초 1회 선택 후 고정):**")
    
    col_app1, col_app2, col_app3 = st.columns(3)
    with col_app1:
        if st.button("앱 목록 불러오기", key="load_apps_right"):
            if st.session_state.selection_locked_right:
                st.info("앱 선택은 이미 고정되었습니다.")
            else:
                adb_path = detect_adb_right(adb_path_right)
                if not adb_path:
                    st.error("adb 실행 파일을 찾을 수 없습니다.")
                elif not st.session_state.get('devices_selected_right'):
                    st.warning("기기를 1대 선택하세요.")
                else:
                    serial = st.session_state.devices_selected_right[0]
                    apps = list_installed_apps_right(adb_path, serial)
                    st.session_state.available_apps_right = apps
    
    with col_app2:
        if st.button("선택 고정", key="fix_selection_right"):
            if st.session_state.selection_locked_right:
                st.info("이미 고정되었습니다.")
            elif 'apps_selected_right' not in st.session_state or not st.session_state.apps_selected_right:
                st.warning("앱을 한 개 이상 선택하세요.")
            else:
                st.session_state.fixed_packages_right = st.session_state.apps_selected_right[:]
                st.session_state.selection_locked_right = True
                log_right("고정된 패키지: " + ", ".join(st.session_state.fixed_packages_right))
                st.success("앱 선택 고정됨!")
    
    with col_app3:
        if st.button("선택 초기화", key="reset_selection_right"):
            st.session_state.fixed_packages_right = []
            st.session_state.selection_locked_right = False
            st.session_state.available_apps_right = []
            log_right("앱 선택 해제")
            st.rerun()

    if st.session_state.selection_locked_right:
        st.info(f"고정: {', '.join(st.session_state.fixed_packages_right)}")
    elif 'available_apps_right' in st.session_state and st.session_state.available_apps_right:
        st.session_state.apps_selected_right = st.multiselect(
            "앱 선택:",
            st.session_state.available_apps_right,
            key="apps_multi_right",
            label_visibility="collapsed"
        )

    st.markdown("**초기화 옵션:**")
    var_fb_right = st.checkbox("Facebook 앱 데이터 삭제", value=True, key="fb_check_right")
    var_ms_right = st.checkbox("Messenger 데이터 삭제", value=True, key="ms_check_right")
    var_browsers_right = st.checkbox("브라우저 데이터 삭제", value=True, key="br_check_right")

    if st.button("완전 초기화 실행", key="exec_cleanup_right"):
        adb_path = detect_adb_right(adb_path_right)
        if not adb_path:
            st.error("adb 실행 파일을 찾을 수 없습니다.")
        elif not st.session_state.get('devices_selected_right'):
            st.warning("하나 이상의 기기를 선택하세요.")
        else:
            st.session_state.logs_right = []
            for serial in st.session_state.devices_selected_right:
                log_right(f">>> [{serial}] 초기화 시작")
                user_ids = get_user_ids_right(adb_path, serial)
                log_right(f"  - 사용자 IDs: {', '.join(user_ids)}")

                for pkg in st.session_state.fixed_packages_right:
                    for uid in user_ids:
                        if package_exists_for_user_right(adb_path, serial, uid, pkg):
                            clear_pkg_for_user_right(adb_path, serial, uid, pkg)

                if var_fb_right:
                    for uid in user_ids:
                        for pkg in FACEBOOK_PKGS:
                            if package_exists_for_user_right(adb_path, serial, uid, pkg):
                                clear_pkg_for_user_right(adb_path, serial, uid, pkg)
                if var_ms_right:
                    for uid in user_ids:
                        for pkg in MESSENGER_PKGS:
                            if package_exists_for_user_right(adb_path, serial, uid, pkg):
                                clear_pkg_for_user_right(adb_path, serial, uid, pkg)

                if var_browsers_right:
                    for fam, pkgs in BROWSER_FAMILIES.items():
                        for pkg in pkgs:
                            for uid in user_ids:
                                if package_exists_for_user_right(adb_path, serial, uid, pkg):
                                    clear_pkg_for_user_right(adb_path, serial, uid, pkg)

                log_right(f">>> [{serial}] 완료 ✅")

    st.caption("⚠️ pm clear는 앱의 모든 로컬 데이터를 삭제합니다.")

    if st.session_state.logs_right:
        st.text_area("로그:", "\n".join(st.session_state.logs_right), height=250, key="logs_area_right")