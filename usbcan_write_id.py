import threading
import time
from ctypes import *

lib = cdll.LoadLibrary("./libusbcan.so")

USBCAN_II = c_uint32(4)
MAX_CHANNELS = 1
g_thd_run = 1

class ZCAN_CAN_OBJ(Structure):
    _fields_ = [("ID", c_uint32),
                ("TimeStamp", c_uint32),
                ("TimeFlag", c_uint8),
                ("SendType", c_byte),
                ("RemoteFlag", c_byte),
                ("ExternFlag", c_byte),
                ("DataLen", c_byte),
                ("Data", c_ubyte*8),
                ("Reserved", c_ubyte*3)]

class ZCAN_CAN_INIT_CONFIG(Structure):
    _fields_ = [("AccCode", c_int),
                ("AccMask", c_int),
                ("Reserved", c_int),
                ("Filter", c_ubyte),
                ("Timing0", c_ubyte),
                ("Timing1", c_ubyte),
                ("Mode", c_ubyte)]

def rx_thread(DeviceType, DevIdx, chn_idx):
    global g_thd_run
    while g_thd_run == 1:
        time.sleep(0.1)
        count = lib.VCI_GetReceiveNum(DeviceType, DevIdx, chn_idx)
        if count > 0:
            can = (ZCAN_CAN_OBJ * count)()
            rcount = lib.VCI_Receive(DeviceType, DevIdx, chn_idx, byref(can), count, 100)
            for i in range(rcount):
                print(f"[{can[i].TimeStamp}] CH{chn_idx} ID: 0x{can[i].ID:x} ", end='')
                print("扩展帧" if can[i].ExternFlag == 1 else "标准帧", end=' ')
                if can[i].RemoteFlag == 0:
                    print("Data:", end=' ')
                    for j in range(can[i].DataLen):
                        print(f"{can[i].Data[j]:02x}", end=' ')
                else:
                    print("远程帧", end='')
                print("")

if __name__ == "__main__":
    threads = []
    gBaud = 0x1400
    DevType = USBCAN_II
    DevIdx = 0

    ret = lib.VCI_OpenDevice(DevType, DevIdx, 0)
    if ret == 0:
        print("Open device fail")
        exit(0)
    else:
        print("Opendevice success")

    for i in range(MAX_CHANNELS):
        init_config = ZCAN_CAN_INIT_CONFIG()
        init_config.AccCode = 0
        init_config.AccMask = 0xFFFFFFFF
        init_config.Reserved = 0
        init_config.Filter = 1
        init_config.Timing0 = gBaud & 0xff
        init_config.Timing1 = gBaud >> 8
        init_config.Mode = 0
        ret = lib.VCI_InitCAN(DevType, 0, i, byref(init_config))
        if ret == 0:
            print(f"InitCAN({i}) fail")
        else:
            print(f"InitCAN({i}) success")

        ret = lib.VCI_StartCAN(DevType, 0, i)
        if ret == 0:
            print(f"StartCAN({i}) fail")
        else:
            print(f"StartCAN({i}) success")

        thread = threading.Thread(target=rx_thread, args=(DevType, DevIdx, i,))
        threads.append(thread)
        thread.start()

    new_can_id = 2  # Set to desired CAN ID (1-32)

    setid_msg = ZCAN_CAN_OBJ()
    setid_msg.ID = 0x300  # Correct broadcast address for CANID setting
    setid_msg.SendType = 0
    setid_msg.RemoteFlag = 0
    setid_msg.ExternFlag = 0
    setid_msg.DataLen = 8
    setid_msg.Data[0] = 0x79  # Command byte
    setid_msg.Data[1] = 0x00
    setid_msg.Data[2] = 0x00  # 0 = write
    setid_msg.Data[3] = 0x00
    setid_msg.Data[4] = 0x00
    setid_msg.Data[5] = 0x00
    setid_msg.Data[6] = 0x00  # Not used for 1-32
    setid_msg.Data[7] = new_can_id  # New CAN ID

    for ch in range(MAX_CHANNELS):
        send_ret = lib.VCI_Transmit(DevType, 0, ch, byref(setid_msg), 1)
        if send_ret == 1:
            print(f"Set CAN ID command sent (new ID: {new_can_id}) on channel {ch}")
        else:
            print(f"Transmit fail on channel {ch}, sendcount is: {send_ret}")


    input("Press Enter to exit...\n")
    g_thd_run = 0

    for thread in threads:
        thread.join()

    for i in range(MAX_CHANNELS):
        ret = lib.VCI_ResetCAN(DevType, DevIdx, i)
        if ret == 0:
            print(f"ResetCAN({i}) fail")
        else:
            print(f"ResetCAN({i}) success")

    ret = lib.VCI_CloseDevice(DevType, DevIdx)
    if ret == 0:
        print("Closedevice fail")
    else:
        print("Closedevice success")
    del lib