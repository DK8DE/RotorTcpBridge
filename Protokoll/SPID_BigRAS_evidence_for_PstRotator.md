# SPID / ROT2PROG status reply — angles > 360° (Big RAS)

**To:** PstRotator developer  
**From:** RotorTcpBridge (DK8DE)  
**Date:** 2026-08-05  
**Issue:** PstRotator UI shows e.g. **2°** while the rotor/bridge reports **362°** over SPID serial (com0com). Bridge side is correct; PstRotator appears to display `(angle − 360)` or `angle mod 360`.

---

## Protocol (ROT2PROG / SPID status reply)

- Frame length: **12 bytes**
- Layout: `'W'` + **4 digit bytes** (AZ) + `PH` + **4 digit bytes** (EL) + `PV` + `' '`
- Digit bytes are **binary 0…9**, **not** ASCII `'0'`…`'9'`
- With `PH = 10` (0.1°):

```text
H  = PH * (az_deg + 360)
az = H / PH − 360
```

Example for **362.0°**:

```text
H = 10 * (362 + 360) = 7220
bytes AZ digits = 07 02 02 00   (not ASCII 37 32 32 30)
```

---

## Evidence from live log (COM12 → PstRotator)

Captured status replies (`cmd` STATUS poll answers) while the rotor was **past 360°**.  
Each line is a real TX hex dump from RotorTcpBridge.

| Rotor AZ sent | H | PH | Hex (12 bytes) | Pst shows if mod 360 / −360 |
|---:|---:|---:|---|---:|
| **360.1°** | 7201 | 10 | `57 07 02 00 01 0A 03 06 00 00 0A 20` | **0.1°** |
| **360.5°** | 7205 | 10 | `57 07 02 00 05 0A 03 06 00 00 0A 20` | **0.5°** |
| **361.0°** | 7210 | 10 | `57 07 02 01 00 0A 03 06 00 00 0A 20` | **1.0°** |
| **361.5°** | 7215 | 10 | `57 07 02 01 05 0A 03 06 00 00 0A 20` | **1.5°** |
| **362.0°** | 7220 | 10 | `57 07 02 02 00 0A 03 06 00 00 0A 20` | **2.0°** ← matches user report |
| **362.4°** | 7224 | 10 | `57 07 02 02 04 0A 03 06 00 00 0A 20` | **2.4°** |
| **363.0°** | 7230 | 10 | `57 07 02 03 00 0A 03 06 00 00 0A 20` | **3.0°** |
| **365.0°** | 7250 | 10 | `57 07 02 05 00 0A 03 06 00 00 0A 20` | **5.0°** |
| **370.4°** | 7304 | 10 | `57 07 03 00 04 0A 03 06 00 00 0A 20` | **10.4°** |
| **379.9°** | 7399 | 10 | `57 07 03 09 09 0A 03 06 00 00 0A 20` | **19.9°** |

Log timestamps (examples): `2026-08-05 22:36:50` (362.0°), `2026-08-05 22:37:22` (362.4°).  
In one session alone: **>2000** status replies with `az > 360°` (unique values about **360.1° … 379.9°**).

### Decode check for 362°

```text
hex: 57 07 02 02 00 0A 03 06 00 00 0A 20
     W  7  2  2  0 PH=10  … EL … PV  space

H = 7220
az = 7220/10 − 360 = 362.0°   ← correct SPID encoding
```

If PstRotator displays **2°**, it is effectively showing `362 − 360` (or `362 mod 360`), **not** the value encoded in `H`.

---

## What we ask PstRotator to verify

With **Big RAS / 720°** enabled on the SPID serial rotor:

1. When status reply has `H = 7220`, `PH = 10`, display/use **362°**, not **2°**.
2. Please confirm the status-reply decoder uses `az = H/PH − 360` with binary digit bytes, and does **not** wrap the result into 0…360 for Big-RAS rotors.
3. Same for other overlap values in the table (370° must not become 10°).

Bridge settings used while capturing: report full rotor angle (not forced 0…360 wrap) on the SPID serial path.

---

## Contact / product

- Application: **RotorTcpBridge** (SPID ROT2PROG slave over serial / com0com)
- Author: Jörg Körner DK8DE
