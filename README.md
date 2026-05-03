# 🌌 Vantage-OS: Autonomous Browser Co-Pilot

> **🏆 Achievement: Ranked Top 5 (3rd Place Equivalent) out of 44 competing engineering projects in the Orbit Hyperthon.**

**Vantage-OS** is a high-speed, dual-tier autonomous agent designed to navigate the web with human-like precision. It features a persistent in-browser "Command Center" and a failover intelligence stack that ensures 100% mission uptime.

---

## 🚀 Key Features

* **Dual-Tier Intelligence**: Separates high-level **Planning** from tactical **Acting** to minimize latency and token costs.
* **All-Flash Failover**: A recursive stack (`Gemini 3.1 Flash-Lite`, `2.5 Flash`, `1.5 Flash`) with a **<1s recovery time** for API rate limits.
* **Persistent Co-Pilot UI**: A neon-cyan glassmorphism sidebar injected directly into the browser that stays active across page navigations.
* **Stealth Execution**: Implements **Bezier Curve** mouse trajectories and randomized **±5px click jitter** to bypass bot detection.
* **Fast-Path "Skip Ad"**: A background DOM-listener that identifies and clicks "Skip Ad" buttons instantly, bypassing the AI loop.

---

## 🛠 Tech Stack

| Component | Technology |
| :--- | :--- |
| **Brain** | Google Gemini SDK (Flash 2.5 / 3.1 / 1.5) |
| **Body** | Playwright (Python) |
| **UI/UX** | JavaScript (Injected Sidebar) + CSS Glassmorphism |
| **Stealth** | Custom Bezier Trajectory Math |
| **Runtime** | Python 3.14 |

---

## 📦 Installation & Setup

1. **Clone the Repo**
   
```bash
   git clone [https://github.com/AbradolfLincler-c137/Vantage-OS.git](https://github.com/AbradolfLincler-c137/Vantage-OS.git)
   cd Vantage-OS
