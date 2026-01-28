# app_sp_links_full_max.py
import streamlit as st
import os, re,  time, tempfile, urllib.parse, subprocess, requests
from typing import List, Tuple, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# -------------------------
# 기본 유틸
# -------------------------
def norm_links(raw: str) -> List[str]:
    """붙여넣은 여러 링크를 정규화(개행/공백/쉼표/세미콜론 구분)"""
    if not raw:
        return []
    tokens = re.split(r"[\s,;]+", raw.strip())
    return [t for t in tokens if t.startswith("http")]

def content_filename_from_headers(headers: Dict[str, str], fallback: str) -> str:
    """Content-Disposition에서 filename 추출 (없으면 fallback)"""
    cd = headers.get("content-disposition", "") or headers.get("Content-Disposition", "")
    m = re.findall(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', cd)
    if m:
        return os.path.basename(m[0])
    return fallback

# -------------------------
# SharePoint 강제 다운로드 URL 변환
# -------------------------
def to_sp_direct_download_url(shared_url: str) -> str:
    """
    /:u:/s/<site>/... ?e=... 형식의 공유 링크를
    /sites/<site>/_layouts/15/download.aspx?SourceUrl=<원본링크> 로 변환
    (site 세그먼트 판별 실패 시 테넌트 루트 경로 사용)
    """
    parsed = urllib.parse.urlparse(shared_url)
    tenant_host = parsed.netloc
    encoded_source = urllib.parse.quote(shared_url, safe=":/%?&=!-_.~*'()")
    m = re.search(r"/:.\:/s/([^/]+)/", parsed.path)  # /:u:/s/<site>/
    if m:
        site_segment = m.group(1)
        return f"https://{tenant_host}/sites/{site_segment}/_layouts/15/download.aspx?SourceUrl={encoded_source}"
    return f"https://{tenant_host}/_layouts/15/download.aspx?SourceUrl={encoded_source}"

def fallback_append_download_param(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}download=1"

# -------------------------
# 다운로드(스트리밍)
# -------------------------
def download_stream(direct_url: str, dst_path: str, timeout_s: int = 7200, chunk_mb: int = 8) -> Tuple[int, Dict[str, str]]:
    with requests.get(direct_url, stream=True, timeout=timeout_s) as r:
        r.raise_for_status()
        headers = r.headers
        chunk = 1024 * 1024 * chunk_mb
        total = 0
        with open(dst_path, "wb") as f:
            for part in r.iter_content(chunk_size=chunk):
                if part:
                    f.write(part)
                    total += len(part)
        return total, headers

def robust_download(shared_url: str, save_dir: str, idx: int) -> Tuple[str, bool, str, int]:
    """download.aspx 우선 → 실패 시 ?download=1 재시도"""
    try:
        direct = to_sp_direct_download_url(shared_url)
        tmp = os.path.join(save_dir, f"pkg_{idx:02d}.bin")
        size, headers = download_stream(direct, tmp)
        name = content_filename_from_headers(headers, os.path.basename(tmp))
        final_path = os.path.join(save_dir, name)
        os.replace(tmp, final_path)
        return final_path, True, "download.aspx", size
    except Exception as e1:
        try:
            direct2 = fallback_append_download_param(shared_url)
            tmp = os.path.join(save_dir, f"pkg_{idx:02d}.bin")
            size, headers = download_stream(direct2, tmp)
            name = content_filename_from_headers(headers, os.path.basename(tmp))
            final_path = os.path.join(save_dir, name)
            os.replace(tmp, final_path)
            return final_path, True, "download=1", size
        except Exception as e2:
            return "", False, f"{e1} / fallback {e2}", 0

# -------------------------
# ADB 유틸 + 기존설치 제거
# -------------------------
def run_adb(cmd: List[str], timeout: int = 1800):
    p = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr

def get_connected_devices() -> List[str]:
    rc, out, err = run_adb(["adb", "devices"])
    if rc != 0:
        raise RuntimeError(f"adb devices 실패: {err}")
    devs = []
    for line in out.strip().splitlines()[1:]:
        if "\tdevice" in line:
            devs.append(line.split("\t")[0])
    return devs

def get_package_name(apk_path: str) -> Optional[str]:
    """APK 패키지명 추출(aapt 필요). 실패 시 None"""
    try:
        out = subprocess.check_output(["aapt", "dump", "badging", apk_path], text=True)
        m = re.search(r"package: name='([^']+)'", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

def is_installed(package: str, device: str) -> bool:
    rc, out, _ = run_adb(["adb", "-s", device, "shell", "pm", "list", "packages", package])
    return (rc == 0) and (package in out)

def uninstall(package: str, device: str):
    run_adb(["adb", "-s", device, "uninstall", package])

def install_apk_clean(apk_path: str, device: str, grant_permissions: bool = True) -> Dict[str, str]:
    """설치 전 기존 설치 감지→uninstall 후 재설치"""
    pkg = get_package_name(apk_path)
    if pkg and is_installed(pkg, device):
        uninstall(pkg, device)
    args = ["adb", "-s", device, "install", "-r"]
    if grant_permissions:
        args.append("-g")
    args.append(apk_path)
    start = time.time()
    p = subprocess.run(args, text=True, capture_output=True, timeout=3600)
    ok = (p.returncode == 0) and ("Success" in p.stdout)
    return {
        "device": device,
        "file": os.path.basename(apk_path),
        "ok": "OK" if ok else "NG",
        "secs": f"{(time.time()-start):.1f}",
        "stdout": p.stdout.strip(),
        "stderr": p.stderr.strip(),
    }

# -------------------------
# Streamlit UI
# -------------------------
st.title("SharePoint 링크 다중 → 강제 다운로드 → 모든 ADB 최대 병렬 설치")

raw_links = st.text_area("SharePoint 공유 링크(여러 개 가능)", height=160, placeholder="한 줄에 하나씩 붙여넣으세요")
grant_permissions = st.checkbox("설치 시 권한 자동 부여(-g)", value=True)

if st.button("디바이스 검색"):
    try:
        st.session_state["devices"] = get_connected_devices()
        if not st.session_state["devices"]:
            st.error("ADB 디바이스가 없습니다.")
        else:
            st.success(", ".join(st.session_state["devices"]))
    except Exception as e:
        st.exception(e)

devices = st.session_state.get("devices", [])
links = norm_links(raw_links)

start_btn = st.button("다운로드 → 설치 시작", disabled=not (links and devices))
if start_btn:
    tmpdir = tempfile.mkdtemp(prefix="sp_max_")
    st.info(f"임시 경로: {tmpdir}")

    # 1) 최대 병렬 다운로드 (workers = 링크 수)
    dl_results = []
    total_dl = len(links)
    prog_dl = st.progress(0.0)

    try:
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, total_dl)) as ex:
            futs = {ex.submit(robust_download, url, tmpdir, i): (i, url)
                    for i, url in enumerate(links, start=1)}
            for f in as_completed(futs):
                i, url = futs[f]
                path, ok, msg, size = f.result()
                dl_results.append({"idx": i, "url": url, "ok": ok, "msg": msg, "path": path, "mb": f"{size/1024/1024:.2f}"})
                done += 1
                prog_dl.progress(done / total_dl)

        st.subheader("다운로드 결과")
        for r in sorted(dl_results, key=lambda x: x["idx"]):
            if r["ok"]:
                st.write(f"✅ [{r['idx']}] {os.path.basename(r['path'])} ({r['mb']} MB)")
            else:
                st.write(f"❌ [{r['idx']}] {r['url']} — {r['msg']}")

        files_ok = [r["path"] for r in dl_results if r["ok"] and os.path.exists(r["path"])]
        if not files_ok:
            st.error("다운로드 성공 파일이 없습니다. 링크/권한을 확인하세요.")
        else:
            # 2) 모든 ADB × 모든 파일 최대 병렬 설치 (workers = 작업 개수)
            jobs = [(d, p) for d in devices for p in files_ok]
            total_inst = len(jobs)
            prog_inst = st.progress(0.0)

            results = []
            done2 = 0
            with ThreadPoolExecutor(max_workers=max(1, total_inst)) as ex:
                futs = {ex.submit(install_apk_clean, p, d, grant_permissions): (d, p) for (d, p) in jobs}
                for f in as_completed(futs):
                    res = f.result()
                    results.append(res)
                    done2 += 1
                    prog_inst.progress(done2 / total_inst)

            st.subheader("설치 결과")
            for r in results:
                mark = "✅" if r["ok"] == "OK" else "❌"
                st.write(f"{mark} {r['device']} ← {r['file']}  ({r['secs']}s)")
                st.caption(f"stdout: {r['stdout']}")
                if r["stderr"]:
                    st.caption(f"stderr: {r['stderr']}")
    except Exception as e:
        st.error(str(e))
