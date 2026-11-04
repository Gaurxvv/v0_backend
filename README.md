# Khel Club Tournament Management System - Walkover Feature & Architecture

## Overview
This document details the architecture, design decisions, database migrations, and frontend/backend implementations for supporting the **Walkover** match outcome (and future outcomes such as **Forfeit** and **Bye**) in the Khel Club Pickleball Tournament Management System.

---

## 1. How the Current Scoring Mechanism Works

### Data Models & Relationships
- **`Match`**: Represents a match between two teams (`team1_id`, `team2_id`) in a tournament pool or knockout bracket. It maintains lifecycle states via:
  - `status`: String (`pending` -> `on-going` -> `completed`)
  - `is_final`: Boolean flag indicating whether the result has been locked
  - `winner_team_id`: ID of the victorious team
  - `round_id`, `pool`, `successor`, `predecessor_1`, `predecessor_2`: Topology for brackets and rounds
- **`Score`**: Represents each team's points in a match (`match_id`, `team_id`, `score`, `tournament_id`).

### Lifecycle & Flow
1. **Match Initiation**: Matches start in `pending` status. Referees move matches to `on-going` via `POST /update-match-status/<match_id>`.
2. **Score Recording**: Scores are submitted to `POST /update-score` in the format `team1_score-team2_score`. Each team's score record is upserted.
3. **Match Finalization**: When `final=True` is provided:
   - Winner is computed by comparing scores: `winner = team1 if team1_score > team2_score else team2`.
   - `is_final` is set to `True`, and status is set to `completed`.
   - If the match is part of a knockout bracket (`successor` is present), the winning team advances automatically to the successor match.
4. **Standings Calculation**: Standings endpoints (`GET /standings/<id>` and `GET /overall-standings/<id>`) aggregate match scores by round and pool:
   - In the legacy code, any match with `team1_score == 0 and team2_score == 0` was skipped as unplayed.

---

## 2. What Changes Were Made and Why

### Extensible Strategy Pattern (`services/outcome_service.py`)
To prevent hardcoded one-off `if/else` checks scattered across routes, controllers, and queries, we implemented the **Strategy Pattern**:

```
                  ┌───────────────────────┐
                  │    OutcomeHandler     │ (Abstract Base)
                  │ ───────────────────── │
                  │ + validate()          │
                  │ + process()           │
                  │ + standings_impact()  │
                  └───────────▲───────────┘
                              │
          ┌───────────────────┴───────────────────┐
          │                                       │
┌───────────────────────┐               ┌───────────────────────┐
│ RegularOutcomeHandler │               │ WalkoverOutcomeHandler│
│ (Score comparison)    │               │ (Declared Winner, 0-0)│
└───────────────────────┘               └───────────────────────┘
          │                                       │
          └───────────────────┬───────────────────┘
                              ▼
                  ┌───────────────────────┐
                  │    OutcomeRegistry    │ (Singleton Registry)
                  └───────────────────────┘
                              │
                              ▼
                  ┌───────────────────────┐
                  │    OutcomeService     │ (Stateless API Facade)
                  └───────────────────────┘
```

#### Key Components:
- **`OutcomeHandler` (ABC)**:
  - `validate(match, **kwargs)`: Validates prerequisites (e.g., verifying `winner_team_id` belongs to the match).
  - `process(match, **kwargs)`: Performs DB updates (setting winner, scores, status, details, and bracket advancement).
  - `calculate_standings_impact(match, score1, score2)`: Encapsulates how this match counts towards wins, points, and table scores.
- **`RegularOutcomeHandler`**: Handles standard numerical score matches.
- **`WalkoverOutcomeHandler`**: Handles walkovers by recording the declared winner, keeping scores at `0-0`, marking the match as completed, and propagating the winner to the next round.
- **`OutcomeRegistry`**: Decoupled registry mapping outcome names (`regular`, `walkover`, `forfeit`, `bye`) to their handler implementations.
- **`OutcomeService`**: Clean facade invoked by all Flask blueprints.

### API Layer Changes
1. **`POST /match/outcome`**: Added to `routes/match/match_core.py`.
   - Dedicated endpoint for setting special outcomes (`outcome_type`, `winner_team_id`, `details`, `override`).
2. **`POST /update-score`**: Updated in `routes/score/score_core.py`.
   - Now routes through `OutcomeService.apply_outcome()` while maintaining 100% backward compatibility for standard score updates.
3. **`GET /score/match` & `GET /get-match-fixtures` & `GET /pools/<pool>/fixtures`**:
   - Now return `outcome_type` and `outcome_details` in match status payloads.
4. **`GET /standings/<id>` & `GET /overall-standings/<id>`**:
   - Updated to use `OutcomeService.get_standings_impact()`. Walkovers now count as played matches awarding 2 table points to the winner without distorting score differentials.

### Frontend UI Changes (Next.js)
1. **Referee Match Screen (`Match.js`)**:
   - Added **"Award Walkover"** button alongside "Update" and "Final".
   - Added Walkover Modal Dialog allowing the referee to select the winning team and add an optional reason (e.g., "Opponent no-show", "Injury").
   - Added prominent **Walkover Banner** displaying winner and reason.
2. **Referee Match Table (`RefereeMatchTable.js`)**:
   - Displays `W/O` tag in Result column.
   - Displays `Walkover` badge in Status column.
   - Highlights winning team in green with `(Winner)`.
   - Added direct "Award Walkover" action button and modal.
3. **Public Fixtures & Match Table (`Fixtures.js`, `MatchTable.js`, `KnockoutBracket.js`, `PoolFixtures.js`)**:
   - Match cards show clear **Walkover** tag.
   - Winner is highlighted with a green accent and `(Winner)` badge.
   - Conceded team is styled with muted strike-through text.
   - Score badges display `W` / `L` or `W/O` instead of `0-0`.
   - CSV export accurately includes walkover status.

---

## 3. Schema & Migration Changes

### Database Changes
Two new columns added to the `match` table:
- **`outcome_type`**: `VARCHAR(20) DEFAULT 'regular' NOT NULL`
- **`outcome_details`**: `TEXT NULL`

### Migration Files
1. **`migrations/bul/add_match_outcome_columns.sql`**: Production MySQL SQL migration.
2. **`migrations/bul/migrate_match_outcome.py`**: Cross-platform Python migration script supporting both MySQL and SQLite.

---

## 4. Assumptions Made
1. **Score Representation**: Walkovers record a score of `0-0` internally so point differential tiebreakers are not distorted unless leagues implement specific walkover point rules.
2. **Standings Impact**: The winner of a walkover receives standard win points (2 table points). The match is counted in `matches_played` for both teams.
3. **Immediate Finalization**: Awarding a walkover completes the match (`status='completed'`, `is_final=True`).
4. **Bracket Propagation**: Walkover winners advance through tournament brackets identically to regular winners.

---

## 5. How to Add Future Outcomes (Forfeits, Byes)

Thanks to the Strategy Pattern, adding **Forfeit** or **Bye** requires **zero modifications** to existing models or routes:

```python
# In services/outcome_service.py (or a new module)
class ForfeitOutcomeHandler(OutcomeHandler):
    outcome_type = 'forfeit'

    def validate(self, match, **kwargs):
        if not kwargs.get('winner_team_id'):
            raise ValueError("winner_team_id is required for a forfeit")

    def process(self, match, **kwargs):
        # E.g., award standard 11-0 score line or custom forfeit penalty
        winner = kwargs['winner_team_id']
        t1_score = 11 if winner == match.team1_id else 0
        t2_score = 11 if winner == match.team2_id else 0
        _upsert_score(match, match.team1_id, t1_score)
        _upsert_score(match, match.team2_id, t2_score)
        match.outcome_type = self.outcome_type
        match.outcome_details = kwargs.get('details', 'Forfeit')
        _finalize(match, winner)
        return self._result(match, t1_score, t2_score)

    def calculate_standings_impact(self, match, t1_score, t2_score):
        # Calculate impact...
        ...

# Register with one line:
outcome_registry.register(ForfeitOutcomeHandler())
```

---

## 6. What We Would Improve With More Time
1. **Configurable Outcome Rules**: Allow tournament directors to customize walkover scoring per tournament (e.g., standard 11-0 vs 0-0 vs custom points).
2. **Audit Logging**: Add an `OutcomeAuditLog` table capturing timestamp, referee ID, and previous score state when an outcome is modified or overridden.
3. **Real-time Notifications**: Emit dedicated Socket.IO events (`match_walkover_awarded`) to alert players via SMS/Push when their match status changes.
4. **Dupr Export Integration**: Update DUPR reporting to correctly flag walkovers so ratings are not distorted by non-played matches.

---

## 7. Local Development & Testing Instructions

### Backend
```powershell
cd v0_backend-main
.\.venv\Scripts\activate
# Run server
python app.py
```

### Frontend
```powershell
cd v0_frontend-main
npm run dev
```
Navigate to `http://localhost:3000` to view the application.
