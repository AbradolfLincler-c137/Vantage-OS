# 🌌 Vantage-OS: Autonomous Browser Co-Pilot

🏆 **Top 5 Engineering Project (3rd Place Equivalent)**

Vantage-OS is a high-speed, dual-tier autonomous agent designed to navigate the web with human-like precision. Built for the **Orbit Hyperthon**, it features a persistent in-browser "Command Center" and a failover intelligence stack that ensures 100% mission uptime by utilizing a recursive Google Gemini model hierarchy.

## ✨ Features
- **Dual-Tier Intelligence**: Separates high-level planning from tactical execution to minimize latency and token costs.
- **All-Flash Failover**: Implements a recursive stack (Gemini 3.1 Flash-Lite → 2.5 Flash → 1.5 Flash) with <1s recovery time.
- **Persistent Co-Pilot UI**: A neon-cyan glassmorphism sidebar injected directly into the browser for real-time status monitoring.
- **Stealth Execution**: Utilizes custom Bezier Curve trajectories and randomized click jitter to bypass advanced bot detection.
- **Fast-Path "Skip Ad"**: A background DOM-listener that identifies and interacts with media controls instantly, bypassing the main AI loop.

## 🚀 Technologies Used
- **Python 3.14**
- **Google Gemini SDK** (Flash 3.1, 2.5, 1.5) for multi-model intelligence logic.
- **Playwright** for high-fidelity browser automation and stealth interaction.
- **JavaScript & CSS** for the injected glassmorphism Sidebar UI.
- **Custom Bezier Math** for human-like cursor movement simulation.

## 🛠️ Installation

1. **Clone the repository**:
   ```bash
   git clone [https://github.com/AbradolfLincler-c137/Vantage-OS.git](https://github.com/AbradolfLincler-c137/Vantage-OS.git)
   cd Vantage-OS

   Set up a virtual environment (recommended):
2. **Set up a virtual environment**:
```bash
python -m venv venv

On Windows use:
venv\Scripts\activate
On macOS/Linux use:
source venv/bin/activate
```
3. **Install the required dependencies**:
```bash
pip install -r requirements.txt
playwright install
```
## 🏆 Orbit Hyperthon Achievement
This project was developed under intense competition, securing a Top 5 finish (3rd place equivalent) out of 44 projects for its robust failover architecture and innovative stealth execution logic.
