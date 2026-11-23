// SPDX-License-Identifier: GPL-3.0
// Copyright (C) 2026 heavydms. Contains framing/CRC/type-map code ported from
// Bambu-Research-Group/Bambu-Bus AMCU-v2 (GPL-3.0); modified by heavydms in 2026. See NOTICE.
//
// Interposer rung 2 - transparent bridge + live frame parser for the ESP32-S3.
// Spec, wiring and validation: README.md (sections 3, 10).
//
// Same transparent byte bridge as rung 1 (firmware/bus_bridge), but it also CRC-checks and decodes
// every frame per direction and logs the types, so we can watch the live protocol and CATCH the auth
// exchange (long-package type 0x040D cert / 0x040E challenge). Forwarding is still immediate and
// byte-for-byte; a COPY of each stream feeds a per-direction frame assembler, so parsing never delays
// the bridge. This confirms our decoder against real X2D traffic before rung 3 (answer frames ourselves).
//
// Validated on an X2D + AMS 2 Pro (boot, auth, load/unload, prints, AMS firmware update), 2026-11-21..23.
// Power the printer and AMS OFF before plugging bus connectors. USB log at 115200. Send '?' = help, 'z' = zero counts.
//
// Framing + CRC + type map ported from Bambu-Research-Group/Bambu-Bus AMCU-v2 (BambuBus.cpp):
//   frame starts 0x3D; byte1 bit7 set => short header (len at [2]), clear => long header (16-bit LE len at [4..5]).
//   len = total frame bytes incl. the trailing CRC16. Short type = byte[4]. Long type = [11]|[12]<<8,
//   target addr = [7]|[8]<<8. CRC16: poly 0x1021, init 0x913D, no reflect, no xorout.

#include "driver/uart.h"
#include "HWCDC.h"
#include "driver/gpio.h"
#include "esp_timer.h"

// ---------- Config (must match docs/interposer.md) ----------
#define BUS_BAUD   1228800        // 8E1; try 1250000 if CRC errors
#define RX_BUF     4096
#define CHUNK      1024
#define FRAME_MAX  1536   // long-header length is 16-bit; AMS firmware-update chunks are 457 B (type 0x0106), maybe more
#define TRACK_LEN  512    // bytes kept/printed per frame in changes-only tracking (RAM: TRACK_MAX x TRACK_LEN)

// Board: Seeed XIAO ESP32-S3 (two-S3 bench, 2026-11-21). D0..D5 are SILKSCREEN labels; the XIAO core maps
// them D0=GPIO1, D1=GPIO2, D2=GPIO3, D3=GPIO4, D4=GPIO5, D5=GPIO6. Using the Dn constants keeps the core as
// the source of truth, so a wrong board FQBN fails to compile instead of silently mis-mapping.
// Build FQBN: esp32:esp32:XIAO_ESP32S3. ONE sketch, flashed to BOTH boards (S3 "A" = Bus A, S3 "B" = Bus B);
// each board bridges its own two modules (one bus leg). See docs/interposer.md ("two-S3 rebuild").
#define A_UART UART_NUM_2         // Module A
#define A_RX D0                   // Module A RO (header "RX") -> MCU   (D0 = GPIO1)
#define A_TX D1                   // Module A DI (header "TX") <- MCU   (D1 = GPIO2)
#define A_DE D2                   // Module A DE/RE (EN)                (D2 = GPIO3, a strapping pin; driven as output after boot is fine)
#define B_UART UART_NUM_1         // Module B
#define B_RX D3                   // Module B RO (header "RX") -> MCU   (D3 = GPIO4)
#define B_TX D4                   // Module B DI (header "TX") <- MCU   (D4 = GPIO5)
#define B_DE D5                   // Module B DE/RE (EN)                (D5 = GPIO6)

// Explicit prototypes for the sync/LED helpers below: with BUF_TAP=1 the ISR is the first function, so the Arduino
// builder's auto-prototypes land above it and clash with these static definitions.
static void led_on();
static void do_mark(const char *src);
static void sync_send();

// Buffer-position tap (cable pins 5/6 = complementary 10 kHz PWM, duty = buffer position). Off by default; the
// spare (left) XIAO is built with -DBUF_TAP=1: wire 5 -> 10k -> D8, wire 6 -> 10k -> D9, pins 5/6 still jumpered
// straight through printer<->AMS (passive tap, never inline). D8/D9 = GPIO7/8, the same GPIOs as the 2026-11-21 tap.
#ifndef BUF_TAP
#define BUF_TAP 0
#endif
#if BUF_TAP
#define BUF_P5 D8
#define BUF_P6 D9
#define BUF_STEP_PCT 0.5f
#define BUF_MIN_MS 50
static volatile int64_t p5_rise = 0, p5_fall = 0;
static volatile uint32_t p5_hi_sum = 0, p5_per_sum = 0, p5_n = 0;
static void IRAM_ATTR p5_isr() {
  int64_t t = esp_timer_get_time();
  if (gpio_get_level((gpio_num_t)BUF_P5)) {
    if (p5_rise && p5_fall > p5_rise) { p5_hi_sum += (uint32_t)(p5_fall - p5_rise); p5_per_sum += (uint32_t)(t - p5_rise); p5_n++; }
    p5_rise = t;
  } else p5_fall = t;
}
#endif

#if !HWCDC_SERIAL_IS_DEFINED
HWCDC HWCDCSerial;
#endif

// ---------- Sync marker + LED (two-S3 bench) ----------
// D7 (GPIO44) is a one-wire marker line shared by both boards, open-drain: idle HIGH (INPUT_PULLUP), a board
// pulses it LOW to announce a MARK. Pressing 'm' on EITHER board stamps a synchronized MARK in BOTH logs, so
// the two independent USB captures (one per bus leg) can be lined up in time. The onboard LED (GPIO21, active
// LOW on the XIAO S3) blinks on every mark on both boards, so they flash together.
#define SYNC_PIN D7          // shared marker line (D7 = GPIO44)
#define LED_PIN  21          // XIAO ESP32-S3 onboard user LED, active LOW
#define LED_MS   120         // LED on-time per mark (ms)
static uint32_t ledOffAt = 0;
static int mk = 0;
static void led_on()  { digitalWrite(LED_PIN, LOW);  ledOffAt = millis() + LED_MS; }  // active LOW = on
static void do_mark(const char *src) {
  HWCDCSerial.printf("=== MARK %d (%s) at %lu ms ===\n", ++mk, src, (unsigned long)millis());
  led_on();
}
static void sync_send() {     // open-drain LOW pulse the other board polls for; never drives HIGH (pull-up does)
  pinMode(SYNC_PIN, OUTPUT); digitalWrite(SYNC_PIN, LOW);
  delayMicroseconds(3000);    // 3 ms LOW, comfortably longer than one loop iteration
  pinMode(SYNC_PIN, INPUT_PULLUP);
}

// ---------- CRC16 (0x1021, init 0x913D, no reflect) ----------
// Explicit prototypes: the Arduino builder's auto-prototypes land inside a function on this Mac's native ctags.
struct Asm;
struct Fwd;
static uint16_t crc16_bambu(const uint8_t *d, int n);
static const char *dirName(bool p2b);
static void decode(const uint8_t *f, int len, bool p2b);
static void feed(Asm &a, const uint8_t *d, int n, bool p2b);
static void help();
static void bus_uart_init(uart_port_t u, int tx, int rx, int de);

static uint16_t crc16_bambu(const uint8_t *d, int n) {
  uint16_t c = 0x913D;
  for (int i = 0; i < n; i++) {
    c ^= (uint16_t)d[i] << 8;
    for (int b = 0; b < 8; b++) c = (c & 0x8000) ? (uint16_t)((c << 1) ^ 0x1021) : (uint16_t)(c << 1);
  }
  return c;
}

// ---------- Per-direction frame assembler ----------
struct Asm {
  uint8_t buf[FRAME_MAX];
  int idx = 0, len = 0, lenIdx = 0;
};

// ---------- Counts ----------
struct Counts {
  uint32_t motion_s, motion_l, online, req6, nfc, setfil, heartbeat;   // short types
  uint32_t mc_online, filament, version, other, crc_bad;               // long + misc
};
// ---------- Changes-only mode ('c') ----------
// Prints a frame's hex when its bytes differ from the last frame of the same direction+type.
// No muting (the old 4-in-a-row mute hid the motion replies and the AMS debug text, 2026-11-20). Instead, each
// type prints at most once per CHG_MIN_MS; AMS->printer 0x021A (debug text) and auth frames always print.
// 'n' is kept as a no-op key for old scripts.
#define TRACK_MAX 96
#define CHG_MIN_MS 200
struct Track { bool used, p2b; uint32_t key; uint16_t len; uint32_t lastPrint; uint32_t skipped; uint8_t last[TRACK_LEN]; };
static Track trk[TRACK_MAX];
static bool changes_only = false;
static void track(const uint8_t *f, int len, bool p2b, uint32_t key);
static void track(const uint8_t *f, int len, bool p2b, uint32_t key) {
  Track *t = nullptr;
  for (int i = 0; i < TRACK_MAX; i++) if (trk[i].used && trk[i].p2b == p2b && trk[i].key == key) { t = &trk[i]; break; }
  bool fresh = false;
  if (!t) {
    for (int i = 0; i < TRACK_MAX; i++) if (!trk[i].used) { t = &trk[i]; break; }
    if (!t) return;
    t->used = true; t->p2b = p2b; t->key = key; t->len = 0; t->lastPrint = 0; t->skipped = 0; fresh = true;
  }
  int keep = len < TRACK_LEN ? len : TRACK_LEN;          // frames longer than TRACK_LEN compare/print their first TRACK_LEN bytes
  bool same = (t->len == len) && memcmp(t->last, f, keep) == 0;
  memcpy(t->last, f, keep); t->len = len;
  if (same) return;
  if (!changes_only && !fresh) return;
  uint32_t now = millis();
  bool always = fresh || (!p2b && key == 0x02021Au) || key == 0x02040Du || key == 0x02040Eu;
  if (!always && now - t->lastPrint < CHG_MIN_MS) { t->skipped++; return; }
  t->lastPrint = now;
  static char line[48 + TRACK_LEN * 3];
  int o = snprintf(line, sizeof line, "%9lu ms [%s] %s %06lX len %3d", (unsigned long)now, p2b ? "P->A" : "A->P",
                   fresh ? "NEW" : "chg", (unsigned long)key, len);
  if (t->skipped) { o += snprintf(line + o, sizeof line - o, " (+%lu)", (unsigned long)t->skipped); t->skipped = 0; }
  line[o++] = ':';
  static const char hx[] = "0123456789ABCDEF";
  for (int i = 0; i < keep && o < (int)sizeof line - 4; i++) { line[o++] = ' '; line[o++] = hx[f[i] >> 4]; line[o++] = hx[f[i] & 15]; }
  line[o++] = '\n';
  HWCDCSerial.write((const uint8_t *)line, o);
}

// ---------- Frame-gated forwarder ----------
// Forwards only bytes inside a 0x3D frame. DE goes high at the frame's first byte and stays high until the last
// byte (length from the header) has left the UART, so the frame goes out as one continuous driven burst.
// Junk between frames (idle noise) is dropped. A frame that stalls for >FWD_STALL_US is cut off.
#define FWD_STALL_US 600
struct Fwd { uart_port_t out; int de; bool in; int idx, len, lenIdx, lenLo; int64_t lastByte; uint32_t frames, dropped, stalls; };
static Fwd fP = {A_UART, A_DE}, fA = {B_UART, B_DE};   // fP: printer->AMS out Module A; fA: AMS->printer out Module B
static void fwd_end(Fwd &f);
static void fwd_check_stall(Fwd &f);
static void fwd_feed(Fwd &f, const uint8_t *d, int n);
static void fwd_end(Fwd &f) {
  uart_wait_tx_done(f.out, pdMS_TO_TICKS(5));
  gpio_set_level((gpio_num_t)f.de, 0);
  f.in = false;
}
static void fwd_feed(Fwd &f, const uint8_t *d, int n) {
  static uint8_t out[CHUNK];
  int o = 0; bool done = false;
  for (int i = 0; i < n; i++) {
    uint8_t x = d[i];
    if (!f.in) {
      if (x != 0x3D) { f.dropped++; continue; }
      if (o) { uart_write_bytes(f.out, (const char *)out, o); o = 0; }
      gpio_set_level((gpio_num_t)f.de, 1);
      f.in = true; f.idx = 0; f.len = 0; f.lenIdx = 0;
    }
    out[o++] = x;
    // Length: short header = 1 byte at [2]; long header = 16-bit little-endian at [4..5]. (Reading only [4] cut every
    // frame over 255 B, e.g. the 457 B AMS firmware chunks, and dropped the rest: update failed 0x07000314, 2026-11-21.)
    if (f.idx == 1) f.lenIdx = (x & 0x80) ? 2 : 5;
    if (f.idx == 4) f.lenLo = x;
    if (f.lenIdx && f.idx == f.lenIdx) {
      int L = (f.lenIdx == 2) ? x : (f.lenLo | (x << 8));
      f.len = (L < 6) ? 6 : L;
    }
    f.idx++;
    if ((f.len && f.idx >= f.len) || f.idx >= FRAME_MAX) {
      uart_write_bytes(f.out, (const char *)out, o); o = 0;
      fwd_end(f); f.frames++; done = true;
    }
  }
  if (o) uart_write_bytes(f.out, (const char *)out, o);
  if (n > 0) f.lastByte = esp_timer_get_time();
  (void)done;
}
static void fwd_check_stall(Fwd &f) {
  if (f.in && esp_timer_get_time() - f.lastByte > FWD_STALL_US) { fwd_end(f); f.stalls++; }
}

Counts cP = {}, cB = {};        // cP = frames seen printer->board, cB = board->printer
uint32_t auth_seen = 0;
static bool stats_on = true;
static bool a_inv = false;   // 'I' inverts Module A TX+RX in software: polarity test without rewiring

static const char *dirName(bool p2b) { return p2b ? "P->A" : "A->P"; }   // printer->AMS / AMS->printer

// Decode one complete, length-consistent frame; CRC-check; count; alert on auth/unknown/bad.
static void decode(const uint8_t *f, int len, bool p2b) {
  Counts *c = p2b ? &cP : &cB;
  if (len < 4) { c->crc_bad++; return; }
  uint16_t want = crc16_bambu(f, len - 2);
  uint16_t got = f[len - 2] | (f[len - 1] << 8);
  if (want != got) {
    c->crc_bad++;
    HWCDCSerial.printf("[%s] CRC FAIL len=%d  b1=%02X\n", dirName(p2b), len, f[1]);
    return;
  }
  // key: short = 0x01_00ss (subtype), long = 0x02_tttt (type)
  track(f, len, p2b, (f[1] & 0x80) ? (0x010000u | f[4]) : (len >= 13 ? (0x020000u | f[11] | (f[12] << 8)) : 0x03FFFFu));
  if (f[1] & 0x80) {                      // short header
    switch (f[4]) {
      case 0x03: c->motion_s++; break;
      case 0x04: c->motion_l++; break;
      case 0x05: c->online++; break;
      case 0x06: c->req6++; break;
      case 0x07: c->nfc++; break;
      case 0x08: c->setfil++; break;
      case 0x20: c->heartbeat++; break;
      default:
        c->other++;   // first sighting of each type is printed by track() as NEW (no per-frame print: it would stall the bridge)
    }
  } else {                                // long header
    if (len < 13) { c->other++; return; }
    uint16_t type = f[11] | (f[12] << 8);
    uint16_t targ = f[7] | (f[8] << 8);
    switch (type) {
      case 0x21A: c->mc_online++; break;
      case 0x211: c->filament++; break;
      case 0x103:
      case 0x402: c->version++; break;
      case 0x040D:
      case 0x040E:
        auth_seen++;
        HWCDCSerial.printf(">>> [%s] AUTH FRAME type 0x%04X target 0x%04X len=%d  (0x040D=cert, 0x040E=challenge)\n",
                           dirName(p2b), type, targ, len);
        break;
      default:
        c->other++;   // see track(): NEW line on first sighting
    }
  }
}

// Feed stream bytes into the assembler; emit complete frames to decode().
static void feed(Asm &a, const uint8_t *d, int n, bool p2b) {
  for (int i = 0; i < n; i++) {
    uint8_t x = d[i];
    if (a.idx == 0) {
      if (x == 0x3D) { a.buf[0] = x; a.idx = 1; a.len = 0; a.lenIdx = 0; }
      continue;
    }
    if (a.idx < FRAME_MAX) a.buf[a.idx] = x;
    if (a.idx == 1) a.lenIdx = (x & 0x80) ? 2 : 5;
    if (a.lenIdx && a.idx == a.lenIdx)                    // total frame length (incl CRC16): short [2], long [4..5] LE
      a.len = (a.lenIdx == 2) ? x : (a.buf[4] | (x << 8));
    if (a.len > FRAME_MAX) a.idx = FRAME_MAX;             // longer than we can hold: resync below
    a.idx++;
    if (a.len && a.idx >= a.len) { decode(a.buf, a.len, p2b); a.idx = 0; }
    if (a.idx >= FRAME_MAX) a.idx = 0;                    // resync on runaway
  }
}

static void help() {
  HWCDCSerial.println();
  HWCDCSerial.println("interposer rung 2 - bridge + parse");
  HWCDCSerial.printf("  baud %d 8E1 | B(printer) tx%d rx%d de%d | A(AMS) tx%d rx%d de%d\n",
                     BUS_BAUD, B_TX, B_RX, B_DE, A_TX, A_RX, A_DE);
  HWCDCSerial.println("  z zero  ? help  c changes-only on/off  s stats on/off  m mark (both boards via D7 + LED)  I invert AMS side");
  HWCDCSerial.println("  (auth/CRC-fail print immediately)");
}

static void bus_uart_init(uart_port_t u, int tx, int rx, int de) {
  uart_config_t c = {};
  c.baud_rate = BUS_BAUD; c.data_bits = UART_DATA_8_BITS; c.parity = UART_PARITY_EVEN;
  c.stop_bits = UART_STOP_BITS_1; c.flow_ctrl = UART_HW_FLOWCTRL_DISABLE; c.source_clk = UART_SCLK_DEFAULT;
  uart_driver_install(u, RX_BUF, 0, 0, NULL, 0);
  uart_param_config(u, &c);
  uart_set_pin(u, tx, rx, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
  uart_set_mode(u, UART_MODE_UART);
  // DE is driven by the forwarder (fwd_*), not by the IDF RS485 mode: IDF drops DE whenever the TX FIFO runs
  // empty, which with chunked cut-through forwarding left mid-frame gaps where the bus was undriven
  // (MAX3485 is not fail-safe) and the AMS saw corrupt frames. Bridge attempt 1-3, 2026-11-20.
  gpio_reset_pin((gpio_num_t)de); gpio_set_direction((gpio_num_t)de, GPIO_MODE_OUTPUT); gpio_set_level((gpio_num_t)de, 0);
  // Low latency: move bytes from the FIFO every 8 bytes or after ~1 idle symbol, so we forward within ~70 us
  // instead of holding the whole frame (IDF default is 120 bytes / 10 symbols = store-and-forward).
  uart_set_rx_full_threshold(u, 8);
  uart_set_rx_timeout(u, 1);
}

void setup() {
  // Never let USB logging block the bridge. With no host reading, HWCDC writes used to wait up to
  // 100 ms each, the RX FIFOs overflowed and the AMS dropped off the printer (2026-11-21). Now a
  // full TX buffer just drops log text.
  HWCDCSerial.setTxBufferSize(4096);
  HWCDCSerial.setTxTimeoutMs(0);
  HWCDCSerial.begin(115200);
  delay(300);
  pinMode(LED_PIN, OUTPUT); digitalWrite(LED_PIN, HIGH);  // LED off (active LOW)
  pinMode(SYNC_PIN, INPUT_PULLUP);                        // marker line idle HIGH
#if BUF_TAP
  pinMode(BUF_P5, INPUT); pinMode(BUF_P6, INPUT); attachInterrupt(BUF_P5, p5_isr, CHANGE);
#endif
  bus_uart_init(B_UART, B_TX, B_RX, B_DE);
  bus_uart_init(A_UART, A_TX, A_RX, A_DE);
  help();
}

void loop() {
  static uint8_t buf[CHUNK];
  static Asm aP, aB;
  static uint32_t last = 0;

  int n = uart_read_bytes(B_UART, buf, sizeof(buf), 0);
  if (n > 0) { fwd_feed(fP, buf, n); feed(aP, buf, n, true); }
  fwd_check_stall(fP);

  n = uart_read_bytes(A_UART, buf, sizeof(buf), 0);
  if (n > 0) { fwd_feed(fA, buf, n); feed(aB, buf, n, false); }
  fwd_check_stall(fA);

  if (HWCDCSerial.available()) {
    char ch = HWCDCSerial.read();
    if (ch == '?') help();
    else if (ch == 'I') {
      a_inv = !a_inv;
      uart_set_line_inverse(A_UART, a_inv ? (UART_SIGNAL_TXD_INV | UART_SIGNAL_RXD_INV) : UART_SIGNAL_INV_DISABLE);
      uart_flush_input(A_UART);
      HWCDCSerial.printf("Module A (AMS side) invert %s\n", a_inv ? "ON" : "off");
    }
    else if (ch == 'c') { changes_only = !changes_only; HWCDCSerial.printf("changes-only %s\n", changes_only ? "ON" : "off"); }
    else if (ch == 'n') { HWCDCSerial.println("(no muting in this build)"); }
    else if (ch == 's') { stats_on = !stats_on; HWCDCSerial.printf("stats %s\n", stats_on ? "on" : "off"); }
    else if (ch == 'm') { do_mark("key"); sync_send(); }   // mark locally + pulse D7 so the other board marks too
    else if (ch == 'z') { cP = {}; cB = {}; auth_seen = 0; HWCDCSerial.println("zeroed"); }
  }

  // Sync marker: when the OTHER board pulses D7 low, stamp a MARK here too (the sender marked via 'm').
  static int lastSync = 1;
  int sy = digitalRead(SYNC_PIN);
  if (lastSync && !sy) do_mark("sync");
  lastSync = sy;
  if (ledOffAt && millis() >= ledOffAt) { digitalWrite(LED_PIN, HIGH); ledOffAt = 0; }  // LED auto-off (active LOW)

  uint32_t now = millis();
#if BUF_TAP
  // Buffer tap: pin 5 duty cycle, averaged over 20 ms windows (~200 PWM periods).
  static uint32_t bufT = 0, bufPrintT = 0; static float duty = -1, perUs = 0; static float dutyLast = -100;
  if (now - bufT >= 20) {
    bufT = now;
    noInterrupts(); uint32_t hs = p5_hi_sum, ps = p5_per_sum, n = p5_n; p5_hi_sum = p5_per_sum = p5_n = 0; interrupts();
    if (n) { duty = 100.0f * hs / ps; perUs = (float)ps / n; } else duty = -1;
    if (duty >= 0 && now - bufPrintT >= BUF_MIN_MS && fabsf(duty - dutyLast) >= BUF_STEP_PCT) {
      bufPrintT = now; dutyLast = duty;
      HWCDCSerial.printf("%9lu ms BUF pin5 duty %5.1f %%  period %5.1f us\n", (unsigned long)now, duty, perUs);
    }
  }
#endif
  if (stats_on && now - last >= 1000) {
    last = now;
#if BUF_TAP
    if (duty < 0) HWCDCSerial.println("BUF  no PWM on pin 5");
    else HWCDCSerial.printf("BUF  pin5 duty %.1f %% period %.1f us\n", duty, perUs);
#endif
    HWCDCSerial.printf("P->A  motS%lu motL%lu onl%lu req6%lu nfc%lu set%lu hb%lu | mc%lu fil%lu ver%lu oth%lu crcX%lu\n",
      (unsigned long)cP.motion_s,(unsigned long)cP.motion_l,(unsigned long)cP.online,(unsigned long)cP.req6,
      (unsigned long)cP.nfc,(unsigned long)cP.setfil,(unsigned long)cP.heartbeat,(unsigned long)cP.mc_online,
      (unsigned long)cP.filament,(unsigned long)cP.version,(unsigned long)cP.other,(unsigned long)cP.crc_bad);
    HWCDCSerial.printf("A->P  motS%lu motL%lu onl%lu req6%lu nfc%lu set%lu hb%lu | mc%lu fil%lu ver%lu oth%lu crcX%lu | AUTH %lu\n",
      (unsigned long)cB.motion_s,(unsigned long)cB.motion_l,(unsigned long)cB.online,(unsigned long)cB.req6,
      (unsigned long)cB.nfc,(unsigned long)cB.setfil,(unsigned long)cB.heartbeat,(unsigned long)cB.mc_online,
      (unsigned long)cB.filament,(unsigned long)cB.version,(unsigned long)cB.other,(unsigned long)cB.crc_bad,
      (unsigned long)auth_seen);
    HWCDCSerial.printf("FWD  P->A frames %lu dropped %lu stalls %lu | A->P frames %lu dropped %lu stalls %lu\n",
      (unsigned long)fP.frames, (unsigned long)fP.dropped, (unsigned long)fP.stalls,
      (unsigned long)fA.frames, (unsigned long)fA.dropped, (unsigned long)fA.stalls);
  }
}
