import os
import time
from multiprocessing import Pool

def get_connected_devices():
    command = "adb devices"
    result = os.popen(command).read()
    devices = []
    lines = result.strip().split('\n')
    for line in lines[1:]:
        if '\tdevice' in line:
            device = line.split('\t')[0]
            devices.append(device)
    return devices

def check_apk_installed(package_name, device):
    command = f"adb -s {device} shell pm list packages {package_name}"
    result = os.popen(command).read()
    return package_name in result

def uninstall_apk(package_name, device):
    command = f"adb -s {device} uninstall {package_name}"
    os.system(command)

def install_apk_on_device(args):
    apk_file, device = args
    start_time = time.time()
    
    command = f"adb -s {device} install -r {apk_file}"
    os.system(command)
    end_time = time.time() 
    install_time = end_time - start_time
    return apk_file, device, install_time

def main():
    apk_files = [file for file in os.listdir('.') if file.endswith('.apk')]
    devices = get_connected_devices()
    total_install_time = 0

    install_args = []
    for apk_file in apk_files:
        package_name = apk_file.split('.apk')[0]
        for device in devices:
            if check_apk_installed(package_name, device):
                print(f"Uninstalling {package_name} from {device}...")
                uninstall_apk(package_name, device)
            install_args.append((apk_file, device))
            print(f"Preparing to install {apk_file} on {device}...")

    with Pool() as pool:
        results = pool.map(install_apk_on_device, install_args)
    
    for apk_file, device, install_time in results:
        print(f"{apk_file} installed on {device} in {install_time:.2f} seconds")
        if total_install_time < install_time:
            total_install_time = install_time

    print(f"Total installation time for all APK files: {total_install_time:.2f} seconds")

if __name__ == '__main__':
    main()
