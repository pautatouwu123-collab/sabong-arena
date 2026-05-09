# Sabong Betting System

Complete cockfight betting system for Linux.

## Requirements

- Python 3.8+
- Linux server
- SQLite3

## Installation

```bash
# Clone or download
git clone https://github.com/pautatouwu123-collab/sabong-arena.git
cd sabong-arena

# Install dependencies (optional: use virtualenv)
python3 -m venv venv
source venv/bin/activate

# Or just install
pip install -r requirements.txt
```

## Running

```bash
# Start server (default port 5000)
python sabong_system.py

# Or production
gunicorn -w 4 -b 0.0.0.0:5000 sabong_system:app
```

## Access

| Interface | URL |
|-----------|-----|
| Admin Panel | http://localhost:5000/admin.html |
| Terminal | http://localhost:5000/terminal.html |
| TV Display | http://localhost:5000/tv_display.html |
| Sales Report | http://localhost:5000/sales_report.html |

## Default Login

```
Username: admin
Password: admin123
PIN: 1234
```

## Fight Status Flow

```
upcoming → open → last_call → closed → fight → finished
                    ↘ cancelled
```

## Features

- Pool-based odds (auto-computed)
- Bayong jackpot system
- Anti-cheat protection
- Operator credit management
- Sales reports
- Thermal printer support (Sunmi V2 Pro)
- Barcode scanner

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|------------|
| POST | /api/auth/login | Login |
| GET | /api/fights | List fights |
| POST | /api/fights | Create fight |
| PUT | /api/fights/:id/status | Update status |
| POST | /api/fights/:id/settle | Declare winner |
| POST | /api/fights/:id/bets | Place bet |
| GET | /api/reports/daily | Sales report |
| POST | /api/config | Update config |

## Configuration

Edit via Admin Panel or API:
- Commission rate (%)
- Draw odds
- Jackpot increment
- Arena name
- Betting on/off