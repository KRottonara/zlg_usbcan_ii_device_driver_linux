# USBCAN-II Device Driver Installation and Usage

The USBCAN-II device driver is based on **libusb** and supports **libusb-1.0**.

## Prerequisites

Install the required library (Ubuntu or similar):

```sh
sudo apt-get install libusb-1.0-0
```

## Installation

Copy `libusbcan.so` to the `/lib` directory:

```sh
sudo cp libusbcan.so /lib
```

## Running Test Programs

- **C test program:**
    ```sh
    sudo ./test
    ```
- **Python test program:**
    ```sh
    sudo python3 usbcan.py
    ```

If `sudo ./test_zuds` reports "No such file or directory":

```sh
cd /lib
sudo cp libzuds.so.20231025 /usr/lib/
```

## Device Usage Instructions

1. **Check if the system detects the USB device and print VID/PID (USBCAN should be `0471:1200`):**
     ```sh
     lsusb
     ```

2. **Check USB device node permissions:**
     ```sh
     ls /dev/bus/usb/ -lR
     ```

3. **Modify USB device node permissions**  
     (Replace `xxx` with the bus number and `yyy` with the device number from `lsusb` output):
     ```sh
     chmod 666 /dev/bus/usb/xxx/yyy
     ```

4. **Permanently grant ordinary users permission to use the USBCAN device:**  
     Create `/etc/udev/rules.d/50-usbcan.rules` with the following content:
     ```
     SUBSYSTEMS=="usb", ATTRS{idVendor}=="0471", ATTRS{idProduct}=="1200", GROUP="users", MODE="0666"
     ```

     Reload udev rules:
     ```sh
     udevadm control --reload
     ```


     ## Reference

     For more information and source files, see:  
     [https://manual.zlg.cn/web/#/146](https://manual.zlg.cn/web/#/146)