"""
Centralized Match Outcome & Scoring Engine
==========================================
Uses a Strategy pattern so new outcomes (forfeit, bye, etc.) can be added
by registering a new OutcomeHandler subclass without touching any routes or models.

Design:
  OutcomeHandler (ABC)
    └─ RegularOutcomeHandler   – normal score-based finalization
    └─ WalkoverOutcomeHandler  – one team wins by default (no game played)
    └─ ... (add ForfeitOutcomeHandler, ByeOutcomeHandler, etc. the same way)

  OutcomeRegistry              – maps outcome_type strings → handlers
  OutcomeService               – stateless facade used by all API routes
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from models import Match, MatchOutcome, Score, db


# ---------------------------------------------------------------------------
# Bracket propagation helper (shared across all outcome types)
# ---------------------------------------------------------------------------

def _propagate_to_successor(match: Match, winning_team_id: str) -> None:
    """Advance the winner to the next bracket match (knockout rounds)."""
    if not match.successor or not winning_team_id:
        return

    successor = db.session.get(Match, match.successor)
    if not successor:
        return

    if successor.predecessor_1 == match.id:
        successor.team1_id = winning_team_id
    elif successor.predecessor_2 == match.id:
        successor.team2_id = winning_team_id

    # Create missing Score records when both teams are now known
    if successor.team1_id and successor.team2_id:
        for tid in (successor.team1_id, successor.team2_id):
            if not Score.query.filter_by(match_id=successor.id, team_id=tid).first():
                db.session.add(Score(
                    match_id=successor.id,
                    team_id=tid,
                    score=0,
                    tournament_id=successor.tournament_id,
                ))
    db.session.flush()


def _upsert_score(match: Match, team_id: str, score: int) -> None:
    rec = Score.query.filter_by(match_id=match.id, team_id=team_id).first()
    if rec:
        rec.score = score
    else:
        db.session.add(Score(
            match_id=match.id,
            team_id=team_id,
            score=score,
            tournament_id=match.tournament_id,
        ))


def _finalize(match: Match, winner_team_id: Optional[str]) -> None:
    match.winner_team_id = winner_team_id
    match.is_final = True
    match.status = 'completed'
    if winner_team_id:
        _propagate_to_successor(match, winner_team_id)


# ---------------------------------------------------------------------------
# Base handler interface
# ---------------------------------------------------------------------------

class OutcomeHandler(ABC):
    """
    Abstract base class for match outcome strategies.

    To add a new outcome type (e.g. 'forfeit'):
      1. Subclass OutcomeHandler
      2. Set outcome_type = 'forfeit'
      3. Implement validate(), process(), and calculate_standings_impact()
      4. Call outcome_registry.register(ForfeitOutcomeHandler()) at module level
    """

    @property
    @abstractmethod
    def outcome_type(self) -> str:
        """Unique string key for this outcome, e.g. 'regular' or 'walkover'."""
        ...

    @abstractmethod
    def validate(self, match: Match, **kwargs: Any) -> None:
        """Raise ValueError if input is invalid for this outcome type."""
        ...

    @abstractmethod
    def process(self, match: Match, **kwargs: Any) -> Dict[str, Any]:
        """
        Apply the outcome to the match (write to DB session, do NOT commit).
        Returns a result dict that the API route will return to the caller.
        """
        ...

    @abstractmethod
    def calculate_standings_impact(
        self,
        match: Match,
        team1_score: int,
        team2_score: int,
    ) -> Dict[str, Any]:
        """
        Return a structure describing how this match contributes to standings.

        Shape:
        {
            'is_played': bool,
            'winner': str | None,          # team_id of winner or None
            'team1': {
                'points_scored': int,
                'points_lost':   int,
                'won':           0 | 1,
                'table_points':  int,      # league-table points (2 for win, 1 for draw, 0 for loss)
            },
            'team2': { ... same ... },
        }
        """
        ...


# ---------------------------------------------------------------------------
# Regular (score-based) handler
# ---------------------------------------------------------------------------

class RegularOutcomeHandler(OutcomeHandler):
    outcome_type = MatchOutcome.REGULAR.value

    def validate(self, match: Match, **kwargs: Any) -> None:
        t1 = kwargs.get('team1_score')
        t2 = kwargs.get('team2_score')
        if t1 is None or t2 is None:
            raise ValueError("team1_score and team2_score are required for a regular match outcome")
        if int(t1) < 0 or int(t2) < 0:
            raise ValueError("Scores cannot be negative")

    def process(self, match: Match, **kwargs: Any) -> Dict[str, Any]:
        team1_score = int(kwargs['team1_score'])
        team2_score = int(kwargs['team2_score'])
        final = bool(kwargs.get('final', False))
        details = kwargs.get('details')

        _upsert_score(match, match.team1_id, team1_score)
        _upsert_score(match, match.team2_id, team2_score)

        match.outcome_type = self.outcome_type
        if details:
            match.outcome_details = details

        winner_team_id = None
        if final:
            if team1_score > team2_score:
                winner_team_id = match.team1_id
            elif team2_score > team1_score:
                winner_team_id = match.team2_id
            _finalize(match, winner_team_id)

        return self._result(match, team1_score, team2_score)

    def calculate_standings_impact(
        self, match: Match, team1_score: int, team2_score: int
    ) -> Dict[str, Any]:
        # An unplayed regular match (both scores 0, not final) contributes nothing
        played = match.is_final or (team1_score != 0 or team2_score != 0)
        winner = match.winner_team_id
        if not winner and match.is_final:
            if team1_score > team2_score:
                winner = match.team1_id
            elif team2_score > team1_score:
                winner = match.team2_id

        return {
            'is_played': played,
            'winner': winner,
            'team1': {
                'points_scored': team1_score,
                'points_lost':   team2_score,
                'won':           1 if winner == match.team1_id else 0,
                'table_points':  2 if winner == match.team1_id else (1 if winner is None and played else 0),
            },
            'team2': {
                'points_scored': team2_score,
                'points_lost':   team1_score,
                'won':           1 if winner == match.team2_id else 0,
                'table_points':  2 if winner == match.team2_id else (1 if winner is None and played else 0),
            },
        }

    @staticmethod
    def _result(match: Match, t1: int, t2: int) -> Dict[str, Any]:
        return {
            'success':         True,
            'match_id':        match.id,
            'winner_team_id':  match.winner_team_id,
            'team1_id':        match.team1_id,
            'team1_score':     t1,
            'team2_id':        match.team2_id,
            'team2_score':     t2,
            'is_final':        match.is_final,
            'status':          match.status,
            'outcome_type':    match.outcome_type,
            'outcome_details': match.outcome_details,
        }


# ---------------------------------------------------------------------------
# Walkover handler
# ---------------------------------------------------------------------------

class WalkoverOutcomeHandler(OutcomeHandler):
    outcome_type = MatchOutcome.WALKOVER.value

    def validate(self, match: Match, **kwargs: Any) -> None:
        winner_team_id = kwargs.get('winner_team_id')
        if not winner_team_id:
            raise ValueError("winner_team_id is required for a walkover outcome")
        if winner_team_id not in (match.team1_id, match.team2_id):
            raise ValueError(
                f"winner_team_id must be one of the participating teams "
                f"({match.team1_id}, {match.team2_id})"
            )

    def process(self, match: Match, **kwargs: Any) -> Dict[str, Any]:
        winner_team_id = kwargs['winner_team_id']
        details = kwargs.get('details', 'Walkover')
        # Walkover scores default to 0-0; they can be overridden if needed
        team1_score = int(kwargs.get('team1_score', 0))
        team2_score = int(kwargs.get('team2_score', 0))

        _upsert_score(match, match.team1_id, team1_score)
        _upsert_score(match, match.team2_id, team2_score)

        match.outcome_type    = self.outcome_type
        match.outcome_details = details
        _finalize(match, winner_team_id)

        return {
            'success':         True,
            'match_id':        match.id,
            'winner_team_id':  match.winner_team_id,
            'team1_id':        match.team1_id,
            'team1_score':     team1_score,
            'team2_id':        match.team2_id,
            'team2_score':     team2_score,
            'is_final':        match.is_final,
            'status':          match.status,
            'outcome_type':    match.outcome_type,
            'outcome_details': match.outcome_details,
        }

    def calculate_standings_impact(
        self, match: Match, team1_score: int, team2_score: int
    ) -> Dict[str, Any]:
        winner = match.winner_team_id
        return {
            'is_played': True,          # walkovers always count as played
            'winner':    winner,
            'team1': {
                'points_scored': team1_score,
                'points_lost':   team2_score,
                'won':           1 if winner == match.team1_id else 0,
                'table_points':  2 if winner == match.team1_id else 0,
            },
            'team2': {
                'points_scored': team2_score,
                'points_lost':   team1_score,
                'won':           1 if winner == match.team2_id else 0,
                'table_points':  2 if winner == match.team2_id else 0,
            },
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class OutcomeRegistry:
    """Central registry — maps outcome_type strings to handlers."""

    def __init__(self) -> None:
        self._handlers: Dict[str, OutcomeHandler] = {}

    def register(self, handler: OutcomeHandler) -> None:
        self._handlers[handler.outcome_type] = handler

    def get_handler(self, outcome_type: str) -> OutcomeHandler:
        handler = self._handlers.get(outcome_type or MatchOutcome.REGULAR.value)
        if not handler:
            raise ValueError(f"Unsupported outcome type: '{outcome_type}'")
        return handler


# Singleton registry — import and call .register() to add new outcome types
outcome_registry = OutcomeRegistry()
outcome_registry.register(RegularOutcomeHandler())
outcome_registry.register(WalkoverOutcomeHandler())


# ---------------------------------------------------------------------------
# Service facade
# ---------------------------------------------------------------------------

class OutcomeService:
    """
    Stateless facade used by all API routes.
    Delegates to the appropriate OutcomeHandler from the registry.
    """

    @staticmethod
    def apply_outcome(
        match_id: int,
        outcome_type: str = MatchOutcome.REGULAR.value,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Validate and apply the specified outcome to the match.
        Commits the session on success; callers should NOT commit themselves.

        Raises ValueError for invalid input.
        Raises LookupError for unknown match.
        """
        match = db.session.get(Match, match_id)
        if not match:
            raise LookupError(f"Match not found with id: {match_id}")

        override = kwargs.pop('override', False)
        if match.is_final and not override:
            raise ValueError(
                "Match has already been finalized. Pass override=True to update."
            )

        handler = outcome_registry.get_handler(outcome_type)
        handler.validate(match, **kwargs)
        result = handler.process(match, **kwargs)
        db.session.commit()
        return result

    @staticmethod
    def get_standings_impact(
        match: Match,
        team1_score: int,
        team2_score: int,
    ) -> Dict[str, Any]:
        """
        Return the standings contribution for a match.
        Uses the match's current outcome_type so walkovers, forfeits, etc.
        are all treated correctly without any if/elif chains in the caller.
        """
        handler = outcome_registry.get_handler(
            match.outcome_type or MatchOutcome.REGULAR.value
        )
        return handler.calculate_standings_impact(match, team1_score, team2_score)

