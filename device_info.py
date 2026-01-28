import subprocess
from typing import Optional, Dict


def run_adb_cmd(args: list[str]) -> subprocess.CompletedProcess:
    """
    ADB 명령을 실행하는 헬퍼.
    에러 발생 시 CompletedProcess.returncode 로 확인.
    """
    return subprocess.run(
        ["adb", *args],
        capture_output=True,
        text=True,
    )


def check_device_connected() -> bool:
    """
    연결된 기기가 있는지 확인.
    """
    proc = run_adb_cmd(["get-state"])
    if proc.returncode != 0:
        return False
    state = proc.stdout.strip()
    return state in {"device", "bootloader", "recovery"}


def get_prop(key: str) -> str:
    """
    adb shell getprop KEY 값을 읽어 반환.
    실패 시 빈 문자열 반환.
    """
    proc = run_adb_cmd(["shell", "getprop", key])
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def get_wm_size() -> Optional[str]:
    """
    adb shell wm size 명령으로 해상도(physical size)를 읽어옴.
    예: Physical size: 2400x1080
    """
    proc = run_adb_cmd(["shell", "wm", "size"])
    if proc.returncode != 0:
        return None

    out = proc.stdout.strip()
    # 예시:
    # Physical size: 2400x1080
    # Override size: 2400x1080
    for line in out.splitlines():
        line = line.strip()
        if line.lower().startswith("physical size:"):
            return line.split(":", 1)[1].strip()

    return None


def get_ram_info(props: Dict[str, str]) -> str:
    """
    RAM 정보를 가능한 한 추론:
    1) 벤더 prop 우선 (예: ro.vendor.hw.ram)
    2) vivo 전용 프로젝트 RAM 크기
    3) /proc/meminfo 의 MemTotal 기반 대략 GB 환산
    """
    ram_prop = props.get("ro.vendor.hw.ram", "")
    project_ram = props.get("sys.vivo.project.ramsize", "")

    if ram_prop:
        return ram_prop
    if project_ram:
        return f"{project_ram}GB (sys.vivo.project.ramsize 기준)"

    # 범용 Fallback: /proc/meminfo 파싱
    proc = run_adb_cmd(["shell", "cat", "/proc/meminfo"])
    if proc.returncode != 0:
        return ""

    total_kb: Optional[int] = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("MemTotal:"):
            parts = line.split()
            # 예: ["MemTotal:", "6050420", "kB"]
            if len(parts) >= 2:
                try:
                    total_kb = int(parts[1])
                except ValueError:
                    total_kb = None
            break

    if total_kb is None:
        return ""

    gb = total_kb / (1024 * 1024)  # kB → GB (GiB 기준)
    return f"{gb:.1f}GB (MemTotal 기준)"


def guess_target_country(props: Dict[str, str]) -> str:
    """
    타겟 국가를 5가지로 분류:
    - 중국, 인도, 한국, 일본, 글로벌
    디바이스별 리전/CSC/커스텀 코드들을 기반으로 간단히 매핑한다.
    """
    # 국가/리전 관련 가능성이 높은 프로퍼티들
    candidate_keys = [
        "ro.product.country.region",
        "ro.product.customize.bbk",
        "persist.vivo.product.cust",
        "persist.sys.vivo.product.cust",
        "ro.csc.countryiso_code",
        "ro.csc.country_code",
        "ro.csc.sales_code",
        "ril.region_props",
        "ro.miui.region",
        "ro.miui.build.region",
        "ro.oplus.pipeline.region",
        "ro.oem.key1",
        "ro.product.brand",
        "ro.product.name",
    ]

    values: list[str] = []
    for k in candidate_keys:
        v = props.get(k)
        if v:
            values.append(v)

    joined = " ".join(values).upper()

    # 중국 계열 판단
    if any(tok in joined for tok in [" CN", "CHINA", "CN_CHINATELECOM", "CHN"]):
        return "중국"

    # 인도
    if any(tok in joined for tok in [" IN", "INDIA", "INS", "IN_HQ", "RETIN"]):
        return "인도"

    # 한국
    if any(tok in joined for tok in [" KR", "KOREA", "KOR"]):
        return "한국"

    # 일본 (일반적인 코드들 기준)
    if any(tok in joined for tok in [" JP", "JPN", "JAPAN", "KDDI", "DOCOMO", "SOFTBANK", "SBM"]):
        return "일본"

    # 나머지는 모두 글로벌 취급 (유럽 EEA/EUEX, MY, KH 등)
    return "글로벌"


def main() -> None:
    print("=== ADB 기기 정보 조회 ===")

    if not check_device_connected():
        print("[-] ADB 기기가 연결되어 있지 않거나 인식되지 않았습니다.")
        print("    - USB 케이블 / 드라이버 / 개발자 옵션(USB 디버깅) 상태를 확인하세요.")
        return

    # 필요한 프로퍼티 일괄 조회
    keys = [
        "ro.product.model",
        "ro.product.manufacturer",
        "ro.product.device",
        "ro.product.name",
        "ro.boot.hardware.sku",
        "ro.vendor.hw.ram",
        "ro.vendor.hw.radio",
        "ro.soc.model",
        "ro.soc.manufacturer",
        "ro.hardware",
        "ro.hardware.egl",
        "ro.hardware.vulkan",
        "ro.product.cpu.abi",
        "ro.product.cpu.abilist",
        "ro.product.cpu.abilist32",
        "ro.product.cpu.abilist64",
        "ro.build.version.release",
        "ro.build.version.sdk",
        # 타겟 국가/커스텀 관련 (vivo 등)
        "ro.product.country.region",
        "ro.vivo.product.cust",
        "persist.vivo.product.cust",
        "persist.sys.vivo.product.cust",
        # 삼성 CSC / 지역 관련
        "ro.csc.country_code",
        "ro.csc.countryiso_code",
        "ro.csc.sales_code",
        "ril.region_props",
        "gsm.operator.iso-country",
        "gsm.operator.numeric",
        # OnePlus / Oplus 계열 리전
        "ro.oem.key1",
        "ro.oplus.pipeline.region",
        # vivo 마케팅 모델명 / 제품 코드
        "ro.vivo.product.release.name",
        "ro.vivo.product.model",
        "ro.vivo.product.release.model",
        # vivo RAM 프로젝트 사이즈
        "sys.vivo.project.ramsize",
        # 삼성 / 기타 제품 코드
        "ril.product_code",
        "vendor.ril.product_code",
    ]

    props: Dict[str, str] = {k: get_prop(k) for k in keys}

    model = props.get("ro.product.model", "")
    manufacturer = props.get("ro.product.manufacturer", "")
    device_code = props.get("ro.product.device", "")
    product_name = props.get("ro.product.name", "")
    sku = props.get("ro.boot.hardware.sku", "")
    # RAM 정보는 헬퍼 함수에서 일괄 처리
    ram = get_ram_info(props)
    radio_region = props.get("ro.vendor.hw.radio", "")
    android_ver = props.get("ro.build.version.release", "")
    android_sdk = props.get("ro.build.version.sdk", "")
    cpu_abi = props.get("ro.product.cpu.abi", "")

    # 마케팅 모델명 (주로 vivo 전용)
    marketing_name = props.get("ro.vivo.product.release.name", "")

    # 아키텍처(32/64비트) 추정
    abilist = props.get("ro.product.cpu.abilist", "")
    abilist64 = props.get("ro.product.cpu.abilist64", "")
    abilist32 = props.get("ro.product.cpu.abilist32", "")

    if abilist64:
        arch_str = "64비트 (64/32비트 앱 동시 지원)"
    elif abilist and "64" in abilist:
        arch_str = "64비트로 추정 (abilist 기반)"
    elif abilist32 or abilist:
        arch_str = "32비트"
    else:
        arch_str = "알 수 없음"

    # 해상도
    resolution = get_wm_size()

    # 제품번호(상품 코드) 추정
    # 벤더별 대표 "제품 코드" 후보들을 그대로 사용 (추정/매핑 X)
    product_code = (
        props.get("ril.product_code", "")
        or props.get("vendor.ril.product_code", "")
        or props.get("ro.vivo.product.model", "")
        or sku
    )

    print()
    print("■ 기본 정보")
    print(f"  - 제조사: {manufacturer or '알 수 없음'}")
    print(f"  - 모델명(ro.product.model): {model or '알 수 없음'}")
    if marketing_name:
        print(f"  - 출시명/마케팅 모델명: {marketing_name}")
    print(f"  - 디바이스 코드명: {device_code or '알 수 없음'}")
    print(f"  - 제품 이름(ro.product.name): {product_name or '알 수 없음'}")
    print(f"  - SKU(하드웨어 모델 코드): {sku or '알 수 없음'}")

    print()
    print("■ 제품번호 / 모델 코드")
    print(f"  - 제품번호/코드 후보: {product_code or '알 수 없음'}")

    print()
    print("■ OS / CPU / GPU / 메모리")
    print(f"  - Android 버전: {android_ver or '알 수 없음'} (SDK {android_sdk or '알 수 없음'})")
    print(f"  - SoC 모델(ro.soc.model): {props.get('ro.soc.model', '') or '알 수 없음'}")
    print(f"  - SoC 제조사(ro.soc.manufacturer): {props.get('ro.soc.manufacturer', '') or '알 수 없음'}")
    # GPU 관련 정보는 시스템 프로퍼티 그대로 출력
    print(f"  - GPU(egl)(ro.hardware.egl): {props.get('ro.hardware.egl', '') or '알 수 없음'}")
    print(f"  - GPU(vulkan)(ro.hardware.vulkan): {props.get('ro.hardware.vulkan', '') or '알 수 없음'}")
    print(f"  - CPU ABI: {cpu_abi or '알 수 없음'}")
    print(f"  - 아키텍처(32/64비트): {arch_str}")
    print(f"  - RAM: {ram or '알 수 없음'}")

    print()
    print("■ 디스플레이")
    if resolution:
        print(f"  - 해상도(Physical size): {resolution}")
    else:
        print("  - 해상도: wm size 정보를 가져오지 못했습니다.")

    print()
    print("■ 지역 / 라디오 관련")
    print(f"  - 라디오 하드웨어 지역 코드(ro.vendor.hw.radio): {radio_region or '알 수 없음'}")

    # 공통 네트워크 기반 지역 정보
    net_country_iso = props.get("gsm.operator.iso-country", "")
    net_operator_num = props.get("gsm.operator.numeric", "")
    print(f"  - 네트워크 국가(gsm.operator.iso-country): {net_country_iso or '알 수 없음'}")
    print(f"  - 네트워크 코드(gsm.operator.numeric): {net_operator_num or '알 수 없음'}")

    # 타겟 국가/지역 관련(vivo / 삼성 등)
    fw_region = props.get("ro.product.country.region", "")
    vivo_region = props.get("ro.vivo.product.cust", "")
    vivo_region_persist = props.get("persist.vivo.product.cust", "")
    vivo_region_sys = props.get("persist.sys.vivo.product.cust", "")
    csc_country = props.get("ro.csc.country_code", "")
    csc_iso = props.get("ro.csc.countryiso_code", "")
    csc_sales = props.get("ro.csc.sales_code", "")
    ril_region = props.get("ril.region_props", "")

    if any([fw_region, vivo_region, vivo_region_persist, vivo_region_sys,
            csc_country, csc_iso, csc_sales, ril_region]):
        print("  - CSC/펌웨어 지역 정보:")
        if fw_region:
            print(f"    · ro.product.country.region: {fw_region}")
        if vivo_region:
            print(f"    · ro.vivo.product.cust: {vivo_region}")
        if vivo_region_persist:
            print(f"    · persist.vivo.product.cust: {vivo_region_persist}")
        if vivo_region_sys:
            print(f"    · persist.sys.vivo.product.cust: {vivo_region_sys}")
        if csc_country:
            print(f"    · ro.csc.country_code: {csc_country}")
        if csc_iso:
            print(f"    · ro.csc.countryiso_code: {csc_iso}")
        if csc_sales:
            print(f"    · ro.csc.sales_code: {csc_sales}")
        if ril_region:
            print(f"    · ril.region_props: {ril_region}")

    # 간단 타겟 국가 분류 (5가지 레벨)
    print()
    print("■ 타겟 국가(단순 분류)")
    target_country = guess_target_country(props)
    print(f"  - 분류된 타겟 국가: {target_country}")

    print()
    print("※ 참고:")
    print("  - 각 항목은 가능한 한 기기 내 시스템 프로퍼티(/proc/meminfo 포함)를 그대로 사용해 표시합니다.")
    print("  - 제조사별 전용 프로퍼티는 다른 기기에서는 비어 있을 수 있습니다.")


if __name__ == "__main__":
    main()


