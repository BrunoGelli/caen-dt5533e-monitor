# CAEN DT5533E Monitor & Control  

Python tool to **monitor and control a CAEN DT5533E high-voltage power supply** over TCP (Telnet) and push live data to **InfluxDB (v1)** for visualization in **Grafana**.  

It’s designed for easy lab operation: continuous monitoring in the background, safe interactive commands in the foreground, and a clean database schema for dashboarding.

---

## ✨ Features
- 📡 **Live monitoring** of key metrics:
  - `VSET`, `VMON` (setpoint & measured voltage)  
  - `ISET`, `IMON` (current limit & measured current)  
  - `TRIP` (over-current trip time, seconds)  
  - `STAT` (status word, bitmask) + decoded flags:
    `IS_ON`, `IS_UP`, `IS_DOWN`, `IS_OVC`, `IS_OVV`, `IS_UNV`,  
    `IS_MAXV`, `IS_TRIP`, `IS_MAXPW`, `IS_TWARN`, `IS_OVT`, `IS_KILL`, `IS_INTLCK`
- 🗂 **Writes directly to InfluxDB** (measurement `DT5533E`, customizable)
- 🖥 **Interactive shell** for safe operations:
  - Start/stop monitoring
  - Set `VSET` and `ISET`
  - Channel ON / OFF
  - Power-down kill
  - Send raw CAEN commands
- 🛡 **Bitmask decoding** for easy alarm monitoring in Grafana
- 🧰 Simple, dependency-light, async Python (just `asyncio` + `influxdb`)

---

## 📦 Installation
Clone this repo and install dependencies:
```bash
git clone https://github.com/BrunoGelli/caen-dt5533e-monitor.git
cd caen-dt5533e-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Dependencies:
- Python 3.8+
- [influxdb](https://pypi.org/project/influxdb/) (v1 client library)

---

## 🚀 Usage
Run the shell, pointing it to your CAEN crate and InfluxDB host:

```bash
python3 dt5533e_shell.py \
  --host XXX.XXX.XXX.XXX --port 23 --channel 0 --period 1.0 \
  --influx-host XXX.XXX.XXX.XXX --influx-port 8086 --influx-db <dbName> \
  --measurement DT5533E --device-tag DT5533E
```

Once inside, you get a REPL:

```
[info] type 'start' to begin monitoring; 'help' for commands
dt5533e> start
[ok] monitor started ch=0 period=1.0s
dt5533e> set vset 500
[ok] #CMD:OK,VAL:500.0;
dt5533e> on
[ok] #CMD:OK,VAL:0010.0;
dt5533e> off
[ok] #CMD:OK,VAL:0000.0;
dt5533e> stop
[ok] monitor stopped
dt5533e> quit
```

---

## 📊 InfluxDB Schema
- **Measurement**: `DT5533E` (configurable via `--measurement`)
- **Tags**: `device`, `channel`
- **Fields**:
  - `VSET`, `VMON`, `ISET`, `IMON`, `TRIP`, `STAT`  
  - Boolean flags: `IS_ON`, `IS_UP`, `IS_DOWN`, `IS_OVC`, `IS_OVV`, `IS_UNV`,  
    `IS_MAXV`, `IS_TRIP`, `IS_MAXPW`, `IS_TWARN`, `IS_OVT`, `IS_KILL`, `IS_INTLCK`

Each loop writes one point with all fields.

---

## ⚡ Safety Notes
- This script **does not enforce limits** — be careful when setting `VSET` or `ISET`.  

---

## 🛠  Development
- Contributions welcome (pull requests, issues).
- Code is fully async (`asyncio`) and tested on Python 3.10+.
- Planned extensions:
  - InfluxDB v2 client
  - Prebuilt Grafana dashboard JSON

---

## 📜 License
MIT — free to use, modify, share.

## Disclaimer 
README wrote with ChatGPT
