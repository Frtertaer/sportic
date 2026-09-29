#!/usr/bin/env python3
"""SSH to the DE server over a socket with the IP Don't-Fragment bit CLEARED.

The local egress path to 178.105.216.125 has a PMTUD black-hole: TCP sets DF,
full-size (~1500B) segments get silently dropped at a ~1492 hop, and the ICMP
"fragmentation needed" never makes it back to the flow -> SSH session phase hangs.
Clearing IP_DONTFRAGMENT lets that hop FRAGMENT our large packets instead of
dropping them, so the session completes without needing to lower the NIC MTU
(which requires admin). Key auth via ~/.ssh/de_cs2 (already installed server-side).

  python de_ssh.py deploy            upload live files + dry-run the live monitor
  python de_ssh.py run "<cmd>"       run an arbitrary command on DE
  python de_ssh.py put <loc> <rem>   upload one file
"""
from __future__ import annotations

import os
import socket
import sys
import time
from pathlib import Path

import paramiko

HOST = "178.105.216.125"
PORT = 22
USER = "root"
KEY = str(Path.home() / ".ssh" / "de_cs2")
IP_DONTFRAGMENT = 14  # Winsock IPPROTO_IP option; 0 = allow fragmentation


def _connect(timeout: int = 20) -> paramiko.SSHClient:
    last = None
    for attempt in range(1, 6):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            # clear DF so the narrow hop fragments instead of black-holing
            sock.setsockopt(socket.IPPROTO_IP, IP_DONTFRAGMENT, 0)
        except OSError:
            pass
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        try:
            sock.connect((HOST, PORT))
            cli = paramiko.SSHClient()
            cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            cli.connect(HOST, port=PORT, username=USER,
                        key_filename=KEY, sock=sock, timeout=timeout,
                        banner_timeout=timeout, auth_timeout=timeout,
                        look_for_keys=False, allow_agent=False)
            return cli
        except Exception as e:  # noqa: BLE001
            last = e
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass
            print(f"  connect try {attempt} failed: {type(e).__name__}: {e}")
            time.sleep(3)
    raise SystemExit(f"all connect attempts failed: {last}")


def run(cli: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def put(cli: paramiko.SSHClient, local: str, remote: str) -> None:
    sftp = cli.open_sftp()
    sftp.put(local, remote)
    sftp.close()
    print(f"  uploaded {local} -> {remote}")


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    action = sys.argv[1]
    cli = _connect()
    print(f"connected to {HOST} (DF cleared)")
    try:
        if action == "deploy":
            here = Path(__file__).resolve().parent
            put(cli, str(here / "cs2_live_scenarios.py"), "/opt/cs2-analyst/live_scenarios.py")
            put(cli, str(here / "cs2_live_monitor.py"), "/opt/cs2-analyst/live_monitor.py")
            print("=== dry-run live_monitor on DE ===")
            rc, out, err = run(cli, "cd /opt/cs2-analyst && ./venv/bin/python live_monitor.py --dry 2>&1")
            print(out or err)
            print(f"rc={rc}")
        elif action == "run":
            rc, out, err = run(cli, sys.argv[2])
            sys.stdout.write(out)
            if err:
                sys.stderr.write(err)
            print(f"\nrc={rc}")
        elif action == "put":
            put(cli, sys.argv[2], sys.argv[3])
        else:
            print("unknown action")
    finally:
        cli.close()


if __name__ == "__main__":
    main()
