# app_links_to_adb_max.py
import streamlit as st
import os, re, time, tempfile, urllib.parse, subprocess, requests, zipfile
from typing import List, Tuple, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# =========================
# 링크 정규화
# =========================
def norm_links(raw: str) -> List[str]:
    """붙여넣은 여러 링크를 정규화(개행/공백/쉼표/세미콜론 구분)"""
    if not raw:
        return []
    tokens = re.split(r"[\s,;]+", raw.strip())
    return [t for t in tokens if t.startswith("http")]

# =========================
# OneDrive 링크 변환
# =========================
def resolve_1drv_to_live(url: str) -> str:
    """1drv.ms 단축링크를 onedrive.live.com으로 확장"""
    with requests.Session() as s:
        r = s.head(url, allow_redirects=True, timeout=30)
        return r.url

def to_onedrive_direct_download(shared_url: str) -> str:
    """
    OneDrive 공유 URL → 직접 다운로드 URL
    - 1drv.ms → onedrive.live.com 으로 확장 후 cid/resid/authkey 추출
    - embed → download 치환
    - 마지막 수단: ?download=1
    """
    if "1drv.ms" in shared_url:
        live_url = resolve_1drv_to_live(shared_url)
    else:
        live_url = shared_url

    parsed = urllib.parse.urlparse(live_url)
    q = urllib.parse.parse_qs(parsed.query)
    cid = q.get("cid", [None])[0]
    resid = q.get("resid", [None])[0]
    authkey = q.get("authkey", [None])[0]
    if cid and resid and authkey:
        return f"https://onedrive.live.com/download?cid={cid}&resid={resid}&authkey={authkey}"

    if "/embed?" in live_url:
        return live_url.replace("/embed?", "/download?")

    sep = "&" if "?" in live_url else "?"
    return f"{live_url}{sep}download=1"

# =========================
# SharePoint 링크 변환
# =========================
def to_sp_direct_download_url(shared_url: str) -> str:
    """
    SharePoint 공유 링크( /:u:/s/<site>/... ?e=... ) → 서버 직접 다운로드 URL
    /sites/<site>/_layouts/15/download.aspx?SourceUrl=<원본링크(인코딩)>
    """
    parsed = urllib.parse.urlparse(shared_url)
    tenant_host = parsed.netloc
    encoded_source = urllib.parse.quote(shared_url, safe=":/%?&=!-_.~*'()")
    m = re.search(r"/:.\:/s/([^/]+)/", parsed.path)  # /:u:/s/<site>/
    if m:
        site_segment = m.group(1)
        return f"https://{tenant_host}/sites/{site_segment}/_layouts/15/download.aspx?SourceUrl={encoded_source}"
    # site 세그먼트를 못 찾으면 루트에 시도
    return f"https://{tenant_host}/_layouts/15/download.aspx?SourceUrl={encoded_source}"

def fallback_append_download_param(url: str) -> str:
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}download=1"

def make_direct_url(shared_url: str) -> str:
    """
    입력된 링크가 어느 계열(OneDrive/SharePoint)인지에 따라 직접 다운로드 URL 생성
    """
    lower = shared_url.lower()
    host = urllib.parse.urlparse(shared_url).netloc.lower()
    if "sharepoint.com" in host:
        return to_sp_direct_download_url(shared_url)
    if "1drv.ms" in lower or "onedrive.live.com" in host:
        return to_onedrive_direct_download(shared_url)
    # 기타 링크는 우선 그대로
    return shared_url

# =========================
# Content-Disposition 파일명 추출(+RFC5987 디코딩)
# =========================
def content_filename_from_headers(headers: Dict[str, str], fallback: str) -> str:
    """
    Content-Disposition 헤더에서 파일명 안전 추출
    - RFC 5987 filename*="utf-8''AOS64%2eapk" 지원
    - 퍼센트 디코딩 및 위험문자 제거
    """
    cd = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
    cd = cd.strip()

    # filename*=
    m = re.search(r"""filename\*\s*=\s*("?)([^";]+)\1""", cd, flags=re.IGNORECASE)
    if m:
        v = m.group(2)  # 예: utf-8''AOS64%5f...%2eapk
        if "''" in v:
            _, raw = v.split("''", 1)
        else:
            raw = v
        decoded = urllib.parse.unquote(raw)
        name = os.path.basename(decoded)
        name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip("'\"")
        return name

    # filename=
    m2 = re.search(r"""filename\s*=\s*("?)([^";]+)\1""", cd, flags=re.IGNORECASE)
    if m2:
        name = os.path.basename(m2.group(2))
        name = urllib.parse.unquote(name)
        name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip("'\"")
        return name

    return fallback

# =========================
# HTML 응답 판별(로그인/뷰어/에러)
# =========================
def is_probably_html(headers: dict, first_bytes: bytes) -> bool:
    ctype = (headers.get("content-type") or headers.get("Content-Type") or "").lower()
    if "text/html" in ctype:
        return True
    head = first_bytes.strip().lower() if first_bytes else b""
    if head.startswith(b"<!doctype html") or head.startswith(b"<html") or b"<head" in head[:1024]:
        return True
    if b"sharepoint" in head[:4096] or b"sign in" in head[:4096] or b"microsoft" in head[:4096]:
        return True
    return False

# =========================
# 다운로드(스트리밍) + HTML 차단
# =========================
def download_stream(direct_url: str, dst_path: str, timeout_s: int = 7200, chunk_mb: int = 8) -> Tuple[int, Dict[str, str], bytes]:
    """
    첫 청크로 HTML 응답 여부를 판별해 HTML이면 즉시 예외 발생
    return: (bytes_written, headers, first_chunk)
    """
    with requests.get(direct_url, stream=True, timeout=timeout_s, allow_redirects=True) as r:
        r.raise_for_status()
        headers = r.headers
        chunk = 1024 * 1024 * chunk_mb

        it = r.iter_content(chunk_size=chunk)
        try:
            first = next(it)
        except StopIteration:
            first = b""

        if is_probably_html(headers, first):
            preview = first[:512]
            raise RuntimeError(f"HTML/권한 이슈 추정: ctype={headers.get('content-type')} preview={preview!r}")

        total = 0
        with open(dst_path, "wb") as f:
            if first:
                f.write(first)
                total += len(first)
            for part in it:
                if part:
                    f.write(part)
                    total += len(part)
        return total, headers, first

# =========================
# 확장자 추정 및 최종 이름 확정
# =========================
def guess_ext_from_zip(path: str) -> Optional[str]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if "AndroidManifest.xml" in names:
                return ".apk"
            if "apex_manifest.json" in names:
                return ".apex"
            if "Info.plist" in names:
                return ".ipa"
    except Exception:
        pass
    return None

def ensure_installable_android_path(tmp_downloaded_path: str,
                                    url: str,
                                    headers: dict,
                                    idx: int,
                                    save_dir: str) -> Tuple[str, bool, str]:
    """
    - 헤더/URL에서 파일명 추출(RFC5987 + 퍼센트 디코딩)
    - .apk/.apex 아니면 ZIP 스니핑으로 확장자 결정
    - 최종적으로 .apk 또는 .apex 로 끝나도록 rename
    """
    # 1) 헤더 우선
    name = content_filename_from_headers(headers, "")
    # 2) URL 후보
    if not name:
        base = os.path.basename(urllib.parse.urlparse(url).path)
        name = urllib.parse.unquote(base) if base else ""
    # 3) 기본값
    if not name:
        name = f"pkg_{idx:02d}.bin"

    name = name.strip().strip("'\"")
    root, ext = os.path.splitext(name)

    # 이미 맞는 확장자
    if ext.lower() in [".apk", ".apex"]:
        final = os.path.join(save_dir, root + ext.lower())
        os.replace(tmp_downloaded_path, final)
        return final, True, "name says installable"

    # ZIP 내부로 추정
    guessed = guess_ext_from_zip(tmp_downloaded_path)
    if guessed in [".apk", ".apex"]:
        final = os.path.join(save_dir, root + guessed)
        os.replace(tmp_downloaded_path, final)
        return final, True, f"sniffed {guessed}"

    if guessed == ".ipa":
        final = os.path.join(save_dir, root + ".ipa")
        os.replace(tmp_downloaded_path, final)
        return final, False, "detected .ipa (skip for ADB)"

    final = os.path.join(save_dir, root + (ext if ext else ".bin"))
    os.replace(tmp_downloaded_path, final)
    return final, False, "unknown type (not .apk/.apex)"

# =========================
# 견고한 다운로드(변환 → 시도 → 폴백)
# =========================
def robust_download(shared_url: str, save_dir: str, idx: int) -> Tuple[str, bool, str, int]:
    """
    1) 링크 유형에 맞는 직접 다운로드 URL 시도
    2) 실패 시 원본에 ?download=1 폴백
    3) HTML 응답이면 예외로 실패 처리(미리보기 포함)
    4) 저장 직후 .apk/.apex 로 자동 rename
    return: (final_path, installable, msg, size_bytes)
    """
    def _do(direct_url: str, tag: str):
        tmp = os.path.join(save_dir, f"pkg_{idx:02d}.bin")
        size, headers, _ = download_stream(direct_url, tmp)
        final, installable, note = ensure_installable_android_path(tmp, shared_url, headers, idx, save_dir)
        return final, installable, f"{tag} | {note}", size

    try:
        direct = make_direct_url(shared_url)
        return _do(direct, "direct")
    except Exception as e1:
        try:
            direct2 = fallback_append_download_param(shared_url)
            return _do(direct2, "fallback=download=1")
        except Exception as e2:
            return "", False, f"HTML/권한 또는 네트워크 이슈 | {e1} | {e2}", 0

# =========================
# ADB 유틸 + 기존설치 제거
# =========================
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

# =========================
# Streamlit UI
# =========================
st.title("링크(SharePoint/OneDrive) → 강제 다운로드 → 모든 ADB에 최대 병렬 설치")

raw_links = st.text_area("공유 링크(여러 개 가능)", height=160, placeholder="한 줄에 하나씩 붙여넣으세요 (파일 링크 권장)")
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
    tmpdir = tempfile.mkdtemp(prefix="dl_adb_")
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
                final_path, installable, msg, size = f.result()
                dl_results.append({
                    "idx": i,
                    "url": url,
                    "ok": installable,            # 설치 가능(True)만 OK
                    "msg": msg,
                    "path": final_path,
                    "mb": f"{size/1024/1024:.2f}",
                })
                done += 1
                prog_dl.progress(done / total_dl)

        st.subheader("다운로드 결과")
        for r in sorted(dl_results, key=lambda x: x["idx"]):
            mark = "✅" if r["ok"] else "❌"
            base = os.path.basename(r["path"]) if r["path"] else "(no file)"
            st.write(f"{mark} [{r['idx']}] {base} ({r['mb']} MB) — {r['msg']}")

        files_ok = [r["path"] for r in dl_results if r["ok"] and os.path.exists(r["path"])]
        if not files_ok:
            st.error("설치 가능한 파일(.apk/.apex)이 없습니다. 링크/권한을 확인하세요.")
        else:
            # 2) 모든 ADB × 모든 파일 최대 병렬 설치
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
