#!/usr/bin/env python3
"""
CAEN DT5533E Monitor & Control → InfluxDB (v1)

This script connects to a CAEN DT5533E (EN series) high-voltage power supply
via TCP/Telnet (port 23). It continuously monitors key parameters and writes
them to InfluxDB v1.x, and also provides an interactive REPL (shell) to
control the supply safely.

Metrics written to InfluxDB:
- VSET (Voltage Setpoint, float)
- VMON (Voltage Monitor, float)
- ISET (Current Limit, float)
- IMON (Current Monitor, float)
- TRIP (Overcurrent Trip time threshold, float, in seconds)
- STAT (Status word, int bitmask)
- IS_ON, IS_UP, IS_DOWN, IS_OVC, IS_OVV, IS_UNV, IS_MAXV, IS_TRIP,
  IS_MAXPW, IS_TWARN, IS_OVT, IS_KILL, IS_INTLCK (bit flags decoded from STAT)

Commands supported in the shell:
- start / stop monitoring (to InfluxDB)
- set vset <V>, set iset <I>
- on, off, pdwn kill
- raw <payload> (send arbitrary CAEN command)
- chan <N> (select channel)
- period <sec> (set monitor period)
- quit
"""

import asyncio, shlex, time
from datetime import datetime
from typing import Optional, Dict, Any

# InfluxDB v1 client library
try:
    from influxdb import InfluxDBClient
except Exception:
    InfluxDBClient = None

PROMPT = "dt5533e> "

# ---- Status Word Bitmask ----
# These bits are defined in the CAEN manual. STAT is a 16-bit word.
STAT_BITS = {
    "IS_ON":     0x0001,  # Channel On
    "IS_UP":     0x0002,  # Ramping Up
    "IS_DOWN":   0x0004,  # Ramping Down
    "IS_OVC":    0x0008,  # Overcurrent
    "IS_OVV":    0x0010,  # Overvoltage
    "IS_UNV":    0x0020,  # Undervoltage
    "IS_MAXV":   0x0040,  # Max Voltage reached
    "IS_TRIP":   0x0080,  # Tripped
    "IS_MAXPW":  0x0100,  # Max Power
    "IS_TWARN":  0x0200,  # Temp warning > 80C
    "IS_OVT":    0x0400,  # Over Temp > 125C
    "IS_KILL":   0x0800,  # Channel Killed
    "IS_INTLCK": 0x1000,  # Interlock
}

# ------------------------------
# Utility Functions
# ------------------------------

def build_cmd(op: str, ch: int, par: str, val: Optional[str] = None) -> str:
    """
    Build a CAEN command string.
    Example: $CMD:MON,CH:0,PAR:VMON\r\n
    """
    parts = [f"$CMD:{op}", f"CH:{ch}", f"PAR:{par}"]
    if val is not None:
        parts.append(f"VAL:{val}")
    return ",".join(parts) + "\r\n"

def parse_reply(line: str) -> Dict[str, Any]:
    """
    Parse a CAEN reply string into a dict.
    Replies are usually:
      #CMD:OK,VAL:123.45;
      #ERR:<code>;
    """
    s = line.strip()
    out: Dict[str, Any] = {"raw": s}
    if s.startswith("#CMD:OK"):
        out["ok"] = True
        # Try to extract VAL
        for t in s.split(","):
            if "VAL:" in t:
                out["val"] = t.split("VAL:", 1)[1].rstrip(";")
                break
    elif s.startswith("#ERR"):
        out["ok"] = False
        out["error"] = s
    else:
        out["ok"] = False
        out["error"] = f"unrecognized_reply:{s}"
    return out

def to_float_maybe(v: Optional[str]) -> Optional[float]:
    """Convert string to float, or None if invalid."""
    try:
        return float(v) if v is not None else None
    except Exception:
        return None

def parse_int_maybe(v: Optional[str]) -> Optional[int]:
    """Convert string to int (decimal or hex), or None if invalid."""
    if v is None:
        return None
    vv = v.strip().lower()
    try:
        return int(vv, 16) if vv.startswith("0x") else int(vv)
    except Exception:
        return None

def decode_stat_fields(stat_val: int) -> Dict[str, int]:
    """Decode STAT bitmask into {flag:0/1} dictionary."""
    return {name: 1 if (stat_val & bit) else 0 for name, bit in STAT_BITS.items()}

# ------------------------------
# CAEN Client
# ------------------------------

class CaenClient:
    """
    Async TCP client for CAEN EN-series HV supplies.
    Maintains one persistent connection to the module.
    """

    def __init__(self, host: str, port: int = 23, timeout: float = 3.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._lock = asyncio.Lock()

    async def connect(self):
        """Open connection if not already open."""
        if self._reader and self._writer:
            return
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout
        )

    async def close(self):
        """Close connection."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def _send(self, payload: str) -> str:
        """Send command and read one line of reply."""
        if not self._writer:
            await self.connect()
        self._writer.write(payload.encode("ascii"))
        await self._writer.drain()
        line = await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
        return line.decode(errors="replace")

    async def request(self, payload: str) -> Dict[str, Any]:
        """
        Send a raw payload and return parsed reply.
        Automatically retries once if connection times out.
        """
        async with self._lock:
            try:
                return parse_reply(await self._send(payload))
            except (asyncio.TimeoutError, ConnectionError):
                await self.close()
                await asyncio.sleep(0.2)
                await self.connect()
                return parse_reply(await self._send(payload))

    # Convenience wrappers for MON commands
    async def mon_vset(self, ch: int): return await self.request(build_cmd("MON", ch, "VSET"))
    async def mon_vmon(self, ch: int): return await self.request(build_cmd("MON", ch, "VMON"))
    async def mon_iset(self, ch: int): return await self.request(build_cmd("MON", ch, "ISET"))
    async def mon_imon(self, ch: int): return await self.request(build_cmd("MON", ch, "IMON"))
    async def mon_stat(self, ch: int): return await self.request(build_cmd("MON", ch, "STAT"))
    async def mon_trip(self, ch: int): return await self.request(build_cmd("MON", ch, "TRIP"))

    # Convenience wrappers for SET commands
    async def set_vset(self, ch: int, value: str): return await self.request(build_cmd("SET", ch, "VSET", value))
    async def set_iset(self, ch: int, value: str): return await self.request(build_cmd("SET", ch, "ISET", value))
    async def set_pdwn_kill(self, ch: int):       return await self.request(build_cmd("SET", ch, "PDWN", "KILL"))

    async def set_on(self, ch: int):
        """Turn channel ON (tries PAR:ON, falls back to PW=ON)."""
        res = await self.request(build_cmd("SET", ch, "ON"))
        return res if res.get("ok") else await self.request(build_cmd("SET", ch, "PW", "ON"))

    async def set_off(self, ch: int):
        """Turn channel OFF (tries PAR:OFF, falls back to PW=OFF)."""
        res = await self.request(build_cmd("SET", ch, "OFF"))
        return res if res.get("ok") else await self.request(build_cmd("SET", ch, "PW", "OFF"))

# ------------------------------
# InfluxDB Writer
# ------------------------------

class InfluxSink:
    """Thin wrapper for InfluxDB v1 client."""
    def __init__(self, host: str, port: int, db: str, measurement: str, device_tag: str):
        if InfluxDBClient is None:
            raise RuntimeError("influxdb client not installed. pip install influxdb")
        self.client = InfluxDBClient(host, port)
        self.measurement, self.device = measurement, device_tag
        try: self.client.create_database(db)
        except Exception: pass
        self.client.switch_database(db)

    def write_fields(self, channel: int, fields: Dict[str, Any], ts: Optional[datetime] = None):
        """Write one point with multiple fields."""
        payload = [{
            "measurement": self.measurement,
            "tags": {"device": self.device, "channel": str(channel)},
            "time": ts or datetime.utcnow(),
            "fields": fields
        }]
        self.client.write_points(payload)

# ------------------------------
# Interactive Shell
# ------------------------------

class Shell:
    """
    Interactive REPL:
    - background monitor writes to Influx
    - user can issue control commands
    - minimal feedback printed ([ok]/[err] + raw reply)
    """

    def __init__(self, client: CaenClient, influx: InfluxSink, channel: int, period: float):
        self.client, self.influx = client, influx
        self.channel, self.period = channel, period
        self._task: Optional[asyncio.Task] = None

    async def monitor_once(self, ch: int) -> Dict[str, Any]:
        """
        Read all desired parameters once and return as dict of fields.
        """
        f: Dict[str, Any] = {}
        rv = await self.client.mon_vset(ch);  f["VSET"] = to_float_maybe(rv.get("val")) if rv.get("ok") else None
        rv = await self.client.mon_vmon(ch);  f["VMON"] = to_float_maybe(rv.get("val")) if rv.get("ok") else None
        rv = await self.client.mon_iset(ch);  f["ISET"] = to_float_maybe(rv.get("val")) if rv.get("ok") else None
        rv = await self.client.mon_imon(ch);  f["IMON"] = to_float_maybe(rv.get("val")) if rv.get("ok") else None
        rv = await self.client.mon_stat(ch)
        if rv.get("ok"):
            raw = parse_int_maybe(rv.get("val"))
            if raw is not None:
                f["STAT"] = raw
                f.update(decode_stat_fields(raw))
        rv = await self.client.mon_trip(ch);  f["TRIP"] = to_float_maybe(rv.get("val")) if rv.get("ok") else None
        return f

    async def loop(self):
        """Continuous monitor loop: reads fields, writes to Influx every period."""
        try:
            while True:
                t0 = time.time()
                fields = await self.monitor_once(self.channel)
                self.influx.write_fields(self.channel, fields, datetime.utcnow())
                await asyncio.sleep(max(0.0, self.period - (time.time() - t0)))
        except asyncio.CancelledError:
            pass

    async def start(self):
        """Start background monitor."""
        if self._task and not self._task.done(): print("[ok] monitor already running"); return
        self._task = asyncio.create_task(self.loop())
        print(f"[ok] monitor started ch={self.channel} period={self.period}s")

    async def stop(self):
        """Stop background monitor."""
        if self._task and not self._task.done():
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
            print("[ok] monitor stopped")
        else:
            print("[ok] monitor not running")

    async def handle(self, line: str) -> bool:
        """
        Parse and execute one line of user input.
        Returns False if user wants to quit.
        """
        try: parts = shlex.split(line)
        except ValueError: print("[err] parse"); return True
        if not parts: return True
        cmd = parts[0].lower()

        if cmd in ("quit","exit",":q"): return False
        if cmd == "help":
            print("commands:\n"
                  "  start | stop\n"
                  "  period <sec>\n"
                  "  chan <N>\n"
                  "  set vset <V>\n"
                  "  set iset <I>\n"
                  "  on | off\n"
                  "  pdwn kill\n"
                  "  raw <payload>\n"
                  "  help | quit")
            return True
        if cmd == "start": await self.start(); return True
        if cmd == "stop":  await self.stop();  return True
        if cmd == "period" and len(parts)==2:
            try: self.period = float(parts[1]); print(f"[ok] period={self.period}s")
            except ValueError: print("[err] period must be a number")
            return True
        if cmd == "chan" and len(parts)==2:
            try: self.channel = int(parts[1]); print(f"[ok] channel={self.channel}")
            except ValueError: print("[err] channel must be an integer")
            return True
        if cmd == "set" and len(parts)>=3:
            what, val = parts[1].lower(), " ".join(parts[2:])
            if   what=="vset": res = await self.client.set_vset(self.channel, val)
            elif what=="iset": res = await self.client.set_iset(self.channel, val)
            else: print("[err] unknown set param (vset|iset)"); return True
            print(("[ok] "+res.get("raw","")) if res.get("ok") else f"[err] {res.get('error','fail')} {res.get('raw','')}")
            return True
        if cmd == "on":
            res = await self.client.set_on(self.channel)
            print(("[ok] "+res.get("raw","")) if res.get("ok") else f"[err] {res.get('error','fail')} {res.get('raw','')}")
            return True
        if cmd == "off":
            res = await self.client.set_off(self.channel)
            print(("[ok] "+res.get("raw","")) if res.get("ok") else f"[err] {res.get('error','fail')} {res.get('raw','')}")
            return True
        if cmd == "pdwn" and len(parts)==2 and parts[1].lower()=="kill":
            res = await self.client.set_pdwn_kill(self.channel)
            print(("[ok] "+res.get("raw","")) if res.get("ok") else f"[err] {res.get('error','fail')} {res.get('raw','')}")
            return True
        if cmd == "raw" and len(parts)>=2:
            payload = " ".join(parts[1:])
            if not payload.endswith("\r\n"): payload += "\r\n"
            res = await self.client.request(payload)
            print(("[ok] "+res.get("raw","")) if res.get("ok") else f"[err] {res.get('error','fail')} {res.get('raw','')}")
            return True

        print("[err] unknown command (help)"); return True

    async def repl(self):
        """Run REPL until user quits."""
        print("[info] type 'start' to begin monitoring; 'help' for commands")
        loop = asyncio.get_running_loop()
        while True:
            print(PROMPT, end="", flush=True)
            line = await loop.run_in_executor(None, input)
            if not await self.handle(line): break

# ------------------------------
# Main entrypoint
# ------------------------------

async def main():
    import argparse
    ap = argparse.ArgumentParser(description="DT5533E monitor → InfluxDB (v1), with interactive control")
    # CAEN
    ap.add_argument("--host", required=True, help="CAEN IP/host (e.g. 192.168.197.102)")
    ap.add_argument("--port", type=int, default=23)
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("-c","--channel", type=int, default=0)
    ap.add_argument("--period", type=float, default=1.0)
    # Influx v1
    ap.add_argument("--influx-host", required=True, help="Influx host (e.g. 192.168.197.46)")
    ap.add_argument("--influx-port", type=int, default=8086)
    ap.add_argument("--influx-db",   required=True, help="Database name (e.g. annie)")
    ap.add_argument("--measurement", default="DT5533E")
    ap.add_argument("--device-tag",  default="DT5533E")
    args = ap.parse_args()

    client = CaenClient(args.host, args.port, args.timeout); await client.connect()
    influx = InfluxSink(args.influx_host, args.influx_port, args.influx_db, args.measurement, args.device_tag)
    shell  = Shell(client, influx, channel=args.channel, period=args.period)
    try:
        await shell.repl()
    finally:
        await shell.stop(); await client.close()

if __name__ == "__main__":
    asyncio.run(main())
