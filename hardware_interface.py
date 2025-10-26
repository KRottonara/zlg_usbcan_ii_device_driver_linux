import threading
import time
from math import cos, pi
from ctypes import *
import sys

# --- Global Constants for ZLG USBCAN Device ---
# Assuming the C library 'libusbcan.so' is in the current directory
LIBRARY_PATH = "./libusbcan.so"
USBCAN_II = c_uint32(4)  # Device Type constant for USBCAN-II
MAX_CHANNELS = 1         # The maximum number of CAN channels to initialize
WHEEL_RADIUS = 0.04      # Wheel radius in meters (example value)
WHEEL_TO_CENTER = 0.27  # Distance from wheel to robot center in meters

# --- C Structure Definitions (Mapping to libusbcan.h) ---

class ZCAN_CAN_OBJ(Structure):
    """
    Structure for a single CAN frame (data object).
    Used for both transmission and reception.
    """
    _fields_ = [("ID", c_uint32),        # Frame ID (11-bit standard or 29-bit extended)
                ("TimeStamp", c_uint32), # Timestamp of the frame
                ("TimeFlag", c_uint8),   # Whether timestamp is used
                ("SendType", c_byte),    # Transmission type (0=normal, 1=single send, 2=self-receive)
                ("RemoteFlag", c_byte),  # Remote transmission request flag (0=data frame, 1=remote frame)
                ("ExternFlag", c_byte),  # Frame format flag (0=standard frame, 1=extended frame)
                ("DataLen", c_byte),     # Length of the data field (0-8)
                ("Data", c_ubyte*8),     # Data field
                ("Reserved", c_ubyte*3)] # Reserved

class ZCAN_CAN_INIT_CONFIG(Structure):
    """
    Structure for CAN channel initialization parameters.
    """
    _fields_ = [("AccCode", c_int),      # Acceptance code
                ("AccMask", c_int),      # Acceptance mask
                ("Reserved", c_int),     # Reserved
                ("Filter", c_ubyte),     # Filter mode (0=off, 1=single, 2=double)
                ("Timing0", c_ubyte),    # Baud rate parameter 0 (e.g., 0x00 for 1M)
                ("Timing1", c_ubyte),    # Baud rate parameter 1 (e.g., 0x14 for 1M)
                ("Mode", c_ubyte)]       # Operation mode (0=normal, 1=listen only)

# --- USBCAN Motor Controller Class ---

class USBCANMotorController:
    """
    Manages communication with a ZLG USBCAN device and implements
    common motor control commands.
    """
    def __init__(self, device_index=0, baud_rate_hex=0x1400):
        """
        Initializes the controller by loading the library and setting up parameters.
        :param device_index: Index of the CAN device (usually 0).
        :param baud_rate_hex: Baud rate code (e.g., 0x1400 for 1Mbit/s).
        """
        self.DevType = USBCAN_II
        self.DevIdx = device_index
        self.gBaud = baud_rate_hex
        self.lib = None
        self.threads = []
        self.g_thd_run = threading.Event()
        self.g_thd_run.set()  # Set the run flag initially to running

        try:
            # Load the shared library
            self.lib = cdll.LoadLibrary(LIBRARY_PATH)
        except OSError as e:
            print(f"Error loading library {LIBRARY_PATH}: {e}")
            self.lib = None

    def _rx_thread(self, chn_idx):
        """
        Dedicated thread for receiving CAN messages on a specific channel.
        Messages are printed to the console.
        """
        while self.g_thd_run.is_set():
            time.sleep(0.1)
            # Check how many messages are available
            count = self.lib.VCI_GetReceiveNum(self.DevType, self.DevIdx, chn_idx)
            
            if count > 0:
                # Create a C array to hold the received messages
                can = (ZCAN_CAN_OBJ * count)()
                # Read the messages
                rcount = self.lib.VCI_Receive(self.DevType, self.DevIdx, chn_idx, byref(can), count, 100)
                
                for i in range(rcount):
                    # Print received frame details
                    frame_type = "Extended Frame" if can[i].ExternFlag == 1 else "Standard Frame"
                    output = f"[{can[i].TimeStamp}] CH{chn_idx} ID: 0x{can[i].ID:x} {frame_type} "
                    
                    if can[i].RemoteFlag == 0:
                        output += "Data: " + " ".join(f"{can[i].Data[j]:02x}" for j in range(can[i].DataLen))
                    else:
                        output += "Remote Frame"
                    
                    print(output)

    def initialize(self):
        """
        Opens the device, initializes CAN channels, and starts the receive thread.
        :return: True if successful, False otherwise.
        """
        if self.lib is None:
            print("Initialization failed: USBCAN library not loaded.")
            return False

        # 1. Open Device
        ret = self.lib.VCI_OpenDevice(self.DevType, self.DevIdx, 0)
        if ret == 0:
            print("Error: Failed to open CAN device.")
            return False
        print("CAN device opened successfully.")

        # 2. Initialize and Start Channels
        for i in range(MAX_CHANNELS):
            init_config = ZCAN_CAN_INIT_CONFIG()
            init_config.AccCode = 0
            init_config.AccMask = 0xFFFFFFFF
            init_config.Reserved = 0
            init_config.Filter = 1 # Single filter mode
            init_config.Timing0 = self.gBaud & 0xff
            init_config.Timing1 = self.gBaud >> 8
            init_config.Mode = 0 # Normal mode

            ret = self.lib.VCI_InitCAN(self.DevType, self.DevIdx, i, byref(init_config))
            if ret == 0:
                print(f"Error: Failed to initialize CAN channel {i}.")
                self.close()
                return False
            print(f"CAN channel {i} initialized successfully.")

            ret = self.lib.VCI_StartCAN(self.DevType, self.DevIdx, i)
            if ret == 0:
                print(f"Error: Failed to start CAN channel {i}.")
                self.close()
                return False
            print(f"CAN channel {i} started successfully.")

            # 3. Start Receive Thread
            thread = threading.Thread(target=self._rx_thread, args=(i,))
            self.threads.append(thread)
            thread.start()

        print(f"Receive threads started for {MAX_CHANNELS} channel(s).")
        return True

    def set_motor_speed(self, motor_id, speed_dps, max_torque):
        """
        Sends a Speed Closed-Loop Control command (0xA2) to the motor gateway.
        This command typically controls a group of motors or a designated CAN ID.

        :param motor_id: The ID of the motor (used for logging only, command is sent to 0x280).
        :param speed_dps: Desired speed in degrees per second (dps). Can be positive or negative.
        :param max_torque: Maximum torque limit (0-255).
        :return: True if transmission was successful, False otherwise.
        """
        if not self.lib:
            print("Library not initialized. Cannot send command.")
            return False

        # Protocol unit is 0.01 dps/LSB
        speed_ctrl_value = int(speed_dps / 0.01)

        msg = ZCAN_CAN_OBJ()
        msg.ID = 0x140+motor_id  # Common CAN ID for motor control gateway
        msg.SendType = 0
        msg.RemoteFlag = 0
        msg.ExternFlag = 0
        msg.DataLen = 8

        # Convert the signed integer speed value to 4 bytes (little-endian)
        # using 32-bit signed representation.
        try:
            speed_bytes = speed_ctrl_value.to_bytes(4, byteorder='little', signed=True)
        except OverflowError:
            print(f"Error: Speed value {speed_ctrl_value} is out of 32-bit signed range.")
            return False

        # Pack the CAN frame data
        msg.Data[0] = 0xA2              # Command: Speed Closed-Loop Control
        msg.Data[1] = int(max_torque)   # Max Torque Limit (0-255)
        msg.Data[2] = 0x00              # Reserved
        msg.Data[3] = 0x00              # Reserved
        msg.Data[4] = speed_bytes[0]    # Speed LSB
        msg.Data[5] = speed_bytes[1]
        msg.Data[6] = speed_bytes[2]
        msg.Data[7] = speed_bytes[3]    # Speed MSB

        success = True
        for ch in range(MAX_CHANNELS):
            send_ret = self.lib.VCI_Transmit(self.DevType, self.DevIdx, ch, byref(msg), 1)
            if send_ret == 1:
                print(f"Speed command (ID {motor_id}, {speed_dps} dps) sent on channel {ch}.")
            else:
                print(f"Error: Transmit failed on channel {ch}. Send count: {send_ret}")
                success = False
        return success

    def stop_all_motors(self):
        """
        Sends the Motor Stop Command (0x81) to the motor gateway (0x280).
        """
        if not self.lib:
            print("Library not initialized. Cannot send command.")
            return False

        stop_msg = ZCAN_CAN_OBJ()
        stop_msg.ID = 0x280
        stop_msg.SendType = 0
        stop_msg.RemoteFlag = 0
        stop_msg.ExternFlag = 0
        stop_msg.DataLen = 8
        stop_msg.Data[0] = 0x81  # Command: Motor Stop
        
        # Fill remaining bytes with 0x00
        for i in range(1, 8):
            stop_msg.Data[i] = 0x00

        success = True
        for ch in range(MAX_CHANNELS):
            send_ret = self.lib.VCI_Transmit(self.DevType, self.DevIdx, ch, byref(stop_msg), 1)
            if send_ret == 1:
                print(f"Stop command (0x81) sent successfully on channel {ch}.")
            else:
                print(f"Error: Stop command transmit failed on channel {ch}. Send count: {send_ret}")
                success = False
        return success
    
    def shutdown_all_motors(self):
        """
        Sends the Motor Shutdown Command (0x80) to the motor gateway (0x280).
        """
        if not self.lib:
            print("Library not initialized. Cannot send command.")
            return False

        stop_msg = ZCAN_CAN_OBJ()
        stop_msg.ID = 0x280
        stop_msg.SendType = 0
        stop_msg.RemoteFlag = 0
        stop_msg.ExternFlag = 0
        stop_msg.DataLen = 8
        stop_msg.Data[0] = 0x80  # Command: Motor Shutdown

        # Fill remaining bytes with 0x00
        for i in range(1, 8):
            stop_msg.Data[i] = 0x00

        success = True
        for ch in range(MAX_CHANNELS):
            send_ret = self.lib.VCI_Transmit(self.DevType, self.DevIdx, ch, byref(stop_msg), 1)
            if send_ret == 1:
                print(f"Stop command (0x80) sent successfully on channel {ch}.")
            else:
                print(f"Error: Stop command transmit failed on channel {ch}. Send count: {send_ret}")
                success = False
        return success

    def move_robot_linear(self, linear_velocity, angle_deg, angular_velocity_deg=0):
        """
        Moves the robot by setting the appropriate motor speeds based on the desired
        linear and angular velocities.

        :param linear_velocity: Desired linear velocity (in m/s).
        :param angle: Desired angular position (in degrees).
        """

        v1 = -linear_velocity * cos((-angle_deg+30.0) * 3.14159 / 180.0)-  (angular_velocity_deg*pi/180)*WHEEL_TO_CENTER
        v2 = linear_velocity * cos((30.0+angle_deg) * 3.14159 / 180.0)- (angular_velocity_deg*pi/180)*WHEEL_TO_CENTER
        v3 = -linear_velocity * cos((90.0+angle_deg) * 3.14159 / 180.0)-  (angular_velocity_deg*pi/180)*WHEEL_TO_CENTER

        # Set motor speeds based on calculated velocities
        self.set_motor_speed(motor_id=1, speed_dps=(v1/WHEEL_RADIUS)*180/pi, max_torque=50)
        self.set_motor_speed(motor_id=2, speed_dps=(v2/WHEEL_RADIUS)*180/pi, max_torque=50)
        self.set_motor_speed(motor_id=3, speed_dps=(v3/WHEEL_RADIUS)*180/pi, max_torque=50)

        # Implementation would depend on the robot's kinematics and motor configuration.
        pass

    def move_robot(self, linear_velocity, angle_deg, angular_velocity_deg):
        """
        Moves the robot by setting the appropriate motor speeds based on the desired
        linear and angular velocities.

        :param linear_velocity: Desired linear velocity (in mm/s).
        :param angular_velocity: Desired angular velocity (in degrees/s).
        """
        start_time = time.time()
        try:
            while True:
                elapsed = time.time() - start_time
                current_angle = angle_deg - elapsed * angular_velocity_deg
                self.move_robot_linear(linear_velocity, current_angle, angular_velocity_deg)
                print(f"Moving robot at linear velocity: {linear_velocity} m/s, angle: {current_angle} deg, angular velocity: {angular_velocity_deg} deg/s")
                time.sleep(0.001)  # Use a reasonable sleep interval

        except KeyboardInterrupt:
            print("\nLoop interrupted by user.")
            controller.shutdown_all_motors()

        # Implementation would depend on the robot's kinematics and motor configuration.
        pass


    def close(self):
        """
        Stops the receive threads, resets CAN channels, and closes the device.
        """
        if not self.lib:
            return

        # 1. Stop threads
        self.g_thd_run.clear()
        for thread in self.threads:
            thread.join()
        print("All receive threads stopped.")

        # 2. Reset CAN channels
        for i in range(MAX_CHANNELS):
            ret = self.lib.VCI_ResetCAN(self.DevType, self.DevIdx, i)
            print(f"CAN channel {i} reset {'success' if ret != 0 else 'fail'}.")

        # 3. Close Device
        ret = self.lib.VCI_CloseDevice(self.DevType, self.DevIdx)
        print(f"CAN device closed {'success' if ret != 0 else 'fail'}.")
        
        # 4. Unload library reference
        del self.lib
        self.lib = None
        self.threads = []

    def __del__(self):
        """Ensure cleanup if the object is destroyed."""
        self.close()


if __name__ == "__main__":
    # --- Example Usage ---
    
    # 1. Create and Initialize the Controller (Baud rate 1Mbit/s: 0x1400)
    controller = USBCANMotorController(device_index=0, baud_rate_hex=0x1400)
    
    if controller.initialize():
        try:
            # 2. Assign a speed to Motor ID 1
            MOTOR_ID = 2
            DESIRED_SPEED_DPS = 360  # 500 degrees per second
            MAX_TORQUE_LIMIT = 150   # 0-255

            print("\n--- Sending Speed Command ---")
            print("Use Ctrl+C to stop the robot and exit the loop.")

            controller.move_robot(linear_velocity=0.05, angle_deg=0, angular_velocity_deg=10)

            print("\nMotors are now running. Check CAN receive output...")
            
            # Keep the application running to receive messages
            input("\nPress Enter to send STOP command...\n")

            # 3. Stop the motors
            print("\n--- Sending Shutdown Command ---")

            controller.shutdown_all_motors()

            input("\nPress Enter to safely exit and close the device...\n")

        finally:
            # 4. Clean up and close device
            controller.close()
    else:
        print("Controller initialization failed. Exiting.")
