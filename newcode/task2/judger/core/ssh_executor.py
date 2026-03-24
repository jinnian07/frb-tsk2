import paramiko
import os
import threading

class SSHExecutor:
    def __init__(self, config):
        self.conf = config
        self.client = None
        self._stop_flag = threading.Event()
        self._connected = False

    def connect(self):
        if self._connected:
            return True
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.client.connect(
            self.conf['host'], 
            port=self.conf['port'], 
            username=self.conf['username'], 
            password=self.conf['password'],
            timeout=10
        )
        self.client.exec_command(f"mkdir -p {self.conf['remote_work_dir']}")
        self._connected = True
        self._stop_flag.clear()
        return True

    def is_connected(self):
        return self._connected and self.client is not None

    def stop(self):
        self._stop_flag.set()

    def should_stop(self):
        return self._stop_flag.is_set()

    def upload_file(self, local_path, remote_name):
        if self._stop_flag.is_set():
            raise InterruptedError("操作已取消")
        remote_path = f"{self.conf['remote_work_dir']}/{remote_name}"
        sftp = self.client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()
        return remote_path

    def download_file(self, remote_name, local_path):
        if self._stop_flag.is_set():
            raise InterruptedError("操作已取消")
        remote_path = f"{self.conf['remote_work_dir']}/{remote_name}"
        sftp = self.client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()

    def execute_timed(self, cmd, timeout=30):
        if self._stop_flag.is_set():
            raise InterruptedError("操作已取消")
        
        full_cmd = f"cd {self.conf['remote_work_dir']} && /usr/bin/time -f '%e' {cmd}"
        stdin, stdout, stderr = self.client.exec_command(full_cmd, timeout=timeout)
        
        out_content = stdout.read().decode()
        err_content = stderr.read().decode()
        
        err_lines = err_content.strip().split('\n')
        exec_time_ms = "0"
        if err_lines:
            try:
                exec_time_sec = float(err_lines[-1])
                exec_time_ms = str(int(exec_time_sec * 1000))
                err_content = '\n'.join(err_lines[:-1])
            except ValueError:
                pass

        return out_content, err_content, exec_time_ms

    def close(self):
        self._stop_flag.set()
        self._connected = False
        if self.client:
            self.client.close()
            self.client = None
