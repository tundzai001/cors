import serial
import socket
from time import sleep
import configparser
import argparse
import base64
import os
import threading
from multiprocessing import Pool
import math
import tkinter as tk
from tkinter import simpledialog, messagebox

config = configparser.ConfigParser()

LICENSE_SECTION = "License"
home_dir = os.path.expanduser(f"~{os.getenv('SUDO_USER') or os.getenv('USER')}")
license_path = os.path.join(home_dir, "Desktop", "license.txt")

config.read('config.ini')

def passwordEncoding(username, password):
    auth = f'{username}:{password}'
    auth = auth.encode('ascii')
    auth = base64.b64encode(auth).decode('ascii')
    return auth

def get_rpi_serial():
    try:
        with open('/proc/cpuinfo', 'r') as f:
            for line in f:
                if line.startswith('Serial'):
                    return line.split(':')[1].strip()
    except Exception:
        return None

def get_license_code1(string_number):
    length = len(string_number)
    arr = [0] * (length + 1)
    narr = [0.0] * (length + 1)

    number = float(string_number)
    r_string = ""
    n = length

    number *= n
    arr[0] = int(number * math.pow(10.0, -float(n)))

    for i in range(1, length):
        number = number - float(arr[i - 1]) / math.pow(10.0, -float(n))
        n -= 1
        arr[i] = int(number * math.pow(10, -float(n)))

    arr[length] = (arr[length - 2] + arr[length - 1]) / 2 + 1
    narr[0] = arr[0]

    for i in range(1, length + 1):
        narr[i] = (narr[i - 1] + float(arr[i])) / 2;

    for i in range(length):
        A = narr[i + 1] * math.exp(-0.2)
        B = (math.log(arr[length], 10)) * math.pow(arr[i + 1], 0.2)
        D = str(int(round(A + B)))
        r_string = r_string + D

    return r_string

def save_license_to_config(code):
    config = configparser.ConfigParser()
    config[LICENSE_SECTION] = {'code': code}
    with open(license_path, 'w') as f:
        config.write(f)

def read_license_from_config():
    if not os.path.exists(license_path):
        return None
    config = configparser.ConfigParser()
    config.read(license_path)
    return config.get(LICENSE_SECTION, 'code', fallback=None)

def license_is_valid():
    SerialNumber = get_rpi_serial()
    if not SerialNumber:
        messagebox.showerror("Error", "Could not read Raspberry Pi serial number.")
        return False
    digits_only = ''.join(filter(str.isdigit, SerialNumber))
    if not digits_only:
        messagebox.showerror("Error", "Serial number has no digits.")
        return False

    newoutcode = ""
    new_string = ""
    if len(SerialNumber) > 10:
        n = len(SerialNumber) - 10
        newSNumber = SerialNumber[-n:]

        for c in newSNumber:
            if 'a' <= c <= 'z':
                new_char = c.upper()
                new_string += new_char
            else:
                new_string += c

        for char in new_string:
            newvalue = ord(char);
            newoutcode += str(newvalue);

        if (len(newoutcode) > 12):
            n1 = len(newoutcode) - 12
            newoutcode = newoutcode[-n1:]

        NewAtivatedCode = newoutcode#"485748546966"
    license_code = get_license_code1(NewAtivatedCode) #"756885565577"
    saved_code = read_license_from_config()

    return saved_code == license_code

def validate_and_save(user_input, root, license_code):
    if user_input == license_code:
        save_license_to_config(user_input)
        messagebox.showinfo("Success", "License verified and saved.")
        root.destroy()
        main()
    else:
        messagebox.showerror("Invalid", "Invalid license. Try again.")

def show_license_gui():
    SerialNumber = get_rpi_serial()

    newoutcode = ""
    new_string = ""
    if len(SerialNumber) > 10:
        n = len(SerialNumber) - 10
        newSNumber = SerialNumber[-n:]

        for c in newSNumber:
            if 'a' <= c <= 'z':
                new_char = c.upper()
                new_string += new_char
            else:
                new_string += c

        for char in new_string:
            newvalue = ord(char);
            newoutcode += str(newvalue);

        if (len(newoutcode) > 12):
            n1 = len(newoutcode) - 12
            newoutcode = newoutcode[-n1:]

        NewAtivatedCode = newoutcode
        license_code = get_license_code1(NewAtivatedCode)

    root = tk.Tk()
    root.title("License Activation")
    root.geometry("300x150")
    root.resizable(False, False)

    label = tk.Label(root, text="Enter License Code:")
    label.pack(pady=10)

    entry = tk.Entry(root, width=30)
    entry.pack()

    def on_submit():
        user_input = entry.get().strip()
        validate_and_save(user_input, root, license_code)

    button = tk.Button(root, text="Activate", command=on_submit)
    button.pack(pady=10)

    root.protocol("WM_DELETE_WINDOW", root.quit)
    root.mainloop()

def Initialization():
    config = configparser.ConfigParser()
    config['DEFAULT'] = {}
    print('NSensor: ')
    config['DEFAULT']['NComport'] = input()
    for i in range(1, int(config['DEFAULT']['NComport'])+1):
        print(f'RTCM{i}: ')
        config['DEFAULT'][f'RTCM{i}'] = input()
        if int(config['DEFAULT'][f'RTCM{i}'])==1:
            print(f'RTCMServer{i}: ')
            config['DEFAULT'][f'RTCMServer{i}'] = input()
            print(f'RTCMPort{i}: ')
            config['DEFAULT'][f'RTCMPort{i}'] = input()
            print(f'RTCMMountpoint{i}: ')
            config['DEFAULT'][f'RTCMMountpoint{i}'] = input()
            print(f'RTCMUsername{i}: ')
            config['DEFAULT'][f'RTCMUsername{i}'] = input()
            print(f'RTCMPassword{i}: ')
            config['DEFAULT'][f'RTCMPassword{i}'] = input()
        print(f'Comport{i}: ')
        config['DEFAULT'][f'Comport{i}'] = input()
        print(f'Sensor{i}: ')
        config['DEFAULT'][f'Sensor{i}'] = input()
        print(f'ServerHost{i}: ')
        config['DEFAULT'][f'ServerHost{i}'] = input()
        print(f'Port{i}: ')
        config['DEFAULT'][f'Port{i}'] = input()
        print(f'MountPoint{i}: ')
        config['DEFAULT'][f'MountPoint{i}'] = input()
        print(f'Password{i}: ')
        config['DEFAULT'][f'Password{i}'] = input()
    print('ReconnectionInterval: ')
    config['DEFAULT']['ReconnectionInterval'] = input()
    with open('config.ini', 'w') as configfile:
        config.write(configfile)
def internet(host="8.8.8.8", port=53, timeout=3):
    try:
        socket.setdefaulttimeout(timeout)
        socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect((host, port))
        return True
    except socket.error as ex:
        print(ex)
        return False
def setRoverMode():
    print('Sensor No:')
    i = int(input())
    ser = serial.Serial(
        port = config['DEFAULT'][f'Comport{i}'],
        baudrate = 115200,
        parity = serial.PARITY_NONE,
        stopbits = serial.STOPBITS_ONE,
        bytesize = serial.EIGHTBITS,
        timeout = 1
    )
    match config['DEFAULT'][f'Sensor{i}']:
        case 'Ublox':
            message = bytearray(b'\xb5\x62\x06\x71\x28\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
            CK_A = 0
            CK_B = 0
            for i in range (2, 46):
                CK_A = CK_A + message[i]
                CK_B = CK_B + CK_A
            CK_A = CK_A & 0xff
            CK_B = CK_B & 0xff
            message[46] = CK_A
            message[47] = CK_B
            print(message)
            ser.write(bytes(message))
            ser.write(bytes(b'\xb5\x62\x06\x09\x0d\x00\x00\x00\x00\x00\xff\xff\x00\x00\x00\x00\x00\x00\x03\x1d\xab'))
        case 'Unicorecomm':
            ser.write(f'MODE ROVER\r\n'.encode())
            ser.write(f'SAVECONFIG\r\n'.encode())
            print(f'MODE ROVER\r\n')
def setBaseMode1():
    print('Sensor No:')
    i = int(input())
    ser = serial.Serial(
        port = config['DEFAULT'][f'Comport{i}'],
        baudrate = 115200,
        parity = serial. PARITY_NONE,
        stopbits = serial.STOPBITS_ONE,
        bytesize = serial.EIGHTBITS,
        timeout = 1
    )
    match config['DEFAULT'][f'Sensor{i}']:
        case 'Ublox':
            print('Duration: ')
            svinMinDur = int(input())
            print('Accuracy: ')
            svinAccLimit = float(input())
            message = bytearray(b'\xb5\x62\x06\x71\x28\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
            message[8] = 1
            svinMinDur = bytearray(int(svinMinDur).to_bytes(4, byteorder='little'))
            svinAccLimit = bytearray(int(svinAccLimit*10000).to_bytes(4, byteorder='little'))
            for i in range (0, 4):
                message[30 + i] = svinMinDur[i]
                message[34 + i] = svinAccLimit[i]
            CK_A = 0
            CK_B = 0
            for i in range (2, 46):
                CK_A = CK_A + message[i]
                CK_B = CK_B + CK_A
            CK_A = CK_A & 0xff
            CK_B = CK_B & 0xff
            message[46] = CK_A
            message[47] = CK_B
            ser.write(bytes(message))
            print(message)
            ser.write(bytes(b'\xb5\x62\x06\x09\x0d\x00\x00\x00\x00\x00\xff\xff\x00\x00\x00\x00\x00\x00\x03\x1d\xab'))
        case 'Unicorecomm':
            print('Duration: ')
            T = int(input())
            print('Accuracy: ')
            DISTANCE = float(input())
            if DISTANCE == '':
                ser.write(f'MODE BASE TIME {T}\r\n'.encode())
                ser.write(f'SAVECONFIG\r\n'.encode())
                print(f'MODE BASE TIME {T}')
            else:
                ser.write(f'MODE BASE TIME {T} {DISTANCE}\r\n'.encode())
                ser.write(f'SAVECONFIG\r\n'.encode())
                print(f'MODE BASE TIME {T} {DISTANCE}')
def setBaseMode2():
    print('Sensor No:')
    i = int(input())
    ser = serial.Serial(
        port = config['DEFAULT'][f'Comport{i}'],
        baudrate = 115200,
        parity = serial. PARITY_NONE,
        stopbits = serial.STOPBITS_ONE,
        bytesize = serial.EIGHTBITS,
        timeout = 1
    )
    match config['DEFAULT'][f'Sensor{i}']:
        case 'Ublox':
            print('lla: ')
            lla = int(input())
            if lla:
                print('Latitude: ')
                XOrLat = float(input())
                print('Longtitude: ')
                YOrLon = float(input())
                print('Altitude: ')
                ZOrAlt = float(input())
            else:
                print('EcefX: ')
                XOrLat = float(input())
                print('EcefY: ')
                YOrLon = float(input())
                print('EcefZ: ')
                ZOrAlt = float(input())
            print('Accuracy: ')
            Accuracy = float(input())
            message = bytearray(b'\xb5\x62\x06\x71\x28\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
            message[8] = 2
            multiplier = 0
            if not lla:
                message[9] = 0
                multiplier = 100
            else:
                message[9] = 1
                multiplier = 10000000
            ecefXOrLat = int(XOrLat*multiplier)
            ecefXOrLatHP = int((XOrLat*multiplier-ecefXOrLat)*100).to_bytes(1, byteorder='little', signed=True)
            ecefXOrLat = int(XOrLat*multiplier).to_bytes(4, byteorder='little', signed=True)
            ecefYOrLon = int(YOrLon*multiplier)
            ecefYOrLonHP = int((YOrLon*multiplier-ecefYOrLon)*100).to_bytes(1, byteorder='little', signed=True)
            ecefYOrLon = int(YOrLon*multiplier).to_bytes(4, byteorder='little', signed=True)
            ecefZOrAlt = int(ZOrAlt*100)
            ecefZOrAltHP = int((ZOrAlt*100-ecefZOrAlt)*100).to_bytes(1, byteorder='little', signed=True)
            ecefZOrAlt = int(ZOrAlt*100).to_bytes(4, byteorder='little', signed=True)
            fixedPosAcc = int(Accuracy*10000).to_bytes(4, byteorder='little')
            for i in range (0, 4):
                message[10 + i] = ecefXOrLat[i]
                message[14 + i] = ecefYOrLon[i]
                message[18 + i] = ecefZOrAlt[i]
                message[26 + i] = fixedPosAcc[i]
            message[22] = ecefXOrLatHP[0]
            message[23] = ecefYOrLonHP[0]
            message[24] = ecefZOrAltHP[0]
            CK_A = 0
            CK_B = 0
            for i in range (2, 46):
                CK_A = CK_A + message[i]
                CK_B = CK_B + CK_A
            CK_A = CK_A & 0xff
            CK_B = CK_B & 0xff
            message[46] = CK_A
            message[47] = CK_B
            ser.write(bytes(message))
            print(message)
            ser.write(bytes(b'\xb5\x62\x06\x09\x0d\x00\x00\x00\x00\x00\xff\xff\x00\x00\x00\x00\x00\x00\x03\x1d\xab'))
        case 'Unicorecomm':
            print('Latitude or EcefX: ')
            XOrLat = input()
            print('Longtitude or EcefY: ')
            YOrLon = input()
            print('Altitude or EcefZ: ')
            ZOrAlt = input()
            ser.write(f'MODE BASE {XOrLat} {YOrLon} {ZOrAlt}\r\n'.encode())
            ser.write(f'SAVECONFIG\r\n'.encode())
            print(f'MODE BASE {XOrLat} {YOrLon} {ZOrAlt}')
def getData(i):
    def getRTCM():
        RTCMMountpoint = config['DEFAULT'][f'RTCMMountpoint{i}']
        RTCMUsername = config['DEFAULT'][f'RTCMUsername{i}']
        RTCMPassword = config['DEFAULT'][f'RTCMPassword{i}']
        RTCMAuthenticate = passwordEncoding(RTCMUsername, RTCMPassword)
        ReconnectionInterval = int(config['DEFAULT'][f'ReconnectionInterval'])
        clientsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connected2 = False
        while True:
            try:
                RTCMPort = int(config['DEFAULT'][f'RTCMPort{i}'])
                RTCMServer = socket.gethostbyname(config['DEFAULT'][f'RTCMServer{i}'])
                if not connected2:
                    clientsocket.connect((RTCMServer, RTCMPort))
                    clientsocket.send(bytes(f'GET /{RTCMMountpoint} HTTP/1.0\r\nUser-Agent: NTRIP AitogyNTRIPClient/20131124\r\nAuthorization: Basic {RTCMAuthenticate}\r\nAccept: */*\r\nConnection: close\r\n\r\n', 'ascii'))
                    response = clientsocket.recv(1024).decode('utf-8')
                    print(response)
                connected2 = True
                while connected2:
                    if not internet():
                        raise socket.error
                    data = clientsocket.recv(1024)
                    ser.write(data)
            except serial.SerialTimeoutException:
                pass
            except socket.error:
                RTCMMountpoint = config['DEFAULT'][f'RTCMMountpoint{i}']
                RTCMUsername = config['DEFAULT'][f'RTCMUsername{i}']
                RTCMPassword = config['DEFAULT'][f'RTCMPassword{i}']
                RTCMAuthenticate = passwordEncoding(RTCMUsername, RTCMPassword)
                ReconnectionInterval = int(config['DEFAULT'][f'ReconnectionInterval'])
                connected2 = False
                while not connected2:
                    try:
                        RTCMPort = int(config['DEFAULT'][f'RTCMPort{i}'])
                        RTCMServer = socket.gethostbyname(config['DEFAULT'][f'RTCMServer{i}'])
                        clientsocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        clientsocket.connect((RTCMServer, RTCMPort))
                        clientsocket.sendall(bytes(f'GET /{RTCMMountpoint} HTTP/1.0\r\nUser-Agent: NTRIP AitogyNTRIPClient/20131124\r\nAuthorization: Basic {RTCMAuthenticate}=\r\nAccept: */*\r\nConnection: close\r\n\r\n', 'utf-8'))
                        response = clientsocket.recv(1024).decode()
                        print(response)
                        connected2 = True
                    except socket.error:
                        print('Reconnection Unsuccessfully')
                        sleep(ReconnectionInterval)
    ser = serial.Serial(
        port = config['DEFAULT'][f'Comport{i}'],
        baudrate = 115200,
        parity = serial. PARITY_NONE,
        stopbits = serial.STOPBITS_ONE,
        bytesize = serial.EIGHTBITS,
        timeout = 1
    )
    Password = config['DEFAULT'][f'Password{i}']
    MountPoint = config['DEFAULT'][f'MountPoint{i}']
    ClientSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connected = False
    if int(config['DEFAULT'][f'RTCM{i}'])==1:
        t1 = threading.Thread(target=getRTCM, name='t1')
        t1.start()
    while True:
        try:
            TCPport = int(config['DEFAULT'][f'Port{i}'])
            ServerHost = socket.gethostbyname(config['DEFAULT'][f'ServerHost{i}'])
            if not connected:
                ClientSocket.connect((ServerHost, TCPport))
                ClientSocket.sendall(bytes(f"SOURCE {Password} /{MountPoint} \r\nSource-Agent: NTRIP NtripServerCMD/1.0\r\n\r\n", "utf-8"))
                Response = ClientSocket.recv(1024).decode("utf-8")
                print(Response)
                connected = True
            while connected:
                data = ser.read(1024)
                if not internet():
                    raise socket.error
                ClientSocket.sendall(bytes(data))
        except serial.SerialTimeoutException:
            pass
        except socket.error:
            Password = config['DEFAULT'][f'Password{i}']
            MountPoint = config['DEFAULT'][f'MountPoint{i}']
            ReconnectionInterval = int(config['DEFAULT']['ReconnectionInterval'])
            print("Disconnected")
            connected = False
            sleep(10)
            while not connected:
                try:
                    ServerHost = socket.gethostbyname(config['DEFAULT'][f'ServerHost{i}'])
                    TCPport = int(config['DEFAULT'][f'Port{i}'])
                    ClientSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    ClientSocket.connect((ServerHost, TCPport))
                    ClientSocket.sendall(bytes(f"SOURCE {Password} /{MountPoint} \r\nSource-Agent: NTRIP NtripServerCMD/1.0\r\n\r\n", "utf-8"))
                    response = ClientSocket.recv(1024).decode('utf-8')
                    print(response)
                    connected = True
                except socket.error:
                    print("Reconnection Unsuccessfully")
                    sleep(ReconnectionInterval)
def pushData():
    Process = []
    for i in range(1, int(config['DEFAULT']['NComport'])+1):
        Process.append(i)
    pool = Pool(int(config['DEFAULT']['NComport']))
    pool.map(getData, Process)
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-srm', '--setRoverMode', action='store_true')
    parser.add_argument('-sbm1', '--setBaseMode1', action='store_true')
    parser.add_argument('-sbm2', '--setBaseMode2', action='store_true')
    parser.add_argument('-p', '--pushData', action='store_true')
    parser.add_argument('-i', '--initialization', action='store_true')
    args = parser.parse_args()
    if (args.initialization):
        Initialization()
    elif (args.setRoverMode):
        setRoverMode()
    elif (args.setBaseMode1):
        setBaseMode1()
    elif (args.setBaseMode2):
        setBaseMode2()
    elif (args.pushData):
        pushData()
if __name__ == "__main__":
    if license_is_valid():
        main()
    else:
        show_license_gui()