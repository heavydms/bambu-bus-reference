#!/bin/bash
# SPDX-License-Identifier: GPL-3.0
# Copyright (C) 2026 heavydms. See LICENSE.
# Raw capture on the Uno Q from ONE FX2 analyzer, picked by its fixed USB hub-port path.
# Captures compressed .sr chunks only -- decode AFTERWARD (live sigrok UART decoding is too slow on the
# Uno Q's CPU and makes the FX2 overrun). Tested 2026-11-22:
#   - 12 MS/s overruns through the Uno Q's hub; 8 MS/s is a full capture, even with BOTH analyzers at once.
#   - idle bus: ~96 KB per 5 s chunk.  offline decode on the Uno Q: ~3.6x slower than real time.
# FX2s re-enumerate (new USB address) after firmware load, so bus.address is re-read from sysfs each chunk.
# Channel map (two-S3 bench, both analyzers identical): CH7 = Module A RX (RO), CH4 = Module B RX (RO).
#   usage: rawcap_unoq.sh <port-path e.g. 1-1.3.3> <outdir> [chunks (0=forever)] [seconds-per-chunk] [samplerate]
# decode a chunk later (Uno Q or Mac):
#   sigrok-cli -i chunk.sr -P uart:rx=D7:baudrate=1228800:parity=even -P uart:rx=D4:baudrate=1228800:parity=even -A uart=rx-data
PORT=$1; OUT=$2; N=${3:-0}; SECS=${4:-20}; SR=${5:-8m}
mkdir -p "$OUT"
resolve() { local d=/sys/bus/usb/devices/$PORT; [ -f $d/busnum ] && echo "$(cat $d/busnum).$(cat $d/devnum)"; }
i=0
while [ "$N" -eq 0 ] || [ $i -lt "$N" ]; do
  i=$((i+1)); t=$(date +%H%M%S)
  CONN=$(resolve)
  if [ -z "$CONN" ]; then echo "$(date +%T) no analyzer at $PORT" >> "$OUT/rawcap.status"; sleep 2; continue; fi
  e=$(sigrok-cli -d fx2lafw:conn=$CONN --config samplerate=$SR --time $((SECS*1000)) -C D4,D7 -o "$OUT/chunk_${t}.sr" 2>&1 | tr '\n' ' ')
  echo "$(date +%T) chunk $i conn=$CONN $(du -h "$OUT/chunk_${t}.sr" 2>/dev/null | cut -f1) ${e:-full}" >> "$OUT/rawcap.status"
done
