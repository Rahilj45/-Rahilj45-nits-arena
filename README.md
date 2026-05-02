# NITS Arena 🏆

> **Production-grade Discord bot for competitive-programming "Lockout" 1v1 matches with Codeforces integration.**

[![CI/CD](https://github.com/Rahilj45/-Rahilj45-nits-arena/actions/workflows/main.yml/badge.svg)](https://github.com/Rahilj45/-Rahilj45-nits-arena/actions/workflows/main.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![PostgreSQL](https://img.shields.io/badge/database-PostgreSQL-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)

---

## Table of Contents

1. [Features](#features)
2. [Architecture Overview](#architecture-overview)
3. [Directory Structure](#directory-structure)
4. [Database Schema](#database-schema)
5. [Race Condition Prevention](#race-condition-prevention)
6. [Elo Rating System](#elo-rating-system)
7. [Getting Started](#getting-started)
8. [Commands Reference](#commands-reference)
9. [Running Tests](#running-tests)
10. [CI/CD Pipeline](#cicd-pipeline)
11. [Contributing](#contributing)

---

## Features

| Feature | Details |
|---|---|
| 🔐 **Identity Verification** | UUID pasted into Codeforces "Organisation" field |
| ⚔️ **1v1 Lockout Matches** | First solver wins the problem; concurrent claims safely serialised |
| 🔒 **Atomic Lock Pattern** | PostgreSQL `SELECT … FOR UPDATE` prevents race conditions |
| 📈 **Elo Rating System** | Standard Elo formula + Giant Slayer 1.5× bonus |
| 🏅 **Rank System** | Script Kiddie → Pupil → Specialist → Expert → The Architect |
| 📋 **Leaderboard** | Paginated global leaderboard + per-user profile cards |
| 🔄 **Retry Logic** | Exponential back-off on Codeforces API failures |
| 📊 **Structured Logging** | Rotating file logs + console output |
| 🐳 **Docker Ready** | Multi-stage Dockerfile + GitHub Actions CI/CD |

---

## Architecture Overview

```
Discord User
    │
    ▼
┌─────────────────────────────────────────────────────┐
│                    disnake Bot                      │
│  ┌──────────────┐  ┌─────────────┐  ┌───────────┐  │
│  │ Verification │  │   Lockout   │  │Leaderboard│  │
│  │     Cog      │  │     Cog     │  │    Cog    │  │
│  └──────┬───────┘  └──────┬──────┘  └─────┬─────┘  │
└─────────┼─────────────────┼───────────────┼─────────┘
          │                 │               │
          ▼                 ▼               ▼
┌─────────────────┐  ┌─────────────────────────────┐
│  Codeforces API │  │       PostgreSQL DB          │
│  (aiohttp +     │  │  users / matches /           │
│   rate limiter) │  │  lockout_problems            │
└─────────────────┘  └─────────────────────────────┘
```

### Sequence Diagram – Submit Solution (Race Condition Safe)

```
User A          User B          Bot               PostgreSQL
  │               │              │                    │
  │──/submit──────────────────►  │                    │
  │               │              │──BEGIN TRANSACTION─►│
  │               │              │──SELECT … FOR UPDATE►│ ← row locked
  │               │  /submit──►  │                    │
  │               │              │  (blocked: row     │
  │               │              │   already locked)  │
  │               │              │──verify CF API      │
  │               │              │──UPDATE locked_by   │
  │               │              │──COMMIT─────────────►│ ← lock released
  │               │              │                    │──unblocks B
  │               │              │◄──────────────────── │ B sees locked=A
  │               │              │──ProblemLockedError►  │
  │               │◄─────────────│                    │
```

---

## Directory Structure

```
nits-arena/
├── cogs/
│   ├── __init__.py
│   ├── verification.py      # /verify, /confirm-verification
│   ├── lockout.py           # /start-match, /submit-solution, /match-status
│   └── leaderboard.py       # /leaderboard, /profile
├── database/
│   ├── __init__.py
│   ├── models.py            # SQLAlchemy ORM models
│   └── session.py           # Async engine, session factory, pooling
├── utils/
│   ├── __init__.py
│   ├── cf_api.py            # Codeforces API wrapper (aiohttp + retry)
│   ├── elo_calculator.py    # Elo formula, Giant Slayer bonus, rank labels
│   ├── exceptions.py        # Custom exception hierarchy
│   └── logger.py            # Structured logging with rotation
├── tests/
│   ├── __init__.py
│   ├── test_elo.py          # Elo unit tests
│   ├── test_cf_api.py       # Mock CF API tests
│   └── test_lockout_logic.py # Race condition prevention tests
├── .env.example
├── .dockerignore
├── .gitignore
├── .github/
│   └── workflows/
│       └── main.yml         # lint → test → docker build
├── Dockerfile
├── bot.py                   # Entry point
├── requirements.txt
└── README.md
```

---

## Database Schema

```
users
──────────────────────────────────────────────────
id               INTEGER  PK
discord_id       BIGINT   UNIQUE NOT NULL
cf_handle        VARCHAR  UNIQUE NOT NULL
verification_uuid VARCHAR  (cleared after verification)
is_verified      BOOLEAN  DEFAULT FALSE
elo_rating       INTEGER  DEFAULT 1200
wins / losses / draws  INTEGER
created_at / updated_at  TIMESTAMPTZ

matches
──────────────────────────────────────────────────
id               INTEGER  PK
player_a_id      FK→users
player_b_id      FK→users
status           ENUM (pending/active/completed/cancelled)
winner_id        FK→users (nullable)
player_a_score / player_b_score  INTEGER
player_a_elo_at_start / player_b_elo_at_start  INTEGER
min_rating / max_rating  INTEGER
started_at / ended_at  TIMESTAMPTZ

lockout_problems
──────────────────────────────────────────────────
id               INTEGER  PK
match_id         FK→matches
contest_id       INTEGER
problem_index    VARCHAR(8)
problem_name     VARCHAR(256)
cf_rating        INTEGER
locked_by_user_id  FK→users (NULL = unclaimed)
points_awarded   INTEGER  DEFAULT 0
locked_at        TIMESTAMPTZ
UNIQUE (match_id, contest_id, problem_index)
```

---

## Race Condition Prevention

The "Multi-Lock Problem" occurs when two concurrent submission requests both
read `locked_by_user_id = NULL` before either write has committed, causing
both to believe they are the first solver.

**Solution: PostgreSQL `SELECT … FOR UPDATE`**

```python
result = await session.execute(
    select(LockoutProblem)
    .where(
        LockoutProblem.match_id == match_id,
        LockoutProblem.contest_id == contest_id,
        LockoutProblem.problem_index == problem_index,
    )
    .with_for_update()   # acquires row-level lock
)
problem = result.scalar_one_or_none()

if problem.locked_by_user_id is not None:
    raise ProblemLockedError("Already claimed.")

# verify CF submission while holding the lock

problem.locked_by_user_id = user.id
await session.commit()   # releases the lock
```

The lock is acquired **before** reading `locked_by_user_id` and released only
after the transaction commits, serialising all concurrent attempts.

---

## Elo Rating System

Standard Elo formula:

```
E_a = 1 / (1 + 10^((R_b - R_a) / 400))
R'_a = R_a + K × (S_a − E_a)      K = 32
```

**Giant Slayer Bonus:** When the winner's pre-match Elo is ≥ 200 below the
loser's, the winner's delta is multiplied by **1.5×**. The gap is calculated
at match start (deterministic).

**Point Scaling:**

| CF Rating | Match Points |
|-----------|-------------|
| 800       | 100 pts     |
| 1000      | 200 pts     |
| 1200      | 300 pts     |

**Rank Thresholds:**

| Rank           | Elo Range  |
|----------------|------------|
| Script Kiddie  | < 1200     |
| Pupil          | 1200–1399  |
| Specialist     | 1400–1599  |
| Expert         | 1600–1799  |
| The Architect  | 1800+      |

---

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- A Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))

### Installation

```bash
# 1. Clone the repo
git clone https://github.com/Rahilj45/-Rahilj45-nits-arena.git
cd -Rahilj45-nits-arena

# 2. Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your DISCORD_TOKEN and DATABASE_URL

# 5. Run the bot
python bot.py
```

### Docker

```bash
docker build -t nits-arena .
docker run --env-file .env nits-arena
```

---

## Commands Reference

| Command | Description |
|---|---|
| `/verify <handle>` | Begin Codeforces account linking |
| `/confirm-verification` | Finalise verification after updating CF Organisation field |
| `/start-match @opponent [min_rating] [max_rating] [problem_count]` | Challenge another player |
| `/submit-solution <match_id> <contest_id> <index>` | Claim a solved problem |
| `/match-status <match_id>` | View live match state |
| `/leaderboard [page]` | Global Elo leaderboard |
| `/profile [@member]` | View a player's stats and rank |

---

## Running Tests

```bash
# Install test extras
pip install aiosqlite

# Run the full test suite
pytest tests/ --asyncio-mode=auto -v

# With coverage
pytest tests/ --asyncio-mode=auto --cov=utils --cov=database --cov-report=term-missing
```

---

## CI/CD Pipeline

`.github/workflows/main.yml` runs three sequential jobs on every push/PR:

```
lint (flake8) → test (pytest + coverage) → build (docker buildx)
```

---

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feat/my-feature`)
3. Commit your changes following the existing code style
4. Ensure all tests pass (`pytest tests/ --asyncio-mode=auto`)
5. Open a Pull Request

---

*Built for GSSoC 2026 – NITS Arena*
