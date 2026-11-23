#!/usr/bin/env python3
# Merged two-board bench logger for the Arduino Uno Q (or any host). Reads both XIAO S3 bridges' USB-CDC
# logs, timestamps and tags each line ([A]/[B]), and writes ONE merged map.log so the two bus legs line up.
# The boards share a D7 sync wire, so 'm' sent to board A stamps a MARK in BOTH logs at once.
#
#   usage: benchlog2.py --a /dev/ttyACM0 --b /dev/ttyACM1 [--out DIR] [--seconds N] [--no-changes]
#     --out DIR     directory for map.log + cmd.txt (default: cwd)
#     --seconds N   stop after N seconds (default: run until Ctrl-C)
#     --no-changes  do NOT put the boards in changes-only mode ('c') on connect
#   control: write to <out>/cmd.txt   e.g.  'm' -> send m to A (both boards mark);  'A:s' / 'B:z' -> key to one board
#
# Reconnect-safe: if a board's port drops it is reopened automatically. stdlib + pyserial only.
import argparse, os, sys, threading, time
import serial

def ts():
    t = time.time()
    return time.strftime('%H:%M:%S', time.localtime(t)) + f'.{int((t%1)*1000):03d}'

class Board:
    def __init__(self, tag, port, writer, send_changes):
        self.tag, self.port, self.writer, self.send_changes = tag, port, writer, send_changes
        self.ser = None
        self.buf = b''
        self.lock = threading.Lock()

    def _open(self):
        self.ser = serial.Serial(self.port, 115200, timeout=0.1)
        self.writer(f"##### [{self.tag}] open {self.port} at {ts()}")
        if self.send_changes:
            try:
                self.ser.write(b'c')  # changes-only on (toggle; sent once per open)
                self.writer(f"##### [{self.tag}] sent 'c' (changes-only)")
            except serial.SerialException:
                pass

    def send(self, keys):
        with self.lock:
            if self.ser:
                try:
                    self.ser.write(keys.encode())
                    self.writer(f"##### [{self.tag}] SENT {keys!r} at {ts()}")
                except serial.SerialException:
                    pass

    def run(self, stop):
        while not stop.is_set():
            try:
                if self.ser is None:
                    self._open()
                d = self.ser.read(8192)
                if d:
                    self.buf += d
                    while b'\n' in self.buf:
                        line, self.buf = self.buf.split(b'\n', 1)
                        self.writer(f"{ts()} [{self.tag}] {line.decode(errors='replace').rstrip()}")
            except (serial.SerialException, OSError):
                self.writer(f"##### [{self.tag}] lost {self.port}, reopening...")
                try:
                    if self.ser: self.ser.close()
                except Exception:
                    pass
                self.ser = None
                time.sleep(1.0)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--a', required=True, help='Bus A board port')
    ap.add_argument('--b', required=True, help='Bus B board port')
    ap.add_argument('--out', default='.', help='dir for map.log + cmd.txt')
    ap.add_argument('--seconds', type=float, default=0, help='stop after N s (0 = forever)')
    ap.add_argument('--no-changes', action='store_true', help='do not send changes-only on connect')
    args = ap.parse_args()

    logpath = os.path.join(args.out, 'map.log')
    cmdpath = os.path.join(args.out, 'cmd.txt')
    logf = open(logpath, 'a')
    wlock = threading.Lock()
    def writer(s):
        with wlock:
            logf.write(s + '\n'); logf.flush()

    boards = {'A': Board('A', args.a, writer, not args.no_changes),
              'B': Board('B', args.b, writer, not args.no_changes)}
    stop = threading.Event()
    threads = [threading.Thread(target=b.run, args=(stop,), daemon=True) for b in boards.values()]
    for t in threads: t.start()
    writer(f"##### benchlog2 start {ts()}  A={args.a}  B={args.b}  out={logpath}")

    end = time.time() + args.seconds if args.seconds else None
    try:
        while end is None or time.time() < end:
            if os.path.exists(cmdpath):
                try:
                    k = open(cmdpath).read().strip(); os.remove(cmdpath)
                except Exception:
                    k = ''
                if k:
                    if len(k) > 1 and k[0] in 'AB' and k[1] == ':':
                        boards[k[0]].send(k[2:])
                    else:
                        boards['A'].send(k)  # default: A; its 'm' syncs both via D7
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set(); time.sleep(0.3)
        writer(f"##### benchlog2 stop {ts()}")

if __name__ == '__main__':
    main()
